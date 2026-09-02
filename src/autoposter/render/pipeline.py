import asyncio
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.badges.compose import (
    manifest_sha,
    BadgeInputs,
    badge_fingerprint,
    badge_values,
    compose as compose_badges,
)
from autoposter.badges.values import media_info_from_plex, video_format_text
from autoposter.config.schema import Config
from autoposter.db.models import ItemFacts, MediaItem, Render
from autoposter.facts.gather import gather_facts, persist_facts
from autoposter.facts.models import GatheredFacts
from autoposter.intake.arr import RenderIntent
from autoposter.plex.artwork import upload_artwork
from autoposter.plex.client import ResolvedItem
from autoposter.plex.writer import apply_facts, exemption_reason
from autoposter.providers import base as art
from autoposter.providers.ladder import language_rank, normalise_language, select_artwork
from autoposter.render import compositor, naming
from autoposter.render.textfit import fit_point_size, prepare_text

logger = logging.getLogger(__name__)

# Mirrored in frontend/src/artKind.ts, which shows each kind's first entry as
# the item's primary art. A kind added here must be added there too, or its
# pages fall back to `poster` and 404.
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


def draw_text_for_local_source(config: Config, art_kind: str) -> bool:
    """Whether text is drawn on a locally supplied base image (roadmap row 39).

    True unless this art kind's ``skip_local_text_add`` is on. Split out as a
    named predicate rather than inlined into ``render_artifact``'s already
    dense ``draw_text`` expression: the branch has its own row, its own
    default pin and its own test.
    """
    return not art_config_for(config, art_kind).skip_local_text_add


def online_fetch_disabled(config: Config, art_kind: str) -> bool:
    """Whether this artifact may make provider requests (roadmap row 47).

    The per-kind value wins in BOTH directions when it is set -- a ``False``
    beneath a global ``True`` re-enables just that kind, which is the whole
    reason the per-kind field is ``bool | None`` rather than ``bool``.
    """
    per_kind = art_config_for(config, art_kind).disable_online_asset_fetch
    if per_kind is not None:
        return per_kind
    return config.artwork.disable_online_asset_fetch


def language_order_for(config: Config, library: str, art_kind: str) -> list[str]:
    """This artifact's language ladder in this library (roadmap row 38).

    ``artwork.library_language_overrides`` wins when it names both the library
    and the art kind; otherwise the art kind's own ``language_order`` stands.
    An art kind the override does not name is deliberately left alone rather
    than inheriting a sibling's order -- that is what lets a library re-point
    its posters while its title cards keep leading with ``xx``.

    Deliberately not applied to ``artwork.logo_language_order``: the row is
    about the art ladder, and a second override surface for logos would be a
    second thing to keep in sync for no requirement.
    """
    override = config.artwork.library_language_overrides.get(library, {}).get(art_kind)
    if override:
        return override
    return art_config_for(config, art_kind).language_order


def primary_title_for(item: ResolvedItem, config: Config) -> str:
    """The title drawn on an artifact (roadmap row 44).

    ``artwork.use_original_title`` swaps in the original-language title where
    Plex has one. Fail-open by construction: Plex carries ``originalTitle`` for
    movies and not for shows or episodes, so an absent value is the ordinary
    case and must quietly keep the localized title rather than draw nothing.
    """
    if config.artwork.use_original_title and item.original_title:
        return item.original_title
    return item.title


def known_with_text(candidate) -> bool:
    """Whether a provider STATED that this artwork carries text (row 41).

    Deliberately not ``not candidate.is_textless``. That property falls back
    to a language-token guess when no provider said anything
    (``providers/base.py``: "TVDB states textlessness outright; the others only
    imply it"), so an ordinary English-tagged TMDb poster reads as
    with-text under it. Stripping an operator's overlay, border and title on
    that inference would be a guess with a visible cost, so this rule fires
    only on the explicit statement and fails open otherwise -- the controller's
    adjudication 2, and the reason a provider carrying no signal simply does
    not trigger the rule.

    ``None`` -- a manual override, which resolved no candidate at all -- is
    likewise not known with text.
    """
    return getattr(candidate, "includes_text", None) is True


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
            # Row 43: an override replaces the whole season half, label and
            # number both -- "Specials", not "Specials 0".
            season = settings.season_name_overrides.get(
                str(item.season_number), f"{settings.season_label} {item.season_number}"
            )
            secondary = (
                f"{season} {_BULLET} {settings.episode_label} {item.episode_number}"
            )
        return primary_title_for(item, config), secondary
    return primary_title_for(item, config), None


def manual_override_target(config: Config, item: ResolvedItem, art_kind: str) -> Path:
    """The mirror path an override for this artifact would occupy.

    Split out from ``manual_override_path`` so the picker can *write* exactly
    the path the pipeline *stats*: two spellings of the same mirror would
    eventually disagree, and the failure mode is a picked image that is written
    successfully and then never looked at again.
    """
    return naming.asset_path(
        config.model_copy(update={"assets_root": config.manual_assets_root}),
        item.library, item.root_folder, art_kind,
        item.season_number, item.episode_number,
    )


