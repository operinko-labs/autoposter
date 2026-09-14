import asyncio
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.badges.compose import (
    manifest_sha,
    BadgeInputs,
    badge_fingerprint,
    badge_values,
    compose as compose_badges,
)
from autoposter.badges.values import (
    MediaInfo,
    media_info_from_plex,
    plex_native_ratings,
    video_format_text,
)
from autoposter.catchup import CATCH_UP_KIND
from autoposter.config.loader import config_for_library, render_version_for
from autoposter.config.schema import Config, TextStyle
from autoposter import deliveries
from autoposter.db.models import (
    ItemFacts, MediaItem, MediaItemServerRef, MetadataWrite, Render, RenderDelivery, Run,
)
from autoposter.db.refs import item_id_for
from autoposter.facts.gather import gather_facts, persist_facts
from autoposter.facts.mdblist import MDBListLimitReached
from autoposter.facts.models import GatheredFacts
from autoposter.facts.sort_positions import (
    delete_sort_position,
    load_sort_position,
    with_sort_position,
)
from autoposter.intake.arr import RenderIntent
from autoposter.overlays.selection import OverlayItemView
from autoposter.overlays.selection import select as select_overlay_definitions
from autoposter.overlays.sources import OverlaySourceError, resolve_image_path
from autoposter.plex.artwork import generated_title_card_url
from autoposter.plex.client import ResolvedItem
from autoposter.plex.item_overrides import load_overrides, overlaid_badge_facts
from autoposter.plex.writer import exemption_reason
from autoposter.providers import base as art
from autoposter.providers.ladder import language_rank, normalise_language, select_artwork
from autoposter.render import compositor, naming
# Re-exported, not merely used: these lived here until the mass-ops logo
# updater needed the same guard and could not import this module to get it (see
# render/artwork_fetch.py's own docstring). `api/candidates.py`, `app.py` and
# `tests/test_render_input_guard.py` all still reach them through this name.
from autoposter.render.artwork_fetch import (
    RENDER_MAX_BYTES,  # noqa: F401 - re-export for tests/test_render_input_guard.py
    SourceRefused,
    _ARTWORK_MAX_PIXELS,
    _download,
    _validate_image,  # noqa: F401 - re-export for api/candidates.py
    pick_guarded_logo,
)
from autoposter.render.textfit import fit_point_size, prepare_text
from autoposter.servers.base import (
    CAP_ARTWORK_PROVENANCE, CAP_LOCK_ARTWORK, CAP_TITLE_CARD_URL,
    ItemNotFound, PathMismatch, ServerItemRef,
)
from autoposter.servers.identity import identity_key_for, parent_identity_key_for

logger = logging.getLogger(__name__)

# Mirrored in frontend/src/artKind.ts, which shows each kind's first entry as
# the item's primary art. A kind added here must be added there too, or its
# pages fall back to `poster` and 404. The flat list of art kinds -- the one a
# render version is computed against -- lives in config/loader.py's
# RENDER_ART_KINDS (roadmap row 111); a kind added here must be added there
# too, or it silently gets no version of its own.
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


