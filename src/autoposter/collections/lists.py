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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.reconcile import has_label
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)


def _members_hash(items: list, summary: str | None) -> str:
    payload = "\x1f".join([summary or "", *[str(i.ratingKey) for i in items]])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _enforce_order(collection, desired: list) -> int:
    """Move only the items that are out of place. Returns moves made."""
    current = [str(i.ratingKey) for i in collection.items()]
    wanted = [str(i.ratingKey) for i in desired]
    if current == wanted:
        return 0
    moves = 0
    previous = None
    for item in desired:
        current = [str(i.ratingKey) for i in collection.items()]
        position = current.index(str(item.ratingKey))
        expected = 0 if previous is None else current.index(str(previous.ratingKey)) + 1
        if position != expected:
            collection.moveItem(item, after=previous)
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
    dry_run: bool = True,
) -> list[str]:
    """Bring one list collection in line with ``items`` (already in source order)."""
    if not items:
        return [
            "%r: source returned no items; leaving the collection untouched" % title
        ]

    existing = {collection.title: collection for collection in section.collections()}
    collection = existing.get(title)

    if collection is not None:
        collection.reload()
        if not has_label(collection, label):
            return [
                "conflict: %r exists without the %r label; leaving it untouched"
                % (title, label)
            ]

    record = (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.library == library, ManagedCollection.title == title
            )
        )
    ).scalar_one_or_none()

    wanted = _members_hash(items, summary)
    if collection is not None and record is not None and record.definition_hash == wanted:
        return []

    if dry_run:
        return ["%s %r with %d item(s)" % (
            "would update" if collection else "would create", title, len(items))]

    actions: list[str] = []
    if collection is None:
        collection = section.createCollection(title=title, items=items, smart=False)
        collection.addLabel(label)
        collection.sortUpdate("custom")
        if summary:
            collection.editSummary(summary)
        actions.append("created %r with %d item(s)" % (title, len(items)))
    else:
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

    await session.flush()
    return actions