# Roadmap row 48. Posterizarr's own key names, kept verbatim so an operator
# migrating a manual-assets tree does not have to rename anything. Fixed
# ``.jpg``, like every other file `naming._file_name` produces.
_TEMPLATE_NAME = {"season_poster": "SeasonTemplate.jpg", "title_card": "EpisodeTemplate.jpg"}


def template_override_path(config: Config, item: ResolvedItem, art_kind: str) -> Path | None:
    """A show-scoped manual asset that stands in for every season/episode.

    Filed beside the show's own overrides -- same directory under
    ``library_folders``, same ``<root_folder>`` prefix in the flat layout --
    which is exactly where ``logo_override_path`` files its own non-render
    asset, so everything one show can be overridden with sits together.

    Only ``season_poster`` and ``title_card`` have a template: a poster and a
    background are already one file per item, so a template for them would be
    that same file under a second name.
    """
    name = _TEMPLATE_NAME.get(art_kind)
    if name is None or not config.artwork.season_episode_templates:
        return None
    manual = config.model_copy(update={"assets_root": config.manual_assets_root})
    poster = naming.asset_path(manual, item.library, item.root_folder, "poster")
    path = (
        poster.with_name(name) if config.library_folders
        else poster.with_name(f"{item.root_folder}_{name}")
    )
    return path if path.exists() else None


def manual_override_path(config: Config, item: ResolvedItem, art_kind: str) -> Path | None:
    """A hand-placed asset wins over anything fetched from a provider.

    The item's own file first; a show-scoped template second (roadmap row 48),
    so a season or episode that has been given its own image keeps it and only
    the ones that have not fall back to the show's template.

    Synchronous by design (see the offloaded call site in ``render_artifact``):
    plain tests call this directly without an event loop to hop off of.
    """
    relative = manual_override_target(config, item, art_kind)
    if relative.exists():
        return relative
    return template_override_path(config, item, art_kind)


# A picked logo keeps whatever container it arrived in -- a logo is composited
# over the poster and needs its alpha channel, which rules out the .jpg the
# poster mirror is fixed to. The order is lookup precedence only: the pick
# endpoint removes every other suffix when it installs a new one, so at most
# one of these ever exists on disk at a time and shadowing cannot happen.
LOGO_OVERRIDE_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")


def logo_override_path(config: Config, item: ResolvedItem, suffix: str) -> Path:
    """Where a picked clearlogo for one item is filed.

    Deliberately not a case in ``naming._file_name``: that function answers for
    the four kinds that have a render row, an ART_KINDS_FOR entry and a fixed
    ``.jpg`` extension, and a logo has none of the four. It is filed *beside*
    the poster's own manual override instead -- same directory under
    ``library_folders``, same ``<root_folder>`` prefix in the flat layout -- so
    everything one item can be overridden with sits together.
    """
    manual = config.model_copy(update={"assets_root": config.manual_assets_root})
    poster = naming.asset_path(manual, item.library, item.root_folder, "poster")
    if config.library_folders:
        return poster.with_name(f"logo{suffix}")
    return poster.with_name(f"{item.root_folder}_logo{suffix}")


def find_logo_override(config: Config, item: ResolvedItem) -> Path | None:
    """The picked logo for this item, in whichever format it was picked in.

    Synchronous for the same reason ``manual_override_path`` is: plain tests
    call it without an event loop, and the render path hops off its own.
    """
    for suffix in LOGO_OVERRIDE_SUFFIXES:
        path = logo_override_path(config, item, suffix)
        if path.exists():
            return path
    return None


# Hiragana, Katakana, the Katakana phonetic extensions, CJK Unified Ideographs
# and their Extension A, and the compatibility ideographs. Deliberately not
# Hangul or Cyrillic: row 40 names Japanese and Chinese, and a broader net
# would skip title cards nobody asked to skip.
_CJK_RANGES = (
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x31F0, 0x31FF),  # Katakana phonetic extensions
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
)


def has_cjk(text: str) -> bool:
    """Whether ``text`` contains any Japanese or Chinese character.

    Any, not all: an episode titled with one Han character and three Latin
    words is still a title this rule is asked to skip, and requiring every
    character to be CJK would let a single stray ASCII colon defeat it.
    """
    return any(
        any(low <= ord(char) <= high for low, high in _CJK_RANGES) for char in text
    )


def _should_skip_title(config: Config, item: ResolvedItem, art_kind: str) -> str | None:
    """The reason this title card must not be built at all, or ``None`` to build it.

    Two independent rules, deliberately not chained: ``skip_tba`` owns the
    literal ``skip_words`` list (row 13), and ``skip_cjk_titles`` owns the
    script test (row 40). Tying the second to the first would make one setting
    silently disable the other, which is exactly the kind of coupling an
    operator discovers by finding a title card they thought they had switched
    off. Returning which rule fired -- rather than a bare bool -- keeps
    ``render.detail`` naming the actual rule instead of guessing at it.
    """
    if art_kind != "title_card":
        return None
    settings = art_config_for(config, art_kind)
    if config.skip_tba:
        skip_words = {word.lower() for word in settings.skip_words}
        if item.title.strip().lower() in skip_words:
            return f"title {item.title!r} matches a skip word"
    if settings.skip_cjk_titles and has_cjk(item.title):
        return f"title {item.title!r} is written in Japanese or Chinese script"
    return None