def _stage_override(override: Path, working: Path, *, stage: str) -> str:
    """Copy the manual-override file into the working directory and hash it.

    ``manual_assets_root`` is a mount an operator writes to directly, and
    nothing between it and this read enforces a byte cap: ``PICK_MAX_BYTES``
    guards the API door (``api/candidates.py``), ``RENDER_MAX_BYTES`` guards a
    provider download, and ``manual_override_path``/``find_logo_override`` do
    a bare ``.exists()``. A file dropped straight onto the share reached
    ImageMagick past all three (roadmap row 238, surface 3).

    A ``stat()`` before the read, at the ceiling a downloaded source is
    already held to, so an override that would cost the pod its memory is
    refused with a served reason. ``process_item``'s per-kind
    ``except SourceRefused`` records it and moves to the next art kind.

    Deliberately a PRE-CHECK and nothing more: the bytes copied and the bytes
    hashed are exactly what they were, so this return value -- which is
    ``compute_fingerprint``'s ``base_sha256`` at the base call site and one of
    its ``asset_hashes`` at the logo one -- is unmoved for every file that was
    already accepted, and no stored fingerprint changes.
    """
    size = override.stat().st_size
    if size > RENDER_MAX_BYTES:
        raise SourceRefused(
            f"{stage} override is {size} bytes, over the "
            f"{RENDER_MAX_BYTES}-byte render source ceiling"
        )
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
    only on the explicit statement and fails open otherwise: a provider
    carrying no signal simply does not trigger the rule.

    ``None`` -- a manual override, which resolved no candidate at all -- is
    likewise not known with text.
    """
    return getattr(candidate, "includes_text", None) is True


# The gap between the show title and the season text it sits above (roadmap
# row 78). OURS: there is no captured upstream render of this feature to
# measure one from -- see `stacked_above`.
SHOW_TITLE_GUTTER = 10


def stacked_above(below: TextStyle, below_point_size: int) -> str:
    """The ``text_offset`` that puts one text block a line above ``below``'s.

    Roadmap row 78's layout adjudication, and it is an adjudication rather
    than a transcription. Posterizarr gives ``ShowTitleOnSeasonPosterPart``
    the same ``text_offset: "+300"`` and ``TextGravity: "south"`` as
    ``SeasonPosterOverlayPart``, so drawn as configured the show title lands
    on top of the season text. Its own toggle ships ``false`` upstream, so
    those values were never tuned against a real render, and no captured
    output of this feature exists anywhere to compare against -- the same
    "no oracle" this project already declares for row 105
    (``collections/poster_title.py``). The ruling: the show title stacks ABOVE
    the season text by one line of it plus ``SHOW_TITLE_GUTTER``, at the
    season block's own gravity.

    **The sign is not a detail.** ``compositor.build_text_argv`` composites
    the caption with ``-gravity <g> -geometry +0<offset>``, and ImageMagick
    measures that offset INWARD from the named edge: under a bottom-anchored
    gravity a larger value is HIGHER on the canvas, under every other gravity
    a larger value is lower. So the raise is ADDED for ``south``/
    ``southwest``/``southeast`` -- the shipped configuration, and the only one
    upstream's block was written for -- and SUBTRACTED otherwise, which is
    what keeps the layout the right way up for an operator who anchored the
    season text to the top of the poster.

    ``below_point_size`` is the season block's FITTED size, not its configured
    maximum: ``compose_styled`` already records exactly that value by
    identity, and using the maximum would push the show title a hundred points
    clear of a title that auto-fitted small.

    Answers a SIGNED string, because ``TextStyle.text_offset`` is validated to
    carry a sign (``config/schema.py``'s ``_must_carry_sign``) and
    ``build_text_argv`` concatenates it after ``+0``.
    """
    raise_by = below_point_size + SHOW_TITLE_GUTTER
    base = int(below.text_offset)
    # `.lower()`: `TextStyle.gravity` is a free-form string with no validator
    # (unlike the collection side's `_COLLECTION_GRAVITIES`, `config/schema.py`),
    # and ImageMagick's own `-gravity` argument matches case-insensitively, so
    # "South" and "SOUTHEAST" are both legal today and both render identically
    # to "south". A case-sensitive compare here would take the wrong branch for
    # either -- silently, since ImageMagick draws something regardless of sign.
    value = (
        base + raise_by if below.gravity.lower().startswith("south") else base - raise_by
    )
    return f"+{value}" if value >= 0 else str(value)


def show_title_for(art_kind: str, item: ResolvedItem, config: Config) -> str | None:
    """The SHOW's own title, for the block a season poster draws above its
    season text (roadmap row 78).

    ``None`` for every other art kind; for a season poster whose
    ``show_title`` block is unset or has ``add_text`` off; and for an item
    carrying no show title, which is the ordinary case for anything that is
    not a season (``ResolvedItem.show_title`` is filled only there).

    The caller passes the answer as ``compose_styled``'s ``secondary_text``.
    That parameter already exists for the title card's second block,
    ``gather_fingerprint_inputs`` already folds it into ``text_inputs``, and
    riding it leaves ``compose_styled``'s signature and both of its other
    callers (``api/testing.py``, and the pipeline's own call site) untouched.
    """
    if art_kind != "season_poster":
        return None
    style = art_config_for(config, art_kind).show_title
    if style is None or not style.add_text:
        return None
    return item.show_title or None


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
    if art_kind == "season_poster":
        # Roadmap row 43's other half, co-delivered here.
        # Posterizarr puts OverrideSeasonName in SeasonPosterOverlayPart -- it
        # renames the text on the SEASON POSTER -- while row 43 landed the
        # table on TitleCardConfig and wired it only to the card's second
        # line. The SAME table is read here, from the same key: an operator
        # who already wrote {"0": "Specials"} gets it on both artifacts, and
        # the season_poster kind pays its version move once rather than twice.
        # `.get(key, default)`, deliberately, NOT `override or <default>`:
        # the title card's own arm (`:216-219`) draws whatever the table says,
        # so an entry of "" draws nothing there. One shared table must not
        # mean two behaviours for one degenerate value -- an operator who
        # blanks an entry is blanking it on both artifacts or on neither.
        return (
            config.artwork.title_card.season_name_overrides.get(
                str(item.season_number), primary_title_for(item, config)
            ),
            show_title_for(art_kind, item, config),
        )
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
    # Row 78's block, gated the same three ways `compose_styled` gates it --
    # plus `draw_text`, which the title card's block above deliberately does
    # not carry (a card draws its episode line even when the title is
    # suppressed; a season poster's show title does not). Miss this
    # and a font swap on the new block silently does not re-render.
    if (
        art_kind == "season_poster"
        and draw_text
        and settings.show_title is not None
        and secondary_text
    ):
        font_hashes.append(
            await asyncio.to_thread(
                _file_sha256, Path(config.fonts_root) / settings.show_title.font
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
    # render_version_for, not config.version: roadmap row 111 confines an
    # invalidation to the kinds an edit touches. config.version is still the
    # wholesale hash -- `api/routes._render_affecting`'s superset
    # short-circuit and the Settings page's "version A to B" line -- but
    # since roadmap row 247 no fingerprint comparison reads it.
    return compute_fingerprint(
        render_version_for(art_kind, config), art_kind, None, base_sha256,
        text_inputs, asset_hashes,
    )


async def fetch_plex_generated_base(
    http: httpx.AsyncClient, plex, native_id: str, destination: Path,
    *, base_url: str, headers: dict[str, str], stage: str,
) -> str | None:
    """Download Plex's own generated title-card frame, or answer ``None``.

    The plex-preview fallback (roadmap row 241): lazily fetches the plexapi
    item for ``native_id`` -- only an episode whose provider ladder came back
    empty ever reaches this, so this is not a second fetch for every item
    ``process_item`` already handles -- lists its posters, and downloads the
    ``media://``-prefixed entry (``generated_title_card_url``) -- never an
    ``upload://`` entry, which is our own previous output locked onto the
    same field and would feed our renders back into themselves. ``None``
    when the listing has no such entry, the caller's cue to fall through to
    the existing ``no_art`` outcome.

    Goes through ``_download``, not a bare GET, so the fetch gets #131's
    full-decode validation and the byte cap for free.
    ``follow_redirects=False``: ``X-Plex-Token`` is a custom header httpx
    will not strip on a cross-origin redirect, and PMS never needs one for
    an image blob anyway.

    Built as a ``functools.partial`` at app.py's composition time, with
    ``http``, ``plex``, ``base_url`` and the token header baked in --
    ``render_artifact`` calls the result with only ``native_id`` and
    ``destination``, so the token is never in scope there at all.

    A non-2xx from Plex (a rotated token's 401, a 3xx now that
    ``follow_redirects=False``, a PMS 5xx) raises ``httpx.HTTPStatusError``
    from ``_download``'s ``raise_for_status()``. ``process_item``'s per-kind
    containment only catches ``SourceRefused``, so left uncaught this would
    fail the whole job over what used to be a quiet ``no_art`` row. Caught
    here and logged once, by native id and status code only -- never the
    URL, which carries no token itself but is still not worth logging -- so
    the caller falls through to the existing ``no_art`` outcome.
    """
    if CAP_TITLE_CARD_URL not in plex.capabilities:
        return None
    plex_item = await plex.fetch_item(native_id)
    url = await generated_title_card_url(plex_item, base_url)
    if url is None:
        return None
    try:
        return await _download(
            http, url, destination, stage=stage, headers=headers, follow_redirects=False,
        )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Plex's generated frame fetch failed for %s: HTTP %s",
            native_id, exc.response.status_code,
        )
        return None


async def upsert_server_ref(session: AsyncSession, item_id: int, item: ResolvedItem) -> None:
    """ONE ref per (item, server) -- spec §4.1's invariant, enforced here.

    A key that moved to another item (a Plex re-match, a library rebuild) is
    re-pointed by the upsert, which is the whole of the old
    ``_rekey_by_identity``: identity is the row, the server id is an
    attribute. But re-pointing alone would leave the item holding BOTH ids
    for that server -- the old one still points at this row -- and every
    reader that asks "what is this item's Plex id" (``db/refs.py``, the API
    payloads, the credits scan) would then have to pick one of several
    arbitrarily. The item's other ids for THIS server are deleted instead:
    the one just resolved is the live one, by construction.

    Only this server's other refs. A Jellyfin id and a Plex id for the same
    item are the point of the table, not a conflict.
    """
    stmt = insert(MediaItemServerRef).values(
        item_id=item_id, server=item.server, native_id=item.native_id, library=item.library,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_server_ref",
        set_={"item_id": item_id, "library": item.library, "updated_at": func.now()},
    )
    await session.execute(stmt)
    await session.execute(
        delete(MediaItemServerRef).where(
            MediaItemServerRef.item_id == item_id,
            MediaItemServerRef.server == item.server,
            MediaItemServerRef.native_id != item.native_id,
        )
    )


def _is_file_replacement(existing_key: str, key: str) -> bool:
    """Do these two keys differ ONLY in ``identity_key``'s file field?

    ``kind:provider:id:coords:file`` (servers/identity.py, five fields, the
    last of which may itself contain colons -- hence ``maxsplit=4``). The
    same first four fields mean the same kind, the same provider id and the
    same season/episode coordinates: the same *item*. A different fifth
    field is a different file for it.

    The legacy placeholder ``kind:legacy:plex:<id>`` has only four fields and
    is deliberately not matched here -- it is handled on its own branch.
    """
    a = existing_key.split(":", 4)
    b = key.split(":", 4)
    return len(a) == 5 and len(b) == 5 and a[:4] == b[:4] and a[4] != b[4]


async def _rekey_in_place(session: AsyncSession, item: ResolvedItem, key: str) -> None:
    """Move an existing row onto the incoming key IN PLACE when the server
    ref says it is the same item under a new key (spec §4.2/§4.6).

    Two shapes reach this, both keyed on the row the incoming
    ``(server, native_id)`` already points at:

    **The migration's legacy placeholder.** A row the migration could not
    otherwise identify is keyed on its Plex id alone
    (``kind:legacy:plex:<id>``) -- a placeholder, not a real identity. Plex
    only, because that is what the placeholder was minted from; a Jellyfin
    ref carrying the same id string is a different item entirely.

    **A file replacement.** A Radarr quality upgrade replaces a movie's file
    in place: same Plex ratingKey, same tmdb id, new basename -- and the
    basename is in the key. Without this the upsert would INSERT a second row
    under the new key and re-point the ref onto it, orphaning the original's
    overrides, dismissals and renders on a row nothing will ever resolve
    again. The same ref plus the same ``kind:provider:id:coords:`` prefix is
    what makes that safe to assert: it is the same item, the same server, the
    same season and episode -- only its file moved. A DIFFERENT ref with the
    same prefix is the 4K/HD pair and stays two rows, which is why the ref
    lookup, not the prefix, is the first test.

    Either way, a genuine re-match onto a different identity (a Plex re-match
    onto another item, a rename to a different film) matches neither shape:
    the ref simply re-points, same as before.

    The full key may already belong to ANOTHER row, in which case there is
    nothing to promote onto -- the unique index would abort it. The existing
    row is left as it is and the upsert below hits that other row, with the
    ref re-pointing to it.

    Not atomic across two concurrent workers resolving the same native id at
    once: both could read the same pre-promotion ``existing_key`` before
    either writes. The window is narrow (one SELECT to the next UPDATE), and
    the unique index on ``identity_key`` aborts the loser's transaction
    rather than corrupting anything -- the loser's caller retries the whole
    upsert, same as any other constraint-violation retry in this module.
    """
    existing_id = await item_id_for(session, item.server, item.native_id)
    if existing_id is None:
        return
    existing_key = (
        await session.execute(select(MediaItem.identity_key).where(MediaItem.id == existing_id))
    ).scalar_one_or_none()
    if existing_key is None or existing_key == key:
        return
    is_legacy = (
        item.server == "plex" and existing_key == f"{item.kind}:legacy:plex:{item.native_id}"
    )
    if not (is_legacy or _is_file_replacement(existing_key, key)):
        return
    already_used = (
        await session.execute(select(MediaItem.id).where(MediaItem.identity_key == key))
    ).scalar_one_or_none()
    if already_used is not None:
        return
    await session.execute(update(MediaItem).where(MediaItem.id == existing_id).values(identity_key=key))
    logger.info("identity re-keyed in place: %s -> %s (item %d)", existing_key, key, existing_id)


async def _upsert_media_item(session: AsyncSession, item: ResolvedItem) -> MediaItem:
    """Insert or update the item, safe under concurrent workers.

    ``identity_key`` carries the unique constraint (spec §4.2), so the
    Postgres upsert is atomic -- the same reason the old ``rating_key``
    upsert was one; a select-then-insert here would race two workers that
    both resolve the same identity at once.

    ``parent_id`` is resolved through the parent's own identity key rather
    than a server-native id, so an episode finds its show regardless of which
    server (or server key) either was last resolved through. A parent not yet
    processed leaves it null rather than inventing one; a later upsert of
    this same item fills it in once the parent exists. The ``set_`` below
    only overwrites ``parent_id`` when THIS upsert resolved one -- a re-visit
    that finds no parent (the parent row does not exist YET, or this call
    carries no parent ids at all) must not null out a parent_id an earlier
    upsert already set.
    """
    key = identity_key_for(item)
    await _rekey_in_place(session, item, key)
    parent_id = None
    parent_key = parent_identity_key_for(item)
    if parent_key is not None:
        parent_id = (
            await session.execute(select(MediaItem.id).where(MediaItem.identity_key == parent_key))
        ).scalar_one_or_none()

    mutable = dict(
        library=item.library,
        kind=item.kind,
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
    stmt = insert(MediaItem).values(identity_key=key, parent_id=parent_id, **mutable)
    on_update = dict(mutable, updated_at=func.now())
    if parent_id is not None:
        on_update["parent_id"] = parent_id
    stmt = stmt.on_conflict_do_update(index_elements=["identity_key"], set_=on_update)
    await session.execute(stmt)
    row = (
        await session.execute(
            select(MediaItem)
            .where(MediaItem.identity_key == key)
            # populate_existing: a second upsert of the same item in one
            # session (a parent arriving between two passes over the same
            # child, in the tests) must not hand back the FIRST call's
            # cached object -- the identity map returns it unrefreshed by
            # default, which is exactly how a just-written parent_id would
            # silently read back as the stale None from before it existed.
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    await upsert_server_ref(session, row.id, item)
    await session.flush()
    return row


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
    # Roadmap row 78. Inside the `draw_text` arm, and appended LAST, for two
    # separate reasons. Inside: the show title obeys the same flag
    # the season text does, inheriting row 39's skip_local_text_add, row 41's
    # suppression and the poster logo swap for free rather than inventing a
    # fourth precedence rule. Last: its offset is computed from the season
    # block's FITTED point size, which only exists once that block has been
    # measured.
    show_title_style = (
        settings.show_title if art_kind == "season_poster" and draw_text else None
    )
    if show_title_style is not None:
        blocks.append((show_title_style, secondary_text))
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
        # Row 78: the show title is drawn one line above the season text, so
        # it is composited at a DERIVED offset AND the season block's own
        # gravity, rather than its configured ones -- the stacking rule owns
        # the position AND the anchor, so the two blocks agree on which edge
        # "above" is measured from. A copy, never a mutation: `settings` is
        # the live config object the holder serves to every other reader.
        draw_style = style
        if style is show_title_style and primary_point_size is not None:
            draw_style = style.model_copy(
                update={
                    "text_offset": stacked_above(settings.text, primary_point_size),
                    "gravity": settings.text.gravity,
                }
            )
        await asyncio.to_thread(
            compositor.run,
            compositor.build_text_argv(
                config.magick_binary, str(working), draw_style, font_path,
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


async def _pick_logo(
    http: httpx.AsyncClient,
    config: Config,
    item: ResolvedItem,
    providers: list,
    tmpdir: Path,
) -> tuple[Path | None, str, int]:
    """This poster's clearlogo, through the shared guard. See
    ``render/artwork_fetch.pick_guarded_logo`` for the three rules and why they
    exist; this is only the render path's half of the call -- the ``ArtRequest``
    a poster asks with, which carries the season/episode numbers and
    ``prefer_clearart`` that a mass-ops row has no equivalent of.

    ``raster_only`` is left at its default: ImageMagick rasterises an SVG
    clearlogo while compositing (``compositor.build_logo_argv``'s
    ``-density 300`` branch), so refusing one here would refuse artwork that
    renders correctly today.
    """
    return await pick_guarded_logo(
        http,
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
        tmpdir,
        native_id=item.native_id,
    )


async def render_artifact(
    session: AsyncSession,
    config: Config,
    http: httpx.AsyncClient,
    item: ResolvedItem,
    art_kind: str,
    providers: list,
    *,
    plex_generated_base=None,
) -> Render:
    """Build one artifact. Idempotent: safe to run repeatedly for the same item.

    ``plex_generated_base`` is the plex-preview fallback's own hook (roadmap
    row 241): an async callable ``(native_id, destination, *, stage) -> str
    | None`` -- ``fetch_plex_generated_base`` bound at app.py's composition
    time via ``functools.partial`` with ``http``, ``plex``, ``base_url`` and
    the ``X-Plex-Token`` header baked in, so this function never sees the
    token. ``None`` (every existing call site, every existing test) means the
    rung simply never runs and ``title_card`` behaves exactly as it does on
    main.
    """
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
        if render.fingerprint == adopted_candidate:
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
        plex_generated = False
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
            base_sha = await asyncio.to_thread(
                _stage_override, override, working, stage=f"the {art_kind} source"
            )
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
            # The plex-preview fallback (roadmap row 241), title_card's own
            # twin of the season_poster rung just above -- and the same seam:
            # after the ladder, before the no_art record. Unreachable when
            # online_fetch_disabled() already returned above (adjudication
            # A4): row 47 scopes itself to "no provider requests", and this
            # reads Plex's own server rather than a provider, but the rung
            # sits after that gate anyway rather than being carved out of it,
            # so a deployment running with fetch disabled sees no change from
            # main. Named here rather than restructuring that early return.
            # ``plex_generated`` itself is initialised above, alongside
            # ``show_fallback``, so the write-back below can read it even on
            # the manual-override branch (which never reaches this block at
            # all) without an UnboundLocalError.
            if (
                selection.candidate is None
                and art_kind == "title_card"
                and plex_generated_base is not None
            ):
                plex_base_sha = await plex_generated_base(
                    item.native_id, working, stage="the title_card source",
                )
                plex_generated = plex_base_sha is not None
            if selection.candidate is None and not plex_generated:
                render.status = "no_art"
                render.detail = f"no {art_kind} art on any provider"
                # L1: a row that rendered plex_generated earlier and now
                # finds no media:// entry (generation turned off, bundle
                # pruned) must stop claiming a fallback it no longer made --
                # cleared the same way the write-back's own elif clears it.
                if render.source_mode == "plex_generated":
                    render.source_mode = "generate"
                await session.commit()
                return render
            if plex_generated:
                # The synthetic, STABLE key -- never the live Plex thumb URL,
                # which carries a cache-busting epoch our own uploadPoster/
                # lockPoster bumps on every pass. Storing and hashing this
                # same string (the write-back
                # below reuses this variable) is what keeps a bumped epoch
                # from moving the fingerprint while regenerated bytes still
                # do, through base_sha alone -- and what keeps
                # config/impact.py's recompute honest, since it reads this
                # same stored column back.
                base_sha = plex_base_sha
                source_url = f"{item.server}://{item.native_id}/title_card"
                provider_name = "plex"
                textless = None
            else:
                candidate = selection.candidate
                chosen_candidate = candidate
                base_sha = await _download(
                    http, candidate.url, working, stage=f"the {art_kind} source"
                )
                source_url = candidate.url
                provider_name = candidate.provider
                textless = candidate.is_textless
                # True exactly when the order preferred textless art, no
                # provider had any, and the ladder took a text-bearing image
                # rather than nothing. The ladder has returned this since it
                # was written and nothing has ever read it.
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
                logo_sha = await asyncio.to_thread(
                    _stage_override, picked_logo, logo_path, stage="the clearlogo"
                )
            elif not online_fetch_disabled(config, art_kind):
                logo_path, logo_sha, skipped_logos = await _pick_logo(
                    http, config, item, providers, Path(tmpdir),
                )
                if logo_path is None:
                    if skipped_logos:
                        # The aggregate line, once per poster: the pod log is
                        # the trusted sink for the item's own identity, and
                        # `_pick_logo` has already logged each refusal. No
                        # URL here either.
                        logger.warning(
                            "no usable clearlogo for %s %r: skipped %d candidate(s) "
                            "over the %dpx ceiling or refused after download; "
                            "rendering the poster without one",
                            item.native_id, item.title, skipped_logos,
                            _ARTWORK_MAX_PIXELS,
                        )
                    if not config.artwork.logo_text_fallback:
                        suppress_text = True
                    else:
                        # The other half of the same decision, which until now
                        # had no variable at all: no logo on any provider AND
                        # logo_text_fallback on, so this poster is wearing its
                        # title text in a logo's place. That is the fact
                        # roadmap 103 calls "logo-to-text fallback taken".
                        # Reached identically whether the ladder had nothing
                        # or everything it had was unusable -- a poster with
                        # no logo is a poster with no logo.
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

        # render_version_for, not config.version (roadmap row 111): this art
        # kind's own settings plus the shared roots, so retuning one kind's
        # text block leaves the other three kinds' fingerprints byte-identical.
        fingerprint = compute_fingerprint(
            render_version_for(art_kind, config), art_kind, source_url, base_sha,
            text_inputs, asset_hashes,
        )
        # target.exists() offloaded (it's a stat() against assets_root, which
        # can be an NFS mount) — only reached once a fingerprint already
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
        else "no title_card art on any provider; used Plex's generated frame instead"
        if plex_generated
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
    # The plex-preview fallback's own twin of the block above -- row 241,
    # cleared the same symmetric way row 132 clears show_fallback.
    if plex_generated:
        render.source_mode = "plex_generated"
    elif render.source_mode == "plex_generated":
        render.source_mode = "generate"
    # A real render just happened, so the adoption no longer describes reality:
    # this row now has a source URL and a fingerprint that covers it.
    render.adopted = False
    # Roadmap row 52: how many bytes the artifact just published occupies.
    # Here rather than inside _publish because this is the write-back -- the
    # one place that owns the row -- and because both publish arms (verbatim
    # and styled) converge on it, so one line covers both. Offloaded like
    # every other stat of assets_root. `target` is the file that was just
    # written; the "unchanged" short-circuit above returns before this point,
    # so a pass that wrote nothing never restamps.
    render.size_bytes = await asyncio.to_thread(_asset_size, target)
    # Database clock, per the global constraint: the app and database clocks drift.
    render.rendered_at = func.now()
    await session.commit()
    return render


def _asset_size(target: Path) -> int | None:
    """``target``'s size in bytes, or ``None`` when it cannot be stat'ed.

    A stat that fails costs the SIZE, never the render: the artifact is on
    disk and uploaded, and a filesystem hiccup at this instant must not turn a
    successful pass into a failed job. The row stays NULL and the scheduled
    ``asset_stats`` pass (scheduler/jobs.py) picks it up on its next run --
    which is the same path every row written before roadmap row 52 takes.

    Synchronous and called from a thread, like every other touch of
    ``assets_root`` here: it can be an NFS mount and a hung one must not stall
    the event loop.
    """
    try:
        return os.stat(target).st_size
    except OSError:
        return None


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


async def absent_servers_for(session: AsyncSession, item_id: int) -> set[str]:
    """Every server whose row for this item says ``absent``, in EITHER table.

    Spec §1: an item whose library a server does not carry "is never resolved
    there, and is never retried". Both tables, because presence stamps both
    and either one saying so is the same fact about the same library.

    One query per ITEM. ``process_item`` reads it once, before it resolves
    anything, and hands the set to ``apply_metadata`` and ``deliver`` rather
    than letting each ask again.
    """
    return set((await session.execute(
        select(MetadataWrite.server)
        .where(MetadataWrite.item_id == item_id, MetadataWrite.status == "absent")
        .union(
            select(RenderDelivery.server)
            .join(Render, Render.id == RenderDelivery.render_id)
            .where(Render.item_id == item_id, RenderDelivery.status == "absent")
        )
    )).scalars())


async def open_catch_up_runs(session: AsyncSession) -> set[int]:
    """The id of every catch-up run that has not finished yet.

    The gate on all three of this module's re-arm doors. A row an IN-FLIGHT
    catch-up armed is already `pending` under that run, so re-arming it is at
    best a no-op -- and `leave_run` on it is not: clearing `run_id` and
    `previous_status` would take the row out of the run while the run is
    still counting it, so the run's progress would under-report and its
    cancel would leave the row `pending` forever with nothing to restore.
    Once the run is FINISHED (or cancelled -- both stamp `finished_at`) the
    row is an ordinary row again and the doors treat it as one.

    Read ONCE per item and handed down, the shape ``absent_servers_for``
    above already takes, and for a sharper version of the same reason
    (controller ruling): this used to be a primary-key SELECT per door hit,
    and nothing ever clears `run_id` off a terminal outcome -- so every row a
    catch-up had touched went on paying that lookup on every later pass, for
    ever. There are at most as many open catch-ups as there are servers, so
    the whole set costs no more than one of those lookups did.
    """
    # The run's id alone, not a `(server, run_id)` pair: `run_id` is already
    # unique, the row that carries it was armed by that run's own server, and
    # `runs.server` is NULLABLE -- so pairing would silently turn the gate off
    # for a catch-up row whose run row has no server, which is a behaviour
    # change and not the cost fix this is.
    return set((await session.execute(
        select(Run.id).where(Run.kind == CATCH_UP_KIND, Run.finished_at.is_(None))
    )).scalars())


async def apply_metadata(
    session: AsyncSession,
    config: Config,
    media_item_id: int,
    item: ResolvedItem,
    server,
    tmdb_facts,
    mdblist,
    tvdb=None,
    imdb_parental=None,
    *,
    servers=None,
    resolved_on: dict[str, ResolvedItem] | None = None,
    absent_servers: set[str] | None = None,
    open_runs: set[int] | None = None,
) -> GatheredFacts:
    """Gather this item's facts, store them, and write the changed ones to
    every configured server that wants them.

    Runs before any badge rendering, because badges read the values off the
    persisted facts rather than the providers.

    Every ``operations`` setting read here is this item's LIBRARY's
    (roadmap row 92): a library that states one uses it, and one that states
    nothing uses the global.

    ``server`` is ``item``'s own server (the identity the gather/persist half
    is keyed on) and is written to exactly as before -- every direct caller
    that predates Jellyfin (roughly two dozen ``mass_ops_*``/``pipeline_facts``
    tests) passes only these nine positionals and gets today's single-server
    write, unchanged.

    ``servers``/``resolved_on`` are the multi-server fan-out: when ``process_item``
    supplies both (the registry and every OTHER server this pass resolved),
    the SAME gathered ``facts`` are additionally written to each of them,
    gated by that server's own ``operations.write_to_<name>`` and exempted by
    that server's own ``item_labels`` -- never ``item``'s. Both default to
    ``None`` so every existing caller above is unaffected.
    """
    # Roadmap row 92. The whole function's `config.operations` reads become
    # this library's, in one statement, at the reads rather than at the
    # caller: a direct caller (a test, a future entry point) then gets the
    # same answer `process_item` does instead of the global one.
    #
    # Identity-returning when this library names no override, so a
    # deployment that never opened the matrix pays one dict lookup per item
    # and allocates nothing. Idempotent, so `process_item` resolving again
    # for its own gate costs the same nothing.
    #
    # `artwork` is carried through by identity by `config_for_library`, so
    # nothing below this line can move a render fingerprint.
    config = config_for_library(config, item.library)
    if not config.operations.enabled:
        return GatheredFacts()

    facts = await gather_facts(
        session, item, tmdb_facts, mdblist, operations=config.operations, tvdb=tvdb
    )
    await persist_facts(session, media_item_id, facts)

    # Row 269. AFTER persist_facts for row 99's reason below: the position is
    # an operator-declared ordering, not a provider fact, and must never
    # reach ``item_facts``. Keyed on the id this function already holds --
    # never on the server's key, which the Jellyfin identity migration
    # replaces. Movies and shows only: lists never hold seasons or
    # episodes, so silence is the truthful report for those.
    if (
        config.operations.sort_title_source == "collections"
        and item.kind in ("movie", "show")
    ):
        facts = with_sort_position(
            facts, await load_sort_position(session, media_item_id)
        )

    # Row 99. Loaded AFTER persist_facts and never before it: ``item_facts``
    # is the PROVIDER's record -- never-overwrite-with-absent, per-field
    # COALESCE, merged ``sources`` provenance -- and writing an operator's
    # value into it would lose the provider's own reading and stop the row
    # tracking the provider at all. The override layers BETWEEN the store and
    # the write, and never into the store.
    #
    # The gate short-circuits ahead of the query, so a deployment that never
    # turns this on pays no read at all.
    overrides: dict[str, object] = {}
    if config.operations.item_overrides_enabled:
        overrides = await load_overrides(session, media_item_id)

    # Row 85. Only fetched when config enables it, so a deployment that never
    # turns this on pays no request and gets today's behaviour exactly.
    parental_categories = None
    if config.operations.parental_labels_enabled:
        parental_categories = await _fetch_parental_categories(imdb_parental, item)

    # Row 87: a verb IS its field's source, so it must fire even when the
    # provider facts are empty -- ``facts.is_empty()`` alone would otherwise
    # skip apply_facts (and every verb with it) on an item no provider has
    # anything to say about. Row 85's fetched categories are the same shape
    # of "the only thing this pass has to write." So is a row-99 override,
    # and for the sharpest version of the reason: the four text fields have
    # NO provider source at all, so a title-only override on an item TMDb has
    # nothing for would never be written without this term.
    has_verbs = bool(config.operations.field_verbs)
    has_parental = parental_categories is not None
    has_overrides = bool(overrides)

    async def _write(
        name: str, target_server, ref_item: ResolvedItem, absent_servers: set[str],
        open_runs: set[int],
    ) -> None:
        # Row 35. Checked here, at the facts/write seam, and not earlier: the
        # facts above are still gathered and persisted for an exempt item,
        # because the badge stage reads the persisted row rather than this
        # write. `ref_item`'s OWN native_id/imdb_id/ref/labels, never
        # `item`'s: a Jellyfin ref is not a Plex one, even for the
        # same media_items row.
        if target_server is None:
            # Nothing is owed to a server this deployment does not have, so
            # there is no row to write -- unlike the toggle below, which IS a
            # deliberate decision about a server that exists (spec §1).
            return
        # `presence.apply_presence` (servers/presence.py) has already
        # stamped `absent` for a library this server does not carry, and
        # spec §1 is explicit that such an item is "never resolved there,
        # and never retried" -- checked before the toggle below, because
        # absent overrides even a write turned on: there is still nothing to
        # write to. Without this, a resolve that finds the item anyway (a
        # cross-library id match, a run that predates this pass's presence
        # refresh) would flip the row back out of `absent` on the next write.
        # `absent_servers` is read ONCE per item, by the caller below, rather
        # than by a SELECT here on every one of this loop's calls.
        if name in absent_servers:
            return
        if not getattr(config.operations, f"write_to_{name}", False):
            await deliveries.record_metadata(
                session, media_item_id, name, "skipped",
                detail=f"config: operations.write_to_{name} is off",
            )
            return
        # Each server's write stands or falls alone (spec §6.1). Without
        # this a Plex `apply_facts` failure aborts before Jellyfin is
        # attempted at all, and a Jellyfin one propagates out of this
        # function into `process_item`'s containment -- whose rollback
        # discards THIS item's `persist_facts` above.
        try:
            exempt = exemption_reason(
                config.operations, ref_item.native_id, ref_item.imdb_id,
                await target_server.item_labels(ref_item.ref),
            )
            if exempt is not None:
                logger.info("%s: skipped writing %s: %s", target_server.name, ref_item.native_id, exempt)
                await deliveries.record_metadata(
                    session, media_item_id, name, "skipped", detail=exempt,
                )
            else:
                await target_server.apply_facts(ref_item.ref, facts, config.operations, parental_categories, overrides)
                await deliveries.record_metadata(session, media_item_id, name, "written")
        except AttributeError:
            # The convention both of `process_item`'s containments follow:
            # a server missing `item_labels` or
            # `apply_facts` is a wiring bug -- a programming error, not the
            # runtime server failure this `except` is for -- and must not be
            # silently contained as a per-server warning.
            raise
        except Exception as exc:
            logger.warning(
                "%s: metadata write failed for %s (%s)",
                target_server.name, ref_item.native_id, deliveries.failure_detail(exc),
            )
            # The whole point of spec §0: a warning in the log and a `done`
            # job left no row anywhere saying the server still lacks this
            # item's metadata. Now it does, and the pass in Phase B drains it.
            #
            # This is `metadata_writes`' ONLY door out of `failed`, so it is
            # also its re-arm: the full pass just tried an exhausted row
            # again, and spec §2 promises such a row the whole budget rather
            # than one retry. Without the reset, `failed` -- itself a counted
            # attempt -- left the row permanently above the cap and the very
            # next failure exhausted it again. Both flags, because this
            # failure IS a real attempt as well as a fresh start; the reset
            # is asked for only when the row was actually exhausted, so an
            # ordinary streak of failures still climbs to the cap.
            #
            # Withheld, both flags, while the row belongs to an OPEN catch-up
            # (spec §3): that run is counting this row and its cancel has to
            # be able to restore it, so the ordinary pass neither resets its
            # budget nor takes it out of the run. The failure is still
            # recorded -- this is the only place that records one -- it is
            # only the RE-ARM that waits for the run to finish.
            #
            # And with it the HORIZON: `keep_next_attempt` leaves a row inside
            # an open run on the `next_attempt_at` that run gave it, because
            # the run's drain owns its timing (spec §3) and pushing it six
            # hours out from here would stall a row the run is still counting.
            existing = (await session.execute(
                select(MetadataWrite.status, MetadataWrite.run_id).where(
                    MetadataWrite.item_id == media_item_id, MetadataWrite.server == name,
                )
            )).one_or_none()
            in_open_run = (
                existing is not None and existing.run_id in open_runs
            )
            re_armed = (
                existing is not None and existing.status == "failed" and not in_open_run
            )
            await deliveries.record_metadata(
                session, media_item_id, name, "pending",
                detail=deliveries.failure_detail(exc), retry_in=deliveries.RETRY_SECONDS,
                reset_attempts=re_armed, leave_run=re_armed,
                keep_next_attempt=in_open_run,
            )

    if not facts.is_empty() or has_verbs or has_parental or has_overrides:
        # One query per ITEM, not one per server per item: every `_write`
        # call below shares this same set rather than each asking its own
        # SELECT (review M1). `process_item` reads the same set BEFORE it
        # resolves -- it skips those servers outright -- and passes it in, so
        # the query below is the direct caller's, not a second copy of it.
        if absent_servers is None:
            absent_servers = await absent_servers_for(session, media_item_id)
        # The failure door below reads this set, and it is read here for the
        # same reason and on the same terms: once per ITEM, by the direct
        # caller when there is one, never once per server per item.
        if open_runs is None:
            open_runs = await open_catch_up_runs(session)
        await _write(item.server, server, item, absent_servers, open_runs)
        if servers is not None and resolved_on is not None:
            for name, resolved_item in resolved_on.items():
                if name == item.server:
                    continue  # already written just above, via `server`
                await _write(
                    name, servers.get(name), resolved_item, absent_servers, open_runs,
                )

    # Row 269. A released sort position is acted on ONCE: the clear above
    # was sent -- or the item is exempt, row 99's own ruling for its DELETE
    # endpoint (the row goes, the write does not), or the server already reports
    # the field unlocked and the writer had nothing to send. Under apply-off
    # the write was only reported, so the tombstone stays for the pass that
    # arms it. Its own commit: nothing else in this function commits, and
    # the tombstone must not outlive the write it authorised.
    if facts.sort_title == "" and config.operations.sort_title_apply:
        await delete_sort_position(session, media_item_id)
        await session.commit()
    return facts


async def _already_delivered(session, config, server, name, ref, render, fingerprint) -> bool:
    """Whether ``server`` is already serving exactly the badged image we
    would upload (the server-neutral successor to the old, Plex-only
    ``_already_in_plex``).

    Asked per server, inside ``deliver``, rather than once globally: each
    server gets its own EXIF-provenance answer, so adopting from Plex never
    tells another server it already has bytes it has never seen.

    The answer comes from the artwork itself -- uploaded images carry their
    fingerprint in EXIF ``ImageDescription`` (see ``plex/exif.py``) -- which
    makes the server, not our database, the source of truth about what it is
    serving. That is what makes adoption cheap: at cutover the whole library
    has no ``badge_fingerprint``, and without this every item would be
    re-uploaded to produce bytes already there.

    Only worth asking when THIS server has no delivery history for this
    render at all -- the per-server generalization of the old single-server
    check (``render.badge_fingerprint is not None`` skipped the probe): a
    server this render has already been delivered to answers the question
    for free from its own last recorded outcome, and asking again would be a
    request per item, every ordinary re-badge. "Delivered" means a row this
    code actually wrote -- `attempted_at IS NOT NULL`: the migration that
    introduced the table backfills a `plex` row for EVERY pre-existing
    render, with no `attempted_at`, so without that term the probe could never run
    again for a single row already in the database -- exactly the population
    (`badge_fingerprint IS NULL`) adoption exists for. `render.badge_fingerprint`
    itself cannot carry this any more -- `compose_badged_bytes` overwrites it
    with the NEW fingerprint once a compose actually succeeds, once for every
    server, so a per-server delivery-row check is what a per-render column
    used to be. A server added to an already-badged library's config still
    gets its own adoption check, having no history of its own yet, even
    though Plex (say) does.

    ``fingerprint`` is passed explicitly rather than read off
    ``render.badge_fingerprint``: the single-server
    adoption shortcut in ``compose_badged_bytes`` asks this BEFORE compose
    has run at all, and writing the new fingerprint to the column ahead of a
    successful compose would commit it even when ``compose_badges`` then
    raises -- stranding the render on a fingerprint no image on disk (or on
    any server) actually matches, and the next pass's unchanged-check would
    skip it forever. Passing the value directly means the column is
    touched only where it always was: after compose (or here, after a
    genuine adoption match) actually succeeds.

    Best-effort by construction otherwise. Anything at all going wrong -- the
    capability absent, a transport error, a stranger's EXIF -- answers False,
    and the caller does the normal upload. A wrong False costs one redundant
    upload; there is no wrong True, because the fingerprint read back has to
    equal the one just composed.

    Deliberately not asked when ``adopt_from_plex`` is off: with nothing to
    skip there is nothing to save, and a dry run should not spend a
    provenance request per item learning that. The caller has already
    confirmed ``upload_to_<name>`` is on before reaching here.
    """
    if not config.badges.adopt_from_plex:
        return False
    if CAP_ARTWORK_PROVENANCE not in server.capabilities:
        return False
    existing = (
        await session.execute(
            select(RenderDelivery.id).where(
                RenderDelivery.render_id == render.id, RenderDelivery.server == name,
                RenderDelivery.attempted_at.isnot(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    try:
        recorded = await server.artwork_provenance(ref, render.art_kind)
    except Exception:
        logger.debug("could not read artwork provenance from %s", server.name, exc_info=True)
        return False
    return recorded is not None and recorded == fingerprint


async def compose_badged_bytes(
    session: AsyncSession,
    config: Config,
    render: Render,
    media_item: MediaItem,
    *,
    http=None,
    mdblist=None,
    server=None,
    ref: ServerItemRef | None = None,
    facts=None,
    solo_delivery: tuple[str, object, ServerItemRef] | None = None,
    force: bool = False,
    out: dict | None = None,
) -> bytes | None:
    """Compose one render's badged bytes, or ``None`` when there is nothing to
    compose -- badges off for this library, a background (never badged), a
    render that never produced a base image, or a fingerprint that has not
    moved since the last successful delivery (spec §5: "render once").

    ``out``, when given, has ``out["fingerprint"]`` set to the fingerprint
    the returned bytes were actually composed under. Only the ``force=True``
    caller needs it: a forced compose deliberately does NOT write
    ``render.badge_fingerprint`` (see the comment on that branch below), so
    it is the one caller for which the render's stored fingerprint is by
    construction not the one it is holding -- and recording the stored one
    against those bytes is what would make a server holding different
    artwork read as up to date to the catch-up.

    The COMPOSE half of the old, single-server ``apply_badges``: it lives
    here, once per render; ``deliver`` (below) fans the
    same bytes out to every configured server. ``retry_pending_deliveries``
    (``deliveries.py``) calls this too, passing the IDENTITY server and its
    ref -- it re-resolves Plex for the media info and native ratings the
    badge and the fingerprint need -- but both stay optional, because that
    pass has neither when the item has no Plex ref.

    ``server``/``ref`` are the ONE identity live media info and native
    ratings are sampled from -- still Plex-only (``media_info_from_plex``,
    ``plex_native_ratings`` and the overlay view all read plexapi attributes
    no other ``MediaServer`` exposes yet, spec §5.1). ``process_item`` passes
    Plex's own resolved ref when this pass resolved one; a Jellyfin-only pass,
    or a caller with neither, degrades to an empty ``MediaInfo`` and no
    Plex-native ratings rather than raising -- the persisted ``ItemFacts`` row
    this function loads itself still drives the badge.

    ``facts`` defaults to ``None``, meaning "load the persisted ``ItemFacts``
    row" -- exactly what ``process_item``'s old badge block did before
    calling ``apply_badges``, and what ``retry_pending_deliveries`` (with no
    gathered facts of its own to hand in) relies on. A caller that already
    holds a ``GatheredFacts``-shaped object (``tests/test_badge_pipeline.py``,
    unit-testing compose in isolation from the database) may pass one
    directly instead.

    ``http``/``mdblist`` are unchanged from the old ``apply_badges``; see the
    module's other docstrings for what each is for.

    ``solo_delivery`` is ``(name, server, ref)`` for the
    ONE configured server that is both resolved and upload-enabled this
    pass, when there is exactly one -- ``process_item`` computes and passes
    it; ``None`` (the multi-server case, or a caller like
    ``retry_pending_deliveries`` that never supplies it) skips this
    entirely, and ``deliver`` runs its own per-server ``_already_delivered``
    check after composing instead. When set, adoption is checked BEFORE the
    per-definition resolution and ``compose_badges`` itself -- the old
    single-server ordering -- so a cutover (a whole library with no
    ``badge_fingerprint`` yet, every render already carrying its own EXIF
    fingerprint) does not recompose bytes already sitting on the one server
    that matters. On a match, this function records the delivery itself
    (there is no ``data`` for ``deliver`` to act on) and returns ``None``;
    ``deliver`` still runs afterward, sees the row already there, and leaves
    it alone (its own ``data is None`` catch-up only acts on a MISSING,
    ``pending`` or ``failed`` row).

    ``force`` skips the unchanged-fingerprint gate
    alone -- never the three "not a badge candidate" checks above it.
    ``retry_pending_deliveries`` is its only caller: a due row means one
    server does not have these bytes, so "nothing has changed since the last
    delivery" is true of every OTHER server and no answer at all for that
    one. See the gate's own comment for the contract this half of. It also
    leaves ``render.badge_fingerprint`` alone: a
    forced compose delivers, it does not decide -- see the write itself.
    """
    # Roadmap row 92, and it must be ABOVE the `badges.enabled` gate: see the
    # old `apply_badges`' own note, still true here.
    config = config_for_library(config, media_item.library)
    if not config.badges.enabled:
        return None
    # Backgrounds are never badged. The tool being replaced overlays posters,
    # season posters and episode title cards only -- a fanart backdrop with a
    # runtime badge stamped on it is not something it produces, and not
    # something we should start producing.
    if render.art_kind == "background":
        return None
    # `asset_path` is written when the row is created, before any file exists,
    # so a render that never produced one -- no_art, truncated, skipped,
    # failed -- would send Image.open() at a path that is not there.
    if render.status != "rendered":
        return None

    # Still Plex-only below this point (media_info_from_plex, the ratings and
    # the overlay view read plexapi attributes no MediaServer method exposes
    # yet). `server`/`ref` absent -- no Plex this pass, or a caller with
    # neither -- degrades rather than raising: an empty MediaInfo and no
    # Plex-native ratings, badging still runs off the persisted facts below.
    plex_item = await server.fetch_item(ref.native_id) if server is not None and ref is not None else None

    # media_info_from_plex() calls item.reload() when `.media` is absent, which
    # is a blocking `requests` GET -- and plexapi Show and Season objects never
    # carry `.media`, so that is not a rare path. On the event loop it stalls
    # the liveness probe and every other worker.
    media = (
        await asyncio.to_thread(media_info_from_plex, plex_item) if plex_item is not None
        else MediaInfo(
            video_resolutions=(), audio_track_titles=(), audio_channels=None,
            duration_ms=None, audio_languages=(), hdr_flags=frozenset(),
            season_number=media_item.season_number, episode_number=media_item.episode_number,
        )
    )

    # The persisted row by default: neither this function's own caller nor
    # retry_pending_deliveries' holds a fresher GatheredFacts than the
    # database has. `facts is not None` lets a direct unit test hand one in.
    if facts is None:
        facts = (
            await session.execute(select(ItemFacts).where(ItemFacts.item_id == media_item.id))
        ).scalar_one_or_none() or GatheredFacts()

    # Row 99, at the single seam that reads facts for a badge. See the
    # old `apply_badges`' own note on why this must be here and not in
    # `apply_metadata`. Skipped when there is no live `ref` to check labels
    # against -- the same degrade as the media read above.
    if config.operations.item_overrides_enabled and ref is not None:
        exempt = exemption_reason(
            config.operations, ref.native_id, media_item.imdb_id,
            getattr(plex_item, "labels", None),
        )
        if exempt is None:
            facts = overlaid_badge_facts(
                facts, await load_overrides(session, media_item.id)
            )

    critic_rating = getattr(facts, "critic_rating", None)
    audience_rating = getattr(facts, "audience_rating", None)
    ratings: dict[str, float | None] = dict(plex_native_ratings(plex_item)) if plex_item is not None else {}
    # The imdb_rating/tmdb_rating aliases (roadmap row 100, sub-phase C2a):
    # this service's IMDb/TMDb facts ARE Kometa's imdb_rating/tmdb_rating
    # rating-source values (probe bucket (a)) -- no new fetch, just making
    # the <<imdb_rating>>/<<tmdb_rating>> SPELLINGS resolve too, alongside
    # the pre-existing <<critic_rating>>/<<audience_rating>> ones.
    if critic_rating is not None:
        ratings["imdb_rating"] = critic_rating
    if audience_rating is not None:
        ratings["tmdb_rating"] = audience_rating
    # MDBList's eleven mdb_* ratings, per-pass, never persisted (adjudication
    # A7). `media_item.kind in ("movie", "show")` mirrors `facts/gather.py::
    # gather_facts`'s own gate for `mdblist.content_rating` exactly -- season
    # and episode badges do not carry mdb_* ratings in this slice. A
    # transient MDBList failure must degrade this one set of values, not the
    # whole badge stage: same two `except` clauses `gather_facts` already
    # uses for the same client.
    if mdblist is not None and media_item.kind in ("movie", "show"):
        try:
            ratings.update(await mdblist.ratings(
                tmdb_id=media_item.tmdb_id, tvdb_id=media_item.tvdb_id,
                is_movie=media_item.kind == "movie",
            ))
        except MDBListLimitReached:
            logger.warning("mdblist daily limit reached; skipping mdb_* overlay ratings")
        except httpx.HTTPError as exc:
            logger.warning("mdblist request failed; skipping mdb_* overlay ratings: %s", exc)
    inputs = BadgeInputs(
        media=media,
        critic_rating=critic_rating,
        audience_rating=audience_rating,
        content_rating=getattr(facts, "content_rating", None),
        video_format=video_format_text(media),
        ratings=ratings,
    )
    values = badge_values(render.art_kind, inputs)

    # The SELECTION half. Evaluated BEFORE the
    # fingerprint gate on purpose: `badge_fingerprint` folds this item's own
    # match outcomes in beside the definitions digest, so an item whose
    # resolution (or, in a later slice, aspect or language count) changed
    # re-renders. If the outcomes were computed after the gate, the gate
    # could never see them and a changed item would keep its old badge
    # forever. `all_definitions()`, not `.definitions`: a
    # named family's definitions are drawn too, and they must be in the list
    # the fingerprint hashes as well as in the list that gets selected over --
    # enabling a family has to re-badge exactly like adding a definition by
    # hand does.
    definitions = config.badges.all_definitions()
    view = OverlayItemView(media, facts=facts, plex_item=plex_item)
    matched_definitions, outcomes = select_overlay_definitions(definitions, view)

    # render.fingerprint, not render.base_sha256: the badged image is composed
    # from the *base we rendered*, so the gate has to track what went into that
    # base -- see badge_fingerprint's docstring. `definitions` is the WHOLE
    # configured list, not the matched subset: editing or removing a
    # definition this item never matched must still move the digest, or a
    # config change goes unnoticed for every item it does not currently
    # apply to.
    fingerprint = badge_fingerprint(
        render.fingerprint or "", render.art_kind, values, manifest_sha(),
        definitions, outcomes, ratings=ratings,
    )
    # The fingerprint alone, and deliberately NOT `render.upload_status`
    # too: that column is now the cross-server roll-up, whose
    # precedence puts `pending` above `uploaded`, so one server that has not
    # scanned the item yet -- the normal steady state of a newly added
    # Jellyfin over a large library -- would keep this gate from ever firing
    # and recompose every render, every pass, then re-upload it to the
    # servers that already had the bytes. "Render once" is the fingerprint's
    # question.
    #
    # Which servers still need those bytes is `deliver`'s own per-server
    # bookkeeping, and the contract between the two is this: on an
    # unchanged fingerprint `deliver` composes nothing
    # and uploads nothing itself, but re-arms every server whose row is
    # missing, `pending` or `failed` as `pending` with no delay, leaving
    # `uploaded` and `skipped` alone. The next `retry_pending_deliveries`
    # pass is then what actually delivers to that one server -- which is
    # why it, and only it, passes `force`: it is asking for bytes for a
    # server that does not have them, so an unchanged fingerprint is no
    # reason to answer `None`. A failed upload is therefore retried once
    # per full pass, exactly as the single-server code retried it.
    if not force and fingerprint == render.badge_fingerprint:
        return None

    # The single-server adoption shortcut, checked BEFORE any image work --
    # see this function's own docstring on `solo_delivery`.
    # The just-computed `fingerprint` is passed to
    # `_already_delivered` as an argument rather than written to
    # `render.badge_fingerprint` first -- writing the column ahead of a
    # successful compose would commit a fingerprint no actual image matches
    # if `compose_badges` below then raised, stranding the render on a
    # stale badge the next pass's unchanged-check would skip forever. The
    # column is set only on an actual adoption match (this IS the
    # successful outcome) or, on the normal path, after compose succeeds.
    if solo_delivery is not None:
        solo_name, solo_server, solo_ref = solo_delivery
        if await _already_delivered(
            session, config, solo_server, solo_name, solo_ref, render, fingerprint,
        ):
            render.badge_fingerprint = fingerprint
            await deliveries.record(session, render.id, solo_name, "uploaded", fingerprint=fingerprint)
            # `deliver` rolls up and commits only what IT
            # recorded, and this shortcut's `uploaded` is recorded here,
            # before `deliver` ever sees the render. So the roll-up and the
            # commit belong beside it -- and an adoption match IS the
            # successful outcome, the same thing `deliver`'s own commit
            # protects for an upload that already reached the server.
            await deliveries.rollup(session, render.id)
            await session.commit()
            return None

    resolved_images: dict[str, Path] = {}
    usable_definitions = []
    for definition in matched_definitions:
        try:
            path = await resolve_image_path(
                definition,
                overlays_root=config.overlays_root,
                http=http,
                max_bytes=config.badges.definition_image_max_bytes,
            )
        except OverlaySourceError as exc:
            # Class name and a fixed sentence: the message may carry an
            # operator-typed path. "skipping it" means the definition itself
            # -- it must not reach compose() at all, or a `back_color` alone
            # stamps a visible empty backdrop where the image should be.
            logger.warning(
                "overlay %r has no usable image (%s); skipping it",
                definition.name, type(exc).__name__,
            )
            continue
        if path is not None:
            resolved_images[definition.name] = path
        usable_definitions.append(definition)

    data = await asyncio.to_thread(
        compose_badges, Path(render.asset_path), render.art_kind, inputs, fingerprint,
        definitions=usable_definitions, resolved_images=resolved_images,
        fonts_root=config.fonts_root,
    )
    if not force:
        # A forced compose is a compose FOR
        # DELIVERY -- one server is owed bytes the others already have -- and
        # the column belongs to the pass that decided what this render should
        # look like. Writing it here would hand the next full pass a
        # fingerprint it did not compute, so it would recompose and re-upload
        # to every server to get back to the one it did.
        render.badge_fingerprint = fingerprint
        await session.flush()
    if out is not None:
        out["fingerprint"] = fingerprint
    return data


async def deliver(
    session: AsyncSession,
    config: Config,
    render: Render,
    media_item: MediaItem,
    servers,
    refs: dict[str, ServerItemRef],
    data: bytes | None,
    *,
    misses: dict[str, Exception] | None = None,
    absent_servers: set[str] | None = None,
    open_runs: set[int] | None = None,
) -> None:
    """Fan ``data`` -- ``compose_badged_bytes``'s own output -- out to every
    configured server, and record what happened to each (spec §5.2/§5.3).

    ``deliveries.record`` is the only writer of per-server status;
    this function's whole job is deciding, for each of ``servers``, which of
    its four outcomes applies:

    * this library's ``badges.upload_to_<name>`` is off -- ``skipped``, and
      only the first time that server is considered; resolved or missed, it
      is asked for nothing, so no miss of its own is worth a row.
    * not resolved this pass, and the miss was ``ItemNotFound`` (not a
      ``PathMismatch``) -- ``pending``, retried by the next
      ``retry_pending_deliveries`` pass (no cap).
    * not resolved this pass, and the miss WAS a ``PathMismatch`` -- ``failed``
      with a fixed sentence; no retry fixes a mount mismatch.
    * resolved and on, and the server already reports this exact fingerprint's
      provenance (``_already_delivered``, ``adopt_from_plex``) -- ``uploaded``
      without a redundant upload.
    * resolved and on, otherwise -- upload; ``uploaded`` on success, ``failed``
      with ``deliveries.failure_detail(exc)`` on an upload exception.

    ``data is None`` (``compose_badged_bytes`` had nothing new to send -- the
    render is not a badge candidate at all, or its fingerprint has not moved)
    composes and uploads nothing here, but it is not silent: a resolved
    server whose row is missing, ``pending`` or ``failed`` is re-armed
    ``pending`` with no delay, so the next ``retry_pending_deliveries`` pass
    delivers to that one server (see the branch's own comment).
    ``uploaded`` and ``skipped`` rows are left exactly as they
    are. A server that MISSED this pass still gets its ``pending``/``failed``
    row regardless -- that outcome depends only on resolution, never on
    whether new bytes exist.

    A background render, a render that never produced a base image, or a
    library with badges off entirely is a no-op here TOO -- the exact same
    three checks ``compose_badged_bytes`` itself opens with. Repeated
    deliberately rather than folded into one shared gate: ``process_item``
    calls this function unconditionally, once per render -- the same per-
    render shape it used to call the single ``apply_badges`` in -- so a test
    (or a future caller) that monkeypatches either half still observes it
    running, regardless of that render's own status, rather than being
    silently skipped by a guard at the call site.

    ``deliveries.rollup`` is called once at the end, and only when this call
    actually recorded something. It recomputes off whatever
    rows already exist, so on a pass that recorded nothing it would rewrite
    the identical status -- an UPDATE and a COMMIT per rendered render per
    pass, including for every item where nothing changed at all, which the
    old ``apply_badges`` returned from without a single statement.

    Commits before returning -- the old ``apply_badges``' own discipline for
    a successful upload ("a flushed-but-uncommitted fingerprint for an
    upload that already reached [the server] would be discarded,
    re-uploading the identical image next pass"), generalized to every
    server and every outcome this function itself decided: `process_item`'s
    badge loop shares one transaction across every render, and a LATER
    render's own failure rolls back only what is still uncommitted, never an
    earlier render's already-delivered servers.
    """
    library_config = config_for_library(config, media_item.library)
    if (
        not library_config.badges.enabled
        or render.art_kind == "background"
        or render.status != "rendered"
    ):
        return
    misses = misses or {}
    absent = absent_servers or set()
    # Both re-arm doors below read this. Read here, after the three cheap
    # gates above, when the caller has none to hand down: once per RENDER at
    # worst, never once per row -- `process_item` reads it once per item and
    # passes it in, which is the path every production call takes.
    if open_runs is None:
        open_runs = await open_catch_up_runs(session)
    # Whether this call wrote a single delivery row. The
    # rollup and the commit below hang off it.
    recorded = False
    for name, server in servers.items():
        if name in absent:
            # Spec §1: a server that does not carry this item's library is
            # owed nothing -- no row, no roll-up, no commit. `deliveries.record`
            # already refuses to write over an `absent` row, so this is not
            # what keeps the row right; it is what stops a pass spending a
            # SELECT and a rewritten roll-up per item per pass on a server
            # that has nothing to do with it. `process_item` resolves none of
            # these either, so `refs` never carries one.
            continue
        ref = refs.get(name)
        # The per-library toggle is evaluated BEFORE the
        # catch-up below -- an upload-disabled server must never get a
        # `pending` catch-up row, only for the very next retry pass to
        # immediately overwrite it with `skipped`. When there is genuinely
        # nothing new this pass (`data is None`) an upload-disabled server
        # gets no row at all, not even `skipped`.
        #
        # Hoisted ABOVE the resolved/missed split: a server nothing is
        # being uploaded to is asked nothing, so its own miss is not worth
        # a `pending` row either --
        # and `upload_to_jellyfin` defaults to off, so a dual deployment's
        # very first pass is exactly that case, for every item Jellyfin has
        # not scanned yet.
        if not getattr(library_config.badges, f"upload_to_{name}", False):
            if data is not None:
                # `skipped` is stamped only the FIRST time a server
                # is considered -- an already-recorded outcome (most of
                # all `uploaded`) must not be rewritten just because the
                # toggle is off this particular pass.
                already_recorded = (
                    await session.execute(
                        select(RenderDelivery.id).where(
                            RenderDelivery.render_id == render.id,
                            RenderDelivery.server == name,
                        )
                    )
                ).scalar_one_or_none()
                if already_recorded is None:
                    await deliveries.record(session, render.id, name, "skipped")
                    recorded = True
            continue
        if ref is not None:
            if data is None:
                # An unchanged fingerprint (nothing new
                # composed) must never overwrite an already-`uploaded` row
                # with `skipped`. A server with no row yet, or one still
                # `pending`, gets a fresh `pending` row with NO delay
                # (`retry_in=0`), so the very next `retry_pending_deliveries`
                # pass delivers it -- the catch-up a server whose
                # `upload_to_<name>` was only just turned ON, or one newly
                # added to the config, needs for a render whose fingerprint
                # had already settled.
                #
                # A `failed` row is re-armed the same way. The
                # single-server code retried a failed upload on
                # every full pass, because its gate also required
                # `upload_status == "uploaded"`; with the gate on the
                # fingerprint alone, nothing would ever retry one again,
                # and `retry_pending_deliveries` only selects `pending`. So
                # the retry stays once per full pass, and it costs no
                # ImageMagick work for the servers that already have the
                # bytes. `uploaded` and `skipped` are still left alone.
                existing = (
                    await session.execute(
                        select(RenderDelivery.status, RenderDelivery.run_id).where(
                            RenderDelivery.render_id == render.id,
                            RenderDelivery.server == name,
                        )
                    )
                ).one_or_none()
                existing_status = existing.status if existing is not None else None
                if existing_status in (None, "pending", "failed"):
                    if existing is not None and existing.run_id in open_runs:
                        # A row an in-flight catch-up armed is already pending
                        # under that run (spec §3). Left alone entirely:
                        # re-arming it would take it out of a run that is still
                        # counting it and still able to cancel it.
                        #
                        # INSIDE this branch, not above it: nothing clears
                        # `run_id` on a terminal outcome, so an `uploaded` row
                        # a catch-up once touched carries its run id for ever
                        # -- and a row that could not be re-armed anyway has
                        # no business consulting the run that armed it.
                        continue
                    # A re-arm, not an attempt: this pass composed no new
                    # bytes for this server, so nothing was actually tried
                    # against it -- only the retry pass below actually
                    # delivers, and that is where the budget is spent.
                    #
                    # `reset_attempts`, because a re-arm is a fresh START and
                    # not a continuation: `failed` is itself a counted
                    # attempt, so an exhausted row left alone here came back
                    # already over the budget and the very next failure
                    # exhausted it again. Spec 2 promises the full pass and
                    # the catch-up re-arm such a row; that is only true if
                    # the counter goes back to zero with it.
                    # `leave_run`: a row the ORDINARY pipeline re-arms has
                    # left the catch-up that armed it, so its run scope goes
                    # back to NULL -- otherwise a later progress query or
                    # cancel acts on rows that run no longer owns.
                    await deliveries.record(
                        session, render.id, name, "pending", retry_in=0,
                        count_attempt=False, reset_attempts=True, leave_run=True,
                    )
                    recorded = True
                continue
            if await _already_delivered(
                session, library_config, server, name, ref, render, render.badge_fingerprint,
            ):
                await deliveries.record(session, render.id, name, "uploaded", fingerprint=render.badge_fingerprint)
                recorded = True
                continue
            lock = library_config.badges.lock_artwork and CAP_LOCK_ARTWORK in server.capabilities
            try:
                await server.upload_artwork(ref, data, render.art_kind, lock)
            except Exception as exc:
                logger.warning("badge upload to %s failed for %s", name, ref.native_id, exc_info=True)
                # Counted, on a row nothing retries: `failed` is terminal
                # until a re-arm, and `retry_pending_deliveries` selects
                # `pending` only. Harmless because BOTH re-arm doors below
                # reset the counter, so the climb never reaches the row that
                # gets its budget back -- said here rather than left for the
                # next reader to re-derive.
                await deliveries.record(
                    session, render.id, name, "failed", detail=deliveries.failure_detail(exc),
                )
            else:
                await deliveries.record(session, render.id, name, "uploaded", fingerprint=render.badge_fingerprint)
            recorded = True
        else:
            exc = misses.get(name)
            if exc is None:
                continue
            if isinstance(exc, PathMismatch):
                # Counted on a terminal row, like the upload failure above,
                # and harmless for the same reason.
                await deliveries.record(
                    # The same `failure_detail` every other
                    # detail goes through -- category and class name, never
                    # the message, which names a filesystem path.
                    session, render.id, name, "failed",
                    detail=deliveries.failure_detail(exc),
                )
                recorded = True
                continue
            # A row that is ALREADY `pending` with a horizon keeps it.
            # Re-recording it every full pass pushed
            # `next_attempt_at` forward by RETRY_SECONDS (6 h) each time, and
            # the measured full pass is ~3.5 h -- so the row the horizon was
            # written for, a server that has not scanned this item for many
            # passes, could never mature and the retry pass never saw it.
            existing = (
                await session.execute(
                    select(
                        RenderDelivery.status, RenderDelivery.next_attempt_at,
                        RenderDelivery.run_id,
                    ).where(
                        RenderDelivery.render_id == render.id,
                        RenderDelivery.server == name,
                    )
                )
            ).one_or_none()
            if existing is not None and existing.status == "pending" and existing.next_attempt_at is not None:
                continue
            if existing is not None and existing.run_id in open_runs:
                # The same rule the `data is None` door above states: a row an
                # in-flight catch-up owns is left to that run, `run_id` and
                # `previous_status` intact, whatever status it has reached.
                continue
            # A `failed` row falls through to here too, and is a re-arm the
            # same way the `data is None` branch above is: `reset_attempts`,
            # so a row this pass could not even resolve does not stay stuck
            # over budget from an earlier, unrelated delivery failure.
            await deliveries.record(
                session, render.id, name, "pending", retry_in=deliveries.RETRY_SECONDS,
                count_attempt=False, reset_attempts=True, leave_run=True,
            )
            recorded = True
    if not recorded:
        # Nothing was written for this render, so the
        # roll-up would recompute the status it already has. Skipping it (and
        # the commit) is what keeps an unchanged full pass free of an
        # UPDATE + COMMIT per render, as the old `apply_badges` was.
        #
        # The commit is still owed when a compose
        # actually happened. `compose_badged_bytes` set and flushed
        # `render.badge_fingerprint` before handing the bytes over, and the
        # badge stage's own `except Exception: await session.rollback()`
        # would otherwise discard a compose that finished -- costing the next
        # pass a re-render of bytes this one already produced.
        if data is not None:
            await session.commit()
        return
    await deliveries.rollup(session, render.id)
    await session.commit()


async def _persist_identity(session, item, resolved_on) -> MediaItem:
    """The item's ``media_items`` row and EVERY resolved server's ref row.

    One intent means one ``media_items`` row and one
    ``media_item_server_refs`` row per server that resolved it (spec
    §4.1/§5.1). ``_upsert_media_item`` writes the identity server's own ref
    and no other, so the two halves belong together everywhere the item is
    (re-)established. The metadata containment and the refusal handler both
    ``rollback()`` while this work is still uncommitted, and each then
    re-establishes the item: re-upserting it ALONE would silently drop every
    non-primary server's ref, permanently for a season or an episode (one
    art kind, so any ``SourceRefused`` is a first-kind refusal) and until a
    clean pass for everything else.

    Neither call commits, here as before: ``render_artifact`` still owns the
    transaction boundary this sits inside.
    """
    media_item = await _upsert_media_item(session, item)
    for other in resolved_on.values():
        if other is not item:
            await upsert_server_ref(session, media_item.id, other)
    return media_item


async def process_item(
    session: AsyncSession,
    config: Config,
    http: httpx.AsyncClient,
    servers,
    providers: list,
    intent: RenderIntent,
    tmdb_facts=None,
    mdblist=None,
    imdb_parental=None,
    plex_generated_base=None,
) -> list[Render]:
    """Resolve one intent on EVERY configured server, render once, and
    deliver per server (spec §5.1).

    ``servers`` is the ``Servers`` registry, not one server: every configured
    server is asked to resolve, independently, and a miss on one never blocks
    the others -- only when NONE resolves does the old ``ItemNotFound``/
    ``PathMismatch`` ladder fire, so the worker defers or parks exactly as
    before. The FIRST success (Plex when present) is the identity the
    ``media_items`` row keys on; every other success contributes only a ref.

    ``tmdb_facts``/``mdblist`` are the metadata-operations clients; they are
    optional (and default to ``None``) so callers that only care about
    artwork — including every test that predates metadata operations — keep working
    unchanged. Metadata operations run whenever ``tmdb_facts`` is supplied.
    ``mdblist`` no longer gates them: production wiring always supplies a
    client, real or a stand-in when no API key is configured (see
    ``app._build_mdblist``), so an unset key degrades only the content
    rating rather than every metadata operation.

    ``artwork_probe`` is gone: ``_already_delivered`` reads
    provenance straight off each resolved server inside ``deliver``, so there
    is no longer a single seam for a caller to wire up or skip.

    ``imdb_parental`` is row 85's ``IMDbParentalGuideClient``; ``None`` --
    what every direct caller and most tests pass -- means the fetch in
    ``apply_metadata`` is skipped, whatever ``operations.parental_labels_enabled``
    says (the same shape ``tvdb=None`` already has for row 84).

    A failure anywhere in this step is caught and logged rather than
    propagated — a ratings-provider hiccup must not cost the item its
    poster and background, which the artifact loop below still owes it.

    ``plex_generated_base`` is passed straight to ``render_artifact``; see
    its own docstring for what it does and why it is optional.
    """
    # Resolve on every configured server (spec §5.1). The first success is the
    # identity the render keys on; every success is a ref; a miss is a pending
    # delivery, never a held job. Only when NO server resolves does the old
    # ItemNotFound/PathMismatch ladder fire, so the worker defers or parks
    # exactly as before.
    # Spec §1, read ONCE and before any resolve: a server whose row for this
    # item is `absent` does not carry its library, so it is asked nothing --
    # not resolved, not delivered to, not written to. The guards in
    # `apply_metadata` and `deliver` keep those ROWS right, but the pass still
    # spent one resolve request per absent server per item, every pass.
    #
    # Keyed off the refs the intent already carries, because the item's own
    # row cannot be found before it resolves: the full pass, reprocess and
    # discovery all build their intents from a `media_items` row (see
    # `RenderIntent.refs`), and those are the passes whose cost this is. A
    # webhook intent carries none; its item's set is read below, once the
    # identity is established, exactly as before.
    known_item_id = None
    for name, native_id in intent.refs.items():
        known_item_id = await item_id_for(session, name, native_id)
        if known_item_id is not None:
            break
    absent_servers = await absent_servers_for(session, known_item_id) if known_item_id else set()

    resolved_on: dict[str, ResolvedItem] = {}
    misses: dict[str, Exception] = {}
    for name, server in servers.items():
        if name in absent_servers:
            continue
        try:
            resolved_on[name] = await server.resolve(intent)
        except ItemNotFound as exc:      # PathMismatch included
            misses[name] = exc
        except Exception as exc:
            # A transport error (or any other failure) from
            # ONE server's resolve must not abort the item for every other
            # server (spec §6.1) -- logged, never a URL, and treated exactly
            # like an ItemNotFound miss for delivery purposes: this server
            # gets a `pending` row (`deliver`'s own misses handling), retried
            # by the next `retry_pending_deliveries` pass, while the loop
            # continues to the remaining servers.
            logger.warning(
                "%s: resolve failed (%s); treating as a miss",
                name, deliveries.failure_detail(exc),
            )
            misses[name] = exc
    if not resolved_on:
        # `servers` itself can be empty (no media server
        # configured at all), which used to raise `StopIteration` from
        # `next(iter(misses.values()))` on an empty dict -- a confusing
        # crash rather than the ItemNotFound-class signal the worker's
        # defer/park ladder expects. An ItemNotFound miss (a scan-in-progress
        # wait) outranks any other exception here: if at least one server
        # simply has not seen the file yet, that is the honest reason to
        # report, even when another server failed for a different reason.
        if not misses:
            if absent_servers:
                # Every configured server says it does not carry this item's
                # library, so none was asked -- which is a different fact from
                # "nothing is configured" and must not be reported as one.
                raise ItemNotFound("no configured server carries this item's library")
            raise ItemNotFound("no media server is configured")
        not_found = next(
            (exc for exc in misses.values() if isinstance(exc, ItemNotFound)), None,
        )
        raise not_found or next(iter(misses.values()))
    item = resolved_on.get("plex") or next(iter(resolved_on.values()))
    media_item = await _persist_identity(session, item, resolved_on)
    if media_item.id != known_item_id:
        # The intent named no ref this database knows, so the set above was
        # read for nothing (or for another row). Now that the identity is
        # established, it is read for the item itself -- the same one query
        # `apply_metadata` used to make on its own.
        absent_servers = await absent_servers_for(session, media_item.id)

    # Spec §3's three re-arm doors, read ONCE per item and handed down the
    # way `absent_servers` is (controller ruling): the doors live inside
    # `apply_metadata`'s per-server loop and `deliver`'s, so asking per row
    # cost a SELECT per row a catch-up had ever touched.
    open_runs = await open_catch_up_runs(session)

    # Roadmap row 92. A separately-named object, and NOT a rebinding of
    # `config`: `render_artifact` below must keep the global one. Today that
    # is a distinction without a difference -- `config_for_library` carries
    # `artwork` through by identity -- but the day the artwork half lands
    # behind row 111 a rebinding here would quietly become a whole-library
    # re-render. The seam is idempotent, so `apply_metadata` and
    # `compose_badged_bytes` resolving again at their own reads costs nothing.
    library_config = config_for_library(config, item.library)

    if library_config.operations.enabled and tmdb_facts is not None:
        try:
            # Row 84's tvdb client, if the deployment's provider order builds
            # one -- the same object `providers` already holds, never a new
            # one, so it shares that client's cached token and cache.
            tvdb = next((p for p in providers if getattr(p, "name", None) == "TVDB"), None)
            await apply_metadata(
                session, config, media_item.id, item, servers.get(item.server), tmdb_facts, mdblist, tvdb,
                imdb_parental, servers=servers, resolved_on=resolved_on,
                absent_servers=absent_servers, open_runs=open_runs,
            )
        except AttributeError:
            # A server missing a required method (item_labels/apply_facts) is
            # a wiring bug, not the runtime failure below is for -- it must
            # not be silently contained as a per-item warning.
            raise
        except Exception:
            # If the failure was a database error, the transaction
            # is already aborted; without rolling back here, the artifact
            # loop's first session.execute() below would raise
            # PendingRollbackError instead of rendering, defeating this
            # containment's whole purpose. Every other error path in this
            # codebase rolls back first (see queue/worker.py).
            await session.rollback()
            # That rollback discards the identity written above, refs and
            # all -- and the only thing that re-establishes it further down
            # is `render_artifact`'s own `_upsert_media_item`, which
            # restores the primary ref and nothing else. Re-established
            # whole here instead, so a TMDb hiccup or one server's
            # `apply_facts` failure never costs the item another server's ref.
            media_item = await _persist_identity(session, item, resolved_on)
            logger.warning(
                "metadata operations failed for %s; continuing to artwork",
                item.native_id, exc_info=True,
            )

    results = []
    # Plain strings, not the ORM rows themselves: the refusal handler's
    # `rollback()` below expires every object the session is tracking
    # (SQLAlchemy's `dirty_only=False` default, independent of
    # `expire_on_commit` -- which this app sets False, so an ordinary commit
    # expires nothing here). So a LATER iteration's refusal would leave an
    # EARLIER iteration's `render` a lazy load away from its own `.detail` --
    # safe only inside an awaited call, which the aggregate message below is
    # not.
    refused: list[tuple[str, str]] = []
    for art_kind in ART_KINDS_FOR[intent.kind]:
        try:
            results.append(
                await render_artifact(
                    session, config, http, item, art_kind, providers,
                    plex_generated_base=plex_generated_base,
                )
            )
        except SourceRefused as exc:
            # Unlike the metadata and badge blocks above, this loop used to
            # have no containment at all: one kind's SourceRefused (a
            # validation refusal -- job 40478's "Inside Out 2" clearlogo,
            # e.g.) aborted every kind after it, costing the item its
            # background over a bad poster source. Recorded and the loop
            # continues instead.
            #
            # `render_artifact` already flushed (but, since it raised before
            # its own commit, did not persist) the render-row upsert for this
            # kind, so roll back before redoing that lookup in a clean
            # transaction -- the same rollback-then-continue shape the
            # metadata block above uses, and for the same reason.
            await session.rollback()
            logger.warning(
                "%s refused for %s: %s", art_kind, item.native_id, exc, exc_info=True,
            )
            media_item_for_kind = await _persist_identity(session, item, resolved_on)
            missing = naming.missing_number(art_kind, item.season_number, item.episode_number)
            target = "" if missing is not None else naming.asset_path(
                config, item.library, item.root_folder, art_kind,
                item.season_number, item.episode_number,
            )
            render = await _get_or_create_render(session, media_item_for_kind, art_kind, target)
            render.status = "failed"
            render.detail = str(exc)
            await session.commit()
            results.append(render)
            refused.append((art_kind, str(exc)))

    # A job whose EVERY kind refused must still fail so the operator sees it
    # on Failures, rather than reading as an ordinary `done` with nothing
    # rendered and nothing to show for it -- the one place this containment
    # must not go all the way.
    if refused and len(refused) == len(results):
        raise SourceRefused(
            f"every art kind refused for {item.native_id!r}: "
            + "; ".join(f"{kind}: {detail}" for kind, detail in refused)
        )

    if library_config.badges.enabled:
        try:
            if refused:
                # A refusal's rollback() above (see the comment on `results`)
                # expired every `Render` already sitting in `results`, not
                # just the refused kind's own row -- AND `media_item` itself
                # (created unconditionally, up front, so it exists
                # as a Python object through the whole artifact loop and is
                # just as exposed to that rollback). Left alone, the loop
                # below's first read of an earlier survivor's `.art_kind`, or
                # `compose_badged_bytes`' own read of `media_item.library`
                # (called unconditionally, once per render, so
                # it is reached even for a render this block used to skip
                # before ever touching `media_item`), is a plain attribute
                # access outside an awaited call -- a MissingGreenlet under
                # asyncio -- which this block's own `except` swallows,
                # silently costing the WHOLE item its badges rather than
                # just the refused kind's. Refresh every survivor before
                # touching any of them.
                #
                # `media_item` gets a fresh re-upsert instead of a refresh:
                # unlike a `Render` row (already committed before this
                # render's own refusal, by an EARLIER pass or an earlier
                # sibling kind this same pass), `media_item` was only ever
                # INSERTed in the transaction the refusal's rollback() just
                # undid -- a rolled-back INSERT is detached, not merely
                # expired, so `session.refresh` on this exact Python
                # reference raises `InvalidRequestError`. `_upsert_media_item`
                # is idempotent and this is the same re-upsert the refusal
                # handler above and `render_artifact` both already do for
                # their own reasons.
                for render in results:
                    await session.refresh(render)
                media_item = await _persist_identity(session, item, resolved_on)
            # Every resolved server's own ref, for `deliver`'s fan-out.
            refs = {name: resolved_item.ref for name, resolved_item in resolved_on.items()}
            # `compose_badged_bytes` still samples live media info and native
            # ratings off Plex alone (see its own docstring) -- `None` when
            # this pass never resolved one, which degrades rather than raises.
            plex_item = resolved_on.get("plex")
            # When exactly one resolved server is
            # upload-enabled this pass, compose_badged_bytes gets to check
            # adoption for THAT server before doing any image work at all --
            # see its own docstring. More than one (or zero) leaves `deliver`
            # to check each server on its own, after composing.
            upload_enabled_resolved = [
                (name, servers[name], resolved_item.ref)
                for name, resolved_item in resolved_on.items()
                if getattr(library_config.badges, f"upload_to_{name}", False)
            ]
            solo_delivery = (
                upload_enabled_resolved[0] if len(upload_enabled_resolved) == 1 else None
            )
            for render in results:
                # Compose once per render, deliver to every server: render
                # once per art kind, deliver per server (spec §5). Called
                # unconditionally, once per render -- exactly the shape the
                # old single `apply_badges` was called in -- and NOT gated
                # here on `render.art_kind`/`.status`: both
                # `compose_badged_bytes` and `deliver` already open with that
                # same check themselves (see their own docstrings), and
                # `deliver` still has to run for a background/unrendered
                # render's OTHER servers' misses to be irrelevant here -- it
                # is only THIS render that is not a badge candidate.
                data = await compose_badged_bytes(
                    session, config, render, media_item, http=http, mdblist=mdblist,
                    server=servers.plex if plex_item is not None else None,
                    ref=plex_item.ref if plex_item is not None else None,
                    solo_delivery=solo_delivery,
                )
                await deliver(
                    session, config, render, media_item, servers, refs, data,
                    misses=misses, absent_servers=absent_servers, open_runs=open_runs,
                )
        except AttributeError:
            # A server missing a required method (fetch_item/upload_artwork)
            # is a wiring bug, not the runtime failure below is for -- it must
            # not be silently contained as a per-item warning.
            raise
        except Exception:
            # Same containment as the metadata-operations block above: the
            # artifact loop already wrote the base image to disk, and a
            # badge failure must not cost the item that.
            await session.rollback()
            logger.warning(
                "badge stage failed for %s; base artwork already on disk",
                item.native_id, exc_info=True,
            )

    return results
