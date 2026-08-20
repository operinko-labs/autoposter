from datetime import date
from urllib.parse import unquote

import pytest

from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import WRITABLE_BY_KIND, apply_facts, plan_edits


class FakeItem:
    """Models the plexapi surface the writer touches.

    The genre handling here models plexapi's *real* wire behaviour, not the
    behaviour we'd wish it had: a single-item indexed tag write
    (`genre[0].tag.tag=...`) MERGES with the item's existing genres rather
    than replacing them, and does not dedupe -- sending a value that's
    already present creates a second copy. This was verified empirically
    against a production Plex server. An earlier version of this double
    modeled `addGenre` as a set/replace operation, which hid the bug where
    `apply_facts` could only ever grow the genre list, never correct it.
    """

    def __init__(self, kind="movie", **attrs):
        self.type = kind
        self.rating = attrs.get("rating")
        self.audienceRating = attrs.get("audienceRating")
        self.contentRating = attrs.get("contentRating")
        self.studio = attrs.get("studio")
        self.originallyAvailableAt = attrs.get("originallyAvailableAt")
        self.genres = [type("G", (), {"tag": g})() for g in attrs.get("genres", [])]
        self.batched = False
        self.saved = False
        self.edits = {}

    def batchEdits(self):
        self.batched = True
        return self

    def saveEdits(self):
        self.saved = True
        return self

    def edit(self, **kwargs):
        self.edits.update(kwargs)
        for key, value in kwargs.items():
            if key.startswith("genre[") and key.endswith("].tag.tag"):
                # Merge, not replace -- and no dedupe, matching the real
                # server's behaviour for indexed tag writes.
                self.genres.append(type("G", (), {"tag": value})())
            elif key == "genre[].tag.tag-":
                removed = {unquote(v) for v in value.split(",")}
                self.genres = [g for g in self.genres if g.tag not in removed]
        return self


def test_plan_includes_changed_fields_only():
    item = FakeItem(rating=4.9, audienceRating=6.3)
    facts = GatheredFacts(critic_rating=4.9, audience_rating=7.0)
    edits = plan_edits(item, facts)
    assert "rating.value" not in edits
    assert edits["audienceRating.value"] == pytest.approx(7.0)


def test_plan_is_empty_when_nothing_changed():
    item = FakeItem(rating=4.9, audienceRating=6.3, contentRating="17")
    facts = GatheredFacts(critic_rating=4.9, audience_rating=6.3, content_rating="17")
    assert plan_edits(item, facts) == {}


def test_ratings_are_compared_on_the_formatted_value():
    """8.65 and 8.6 both render '86%', so they are not a change worth writing."""
    item = FakeItem(audienceRating=8.6)
    assert plan_edits(item, GatheredFacts(audience_rating=8.65)) == {}


def test_none_values_are_never_written():
    item = FakeItem(rating=4.9, contentRating="17")
    assert plan_edits(item, GatheredFacts()) == {}


def test_every_written_field_is_locked():
    item = FakeItem()
    edits = plan_edits(item, GatheredFacts(critic_rating=4.9, content_rating="17"))
    assert edits["rating.locked"] == 1
    assert edits["contentRating.locked"] == 1


def test_zero_rating_is_written_rather_than_clearing_the_field():
    """plexapi's editField sends `value or ''`, so 0.0 would blank the field."""
    item = FakeItem(rating=4.9)
    edits = plan_edits(item, GatheredFacts(critic_rating=0.0))
    assert edits["rating.value"] == 0.0


def test_seasons_accept_only_ratings():
    assert WRITABLE_BY_KIND["season"] == {"critic_rating", "audience_rating"}


def test_episodes_do_not_accept_genres_or_studio():
    assert "genres" not in WRITABLE_BY_KIND["episode"]
    assert "studio" not in WRITABLE_BY_KIND["episode"]
    assert "content_rating" in WRITABLE_BY_KIND["episode"]


def test_season_plan_ignores_inapplicable_fields():
    item = FakeItem(kind="season")
    edits = plan_edits(item, GatheredFacts(content_rating="17", studio="A24",
                                           critic_rating=4.9))
    assert edits["rating.value"] == pytest.approx(4.9)
    assert "contentRating.value" not in edits
    assert "studio.value" not in edits


def test_date_is_formatted_for_plex():
    item = FakeItem()
    edits = plan_edits(item, GatheredFacts(originally_available=date(2023, 5, 12)))
    assert edits["originallyAvailableAt.value"] == "2023-05-12"


async def test_apply_uses_a_single_batched_call():
    item = FakeItem()
    written = await apply_facts(item, GatheredFacts(critic_rating=4.9, content_rating="17"))
    assert item.batched and item.saved
    assert written["rating.value"] == pytest.approx(4.9)


async def test_apply_does_nothing_when_there_is_nothing_to_write():
    item = FakeItem(rating=4.9)
    written = await apply_facts(item, GatheredFacts(critic_rating=4.9))
    assert written == {}
    assert item.batched is False


async def test_genres_are_applied_through_edit():
    item = FakeItem(genres=["Horror"])
    written = await apply_facts(item, GatheredFacts(genres=["Horror", "Drama"]))
    assert written["genre[0].tag.tag"] == "Drama"
    assert written["genre.locked"] == 1
    assert "genre[].tag.tag-" not in written
    assert sorted(g.tag for g in item.genres) == ["Drama", "Horror"]


async def test_genres_that_drop_one_do_not_survive():
    """Regression test for the concatenating-addGenre bug: the old
    implementation could only grow the genre list, so a target that drops
    a currently-held genre would still leave that genre in place (and
    duplicate any that were kept). This must actually remove it.
    """
    item = FakeItem(genres=["Horror", "Comedy"])
    written = await apply_facts(item, GatheredFacts(genres=["Horror", "Drama"]))
    assert sorted(g.tag for g in item.genres) == ["Drama", "Horror"]
    assert written["genre[0].tag.tag"] == "Drama"
    assert unquote(written["genre[].tag.tag-"]) == "Comedy"


async def test_genre_only_change_is_reported_in_the_return_value():
    """Finding 2 regression: a genre-only write must not report {} / log 0
    fields written, since a real write did happen.
    """
    item = FakeItem(rating=4.9, genres=["Horror"])
    written = await apply_facts(
        item, GatheredFacts(critic_rating=4.9, genres=["Horror", "Drama"])
    )
    assert written != {}
    assert written["genre[0].tag.tag"] == "Drama"