async def gather_fingerprint_inputs(
    config: Config,
    item: ResolvedItem,
    art_kind: str,
    *,
    draw_text: bool = True,
    logo_sha: str = "",
    suppress_styling: bool = False,
) -> tuple[list[str], list[str]]:
    """Collect the ``(text_inputs, asset_hashes)`` halves of the fingerprint.

    The single definition of "what goes into a fingerprint besides the source
    URL and the base image". ``render_artifact`` calls it, and so does the
    adoption walk — which cannot know a source URL, having resolved no provider
    (spec section 5). Two implementations of this would silently stop agreeing,
    and the cost of that disagreement is re-rendering the whole library.

    ``draw_text`` and ``logo_sha`` are what the render path knows and adoption
    does not: whether a clearlogo replaced the title text, and which logo.
    ``suppress_styling`` (roadmap row 41) is the third thing the render path
    knows and adoption does not: whether the chosen candidate is known to
    already carry text, which drops the overlay from the fingerprint too. All
    three default to the adoption case — no logo, title text drawn, styling
    not suppressed — so an adopted row and the short-circuit that reads it
    always agree.
    """
    settings = art_config_for(config, art_kind)
    primary_text, secondary_text = title_text_for(art_kind, item, config)

    text_inputs = [t for t in (primary_text, secondary_text) if t] if draw_text else []
    overlay_hash = (
        await asyncio.to_thread(
            _file_sha256, Path(config.overlays_root) / settings.overlay_file
        )
        if settings.add_overlay and not suppress_styling
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
    return text_inputs, [overlay_hash, *font_hashes, logo_sha]


def adopted_fingerprint(
    config: Config,
    art_kind: str,
    base_sha256: str | None,
    text_inputs: list[str],
    asset_hashes: list[str],
) -> str:
    """``compute_fingerprint`` with the one input adoption cannot know left out.

    An artifact adopted from the existing library carries no record of which
    provider URL produced it, so the comparison drops ``source_url`` on both
    sides rather than guessing at it.
    """
    return compute_fingerprint(
        config.version, art_kind, None, base_sha256, text_inputs, asset_hashes
    )


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
    session: AsyncSession, media_item: MediaItem, art_kind: str, asset_path: Path | str
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


@dataclass(frozen=True)
class ComposeResult:
    """The outcome of styling one base image.

    ``truncated`` is Posterizarr's no-artifact outcome rather than an error:
    text that will not fit at the minimum point size means no file is produced
    at all, and ``detail`` says which text failed to fit in which box.
    """

    output: Path
    truncated: bool
    detail: str | None = None
    # The point size the PRIMARY title block finally fitted at (textfit's
    # FitResult). Carried out because render_artifact records it as a quality
    # fact and the value is otherwise computed inside the loop below and
    # dropped. None when no title was drawn at all. Optional with a default so
    # every other caller of compose_styled -- api/testing.py, api/manual.py,
    # the artwork modes -- is untouched by its arrival.
    point_size: int | None = None


async def compose_styled(
    config: Config,
    art_kind: str,
    working: Path,
    *,
    primary_text: str | None,
    secondary_text: str | None,
    draw_text: bool,
    logo_path: Path | None = None,
    suppress_styling: bool = False,
) -> ComposeResult:
    """Style ``working`` in place -- stamp, base canvas, optional logo, text.

    Lifted out of ``render_artifact`` unchanged so a caller holding a base
    image can get exactly the pipeline's styled bytes and nothing else: no
    fingerprints, no publishing, no database. The caller owns ``working`` and
    is handed it back as ``ComposeResult.output``.

    ``draw_text`` is not inferred from ``logo_path``. The pipeline suppresses
    the title for a poster that composites a logo *and* for one that found no
    logo with ``logo_text_fallback`` off, and only the caller knows which.

    Each ImageMagick invocation keeps its own ``asyncio.to_thread`` hop, as it
    had inside the pipeline -- so this is awaited directly from an event loop,
    not wrapped in a thread again.
    """
    settings = art_config_for(config, art_kind)
    # Row 41: the provider said this image already carries text, so the
    # overlay and border go with the text rather than being layered onto
    # somebody's finished design.
    add_overlay = settings.add_overlay and not suppress_styling
    add_border = settings.add_border and not suppress_styling
    overlay = (
        str(Path(config.overlays_root) / settings.overlay_file)
        if add_overlay
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
            config.artwork.output_quality, add_border,
            settings.border_color, settings.border_width,
        ),
    )
    if logo_path is not None:
        await asyncio.to_thread(
            compositor.run,
            compositor.build_logo_argv(
                config.magick_binary, str(working), str(logo_path),
                settings.text, config.artwork.output_quality,
                config.artwork.logo_flat_color,
            ),
        )
    blocks = []
    if draw_text:
        blocks.append((settings.text, primary_text))
    if art_kind == "title_card":
        blocks.append((settings.episode_text, secondary_text))
    primary_point_size: int | None = None
    for style, text in blocks:
        if style is None or not text:
            continue
        prepared = prepare_text(text, style)
        font_path = str(Path(config.fonts_root) / style.font)
        fit = await asyncio.to_thread(
            fit_point_size, config.magick_binary, font_path, style, prepared
        )
        # Identity against settings.text rather than the loop position: on a
        # title card with draw_text off, the first block IS the episode text,
        # and recording its size as the title's would be a fact about the
        # wrong string.
        if style is settings.text:
            primary_point_size = fit.point_size
        if fit.truncated:
            # Posterizarr writes no file when text cannot fit at the minimum
            # point size. Emitting one here would produce artwork the current
            # system never would.
            return ComposeResult(
                output=working,
                truncated=True,
                detail=(
                    f"{text!r} does not fit in "
                    f"{style.max_width}x{style.max_height} at "
                    f"{style.min_point_size}pt"
                ),
            )
        await asyncio.to_thread(
            compositor.run,
            compositor.build_text_argv(
                config.magick_binary, str(working), style, font_path,
                fit.point_size, prepared, config.artwork.output_quality,
            ),
        )
    return ComposeResult(output=working, truncated=False, point_size=primary_point_size)


