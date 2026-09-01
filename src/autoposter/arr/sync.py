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
from autoposter.plex.client import as_int, parse_guids
from autoposter.queue.jobs import enqueue

logger = logging.getLogger(__name__)

# ArrKind.name -> the key parse_guids() uses for that kind's external id.
#
# Public, like ``source_path``/``norm_path``/``shares_tree`` below and for the
# same reason: ``collections/arr_overrides.py`` (roadmap row 89) matches Plex
# items to arr entries by exactly this rule, and two spellings of it would
# eventually disagree about whether an item is registered.
GUID_KEY = {"radarr": "tmdb", "sonarr": "tvdb"}

# A service answering a listing request with nothing at all, for a Plex
# section holding more than this many items, is refused rather than acted
# on. Live the services hold 1,982 movies and 286 series, so a legitimately
# empty answer alongside a section of any real size means the request went
# somewhere else -- a wrong base_url, a proxy swallowing the body, a fresh
# or restored instance. Acting on it under ``add_existing`` would POST the
# entire Plex library into that instance.
_EMPTY_LISTING_MAX_TRIVIAL_ITEMS = 10


class ArrSyncRefused(RuntimeError):
    """The service's answers do not describe the instance this sync was
    configured for. Nothing is compared and nothing is added."""


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
    present_by_path: int
    skipped_no_path: int
    skipped_path_taken: int
    skipped_root_path: int
    failed: int
    titles: list[str]
    misassignments: list[str]


def external_id(item, guid_key: str) -> str | None:
    """The external id an arr instance would know this Plex item by.

    Public for the reason ``GUID_KEY`` above is.
    """
    guids = parse_guids([g.id for g in getattr(item, "guids", None) or []])
    return guids.get(guid_key)


def source_path(item, kind: ArrKind) -> str | None:
    """The Plex-side path to register.

    A movie's location is its file; the service registers a folder, so the
    parent directory is used. A show's location is already the series
    directory, and is used as-is.

    Public because the id-mismatch view (api/mismatches.py) pairs arr entries
    with Plex items by exactly this path, the same way the collision guard
    below does. A second implementation of it there would be a second answer
    to "are these the same thing on disk".
    """
    locations = getattr(item, "locations", None) or []
    if not locations:
        return None
    location = locations[0].replace("\\", "/")
    if kind.resource == "movie":
        parent, _, _ = location.rpartition("/")
        return parent or None
    return location


def norm_path(path: str) -> str:
    """The normalisation ``ArrClient.paths_in`` keys its map by -- forward
    slashes, no trailing slash, case preserved. Shared with the id-mismatch
    view so both sides of that comparison are normalised identically."""
    return path.replace("\\", "/").rstrip("/")


