"""Rows 32 and 33a -- two more mass-op fields, under the explicit-source model.

The negative case IS the test (facts C2): with no source named, neither field
is written, and the whole edits dict must be byte-identical to today's. The
positive case only fires when config NAMES the source, because there is no
default source and no fallback chain (facts C1.2).

Row 33's other half, ``mass_added_at_update``, ships nothing: the roadmap row
names no source for it and this codebase fetches no value for Plex's addedAt.
See the STOP-and-file row filed in Step 12.
"""
import pytest

from autoposter.config.schema import OperationsConfig
from autoposter.facts.models import GatheredFacts
from autoposter.facts.tmdb_facts import parse_movie_facts, parse_show_facts
from autoposter.plex.writer import WRITABLE_BY_KIND, plan_edits


class FakeGenre:
    def __init__(self, tag):
        self.tag = tag


class FakeItem:
    """The minimum plexapi surface ``plan_edits`` reads off an item."""

    def __init__(self, kind="movie", **attrs):
        self.type = kind
        self.title = attrs.pop("title", "Heat")
        self.year = attrs.pop("year", 1995)
        self.rating = attrs.pop("rating", None)
        self.audienceRating = attrs.pop("audienceRating", None)  # noqa: N815
        self.userRating = attrs.pop("userRating", None)  # noqa: N815
        self.contentRating = attrs.pop("contentRating", None)  # noqa: N815
        self.studio = attrs.pop("studio", None)
        self.originallyAvailableAt = attrs.pop("originallyAvailableAt", None)  # noqa: N815
        self.originalTitle = attrs.pop("originalTitle", None)  # noqa: N815
        # Roadmap row 99's four override-only text fields, plus the rating key
        # the verb-collision log names. This double's attribute list is CLOSED
        # -- it ends in ``assert not attrs, attrs`` -- so a field it does not
        # pop is a construction error rather than a test failure, which is why
        # these land before any test that sets them.
        self.titleSort = attrs.pop("titleSort", None)  # noqa: N815
        self.summary = attrs.pop("summary", None)
        self.tagline = attrs.pop("tagline", None)
        self.ratingKey = attrs.pop("ratingKey", None)  # noqa: N815
        self.genres = [FakeGenre(g) for g in attrs.pop("genres", [])]
        assert not attrs, attrs


def test_user_rating_is_writable_on_every_kind():
    for kind in ("movie", "show", "season", "episode"):
        assert "user_rating" in WRITABLE_BY_KIND[kind]


def test_original_title_is_writable_on_movies_only():
    assert "original_title" in WRITABLE_BY_KIND["movie"]
    for kind in ("show", "season", "episode"):
        assert "original_title" not in WRITABLE_BY_KIND[kind]


def test_user_rating_is_written_when_it_differs():
    item = FakeItem(userRating=4.0)
    edits = plan_edits(item, GatheredFacts(user_rating=8.65))
    assert edits == {"userRating.value": 8.7, "userRating.locked": 1}


def test_user_rating_is_not_rewritten_when_it_already_matches():
    # Formatted comparison, like every other rating here: 8.65 and 8.7 both
    # format to "8.7", so rewriting one as the other would churn Plex.
    item = FakeItem(userRating=8.7)
    assert plan_edits(item, GatheredFacts(user_rating=8.65)) == {}


def test_no_user_rating_gathered_writes_nothing():
    # The negative case: an absent value must never be written as an empty one.
    item = FakeItem(userRating=4.0)
    assert plan_edits(item, GatheredFacts()) == {}


def test_original_title_is_written_when_it_differs():
    item = FakeItem(originalTitle=None)
    edits = plan_edits(item, GatheredFacts(original_title="Sen to Chihiro"))
    assert edits == {
        "originalTitle.value": "Sen to Chihiro",
        "originalTitle.locked": 1,
    }


def test_original_title_is_not_written_for_a_show():
    item = FakeItem(kind="show", originalTitle=None)
    assert plan_edits(item, GatheredFacts(original_title="Dunkles")) == {}


