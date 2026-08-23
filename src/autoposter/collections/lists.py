"""Reconcile regular (list) collections against a source-ordered item list.

Unlike the smart collections in ``reconcile.py``, membership here is explicit
and diffed on every pass, with sync semantics: a member the source no longer
selects is removed.

That makes an empty desired-set dangerous. A failed chart fetch returning
nothing would, taken literally, empty a live collection -- so an empty list
means "make no changes", never "remove everything".
"""
import hashlib
import logging

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.posters import apply_poster, posters_enabled
from autoposter.collections.reconcile import _edit_collection_summary, resolve_collision
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)


def _members_hash(items: list, summary: str | None) -> str:
    payload = "\x1f".join([summary or "", *[str(i.ratingKey) for i in items]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _enforce_order(collection, desired: list) -> int:
    """Move only the items that are out of place. Returns moves made.

    ``Collection.items()`` returns ``self._items``, a ``cached_data_property``
    that ``addItems``, ``removeItems`` and ``moveItem`` all leave in place --
    only ``reload()`` invalidates it. So the order is read exactly once, from
    a freshly reloaded collection, and every move is then applied to a local
    copy. Re-reading inside the loop would keep handing back the pre-add
    snapshot: ``index()`` would raise for a newly added item, and the moves it
    did compute would be against stale positions.
    """
    collection.reload()
    current = [str(i.ratingKey) for i in collection.items()]
    wanted = [str(i.ratingKey) for i in desired]
    if current == wanted:
        return 0
    moves = 0
    previous = None
    for item in desired:
        key = str(item.ratingKey)
        position = current.index(key)
        expected = 0 if previous is None else current.index(str(previous.ratingKey)) + 1
        if position != expected:
            collection.moveItem(item, after=previous)
            current.pop(position)
            after = 0 if previous is None else current.index(str(previous.ratingKey)) + 1
            current.insert(after, key)
            moves += 1
        previous = item
    return moves


async def reconcile_list_collection(
    session: AsyncSession,
    section,
    library: str,
    title: str,
    items: list,
    label: str,
    summary: str | None = None,
    sort: str = "custom",
    dry_run: bool = True,
    existing: dict | None = None,
    adopt: bool = False,
    adopt_from: list[str] | None = None,
    adopt_removes_prior_label: bool = False,
    protect_labels: list[str] | None = None,
    kind: str | None = None,
    key: str | None = None,
    http: httpx.AsyncClient | None = None,
    config=None,
) -> list[str]:
    """Bring one list collection in line with ``items`` (already in source order).

    ``existing`` is a ``{title: collection}`` map of the section's collections.
    Listing them costs one request that returns every collection in the
    library -- 305 of them on the production Movies section -- so the caller
    reconciling several collections in a row passes one listing in rather than
    paying for it per collection. Omitting it falls back to listing here.
    """
    if not items:
        return [
            "%r: source returned no items; leaving the collection untouched" % title
        ]

    if existing is None:
        existing = {c.title: c for c in section.collections()}
    collection = existing.get(title)

    claim_action = None
    if collection is not None:
        ok, message = resolve_collision(
            collection, label, adopt, adopt_from or [], adopt_removes_prior_label, dry_run,
            protect_labels or [],
        )
        if not ok:
            return [message] if message else []
        claim_action = message

    record = (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.library == library, ManagedCollection.title == title
            )
        )
    ).scalar_one_or_none()

    wanted = _members_hash(items, summary)
    # ``key`` is checked alongside ``kind``: one without the other would
    # interpolate the string "None" into a poster URL.
    posters_on = (
        kind is not None and key is not None and posters_enabled(config, http)
    )
    definition_current = (
        collection is not None and record is not None and record.definition_hash == wanted
    )
    # An unchanged hash still means the pass confirmed membership is as
    # desired, so the row records the observation with a zero delta. Stamped
    # ahead of the short-circuit rather than inside it so the poster-refresh
    # fall-through below -- an unchanged membership whose ``poster_sha256`` is
    # still NULL -- records its pass too, since that branch skips the write
    # block further down entirely.
    if definition_current and not dry_run:
        record.member_count = len(items)
        record.last_added = 0
        record.last_removed = 0
        record.last_reconciled_at = func.now()

    # An unchanged membership is not on its own a reason to stop: a row whose
    # ``poster_sha256`` is still NULL -- one that predates posters being
    # enabled, or whose fetch failed on the pass that created it -- would
    # otherwise never be revisited. Once the hash is stored, the return below
    # resumes.
    if definition_current and not (posters_on and record.poster_sha256 is None):
        # The membership is already correct, but a claim just written a label
        # to Plex. Returning [] here would drop that write from the summary --
        # the operator would see "0 action(s)" for a pass that changed the
        # collection's ownership. ``reconcile.py`` reports it the same way.
        return [claim_action] if claim_action else []

    actions: list[str] = [claim_action] if claim_action else []
    # The delta the row records, set by whichever write path runs below. A
    # create adds every item and removes none; an update counts its own diff.
    added_count = removed_count = 0

    if not definition_current:
        if dry_run:
            actions.append("%s %r with %d item(s)" % (
                "would update" if collection else "would create", title, len(items)))
        elif collection is None:
            collection = section.createCollection(title=title, items=items, smart=False)
            existing[title] = collection
            collection.addLabel(label)
            collection.sortUpdate(sort)
            if summary:
                _edit_collection_summary(collection, summary)
            added_count = len(items)
            actions.append("created %r with %d item(s)" % (title, len(items)))
        else:
            # The summary is part of the members hash, so a corrected summary
            # takes the update branch. Writing it only on create would mean the
            # new hash gets stored while the old summary stays on the collection
            # forever, with every later pass short-circuiting on that hash.
            #
            # The helper is called unconditionally -- it skips by itself. The
            # text comparison here gates only the ACTION MESSAGE: gating the
            # call on it would starve the helper's lock repair, leaving a
            # summary whose text already matches but whose field is unlocked
            # (the Kometa-era state) unlocked forever.
            if summary:
                if getattr(collection, "summary", None) != summary:
                    actions.append("updated the summary of %r" % title)
                _edit_collection_summary(collection, summary)

            current = {str(i.ratingKey): i for i in collection.items()}
            desired = {str(i.ratingKey): i for i in items}

            adding = [i for k, i in desired.items() if k not in current]
            removing = [i for k, i in current.items() if k not in desired]
            if adding:
                collection.addItems(adding)
            if removing:
                collection.removeItems(removing)
            moves = _enforce_order(collection, items)
            added_count, removed_count = len(adding), len(removing)
            if adding or removing or moves:
                actions.append(
                    "updated %r: +%d -%d, %d move(s)"
                    % (title, added_count, removed_count, moves)
                )

        if not dry_run:
            if record is None:
                record = ManagedCollection(
                    library=library, title=title, kind="manual",
                    plex_rating_key=str(getattr(collection, "ratingKey", "") or ""),
                    definition_hash=wanted,
                )
                session.add(record)
            else:
                record.definition_hash = wanted
                record.plex_rating_key = str(getattr(collection, "ratingKey", "") or "")
            record.member_count = len(items)
            record.last_added = added_count
            record.last_removed = removed_count
            record.last_reconciled_at = func.now()

    if posters_on and collection is not None and record is not None:
        message = await apply_poster(
            session, http, config, collection, record, library, kind, key, dry_run=dry_run,
        )
        if message:
            actions.append(message)

    await session.flush()
    return actions