def shares_tree(path: str, other: str) -> bool:
    """True when two normalised paths are the same directory, or one holds
    the other. Segment-wise, so ``/mnt/media2`` does not match ``/mnt/media``.

    Public because the id-mismatch view (api/mismatches.py) uses it for the
    same root-folder sanity check ``sync_section``'s guard below runs, before
    it, too, trusts an arr instance's answers.
    """
    return path == other or path.startswith(other + "/") or other.startswith(path + "/")


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
    items: list,
    kind: ArrKind,
    settings: ArrSyncSettings,
    dry_run: bool = True,
) -> ArrSyncReport:
    """Register every Plex item in ``items`` that ``client``'s service is missing.

    ``items`` is the section's contents, already listed by the caller --
    listing a Plex section is a blocking call of several seconds and the
    caller offloads it and shares one list between this and
    ``enqueue_unknown_items``. The whole comparison is built from that list
    plus three calls to the service: its root folders, everything it holds
    (one listing, both views derived from it) and its quality profiles.

    Two things are checked before anything is compared, and either one
    refuses the whole pass with ``ArrSyncRefused`` rather than acting on
    answers that cannot be right:

    * the configured ``arr_root`` must share a tree with one of the root
      folders the service actually manages -- an instance managing something
      else entirely is not the one this sync was configured for; and
    * a service reporting that it holds *nothing* while Plex has a
      non-trivial number of items here is treated as a misconfiguration, not
      as a library-sized gap. That answer empties both the id set and the
      path set at once, so every item looks missing *and* the collision
      guard has nothing left to catch it with.

    Under ``dry_run`` (the default) the report is identical to a real run
    except ``added`` is zero and no POST is ever issued -- ``titles`` still
    names every item that would be registered, and a path that would fail to
    map is still counted in ``skipped_no_path``. A path collision is still
    reported under ``dry_run`` too -- that is exactly when an operator wants
    to discover it.

    Before adding, the mapped target path is checked against every path the
    service already has registered (``ArrClient.paths_in``). A service
    can hold the right folder under the *wrong* external id -- a leftover
    misidentification, not a genuine gap -- and comparing ids alone would
    try to register a second entry over that same folder. Such an item is
    not added; it is counted in ``skipped_path_taken`` and a message naming
    both sides (the Plex title/id and the title/id the service currently has
    for that folder) is logged and appended to ``misassignments``, so an
    operator can go and fix the misassignment by hand.

    An item with no external id at all is checked the same way before being
    called unmatchable: if its mapped path is already registered, the
    service plainly already has it -- Plex just has not picked up the id yet
    (an agent lag, typically) -- and it is counted in ``present_by_path``,
    not reported as a gap. Only when the id is absent *and* the path is not
    registered is the item genuinely unmatchable (``skipped_no_id``).

    An item whose mapped path *is* a root -- the configured ``arr_root`` or
    one of the service's own root folders, which happens when a file sits
    directly under the library root with no folder of its own -- is never
    registered: the service would believe that one item owns the entire
    tree, and a later "delete with files" would take the library with it.
    It is counted in ``skipped_root_path``.

    A failure adding one item is logged and counted in ``failed``; it never
    stops the rest of the batch. That includes a malformed external id: a
    guid such as ``tvdb://12345/1/2`` fails to become an integer, and that
    is one item's failure, not the end of the pass.
    """
    guid_key = GUID_KEY[kind.name]

    arr_root = norm_path(settings.arr_root)
    root_folders = [norm_path(path) for path in await client.root_folders()]
    if not any(shares_tree(arr_root, folder) for folder in root_folders):
        raise ArrSyncRefused(
            f"configured arr path {settings.arr_root!r} shares no tree with any root "
            f"folder {kind.name} manages ({root_folders or 'none reported'}) -- probably "
            f"the wrong instance or a bad base_url; nothing was compared or added"
        )

    entries = await client.listing()
    if not entries and len(items) > _EMPTY_LISTING_MAX_TRIVIAL_ITEMS:
        raise ArrSyncRefused(
            f"{kind.name} reports no {kind.resource} entries at all while Plex has "
            f"{len(items)} item(s) in this library -- probably the wrong instance or a "
            f"bad base_url; nothing was compared or added"
        )

    existing = client.ids_in(entries)
    existing_paths = client.paths_in(entries)
    quality_profile_id = await client.quality_profile_id(settings.quality_profile)
    if quality_profile_id is None:
        raise ValueError(f"quality profile {settings.quality_profile!r} not found in {kind.name}")

    roots = {arr_root, *root_folders}

    checked = missing = added = skipped_no_id = present_by_path = 0
    skipped_no_path = skipped_path_taken = skipped_root_path = failed = 0
    titles: list[str] = []
    misassignments: list[str] = []

    for item in items:
        checked += 1

        ext_id = external_id(item, guid_key)
        if ext_id is None:
            unmatched_path = source_path(item, kind)
            mapped_for_check = map_path(unmatched_path, settings.plex_root, settings.arr_root)
            if mapped_for_check is not None and mapped_for_check.rstrip("/") in existing_paths:
                present_by_path += 1
            else:
                skipped_no_id += 1
            continue

        if ext_id in existing:
            continue

        missing += 1

        mapped = map_path(source_path(item, kind), settings.plex_root, settings.arr_root)
        if mapped is None:
            skipped_no_path += 1
            continue

        mapped = mapped.rstrip("/")
        if mapped in roots:
            skipped_root_path += 1
            logger.warning(
                "arr_sync: %r maps to the root %r itself -- not registering an item "
                "that would own the whole tree", item.title, mapped,
            )
            continue

        collision = existing_paths.get(mapped)
        if collision is not None:
            skipped_path_taken += 1
            other_title = collision.get("title")
            other_id = collision.get(kind.id_field)
            message = (
                f"{item.title!r} ({guid_key} {ext_id}) maps to {mapped!r}, already "
                f"registered in {kind.name} as {other_title!r} ({guid_key} {other_id})"
            )
            misassignments.append(message)
            logger.warning("arr_sync path collision: %s", message)
            continue

        titles.append(item.title)

        if dry_run:
            continue

        try:
            payload = _payload(kind, item, ext_id, mapped, quality_profile_id, settings)
            await client.add(payload)
        except Exception:
            logger.warning("failed to add %r to %s", item.title, kind.name, exc_info=True)
            failed += 1
            continue

        added += 1

    return ArrSyncReport(
        checked=checked, missing=missing, added=added,
        skipped_no_id=skipped_no_id, present_by_path=present_by_path,
        skipped_no_path=skipped_no_path,
        skipped_path_taken=skipped_path_taken,
        skipped_root_path=skipped_root_path,
        failed=failed, titles=titles, misassignments=misassignments,
    )


async def enqueue_unknown_items(
    session: AsyncSession, items: list, kind: str, batch_size: int = 500
) -> int:
    """Enqueue every Plex item in ``items`` this service has never recorded.

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

    ``items`` is the section's contents, listed once by the caller and
    shared with ``sync_section`` -- listing a Plex section takes seconds and
    must not happen twice per pass.
    """
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
            tmdb_id=as_int(guids.get("tmdb")),
            tvdb_id=as_int(guids.get("tvdb")),
            imdb_id=guids.get("imdb"),
            year=getattr(item, "year", None),
        )
        job_id = await enqueue(
            session, kind="process_item", payload=asdict(intent), dedupe_key=intent.dedupe_key,
        )
        if job_id is not None:
            enqueued += 1

    return enqueued
