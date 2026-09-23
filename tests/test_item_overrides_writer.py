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
from datetime import date

from autoposter.config.schema import OperationsConfig
from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import (
    _PLEX_FIELD_NAMES,
    WRITABLE_BY_KIND,
    override_edits,
    plan_edits,
)

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
    # 13 since roadmap row 227 added ``added_at`` to the movie set (and to no
    # other kind), which is exactly the deliberate change this count exists to
    # make someone state out loud.
    assert len(WRITABLE_BY_KIND["movie"]) == 13
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


class LockableItem(FakeItem):
    """A ``FakeItem`` that also reports Plex's per-field lock state.

    The same shape ``tests/test_mass_ops_verbs.py`` defines for row 87's verb
    tests, written out here rather than imported so this file reads on its
    own and so a change to the verb suite's private double cannot silently
    move row 99's precedence pins. The ATTRIBUTE half is still the one shared
    ``FakeItem`` -- widened at Step 1 -- because that is the double three
    modules already agree on.
    """

    def __init__(self, kind="movie", locks=(), **attrs):
        super().__init__(kind, **attrs)
        self.fields = [
            type("F", (), {"name": name, "locked": locked})()
            for name, locked in locks
        ]


# --- override_edits --------------------------------------------------------


def test_no_overrides_produces_nothing():
    assert override_edits(FakeItem(), {}) == {}


def test_a_text_override_is_written_and_locked():
    item = FakeItem(tagline="Old words")
    assert override_edits(item, {"tagline": "New words"}) == {
        "tagline.value": "New words", "tagline.locked": 1,
    }


def test_a_text_override_equal_to_plex_and_already_locked_writes_nothing():
    """The idempotence that makes this steady-state rather than a rewrite
    every pass -- inherited from ``plan_edits``' own diff, not reinvented.
    Genuinely nothing here: the value matches AND Plex already reports the
    field locked, so there is nothing left for this override to ensure."""
    item = LockableItem(tagline="Same", locks=[("tagline", True)])
    assert override_edits(item, {"tagline": "Same"}) == {}


def test_a_text_override_equal_to_plex_but_unlocked_still_writes_the_lock():
    """The value already matches, but the field is NOT locked -- exactly the
    state Plex's own agent is free to rewrite on its next refresh, clobbering
    the value the operator pinned before a later pass would notice the drift
    and write-and-lock it. So the write still happens, carrying the lock
    alone and no value key."""
    item = LockableItem(tagline="Same", locks=[])
    assert override_edits(item, {"tagline": "Same"}) == {"tagline.locked": 1}


def test_the_lock_only_write_above_is_idempotent():
    """Second pass, after the lock: Plex now reports ``tagline`` locked, so
    this is back to genuinely nothing -- the fix costs one write, not one
    every pass."""
    item = LockableItem(tagline="Same", locks=[("tagline", True)])
    assert override_edits(item, {"tagline": "Same"}) == {}


def test_a_rating_override_compares_on_the_formatted_value():
    """8.65 and 8.7 both render "8.7" to a viewer, so rewriting one as the
    other would churn Plex for no visible gain -- the same rule
    ``plan_edits`` applies to a provider's rating. Locked already, so the
    equal case is genuinely nothing (the lock-ensure guard only adds
    a write when the field is unlocked -- pinned on the text field above)."""
    locked = LockableItem(rating=8.7, locks=[("rating", True)])
    assert override_edits(locked, {"critic_rating": 8.7}) == {}
    assert override_edits(FakeItem(rating=4.9), {"critic_rating": 8.7}) == {
        "rating.value": 8.7, "rating.locked": 1,
    }