def _provider_rank(providers: list, provider_name: str | None) -> int | None:
    """Where the winning provider sat in the ladder this pass actually walked.

    Against the RUNTIME list, never ``config.providers.order``: app.py's
    ``_build_providers`` warns and drops a configured provider that has no
    implementation, so a config-index rank can claim a position that never
    existed on this deployment. A name the runtime list does not hold -- the
    ``"manual"`` a hand-placed override stamps -- has no ladder position at
    all and records None, rather than a number that would read as a downgrade
    of a choice no ladder made.
    """
    names = [getattr(provider, "name", None) for provider in providers]
    if provider_name is None or provider_name not in names:
        return None
    return names.index(provider_name)


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
    # The adoption walk's guard (naming.missing_number): year-grouped specials
    # arrive with episode_number None, and asset_path would raise for an item
    # whose artifact cannot be named at all -- parking the job on every full
    # pass. Such a row records an empty asset_path: there is no path to record.
    missing = naming.missing_number(art_kind, item.season_number, item.episode_number)
    target = "" if missing is not None else naming.asset_path(
        config, item.library, item.root_folder, art_kind,
        item.season_number, item.episode_number,
    )
    render = await _get_or_create_render(session, media_item, art_kind, target)

    if not settings.enabled:
        render.status = "skipped"
        render.detail = f"{art_kind} disabled in config"
        await session.commit()
        return render

    skip_title_reason = _should_skip_title(config, item, art_kind)
    if skip_title_reason is not None:
        render.status = "skipped"
        render.detail = skip_title_reason
        await session.commit()
        return render

    # After the config and skip-word gates, mirroring the walk: an item that
    # is both TBA-titled and unnumbered keeps its more specific reason.
    if missing is not None:
        render.status = "skipped"
        render.detail = f"item has no {missing}; cannot name its {art_kind}"
        await session.commit()
        return render

    # An adopted row describes an artifact this service found already on disk,
    # never rendered, and resolved no provider for. Compare the fingerprint it
    # could compute -- everything but the source URL -- and stop here if it
    # still holds. This sits above every provider call on purpose: the whole
    # point of the adoption run is that the first real pass over an existing
    # library costs zero outbound requests (spec section 5).
    if render.adopted and render.base_sha256:
        text_inputs, asset_hashes = await gather_fingerprint_inputs(config, item, art_kind)
        adopted_candidate = adopted_fingerprint(
            config, art_kind, render.base_sha256, text_inputs, asset_hashes
        )
        # Re-hash rather than merely stat. Between the adoption run and the
        # moment the old tools are actually stopped (step 3 of the cutover in
        # deploy/README.md) they are still writing into the same asset tree, so
        # a file replaced in that window would otherwise stay invisible: the
        # row would claim art that no longer matches its hash. Offloaded, as
        # elsewhere here, because assets_root can be an NFS mount. This is the
        # one hash the adopted pass is meant to cost. _file_sha256 answers ""
        # for a file that is gone, so a deleted asset fails the comparison and
        # re-renders without needing a separate exists() check.
        if adopted_candidate == render.fingerprint:
            current_sha = await asyncio.to_thread(_file_sha256, target)
            if current_sha == render.base_sha256:
                render.status = "rendered"
                render.detail = "adopted"
                await session.commit()
                return render

    primary_text, secondary_text = title_text_for(art_kind, item, config)

    with tempfile.TemporaryDirectory() as tmpdir:
        working = Path(tmpdir) / target.name

        # manual_override_path() stats the (possibly NFS) manual-assets mount;
        # offloaded like the rest of this pipeline's blocking I/O so a hung
        # mount cannot stall the event loop.
        override = await asyncio.to_thread(manual_override_path, config, item, art_kind)
        show_fallback = False
        local_source = False
        chosen_candidate = None
        # Action Center quality facts (roadmap 11a). Each of these is already
        # decided somewhere below and, until this phase, thrown away: the
        # ladder returns is_fallback and nobody reads it, the logo branch
        # materialises only its suppressed half, and the language order is an
        # inline expression at the select_artwork call. They are bound here so
        # the write-back at the end of this function can record them whichever
        # branch ran.
        selection_order: list[str] = []
        textless_fallback = False
        logo_text_fallback_taken = False
        text_point_size: int | None = None
        if override is not None:
            base_sha = await asyncio.to_thread(_stage_override, override, working)
            source_url, provider_name, textless = str(override), "manual", None
            local_source = True
        else:
            if online_fetch_disabled(config, art_kind):
                # Row 47: no local asset and no provider allowed. Skipped with
                # the setting named, rather than reported as "no art on any
                # provider" -- no provider was asked.
                render.status = "skipped"
                render.detail = (
                    f"no local asset for {art_kind} and online asset fetch is "
                    "disabled by artwork.disable_online_asset_fetch"
                )
                await session.commit()
                return render
            selection_order = language_order_for(config, item.library, art_kind)
            selection = await select_artwork(
                providers,
                selection_order,
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
            if selection.candidate is None and art_kind == "season_poster":
                # Kometa's effective behaviour: a season poster is classically
                # the show's own poster with the season text applied -- which is
                # exactly what compose_styled does below to whatever base image
                # it is handed. So a season with no season-specific art anywhere
                # is styled from the show's poster rather than left at no_art.
                #
                # A season's ResolvedItem already carries the SHOW's external ids
                # (plex/client._RawMatch: identity is the season's, but the agent
                # ids come from the containing show), so dropping the season and
                # episode numbers is the whole difference between this request
                # and the season one above -- and makes it the same request the
                # show's own poster render issues, down to the language order.
                # The rank recorded below must be taken against the list this
                # request was actually ranked by. The show-poster request uses
                # the poster order, so re-binding it here is the difference
                # between a true fact and one computed against a list that did
                # not choose this image.
                selection_order = language_order_for(config, item.library, "poster")
                selection = await select_artwork(
                    providers,
                    selection_order,
                    art.ArtRequest(
                        art_kind=art.POSTER,
                        is_movie=item.kind == "movie",
                        tmdb_id=item.tmdb_id,
                        tvdb_id=item.tvdb_id,
                        imdb_id=item.imdb_id,
                    ),
                )
                show_fallback = selection.candidate is not None
            if selection.candidate is None:
                render.status = "no_art"
                render.detail = f"no {art_kind} art on any provider"
                await session.commit()
                return render
            candidate = selection.candidate
            chosen_candidate = candidate
            base_sha = await _download(http, candidate.url, working)
            source_url = candidate.url
            provider_name = candidate.provider
            textless = candidate.is_textless
            # True exactly when the order preferred textless art, no provider
            # had any, and the ladder took a text-bearing image rather than
            # nothing. The ladder has returned this since it was written and
            # nothing has ever read it.
            textless_fallback = selection.is_fallback

        # Posterizarr parity: UseLogo/UseClearlogo composites a clearlogo in place
        # of the title text on posters. With LogoTextFallback false, a poster with
        # no logo on any provider gets neither logo nor text (spec section on
        # clearlogos). Other art kinds are unaffected.
        logo_path: Path | None = None
        logo_sha = ""
        suppress_text = False
        if art_kind == "poster" and config.artwork.use_logo and settings.text is not None:
            # An operator's picked logo comes first, and stops the ladder from
            # running at all: a selection made here would be staged over the
            # choice, and the request it costs is one the choice made pointless.
            # Nested inside the use_logo guard on purpose -- `use_logo: false`
            # is a deployment saying it does not composite logos, and a file on
            # a mount does not overrule the config. Unconditional on
            # online_fetch_disabled -- row 47 suppresses provider requests, not
            # local lookups, and find_logo_override makes none: mirrors how
            # manual_override_path treats the artifact's own base image.
            picked_logo = await asyncio.to_thread(find_logo_override, config, item)
            if picked_logo is not None:
                logo_path = Path(tmpdir) / f"logo{picked_logo.suffix}"
                logo_sha = await asyncio.to_thread(_stage_override, picked_logo, logo_path)
            elif not online_fetch_disabled(config, art_kind):
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
                        prefer_clearart=config.artwork.use_clearart,
                    ),
                )
                if logo_selection.candidate is not None:
                    logo_candidate = logo_selection.candidate
                    suffix = Path(httpx.URL(logo_candidate.url).path).suffix or ".png"
                    logo_path = Path(tmpdir) / f"logo{suffix}"
                    logo_sha = await _download(http, logo_candidate.url, logo_path)
                elif not config.artwork.logo_text_fallback:
                    suppress_text = True
                else:
                    # The other half of the same decision, which until now had
                    # no variable at all: no logo on any provider AND
                    # logo_text_fallback on, so this poster is wearing its
                    # title text in a logo's place. That is the fact roadmap
                    # 103 calls "logo-to-text fallback taken".
                    logo_text_fallback_taken = True

        suppress_styling = (
            settings.skip_add_text_when_with_text and known_with_text(chosen_candidate)
        )
        draw_text = not (art_kind == "poster" and (logo_path is not None or suppress_text))
        if local_source and not draw_text_for_local_source(config, art_kind):
            draw_text = False
        if suppress_styling:
            draw_text = False

        text_inputs, asset_hashes = await gather_fingerprint_inputs(
            config, item, art_kind, draw_text=draw_text, logo_sha=logo_sha,
            suppress_styling=suppress_styling,
        )

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
            styled = await compose_styled(
                config, art_kind, working,
                primary_text=primary_text, secondary_text=secondary_text,
                draw_text=draw_text, logo_path=logo_path,
                suppress_styling=suppress_styling,
            )
            if styled.truncated:
                render.status = "truncated"
                render.detail = styled.detail
                await session.commit()
                return render
            text_point_size = styled.point_size
            await asyncio.to_thread(
                _publish, styled.output, target, config.backup_root, config.assets_root
            )

    render.provider = provider_name
    render.source_url = source_url
    render.textless = textless
    # The Action Center's quality facts (roadmap 11a), here rather than
    # anywhere earlier for source_mode's own stated reason two blocks below:
    # the "unchanged" fingerprint short-circuit returns above this point, so a
    # fact recorded here survives a pass that changes nothing, while anything
    # written like `detail` would be blanked on the next visit.
    #
    # Facts only. Whether "en under an xx-preferring order" is worth an
    # operator's attention is decided by a SQL predicate in actions/flags.py,
    # against the config that is live when the queue is read -- never by a
    # verdict frozen here, which would go stale the moment the order changed.
    render.selected_language = (
        normalise_language(chosen_candidate.language) if chosen_candidate is not None else None
    )
    render.language_rank = (
        language_rank(chosen_candidate, selection_order)
        if chosen_candidate is not None and selection_order
        else None
    )
    render.provider_rank = _provider_rank(providers, provider_name)
    render.textless_fallback = textless_fallback
    render.logo_text_fallback = logo_text_fallback_taken
    render.base_width = chosen_candidate.width if chosen_candidate is not None else None
    render.base_height = chosen_candidate.height if chosen_candidate is not None else None
    render.text_point_size = text_point_size
    # Database clock, per the global constraint, exactly like rendered_at
    # below: the app and database clocks drift.
    render.quality_scored_at = func.now()
    render.base_sha256 = base_sha
    render.fingerprint = fingerprint
    render.status = "rendered"
    render.detail = (
        "no season_poster art on any provider; styled the show's poster instead"
        if show_fallback
        else None
    )
    # Provenance the "unchanged" short-circuit above cannot blank out, unlike
    # detail. Cleared again when the row stops being a fallback: the fingerprint
    # includes the source URL, so season art appearing on a provider re-renders
    # this row from the season's own art, and it must not go on claiming a
    # fallback it no longer made.
    if show_fallback:
        render.source_mode = "show_fallback"
    elif render.source_mode == "show_fallback":
        render.source_mode = "generate"
    # A real render just happened, so the adoption no longer describes reality:
    # this row now has a source URL and a fingerprint that covers it.
    render.adopted = False
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


