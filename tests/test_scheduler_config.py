"""Scheduler configuration.

``Config()`` cannot be built directly -- see ``tests/test_collection_config.py``
for why -- so these tests load the example config, the same way.
"""
from pathlib import Path

from autoposter.config.loader import load_config

EXAMPLE_CONFIG = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def test_scheduler_defaults():
    config = load_config(EXAMPLE_CONFIG)
    assert config.scheduler.enabled is True
    assert config.scheduler.poll_seconds == 60
    assert config.scheduler.collections_hours == 24
    assert config.scheduler.drift_days == 7
    assert config.scheduler.drift_max_age_days == 7
    assert config.scheduler.cleanup_days == 7


def test_drift_batch_size_defaults_to_500():
    """The safety valve that stops a sweep enqueuing all ~16,000 items at once."""
    config = load_config(EXAMPLE_CONFIG)
    assert config.scheduler.drift_batch_size == 500


def test_cleanup_apply_defaults_to_false():
    """The asset cleanup moves the operator's files, so it must stay a dry
    run until explicitly opted into. This lives on ``cleanup.apply``, not a
    ``scheduler.cleanup_apply`` field -- see ``SchedulerConfig``'s docstring
    in config/schema.py for why the two are not duplicated."""
    config = load_config(EXAMPLE_CONFIG)
    assert config.cleanup.apply is False