def test_a_rating_override_within_the_same_formatted_value_writes_no_value():
    """Regression pin: replacing the FORMATTED compare with a raw
    ``!=`` would treat Plex's 7.04 and the operator's 7.0 as different --
    both render "7.0" -- and rewrite the same rating every pass. Locked
    already, so the only observable outcome of the formatted-equal check is
    the absence of ``rating.value`` (the lock rule still applies:
    an unlocked item here would instead get the lock-only edit, as pinned
    for text above)."""
    item = LockableItem(rating=7.04, locks=[("rating", True)])
    assert override_edits(item, {"critic_rating": 7.0}) == {}


def test_an_audience_rating_override_uses_the_audience_formatter():
    locked = LockableItem(audienceRating=6.3, locks=[("audienceRating", True)])
    assert override_edits(locked, {"audience_rating": 6.3}) == {}
    assert override_edits(
        FakeItem(audienceRating=6.3), {"audience_rating": 9.1}
    ) == {"audienceRating.value": 9.1, "audienceRating.locked": 1}


def test_an_audience_rating_override_this_writer_stored_is_never_rewritten():
    """The override path's copy of the plan_edits fix: an override of 8.68 is
    stored as 8.7, and 8.7 must then compare equal to 8.68 -- the truncating
    formatter on the raw override said '86%' against Plex's '87%' forever."""
    locked = LockableItem(audienceRating=8.7, locks=[("audienceRating", True)])
    assert override_edits(locked, {"audience_rating": 8.68}) == {}


def test_a_date_override_compares_on_the_iso_string():
    locked = LockableItem(
        originallyAvailableAt=date(1995, 12, 15),
        locks=[("originallyAvailableAt", True)],
    )
    assert override_edits(locked, {"originally_available": date(1995, 12, 15)}) == {}
    item = FakeItem(originallyAvailableAt=date(1995, 12, 15))
    assert override_edits(item, {"originally_available": date(1996, 1, 1)}) == {
        "originallyAvailableAt.value": "1996-01-01",
        "originallyAvailableAt.locked": 1,
    }


def test_a_genre_override_is_SYNC_and_not_an_add():
    """The override IS the list. ``_genre_plan`` computes the additions and
    the removals that make Plex's genres exactly this, which is upstream's
    ``genre.sync`` rather than its bare ``genre``."""
    # Plain STRINGS: ``FakeItem`` wraps each one in its own ``FakeGenre``
    # (``self.genres = [FakeGenre(g) for g in attrs.pop("genres", [])]``), so
    # passing pre-wrapped tag objects would double-wrap them and make
    # ``_current_genres`` yield wrapper instances instead of names. Every
    # existing genre test in that module passes strings for the same reason.
    item = FakeItem(genres=["Drama", "Romance"])
    plan = override_edits(item, {"genres": ["Crime", "Drama"]})
    assert plan["genres.added"] == ["Crime"]
    assert plan["genres.removed"] == ["Romance"]


def test_a_genre_override_matching_plex_locks_the_genre_field():
    """Roadmap row 246, and the rename is the point: this used to assert that
    an override matching Plex wrote NOTHING. An equal list Plex does not
    report locked now writes exactly one key -- the SINGULAR ``genre.locked``,
    ``_PLEX_FIELD_NAMES``' one asymmetric entry -- and nothing else. Same
    guarantee the four scalar branches below have carried since row 99."""
    item = FakeItem(genres=["Drama", "Crime"])
    assert override_edits(item, {"genres": ["Crime", "Drama"]}) == {"genre.locked": 1}


def test_a_genre_override_matching_an_already_locked_plex_writes_nothing():
    """Steady state: Plex reports the genre field locked, so there is nothing
    left to do and the second pass writes nothing."""
    item = LockableItem(genres=["Drama", "Crime"], locks=[("genre", True)])
    assert override_edits(item, {"genres": ["Crime", "Drama"]}) == {}


def test_a_field_the_kind_cannot_carry_is_skipped_rather_than_written():
    """Belt and braces behind the endpoint's 422: a season row for
    ``tagline`` (hand-inserted, or left behind by a kind change) must not
    reach a Plex object that has no such attribute."""
    assert override_edits(FakeItem(kind="season"), {"tagline": "x"}) == {}