async def _fetch_parental_categories(imdb_parental, item) -> list[tuple[str, str, str]] | None:
    """Row 85's fetch, isolated so one IMDb hiccup costs only this item's
    labels, not everything else this pass gathered.

    ``None`` -- for every reason ``parental_label_edits`` treats as "nothing
    to label" -- covers: no client (feature disabled upstream), no IMDb id,
    an item kind IMDb's parental guide is not modelled for (season/episode --
    the guide is a title-level concept, the same scope row 84's TVDb overlay
    already uses), and a transport failure.
    """
    if imdb_parental is None or not item.imdb_id or item.kind not in ("movie", "show"):
        return None
    try:
        return await imdb_parental.categories(item.imdb_id)
    except httpx.HTTPError as exc:
        # The row-84 precedent: one provider's transient failure must not
        # throw away everything else this pass gathered.
        logger.warning("IMDb parental-guide request failed; skipping labels: %s", exc)
        return None


async def apply_metadata(
    session: AsyncSession,
    config: Config,
    media_item_id: int,
    item: ResolvedItem,
    plex_item,
    tmdb_facts,
    mdblist,
    tvdb=None,
    imdb_parental=None,
) -> GatheredFacts:
    """Gather this item's facts, store them, and write the changed ones to Plex.

    Runs before any badge rendering, because badges read the values from Plex
    rather than from the providers.
    """
    if not config.operations.enabled:
        return GatheredFacts()

    facts = await gather_facts(
        session, item, tmdb_facts, mdblist, operations=config.operations, tvdb=tvdb
    )
    await persist_facts(session, media_item_id, facts)

    # Row 85. Only fetched when config enables it, so a deployment that never
    # turns this on pays no request and gets today's behaviour exactly.
    parental_categories = None
    if config.operations.parental_labels_enabled:
        parental_categories = await _fetch_parental_categories(imdb_parental, item)

    # Row 87: a verb IS its field's source, so it must fire even when the
    # provider facts are empty -- ``facts.is_empty()`` alone would otherwise
    # skip apply_facts (and every verb with it) on an item no provider has
    # anything to say about. Row 85's fetched categories are the same shape
    # of "the only thing this pass has to write."
    has_verbs = bool(config.operations.field_verbs)
    has_parental = parental_categories is not None
    if (
        config.operations.write_to_plex
        and plex_item is not None
        and (not facts.is_empty() or has_verbs or has_parental)
    ):
        # Row 35. Checked here, at the facts/write seam, and not earlier: the
        # facts above are still gathered and persisted for an exempt item,
        # because the badge stage reads the persisted row rather than this
        # write.
        exempt = exemption_reason(
            config.operations, item.rating_key, item.imdb_id,
            getattr(plex_item, "labels", None),
        )
        if exempt is not None:
            logger.info("plex: skipped writing %s: %s", item.rating_key, exempt)
        else:
            await apply_facts(plex_item, facts, config.operations, parental_categories)
    return facts


