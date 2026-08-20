from pathlib import Path

import pytest
from plexapi.exceptions import NotFound as PlexNotFound

from autoposter.config.loader import load_config
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ItemNotFound, PlexClient, parse_guids
from autoposter.render.pipeline import title_text_for

EXAMPLE_CONFIG = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def test_parse_guids_extracts_all_three_agents():
    guids = parse_guids(["tmdb://693134", "imdb://tt15239678", "tvdb://371980"])
    assert guids == {"tmdb": "693134", "imdb": "tt15239678", "tvdb": "371980"}


def test_parse_guids_parses_legacy_prefixed_tokens():
    guids = parse_guids(["com.plexapp.agents.imdb://tt123?lang=en", "tmdb://5"])
    assert guids == {"imdb": "tt123", "tmdb": "5"}


def test_parse_guids_on_empty_input():
    assert parse_guids([]) == {}


class FakeMedia:
    def __init__(self, file_path):
        self.parts = [type("Part", (), {"file": file_path})()]


class FakeSection:
    def __init__(self, title, location, items):
        self.title = title
        self.locations = [location]
        self._items = items

    def getGuid(self, guid):
        # Mirrors plexapi: an EXTERNAL id (tvdb://, tmdb://, imdb://) is matched
        # against the item's `guids` list, not its primary `.guid`, and a miss
        # raises NotFound. Modelling this as `search(guid=...)` previously hid a
        # bug where nothing resolved against a real server.
        for item in self._items:
            if any(g.id == guid for g in item.guids):
                return item
        raise PlexNotFound(f"Guid '{guid}' is not found in the library")


class FakeItem:
    def __init__(
        self, rating_key, title, year, file_path, guids, item_type="movie",
        parent_rating_key=None,
    ):
        self.ratingKey = rating_key
        self.title = title
        self.year = year
        self.type = item_type
        self.guids = [type("Guid", (), {"id": g})() for g in guids]
        self.media = [FakeMedia(file_path)] if file_path else []
        self.thumb = f"/library/metadata/{rating_key}/thumb/1"
        self.parentRatingKey = parent_rating_key
        self._seasons = {}

    def add_season(self, number, season_item):
        self._seasons[number] = season_item

    def season(self, season=None):
        try:
            return self._seasons[season]
        except KeyError:
            raise PlexNotFound(f"season {season} not found")

    def episode(self, season=None, episode=None):
        return self.season(season=season)._episode(episode)

    def add_episode(self, number, episode_item):
        if not hasattr(self, "_episodes"):
            self._episodes = {}
        self._episodes[number] = episode_item

    def _episode(self, number):
        try:
            return self._episodes[number]
        except (AttributeError, KeyError):
            raise PlexNotFound(f"episode {number} not found")


class FakeServer:
    def __init__(self, sections, items_by_key=None):
        self._sections = sections
        self._items_by_key = items_by_key or {}

    @property
    def library(self):
        # plexapi exposes `library` as a property, not a method. Modelling it as
        # a method here once hid a TypeError that only showed up against a real
        # server, so the double deliberately mirrors the real shape.
        return self

    def sections(self):
        return self._sections

    def fetchItem(self, ekey):
        return self._items_by_key[ekey]


@pytest.fixture
def server():
    movie = FakeItem(
        "12345", "Dune: Part Two", 2024,
        "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        ["tmdb://693134", "imdb://tt15239678"],
    )
    movies = FakeSection("Movies", "/mnt/Media/Movies", [movie])
    excluded = FakeSection("Photos", "/mnt/Media/Photos", [])
    return FakeServer([movies, excluded])


async def test_resolve_finds_a_movie_by_tmdb_id(server, tmp_path):
    client = PlexClient(server=server, excluded_libraries=["Photos"])
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)
    item = await client.resolve(intent)
    assert item.rating_key == "12345"
    assert item.library == "Movies"
    assert item.root_folder == "Dune Part Two (2024)"
    assert item.imdb_id == "tt15239678"


async def test_fetch_item_fetches_by_rating_key_as_int():
    marker = object()
    server = FakeServer([], items_by_key={12345: marker})
    client = PlexClient(server=server, excluded_libraries=[])

    item = await client.fetch_item("12345")

    assert item is marker


async def test_resolve_raises_when_plex_has_not_scanned_yet(server):
    client = PlexClient(server=server, excluded_libraries=["Photos"])
    intent = RenderIntent(kind="movie", title="Unknown", tmdb_id=999999)
    with pytest.raises(ItemNotFound):
        await client.resolve(intent)


async def test_excluded_libraries_are_never_searched(server):
    client = PlexClient(server=server, excluded_libraries=["Movies", "Photos"])
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)
    with pytest.raises(ItemNotFound):
        await client.resolve(intent)


