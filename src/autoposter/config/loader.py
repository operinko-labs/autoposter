import hashlib
import json
import os
from pathlib import Path

import yaml

from autoposter.config.schema import Config, _merged_sections
from autoposter.config.state import state_config_path


def _default_config_path() -> Path:
    """Where the config document is.

    A PRESENT ``AUTOPOSTER_CONFIG`` file wins outright, which is what keeps
    every existing deployment on exactly today's path: the Kubernetes one sets
    the variable (helmrelease.yaml's env block) and so does
    docker-compose.yml's ``api`` service, and both mount a document at the
    path they set. That is roadmap row 121's rule.

    The variable being SET is not enough, because the image itself sets it:
    the runtime stage bakes ``ENV AUTOPOSTER_CONFIG=/config/autoposter.yaml``,
    so every container from this image carries it -- including the fresh
    first-start one that has no /config mount at all. Keying on the variable
    alone would make the fallback below unreachable in exactly the deployment
    it exists for: the wizard would write its document, the next boot would
    read ``/config/autoposter.yaml``, and ``read_config_document`` would raise
    ``FileNotFoundError`` with no wizard left to fix it.

    So the fallback fires when the configured path is unset OR absent. The
    wizard writes ``$AUTOPOSTER_STATE_DIR/autoposter.yaml`` and cannot set the
    process's own environment, so its document has to be FOUND rather than
    pointed at. With nothing anywhere the answer is the path that was
    configured (or ``/config/autoposter.yaml``, the mount it has always been),
    so a reader's error names the file an operator was expecting.
    """
    configured = os.environ.get("AUTOPOSTER_CONFIG")
    if configured and Path(configured).is_file():
        return Path(configured)
    from_state = state_config_path()
    if from_state.is_file():
        return from_state
    return Path(configured) if configured else Path("/config/autoposter.yaml")


def config_document_path() -> Path | None:
    """The config document this process would read, or ``None`` if there is
    none to read.

    ``boot`` decides the whole boot mode on this: credentials plus a document
    is CONFIGURED, and anything else is the first-start wizard. It asks here
    rather than re-deriving the search order so that the boot decision and the
    load that follows it can never look in different places.
    """
    path = _default_config_path()
    return path if path.is_file() else None


# Where the YAML lives, for the code that needs the *file* rather than the
# loaded object: the config editor merges its overrides onto that document, so
# it has to know which document. Defined here, next to the reader, because
# three entry points (`main`, and both CLIs) already spell this out
# individually and a fourth copy in the API layer would be one too many.
#
# Evaluated at import, as it always has been. A wizard that writes the document
# and then execs a fresh boot is re-importing this module, so the new file is
# picked up by the process that will actually use it.
DEFAULT_CONFIG_PATH = _default_config_path()