async def _already_in_plex(config, probe, plex_item, render, fingerprint) -> bool:
    """Whether Plex is already serving exactly the badged image we would upload.

    Only asked when the database has no ``badge_fingerprint`` for this render:
    after adoption, after a database restore, or for anything this service has
    never badged. In every other case the stored fingerprint already answers
    the question for free, and asking Plex would be a request per item.

    The answer comes from the artwork itself -- uploaded images carry their
    fingerprint in EXIF ``ImageDescription`` (see ``plex/exif.py``) -- which
    makes Plex, not our database, the source of truth about what is in Plex.
    That is what makes adoption cheap on the badge side: at cutover the whole
    library has no ``badge_fingerprint``, and without this every item would be
    re-composed and re-uploaded to produce the bytes already there.

    Best-effort by construction. Anything at all going wrong -- no probe
    wired up, no Plex object, a transport error, a stranger's EXIF -- answers
    False, and the caller does the normal upload. A wrong False costs one
    redundant upload; there is no wrong True, because the fingerprint read
    back has to equal the one just computed.

    Deliberately not asked when ``upload_to_plex`` is off: with nothing to
    skip there is nothing to save, and a dry run should not spend ~16,000
    range requests learning that.
    """
    if not (config.badges.adopt_from_plex and config.badges.upload_to_plex):
        return False
    if probe is None or plex_item is None or render.badge_fingerprint is not None:
        return False
    try:
        recorded = await probe(plex_item)
    except Exception:
        logger.debug("could not read artwork provenance from Plex", exc_info=True)
        return False
    return recorded is not None and recorded == fingerprint


