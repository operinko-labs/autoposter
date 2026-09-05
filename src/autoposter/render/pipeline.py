import asyncio
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.badges.compose import (
    manifest_sha,
    BadgeInputs,
    badge_fingerprint,
    badge_values,
    compose as compose_badges,
)
from autoposter.badges.values import (
    media_info_from_plex,
    plex_native_ratings,
    video_format_text,
)
from autoposter.config.loader import config_for_library, render_version_for
from autoposter.config.schema import Config, TextStyle
from autoposter.db.models import EventLog, ItemFacts, MediaItem, Render
from autoposter.facts.gather import gather_facts, persist_facts
from autoposter.facts.mdblist import MDBListLimitReached
from autoposter.facts.models import GatheredFacts
from autoposter.intake.arr import RenderIntent
from autoposter.overlays.selection import OverlayItemView
from autoposter.overlays.selection import select as select_overlay_definitions
from autoposter.overlays.sources import OverlaySourceError, resolve_image_path
from autoposter.plex.artwork import generated_title_card_url, upload_artwork
from autoposter.plex.client import ResolvedItem
from autoposter.plex.item_overrides import load_overrides, overlaid_badge_facts
from autoposter.plex.writer import apply_facts, exemption_reason
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
        # Roadmap row 43's other half, co-delivered here (facts C7).
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
    # suppressed; a season poster's show title does not, facts C6). Miss this
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
    # wholesale hash and is still what render_artifact's dual-read grandfather
    # (its `legacy_candidate` in the adopted arm and `legacy_fingerprint` in
    # the live compare) accepts from a row written before that row landed.
    return compute_fingerprint(
        render_version_for(art_kind, config), art_kind, None, base_sha256,
        text_inputs, asset_hashes,
    )


async def fetch_plex_generated_base(
    http: httpx.AsyncClient, plex, rating_key: str, destination: Path,
    *, base_url: str, headers: dict[str, str], stage: str,
) -> str | None:
    """Download Plex's own generated title-card frame, or answer ``None``.

    The plex-preview fallback (roadmap row 241): lazily fetches the plexapi
    item for ``rating_key`` (adjudication A2 -- only an episode whose
    provider ladder came back empty ever reaches this, so this is not a
    second fetch for every item ``process_item`` already handles), lists its
    posters, and downloads the ``media://``-prefixed entry
    (``generated_title_card_url``) -- never an ``upload://`` entry, which is
    our own previous output locked onto the same field (the self-feed loop
    C1.2 refutes). ``None`` when the listing has no such entry, the caller's
    cue to fall through to the existing ``no_art`` outcome.

    Goes through ``_download``, not a bare GET, so the fetch gets #131's
    full-decode validation and the byte cap for free.
    ``follow_redirects=False`` (adjudication A5): ``X-Plex-Token`` is a
    custom header httpx will not strip on a cross-origin redirect, and PMS
    never needs one for an image blob anyway.

    Built as a ``functools.partial`` at app.py's composition time, with
    ``http``, ``plex``, ``base_url`` and the token header baked in --
    ``render_artifact`` calls the result with only ``rating_key`` and
    ``destination``, so the token is never in scope there at all.

    M1: a non-2xx from Plex (a rotated token's 401, a 3xx now that
    ``follow_redirects=False``, a PMS 5xx) raises ``httpx.HTTPStatusError``
    from ``_download``'s ``raise_for_status()``. ``process_item``'s per-kind
    containment only catches ``SourceRefused``, so left uncaught this would
    fail the whole job over what used to be a quiet ``no_art`` row. Caught
    here and logged once, by rating key and status code only -- never the
    URL, which carries no token itself but is still not worth logging -- so
    the caller falls through to the existing ``no_art`` outcome.
    """
    plex_item = await plex.fetch_item(rating_key)
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
            rating_key, exc.response.status_code,
        )
        return None


# The events_log identity of a re-key, for the reason scheduler/prune.py's
# PRUNE_SOURCE/PRUNE_EVENT are constants: the audit row is the only surviving
# record that a row's Plex key moved under it, and a typo in either would make
# a library's worth of them unfindable.
REKEY_SOURCE = "rekey"
REKEY_EVENT = "media_item_rekeyed"

