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


def _has_label(collection, label: str) -> bool:
    return any(tag.tag == label for tag in (getattr(collection, "labels", None) or []))


async def reconcile_content_ratings(
    session: AsyncSession,
    section,
    library_type: str,
    label: str,
    dry_run: bool = True,
) -> list[str]:
    """Bring this library's Common Sense collections in line with its ratings.

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
                select(ManagedCollection).where(ManagedCollection.library == library_type)
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

        if collection is not None and not _has_label(collection, label):
            actions.append(
                "conflict: %r exists without the %r label; leaving it untouched"
                % (bucket.title, label)
            )
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
                library=library_type, title=bucket.title, kind="smart",
                plex_rating_key=str(getattr(collection, "ratingKey", "") or ""),
                definition_hash=wanted,
            )
            session.add(record)
        else:
            record.definition_hash = wanted
            record.plex_rating_key = str(getattr(collection, "ratingKey", "") or "")

    await session.flush()
    return actions
