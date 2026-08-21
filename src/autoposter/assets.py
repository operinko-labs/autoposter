"""Where the bundled asset files live.

The badge images, fonts and lookup tables are data files shipped alongside the
code, not Python modules. Locating them by walking up from ``__file__`` works
in a source checkout and breaks in the container: the package is pip-installed
into site-packages while the assets are copied to ``/app/assets``, so the
relative walk lands in the Python library root instead.

That failure is not subtle in effect -- several modules read their tables at
import time, so the whole application fails to start -- but it is invisible in
a source checkout, which is why this lives in one place with an explicit
override rather than being repeated per module.
"""
import os
from functools import lru_cache
from pathlib import Path

ENV_VAR = "AUTOPOSTER_ASSETS_ROOT"


@lru_cache(maxsize=1)
def assets_root() -> Path:
    """The directory holding ``badges/`` and ``collections/``.

    ``AUTOPOSTER_ASSETS_ROOT`` wins when set -- the container image sets it.
    Otherwise fall back to the source-checkout layout, where this module sits
    at ``<repo>/src/autoposter/assets.py``.
    """
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "assets"


def asset_path(*parts: str) -> Path:
    """A path beneath the assets root, e.g. ``asset_path("badges", "fonts")``."""
    return assets_root().joinpath(*parts)
