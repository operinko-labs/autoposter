"""Collections configuration.

``Config()`` with no arguments cannot be built directly -- ``assets_root``,
``plex``, ``providers`` and ``artwork`` are all required fields with no
defaults, independent of anything in this phase. The existing badges config
tests (see conftest.py's ``config_with_badges`` and friends) work around this
by loading the example config instead, so these tests do the same.
"""
from pathlib import Path

from autoposter.config.loader import load_config

EXAMPLE_CONFIG = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def test_collections_default_to_reporting_without_writing():
    """Consistent with operations.write_to_plex and badges.upload_to_plex:
    nothing reaches the live server until the operator opts in."""
    config = load_config(EXAMPLE_CONFIG)
    assert config.collections.enabled is True
    assert config.collections.apply_to_plex is False


def test_the_ownership_label_defaults_to_our_own_name():
    """It must not be 'Kometa' -- the tool being replaced uses that label,
    and sharing it would make both tools claim the same collections."""
    config = load_config(EXAMPLE_CONFIG)
    assert config.collections.ownership_label == "autoposter"
    assert config.collections.ownership_label != "Kometa"


def test_both_libraries_are_configured_by_default():
    assert load_config(EXAMPLE_CONFIG).collections.libraries == ["Movies", "TV Shows"]


def test_the_separator_toggle_defaults_to_enabled():
    assert load_config(EXAMPLE_CONFIG).collections.separators is True
