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
from autoposter.collections.posters import apply_poster
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


def _create_separator(section, libtype: str):
    """Create the empty ``Ratings Collections`` divider via a raw POST.

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
    """
    server = section._server
    args = {
        "type": 1 if libtype == "movie" else 2,
        "title": SEPARATOR_TITLE,
        "smart": 0,
        "sectionId": section.key,
        "uri": "%s/library/metadata" % server._uriRoot(),
    }
    server.query("/library/collections%s" % joinArgs(args), method=server._session.post)
    return section.collection(SEPARATOR_TITLE)


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

    record = stored.get(SEPARATOR_TITLE)
    if collection is not None and record is not None and record.definition_hash == SEPARATOR_HASH:
        return actions

    if dry_run:
        actions.append(
            "%s %r" % ("would update" if collection else "would create", SEPARATOR_TITLE)
        )
        return actions

    if collection is None:
        collection = _create_separator(section, libtype)
        collection.addLabel(label)
        actions.append("created %r" % SEPARATOR_TITLE)
    else:
        actions.append("updated %r" % SEPARATOR_TITLE)

    collection.editSummary(SEPARATOR_SUMMARY)
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

    if config is not None and http is not None and config.collections.posters:
        message = await apply_poster(
            session, http, config, collection, record, library_name,
            "separator", "content_rating", dry_run=False,
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
) -> list[str]:
    """Bring this library's Common Sense collections in line with its ratings.

    ``library_name`` is the Plex section name (e.g. ``"Movies"``,
    ``"Kids Movies"``) and is what ``ManagedCollection.library`` is keyed on --
    two libraries of the same ``library_type`` must not collide. ``library_type``
    (``"Movie"``/``"Show"``) only drives title/summary text and ``libtype``.

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
        if collection is not None and record is not None and record.definition_hash == wanted:
            continue

        if dry_run:
            actions.append(
                "%s %r -> %s"
                % ("would update" if collection else "would create",
                   bucket.title, ", ".join(bucket.values))
            )
            continue

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

        collection.editSummary(bucket.summary)

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

        if config is not None and http is not None and config.collections.posters:
            poster_kind = "content_rating_other" if bucket.key == "other" else "content_rating"
            message = await apply_poster(
                session, http, config, collection, record, library_name,
                poster_kind, bucket.key, dry_run=False,
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
