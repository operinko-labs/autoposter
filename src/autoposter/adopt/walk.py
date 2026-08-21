"""Adopt an existing library without re-rendering it (spec section 5, "One-time
adoption").

Walks a Plex library section and the asset tree beneath it, building
``media_items``/``renders`` rows for whatever artwork already exists on disk.
Every render this creates is marked ``adopted`` and fingerprinted straight off
the file already there -- **nothing here resolves a provider, writes to Plex,
or writes an image.** See ``render.pipeline.render_artifact`` for the
short-circuit that makes the first real pass over an adopted library a no-op.
"""
import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.schema import Config
from autoposter.db.models import MediaItem, Render
from autoposter.plex.client import ResolvedItem, parse_guids
from autoposter.render import naming
from autoposter.render.pipeline import (
    ART_KINDS_FOR,
    _get_or_create_render,
    _upsert_media_item,
    adopted_fingerprint,
    gather_fingerprint_inputs,
)

logger = logging.getLogger(__name__)

# Hashing ~18,000 files is the expensive part of a real run; a log line every
# this many keeps an operator watching the console reassured without spamming it.
_PROGRESS_EVERY = 500


@dataclass(frozen=True)
class AdoptionReport:
    items: int
    renders: int
    missing_assets: int
    skipped: int
    by_kind: dict[str, int]


@dataclass
class _Counters:
    """Mutable running totals for one walk, turned into an ``AdoptionReport`` at the end."""

    items: int = 0
    renders: int = 0
    missing_assets: int = 0
    skipped: int = 0
    hashed: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)


def _as_int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _guids(plex_object) -> dict[str, str]:
    return parse_guids([g.id for g in getattr(plex_object, "guids", None) or []])


def _root_folder(section_locations: list[str], target_path: str, is_directory: bool) -> str | None:
    """Try each of the library's roots, as ``PlexClient.resolve`` does.

    A library can have more than one root path; the first one the item's own
    path is actually inside of wins.
    """
    for library_root in section_locations:
        try:
            return naming.derive_root_folder(library_root, target_path, is_directory=is_directory)
        except ValueError:
            continue
    return None


def _resolved_movie(section, movie) -> ResolvedItem | None:
    locations = movie.locations
    if not locations:
        return None
    root_folder = _root_folder(section.locations, locations[0], is_directory=False)
    if root_folder is None:
        return None
    guids = _guids(movie)
    return ResolvedItem(
        rating_key=str(movie.ratingKey), library=section.title, kind="movie",
        title=movie.title, year=getattr(movie, "year", None),
        season_number=None, episode_number=None,
        root_folder=root_folder, file_path=locations[0], art_url=None,
        tmdb_id=_as_int(guids.get("tmdb")), tvdb_id=_as_int(guids.get("tvdb")),
        imdb_id=guids.get("imdb"),
    )


def _resolved_show(section, show) -> ResolvedItem | None:
    locations = show.locations
    if not locations:
        return None
    root_folder = _root_folder(section.locations, locations[0], is_directory=True)
    if root_folder is None:
        return None
    guids = _guids(show)
    return ResolvedItem(
        rating_key=str(show.ratingKey), library=section.title, kind="show",
        title=show.title, year=getattr(show, "year", None),
        season_number=None, episode_number=None,
        root_folder=root_folder, file_path=None, art_url=None,
        tmdb_id=_as_int(guids.get("tmdb")), tvdb_id=_as_int(guids.get("tvdb")),
        imdb_id=guids.get("imdb"),
    )


def _resolved_season(section, show, season, root_folder: str) -> ResolvedItem:
    # Seasons and episodes carry no location of their own -- their artifacts
    # live under the show's folder, so root_folder is passed down rather than
    # derived again.
    guids = _guids(season)
    return ResolvedItem(
        rating_key=str(season.ratingKey), library=section.title, kind="season",
        title=season.title, year=getattr(season, "year", None),
        season_number=season.index, episode_number=None,
        root_folder=root_folder, file_path=None, art_url=None,
        tmdb_id=_as_int(guids.get("tmdb")), tvdb_id=_as_int(guids.get("tvdb")),
        imdb_id=guids.get("imdb"), parent_rating_key=str(show.ratingKey),
    )