async def apply_badges(session, config, render, item, plex_item, facts, probe=None) -> None:
    """Badge one rendered artifact and upload it, if anything changed.

    The fingerprint gate is the point of this whole stage: an unchanged item
    costs one hash and no image work at all. It is also what stops uploads
    accumulating on the Plex server, which is what happens when every run
    uploads unconditionally.

    ``probe`` is the optional provenance reader described in
    ``_already_in_plex`` -- an async callable taking the ``plexapi`` object and
    returning the fingerprint recorded in its current artwork. Optional so
    every caller that only cares about composing (including every test
    predating this) keeps working unchanged.
    """
    if not config.badges.enabled:
        return
    # Backgrounds are never badged. The tool being replaced overlays posters,
    # season posters and episode title cards only -- a fanart backdrop with a
    # runtime badge stamped on it is not something it produces, and not
    # something we should start producing.
    if render.art_kind == "background":
        return
    # `asset_path` is written when the row is created, before any file exists,
    # so a render that never produced one -- no_art, truncated, skipped,
    # failed -- would send Image.open() at a path that is not there.
    if render.status != "rendered":
        return

    # media_info_from_plex() calls item.reload() when `.media` is absent, which
    # is a blocking `requests` GET -- and plexapi Show and Season objects never
    # carry `.media`, so that is not a rare path. On the event loop it stalls
    # the liveness probe and every other worker.
    media = await asyncio.to_thread(media_info_from_plex, plex_item)
    inputs = BadgeInputs(
        media=media,
        critic_rating=getattr(facts, "critic_rating", None),
        audience_rating=getattr(facts, "audience_rating", None),
        content_rating=getattr(facts, "content_rating", None),
        video_format=video_format_text(media),
    )
    values = badge_values(render.art_kind, inputs)
    # render.fingerprint, not render.base_sha256: the badged image is composed
    # from the *base we rendered*, so the gate has to track what went into that
    # base -- see badge_fingerprint's docstring.
    fingerprint = badge_fingerprint(
        render.fingerprint or "", render.art_kind, values, manifest_sha()
    )
    if fingerprint == render.badge_fingerprint and render.upload_status == "uploaded":
        return

    if await _already_in_plex(config, probe, plex_item, render, fingerprint):
        # The image Plex is serving stamped this exact fingerprint, so it is
        # byte-for-byte what compose() would produce. Record what is already
        # true and skip both the composite and the upload.
        render.badge_fingerprint = fingerprint
        render.upload_status = "uploaded"
        render.uploaded_at = func.now()
        await session.commit()
        return

    data = await asyncio.to_thread(
        compose_badges, Path(render.asset_path), render.art_kind, inputs, fingerprint
    )
    render.badge_fingerprint = fingerprint

    if not config.badges.upload_to_plex:
        render.upload_status = "skipped"
        await session.flush()
        return

    try:
        await asyncio.to_thread(
            upload_artwork, plex_item, data, render.art_kind, config.badges.lock_artwork
        )
    except Exception:
        render.upload_status = "failed"
        await session.flush()
        logger.warning("badge upload failed for %s", item.rating_key, exc_info=True)
        return

    render.upload_status = "uploaded"
    render.uploaded_at = func.now()
    # Commit, not flush: the badge stage's own error handler rolls back, and a
    # flushed-but-uncommitted fingerprint for an upload that already reached
    # Plex would be discarded, re-uploading the identical image next pass.
    # Preventing exactly that accumulation is the point of this stage.
    await session.commit()