# The events_log identity of a FORK STOP, filed under REKEY_SOURCE beside the
# re-key's own row rather than under a source of its own: both are the same
# identity story, and an operator asking "what happened to this row's key"
# should grep one source. Its own event_type, because the two rows say
# opposite things -- one records a key that moved, the other a job that
# declined to touch a key that did not.
FORK_EVENT = "process_item_fork_stopped"

# The no-op outcome's name. A job that stops here COMPLETES -- queue/worker's
# `else: await complete(...)` -- so it is never retried, never deferred and
# never parked. `complete()` does write `job.last_error` (the SERVED column,
# api/jobs.py), but writes it None (queue/jobs.py:327): NOTHING IS SERVED,
# which is stricter than the class-name-only rule row 213 sets for the paths
# that do serve something. _served_reason is never even reached -- it is
# called only from the fail() branches. The pod log's WARNING and this audit
# row are the whole record.
FORK_OUTCOME = "fork_stopped"


def _identity_clauses(item: ResolvedItem) -> list:
    """The external-id half of the identity predicate, as OR-able clauses.

    Empty when the resolved item carries no external id at all. That is the
    "never a title-only match" rule expressed as an absence rather than as a
    special case: with no clause to OR there is no identity to match on, and
    every caller reads the empty list as a refusal.
    """
    clauses = []
    if item.tmdb_id is not None:
        clauses.append(MediaItem.tmdb_id == item.tmdb_id)
    if item.tvdb_id is not None:
        clauses.append(MediaItem.tvdb_id == item.tvdb_id)
    if item.imdb_id is not None:
        clauses.append(MediaItem.imdb_id == item.imdb_id)
    return clauses


async def _identity_candidates(
    session: AsyncSession, item: ResolvedItem
) -> list[MediaItem]:
    """Every row carrying ``item``'s identity under some OTHER key, locked.

    Its own function for two reasons. It is where the ``FOR UPDATE`` lives,
    and a lock taken half-way down a longer function is the kind of thing that
    gets moved by accident; and it is the seam the lost-race test wraps, which
    needs somewhere to land a competing insert between the free-key check and
    the update.

    The predicate is the same cross-check ``_fetch_by_rating_key_sync``
    already applies, and adds no new field: same kind, same library, the same
    season/episode coordinates, and at least one external id in common. The
    coordinates are not decoration -- every season of one show carries the
    show's ids, so without them season 1's row matches season 2's resolution
    and the exactly-one guard below refuses every season in the library
    instead of making the one right re-key.

    ``LIMIT 2`` because the caller only ever asks "exactly one?". A third row
    changes no decision and a library-sized result is not worth reading to
    find that out.
    """
    clauses = _identity_clauses(item)
    if not clauses:
        return []
    return (
        await session.execute(
            select(MediaItem)
            .where(MediaItem.kind == item.kind)
            .where(MediaItem.library == item.library)
            .where(MediaItem.season_number.is_not_distinct_from(item.season_number))
            .where(MediaItem.episode_number.is_not_distinct_from(item.episode_number))
            .where(or_(*clauses))
            .where(MediaItem.rating_key != item.rating_key)
            .order_by(MediaItem.id)
            .limit(2)
            .with_for_update()
        )
    ).scalars().all()


