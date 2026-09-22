"""Reconcile regular (list) collections against a source-ordered item list.

Unlike the smart collections in ``reconcile.py``, membership here is explicit
and diffed on every pass, with sync semantics by default: a member the source
no longer selects is removed. A definition may ask for ``append`` instead,
which only ever adds -- see ``member_diff``.

That makes an empty desired-set dangerous. A failed chart fetch returning
nothing would, taken literally, empty a live collection -- so an empty list
means "make no changes", never "remove everything".
"""
import asyncio
import hashlib
import logging
from typing import NamedTuple

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections import groups
from autoposter.collections.poster_title import poster_title_parts
from autoposter.collections.posters import apply_poster, posters_enabled
from autoposter.collections.reconcile import (
    _clear_collection_summary,
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
# definition that sets none of them (see ``_settings_parts``). Appended to,
# never reordered: a new pair at the END leaves the payload of every
# definition that sets none of the new fields byte-identical.
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
    # Roadmap row 222. Not a setting ``apply_collection_settings`` writes to
    # Plex -- it is read by ``posters.apply_poster``'s fifth rung -- but it
    # belongs here for the reason this tuple exists: the short-circuit below
    # (``:314``, "definition_current and not (posters_on and poster_sha256 is
    # None)") returns BEFORE ``apply_poster`` whenever the membership is
    # unchanged and a poster has already been uploaded. A ``poster_url``
    # outside this tuple would therefore be a setting that reads as saved and
    # silently never applies.
    ("poster_url", None),
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
    items: list, summary: str | None, sync_mode: str = "sync", settings=None, config=None
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

    ``config`` folds ``collections.poster_title`` in the same way and one term
    further along -- see ``poster_title.poster_title_parts``, which is where
    the reason and the gate-off byte-identity live. It is last in the payload
    and contributes NOTHING while the gate is off, so every hash already stored
    still matches. Defaulted, like ``settings``, so every existing caller and
    every existing test keeps its current call and its current digest.
    """
    payload = "\x1f".join([
        summary or "",
        *[str(i.ratingKey) for i in items],
        *([] if sync_mode == "sync" else [sync_mode]),
        *_settings_parts(settings),
        *poster_title_parts(config),
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


class _Claimed(NamedTuple):
    """What ``_claim`` found: the listing (fetched here if the caller had
    none), the collection under the title, and either the actions to return
    at once (``stop``) or the claim's own action string to carry on with."""

    existing: dict
    collection: object | None
    stop: list[str] | None
    claim_action: str | None


def _claim(
    section, existing, title, label, adopt, adopt_from, remove_prior, dry_run,
    protect_labels,
) -> _Claimed:
    """The Plex half of ``reconcile_list_collection`` before its database read,
    in ONE ``asyncio.to_thread`` hop (perf workstream C1).

    Every step can block: the fallback listing is a request, ``smart`` and
    ``labels`` are plexapi attributes whose read can reload, and
    ``resolve_collision`` forces a ``reload()`` and, when it adopts, writes a
    label. So the whole run moves to a thread rather than call by call -- the
    ~40-call-site alternative the spec rejects is exactly the one where a lazy
    attribute read gets missed.

    The shape check comes BEFORE ``resolve_collision``, as it always has: a
    smart collection this service already owns would otherwise pass the
    ownership check and go on to ``addItems``, which Plex answers for a smart
    collection by doing nothing useful and reporting success.
    """
    if existing is None:
        existing = {c.title: c for c in section.collections()}
    collection = existing.get(title)
    if collection is None:
        return _Claimed(existing, None, None, None)
    conflict = shape_conflict(collection, title, want_smart=False)
    if conflict is not None:
        return _Claimed(existing, collection, [conflict], None)
    ok, message = resolve_collision(
        collection, label, adopt, adopt_from or [], remove_prior, dry_run,
        protect_labels or [],
    )
    if not ok:
        return _Claimed(existing, collection, [message] if message else [], None)
    return _Claimed(existing, collection, None, message)


class _Written(NamedTuple):
    """What ``_write`` did, as values the loop can read without touching
    plexapi: the (possibly new) collection, the actions in the order they
    happened, the membership delta, whether every setting landed, and the
    collection's rating key for the managed row."""

    collection: object
    created: bool
    actions: list[str]
    added: int
    removed: int
    settings_ok: bool
    rating_key: str


def _write(
    section, collection, title, items, label, summary, summary_asserted, sort,
    sync_mode, settings, config,
) -> _Written:
    """Every Plex write of a non-dry pass, in ONE ``asyncio.to_thread`` hop
    (perf workstream C1).

    The per-member loops are the reason this matters most: ``_enforce_order``
    is a ``reload()`` plus one ``moveItem`` request per displaced member, and
    ``_label_members`` is one ``addLabel`` request per member per tag -- on the
    event loop, a large reordered collection froze every worker for its whole
    length. Order, actions and deltas are exactly the inline code's:

    - create: ``createCollection`` with every item, the ownership label, the
      sort, the summary;
    - update: the summary (helper called unconditionally -- it skips by itself,
      and gating it on the text would starve its lock repair), or the clear
      under ``summary_asserted``; then the diff, and the order under sync only
      (under append the new members arrive in source order at the end, and the
      positions already there are left alone);
    - then the settings and the member labels, on both paths, because the
      settings are in the members hash -- reaching here at all means the
      membership or one of them changed.
    """
    actions: list[str] = []
    created = collection is None
    if created:
        collection = section.createCollection(title=title, items=items, smart=False)
        collection.addLabel(label)
        collection.sortUpdate(sort)
        if summary:
            _edit_collection_summary(collection, summary)
        added, removed = len(items), 0
        actions.append("created %r with %d item(s)" % (title, len(items)))
    else:
        if summary:
            if getattr(collection, "summary", None) != summary:
                actions.append("updated the summary of %r" % title)
            _edit_collection_summary(collection, summary)
        elif summary_asserted and _clear_collection_summary(collection):
            # Row 187: see ``reconcile_list_collection``'s docstring for why
            # only an asserted absence may clear.
            actions.append("cleared the summary of %r" % title)
        adding, removing = member_diff(collection, items, sync_mode)
        if adding:
            collection.addItems(adding)
        if removing:
            collection.removeItems(removing)
        moves = 0 if sync_mode == "append" else _enforce_order(collection, items)
        added, removed = len(adding), len(removing)
        if adding or removing or moves:
            actions.append(
                "updated %r: +%d -%d, %d move(s)" % (title, added, removed, moves)
            )
    settings_actions, settings_ok = apply_collection_settings(
        section, collection, settings, label, config
    )
    actions += settings_actions
    actions += _label_members(items, settings, title)
    return _Written(
        collection, created, actions, added, removed, settings_ok,
        str(getattr(collection, "ratingKey", "") or ""),
    )


async def reconcile_list_collection(
    session: AsyncSession,
    section,
    library: str,
    title: str,
    items: list,
    label: str,
    summary: str | None = None,
    summary_asserted: bool = False,
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
    deltas: dict | None = None,
) -> list[str]:
    """Bring one list collection in line with ``items`` (already in source order).

    ``existing`` is a ``{title: collection}`` map of the section's collections.
    Listing them costs one request that returns every collection in the
    library -- 305 of them on the production Movies section -- so the caller
    reconciling several collections in a row passes one listing in rather than
    paying for it per collection. Omitting it falls back to listing here.

    ``deltas``, when given, is filled with the members this call actually
    added and removed (``{"added": int, "removed": int}``) on a pass that
    wrote. It is an out-param rather than a second return value because every
    caller wants the action strings and only one wants the numbers -- row 19's
    per-collection webhook, which reports what changed. A dry run, an
    unchanged membership and a refused claim all leave it untouched, which is
    exactly the "nothing to announce" case.

    ``sync_mode`` is ``sync`` (the collection is exactly ``items``) or
    ``append`` (``items`` are added; nothing is ever removed and nothing
    already there is moved). See ``member_diff`` and ``_enforce_order``'s
    caller below for what each half of that means.

    ``settings`` is the ``CollectionDefinition`` this collection is built from,
    and carries everything applied *besides* membership -- labels, sort title,
    display mode, hub visibility, member labels. Optional: the direct callers
    that predate definitions pass none and get exactly what they always got.

    ``summary_asserted`` says whether an absent ``summary`` is the definition
    ASSERTING that there is none, which is what licenses the clear on the update
    path below. The engine sets it only when ``_summary_for`` RESOLVED the
    summary: a pull that failed (no TMDB client, an exception, an overview TMDB
    does not hold) falls back to the builder's summary and returns a note, and
    clearing on that fallback would wipe and unlock the text the last healthy
    pass wrote while the same pass reported the summary unchanged. Off by
    default, so a direct caller with no definition behind it -- the provider
    entry points, and the tests -- never reverts a summary it knows nothing
    about. See ``reconcile._clear_collection_summary`` for what the clear does.

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

    claimed = await asyncio.to_thread(
        _claim, section, existing, title, label, adopt, adopt_from,
        adopt_removes_prior_label, dry_run, protect_labels,
    )
    if claimed.stop is not None:
        return claimed.stop
    existing, collection, claim_action = (
        claimed.existing, claimed.collection, claimed.claim_action
    )

    record = (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.library == library, ManagedCollection.title == title
            )
        )
    ).scalar_one_or_none()

    wanted = _members_hash(items, summary, sync_mode, settings, config)
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
        settings_ok = True
        written = None
        if dry_run:
            actions.append("%s %r with %d item(s)" % (
                "would update" if collection else "would create", title, len(items)))
        else:
            written = await asyncio.to_thread(
                _write, section, collection, title, items, label, summary,
                summary_asserted, sort, sync_mode, settings, config,
            )
            if written.created:
                existing[title] = written.collection
            collection = written.collection
            actions += written.actions
            added_count, removed_count = written.added, written.removed
            settings_ok = written.settings_ok

        if deltas is not None and not dry_run:
            # After both write paths, so a create (every item added) and an
            # update (its own diff) report through one statement. `added_count`
            # and `removed_count` are initialised to 0 above, so a branch that
            # wrote nothing reports nothing.
            deltas["added"] = added_count
            deltas["removed"] = removed_count

        if not dry_run:
            if record is None:
                record = ManagedCollection(
                    library=library, title=title, kind="manual",
                    plex_rating_key=written.rating_key,
                    definition_hash=wanted if settings_ok else "",
                )
                session.add(record)
            else:
                record.definition_hash = wanted if settings_ok else ""
                record.plex_rating_key = written.rating_key
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
            session, http, config, collection, record, library, kind, key,
            dry_run=dry_run,
            # Roadmap row 222. ``getattr`` rather than an attribute access:
            # ``settings`` is optional here and is not always a
            # ``CollectionDefinition`` -- several callers pass a stand-in with
            # only the fields they exercise.
            poster_url=getattr(settings, "poster_url", None),
        )
        if message:
            actions.append(message)

    await session.flush()
    return actions
