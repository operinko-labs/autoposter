"""Row 34 -- genre and content-rating mappers.

One normalize step AHEAD of the existing diff, not a second write path: the
mapped value is what the diff compares and what is written, so an item already
holding the mapped value is left alone instead of being rewritten every pass.

The negative case is the test: with both mappers empty (the default), every
plan_edits result must be byte-identical to the pre-phase one.
"""
from autoposter.config.schema import OperationsConfig
from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import map_value, map_values, plan_edits

from test_mass_ops_fields import FakeItem


def test_map_value_passes_an_unnamed_value_through():
    assert map_value({"TV-MA": "18"}, "R") == "R"


def test_map_value_rewrites_a_named_value():
    assert map_value({"TV-MA": "18"}, "TV-MA") == "18"


def test_map_value_leaves_none_alone():
    assert map_value({"TV-MA": "18"}, None) is None


def test_map_value_with_an_empty_mapping_is_the_identity():
    assert map_value({}, "TV-MA") == "TV-MA"


def test_map_values_rewrites_only_what_is_named_and_keeps_order():
    mapping = {"Sci-Fi & Fantasy": "Sci-Fi"}
    assert map_values(mapping, ["Drama", "Sci-Fi & Fantasy", "Crime"]) == [
        "Drama", "Sci-Fi", "Crime",
    ]


def test_map_values_deduplicates_when_two_genres_map_onto_one():
    mapping = {"Sci-Fi & Fantasy": "Sci-Fi", "Science Fiction": "Sci-Fi"}
    assert map_values(mapping, ["Science Fiction", "Sci-Fi & Fantasy"]) == ["Sci-Fi"]


def test_map_values_matches_case_sensitively():
    # An operator's hand-written table is exact: folding would silently merge
    # two keys they meant to keep apart.
    assert map_values({"Sci-Fi": "SF"}, ["sci-fi"]) == ["sci-fi"]


def test_the_content_rating_mapper_normalises_before_the_diff():
    item = FakeItem(contentRating="18")
    operations = OperationsConfig(content_rating_mapper={"TV-MA": "18"})
    # Plex already holds the MAPPED value, so nothing is written -- proving
    # the mapping runs before the comparison and not after it.
    assert plan_edits(item, GatheredFacts(content_rating="TV-MA"), operations) == {}


def test_the_content_rating_mapper_writes_the_mapped_value():
    item = FakeItem(contentRating="R")
    operations = OperationsConfig(content_rating_mapper={"TV-MA": "18"})
    edits = plan_edits(item, GatheredFacts(content_rating="TV-MA"), operations)
    assert edits == {"contentRating.value": "18", "contentRating.locked": 1}


def test_the_genre_mapper_normalises_before_the_diff():
    item = FakeItem(genres=["Sci-Fi", "Drama"])
    operations = OperationsConfig(genre_mapper={"Sci-Fi & Fantasy": "Sci-Fi"})
    facts = GatheredFacts(genres=["Sci-Fi & Fantasy", "Drama"])
    # Plex already holds the MAPPED list, so no add and no removal is planned
    # -- which is what proves the mapping runs before the comparison and not
    # after it. The one key written is roadmap row 246's equal-list lock, and
    # a lock is not a genre CHANGE: no ``genres.added``/``genres.removed``.
    assert plan_edits(item, facts, operations) == {"genre.locked": 1}


def test_the_genre_mapper_writes_the_mapped_genre():
    item = FakeItem(genres=["Drama"])
    operations = OperationsConfig(genre_mapper={"Sci-Fi & Fantasy": "Sci-Fi"})
    facts = GatheredFacts(genres=["Sci-Fi & Fantasy", "Drama"])
    edits = plan_edits(item, facts, operations)
    # No lock key in the plan: roadmap row 246 deleted the dead plural
    # ``genres.locked``, which ``apply_facts`` stripped out of every payload it
    # ever built. A real change locks through ``locked=True`` on the
    # ``addGenre``/``removeGenre`` calls instead.
    assert edits == {"genres.added": ["Sci-Fi"]}


def test_no_mapper_configured_is_byte_identical_to_no_operations_at_all():
    # Global Constraint 1, asserted on the WHOLE dict both ways.
    item = FakeItem(contentRating="R", genres=["Drama"])
    facts = GatheredFacts(content_rating="TV-MA", genres=["Sci-Fi & Fantasy"])
    assert plan_edits(item, facts, OperationsConfig()) == plan_edits(item, facts)


def test_the_mappers_default_empty():
    operations = OperationsConfig()
    assert operations.genre_mapper == {}
    assert operations.content_rating_mapper == {}
