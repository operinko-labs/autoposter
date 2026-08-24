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

import httpx
from plexapi.utils import joinArgs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.buckets import Bucket, derive_buckets
from autoposter.collections.posters import apply_poster, posters_enabled
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)

SORT = "originallyAvailableAt:desc"
LIBTYPES = {"Movie": "movie", "Show": "show"}

# The "Ratings Collections" separator: a permanently-empty divider for the
# Common Sense age buckets, identical in both libraries. Values measured off
# the live server and the pinned Kometa image, not re-derived here: the
# summary is Kometa's own translation string, and the sort title reproduces
# its ``separator`` template (``defaults/templates.yml``) --
# ``sort_title: <<sort_prefix>><<collection_section>>_!<<title>>`` with
# ``sort_prefix: "!"`` and ``collection_section: "110"``
# (``defaults/both/content_rating_cs.yml``). That prefix is what makes the
# collection sort as a divider instead of alphabetically by title.
SEPARATOR_TITLE = "Ratings Collections"
SEPARATOR_SUMMARY = "Section separator for Ratings Collections."
SEPARATOR_SORT_TITLE = "!110_!" + SEPARATOR_TITLE
# The separator's desired state never varies by library, so its hash is a
# constant -- computed once here rather than by ``definition_hash()``, whose
# ``Bucket`` shape does not fit it.
SEPARATOR_HASH = hashlib.sha256(
    "\x1f".join([SEPARATOR_TITLE, SEPARATOR_SUMMARY, SEPARATOR_SORT_TITLE]).encode("utf-8")
).hexdigest()


def definition_hash(bucket: Bucket) -> str:
    """Hash the desired filter and summary, so an unchanged pass writes nothing."""
    payload = "\x1f".join([bucket.title, bucket.summary, *bucket.values])
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
        return [
            "could not set the hub visibility of %r: see logs (a Plex Pass is "
            "required for hub pinning)" % collection.title
        ]
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


async def _reconcile_separator(
    session: AsyncSession,
    section,
    library_name: str,
    libtype: str,
    label: str,
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
    """The blank ``Ratings Collections`` divider: same ownership and
    adoption rules as every other collection this family manages, but it is
    never populated -- nothing here ever calls ``addItems``.
    """
    collection = existing.get(SEPARATOR_TITLE)
    actions: list[str] = []

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
    record = stored.get(SEPARATOR_TITLE)
    definition_current = (
        collection is not None and record is not None
        and record.definition_hash == SEPARATOR_HASH
    )
    if definition_current and not (posters_on and record.poster_sha256 is None):
        return actions

    if not definition_current:
        if dry_run:
            actions.append(
                "%s %r" % ("would update" if collection else "would create", SEPARATOR_TITLE)
            )
        else:
            if collection is None:
                collection = create_blank_collection(section, libtype, SEPARATOR_TITLE)
                collection.addLabel(label)
                actions.append("created %r" % SEPARATOR_TITLE)
            else:
                actions.append("updated %r" % SEPARATOR_TITLE)

            _edit_collection_summary(collection, SEPARATOR_SUMMARY)
            collection.editSortTitle(SEPARATOR_SORT_TITLE)

            if record is None:
                record = ManagedCollection(
                    library=library_name, title=SEPARATOR_TITLE, kind="separator",
                    plex_rating_key=str(getattr(collection, "ratingKey", "") or ""),
                    definition_hash=SEPARATOR_HASH,
                )
                session.add(record)
            else:
                record.definition_hash = SEPARATOR_HASH
                record.plex_rating_key = str(getattr(collection, "ratingKey", "") or "")

    if posters_on and collection is not None and record is not None:
        message = await apply_poster(
            session, http, config, collection, record, library_name,
            "separator", "content_rating", dry_run=dry_run,
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
    separators: bool = False,
    protect_labels: list[str] | None = None,
    http: httpx.AsyncClient | None = None,
    config=None,
    settings=None,
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

    ``separators`` additionally maintains the blank ``Ratings Collections``
    divider that belongs to this same family -- production always passes it
    from ``config.collections.separators``, which defaults to ``True``; it
    defaults to ``False`` here so that direct callers not concerned with it
    (most tests) do not also need a ``section._server`` double.

    Returns a description of every action taken -- or, under ``dry_run``,
    every action that would be taken.
    """
    present = {choice.title for choice in section.listFilterChoices("contentRating")}
    existing = {collection.title: collection for collection in section.collections()}
    libtype = LIBTYPES[library_type]

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
            # and leave any existing collection exactly as it is.
            continue

        wanted = definition_hash(bucket)
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
                    collection = section.createCollection(
                        title=bucket.title, smart=True, libtype=libtype, sort=SORT,
                        filters={"contentRating": list(bucket.values)},
                    )
                    collection.addLabel(label)
                    actions.append("created %r" % bucket.title)
                else:
                    collection.updateFilters(
                        libtype=libtype, sort=SORT,
                        filters={"contentRating": list(bucket.values)},
                    )
                    actions.append("updated %r" % bucket.title)

                _edit_collection_summary(collection, bucket.summary)
                actions += apply_collection_settings(
                    section, collection, settings, label, config
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

    if separators:
        actions += await _reconcile_separator(
            session, section, library_name, libtype, label,
            existing, stored, adopt, adopt_from or [], adopt_removes_prior_label, dry_run,
            protect_labels or [], http, config,
        )

    await session.flush()
    return actions