def test_parse_movie_facts_reads_original_title():
    facts = parse_movie_facts({"original_title": "Sen to Chihiro"})
    assert facts.original_title == "Sen to Chihiro"
    # Provenance is recorded by gather_facts, not here: the parser does not
    # know whether config named tmdb as this field's source.
    assert "original_title" not in facts.sources


def test_parse_show_facts_reads_original_name():
    facts = parse_show_facts({"original_name": "Dark"})
    assert facts.original_title == "Dark"


def test_parse_movie_facts_ignores_a_blank_original_title():
    assert parse_movie_facts({"original_title": "   "}).original_title is None
    assert parse_movie_facts({}).original_title is None


def test_an_unknown_source_key_is_a_config_load_error():
    # Global Constraint 2: an unserviceable source is a load error, never a
    # silent no-op -- the imdb_search precedent (row 81).
    with pytest.raises(Exception):
        OperationsConfig(user_rating_source="trakt")


def test_the_source_keys_default_to_unset():
    operations = OperationsConfig()
    assert operations.user_rating_source is None
    assert operations.original_title_source is None


# --- the explicit-source model at the gather seam ---------------------------

import pytest_asyncio  # noqa: E402,F401  (imported for the plugin's fixtures)

from autoposter.facts.gather import gather_facts  # noqa: E402
from autoposter.facts.mdblist import NullMDBListClient  # noqa: E402
from autoposter.plex.client import ResolvedItem  # noqa: E402


def _item(kind="movie", **over):
    base = dict(
        rating_key="1", library="Movies", kind=kind, title="Heat", year=1995,
        season_number=None, episode_number=None, root_folder="Heat (1995)",
        file_path=None, art_url=None, tmdb_id=949, tvdb_id=None,
        imdb_id="tt0113277",
    )
    base.update(over)
    return ResolvedItem(**base)


class FakeTMDB:
    """Answers ``movie``/``show`` with a fixed facts object."""

    def __init__(self, facts=None):
        self._facts = facts if facts is not None else GatheredFacts(
            audience_rating=7.6, original_title="Heat (original)",
        )

    async def movie(self, tmdb_id):
        return self._facts

    async def show(self, tmdb_id):
        return self._facts


@pytest.mark.asyncio
async def test_no_source_named_gathers_neither_field(session):
    facts = await gather_facts(session, _item(), FakeTMDB(), NullMDBListClient())
    assert facts.user_rating is None
    assert facts.original_title is None
    assert "user_rating" not in facts.sources
    assert "original_title" not in facts.sources


@pytest.mark.asyncio
async def test_tmdb_named_as_the_user_rating_source_uses_the_audience_rating(session):
    operations = OperationsConfig(user_rating_source="tmdb")
    facts = await gather_facts(
        session, _item(), FakeTMDB(), NullMDBListClient(), operations=operations
    )
    assert facts.user_rating == 7.6
    assert facts.sources["user_rating"] == "tmdb"


@pytest.mark.asyncio
async def test_imdb_named_as_the_user_rating_source_uses_no_tmdb_value(session):
    # No IMDb rating is stored for this item in the test database, so naming
    # imdb yields nothing -- and must NOT silently fall back to TMDb's, which
    # is the whole point of the explicit-source model.
    operations = OperationsConfig(user_rating_source="imdb")
    facts = await gather_facts(
        session, _item(), FakeTMDB(), NullMDBListClient(), operations=operations
    )
    assert facts.user_rating is None
    assert "user_rating" not in facts.sources


@pytest.mark.asyncio
async def test_tmdb_named_as_the_original_title_source_keeps_the_parsed_value(session):
    operations = OperationsConfig(original_title_source="tmdb")
    facts = await gather_facts(
        session, _item(), FakeTMDB(), NullMDBListClient(), operations=operations
    )
    assert facts.original_title == "Heat (original)"
    assert facts.sources["original_title"] == "tmdb"


@pytest.mark.asyncio
async def test_an_unnamed_original_title_source_strips_the_parsed_value(session):
    # The parser always reads it (it is free -- the payload is already
    # fetched); the source key is what decides whether it survives the gather.
    facts = await gather_facts(session, _item(), FakeTMDB(), NullMDBListClient())
    assert facts.original_title is None