async def _rekey_by_identity(
    session: AsyncSession, item: ResolvedItem
) -> str | None:
    """Move an existing row onto ``item``'s live rating key. Returns the old key.

    ``media_items`` is keyed on the Plex rating key and the Plex rating key is
    a *hint*: ``resolve()`` returns the key it FOUND, which after a re-match
    or a library rebuild is not the key the row holds. ``_upsert_media_item``
    below is an ``ON CONFLICT (rating_key)`` upsert, so a new key conflicts
    with nothing and INSERTS -- a second row that inherits every future render
    while the original keeps its renders, its facts, its dismissals, its
    ``logo_upload_key`` and its children and is never written again.

    The precondition is proved from the DATABASE, not from a key comparison,
    and that is the whole design. Comparing ``intent.rating_key`` to
    ``item.rating_key`` covers only the paths that CARRY a key; the ratings
    drift sweep and the Sonarr/Radarr webhooks carry none by construction, and
    both minted twins with no warning at all. Two facts, in this order:

    1. **No row already holds the resolved key.** If one does, the twin exists
       already, and reconciling two rows is the ``plex_merge`` job's work
       under a dry run an operator reads first -- so this does nothing. This
       check is also the hot path's entire cost: an item whose key has not
       moved satisfies it with its own row and returns immediately.
    2. **Exactly one row carries the resolved identity.** Zero is an ordinary
       new item; more than one is an ambiguity that would otherwise be settled
       by picking a side at random.

    Concurrency: ``rating_key`` carries a real unique constraint, which is why
    ``_upsert_media_item`` is an upsert in the first place. Two workers can
    resolve the same identity at once, so the candidate is taken ``FOR
    UPDATE`` and the flush is guarded -- the loser rolls back and falls
    through to the ordinary upsert, which then finds the row the winner
    created. A lost race must never fail a job.

    The write is committed rather than left to the caller's transaction. A
    ``process_item`` whose every art kind refuses raises and the worker rolls
    back, so a flush-only re-key would be lost -- and the same fork would then
    happen again on every subsequent pass while the audit trail said it had
    been fixed.
    """
    taken = (
        await session.execute(
            select(MediaItem.id).where(MediaItem.rating_key == item.rating_key)
        )
    ).scalar_one_or_none()
    if taken is not None:
        return None

    candidates = await _identity_candidates(session, item)
    if len(candidates) != 1:
        if candidates:
            logger.warning(
                "not re-keying to %s: %d rows carry that identity "
                "(%s in %r); the twin merge owns this pair",
                item.rating_key, len(candidates), item.kind, item.library,
            )
        # The candidate query above takes FOR UPDATE. Left open, this
        # transaction would hold that lock across the rest of process_item's
        # render -- the provider fetch, the image download, the ImageMagick
        # compose -- blocking any concurrent upsert or prune on either row
        # for that whole window. Rolling back releases it immediately; there
        # is nothing pending in this transaction to lose (the candidate
        # query is read-only). This also fires on the zero-candidate path
        # (an ordinary new item), which is the common case.
        await session.rollback()
        return None

    stale = candidates[0]
    stale_id = stale.id
    old_key = stale.rating_key
    try:
        # An ORM mutation rather than a Core UPDATE: the row is in this
        # session's identity map (the SELECT above loaded it), and a Core
        # UPDATE with synchronize_session=False would leave the in-memory
        # object still reporting the OLD key -- which is exactly what
        # _upsert_media_item's re-SELECT would then hand back.
        stale.rating_key = item.rating_key
        await session.flush()
        session.add(EventLog(
            source=REKEY_SOURCE,
            event_type=REKEY_EVENT,
            payload={
                "media_item_id": stale_id,
                "old_rating_key": old_key,
                "new_rating_key": item.rating_key,
                "kind": item.kind,
                "library": item.library,
                "title": item.title,
                "season_number": item.season_number,
                "episode_number": item.episode_number,
                "tmdb_id": item.tmdb_id,
                "tvdb_id": item.tvdb_id,
                "imdb_id": item.imdb_id,
            },
            outcome=f"re-keyed {old_key} -> {item.rating_key} on an identity match",
        ))
        await session.commit()
    except (IntegrityError, DBAPIError) as exc:
        # IntegrityError is the unique-constraint race (two workers resolve
        # the same identity at once). DBAPIError also belongs here, but only
        # when it carries sqlstate 40P01 -- under asyncpg, a lock cycle
        # across two concurrent re-keys surfaces as a plain DBAPIError, not
        # OperationalError, with that sqlstate stamped onto ``exc.orig``.
        # Anything else (e.g. a dropped connection) must still fail the job.
        if not isinstance(exc, IntegrityError) and getattr(
            exc.orig, "sqlstate", None
        ) != "40P01":
            raise
        await session.rollback()
        if isinstance(exc, IntegrityError):
            logger.warning(
                "re-key of %s to %s lost a race; the winner's row holds the "
                "key, so this pass stops rather than upsert onto it",
                old_key, item.rating_key, exc_info=True,
            )
        else:
            logger.warning(
                "re-key of %s to %s lost a race to a deadlock; nothing took "
                "the key in THIS transaction -- the rollback says no more "
                "than that -- so this pass falls through and mints a fresh "
                "twin unless another worker landed the key first",
                old_key, item.rating_key, exc_info=True,
            )
        return None

    logger.info(
        "re-keyed media_items row %d from %s to %s (%s %r)",
        stale_id, old_key, item.rating_key, item.kind, item.title,
    )
    return old_key


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
    # Roadmap row 78. Inside the `draw_text` arm, and appended LAST, for two
    # separate reasons. Inside: facts C6 -- the show title obeys the same flag
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
        rating_key=item.rating_key,
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
    row 241): an async callable ``(rating_key, destination, *, stage) -> str
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
        # Roadmap row 111's dual-read grandfather. Rows written before that row
        # landed carry element 0 = the WHOLESALE config.version, and four
        # per-kind payloads cannot all hash to that one value except by
        # collision -- so without this arm the first deploy would strand every
        # adopted row in the library. One extra sha256 over a joined string,
        # and NO extra I/O: text_inputs and asset_hashes are already in hand.
        # Removed one release later, once the pod has completed a full pass
        # (roadmap follow-up row).
        legacy_candidate = compute_fingerprint(
            config.version, art_kind, None, render.base_sha256, text_inputs, asset_hashes
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
        if render.fingerprint in (adopted_candidate, legacy_candidate):
            current_sha = await asyncio.to_thread(_file_sha256, target)
            if current_sha == render.base_sha256:
                # The write-back, and it is the whole migration: a legacy row
                # leaves this branch carrying the per-kind value, so the next
                # pass matches outright. `detail` stays "adopted" -- no new
                # served string is invented to make the migration visible.
                render.fingerprint = adopted_candidate
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
                    item.rating_key, working, stage="the title_card source",
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
                # lockPoster bumps on every pass (the epoch-URL refutation,
                # C1.3). Storing and hashing this same string (the write-back
                # below reuses this variable) is what keeps a bumped epoch
                # from moving the fingerprint while regenerated bytes still
                # do, through base_sha alone -- and what keeps
                # config/impact.py's recompute honest, since it reads this
                # same stored column back.
                base_sha = plex_base_sha
                source_url = f"plex://{item.rating_key}/title_card"
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
                logo_sha = await asyncio.to_thread(_stage_override, picked_logo, logo_path)
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
                            item.rating_key, item.title, skipped_logos,
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
        # Roadmap row 111's dual-read grandfather; see the adopted arm above
        # for why it exists and when it goes.
        legacy_fingerprint = compute_fingerprint(
            config.version, art_kind, source_url, base_sha, text_inputs, asset_hashes
        )
        # target.exists() offloaded (it's a stat() against assets_root, which
        # can be an NFS mount) — only reached once a fingerprint already
        # matches, so the short-circuit still skips it entirely otherwise.
        if render.fingerprint in (fingerprint, legacy_fingerprint) and await asyncio.to_thread(
            target.exists
        ):
            # The write-back. On a legacy match this is the migration: no
            # composite, no asset write, no Plex upload -- the ladder and the
            # download above already ran and would have run anyway.
            render.fingerprint = fingerprint
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

    Every ``operations`` setting read here is this item's LIBRARY's
    (roadmap row 92): a library that states one uses it, and one that states
    nothing uses the global.
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
    if (
        config.operations.write_to_plex
        and plex_item is not None
        and (not facts.is_empty() or has_verbs or has_parental or has_overrides)
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
            await apply_facts(
                plex_item, facts, config.operations, parental_categories, overrides,
            )
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


async def apply_badges(
    session, config, render, item, plex_item, facts, probe=None, *, http=None, mdblist=None
) -> None:
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

    ``http`` is the client roadmap row 97's operator-defined overlays resolve
    their ``url:`` sources through (``overlays.sources.resolve_image_path``).
    Keyword-only with a ``None`` default: ``tests/test_badge_pipeline.py``
    calls this function positionally at ~30 sites, and this keeps every one
    of them working. With no client, a definition naming a ``url:`` source
    simply cannot resolve one -- ``resolve_image_path`` raises
    ``OverlaySourceError``, which the loop below already turns into a
    skip-with-a-warning.

    ``mdblist`` is roadmap row 100 sub-phase C2a's rating source, optional
    with a ``None`` default for the same reason ``http`` is: every existing
    caller keeps working unchanged. With no client, the eleven ``mdb_*``
    tokens simply do not resolve -- ``UnresolvedVariable`` is caught per
    definition, same as any other unresolved token.
    """
    # Roadmap row 92, and it must be ABOVE the `badges.enabled` gate: that
    # gate is itself a per-library setting, so resolving after it would make
    # a library able to override everything except whether badges happen at
    # all. `_already_in_plex` below reads `config.badges` too and is called
    # with this rebound object, so the whole stage is one library's.
    #
    # `item` here is the `media_items` ROW, not the resolved item -- the
    # badge block re-reads the row before calling this -- and it carries
    # `.library` for the same reason every other consumer does.
    config = config_for_library(config, item.library)
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

    # Row 99's C4, at the single seam that reads facts for a badge. The
    # placement is code-true rather than assumed: ``process_item`` discards
    # ``apply_metadata``'s return value and re-reads the PERSISTED
    # ``ItemFacts`` row before calling this function, so laying the override
    # on inside ``apply_metadata`` would never reach the badge. Here it
    # reaches every caller of this function.
    #
    # A READ-ONLY view, never a mutation: ``facts`` is usually the ORM row
    # this session is tracking, and mutating it would be flushed into
    # ``item_facts`` by the next commit -- the provider's own record poisoned
    # by accident. With no overrides ``overlaid_badge_facts`` returns the very
    # object it was handed, so gate-off is indistinguishable from before this
    # row even by identity.
    #
    # What it costs: this item's ``badge_values`` move, so its
    # ``badge_fingerprint`` moves ONCE and it re-badges ONCE. Nothing here
    # touches ``_definitions_digest``, ``_outcomes_digest``,
    # ``_rating_values_digest`` or ``config.version``, so no other item moves
    # at all.
    # Task-2 fix round 1, ruling on m-2: an item ``exemption_reason`` excludes
    # from the Plex write (above, in ``apply_metadata``) must be excluded
    # from the badge overlay too, or the two visibly disagree -- Plex still
    # shows the provider's value, the badge shows the operator's. This checks
    # the same gate ``apply_metadata`` does and, when exempt, skips the
    # overlay AND the ``load_overrides`` read that would feed it -- the badge
    # still renders, from the persisted provider facts, exactly as it does
    # for an exempt item with no override at all.
    if config.operations.item_overrides_enabled:
        exempt = exemption_reason(
            config.operations, item.rating_key, item.imdb_id,
            getattr(plex_item, "labels", None),
        )
        if exempt is None:
            facts = overlaid_badge_facts(
                facts, await load_overrides(session, item.id)
            )

    critic_rating = getattr(facts, "critic_rating", None)
    audience_rating = getattr(facts, "audience_rating", None)
    ratings: dict[str, float | None] = dict(plex_native_ratings(plex_item))
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
    # A7). `item.kind in ("movie", "show")` mirrors `facts/gather.py::
    # gather_facts`'s own gate for `mdblist.content_rating` exactly -- season
    # and episode badges do not carry mdb_* ratings in this slice. A
    # transient MDBList failure must degrade this one set of values, not the
    # whole badge stage: same two `except` clauses `gather_facts` already
    # uses for the same client.
    if mdblist is not None and item.kind in ("movie", "show"):
        try:
            ratings.update(await mdblist.ratings(
                tmdb_id=item.tmdb_id, tvdb_id=item.tvdb_id,
                is_movie=item.kind == "movie",
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

    # The SELECTION half (overlay era sub-phase C1). Evaluated BEFORE the
    # fingerprint gate on purpose: `badge_fingerprint` folds this item's own
    # match outcomes in beside the definitions digest, so an item whose
    # resolution (or, in a later slice, aspect or language count) changed
    # re-renders. If the outcomes were computed after the gate, the gate
    # could never see them and a changed item would keep its old badge
    # forever -- adjudication A4. The cost is one `json.dumps` of the
    # condition (ahead of `compiled_condition`'s cache lookup) plus a filter
    # tree walk, per CONDITIONED definition, on every unchanged item; the
    # view itself makes no Plex request of its own -- `media_info_from_plex`
    # above already reloaded the item for `.media`, and the view's own
    # accessors read `plex_item` the same reload-free way `filter_values.py`
    # does for everything else.
    # `all_definitions()`, not `.definitions`: a named family's definitions
    # are drawn too, and they must be in the list the fingerprint hashes as
    # well as in the list that gets selected over -- enabling a family has to
    # re-badge exactly like adding a definition by hand does.
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
    plex_generated_base=None,
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

    ``plex_generated_base`` is passed straight to ``render_artifact``; see
    its own docstring for what it does and why it is optional.

    Answers ``[]`` -- a completed no-op job -- when the intent carried a
    rating key, the resolved key differs, and a ``media_items`` row already
    exists under the resolved key that the identity re-key did not put there.
    A resolved key with no row at all is NOT a stop: that job falls through to
    the ordinary upsert exactly as before, which is what still mints a newly
    discovered item. See the fork stop below.
    """
    item = await plex.resolve(intent)

    # The identity fork (roadmap: the ~411 unscorable floor investigation).
    # `resolve()` treats `intent.rating_key` as a hint and is free to return a
    # DIFFERENT key -- the live copy's, after a re-match or a library rebuild
    # renumbers the item. Everything below this point (media_item upsert, the
    # render rows, the Plex field writes) is keyed on the RESOLVED item, not
    # the one the intent named. The served surfaces stay class-name-only as
    # everywhere else in this queue; the pod log is the trusted sink, so this
    # is a WARNING there and nowhere else -- but it turns a silent hole into a
    # grep.
    forked = intent.rating_key is not None and item.rating_key != intent.rating_key
    if forked:
        logger.warning(
            "resolved rating key %s for %r differs from the intent's %s; "
            "the intent's row will not be scored by this job",
            item.rating_key, item.title, intent.rating_key,
        )

    # The re-key. The WARNING above only fires when the intent CARRIED a key,
    # so it can never see the two silent producers -- the ratings-drift sweep
    # and the webhook intake both build intents with no rating key at all --
    # and a key-comparison guard here would leave both open. This asks the
    # database instead, which closes every upserting path in one place: is the
    # resolved key free, and does exactly one row carry the resolved identity.
    # It must run BEFORE the first _upsert_media_item below, because that is
    # the call that would otherwise insert the twin (and render_artifact, the
    # refusal path and the badge path all upsert again after it).
    rekeyed_from = await _rekey_by_identity(session, item)

    # The fork stop. It fires on exactly one condition: a row ALREADY HOLDS
    # the resolved key and it is not this intent's row. Then the resolved item
    # is somebody else's -- it has its own media_items row and its own visits,
    # and this job was never asked about it. Carrying on rendered it, uploaded
    # it, and -- because the unchanged-check compares against the intent's row
    # -- wrote Plex fields to it on every single pass ("plex: wrote 2 field(s)
    # to movie 'Boss Level'", every run, forever).
    #
    # Two filters, cheapest first, and the order is the whole design:
    #
    # 1. `rekeyed_from != intent.rating_key`. A re-key that MOVED this
    #    intent's row onto the resolved key is the success this phase exists
    #    for: the row under that key is ours, and the job goes on. Checking it
    #    first also means the hot path -- an unforked item, or a successful
    #    re-key -- never issues the query below at all.
    # 2. A row exists under `item.rating_key`. This is the part that cannot be
    #    inferred from the re-key's return value, because _rekey_by_identity
    #    answers None on FIVE different refusals and only two of them mean a
    #    row is there (the key was already taken; an IntegrityError race the
    #    winner committed). On the other three -- zero identity candidates, an
    #    ambiguous pair, a 40P01 deadlock -- the resolved key is EMPTY, there
    #    is no twin, and stopping would delete a job's whole purpose: the
    #    ordinary upsert below is what mints that row, and it is what arr
    #    discovery relies on. The twin merge reconciles rows; it never creates
    #    them, so a stop there would wait for something that never comes.
    #
    # One indexed read on the rating_key unique constraint, and an honest one:
    # four of the five refusal arms roll back before returning (:797, :841),
    # ending the transaction, and the taken-key arm has issued nothing but
    # this same select -- so whatever it sees is committed truth.
    #
    # This changes nothing about the refusal itself (`p-rekey-facts.md` C1:
    # cross-library or id-disjoint is not a re-key, and the pair stays the
    # twin merge's work), and nothing about the row-less fall-through C1.1
    # calls "the twin path as today" -- only about a pair that already exists.
    # Returning an empty list COMPLETES the job (queue/worker.py's
    # `else: complete(...)`), so it is never retried, deferred or parked; a
    # job that cannot do anything useful must not look like a failure an
    # operator has to clear.
    if forked and rekeyed_from != intent.rating_key:
        resolved_row_id = (
            await session.execute(
                select(MediaItem.id).where(MediaItem.rating_key == item.rating_key)
            )
        ).scalar_one_or_none()
        if resolved_row_id is not None:
            session.add(EventLog(
                source=REKEY_SOURCE,
                event_type=FORK_EVENT,
                payload={
                    "intent_rating_key": intent.rating_key,
                    "resolved_rating_key": item.rating_key,
                    "kind": item.kind,
                    "library": item.library,
                    "title": item.title,
                },
                outcome=FORK_OUTCOME,
            ))
            # A commit, not a flush: _rekey_by_identity's refusal paths roll
            # back (releasing their FOR UPDATE), and this row must survive
            # whatever the caller does next.
            await session.commit()
            return []

    media_item = None
    plex_item = None
    # Roadmap row 92. A separately-named object, and NOT a rebinding of
    # `config`: `render_artifact` below must keep the global one. Today that
    # is a distinction without a difference -- `config_for_library` carries
    # `artwork` through by identity -- but the day the artwork half lands
    # behind row 111 a rebinding here would quietly become a whole-library
    # re-render. The seam is idempotent, so `apply_metadata` and
    # `apply_badges` resolving again at their own reads costs nothing.
    library_config = config_for_library(config, item.library)

    if library_config.operations.enabled and tmdb_facts is not None:
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
                "%s refused for %s: %s", art_kind, item.rating_key, exc, exc_info=True,
            )
            media_item_for_kind = await _upsert_media_item(session, item)
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
            f"every art kind refused for {item.rating_key!r}: "
            + "; ".join(f"{kind}: {detail}" for kind, detail in refused)
        )

    if library_config.badges.enabled:
        fetch_item = plex.fetch_item  # outside the try; see the block above
        try:
            if refused:
                # A refusal's rollback() above (see the comment on `results`)
                # expired every `Render` already sitting in `results`, not
                # just the refused kind's own row. Left alone, the loop
                # below's first read of an earlier survivor's `.art_kind` is
                # a plain attribute access outside an awaited call -- a
                # MissingGreenlet under asyncio -- which this block's own
                # `except` swallows, silently costing the WHOLE item its
                # badges rather than just the refused kind's. Refresh every
                # survivor before touching any of them.
                for render in results:
                    await session.refresh(render)
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
                    probe=artwork_probe, http=http, mdblist=mdblist,
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
