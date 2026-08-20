import pytest

from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ItemNotFound, PlexClient, parse_guids


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

    def search(self, **kwargs):
        results = []
        for item in self._items:
            guid_values = [g.id for g in item.guids]
            if any(kwargs.get("guid", "") == value for value in guid_values):
                results.append(item)
        return results


class FakeItem:
    def __init__(self, rating_key, title, year, file_path, guids, item_type="movie"):
        self.ratingKey = rating_key
        self.title = title
        self.year = year
        self.type = item_type
        self.guids = [type("Guid", (), {"id": g})() for g in guids]
        self.media = [FakeMedia(file_path)] if file_path else []
        self.thumb = f"/library/metadata/{rating_key}/thumb/1"


class FakeServer:
    def __init__(self, sections):
        self._sections = sections

    def library(self):
        return self

    def sections(self):
        return self._sections


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