async def process_item(
    session: AsyncSession,
    config: Config,
    http: httpx.AsyncClient,
    plex,
    providers: list,
    intent: RenderIntent,
    tmdb_facts=None,
    mdblist=None,
    artwork_probe=None,
    imdb_parental=None,
) -> list[Render]:
    """Resolve one intent and build every artifact it implies.

    ``tmdb_facts``/``mdblist`` are the metadata-operations clients; they are
    optional (and default to ``None``) so callers that only care about
    artwork — including every test that predates Phase 2a — keep working
    unchanged. Metadata operations run whenever ``tmdb_facts`` is supplied.
    ``mdblist`` no longer gates them: production wiring always supplies a
    client, real or a stand-in when no API key is configured (see
    ``app._build_mdblist``), so an unset key degrades only the content
    rating rather than every metadata operation.

    ``artwork_probe`` is passed straight to ``apply_badges``; see
    ``_already_in_plex`` for what it is for and why it is optional.

    ``imdb_parental`` is row 85's ``IMDbParentalGuideClient``; ``None`` --
    what every direct caller and most tests pass -- means the fetch in
    ``apply_metadata`` is skipped, whatever ``operations.parental_labels_enabled``
    says (the same shape ``tvdb=None`` already has for row 84).

    A failure anywhere in this step is caught and logged rather than
    propagated — a ratings-provider hiccup must not cost the item its
    poster and background, which the artifact loop below still owes it.
    """
    item = await plex.resolve(intent)

    media_item = None
    plex_item = None
    if config.operations.enabled and tmdb_facts is not None:
        # Bound outside the try on purpose: a `plex` that has no fetch_item at
        # all is a wiring bug, not the runtime failure this block contains, and
        # the except below would demote it to a WARNING and carry on. That is
        # exactly how a test double missing the method stayed hidden until it
        # resurfaced as an unrelated error much later.
        fetch_item = plex.fetch_item
        try:
            media_item = await _upsert_media_item(session, item)
            plex_item = await fetch_item(item.rating_key)
            # Row 84's tvdb client, if the deployment's provider order builds
            # one -- the same object `providers` already holds, never a new
            # one, so it shares that client's cached token and cache.
            tvdb = next((p for p in providers if getattr(p, "name", None) == "TVDB"), None)
            await apply_metadata(
                session, config, media_item.id, item, plex_item, tmdb_facts, mdblist, tvdb,
                imdb_parental,
            )
        except Exception:
            # Finding 5: if the failure was a database error, the transaction
            # is already aborted; without rolling back here, the artifact
            # loop's first session.execute() below would raise
            # PendingRollbackError instead of rendering, defeating this
            # containment's whole purpose. Every other error path in this
            # codebase rolls back first (see queue/worker.py).
            await session.rollback()
            logger.warning(
                "metadata operations failed for %s; continuing to artwork",
                item.rating_key, exc_info=True,
            )

    results = []
    for art_kind in ART_KINDS_FOR[intent.kind]:
        results.append(
            await render_artifact(session, config, http, item, art_kind, providers)
        )

    if config.badges.enabled:
        fetch_item = plex.fetch_item  # outside the try; see the block above
        try:
            if media_item is None:
                media_item = await _upsert_media_item(session, item)
            if plex_item is None:
                plex_item = await fetch_item(item.rating_key)
            # The persisted row, not the in-memory GatheredFacts from the
            # metadata-operations block above: a partial gather this pass
            # (e.g. only a new critic rating) must not blank out fields a
            # previous pass already established, and badges must still get
            # facts when operations.enabled is off entirely.
            facts = (
                await session.execute(
                    select(ItemFacts).where(ItemFacts.item_id == media_item.id)
                )
            ).scalar_one_or_none() or GatheredFacts()
            for render in results:
                await apply_badges(
                    session, config, render, media_item, plex_item, facts,
                    probe=artwork_probe,
                )
        except Exception:
            # Same containment as the metadata-operations block above: the
            # artifact loop already wrote the base image to disk, and a
            # badge failure must not cost the item that.
            await session.rollback()
            logger.warning(
                "badge stage failed for %s; base artwork already on disk",
                item.rating_key, exc_info=True,
            )

    return results
