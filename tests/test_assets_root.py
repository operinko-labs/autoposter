"""Where bundled assets are found, and that a missing one cannot kill startup.

Both properties were broken: modules located their data files by walking up
from ``__file__``, which works in a source checkout and resolves into the
Python library root once the package is pip-installed with the assets copied
elsewhere -- and they read those files at import time, so the failure took
down the whole application rather than the one feature.
"""
import importlib
import sys

import pytest

from autoposter import assets


@pytest.fixture(autouse=True)
def _clear_cache():
    assets.assets_root.cache_clear()
    yield
    assets.assets_root.cache_clear()


def test_the_environment_variable_wins(monkeypatch, tmp_path):
    """The container image sets this, because nothing relative works there."""
    monkeypatch.setenv(assets.ENV_VAR, str(tmp_path))
    assert assets.assets_root() == tmp_path


def test_the_source_checkout_layout_is_the_fallback(monkeypatch):
    monkeypatch.delenv(assets.ENV_VAR, raising=False)
    root = assets.assets_root()
    assert root.name == "assets"
    assert (root / "badges").is_dir()


def test_asset_path_joins_beneath_the_root(monkeypatch, tmp_path):
    monkeypatch.setenv(assets.ENV_VAR, str(tmp_path))
    assert assets.asset_path("badges", "fonts") == tmp_path / "badges" / "fonts"


def test_the_dockerfile_sets_the_asset_root():
    """The image pip-installs the package but copies assets to /app/assets,
    so without this the badge modules cannot find their data at all."""
    from pathlib import Path

    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "AUTOPOSTER_ASSETS_ROOT=/app/assets" in dockerfile


@pytest.mark.parametrize(
    "module",
    ["autoposter.badges.values", "autoposter.badges.spec", "autoposter.badges.compose"],
)
def test_modules_import_without_reading_their_assets(module, monkeypatch, tmp_path):
    """Importing must not touch the filesystem.

    These modules are imported by the render pipeline at module level, so an
    import-time read of a missing asset stops the service from starting at
    all -- which is exactly what happened in the container image.
    """
    monkeypatch.setenv(assets.ENV_VAR, str(tmp_path / "does-not-exist"))
    assets.assets_root.cache_clear()
    for name in list(sys.modules):
        if name.startswith("autoposter.badges"):
            del sys.modules[name]

    importlib.import_module(module)  # must not raise


def test_language_lookup_reads_from_the_configured_root(monkeypatch, tmp_path):
    import json

    badges = tmp_path / "badges"
    badges.mkdir(parents=True)
    (badges / "languages.json").write_text(
        json.dumps({"xx": {"text": "XX", "country": "xx", "weight": 999}}), encoding="utf-8"
    )
    monkeypatch.setenv(assets.ENV_VAR, str(tmp_path))
    assets.assets_root.cache_clear()

    for name in list(sys.modules):
        if name.startswith("autoposter.badges"):
            del sys.modules[name]
    values = importlib.import_module("autoposter.badges.values")
    values._languages.cache_clear()

    info = values.MediaInfo(None, None, None, None, ("xx",), frozenset(), None, None, None)
    assert values.language_slots(info) == [("xx", "XX")]
