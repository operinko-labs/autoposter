"""Adoption configuration.

Asserts the *schema* defaults, not the values loaded from the example YAML --
see ``tests/test_scheduler_config.py``'s docstring for why: change a default
in ``schema.py`` to whatever the example already says and a test against the
loaded config would still pass, which is precisely the case where a
deployment with no ``adopt:`` block silently changes behaviour.
"""
from autoposter.config.schema import AdoptConfig


def test_adopt_apply_defaults_to_false():
    """Adoption is a dry run until the operator has read the report and
    opted in -- the same posture as cleanup.apply and badges.upload_to_plex."""
    assert AdoptConfig().apply is False


def test_adopt_libraries_defaults_to_movies_and_tv_shows():
    assert AdoptConfig().libraries == ["Movies", "TV Shows"]
