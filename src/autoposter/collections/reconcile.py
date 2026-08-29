"""Create and update the Common Sense smart collections in Plex.

These are Plex-native smart collections, so there is no membership to
maintain -- Plex evaluates the filter live. The entire job is to keep each
collection's filter matching what the library's ratings imply.

Two safety rules shape this module. Nothing is ever deleted, and nothing
without our ownership label is ever modified: the Movies library holds 305
collections of which only a handful are ours, the rest being Plex's own
franchise collections, another tool's, or hand-made by the operator.
"""
import hashlib
import logging
from types import SimpleNamespace

import httpx
from plexapi.utils import joinArgs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections import groups
from autoposter.collections.buckets import Bucket, derive_buckets
from autoposter.collections.filters import parse_filters
from autoposter.collections.posters import apply_poster, posters_enabled
from autoposter.collections.search_url import (
    SearchAttributeNotAvailable,
    SearchProducedNothing,
    TagValueNotFound,
    build_search_url,
)
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)

LIBTYPES = {"Movie": "movie", "Show": "show"}

# Row 49 retired the module constants ``SEPARATOR_TITLE`` and
# ``SEPARATOR_SUMMARY`` that used to live here: neither has a production
# reader any more (the content-ratings divider is one of ten now, driven by
# ``groups.separator_specs`` rather than this module), and the two tests that
# used them call ``groups.separator_title("content_ratings")`` /
# ``groups.separator_summary("content_ratings")`` directly instead -- one
# spelling of it, in ``groups.py``. The byte-history the retired constants
# would have documented is not lost: ``separator_hash`` below still has to
# reproduce the shipped digest exactly, and
# ``tests/test_collection_group_separators.py``'s ``SHIPPED_TITLE`` /
# ``SHIPPED_SUMMARY`` / ``SHIPPED_HASH`` pin it as literals.


def separator_hash(title: str, summary: str, sort_title: str) -> str:
    """Hash one separator's whole desired state.

    Was a module CONSTANT: there was one separator, its desired state never
    varied by library, and ``definition_hash``'s ``Bucket`` shape does not fit
    it. Row 49 made separators plural, so the constant became this -- and it
    computes the SAME payload in the SAME order, which is what keeps the
    shipped digest reproducible byte for byte
    (``tests/test_collection_group_separators.py`` pins it). Anything else
    would give every live server a spurious re-write of a collection whose
    desired state had not changed, because the hash is what a pass
    short-circuits on.
    """
    return hashlib.sha256(
        "\x1f".join([title, summary, sort_title]).encode("utf-8")
    ).hexdigest()