async def test_resolve_finds_item_under_the_sections_second_location():
    movie = FakeItem(
        "777", "Second Root Movie", 2021,
        "/mnt/Media2/Movies/Second Root Movie (2021)/movie.mkv",
        ["tmdb://42"],
    )
    movies = FakeSection("Movies", "/mnt/Media/Movies", [movie])
    movies.locations = ["/mnt/Media/Movies", "/mnt/Media2/Movies"]
    server = FakeServer([movies])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(kind="movie", title="Second Root Movie", tmdb_id=42)

    item = await client.resolve(intent)

    assert item.root_folder == "Second Root Movie (2021)"


async def test_resolve_raises_item_not_found_when_path_matches_no_location():
    movie = FakeItem(
        "888", "Orphan Movie", 2020,
        "/mnt/Other/Movies/Orphan Movie (2020)/movie.mkv",
        ["tmdb://43"],
    )
    movies = FakeSection("Movies", "/mnt/Media/Movies", [movie])
    movies.locations = ["/mnt/Media/Movies", "/mnt/Media2/Movies"]
    server = FakeServer([movies])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(kind="movie", title="Orphan Movie", tmdb_id=43)

    with pytest.raises(ItemNotFound) as exc_info:
        await client.resolve(intent)

    message = str(exc_info.value)
    assert "888" in message
    assert "/mnt/Media/Movies" in message
    assert "/mnt/Media2/Movies" in message


async def test_resolve_finds_a_show_by_series_directory():
    show = FakeItem(
        "555", "Severance", 2022, None, ["tvdb://371980"], item_type="show",
    )
    show.locations = ["/mnt/Media/Shows/Severance (2022)"]
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show])
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(kind="show", title="Severance", tvdb_id=371980)

    item = await client.resolve(intent)

    assert item.kind == "show"
    assert item.root_folder == "Severance (2022)"
    assert item.file_path is None


def _show_with_season_and_episode():
    """A show with one scanned season and one scanned episode inside it.

    Mirrors the real ``plexapi`` shape: the season carries its own rating key
    and title, its ``parentRatingKey`` is the show; the episode carries its
    own rating key and title, its ``parentRatingKey`` is the season.
    """
    show = FakeItem("555", "Severance", 2022, None, ["tvdb://371980"], item_type="show")
    show.locations = ["/mnt/Media/Shows/Severance (2022)"]
    season = FakeItem(
        "556", "Season 2", None, None, [], item_type="season", parent_rating_key="555",
    )
    episode = FakeItem(
        "557", "Who Is Alive?", None, None, [], item_type="episode", parent_rating_key="556",
    )
    season.add_episode(3, episode)
    show.add_season(2, season)
    return show


async def test_resolve_a_season_intent_returns_the_seasons_own_identity():
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show])
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(kind="season", title="Severance", tvdb_id=371980, season_number=2)

    item = await client.resolve(intent)

    assert item.rating_key == "556"
    assert item.title == "Season 2"
    assert item.root_folder == "Severance (2022)"
    assert item.parent_rating_key == "555"


async def test_resolve_an_episode_intent_returns_the_episodes_own_identity():
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show])
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980, season_number=2, episode_number=3,
    )

    item = await client.resolve(intent)

    assert item.rating_key == "557"
    assert item.title == "Who Is Alive?"
    assert item.root_folder == "Severance (2022)"
    assert item.parent_rating_key == "556"


async def test_resolve_a_season_plex_has_not_scanned_yet_raises_item_not_found():
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show])
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(kind="season", title="Severance", tvdb_id=371980, season_number=9)

    with pytest.raises(ItemNotFound):
        await client.resolve(intent)


async def test_resolve_an_episode_plex_has_not_scanned_yet_raises_item_not_found():
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show])
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980, season_number=2, episode_number=99,
    )

    with pytest.raises(ItemNotFound):
        await client.resolve(intent)


async def test_resolved_season_and_episode_feed_the_right_text_into_title_text_for():
    # Regression guard: before the fix, resolve() always returned the show's own
    # rating key and title for a season/episode intent, so a season poster would
    # draw "SEVERANCE" instead of "SEASON 2" and a title card would draw
    # "SEVERANCE" instead of the episode title. title_text_for itself was never
    # buggy — it just always received the wrong item.
    config = load_config(EXAMPLE_CONFIG)
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show])
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])

    season_item = await client.resolve(
        RenderIntent(kind="season", title="Severance", tvdb_id=371980, season_number=2)
    )
    episode_item = await client.resolve(
        RenderIntent(
            kind="episode", title="Severance", tvdb_id=371980,
            season_number=2, episode_number=3,
        )
    )

    season_primary, _ = title_text_for("season_poster", season_item, config)
    episode_primary, _ = title_text_for("title_card", episode_item, config)

    assert season_primary == "Season 2"
    assert episode_primary == "Who Is Alive?"
