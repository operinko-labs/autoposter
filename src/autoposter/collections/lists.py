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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.posters import apply_poster
from autoposter.collections.reconcile import resolve_collision
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
    if collection is not None and record is not None and record.definition_hash == wanted:
        # The membership is already correct, but a claim just written a label
        # to Plex. Returning [] here would drop that write from the summary --
        # the operator would see "0 action(s)" for a pass that changed the
        # collection's ownership. ``reconcile.py`` reports it the same way.
        return [claim_action] if claim_action else []

    if dry_run:
        return ["%s %r with %d item(s)" % (
            "would update" if collection else "would create", title, len(items))]

    actions: list[str] = [claim_action] if claim_action else []
    if collection is None:
        collection = section.createCollection(title=title, items=items, smart=False)
        existing[title] = collection
        collection.addLabel(label)
        collection.sortUpdate(sort)
        if summary:
            collection.editSummary(summary)
        actions.append("created %r with %d item(s)" % (title, len(items)))
    else:
        # The summary is part of the members hash, so a corrected summary
        # takes the update branch. Writing it only on create would mean the
        # new hash gets stored while the old summary stays on the collection
        # forever, with every later pass short-circuiting on that hash.
        if summary and getattr(collection, "summary", None) != summary:
            collection.editSummary(summary)
            actions.append("updated the summary of %r" % title)

        current = {str(i.ratingKey): i for i in collection.items()}
        desired = {str(i.ratingKey): i for i in items}

        adding = [i for key, i in desired.items() if key not in current]
        removing = [i for key, i in current.items() if key not in desired]
        if adding:
            collection.addItems(adding)
        if removing:
            collection.removeItems(removing)
        moves = _enforce_order(collection, items)
        if adding or removing or moves:
            actions.append(
                "updated %r: +%d -%d, %d move(s)" % (title, len(adding), len(removing), moves)
            )

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

    if kind is not None and http is not None and config is not None and config.collections.posters:
        message = await apply_poster(
            session, http, config, collection, record, library, kind, key, dry_run=False,
        )
        if message:
            actions.append(message)

    await session.flush()
    return actions
