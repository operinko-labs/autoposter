"""Reconcile regular (list) collections against a source-ordered item list.

Unlike the smart collections in ``reconcile.py``, membership here is explicit
and diffed on every pass, with sync semantics by default: a member the source
no longer selects is removed. A definition may ask for ``append`` instead,
which only ever adds -- see ``member_diff``.

That makes an empty desired-set dangerous. A failed chart fetch returning
nothing would, taken literally, empty a live collection -- so an empty list
means "make no changes", never "remove everything".
"""
import hashlib
import logging

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections import groups
from autoposter.collections.posters import apply_poster, posters_enabled
from autoposter.collections.reconcile import (
    _edit_collection_summary,
    apply_collection_settings,
    resolve_collision,
    shape_conflict,
)
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)

# The per-definition settings that ride along on a collection, and the value
# each has when the operator has not asked for it. Order is fixed because it
# feeds a hash; the defaults are what keep that hash unchanged for every
# definition that sets none of them (see ``_settings_parts``).
_RIDE_ALONG_DEFAULTS = (
    ("labels", []),
    ("label_sync", False),
    ("item_label", []),
    ("sort_title", None),
    ("collection_mode", None),
    ("visible_library", None),
    ("visible_home", None),
    ("visible_shared", None),
    ("hub_priority", None),
)


def _settings_parts(settings) -> list[str]:
    """The ride-along settings' contribution to the members hash.

    They have to be in it. The hash is what a pass short-circuits on, so a
    definition whose *only* edit was a new label or a new sort title would
    otherwise be recognised as already current and the edit would never be
    applied -- a setting that reads as saved and silently is not.

    A definition that sets none of them contributes nothing, exactly as
    ``sync`` does for the mode: that is what keeps every hash already stored
    matching, instead of this change re-reconciling every managed collection
    in the library to write nothing.
    """
    if settings is None:
        return []
    return [
        "%s=%r" % (name, value)
        for name, default in _RIDE_ALONG_DEFAULTS
        if (value := getattr(settings, name, default)) != default
    ]


