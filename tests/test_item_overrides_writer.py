"""Roadmap row 99 -- the writer half: the field set, ``override_edits``, and
the precedence subtraction.

The write path itself is row 87's and row 32/33/34's, already shipped and
already tested: per-field locks, a diff against what Plex currently reports,
one batched HTTP call. What this file pins is the three things row 99 adds --
four more writable fields, an operator's value as a SOURCE, and the rule that
a field with an override drops out of every other source's path.

The import block below names only what the field-set tests use. `ruff check .`
covers `tests/` (F401), and this file is committed twice -- once after the
field set lands and once after `override_edits` does -- so an import for a
symbol a later step introduces would fail lint at the first of those commits
as well as failing collection at its RED.
"""
from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import _PLEX_FIELD_NAMES, WRITABLE_BY_KIND, plan_edits

from test_mass_ops_fields import FakeItem


# --- C3's field set --------------------------------------------------------


def test_the_four_text_fields_are_writable_where_plexapi_says_they_are():
    """The per-libtype matrix, transcribed from
    ``plexapi/mixins/__init__.py:35-70`` -- which is also what Kometa's
    ``add_edit`` writes through. A season has no ``titleSort`` and no
    ``tagline``; an episode has no ``tagline``."""
    assert {"title", "sort_title", "summary", "tagline"} <= WRITABLE_BY_KIND["movie"]
    assert {"title", "sort_title", "summary", "tagline"} <= WRITABLE_BY_KIND["show"]
    assert {"title", "summary"} <= WRITABLE_BY_KIND["season"]
    assert "sort_title" not in WRITABLE_BY_KIND["season"]
    assert "tagline" not in WRITABLE_BY_KIND["season"]
    assert {"title", "sort_title", "summary"} <= WRITABLE_BY_KIND["episode"]
    assert "tagline" not in WRITABLE_BY_KIND["episode"]


def test_the_writable_sets_are_exactly_these_sizes():
    """A count rather than a membership test, so that a field added by
    accident is caught here rather than discovered in a backup file."""
    assert len(WRITABLE_BY_KIND["movie"]) == 12
    assert len(WRITABLE_BY_KIND["show"]) == 11
    assert len(WRITABLE_BY_KIND["season"]) == 5
    assert len(WRITABLE_BY_KIND["episode"]) == 8


def test_every_writable_field_has_a_plex_name():
    """The invariant ``tests/test_mass_ops_verbs.py`` already asserts, restated
    here because this task is what could break it: a field in the writable set
    with no ``_PLEX_FIELD_NAMES`` row can be configured and never written."""
    assert set().union(*WRITABLE_BY_KIND.values()) <= set(_PLEX_FIELD_NAMES)


def test_the_four_new_plex_names_are_plexapis_own():
    assert _PLEX_FIELD_NAMES["title"] == ("title", "title")
    assert _PLEX_FIELD_NAMES["sort_title"] == ("titleSort", "titleSort")
    assert _PLEX_FIELD_NAMES["summary"] == ("summary", "summary")
    assert _PLEX_FIELD_NAMES["tagline"] == ("tagline", "tagline")


def test_the_four_new_fields_have_no_provider_source_and_are_never_written_from_facts():
    """C4: the four text fields flow through ``override_edits`` ONLY.
    ``GatheredFacts`` carries no title, sort title, summary or tagline, and
    this task adds no branch to ``plan_edits``' value path for them -- so an
    item with no override writes exactly what it wrote before this row."""
    item = FakeItem(title="Old", titleSort="Old", summary="Old", tagline="Old")
    assert plan_edits(item, GatheredFacts(critic_rating=8.7)) == {
        "rating.value": 8.7, "rating.locked": 1,
    }
