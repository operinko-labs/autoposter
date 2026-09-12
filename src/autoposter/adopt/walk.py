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
from autoposter.db.models import Render
from autoposter.db.refs import item_id_for
from autoposter.plex.client import ResolvedItem, parse_guids
from autoposter.render import naming
from autoposter.render.pipeline import (
    ART_KINDS_FOR,
    _get_or_create_render,
    _should_skip_title,
    _upsert_media_item,
    adopted_fingerprint,
    art_config_for,
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
    # Art kinds this config would never render anyway: the kind is disabled
    # (``artwork.background.enabled: false``) or the title matches a
    # ``skip_tba`` word. Counted separately rather than as missing_assets --
    # deploy/README.md tells the operator to scrutinise that number, and with
    # backgrounds off it would otherwise report ~2,000 phantom gaps.
    skipped_by_config: int = 0
    # Art kinds skipped because the item lacks a number their file name is
    # built from -- see ``naming.missing_number``. Counted separately so the
    # operator sees them: neither a gap on disk nor a deliberate config choice.
    unnumbered: int = 0
    # Row 122: a rerun over an already-adopted library used to report the
    # same `renders` number as the first run, so an operator watching it
    # could not tell a resumed pass made progress. `renders` stays the sum
    # of both, for compatibility with anything already reading it; these two
    # are the split.
    adopted: int = 0
    reconfirmed: int = 0


@dataclass
class _Counters:
    """Mutable running totals for one walk, turned into an ``AdoptionReport`` at the end."""

    items: int = 0
    renders: int = 0
    missing_assets: int = 0
    skipped: int = 0
    skipped_by_config: int = 0
    unnumbered: int = 0
    hashed: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    adopted: int = 0
    reconfirmed: int = 0


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


def _resolved_movie(library: str, section_locations: list[str], movie) -> ResolvedItem | None:
    locations = movie.locations
    if not locations:
        return None
    root_folder = _root_folder(section_locations, locations[0], is_directory=False)
    if root_folder is None:
        return None
    guids = _guids(movie)
    return ResolvedItem(
        server="plex", native_id=str(movie.ratingKey), library=library, kind="movie",
        title=movie.title, year=getattr(movie, "year", None),
        season_number=None, episode_number=None,
        root_folder=root_folder, file_path=locations[0], art_url=None,
        tmdb_id=_as_int(guids.get("tmdb")), tvdb_id=_as_int(guids.get("tvdb")),
        imdb_id=guids.get("imdb"),
    )


def _resolved_show(library: str, section_locations: list[str], show) -> ResolvedItem | None:
    locations = show.locations
    if not locations:
        return None
    root_folder = _root_folder(section_locations, locations[0], is_directory=True)
    if root_folder is None:
        return None
    guids = _guids(show)
    return ResolvedItem(
        server="plex", native_id=str(show.ratingKey), library=library, kind="show",
        title=show.title, year=getattr(show, "year", None),
        season_number=None, episode_number=None,
        root_folder=root_folder, file_path=None, art_url=None,
        tmdb_id=_as_int(guids.get("tmdb")), tvdb_id=_as_int(guids.get("tvdb")),
        imdb_id=guids.get("imdb"),
    )


def _resolved_season(library: str, show, season, root_folder: str) -> ResolvedItem:
    # Seasons and episodes carry no location of their own -- their artifacts
    # live under the show's folder, so root_folder is passed down rather than
    # derived again.
    # A Plex season carries no provider guids of its own -- mirrors the SHOW's
    # guids here, exactly as plex/client.py's own match resolution does
    # (``container = item.show()`` there), so an adopted season and a
    # pipeline-resolved season compute the SAME identity key. The same show
    # guids are also the season's PARENT ids (parent_identity_key_for's
    # "show" lookup), mirroring the resolver's own ``parent_guids``.
    guids = _guids(show)
    return ResolvedItem(
        server="plex", native_id=str(season.ratingKey), library=library, kind="season",
        title=season.title, year=getattr(season, "year", None),
        season_number=season.index, episode_number=None,
        root_folder=root_folder, file_path=None, art_url=None,
        tmdb_id=_as_int(guids.get("tmdb")), tvdb_id=_as_int(guids.get("tvdb")),
        imdb_id=guids.get("imdb"), parent_native_id=str(show.ratingKey),
        parent_tmdb_id=_as_int(guids.get("tmdb")), parent_tvdb_id=_as_int(guids.get("tvdb")),
        parent_imdb_id=guids.get("imdb"),
        # Roadmap row 78. `show` is already a parameter -- the walk holds it
        # for the root folder and the parent key -- so the show's title costs
        # nothing here and must agree with what PlexClient.resolve produces,
        # or an adopted fingerprint and the render path's would disagree the
        # first time the gate is turned on.
        show_title=show.title,
    )


def _resolved_episode(library: str, show, season, episode, root_folder: str) -> ResolvedItem:
    # An episode's OWN provider ids are the SHOW's, exactly as
    # ``_resolved_season`` above takes them: the Plex resolver rebinds its
    # match container to the SHOW for a season *or an episode* intent
    # (plex/client.py:416-436) and builds ``guids`` off THAT container, so a
    # ResolvedItem's tmdb_id/tvdb_id/imdb_id for a resolved episode are the
    # series' ids. Reading the episode's own guids here instead would give an
    # adopted episode a different identity_key from the one the render path
    # computes for the same episode -- two rows for one episode on the next
    # pass. The same show guids are also the episode's PARENT ids (its
    # season's, which carry the show's), so both land on one source.
    #
    # Its file_path is always None: the resolver reads file_path off that
    # same show container, which is always None -- no producer, adopted or
    # resolved, can ever hand an episode a real file_path
    # (servers/identity.py's FILE_BEARING no longer includes "episode" for
    # exactly this reason).
    show_guids = _guids(show)
    return ResolvedItem(
        server="plex", native_id=str(episode.ratingKey), library=library, kind="episode",
        title=episode.title, year=getattr(episode, "year", None),
        season_number=episode.parentIndex, episode_number=episode.index,
        root_folder=root_folder, file_path=None, art_url=None,
        tmdb_id=_as_int(show_guids.get("tmdb")), tvdb_id=_as_int(show_guids.get("tvdb")),
        imdb_id=show_guids.get("imdb"), parent_native_id=str(season.ratingKey),
        parent_tmdb_id=_as_int(show_guids.get("tmdb")), parent_tvdb_id=_as_int(show_guids.get("tvdb")),
        parent_imdb_id=show_guids.get("imdb"),
    )


def _resolve_section(section) -> list[ResolvedItem]:
    """Walk one section and build every ``ResolvedItem``, all inside one thread.

    Blocking -- call via ``asyncio.to_thread``, and never touch a ``plexapi``
    object outside it.

    ``PlexPartialObject.__getattribute__`` issues a synchronous ``_reload()``
    HTTP GET whenever the attribute it is asked for is ``None`` or ``[]``. That
    is not a rare path here: seasons and episodes routinely have ``year is
    None``, and an unmatched item has empty ``guids``. Offloading only
    ``section.all()``/``seasons()``/``episodes()`` and then reading
    ``.locations``, ``.guids`` and ``.year`` back on the event loop therefore
    meant up to ~16,000 blocking GETs on the loop -- so the resolved items are
    built here, in the same thread that fetched the objects, exactly as
    ``PlexClient._search_sync`` does for the render path.

    Returning plain ``ResolvedItem`` dataclasses (never a ``plexapi`` object)
    is what makes that guarantee hold at the boundary.
    """
    library = section.title
    section_locations = list(section.locations)
    resolved: list[ResolvedItem] = []
    for top in section.all():
        kind = getattr(top, "type", None)
        if kind == "movie":
            movie = _resolved_movie(library, section_locations, top)
            if movie is not None:
                resolved.append(movie)
        elif kind == "show":
            show = _resolved_show(library, section_locations, top)
            if show is None:
                continue
            resolved.append(show)
            for season in top.seasons():
                resolved.append(_resolved_season(library, top, season, show.root_folder))
                for episode in season.episodes():
                    resolved.append(
                        _resolved_episode(library, top, season, episode, show.root_folder)
                    )
    return resolved


def _hash_file(path: Path) -> str:
    """SHA-256 of a file already confirmed to exist. Blocking -- call via ``asyncio.to_thread``."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _existing_render(session: AsyncSession, native_id: str, art_kind: str) -> Render | None:
    item_id = await item_id_for(session, "plex", native_id)
    if item_id is None:
        return None
    return (
        await session.execute(
            select(Render).where(Render.item_id == item_id, Render.art_kind == art_kind)
        )
    ).scalar_one_or_none()


async def _adopt_item(
    session: AsyncSession, config: Config, resolved: ResolvedItem, dry_run: bool, counters: _Counters,
) -> None:
    counters.items += 1
    # Upserted unconditionally, one per item, regardless of what its art kinds
    # turn out to need below -- a media_items row exists for everything walked,
    # there may already be one from a webhook, and identity_key is unique.
    media_item = None
    if not dry_run:
        media_item = await _upsert_media_item(session, resolved)

    for art_kind in ART_KINDS_FOR[resolved.kind]:
        # The same two gates render_artifact applies before it does anything
        # else. Without them a deployment with `artwork.background.enabled:
        # false` reports every background as a missing asset -- ~2,000 of them
        # -- which is precisely the number deploy/README.md asks the operator
        # to check before cutover.
        if not art_config_for(config, art_kind).enabled or _should_skip_title(
            config, resolved, art_kind
        ):
            counters.skipped_by_config += 1
            continue

        # Placed after the config gate so an item that is both TBA-titled and
        # unnumbered keeps its more specific "this config would never render
        # it" reason.
        missing_number = naming.missing_number(
            art_kind, resolved.season_number, resolved.episode_number
        )
        if missing_number is not None:
            counters.unnumbered += 1
            logger.warning(
                "adoption: %s %r (native id %s) has no %s -- skipping its %s",
                resolved.kind, resolved.title, resolved.native_id,
                missing_number, art_kind,
            )
            continue

        target = naming.asset_path(
            config, resolved.library, resolved.root_folder, art_kind,
            resolved.season_number, resolved.episode_number,
        )
        # assets_root can be an NFS mount; keep every stat() off the event loop.
        if not await asyncio.to_thread(target.exists):
            counters.missing_assets += 1
            continue

        existing = await _existing_render(session, resolved.native_id, art_kind)
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
        # `existing is None` is a brand new item; `existing is not None and
        # existing.adopted` (the only other way execution reaches here -- the
        # branch above already returned for a real, non-adopted render) is a
        # prior run's adoption being re-hashed and re-stamped by this one.
        # `renders` stays their sum either way, for compatibility.
        if existing is None:
            counters.adopted += 1
        else:
            counters.reconfirmed += 1
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

    The whole Plex side of the walk happens in one thread (see
    ``_resolve_section``) and hands back plain dataclasses; nothing below this
    line touches a ``plexapi`` object. For a show library the walk yields the
    shows themselves plus their seasons and episodes, since each carries its
    own artifacts. ``dry_run=True`` (the default) computes and reports
    everything and writes no rows -- the mode to run before cutover.
    """
    counters = _Counters()
    for resolved in await asyncio.to_thread(_resolve_section, section):
        await _adopt_item(session, config, resolved, dry_run, counters)

    return AdoptionReport(
        items=counters.items, renders=counters.renders,
        missing_assets=counters.missing_assets, skipped=counters.skipped,
        skipped_by_config=counters.skipped_by_config, unnumbered=counters.unnumbered,
        by_kind=counters.by_kind,
        adopted=counters.adopted, reconfirmed=counters.reconfirmed,
    )