def _members_hash(
    items: list, summary: str | None, sync_mode: str = "sync", settings=None
) -> str:
    """The desired state, hashed -- what an unchanged pass short-circuits on.

    The mode is part of it, because the same list means two different desired
    states under the two modes: switching a definition to sync has to remove
    members append was keeping, and a hash blind to the mode would report the
    membership as already correct and never do it. The ride-along settings are
    in it for the same reason -- see ``_settings_parts``.

    ``sync`` contributes nothing to the payload, so every hash already stored
    still matches: the mode is the shipped behaviour, and making the upgrade
    itself look like an edit would re-reconcile every managed collection in the
    library for no change at all.
    """
    payload = "\x1f".join([
        summary or "",
        *[str(i.ratingKey) for i in items],
        *([] if sync_mode == "sync" else [sync_mode]),
        *_settings_parts(settings),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _label_members(items: list, settings, title: str) -> list[str]:
    """Row 69: the definition's ``item_label`` on every resolved member.

    Only ever added, and only to the items the source names *now*. A member
    that left the collection is not a member, so taking the label off it would
    be a write against an item this definition no longer describes -- and the
    label may be one the operator applies from elsewhere too. Sync semantics
    stop at the collection object (row 29); an item is not ours to own.
    """
    if settings is None or not settings.item_label:
        return []
    tags = list(dict.fromkeys(settings.item_label))
    for item in items:
        for tag in tags:
            item.addLabel(tag)
    return ["labelled %d member(s) of %r: %s" % (len(items), title, ", ".join(tags))]


def member_diff(collection, items: list, sync_mode: str = "sync") -> tuple[list, list]:
    """``(adding, removing)`` for one collection against the desired list.

    The one place the append rule lives: under ``append`` the removals are
    always empty -- a member the source no longer names, or never named, is
    exactly what an append definition exists to keep. Shared with the dry-run
    preview so the counts an operator is shown are the ones a real pass would
    act on, rather than a second implementation of the same subtraction.
    """
    current = {str(i.ratingKey): i for i in collection.items()}
    desired = {str(i.ratingKey): i for i in items}
    adding = [i for k, i in desired.items() if k not in current]
    removing = (
        [] if sync_mode == "append"
        else [i for k, i in current.items() if k not in desired]
    )
    return adding, removing


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
    sync_mode: str = "sync",
    settings=None,
    sort_prefix: str | None = None,
    sort_order: str | None = None,
) -> list[str]:
    """Bring one list collection in line with ``items`` (already in source order).

    ``existing`` is a ``{title: collection}`` map of the section's collections.
    Listing them costs one request that returns every collection in the
    library -- 305 of them on the production Movies section -- so the caller
    reconciling several collections in a row passes one listing in rather than
    paying for it per collection. Omitting it falls back to listing here.

    ``sync_mode`` is ``sync`` (the collection is exactly ``items``) or
    ``append`` (``items`` are added; nothing is ever removed and nothing
    already there is moved). See ``member_diff`` and ``_enforce_order``'s
    caller below for what each half of that means.

    ``settings`` is the ``CollectionDefinition`` this collection is built from,
    and carries everything applied *besides* membership -- labels, sort title,
    display mode, hub visibility, member labels. Optional: the direct callers
    that predate definitions pass none and get exactly what they always got.

    ``sort_prefix`` is the collection GROUP's sort-title prefix (``"!010_"``),
    and ``sort_order`` the family's per-member ordering key (``"01"``), both
    resolved engine-side (``collections/groups.py``, roadmap row 49). They are
    applied here rather than by the caller because only here is the concrete
    collection title known -- a family's members share a prefix and not a sort
    title. A definition that names its own ``sort_title`` keeps it; None derives
    nothing, which is what a direct caller with no pass around it gets.
    """
    if not items:
        return [
            "%r: source returned no items; leaving the collection untouched" % title
        ]

    # Out of band and read-only: the definition itself is never touched, so
    # nothing downstream can mistake a derived value for one the operator wrote
    # (``groups._DerivedSortTitle``). Placed here so the value is in front of
    # ``_settings_parts`` below -- the hash is what a pass short-circuits on, and
    # a sort title that appeared only at write time would never trigger one.
    settings = groups.with_derived_sort_title(settings, sort_prefix, title, sort_order)

    if existing is None:
        existing = {c.title: c for c in section.collections()}
    collection = existing.get(title)

    # C11, the list half. Checked BEFORE ``resolve_collision`` because a smart
    # collection this service already owns would otherwise pass the ownership
    # check and go on to ``addItems``, which Plex answers for a smart collection
    # by doing nothing useful and reporting success.
    if collection is not None:
        conflict = shape_conflict(collection, title, want_smart=False)
        if conflict is not None:
            return [conflict]

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

    wanted = _members_hash(items, summary, sync_mode, settings)
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

            adding, removing = member_diff(collection, items, sync_mode)
            if adding:
                collection.addItems(adding)
            if removing:
                collection.removeItems(removing)
            # Order is enforced only under sync. ``_enforce_order`` arranges
            # the collection to be exactly ``items``, which would drag every
            # appended definition's picks to the front and push whatever else
            # the collection holds behind them -- a write against members this
            # mode exists not to touch. Under append the new members arrive in
            # source order at the end, where addItems puts them, and the
            # positions that were already there are left alone.
            moves = 0 if sync_mode == "append" else _enforce_order(collection, items)
            added_count, removed_count = len(adding), len(removing)
            if adding or removing or moves:
                actions.append(
                    "updated %r: +%d -%d, %d move(s)"
                    % (title, added_count, removed_count, moves)
                )

        # Below both write branches and skipped entirely under dry_run: every
        # step of this writes to Plex. It runs on an update as well as a create
        # because the settings are in the hash -- reaching here at all means
        # either the membership or one of them changed, and which one it was is
        # not worth a second hash to learn.
        if not dry_run and collection is not None:
            actions += apply_collection_settings(
                section, collection, settings, label, config
            )
            actions += _label_members(items, settings, title)

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
                if record.kind != "manual":
                    # A row written under another kind can sit under a title
                    # this definition is later pointed at -- reconciling here
                    # is this definition claiming it, the same as any other
                    # take-over, and leaving the old kind would make the row
                    # lie forever afterwards. Two ways it happens, and the
                    # lie is different in each. An operator blank
                    # (``ops/blank``) leaves ``kind="operator"``: if the
                    # definition is later removed, ``engine._sweep`` reads
                    # that as "no definition ever built this, never delete
                    # it" and reports a collection an operator made, not one
                    # a since-removed definition did. A SMART definition
                    # leaves ``kind="smart"`` -- ``shape_conflict`` tells an
                    # operator switching the other way to delete the
                    # collection in Plex and let the next pass rebuild it,
                    # which lands here -- and that kind carries
                    # ``ManagedCollection``'s promise that ``member_count``
                    # and the reconcile stamps stay NULL, which the four
                    # lines below are about to falsify. "manual" is the safe
                    # direction and the true story either way: a definition
                    # really does maintain this title's membership now.
                    record.kind = "manual"
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
