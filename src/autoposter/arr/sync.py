"""Registering Plex items that Radarr or Sonarr do not know about.

Comparison is entirely by external id -- ``tmdb`` for Radarr, ``tvdb`` for
Sonarr: an item Plex owns that the service has never heard of is "missing"
and gets registered against the file already on disk, monitored but never
searched for. Nothing the service already has is ever touched, and nothing
is ever removed -- this module only ever adds.

The one flag that matters more than any other here is the search flag inside
``addOptions``. Left on, or left out, a POST tells the service to go find
and download a release for the item -- across the whole batch this call
registers. Every payload built here pins it to ``False``.
"""
import logging
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.arr.client import ArrClient, ArrKind
from autoposter.arr.paths import map_path
from autoposter.db.models import MediaItem
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import parse_guids
from autoposter.queue.jobs import enqueue

logger = logging.getLogger(__name__)

# ArrKind.name -> the key parse_guids() uses for that kind's external id.
_GUID_KEY = {"radarr": "tmdb", "sonarr": "tvdb"}


@dataclass(frozen=True)
class ArrSyncSettings:
    """How to register a new item. Mirrors the tool being replaced:
    ``add_existing: true``, ``monitor: true``/``all``, ``availability:
    announced``, ``series_type: standard``, ``season_folder: true``, plus the
    Plex-to-service path mapping.
    """

    plex_root: str
    arr_root: str
    quality_profile: str
    monitored: bool = True
    minimum_availability: str = "announced"  # Radarr only
    series_type: str = "standard"  # Sonarr only
    season_folder: bool = True  # Sonarr only


@dataclass(frozen=True)
class ArrSyncReport:
    checked: int
    missing: int
    added: int
    skipped_no_id: int
    skipped_no_path: int
    failed: int
    titles: list[str]


def _external_id(item, guid_key: str) -> str | None:
    guids = parse_guids([g.id for g in getattr(item, "guids", None) or []])
    return guids.get(guid_key)


def _source_path(item, kind: ArrKind) -> str | None:
    """The Plex-side path to register.

    A movie's location is its file; the service registers a folder, so the
    parent directory is used. A show's location is already the series
    directory, and is used as-is.
    """
    locations = getattr(item, "locations", None) or []
    if not locations:
        return None
    location = locations[0].replace("\\", "/")
    if kind.resource == "movie":
        parent, _, _ = location.rpartition("/")
        return parent or None
    return location


def _payload(
    kind: ArrKind, item, ext_id: str, path: str, quality_profile_id: int, settings: ArrSyncSettings
) -> dict:
    payload = {
        "title": item.title,
        kind.id_field: int(ext_id),
        "qualityProfileId": quality_profile_id,
        "rootFolderPath": settings.arr_root,
        "path": path,
        "monitored": settings.monitored,
    }
    if kind.resource == "movie":
        payload["minimumAvailability"] = settings.minimum_availability
        payload["addOptions"] = {"searchForMovie": False}
    else:
        payload["seasonFolder"] = settings.season_folder
        payload["seriesType"] = settings.series_type
        payload["addOptions"] = {"searchForMissingEpisodes": False}
    return payload


async def sync_section(
    client: ArrClient,
    section,
    kind: ArrKind,
    settings: ArrSyncSettings,
    dry_run: bool = True,
) -> ArrSyncReport:
    """Register every Plex item in ``section`` that ``client``'s service is missing.

    ``section.all()`` is called exactly once; the whole comparison is built
    from that single call plus one call each to list what the service
    already holds and to resolve its quality profile.

    Under ``dry_run`` (the default) the report is identical to a real run
    except ``added`` is zero and no POST is ever issued -- ``titles`` still
    names every item that would be registered, and a path that would fail to
    map is still counted in ``skipped_no_path``.

    A failure adding one item is logged and counted in ``failed``; it never
    stops the rest of the batch.
    """
    guid_key = _GUID_KEY[kind.name]
    existing = await client.existing_ids()
    quality_profile_id = await client.quality_profile_id(settings.quality_profile)
    if quality_profile_id is None:
        raise ValueError(f"quality profile {settings.quality_profile!r} not found in {kind.name}")

    checked = missing = added = skipped_no_id = skipped_no_path = failed = 0
    titles: list[str] = []

    for item in section.all():
        checked += 1

        ext_id = _external_id(item, guid_key)
        if ext_id is None:
            skipped_no_id += 1
            continue

        if ext_id in existing:
            continue

        missing += 1

        mapped = map_path(_source_path(item, kind), settings.plex_root, settings.arr_root)
        if mapped is None:
            skipped_no_path += 1
            continue

        titles.append(item.title)

        if dry_run:
            continue

        payload = _payload(kind, item, ext_id, mapped, quality_profile_id, settings)
        try:
            await client.add(payload)
        except Exception:
            logger.warning("failed to add %r to %s", item.title, kind.name, exc_info=True)
            failed += 1
            continue

        added += 1

    return ArrSyncReport(
        checked=checked, missing=missing, added=added,
        skipped_no_id=skipped_no_id, skipped_no_path=skipped_no_path,
        failed=failed, titles=titles,
    )


def _as_int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def enqueue_unknown_items(
    session: AsyncSession, section, kind: str, batch_size: int = 500
) -> int:
    """Enqueue every Plex item in ``section`` this service has never recorded.

    Independent of Radarr/Sonarr: an item with no ``media_items`` row was
    never picked up by a webhook, so this is what makes the library
    self-correct without waiting for one. Capped at ``batch_size`` for the
    same reason the ratings-drift sweep is -- on a first run against a fresh
    database every item is unknown, and enqueuing the whole library at once
    would swamp the worker pool and every provider. Successive runs work
    through the rest.

    The comparison against ``media_items`` is one query -- an anti-join over
    every rating key in the section -- not one query per item. Dedupe is left
    to ``enqueue``'s own ``dedupe_key`` convention, so a second run before the
    first pass's jobs have drained does not queue anything twice.
    """
    items = section.all()
    if not items:
        return 0

    rating_keys = [str(item.ratingKey) for item in items]
    known = set(
        (
            await session.execute(
                select(MediaItem.rating_key).where(MediaItem.rating_key.in_(rating_keys))
            )
        ).scalars()
    )

    enqueued = 0
    for item in items:
        if enqueued >= batch_size:
            break
        if str(item.ratingKey) in known:
            continue

        guids = parse_guids([g.id for g in getattr(item, "guids", None) or []])
        intent = RenderIntent(
            kind=kind,
            title=item.title,
            tmdb_id=_as_int(guids.get("tmdb")),
            tvdb_id=_as_int(guids.get("tvdb")),
            imdb_id=guids.get("imdb"),
            year=getattr(item, "year", None),
        )
        job_id = await enqueue(
            session, kind="process_item", payload=asdict(intent), dedupe_key=intent.dedupe_key,
        )
        if job_id is not None:
            enqueued += 1

    return enqueued
