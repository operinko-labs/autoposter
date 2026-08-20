from datetime import date

import pytest

from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import WRITABLE_BY_KIND, apply_facts, plan_edits


class FakeItem:
    """Models the plexapi surface the writer touches.

    `addGenre`/`removeGenre` mirror documented plexapi behaviour
    (plexapi.mixins.GenreMixin, built on EditTagsMixin.editTags): `addGenre`
    MERGES with the item's current genres rather than replacing them, and
    does not dedupe -- passing a tag that's already present adds a second
    copy. This was verified offline against the real plexapi `Movie` class
    (see test_genre_payload_has_no_conflicting_directives_against_real_plexapi
    below). An earlier version of this double modeled genre edits as a
    set/replace operation, which hid the bug where `apply_facts` could only
    ever grow the genre list, never correct it.
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
        return self

    def addGenre(self, genres, locked=True):
        # Merge, not replace -- and no dedupe, matching plexapi's documented
        # GenreMixin.addGenre behaviour.
        if not isinstance(genres, list):
            genres = [genres]
        self.genres = self.genres + [type("G", (), {"tag": g})() for g in genres]
        self.edits["genre.locked"] = 1 if locked else 0
        return self

    def removeGenre(self, genres, locked=True):
        if not isinstance(genres, list):
            genres = [genres]
        removed = set(genres)
        self.genres = [g for g in self.genres if g.tag not in removed]
        self.edits["genre.locked"] = 1 if locked else 0
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


async def test_genres_are_applied_through_addgenre():
    item = FakeItem(genres=["Horror"])
    written = await apply_facts(item, GatheredFacts(genres=["Horror", "Drama"]))
    assert written["genres.added"] == ["Drama"]
    assert written["genres.locked"] == 1
    assert "genres.removed" not in written
    # addGenre's documented merge reads the *current* genres live; re-listing
    # "Horror" (unchanged) would create a duplicate tag on the server, since
    # plexapi's indexed tag write does not dedupe. To avoid that,
    # _apply_genre_edits resets the cached genres before calling addGenre so
    # only the genuinely new tag is queued -- which intentionally leaves the
    # local cache incomplete (Horror is still on the server, untouched; it's
    # just no longer reflected here without a reload()). What actually
    # matters -- the queued payload -- is asserted above and proven
    # conflict-free against real plexapi in the test below.
    assert [g.tag for g in item.genres] == ["Drama"]


async def test_genres_that_drop_one_do_not_survive():
    """Regression test for the concatenating-addGenre bug: the old
    implementation could only grow the genre list, so a target that drops
    a currently-held genre would still leave that genre in place (and
    duplicate any that were kept). This must actually remove it.
    """
    item = FakeItem(genres=["Horror", "Comedy"])
    written = await apply_facts(item, GatheredFacts(genres=["Horror", "Drama"]))
    assert "Comedy" not in [g.tag for g in item.genres]
    assert written["genres.added"] == ["Drama"]
    assert written["genres.removed"] == ["Comedy"]


async def test_genre_only_change_is_reported_in_the_return_value():
    """Finding 2 regression: a genre-only write must not report {} / log 0
    fields written, since a real write did happen.
    """
    item = FakeItem(rating=4.9, genres=["Horror"])
    written = await apply_facts(
        item, GatheredFacts(critic_rating=4.9, genres=["Horror", "Drama"])
    )
    assert written != {}
    assert written["genres.added"] == ["Drama"]


def test_genre_payload_has_no_conflicting_directives_against_real_plexapi():
    """Offline regression test against plexapi's *real* `Movie`/`GenreMixin`
    classes -- no server connection is made or needed, since `batchEdits()`
    causes all subsequent edit calls to accumulate into `item._edits`
    without any network I/O; only `saveEdits()` would perform a request, and
    this test never calls it.

    This guards against a real gotcha found while implementing the
    documented-API rewrite: `addGenre`'s "merge with existing genres" reads
    the item's *current* `genres` property live off its original XML data,
    unaffected by a `removeGenre` queued earlier in the same batch. Naively
    chaining `removeGenre(surplus)` + `addGenre(missing)` -- the pattern the
    plexapi docs themselves suggest for "set the genres to exactly this
    list" -- would silently re-list the surplus genre as an explicit add in
    the very request that removes it, sending contradictory directives for
    the same tag. `writer._apply_genre_edits` avoids this by resetting the
    cached `genres` to `[]` immediately before `addGenre`; this test proves
    that fix against plexapi's actual merge logic, not a model of it.
    """
    import xml.etree.ElementTree as ET

    from plexapi.video import Movie

    from autoposter.plex.writer import _apply_genre_edits

    xml = (
        '<Video ratingKey="1" key="/library/metadata/1" type="movie" title="T">'
        '<Genre tag="Horror" /><Genre tag="Comedy" /></Video>'
    )
    item = Movie(server=None, data=ET.fromstring(xml))
    assert sorted(g.tag for g in item.genres) == ["Comedy", "Horror"]

    item.batchEdits()
    _apply_genre_edits(item, additions=["Drama"], removals=["Comedy"])

    # The accumulated payload, exactly as it would be sent to the server.
    edits = item._edits
    assert edits["genre[].tag.tag-"] == "Comedy"
    # Exactly one indexed add, for the genuinely new tag -- no entry
    # resurrecting "Comedy" and no duplicate entry for "Horror".
    add_values = {v for k, v in edits.items() if k.startswith("genre[") and k.endswith("].tag.tag")}
    assert add_values == {"Drama"}
    assert edits["genre.locked"] == 1

    # No network call was made: saveEdits() was never invoked, and the
    # object still reports batch-edit mode is active (a dict, not None).
    assert isinstance(item._edits, dict)
