"""Scheduler configuration.

These assert the *schema* defaults, not the values the example config
happens to carry. Loading the example and checking what came back tests the
YAML, not the default: change a default in ``schema.py`` to whatever the
example already says and such a test still passes, which is precisely the
case where a deployment with no ``scheduler:`` block silently changes
behaviour. ``SchedulerConfig()`` and ``CleanupConfig()`` construct with no
arguments (unlike ``Config()`` -- see ``tests/test_collection_config.py``),
so the defaults can be read straight off the models.

``test_the_example_agrees_with_the_schema_defaults`` then pins the two
together, so the documented example cannot quietly drift away from what an
operator who omits the block actually gets.
"""
from pathlib import Path

from autoposter.config.loader import load_config
from autoposter.config.schema import CleanupConfig, SchedulerConfig

EXAMPLE_CONFIG = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def test_scheduler_defaults():
    defaults = SchedulerConfig()
    assert defaults.enabled is True
    assert defaults.poll_seconds == 60
    assert defaults.collections_hours == 24
    assert defaults.drift_days == 7
    assert defaults.drift_max_age_days == 7
    assert defaults.cleanup_days == 7


def test_drift_batch_size_defaults_to_500():
    """The safety valve that stops a sweep enqueuing all ~16,000 items at once."""
    assert SchedulerConfig().drift_batch_size == 500


def test_cleanup_apply_defaults_to_false():
    """The asset cleanup moves the operator's files, so it must stay a dry
    run until explicitly opted into. This lives on ``cleanup.apply``, not a
    ``scheduler.cleanup_apply`` field -- see ``SchedulerConfig``'s docstring
    in config/schema.py for why the two are not duplicated."""
    assert CleanupConfig().apply is False


def test_cleanup_safety_caps_have_defaults():
    """The caps that stop a repointed ``assets_root`` reading as "the whole
    library is garbage" must apply to a deployment that never sets them."""
    defaults = CleanupConfig()
    assert defaults.max_orphans == 500
    assert defaults.max_orphan_share == 0.25


def test_the_example_agrees_with_the_schema_defaults():
    """The example documents the defaults, so it must not contradict them."""
    config = load_config(EXAMPLE_CONFIG)
    assert config.scheduler == SchedulerConfig()
    assert config.cleanup == CleanupConfig()