# --- precedence ------------------------------------------------------------


def test_an_overridden_field_drops_out_of_the_provider_value_path():
    """C4's one-line extension of row 87's seam: an override IS a source, so
    the two can never both touch a field in one payload."""
    item = FakeItem(rating=4.9, studio="MGM")
    edits = plan_edits(
        item,
        GatheredFacts(critic_rating=2.2, studio="Warner"),
        overrides={"critic_rating": 8.7},
    )
    assert edits["rating.value"] == 8.7          # the operator's, not 2.2
    assert edits["studio.value"] == "Warner"     # untouched: no override


def test_an_override_fires_when_the_provider_has_nothing_at_all():
    """The whole point of the row. An item no provider has anything for still
    gets the operator's value."""
    item = FakeItem(tagline=None)
    assert plan_edits(item, GatheredFacts(), overrides={"tagline": "x"}) == {
        "tagline.value": "x", "tagline.locked": 1,
    }


def test_a_second_pass_over_an_applied_override_writes_nothing():
    """Steady state through the real planner, not just through
    ``override_edits`` -- on an item Plex already reports BOTH fields
    locked, i.e. the state the first pass's write would have left behind
    (a second pass over an unlocked-but-equal item is pinned separately,
    above, on ``override_edits`` directly)."""
    item = LockableItem(
        tagline="x", rating=8.7, locks=[("tagline", True), ("rating", True)],
    )
    assert plan_edits(
        item, GatheredFacts(critic_rating=2.2),
        overrides={"tagline": "x", "critic_rating": 8.7},
    ) == {}


def test_an_override_beats_a_verb_and_the_verb_is_skipped_for_that_item(caplog):
    """C4's collision rule. Library-wide ``field_verbs`` say what happens to a
    field everywhere; a per-item override says what happens to it HERE, and
    here wins. Logged at INFO with the rating key and the field and nothing
    else -- the value is operator-typed free text (row 213)."""
    import logging

    item = LockableItem(rating=4.9, locks=[("rating", True)], ratingKey="12345")
    operations = OperationsConfig(
        field_verbs={"critic_rating": "unlock"}, unlock_apply=True,
    )
    with caplog.at_level(logging.INFO):
        edits = plan_edits(
            item, GatheredFacts(), operations, overrides={"critic_rating": 8.7},
        )

    assert edits == {"rating.value": 8.7, "rating.locked": 1}
    assert "rating.locked" in edits and edits["rating.locked"] == 1  # not the unlock's 0
    assert "critic_rating" in caplog.text
    assert "12345" in caplog.text
    assert "8.7" not in caplog.text


def test_a_verb_on_an_un_overridden_field_still_fires():
    """The collision rule is per FIELD, not per item: overriding the tagline
    must not disarm a studio verb."""
    item = LockableItem(studio="Warner", locks=[("studio", False)], tagline=None)
    operations = OperationsConfig(field_verbs={"studio": "lock"}, lock_apply=True)
    edits = plan_edits(item, GatheredFacts(), operations, overrides={"tagline": "x"})
    assert edits["studio.locked"] == 1
    assert edits["tagline.value"] == "x"


def test_the_default_call_is_byte_identical_to_before_this_row():
    """Gate-off, and every existing caller: ``overrides`` defaults to None and
    ``plan_edits`` then behaves exactly as it did, which is what makes this
    row invisible to a deployment that never turns it on."""
    item = FakeItem(rating=4.9, studio="MGM")
    facts = GatheredFacts(critic_rating=8.7, studio="Warner")
    assert plan_edits(item, facts) == plan_edits(item, facts, overrides={})
    assert plan_edits(item, facts, overrides=None) == {
        "rating.value": 8.7, "rating.locked": 1,
        "studio.value": "Warner", "studio.locked": 1,
    }
