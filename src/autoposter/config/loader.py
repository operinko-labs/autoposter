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
    """A content hash of the *render-affecting* configuration only.

    This value is the first component of every render fingerprint, so anything
    it covers invalidates every stored fingerprint in the library when it
    changes. Hashing the raw config bytes -- which is what this used to do --
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
