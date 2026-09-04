import hashlib
import json
import os
from pathlib import Path

import yaml

from autoposter.config.schema import Config

# Where the YAML lives, for the code that needs the *file* rather than the
# loaded object: the config editor merges its overrides onto that document, so
# it has to know which document. Defined here, next to the reader, because
# three entry points (`main`, and both CLIs) already spell this out
# individually and a fourth copy in the API layer would be one too many.
DEFAULT_CONFIG_PATH = Path(os.environ.get("AUTOPOSTER_CONFIG", "/config/autoposter.yaml"))


def render_version(config: Config) -> str:
    """A content hash of the *render-affecting* configuration only, WHOLESALE.

    Since roadmap row 111 this is no longer the first component of a render
    fingerprint -- ``render_version_for`` below computes that, per art kind, so
    that retuning one kind's settings invalidates that kind and leaves the
    others alone. This value is retained, and is load-bearing in two places:
    it is ``api/routes._render_affecting``'s cheap short-circuit (its payload
    is a strict SUPERSET of every per-kind payload, so an edit that leaves it
    alone cannot have moved a single kind), and it is the legacy candidate the
    dual-read grandfather accepts from rows fingerprinted before row 111
    landed. It also remains what the config editor displays as "version A to
    B", which is honest as "the render settings as a whole moved".

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
# httpx, PIL, the provider ladder, the badge stack and the Plex client. Putting
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
    test files meaningful without rewriting them, it keeps the Settings page's
    one-line "version A to B" honest as "the render settings as a whole
    moved", and it is the value the dual-read grandfather accepts from rows
    written before this function existed. The per-kind values are DERIVED,
    never a replacement, and are never stored on the config.

    **The partition, and why each half of it is where it is.**

    * the shared block -- see ``_shared_render_inputs``;
    * ``artwork.<art_kind>`` -- that kind's whole ``ArtKindConfig`` (or
      ``TitleCardConfig``), dumped. Every field on those models is cleanly
      confined to its own kind by ``art_config_for`` (``pipeline.py:118``);
    * ``artwork.library_language_overrides`` PROJECTED to this kind, not
      included wholesale: the mapping is already keyed by art kind
      (``pipeline.py:160``, validator ``config/schema.py:689-695``), so an
      override naming ``title_card`` for one library has no business moving a
      poster;
    * the five logo fields, for ``poster`` only -- see ``_LOGO_FIELDS``;
    * ``artwork.season_episode_templates``, for ``season_poster`` and
      ``title_card`` only -- see ``_TEMPLATE_KINDS``.

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
    relevant: dict = {
        "art_kind": art_kind,
        **_shared_render_inputs(config),
        f"artwork.{art_kind}": getattr(config.artwork, art_kind).model_dump(mode="json"),
        "artwork.library_language_overrides": {
            library: by_kind[art_kind]
            for library, by_kind in config.artwork.library_language_overrides.items()
            if art_kind in by_kind
        },
    }
    if art_kind == "poster":
        for field in _LOGO_FIELDS:
            relevant[f"artwork.{field}"] = getattr(config.artwork, field)
    if art_kind in _TEMPLATE_KINDS:
        relevant["artwork.season_episode_templates"] = config.artwork.season_episode_templates
    payload = json.dumps(relevant, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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


def load_config(path: Path) -> Config:
    """Load and validate the YAML config.

    Stays synchronous: tests, fixtures and the config-only code paths call it
    without a database. The overrides-aware variant is
    ``config.overrides.load_effective_config``.
    """
    return build_config(read_config_document(path))