def _resolved_episode(section, season, episode, root_folder: str) -> ResolvedItem:
    guids = _guids(episode)
    return ResolvedItem(
        rating_key=str(episode.ratingKey), library=section.title, kind="episode",
        title=episode.title, year=getattr(episode, "year", None),
        season_number=episode.parentIndex, episode_number=episode.index,
        root_folder=root_folder, file_path=None, art_url=None,
        tmdb_id=_as_int(guids.get("tmdb")), tvdb_id=_as_int(guids.get("tvdb")),
        imdb_id=guids.get("imdb"), parent_rating_key=str(season.ratingKey),
    )


def _hash_file(path: Path) -> str:
    """SHA-256 of a file already confirmed to exist. Blocking -- call via ``asyncio.to_thread``."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _existing_render(session: AsyncSession, rating_key: str, art_kind: str) -> Render | None:
    return (
        await session.execute(
            select(Render)
            .join(MediaItem, Render.item_id == MediaItem.id)
            .where(MediaItem.rating_key == rating_key, Render.art_kind == art_kind)
        )
    ).scalar_one_or_none()


async def _adopt_item(
    session: AsyncSession, config: Config, resolved: ResolvedItem, dry_run: bool, counters: _Counters,
) -> None:
    counters.items += 1
    # Upserted unconditionally, one per item, regardless of what its art kinds
    # turn out to need below -- a media_items row exists for everything walked,
    # there may already be one from a webhook, and rating_key is unique.
    media_item = None
    if not dry_run:
        media_item = await _upsert_media_item(session, resolved)

    for art_kind in ART_KINDS_FOR[resolved.kind]:
        target = naming.asset_path(
            config, resolved.library, resolved.root_folder, art_kind,
            resolved.season_number, resolved.episode_number,
        )
        # assets_root can be an NFS mount; keep every stat() off the event loop.
        if not await asyncio.to_thread(target.exists):
            counters.missing_assets += 1
            continue

        existing = await _existing_render(session, resolved.rating_key, art_kind)
        if existing is not None and not existing.adopted:
            # A real fingerprint means this item was already processed
            # properly -- adoption must not clobber it.
            counters.skipped += 1
            continue

        base_sha = await asyncio.to_thread(_hash_file, target)
        counters.hashed += 1
        if counters.hashed % _PROGRESS_EVERY == 0:
            logger.info("adoption: hashed %d files", counters.hashed)

        text_inputs, asset_hashes = await gather_fingerprint_inputs(config, resolved, art_kind)
        fingerprint = adopted_fingerprint(config, art_kind, base_sha, text_inputs, asset_hashes)

        counters.renders += 1
        counters.by_kind[art_kind] = counters.by_kind.get(art_kind, 0) + 1

        if dry_run:
            continue

        render = await _get_or_create_render(session, media_item, art_kind, target)
        render.base_sha256 = base_sha
        render.fingerprint = fingerprint
        render.status = "rendered"
        render.adopted = True
        await session.commit()


async def adopt_library(
    session: AsyncSession, config: Config, section, dry_run: bool = True,
) -> AdoptionReport:
    """Walk one Plex library section and adopt whatever art already exists there.

    ``section.all()`` returns items with their guids already populated in one
    call. For a show library that call yields the shows themselves; their
    seasons and episodes are walked underneath, since each carries its own
    artifacts. ``dry_run=True`` (the default) computes and reports everything
    and writes no rows -- the mode to run before cutover.
    """
    counters = _Counters()
    top_level = await asyncio.to_thread(section.all)
    for top in top_level:
        kind = getattr(top, "type", None)
        if kind == "movie":
            resolved = _resolved_movie(section, top)
            if resolved is not None:
                await _adopt_item(session, config, resolved, dry_run, counters)
        elif kind == "show":
            resolved_show = _resolved_show(section, top)
            if resolved_show is None:
                continue
            await _adopt_item(session, config, resolved_show, dry_run, counters)
            seasons = await asyncio.to_thread(top.seasons)
            for season in seasons:
                resolved_season = _resolved_season(section, top, season, resolved_show.root_folder)
                await _adopt_item(session, config, resolved_season, dry_run, counters)
                episodes = await asyncio.to_thread(season.episodes)
                for episode in episodes:
                    resolved_episode = _resolved_episode(
                        section, season, episode, resolved_show.root_folder
                    )
                    await _adopt_item(session, config, resolved_episode, dry_run, counters)

    return AdoptionReport(
        items=counters.items, renders=counters.renders,
        missing_assets=counters.missing_assets, skipped=counters.skipped,
        by_kind=counters.by_kind,
    )
