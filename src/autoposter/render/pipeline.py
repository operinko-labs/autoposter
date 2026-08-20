import asyncio
import hashlib
import logging
import os
import tempfile
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.schema import Config
from autoposter.db.models import MediaItem, Render
from autoposter.facts.gather import gather_facts, persist_facts
from autoposter.facts.models import GatheredFacts
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.plex.writer import apply_facts
from autoposter.providers import base as art
from autoposter.providers.ladder import select_artwork
from autoposter.render import compositor, naming
from autoposter.render.textfit import fit_point_size, prepare_text

logger = logging.getLogger(__name__)

ART_KINDS_FOR = {
    "movie": ["poster", "background"],
    "show": ["poster", "background"],
    "season": ["season_poster"],
    "episode": ["title_card"],
}

_CANVAS = {
    "poster": compositor.POSTER_SIZE,
    "season_poster": compositor.POSTER_SIZE,
    "background": compositor.BACKGROUND_SIZE,
    "title_card": compositor.BACKGROUND_SIZE,
}

_BULLET = "•"


def compute_fingerprint(
    config_version: str,
    art_kind: str,
    source_url: str | None,
    base_sha256: str | None,
    text_inputs: list[str],
    asset_hashes: list[str] = (),
) -> str:
    """Hash every input that affects the finished image.

    Re-rendering happens only when this value changes, which is what turns a
    library-wide pass into a cheap comparison instead of 16k image operations.
    ``asset_hashes`` carries the content hashes of files that affect the pixels
    but are not otherwise reflected here (overlay, font, logo) — a filename
    alone does not change when an operator replaces the file in place.
    """
    parts = [
        config_version, art_kind, source_url or "", base_sha256 or "",
        *text_inputs, *asset_hashes,
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes, or ``""`` if it does not exist.

    The sentinel keeps ``compute_fingerprint`` usable in tests and environments
    that lack the real asset files (fonts, overlays) on disk.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def _stage_override(override: Path, working: Path) -> str:
    """Copy the manual-override file into the working directory and hash it."""
    working.write_bytes(override.read_bytes())
    return hashlib.sha256(working.read_bytes()).hexdigest()


def art_config_for(config: Config, art_kind: str):
    return getattr(config.artwork, art_kind)


def title_text_for(
    art_kind: str, item: ResolvedItem, config: Config
) -> tuple[str | None, str | None]:
    """Return ``(primary_text, secondary_text)`` for an artifact.

    The secondary line exists only for title cards. Numbers there are unpadded,
    unlike the zero-padded asset file names.
    """
    settings = art_config_for(config, art_kind)
    if settings.text is None or not settings.text.add_text:
        return None, None
    if art_kind == "title_card":
        secondary = None
        if settings.episode_text is not None and settings.episode_text.add_text:
            secondary = (
                f"{settings.season_label} {item.season_number} "
                f"{_BULLET} {settings.episode_label} {item.episode_number}"
            )
        return item.title, secondary
    return item.title, None


def manual_override_path(config: Config, item: ResolvedItem, art_kind: str) -> Path | None:
    """A hand-placed asset wins over anything fetched from a provider.

    Synchronous by design (see the offloaded call site in ``render_artifact``):
    plain tests call this directly without an event loop to hop off of.
    """
    relative = naming.asset_path(
        config.model_copy(update={"assets_root": config.manual_assets_root}),
        item.library, item.root_folder, art_kind,
        item.season_number, item.episode_number,
    )
    return relative if relative.exists() else None


def _should_skip_title(config: Config, item: ResolvedItem, art_kind: str) -> bool:
    if art_kind != "title_card" or not config.skip_tba:
        return False
    skip_words = {word.lower() for word in art_config_for(config, art_kind).skip_words}
    return item.title.strip().lower() in skip_words


async def _download(http: httpx.AsyncClient, url: str, destination: Path) -> str:
    """Fetch artwork to ``destination`` and return its SHA-256."""
    digest = hashlib.sha256()
    async with http.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            async for chunk in response.aiter_bytes():
                digest.update(chunk)
                handle.write(chunk)
    return digest.hexdigest()


async def _upsert_media_item(session: AsyncSession, item: ResolvedItem) -> MediaItem:
    """Insert or update the item, safe under concurrent workers.

    ``rating_key`` carries a real unique constraint. A select-then-insert here
    would race: two workers can both miss the select and both try to insert,
    and the loser's flush raises ``IntegrityError``. The Postgres upsert makes
    the write atomic; the row is then re-selected to get an ORM-tracked object.

    ``parent_id`` is looked up by the parent's rating key rather than passed in,
    because ``ResolvedItem`` only carries the parent's Plex identity, not its
    database id. If the parent has not been processed yet there is no row to
    find, so ``parent_id`` is left null rather than invented — a later upsert
    of this same item (e.g. a re-delivered webhook) will fill it in once the
    parent exists.
    """
    parent_id = None
    if item.parent_rating_key is not None:
        parent_id = (
            await session.execute(
                select(MediaItem.id).where(MediaItem.rating_key == item.parent_rating_key)
            )
        ).scalar_one_or_none()

    mutable = dict(
        library=item.library,
        kind=item.kind,
        parent_id=parent_id,
        title=item.title,
        year=item.year,
        season_number=item.season_number,
        episode_number=item.episode_number,
        root_folder=item.root_folder,
        file_path=item.file_path,
        tmdb_id=item.tmdb_id,
        tvdb_id=item.tvdb_id,
        imdb_id=item.imdb_id,
    )
    stmt = insert(MediaItem).values(rating_key=item.rating_key, **mutable)
    stmt = stmt.on_conflict_do_update(
        index_elements=["rating_key"], set_={**mutable, "updated_at": func.now()}
    )
    await session.execute(stmt)
    await session.flush()
    return (
        await session.execute(
            select(MediaItem).where(MediaItem.rating_key == item.rating_key)
        )
    ).scalar_one()


async def _get_or_create_render(
    session: AsyncSession, media_item: MediaItem, art_kind: str, asset_path: Path
) -> Render:
    """Insert or update the render row, safe under concurrent workers.

    ``(item_id, art_kind)`` carries a real unique constraint; see
    ``_upsert_media_item`` for why select-then-insert is unsafe here.
    """
    stmt = insert(Render).values(
        item_id=media_item.id, art_kind=art_kind, asset_path=str(asset_path)
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["item_id", "art_kind"], set_={"asset_path": str(asset_path)}
    )
    await session.execute(stmt)
    await session.flush()
    return (
        await session.execute(
            select(Render).where(
                Render.item_id == media_item.id, Render.art_kind == art_kind
            )
        )
    ).scalar_one()


async def render_artifact(
    session: AsyncSession,
    config: Config,
    http: httpx.AsyncClient,
    item: ResolvedItem,
    art_kind: str,
    providers: list,
) -> Render:
    """Build one artifact. Idempotent: safe to run repeatedly for the same item."""
    settings = art_config_for(config, art_kind)
    media_item = await _upsert_media_item(session, item)
    target = naming.asset_path(
        config, item.library, item.root_folder, art_kind,
        item.season_number, item.episode_number,
    )
    render = await _get_or_create_render(session, media_item, art_kind, target)

    if not settings.enabled:
        render.status = "skipped"
        render.detail = f"{art_kind} disabled in config"
        await session.commit()
        return render

    if _should_skip_title(config, item, art_kind):
        render.status = "skipped"
        render.detail = f"title {item.title!r} matches a skip word"
        await session.commit()
        return render

    primary_text, secondary_text = title_text_for(art_kind, item, config)

    with tempfile.TemporaryDirectory() as tmpdir:
        working = Path(tmpdir) / target.name

        # manual_override_path() stats the (possibly NFS) manual-assets mount;
        # offloaded like the rest of this pipeline's blocking I/O so a hung
        # mount cannot stall the event loop.
        override = await asyncio.to_thread(manual_override_path, config, item, art_kind)
        if override is not None:
            base_sha = await asyncio.to_thread(_stage_override, override, working)
            source_url, provider_name, textless = str(override), "manual", None
        else:
            selection = await select_artwork(
                providers,
                settings.language_order,
                art.ArtRequest(
                    art_kind=art_kind,
                    is_movie=item.kind == "movie",
                    tmdb_id=item.tmdb_id,
                    tvdb_id=item.tvdb_id,
                    imdb_id=item.imdb_id,
                    season_number=item.season_number,
                    episode_number=item.episode_number,
                ),
            )
            if selection.candidate is None:
                render.status = "no_art"
                render.detail = f"no {art_kind} art on any provider"
                await session.commit()
                return render
            candidate = selection.candidate
            base_sha = await _download(http, candidate.url, working)
            source_url = candidate.url
            provider_name = candidate.provider
            textless = candidate.is_textless

        # Posterizarr parity: UseLogo/UseClearlogo composites a clearlogo in place
        # of the title text on posters. With LogoTextFallback false, a poster with
        # no logo on any provider gets neither logo nor text (spec section on
        # clearlogos). Other art kinds are unaffected.
        logo_path: Path | None = None
        logo_sha = ""
        suppress_text = False
        if art_kind == "poster" and config.artwork.use_logo and settings.text is not None:
            logo_selection = await select_artwork(
                providers,
                config.artwork.logo_language_order,
                art.ArtRequest(
                    art_kind=art.LOGO,
                    is_movie=item.kind == "movie",
                    tmdb_id=item.tmdb_id,
                    tvdb_id=item.tvdb_id,
                    imdb_id=item.imdb_id,
                    season_number=item.season_number,
                    episode_number=item.episode_number,
                ),
            )
            if logo_selection.candidate is not None:
                logo_candidate = logo_selection.candidate
                suffix = Path(httpx.URL(logo_candidate.url).path).suffix or ".png"
                logo_path = Path(tmpdir) / f"logo{suffix}"
                logo_sha = await _download(http, logo_candidate.url, logo_path)
            elif not config.artwork.logo_text_fallback:
                suppress_text = True

        draw_text = not (art_kind == "poster" and (logo_path is not None or suppress_text))

        text_inputs = [t for t in (primary_text, secondary_text) if t] if draw_text else []
        overlay_hash = (
            await asyncio.to_thread(
                _file_sha256, Path(config.overlays_root) / settings.overlay_file
            )
            if settings.add_overlay
            else ""
        )
        font_hashes = []
        if draw_text and settings.text is not None and primary_text:
            font_hashes.append(
                await asyncio.to_thread(
                    _file_sha256, Path(config.fonts_root) / settings.text.font
                )
            )
        if art_kind == "title_card" and settings.episode_text is not None and secondary_text:
            font_hashes.append(
                await asyncio.to_thread(
                    _file_sha256, Path(config.fonts_root) / settings.episode_text.font
                )
            )
        asset_hashes = [overlay_hash, *font_hashes, logo_sha]

        fingerprint = compute_fingerprint(
            config.version, art_kind, source_url, base_sha, text_inputs, asset_hashes
        )
        # target.exists() offloaded (it's a stat() against assets_root, which
        # can be an NFS mount) — only reached once the fingerprint already
        # matches, so the short-circuit still skips it entirely otherwise.
        if render.fingerprint == fingerprint and await asyncio.to_thread(target.exists):
            render.status = "rendered"
            render.detail = "unchanged"
            await session.commit()
            return render

        if render.source_mode == "verbatim":
            # MediUX and similar sources ship finished art; compositing would fight
            # the designer's own title treatment (spec section 11).
            await asyncio.to_thread(
                _publish, working, target, config.backup_root, config.assets_root
            )
        else:
            overlay = (
                str(Path(config.overlays_root) / settings.overlay_file)
                if settings.add_overlay
                else None
            )
            await asyncio.to_thread(
                compositor.run,
                compositor.build_stamp_argv(config.magick_binary, str(working)),
            )
            await asyncio.to_thread(
                compositor.run,
                compositor.build_base_argv(
                    config.magick_binary, str(working), _CANVAS[art_kind], overlay,
                    config.artwork.output_quality, settings.add_border,
                    settings.border_color, settings.border_width,
                ),
            )
            if logo_path is not None:
                await asyncio.to_thread(
                    compositor.run,
                    compositor.build_logo_argv(
                        config.magick_binary, str(working), str(logo_path),
                        settings.text, config.artwork.output_quality,
                    ),
                )
            blocks = []
            if draw_text:
                blocks.append((settings.text, primary_text))
            if art_kind == "title_card":
                blocks.append((settings.episode_text, secondary_text))
            for style, text in blocks:
                if style is None or not text:
                    continue
                prepared = prepare_text(text, style)
                font_path = str(Path(config.fonts_root) / style.font)
                fit = await asyncio.to_thread(
                    fit_point_size, config.magick_binary, font_path, style, prepared
                )
                if fit.truncated:
                    # Posterizarr writes no file when text cannot fit at the minimum
                    # point size. Emitting one here would produce artwork the current
                    # system never would.
                    render.status = "truncated"
                    render.detail = (
                        f"{text!r} does not fit in "
                        f"{style.max_width}x{style.max_height} at "
                        f"{style.min_point_size}pt"
                    )
                    await session.commit()
                    return render
                await asyncio.to_thread(
                    compositor.run,
                    compositor.build_text_argv(
                        config.magick_binary, str(working), style, font_path,
                        fit.point_size, prepared, config.artwork.output_quality,
                    ),
                )
            await asyncio.to_thread(
                _publish, working, target, config.backup_root, config.assets_root
            )

    render.provider = provider_name
    render.source_url = source_url
    render.textless = textless
    render.base_sha256 = base_sha
    render.fingerprint = fingerprint
    render.status = "rendered"
    render.detail = None
    # Database clock, per the global constraint: the app and database clocks drift.
    render.rendered_at = func.now()
    await session.commit()
    return render


def _publish(working: Path, target: Path, backup_root: Path | None, assets_root: Path) -> None:
    """Move the finished image into the asset tree atomically.

    ``os.replace`` is atomic within a filesystem, so readers never observe a
    half-written asset. The staging copy lives beside the target so the rename
    does not cross a mount boundary. If anything fails after the staging file is
    written, it is removed rather than left behind.

    Before overwriting, the existing asset is copied into ``backup_root`` under
    the same path relative to ``assets_root``, keeping exactly one previous
    generation so a bad render can be rolled back (spec section 7). Using the
    full relative path — not just the immediate parent folder name — keeps two
    libraries that happen to share a folder name (e.g. "Movies" and "4K Movies"
    both holding "Dune (2024)") from clobbering each other's backup.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup_root is not None and target.exists():
        relative = target.relative_to(Path(assets_root))
        backup = Path(backup_root) / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(target.read_bytes())
    staging = target.with_name(f".{target.name}.tmp")
    try:
        staging.write_bytes(working.read_bytes())
        os.replace(staging, target)
    except Exception:
        staging.unlink(missing_ok=True)
        raise


async def apply_metadata(
    session: AsyncSession,
    config: Config,
    media_item_id: int,
    item: ResolvedItem,
    plex_item,
    tmdb_facts,
    mdblist,
) -> GatheredFacts:
    """Gather this item's facts, store them, and write the changed ones to Plex.

    Runs before any badge rendering, because badges read the values from Plex
    rather than from the providers.
    """
    if not config.operations.enabled:
        return GatheredFacts()

    facts = await gather_facts(session, item, tmdb_facts, mdblist)
    await persist_facts(session, media_item_id, facts)

    if config.operations.write_to_plex and plex_item is not None and not facts.is_empty():
        await apply_facts(plex_item, facts)
    return facts


async def process_item(
    session: AsyncSession,
    config: Config,
    http: httpx.AsyncClient,
    plex,
    providers: list,
    intent: RenderIntent,
    tmdb_facts=None,
    mdblist=None,
) -> list[Render]:
    """Resolve one intent and build every artifact it implies.

    ``tmdb_facts``/``mdblist`` are the metadata-operations clients; they are
    optional (and default to ``None``) so callers that only care about
    artwork — including every test that predates Phase 2a — keep working
    unchanged. Metadata operations run only when both are supplied, which
    also means production wiring skips them gracefully until an operator has
    configured the MDBList API key.
    """
    item = await plex.resolve(intent)

    if config.operations.enabled and tmdb_facts is not None and mdblist is not None:
        media_item = await _upsert_media_item(session, item)
        plex_item = await plex.fetch_item(item.rating_key)
        await apply_metadata(
            session, config, media_item.id, item, plex_item, tmdb_facts, mdblist
        )

    results = []
    for art_kind in ART_KINDS_FOR[intent.kind]:
        results.append(
            await render_artifact(session, config, http, item, art_kind, providers)
        )
    return results