def render_version(config: Config) -> str:
    """A content hash of the *render-affecting* configuration only, WHOLESALE.

    Since roadmap row 111 this is no longer the first component of a render
    fingerprint -- ``render_version_for`` below computes that, per art kind, so
    that retuning one kind's settings invalidates that kind and leaves the
    others alone. This value is retained, and is load-bearing in two places:
    it is ``api/routes._render_affecting``'s cheap short-circuit (its payload
    is a strict SUPERSET of every per-kind payload, so an edit that leaves it
    alone cannot have moved a single kind), and it remains what the config
    editor displays as "version A to B", which is honest as "the render
    settings as a whole moved". Roadmap row 247 removed the third reader,
    row 111's dual-read grandfather; this value did NOT become dead with it.

    Hashing the raw config bytes -- which is what this used to do --
    made that true of every edit to the file, including ones that cannot
    change a single pixel: flipping ``adopt.apply`` back to ``false`` after a
    cutover, retuning ``scheduler.drift_batch_size``, or adding a comment.
    Each of those would strand ~16,000 adopted fingerprints and re-render the
    entire library through the provider ladder. The documented cutover
    procedure in ``deploy/README.md`` asks the operator to edit ``adopt.apply``
    twice, so that outcome was the expected path, not a corner case.

    So the hash is taken over a deterministic serialisation of the *validated
    model's* render-relevant fields instead: the artwork settings (every
    overlay, border, text block and quality setting that reaches ImageMagick),
    ``library_folders`` and the asset/font/overlay roots (which decide where a
    render reads its inputs from and writes its output to). Everything that
    cannot change a rendered image is excluded -- ``adopt``, ``scheduler``,
    ``cleanup``, ``prune``, ``collections``, ``operations``, ``badges``, the
    worker and database settings, and ``plex``/``providers`` connection
    details. Because the input is the parsed model, comments, key order and
    whitespace are irrelevant by construction.

    Note what is deliberately *not* covered: the overlay and font *files*.
    Their bytes are hashed separately into every fingerprint by
    ``render.pipeline.gather_fingerprint_inputs``, so replacing a font in
    place is still noticed without touching this value.
    """
    relevant = {
        "artwork": config.artwork.model_dump(mode="json"),
        "library_folders": config.library_folders,
        "assets_root": str(config.assets_root),
        "manual_assets_root": str(config.manual_assets_root),
        "fonts_root": str(config.fonts_root),
        "overlays_root": str(config.overlays_root),
    }
    payload = json.dumps(relevant, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# The art kinds a render fingerprint can carry, and the ONE home for that list
# (roadmap row 111). `render/pipeline.py`'s ART_KINDS_FOR maps an ITEM kind to
# the artifacts it produces, and `config/impact.py`'s `_ART_KINDS` was a second
# frozenset of the same four names; both now cite this one.
#
# It lives here, and not in pipeline.py, for an import reason: this module
# imports `config.schema` and nothing else, while `render/pipeline.py` pulls in
# httpx, the provider ladder, the badge stack and the Plex client. Putting
# the canonical list there would make every consumer of a config version import
# the whole render stack to hash a dict.
RENDER_ART_KINDS: tuple[str, ...] = ("poster", "season_poster", "background", "title_card")

# `artwork.*` fields that reach the POSTER's logo branch and nothing else.
# There is no `logo` art kind: no renders row, no _CANVAS entry, no
# ArtworkConfig.logo section. A logo is an ingredient of a poster, gated at
# render/pipeline.py:1202's `if art_kind == "poster" and config.artwork.use_logo`.
# If a future row ever gives logos their own renders row, this partition gains
# a fifth entry rather than needing rework.
_LOGO_FIELDS = (
    "use_logo",
    "logo_language_order",
    "logo_text_fallback",
    "use_clearart",
    "logo_flat_color",
)

# `artwork.season_episode_templates` names a template file for a season poster
# or a title card and nothing else (render/pipeline.py:242, :258).
_TEMPLATE_KINDS = ("season_poster", "title_card")


def _shared_render_inputs(config: Config) -> dict:
    """The render inputs that are a member of EVERY art kind's payload.

    Three groups, each global for a stated reason rather than by default:

    * the four roots and ``library_folders`` -- they decide where every kind
      reads its inputs and writes its output (``render/naming.py:105``), and
      ``scheduler/jobs.py`` already treats a root repoint as a library-wide
      event. Confining them would be a real bug;
    * ``artwork.use_original_title`` -- ``render/pipeline.py:174``'s
      ``primary_title_for`` swaps the title on every kind;
    * ``artwork.disable_online_asset_fetch`` (the GLOBAL half of row 47; each
      kind's own half rides in its subsection) -- ``pipeline.py:141-144``;
    * ``artwork.output_quality`` -- ``compose_styled`` passes it to
      ``build_base_argv`` (``pipeline.py:855``) for every kind and to
      ``build_text_argv`` (``:905``) for every kind that draws text.

    Because this block is a literal member of all four payloads, editing one
    of these still moves four hashes at once and the storm is unchanged for
    those edits. That is the correct behaviour, and
    ``tests/test_config.py::test_a_global_render_input_moves_every_kind`` and
    its ``_PARTITION`` siblings pin it.
    """
    return {
        "library_folders": config.library_folders,
        "assets_root": str(config.assets_root),
        "manual_assets_root": str(config.manual_assets_root),
        "fonts_root": str(config.fonts_root),
        "overlays_root": str(config.overlays_root),
        "artwork.use_original_title": config.artwork.use_original_title,
        "artwork.disable_online_asset_fetch": config.artwork.disable_online_asset_fetch,
        "artwork.output_quality": config.artwork.output_quality,
    }


def render_version_for(art_kind: str, config: Config) -> str:
    """``render_version``, confined to one art kind (roadmap row 111).

    ``render_version`` above hashes ``config.artwork`` WHOLESALE, so an
    operator retuning ``artwork.season_poster.text.max_point_size`` moved the
    first component of every stored fingerprint in the library and a
    full-library re-render was the honest answer. This hashes only the inputs
    that art kind actually reads, so the invalidation -- and the config
    editor's impact preview -- reaches the kinds the edit touched and no more.

    **``config.version`` deliberately STAYS the wholesale hash.** It is the
    correct cheap short-circuit for "could this edit possibly matter to any
    render" (``api/routes.py``'s ``_render_affecting``), it keeps six existing
    test files meaningful without rewriting them, and it keeps the Settings
    page's one-line "version A to B" honest as "the render settings as a whole
    moved". Roadmap row 247 removed the fourth reason -- row 111's dual-read
    grandfather -- and the three above are why this value is still here. The
    per-kind values are DERIVED, never a replacement, and are never stored on
    the config.

    **The partition, and why each half of it is where it is.**

    * the shared block -- see ``_shared_render_inputs``;
    * ``artwork.<art_kind>`` -- that kind's whole ``ArtKindConfig`` (or
      ``TitleCardConfig``), dumped. Every field on those models is cleanly
      confined to its own kind by ``art_config_for`` (``pipeline.py:149``);
    * ``artwork.library_language_overrides`` PROJECTED to this kind, not
      included wholesale: the mapping is already keyed by art kind
      (``pipeline.py:190``, validator ``config/schema.py:689-695``), so an
      override naming ``title_card`` for one library has no business moving a
      poster;
    * the five logo fields, for ``poster`` only -- see ``_LOGO_FIELDS``;
    * ``artwork.season_episode_templates``, for ``season_poster`` and
      ``title_card`` only -- see ``_TEMPLATE_KINDS``;
    * ``artwork.title_card.season_name_overrides``, for ``season_poster`` as
      well as for ``title_card``'s own dump -- roadmap row 78 made that table
      an input to the season poster's text too; see the projection below;

    Fonts and overlay FILES are not covered here for the same reason
    ``render_version`` does not cover them: their bytes are hashed per render
    into ``asset_hashes`` (``render/pipeline.gather_fingerprint_inputs``), so
    replacing a font in place is still noticed without touching this value.

    The art kind's own name is in the payload. ``art_kind`` is already
    fingerprint element 1 so this is belt-and-braces rather than load-bearing,
    but it means the four values read as four in a log and a future kind whose
    settings happen to match another's cannot silently share a version.
    """
    if art_kind not in RENDER_ART_KINDS:
        raise ValueError(
            f"{art_kind!r} is not a render art kind: expected one of "
            f"{', '.join(RENDER_ART_KINDS)}"
        )
    artwork_json = config.artwork.model_dump(mode="json")
    relevant: dict = {
        "art_kind": art_kind,
        f"artwork.{art_kind}": artwork_json[art_kind],
        "artwork.library_language_overrides": {
            library: by_kind[art_kind]
            for library, by_kind in config.artwork.library_language_overrides.items()
            if art_kind in by_kind
        },
    }
    if art_kind == "poster":
        for field in _LOGO_FIELDS:
            relevant[f"artwork.{field}"] = artwork_json[field]
    if art_kind in _TEMPLATE_KINDS:
        relevant["artwork.season_episode_templates"] = artwork_json["season_episode_templates"]
    if art_kind == "season_poster":
        # Roadmap row 78 co-delivers row 43's gap: since that row, a season
        # poster's own text is renamed by
        # `artwork.title_card.season_name_overrides`
        # (`render/pipeline.py::title_text_for`). The key lives under
        # title_card -- Posterizarr's OverrideSeasonName is in
        # SeasonPosterOverlayPart, but row 43 landed it on the card -- so
        # without this projection an operator's edit to it would move the
        # title cards and leave every season poster serving text the config no
        # longer describes. Projected, not moved: title_card's own wholesale
        # dump still carries it, so a card edit is unaffected.
        relevant["artwork.title_card.season_name_overrides"] = (
            artwork_json["title_card"]["season_name_overrides"]
        )
    # Literal keys first, the shared block spread last: the two key sets are
    # disjoint today (the "artwork." prefix above makes a collision unlikely),
    # but this ordering is the cheap insurance against a silent overwrite if
    # that ever stops being true.
    relevant = {**relevant, **_shared_render_inputs(config)}
    payload = json.dumps(relevant, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def moved_kinds(before: Config, after: Config) -> set[str]:
    """Which art kinds an edit invalidates.

    Option (b)'s one useful half, taken as a plain helper rather than as a
    field on ``Config``. A ``config.versions`` map would grow
    ``COMPUTED_PATHS``, would make ``GET /api/config`` serve a
    mapping where the editor renders a scalar, would turn
    ``ConfigSaveResponse.version_before``/``version_after`` into a four-way
    display, and would need a story for a computed ``dict[str, str]`` in both
    the descriptions walk and the example-config guard. This buys the
    exactness without touching any of that.

    Answers a set so a caller can say "nothing" (an empty set is falsy) as
    well as "which".
    """
    return {
        art_kind for art_kind in RENDER_ART_KINDS
        if render_version_for(art_kind, before) != render_version_for(art_kind, after)
    }


def config_for_library(config: Config, library: str) -> Config:
    """``config`` as it applies to ONE Plex library (roadmap row 92).

    Beside ``render_version`` and ``render_version_for`` because the version
    question and the library-effective question are the same question asked
    two ways, and because ``build_config`` below is already the one
    construction path -- a second one in a second module is how a derived
    value drifts.

    **Identity when there is nothing to do.** A library that names no
    override, or names one that states no leaf, gets the very object it was
    handed back: the ordinary deployment -- every one whose operator never
    opened the matrix -- allocates nothing per item. That is the posture
    ``config/overrides.py``'s ``without_migrated_sections`` already takes.

    **Only the whitelisted sections are rebuilt**, through
    ``model_copy(update=...)``, so ``artwork``, ``version`` and every other
    section are carried through BY IDENTITY -- except ``libraries`` itself,
    which the result CLEARS to ``{}`` rather than carrying through or
    rebuilding (see below). That is the storm proof restated at runtime: an
    effective config cannot carry a different ``artwork``, so it cannot
    carry a different version, so no per-library setting can invalidate a
    stored fingerprint however a caller uses the result.

    **The merge is ``config/overrides.py``'s own**, reached by a local import
    because that module imports this one. Nested mappings merge key by key;
    scalars and lists are replaced. Reusing it rather than writing a second
    merge is the whole point: an operator's expectation of what a stored
    override does to ``operations.genre_mapper`` should not change depending
    on whether the override was global or per library.

    **Pure, and cheap enough not to cache.** No I/O, no session, no mutation
    of the argument. A deployment WITH overrides pays three model
    constructions per resolution, which is nothing beside the provider ladder
    and the ImageMagick subprocess on the same code path; a cache keyed on
    (library, generation) is a thing to add when a profile asks for it, not
    before. It is also idempotent, so a caller that resolves once for a gate
    and a callee that resolves again at its own reads cost nothing but that.
    """
    override = config.libraries.get(library)
    if override is None:
        return config

    from autoposter.config.overrides import _merge

    sections = _merged_sections(config, override, _merge)
    if not sections:
        return config
    # Roadmap row 92 review, Minor 1. The result is now THIS library's own
    # effective config, so its `libraries` mapping is cleared rather than
    # carried through: without this, a caller resolving twice for two
    # DIFFERENT libraries -- `config_for_library(config_for_library(c,
    # "Movies"), "TV Shows")` -- would merge TV Shows' stated leaves over a
    # Movies-merged `operations`, a composition nobody asked for. Clearing it
    # makes a second resolve for any other library find no override and
    # return the result unchanged, the same identity path a config with no
    # override at all already takes.
    sections["libraries"] = {}
    # `model_copy`, not a re-validation: each section above was rebuilt
    # through its own model and so ran its own rules, and re-running
    # `Config`'s cross-section validators once per item would be work with
    # nothing to find.
    return config.model_copy(update=sections)


def read_config_document(path: Path) -> dict:
    """The YAML file as a plain dict, unvalidated.

    Split out from ``load_config`` so the database overrides layer
    (``config/overrides.py``) can merge into the same starting document
    instead of re-reading and re-parsing the file its own way.
    """
    raw_bytes = Path(path).read_bytes()
    data = yaml.safe_load(raw_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config at {path} must be a YAML mapping")
    return data


def build_config(data: dict) -> Config:
    """Validate a config document and derive its ``version``.

    The one construction path: both ``load_config`` and the overrides-aware
    ``load_effective_config`` end here, so a merged config is validated and
    versioned exactly like a file-only one. ``version`` is derived from the
    render-affecting settings only, after validation -- see ``render_version``
    for why it is not a hash of the file.
    """
    config = Config(**data)
    config.version = render_version(config)
    return config


#: Paths the service computes rather than the operator setting.
#:
#: ``version`` is the render-settings hash ``build_config`` stamps above,
#: stored on each Render row so a settings change is detectable as staleness --
#: writing one by hand overrides it with a value the next load recomputes away.
#: Served by ``GET /api/config`` so the editor can render it read-only instead
#: of offering an edit that does nothing (roadmap row 112). Not a refusal: an
#: override on it is still accepted and still inert, exactly as before.
#:
#: Here rather than beside that endpoint because two readers need it now: the
#: drift report subtracts these before comparing the file with the store, since
#: a value neither of them owns cannot be a difference between them.
COMPUTED_PATHS: tuple[str, ...] = ("version",)


def load_config(path: Path) -> Config:
    """Load and validate the YAML config.

    Stays synchronous: tests, fixtures and the config-only code paths call it
    without a database. The overrides-aware variant is
    ``config.overrides.load_effective_config``.
    """
    return build_config(read_config_document(path))
