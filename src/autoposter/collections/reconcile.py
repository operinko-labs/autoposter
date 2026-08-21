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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.buckets import Bucket, derive_buckets
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)

SORT = "originallyAvailableAt:desc"
LIBTYPES = {"Movie": "movie", "Show": "show"}


def definition_hash(bucket: Bucket) -> str:
    """Hash the desired filter and summary, so an unchanged pass writes nothing."""
    payload = "\x1f".join([bucket.title, bucket.summary, *bucket.values])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def has_label(collection, label: str) -> bool:
    # ``labels`` is a cached_data_property plexapi never populates from
    # ``section.collections()`` results -- reading it relies on an implicit
    # reload gated on ``plexapi.autoreload``, which can be turned off. Force
    # the reload explicitly rather than depend on that global setting.
    # ``reload()``, never ``refresh()``: refresh() re-scans metadata on Plex.
    collection.reload()
    return any(tag.tag == label for tag in (getattr(collection, "labels", None) or []))


def prior_tool_label(collection, adopt_from: list[str]) -> str | None:
    """Return the first label ``collection`` carries that belongs to a tool
    being replaced, or ``None`` if it carries none of them.

    Called only after ``has_label`` has already forced a ``reload()`` on this
    same object, so this reads ``labels`` directly rather than reloading
    again per candidate -- a collection with no label at all (the operator's
    hand-made ones) must never match here.
    """
    tags = {tag.tag for tag in (getattr(collection, "labels", None) or [])}
    for candidate in adopt_from:
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
) -> tuple[bool, str | None]:
    """Decide what to do with an existing collection at a title collision.

    Shared by both reconcilers so the ownership rule cannot drift between
    them. Returns ``(ok, message)``: ``ok`` is ``True`` when the caller
    should proceed to reconcile the collection normally -- it already
    carries our label, or was just claimed -- and ``False`` for a true
    conflict or an eligible adoption still held back by ``dry_run``.
    ``message`` is the action to report, or ``None`` when there is nothing
    to say (the collection was already ours).
    """
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
) -> list[str]:
    """Bring this library's Common Sense collections in line with its ratings.

    ``library_name`` is the Plex section name (e.g. ``"Movies"``,
    ``"Kids Movies"``) and is what ``ManagedCollection.library`` is keyed on --
    two libraries of the same ``library_type`` must not collide. ``library_type``
    (``"Movie"``/``"Show"``) only drives title/summary text and ``libtype``.

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

    await session.flush()
    return actions