def definition_hash(bucket: Bucket, settings=None, url: str = "") -> str:
    """Hash the desired filter, summary, and ride-along settings.

    ``url`` is the BUILT query string this bucket's collection stores in Plex,
    and it is in the payload for the reason phase 10a-2 exists: the family's
    write path moved from plexapi's ``filters=`` grammar onto the raw-POST 9b
    one, and a hash over the bucket's VALUES alone would be unchanged by that
    move -- so the first pass after the migration would find every hash current,
    skip every bucket, and leave every collection's stored filter in the retired
    grammar forever. Folding the URI is what makes the one-time re-PUT happen,
    and what makes it happen exactly once. It defaults to empty so the shape of
    a bucket's hash without one is still computable, which this module's own
    tests rely on.

    ``settings`` folds in the same way ``lists._settings_parts`` folds it into
    the list-collection members hash, and for the same reason: a pass
    short-circuits on this hash, so a definition whose only edit was a new
    label or sort title would otherwise be recognised as already current and
    the edit would never be applied. Imported locally -- ``lists.py`` imports
    from this module at load time, so a module-level import here would be a
    cycle. Settings contribute nothing at their defaults, so a definition that
    sets none hashes to the same string it would have without this term.
    """
    from autoposter.collections.lists import _settings_parts

    payload = "\x1f".join([
        bucket.title, bucket.summary, *bucket.values, url,
        *_settings_parts(settings),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_labels(collection) -> None:
    """Populate ``collection.labels`` from the server.

    ``labels`` is a cached_data_property plexapi never populates from
    ``section.collections()`` results -- reading it relies on an implicit
    reload gated on ``plexapi.autoreload``, which can be turned off. Force
    the reload explicitly rather than depend on that global setting.
    ``reload()``, never ``refresh()``: refresh() re-scans metadata on Plex.

    Every reader below is pure, so this is called exactly once per
    collection: three readers each forcing their own reload cost three GETs
    per title collision, roughly 150 wasted requests per Movies pass.
    """
    collection.reload()


def has_label(collection, label: str) -> bool:
    """Pure reader -- ``load_labels`` must have been called on ``collection``."""
    return any(tag.tag == label for tag in (getattr(collection, "labels", None) or []))


def prior_tool_label(collection, adopt_from: list[str]) -> str | None:
    """Return the first label ``collection`` carries that belongs to a tool
    being replaced, or ``None`` if it carries none of them.

    Pure reader -- ``load_labels`` must have been called on ``collection``
    first. A collection with no label at all (the operator's hand-made ones)
    must never match here.
    """
    tags = {tag.tag for tag in (getattr(collection, "labels", None) or [])}
    for candidate in adopt_from:
        if candidate in tags:
            return candidate
    return None


def protected_label(collection, protect_labels: list[str]) -> str | None:
    """Return the first label ``collection`` carries that marks it as
    belonging to a tool this service must never touch, or ``None`` if it
    carries none of them.

    Checked before ownership or adoption -- a protected label wins even when
    the collection also carries our own label or an ``adopt_from`` label.
    Pure reader -- ``load_labels`` must have been called on ``collection``.
    """
    tags = {tag.tag for tag in (getattr(collection, "labels", None) or [])}
    for candidate in protect_labels:
        if candidate in tags:
            return candidate
    return None


def claim_ownership(collection, label: str, prior: str, remove_prior: bool) -> None:
    """Claim a prior tool's collection by adding our ownership label.

    The prior label is kept by default -- keeping both is reversible, a run
    that strips labels is not -- and removed only when ``remove_prior`` is set.
    """
    collection.addLabel(label)
    if remove_prior:
        collection.removeLabel(prior)


def resolve_collision(
    collection,
    label: str,
    adopt: bool,
    adopt_from: list[str],
    remove_prior: bool,
    dry_run: bool,
    protect_labels: list[str] = (),
) -> tuple[bool, str | None]:
    """Decide what to do with an existing collection at a title collision.

    Shared by both reconcilers so the ownership rule cannot drift between
    them. Returns ``(ok, message)``: ``ok`` is ``True`` when the caller
    should proceed to reconcile the collection normally -- it already
    carries our label, or was just claimed -- and ``False`` for a true
    conflict, a protected collection, or an eligible adoption still held
    back by ``dry_run``. ``message`` is the action to report, or ``None``
    when there is nothing to say (the collection was already ours).

    The protected-label check runs first and wins unconditionally -- before
    ownership, before adoption -- so a collection carrying a protected label
    is never claimed even if it also carries an ``adopt_from`` label.

    This is the one place the labels are fetched: every reader it calls is
    pure, so one collision costs one GET rather than one per check.
    """
    load_labels(collection)

    protecting = protected_label(collection, protect_labels)
    if protecting is not None:
        return False, (
            "protected: %r carries %r; leaving it untouched"
            % (collection.title, protecting)
        )

    if has_label(collection, label):
        return True, None

    prior = prior_tool_label(collection, adopt_from) if adopt else None
    if prior is None:
        return False, (
            "conflict: %r exists without the %r label; leaving it untouched"
            % (collection.title, label)
        )

    if dry_run:
        return False, "would adopt %r (currently labelled %r)" % (collection.title, prior)

    claim_ownership(collection, label, prior, remove_prior)
    return True, "claimed %r (was labelled %r)" % (collection.title, prior)


def shape_conflict(collection, title: str, want_smart: bool) -> str | None:
    """Roadmap 9c / C11: an existing collection whose SHAPE the definition changed.

    A Plex collection is either smart -- Plex evaluates a stored filter and owns
    the membership -- or it is a list, whose membership this service maintains
    item by item. A definition that switches ``builder:`` between the two kinds
    is asking for the second thing to happen to a collection that is the first,
    and there is no edit that converts one into the other.

    Kometa's answer is to DELETE the collection and recreate it
    (modules/builder.py:1768-1772), silently, in the middle of a pass. That
    crosses the whole design of this service's delete guards: nothing is deleted
    without an ownership label, a ``managed_collections`` row, a
    ``delete_unconfigured`` opt-in and a ``max_deletes`` cap, and a conversion
    would bypass all four. So this refuses, and names the two manual paths --
    which are also the two the operator would want: keep the collection and
    rename the definition, or delete the collection and let the definition
    rebuild it.

    The second path is worded conditionally on purpose. This check runs BEFORE
    ownership is resolved (both callers, deliberately -- see their comments), so
    the collection under this title may belong to another tool entirely, and
    "delete it in Plex" is not advice to give about a stranger's collection.
    Neither path writes anything either way; this is the message being honest
    about which of the two the reader is in.

    Returns the refusal message, or ``None`` when the shapes agree -- the
    ``(ok, message)`` idiom ``resolve_collision`` above already uses, and not an
    exception, deliberately: a definition's own configuration error must cost
    that definition its pass and nothing else, and an exception out of either
    reconciler reaches ``reconcile_libraries``' per-library rollback, which
    would undo the OTHER definitions' work in the same library.

    A missing ``smart`` attribute reads as a list collection. plexapi casts it
    from an XML attribute that defaults to ``'0'`` (pinned in
    ``tests/test_plexapi_collection_contract.py``), so absence is the same
    defensiveness ``has_label`` applies to ``labels``.
    """
    is_smart = bool(getattr(collection, "smart", False))
    if is_smart == want_smart:
        return None
    have, wanted = ("smart", "list") if is_smart else ("list", "smart")
    return (
        "shape conflict: %r already exists in Plex as a %s collection and this "
        "definition builds a %s one. Plex cannot convert one into the other, and "
        "this service will not delete and recreate it. Either rename the "
        "definition so it builds a new collection, or -- if %r is yours to "
        "delete -- delete it in Plex and let the next pass create it."
        % (title, have, wanted, title)
    )


# plexapi's own mapping, reproduced so a no-op mode write can be skipped by
# comparing against ``Collection.collectionMode`` (an int). Pinned against
# ``Collection.modeUpdate``'s source in the plexapi contract test, so a rename
# upstream fails here rather than writing the wrong mode to a live server.
COLLECTION_MODES = {"default": -1, "hide": 0, "hideItems": 1, "showItems": 2}


def _apply_labels(collection, definition, label: str, config) -> list[str]:
    """Row 29: the definition's labels on the collection object.

    Additive by default. ``label_sync`` makes the definition's labels plus the
    ownership label authoritative and removes the rest -- except a protected or
    ``adopt_from`` label, which this never strips: those mark another tool's
    claim, and giving one up is what ``adopt_removes_prior_label`` decides
    deliberately and once. A sync that quietly undid the label
    ``claim_ownership`` had just been told to keep would be the same bug in
    the opposite direction.
    """
    wanted = list(dict.fromkeys(definition.labels))
    if not wanted and not definition.label_sync:
        return []

    load_labels(collection)
    current = {tag.tag for tag in (getattr(collection, "labels", None) or [])}
    adding = [tag for tag in wanted if tag not in current]
    removing: list[str] = []
    if definition.label_sync:
        collections = getattr(config, "collections", None)
        keep = {
            label, *wanted,
            *(getattr(collections, "adopt_from", None) or []),
            *(getattr(collections, "protect_labels", None) or []),
        }
        removing = sorted(current - keep)

    for tag in adding:
        collection.addLabel(tag)
    for tag in removing:
        collection.removeLabel(tag)

    if not adding and not removing:
        return []
    return ["labelled %r: +%d -%d" % (collection.title, len(adding), len(removing))]


def _apply_sort_title(collection, definition) -> list[str]:
    """Row 104's sort title, on create and on every later edit of it.

    ``editSortTitle`` is the one collection edit route proven working against
    the live server (see ``_edit_collection_summary``), so it is used as-is.
    """
    wanted = definition.sort_title
    if wanted is None or getattr(collection, "titleSort", None) == wanted:
        return []
    collection.editSortTitle(wanted)
    return ["set the sort title of %r to %r" % (collection.title, wanted)]


def _apply_mode(collection, definition) -> list[str]:
    """Row 104's display mode."""
    wanted = definition.collection_mode
    if wanted is None or getattr(collection, "collectionMode", None) == COLLECTION_MODES[wanted]:
        return []
    collection.modeUpdate(mode=wanted)
    return ["set the display mode of %r to %r" % (collection.title, wanted)]


def _apply_hub(section, collection, definition) -> list[str]:
    """Row 68: pin the collection to Plex's recommendation hubs.

    Contained, unlike the writes above: promoting a collection to a hub is a
    Plex Pass feature, so on a server without one every call here fails and
    that must cost the definition an action string rather than the library its
    pass. Nothing is read back from the exception -- the same rule the engine
    applies to a builder's.

    ``None`` leaves a flag alone: "not visible on the home page" and "this
    definition does not manage the home page" are different requests, and only
    the second can be expressed by omitting the setting.
    """
    flags = (definition.visible_library, definition.visible_home, definition.visible_shared)
    if all(flag is None for flag in flags) and definition.hub_priority is None:
        return []

    actions: list[str] = []
    try:
        hub = collection.visibility()
        if any(flag is not None for flag in flags):
            hub = hub.updateVisibility(
                recommended=definition.visible_library,
                home=definition.visible_home,
                shared=definition.visible_shared,
            )
            actions.append(
                "set the hub visibility of %r (library=%s, home=%s, shared=%s)"
                % (collection.title, *flags)
            )
        if definition.hub_priority is not None:
            actions += _move_hub(section, hub, definition.hub_priority, collection.title)
    except Exception:
        logger.exception(
            "could not set the hub visibility of %r; a Plex Pass is required",
            collection.title,
        )
        # Appended, not returned: a visibility write that already succeeded
        # above must not be reported as total failure just because the move
        # after it failed.
        actions.append(
            "could not set the hub visibility of %r: see logs (a Plex Pass is "
            "required for hub pinning)" % collection.title
        )
    return actions


def _move_hub(section, hub, priority: int, title: str) -> list[str]:
    """Put ``hub`` at ``priority`` among the library's managed recommendations.

    plexapi moves a hub *after* another one rather than to an index, so the
    index is turned into the hub it should follow -- and the hub being moved is
    taken out of that listing first, or a hub already at position 3 would be
    asked to move after itself. A priority past the end lands it last, which is
    what an operator asking for "near the bottom" of a list whose length they
    cannot see meant.
    """
    others = [
        other for other in section.managedHubs()
        if getattr(other, "identifier", None) != hub.identifier
    ]
    index = min(priority, len(others))
    hub.move(after=others[index - 1] if index else None)
    return ["moved %r to position %d in the managed recommendations" % (title, index)]


def apply_collection_settings(section, collection, definition, label: str, config) -> list[str]:
    """Every per-definition setting that lives on the collection *object*.

    Shared by both reconcilers -- the list collections in ``lists.py`` and the
    Common Sense family here -- so a definition means the same thing whichever
    kind of collection it builds, and so the ownership and containment rules
    cannot drift between two copies of this.

    Never called under ``dry_run``: every step writes to Plex. ``definition``
    is None for the direct callers that predate definitions (most tests, and
    the separator, which owns its own sort title).
    """
    if definition is None:
        return []
    return [
        *_apply_labels(collection, definition, label, config),
        *_apply_sort_title(collection, definition),
        *_apply_mode(collection, definition),
        *_apply_hub(section, collection, definition),
    ]


def create_blank_collection(section, libtype: str, title: str):
    """Create an empty collection via a raw POST, and return it.

    ``section.createCollection`` raises ``BadRequest`` when given no items --
    plexapi has no way to create an empty collection through its normal API.
    Kometa's own client resorts to the same direct POST for exactly this
    reason (Kometa v2.4.8 ``modules/plex.py:1602``): a ``uri`` that names no
    item keys is what produces a collection with zero members.

    Verified against the installed plexapi 4.18.2 (pinned alongside this in
    ``tests/test_plexapi_collection_contract.py``): ``PlexServer._uriRoot``
    still returns ``f"server://{machineIdentifier}/com.plexapp.plugins.library"``,
    ``plexapi.utils.joinArgs`` still builds a URL-encoded query string from a
    dict, and ``PlexServer.query`` still accepts a ``method`` override to
    issue the POST instead of its default GET.

    The title is a parameter because the separator is no longer the only blank
    collection this service creates: roadmap row 28's ``blank`` operator
    endpoint makes one on demand, and two spellings of this POST would be two
    chances to get the ``uri`` wrong.
    """
    server = section._server
    args = {
        "type": 1 if libtype == "movie" else 2,
        "title": title,
        "smart": 0,
        "sectionId": section.key,
        "uri": "%s/library/metadata" % server._uriRoot(),
    }
    server.query("/library/collections%s" % joinArgs(args), method=server._session.post)
    return section.collection(title)


def _edit_collection_summary(collection, summary: str) -> None:
    """Set a collection's summary through the item-level route, via a raw PUT.

    ``Collection.editSummary`` cannot be used. In plexapi 4.18.2 it goes
    through ``PlexPartialObject.edit`` (``base.py:725``), which for a
    collection dispatches to ``section()._edit`` and issues
    ``PUT /library/sections/{id}/all?type=18&id={ratingKey}&summary.value=...``.
    Against Plex 1.43.3.10896-cb3ebc72d that route returns **404** (an HTML
    not-found body) for every collection summary, so every summary edit in
    this service failed -- the first live reconcile pass rolled back at the
    separator and left ``managed_collections`` empty.

    Bisected request by request against the live server: the same route with
    ``summary.value`` for a movie (``type=1``) returns 200, and the same route
    for a collection's ``title.value``/``titleSort.value`` returns 200 -- only
    collection + summary is broken. ``PUT /library/metadata/{ratingKey}?``
    ``summary.value=...&summary.locked=1`` returns 200. So the item-level
    route is what is used here, with the same raw-query idiom
    ``create_blank_collection`` above needs for its POST. ``editSortTitle`` is
    deliberately left alone: its route is proven working.

    A summary that already matches writes nothing, following this module's
    rule that an unchanged pass issues no requests -- but only when the
    field is already locked. ``editSummary(locked=True)`` always locked the
    field; skipping on text alone would leave a matching-but-unlocked
    summary unlocked forever -- exactly the Kometa-era separators' starting
    state, which a later Plex metadata refresh could then clear. ``fields``
    is a ``cached_data_property`` populated the same lazy way ``labels`` is
    (see ``load_labels``) -- callers reconciling an existing collection call
    ``resolve_collision``, which reloads it first. Missing or empty
    ``fields`` is treated as NOT locked, the same defensiveness ``has_label``
    uses for ``labels``.
    """
    locked = any(
        field.name == "summary" and field.locked
        for field in (getattr(collection, "fields", None) or [])
    )
    if getattr(collection, "summary", None) == summary and locked:
        return
    server = collection._server
    args = {"summary.value": summary, "summary.locked": 1}
    server.query(
        "/library/metadata/%s%s" % (collection.ratingKey, joinArgs(args)),
        method=server._session.put,
    )


async def reconcile_separator(
    session: AsyncSession,
    section,
    library_name: str,
    libtype: str,
    label: str,
    spec: groups.SeparatorSpec,
    existing: dict,
    stored: dict,
    adopt: bool,
    adopt_from: list[str],
    adopt_removes_prior_label: bool,
    dry_run: bool,
    protect_labels: list[str],
    http: httpx.AsyncClient | None = None,
    config=None,
) -> list[str]:
    """One group's blank divider: created, kept current, never populated.

    Same ownership and adoption rules as every other collection this service
    manages -- the shared ``resolve_collision`` -- and nothing here ever calls
    ``addItems``. It owns its own sort title (the ``spec``'s), which is why the
    shared ``apply_collection_settings`` is deliberately not called on it: that
    would hand it the group's MEMBER prefix and sink the heading into its own
    block.

    Was ``_reconcile_separator``, a private routine over three module constants,
    reached only from ``reconcile_content_ratings``. Row 49 made separators
    plural, so it takes a ``SeparatorSpec`` and the engine drives it once per
    active group -- which is the whole of "generalise the one into N".

    **Deletion is not here, by design.** A group that stops having collections
    stops being named by ``groups.separator_titles``, and its divider becomes an
    ordinary candidate for ``engine._sweep`` -- through the ownership label, the
    managed row, any protecting label, ``delete_unconfigured`` (off by default,
    and off means reported) and ``max_deletes``. Nothing about a heading earns
    it a shortcut past guards every other collection has.
    """
    collection = existing.get(spec.title)
    record = stored.get(spec.title)
    actions: list[str] = []

    if record is not None and record.kind == "operator":
        # The ops/blank hazard. An operator blanked a collection under a title
        # a group now claims; the endpoint gave it OUR ownership label, so
        # resolve_collision would approve it and this routine would write a
        # summary and a sort title over something nobody asked it to touch.
        # The managed row is what says whose it is, and "operator" is the kind
        # ``api/collections_builders.py`` writes precisely so this decision is
        # possible. Returned, not raised: one contested title must not cost the
        # library its pass.
        return [
            "%r was created by an operator, not by any definition; the %r "
            "group's separator is not written over it" % (spec.title, spec.group)
        ]

    if collection is not None:
        ok, message = resolve_collision(
            collection, label, adopt, adopt_from, adopt_removes_prior_label, dry_run,
            protect_labels,
        )
        if message:
            actions.append(message)
        if not ok:
            return actions

    posters_on = posters_enabled(config, http)
    wanted = separator_hash(spec.title, spec.summary, spec.sort_title)
    definition_current = (
        collection is not None and record is not None
        and record.definition_hash == wanted
    )
    if definition_current and not (posters_on and record.poster_sha256 is None):
        return actions

    if not definition_current:
        if dry_run:
            actions.append(
                "%s %r" % ("would update" if collection else "would create", spec.title)
            )
        else:
            if collection is None:
                collection = create_blank_collection(section, libtype, spec.title)
                collection.addLabel(label)
                actions.append("created %r" % spec.title)
            else:
                actions.append("updated %r" % spec.title)

            _edit_collection_summary(collection, spec.summary)
            collection.editSortTitle(spec.sort_title)

            if record is None:
                record = ManagedCollection(
                    library=library_name, title=spec.title, kind="separator",
                    plex_rating_key=str(getattr(collection, "ratingKey", "") or ""),
                    definition_hash=wanted,
                )
                session.add(record)
            else:
                record.definition_hash = wanted
                record.plex_rating_key = str(getattr(collection, "ratingKey", "") or "")

    if (
        posters_on and collection is not None and record is not None
        and spec.poster_key is not None
    ):
        # A group with no measured artwork stem gets no poster rather than a
        # guessed path: a wrong URL 404s and the collection quietly keeps none,
        # which is harder to spot than an absence
        # (``posters.hosted_poster_url``'s own rule, applied one level up).
        message = await apply_poster(
            session, http, config, collection, record, library_name,
            "separator", spec.poster_key, dry_run=dry_run,
        )
        if message:
            actions.append(message)

    return actions


async def reconcile_content_ratings(
    session: AsyncSession,
    section,
    library_name: str,
    library_type: str,
    label: str,
    dry_run: bool = True,
    adopt: bool = False,
    adopt_from: list[str] | None = None,
    adopt_removes_prior_label: bool = False,
    protect_labels: list[str] | None = None,
    http: httpx.AsyncClient | None = None,
    config=None,
    settings=None,
    resolver=None,
    sort_prefix: str | None = None,
) -> list[str]:
    """Bring this library's Common Sense collections in line with its ratings.

    ``library_name`` is the Plex section name (e.g. ``"Movies"``,
    ``"Kids Movies"``) and is what ``ManagedCollection.library`` is keyed on --
    two libraries of the same ``library_type`` must not collide. ``library_type``
    (``"Movie"``/``"Show"``) only drives title/summary text and ``libtype``.

    ``settings`` is the ``CollectionDefinition`` this family is built from, and
    supplies the per-definition collection settings (labels, sort title,
    display mode, hub visibility) applied to every bucket -- roadmap row 104's
    half of the create path that lives here. One definition names the whole
    family, so they share those settings, which is what Kometa's section
    sort-title prefix is for in the first place: the family sorts as one block.
    The separator is deliberately excluded -- its sort title is the constant
    that makes it a divider. None (the default) applies nothing.

    ``sort_prefix`` is the content-ratings GROUP's sort-title prefix
    (``"!030_"``), resolved engine-side (``collections/groups.py``, roadmap row
    49) and applied per BUCKET below rather than to the definition here -- the
    sentence above is exactly why. The family shares the prefix, which is what
    makes it one block; inside that block each bucket sorts by its own age key
    and its own title. A definition that names its own ``sort_title`` keeps it,
    for every bucket, unchanged. None derives nothing.

    The family's divider is no longer reconciled here -- every group's separator
    is driven by the engine (``engine._separators``), which is what made one
    divider into N (roadmap row 49).

    ``resolver`` is the pass's ``LibraryTagResolver``, which answers both halves
    of this reconcile: what content ratings the library holds, and which Plex
    key each written rating resolves to. Passing the pass's own instance is what
    keeps the whole family to ONE ``listFilterChoices`` round trip, memoised
    beside every other builder's. Omitting it builds one from ``section`` here,
    which is the same class with a private cache -- the fallback
    ``reconcile_smart_collection``'s ``existing=None`` already has, and the
    right answer for a direct caller with no pass around it.

    Returns a description of every action taken -- or, under ``dry_run``,
    every action that would be taken.
    """
    # Both imports are local, and both for the same reason ``definition_hash``
    # imports ``_settings_parts`` locally -- a module-level one would be a
    # cycle. ``smart.py`` imports from this module at load time
    # (smart.py:52-58); ``plex_search`` does not, but it lives in the
    # ``builders`` package, whose ``__init__`` imports ``cs_bucket``, which
    # imports this module. ``filters`` and ``search_url`` have no such edge and
    # are imported at the top.
    from autoposter.collections.builders.plex_search import (
        LibraryTagResolver,
        PlexSearchUnavailable,
    )
    from autoposter.collections.smart import (
        create_smart_collection,
        update_smart_collection,
    )

    libtype = LIBTYPES[library_type]
    if resolver is None:
        # A pass-less caller. ``LibraryTagResolver`` needs only ``library`` and
        # ``run_cache`` off its context, so a private one is a complete context
        # for a single reconcile -- it simply memoises nothing beyond this call.
        resolver = LibraryTagResolver(
            SimpleNamespace(library=library_name, run_cache={}), section, libtype,
        )
    try:
        present = {title for _, title in resolver.choices("content_rating")}
    except PlexSearchUnavailable as refusal:
        # The whole family's input. Returned rather than raised: this reconciler
        # is reached through a SMART builder, and ``engine.py``'s smart dispatch
        # does not wrap ``apply`` -- an escape here would cost the library its
        # entire reconcile because one listing call failed.
        logger.warning("%s: the Common Sense family was not built: %s",
                       library_name, refusal)
        return ["refused the Common Sense collections: %s" % refusal]
    existing = {collection.title: collection for collection in section.collections()}

    stored = {
        row.title: row
        for row in (
            await session.execute(
                select(ManagedCollection).where(ManagedCollection.library == library_name)
            )
        ).scalars()
    }

    posters_on = posters_enabled(config, http)
    actions: list[str] = []
    for bucket in derive_buckets(present, library_type):
        if not bucket.values:
            # An empty filter matches the entire library. Never create one,
            # and leave any existing collection exactly as it is. Addendum 3:
            # Kometa DROPS such a bucket at derive time and we keep it, so this
            # branch is the difference -- and ``cs_bucket.titles()`` still names
            # it, which is what keeps an existing-but-now-empty collection out
            # of the delete sweep's candidate set.
            continue

        collection = existing.get(bucket.title)

        if collection is not None:
            ok, message = resolve_collision(
                collection, label, adopt, adopt_from or [], adopt_removes_prior_label, dry_run,
                protect_labels or [],
            )
            if message:
                actions.append(message)
            if not ok:
                continue

        try:
            # The 9b grammar, under an ``any:`` base -- one term per value
            # joined by ``or=1``, which is the reading this family has always
            # relied on and which the equivalence proof
            # (``tests/test_collection_cs_equivalence.py``) shows selects
            # exactly the items plexapi's comma-joined form did. ``release.desc``
            # is this engine's spelling of the ``originallyAvailableAt:desc``
            # sort plexapi was asked for (``search_sorts.py:83``).
            url = build_search_url(
                parse_filters(
                    {"content_rating": list(bucket.values)},
                    field="params", searching=True, base="any",
                ),
                libtype=libtype,
                sort_by=("release.desc",),
                limit=None,
                resolve_tag=resolver,
            )
        except (
            ValueError, SearchAttributeNotAvailable, SearchProducedNothing,
            TagValueNotFound,
        ) as refusal:
            # Contained to ONE bucket, for the reason above: seventeen working
            # collections must not stop being managed because an eighteenth
            # cannot have its query built.
            #
            # Honestly, about ``TagValueNotFound`` specifically: at THIS call
            # site it is unreachable by construction. ``derive_buckets``
            # filters every bucket's values against ``present``, and
            # ``present`` is exactly the titles this same resolver just
            # returned, so no shipped bucket can fail to resolve -- which is
            # why the test that exercises this branch has to inject a resolver
            # rather than arrange a section. The catch stays because the other
            # three classes are live (``SearchAttributeNotAvailable`` the
            # moment a ``FILTER_ATTRIBUTES`` row's ``field_for`` changes for a
            # libtype) and because ``apply`` is not wrapped upstream of here;
            # it is not a guard against a production failure mode that exists
            # today.
            logger.warning("%s: %r was not built: %s",
                           library_name, bucket.title, refusal)
            actions.append("refused %r: %s" % (bucket.title, refusal))
            continue

        # The hash is computed HERE and not before ``resolve_collision``,
        # because it now folds the URL and building the URL costs a resolution:
        # a protected or foreign collection is skipped without building its
        # query at all. No action string moves -- ``resolve_collision``'s
        # message was already appended before the skip decision.
        # Per BUCKET, not per definition: one definition names the whole
        # family, and the group's prefix is what they share -- inside the block
        # each bucket sorts by its own age key and its own title. That is why
        # the wrap is here and not at the top of the function. Out of band and
        # in front of the hash, for the two reasons
        # ``groups._DerivedSortTitle`` gives.
        bucket_settings = groups.with_derived_sort_title(
            settings, sort_prefix, bucket.title, groups.age_order(bucket.key)
        )
        wanted = definition_hash(bucket, bucket_settings, url)
        record = stored.get(bucket.title)
        definition_current = (
            collection is not None and record is not None and record.definition_hash == wanted
        )
        # An unchanged definition is not on its own a reason to skip: a
        # collection whose definition was already correct when posters were
        # first enabled, or whose poster fetch failed on the pass that created
        # it, still carries a NULL ``poster_sha256`` and would otherwise never
        # be revisited. Once the hash is stored, the skip resumes.
        if definition_current and not (posters_on and record.poster_sha256 is None):
            continue

        if not definition_current:
            if dry_run:
                actions.append(
                    "%s %r -> %s"
                    % ("would update" if collection else "would create",
                       bucket.title, ", ".join(bucket.values))
                )
            else:
                if collection is None:
                    collection = create_smart_collection(
                        section, libtype, bucket.title, url
                    )
                    collection.addLabel(label)
                    actions.append("created %r" % bucket.title)
                else:
                    update_smart_collection(section, collection, url)
                    actions.append("updated %r" % bucket.title)

                _edit_collection_summary(collection, bucket.summary)
                actions += apply_collection_settings(
                    section, collection, bucket_settings, label, config
                )

                if record is None:
                    record = ManagedCollection(
                        library=library_name, title=bucket.title, kind="smart",
                        plex_rating_key=str(getattr(collection, "ratingKey", "") or ""),
                        definition_hash=wanted,
                    )
                    session.add(record)
                else:
                    record.definition_hash = wanted
                    record.plex_rating_key = str(getattr(collection, "ratingKey", "") or "")

        if posters_on and collection is not None and record is not None:
            poster_kind = "content_rating_other" if bucket.key == "other" else "content_rating"
            message = await apply_poster(
                session, http, config, collection, record, library_name,
                poster_kind, bucket.key, dry_run=dry_run,
            )
            if message:
                actions.append(message)

    await session.flush()
    return actions
