from pathlib import Path

import pytest
from plexapi.exceptions import BadRequest as PlexBadRequest
from plexapi.exceptions import NotFound as PlexNotFound

from sqlalchemy import select

from autoposter.arr.sync import enqueue_unknown_items
from autoposter.config.loader import load_config
from autoposter.db.models import Job
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
    def __init__(self, title, location, items, section_type="movie"):
        self.title = title
        self.locations = [location]
        self.type = section_type
        self._items = items
        # Which guids this section was asked about: the resolver must not ask a
        # movie library about a show-shaped intent at all.
        self.getguid_calls = []

    def all(self, **kwargs):
        # plexapi's section listing. `list_items` walks it; the resolver never
        # does, which is why this arrived with that method. Real plexapi
        # accepts `libtype`/`includeGuids`/etc as kwargs here -- accepted and
        # ignored, since this fake already returns every item's real data.
        return list(self._items)

    def getGuid(self, guid):
        # Mirrors plexapi: an EXTERNAL id (tvdb://, tmdb://, imdb://) is matched
        # against the item's `guids` list, not its primary `.guid`, and a miss
        # raises NotFound. Modelling this as `search(guid=...)` previously hid a
        # bug where nothing resolved against a real server.
        self.getguid_calls.append(guid)
        for item in self._items:
            if any(g.id == guid for g in item.guids):
                return item
        raise PlexNotFound(f"Guid '{guid}' is not found in the library")


class FakeItem:
    """A movie, season or episode.

    Deliberately has no ``season``/``episode`` methods: a real plexapi ``Movie``
    has none either, and ``'Movie' object has no attribute 'episode'`` is the
    exact production crash this file guards. Shows are ``FakeShow`` below.
    """

    def __init__(
        self, rating_key, title, year, file_path, guids, item_type="movie",
        parent_rating_key=None, library_section_title=None, show=None,
        index=None, parent_index=None, locations=None,
    ):
        self.ratingKey = rating_key
        self.title = title
        self.year = year
        self.type = item_type
        self.guids = [type("Guid", (), {"id": g})() for g in guids]
        self.media = [FakeMedia(file_path)] if file_path else []
        # A movie's own location is its file, a show's its directory -- the
        # same split `arr.sync.source_path` reads. Empty unless a test sets it,
        # which is what every plexapi caller here already tolerates (`resolve`
        # falls back to the section's locations).
        self.locations = list(locations or [])
        self.thumb = f"/library/metadata/{rating_key}/thumb/1"
        self.parentRatingKey = parent_rating_key
        # Every plexapi item knows which library it came out of; the direct
        # rating-key fetch has no section object and reads this instead.
        self.librarySectionTitle = library_section_title
        self._show = show
        # A Season's own number (`index`), or an Episode's own number
        # (`index`) plus its season's number (`parentIndex`) -- what the
        # direct rating-key fetch checks a stale/renumbered key against.
        self.index = index
        self.parentIndex = parent_index
        if item_type in ("season", "episode"):
            # Only plexapi Season and Episode carry ``show()``. A Movie does
            # not, and this fake must never be more permissive than the
            # library it stands in for.
            self.show = self._navigate_to_show

    def _navigate_to_show(self):
        if self._show is None:
            raise PlexNotFound(f"no show for {self.ratingKey}")
        return self._show

    def add_episode(self, number, episode_item):
        if not hasattr(self, "_episodes"):
            self._episodes = {}
        self._episodes[number] = episode_item

    def _episode(self, number):
        try:
            return self._episodes[number]
        except (AttributeError, KeyError):
            raise PlexNotFound(f"episode {number} not found")


class FakeShow(FakeItem):
    """A show — the only kind of item that navigates down to seasons/episodes."""

    def __init__(
        self, rating_key, title, year, file_path, guids, parent_rating_key=None,
        library_section_title=None,
    ):
        super().__init__(
            rating_key, title, year, file_path, guids,
            item_type="show", parent_rating_key=parent_rating_key,
            library_section_title=library_section_title,
        )
        self._seasons = {}

    def add_season(self, number, season_item):
        self._seasons[number] = season_item

    def season(self, season=None):
        # plexapi refuses an unaddressable call before any request goes out:
        # with no title and no number there is nothing to ask the server for,
        # and BadRequest -- not NotFound -- is what comes back.
        if season is None:
            raise PlexBadRequest("Missing argument: title or season is required")
        try:
            return self._seasons[season]
        except KeyError:
            raise PlexNotFound(f"season {season} not found")

    def episode(self, season=None, episode=None):
        # Same contract as `season` above: plexapi needs a title, or BOTH
        # coordinates, and raises BadRequest when it has neither.
        if season is None or episode is None:
            raise PlexBadRequest("Missing argument: title or season and episode are required")
        return self.season(season=season)._episode(episode)


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
        # plexapi raises NotFound for a key the server no longer has -- which
        # is exactly what a rating key stored before a Plex rebuild becomes.
        try:
            return self._items_by_key[ekey]
        except KeyError:
            raise PlexNotFound(f"Unable to find item with key {ekey}")


@pytest.fixture
def server():
    movie = FakeItem(
        "12345", "Dune: Part Two", 2024,
        "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        ["tmdb://693134", "imdb://tt15239678"],
    )
    movies = FakeSection("Movies", "/mnt/Media/Movies", [movie])
    excluded = FakeSection("Photos", "/mnt/Media/Photos", [], section_type="photo")
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
    show = FakeShow("555", "Severance", 2022, None, ["tvdb://371980"])
    show.locations = ["/mnt/Media/Shows/Severance (2022)"]
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(kind="show", title="Severance", tvdb_id=371980)

    item = await client.resolve(intent)

    assert item.kind == "show"
    assert item.root_folder == "Severance (2022)"
    assert item.file_path is None


def _show_with_season_and_episode(guids=("tvdb://371980",), section_title="Shows"):
    """A show with one scanned season and one scanned episode inside it.

    Mirrors the real ``plexapi`` shape: the season carries its own rating key
    and title, its ``parentRatingKey`` is the show; the episode carries its
    own rating key and title, its ``parentRatingKey`` is the season. Both
    children know their library and can navigate back up to the show, as
    ``Season.show()``/``Episode.show()`` do.
    """
    show = FakeShow(
        "555", "Severance", 2022, None, list(guids), library_section_title=section_title
    )
    show.locations = ["/mnt/Media/Shows/Severance (2022)"]
    season = FakeItem(
        "556", "Season 2", None, None, [], item_type="season", parent_rating_key="555",
        library_section_title=section_title, show=show, index=2,
    )
    episode = FakeItem(
        "557", "Who Is Alive?", None, None, [], item_type="episode", parent_rating_key="556",
        library_section_title=section_title, show=show, index=3, parent_index=2,
    )
    season.add_episode(3, episode)
    show.add_season(2, season)
    return show


def _by_key(show):
    """The show, its season and its episode indexed by integer rating key --
    the way ``PlexServer.fetchItem`` reaches any of them directly."""
    season = show.season(season=2)
    return {555: show, 556: season, 557: season._episode(3)}


async def test_resolve_a_season_intent_returns_the_seasons_own_identity():
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
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
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
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
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(kind="season", title="Severance", tvdb_id=371980, season_number=9)

    with pytest.raises(ItemNotFound):
        await client.resolve(intent)


async def test_resolve_an_episode_plex_has_not_scanned_yet_raises_item_not_found():
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980, season_number=2, episode_number=99,
    )

    with pytest.raises(ItemNotFound):
        await client.resolve(intent)


async def test_an_episode_intent_without_coordinates_is_absent_not_a_bad_request():
    """A ``media_items`` row with NULL season/episode numbers is unresolvable.

    Production: the first ``plex_prune`` pass refused the whole scan with
    ``PruneRefused: ... (BadRequest)`` because such a row's stale rating key
    fell through to the GUID search, matched the show, and then descended with
    ``item.episode(season=None, episode=None)`` -- a call plexapi refuses by
    contract before any request goes out. One row poisoned the entire sweep.
    Since ``resolve()`` shares the same search, those rows' render jobs were
    erroring on BadRequest instead of parking cleanly as ItemNotFound.
    """
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    no_coordinates = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980,
        season_number=None, episode_number=None, rating_key="557",
    )
    # The shape production actually holds: a year-grouped special, filed under
    # `parentIndex` 2024 with no `index` of its own. plexapi needs BOTH
    # coordinates, so half of them is still an unmakeable call.
    year_grouped_special = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980,
        season_number=2024, episode_number=None, rating_key="557",
    )

    for intent in (no_coordinates, year_grouped_special):
        with pytest.raises(ItemNotFound):
            await client.resolve(intent)
    assert await client.exists_many([no_coordinates, year_grouped_special]) == [False, False]


async def test_an_episode_intent_with_coordinates_still_descends():
    """The guard above reads the data, so it must not fire on a complete row."""
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980,
        season_number=2, episode_number=3, rating_key="557",
    )

    item = await client.resolve(intent)

    assert item.rating_key == "557"
    assert await client.exists_many([intent]) == [True]


async def test_a_live_special_with_no_episode_number_still_resolves_by_rating_key():
    """The guard sits in the GUID descend only, and that asymmetry is the point.

    A year-grouped special whose stored rating key STILL names it never reaches
    the descend: ``_fetch_by_rating_key_sync`` compares the item's own
    ``index``/``parentIndex`` against the intent's numbers, and ``None ==
    None`` matches. So the live ones keep resolving exactly as before, and only
    the ones whose key has gone stale -- which is to say, the ones the descend
    could never have addressed anyway -- read as absent.
    """
    show = _show_with_season_and_episode()
    special = FakeItem(
        "601", "The 2024 Special", None, None, [], item_type="episode",
        parent_rating_key="600", library_section_title="Shows", show=show,
        index=None, parent_index=2024,
    )
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    server = FakeServer([shows], items_by_key={601: special})
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="The 2024 Special", tvdb_id=371980,
        season_number=2024, episode_number=None, rating_key="601",
    )

    item = await client.resolve(intent)

    assert item.rating_key == "601"
    assert await client.exists_many([intent]) == [True]
    assert shows.getguid_calls == [], (
        "the stored rating key resolved it; the descend was never reached"
    )


async def test_a_season_intent_without_a_number_is_absent_not_a_bad_request():
    """The episode descend's sibling hazard: ``Show.season(season=None)`` is
    refused by plexapi the same way, so a season row with a NULL number would
    poison a prune scan identically."""
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="season", title="Severance", tvdb_id=371980,
        season_number=None, rating_key="556",
    )

    with pytest.raises(ItemNotFound):
        await client.resolve(intent)
    assert await client.exists_many([intent]) == [False]


async def test_a_season_intent_with_a_number_still_descends():
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="season", title="Severance", tvdb_id=371980,
        season_number=2, rating_key="556",
    )

    item = await client.resolve(intent)

    assert item.rating_key == "556"
    assert await client.exists_many([intent]) == [True]


async def test_resolved_season_and_episode_feed_the_right_text_into_title_text_for():
    # Regression guard: before the fix, resolve() always returned the show's own
    # rating key and title for a season/episode intent, so a season poster would
    # draw "SEVERANCE" instead of "SEASON 2" and a title card would draw
    # "SEVERANCE" instead of the episode title. title_text_for itself was never
    # buggy — it just always received the wrong item.
    config = load_config(EXAMPLE_CONFIG)
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
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


async def test_resolve_a_season_carries_the_shows_own_title():
    """Roadmap row 78, the GUID-search producer.

    ``ResolvedItem.title`` for a season is the SEASON's own Plex title --
    "Season 2", "Specials", or a bare year for a year-grouped season -- so
    there was nothing for a season poster to draw the show's name from. The
    show object is already in hand here (``item`` IS the show; ``target`` is
    the season it descends to), so this costs no extra Plex request.
    """
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    client = PlexClient(server=FakeServer([shows]), excluded_libraries=[])

    item = await client.resolve(
        RenderIntent(kind="season", title="Severance", tvdb_id=371980, season_number=2)
    )

    assert item.title == "Season 2", "the season's own identity is unchanged"
    assert item.show_title == "Severance"


async def test_a_direct_rating_key_fetch_of_a_season_carries_the_shows_own_title():
    """The same field off the OTHER producer, which arrives at the show from
    the opposite direction -- it fetches the season by key and climbs back up
    via ``item.show()``. ``_RawMatch``'s docstring requires the two paths to
    fill these fields identically; this is that requirement, asserted."""
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    client = PlexClient(
        server=FakeServer([shows], items_by_key=_by_key(show)), excluded_libraries=[],
    )

    item = await client.resolve(
        RenderIntent(
            kind="season", title="Severance", tvdb_id=371980,
            season_number=2, rating_key="556",
        )
    )

    assert item.rating_key == "556", "the stored key resolved it"
    assert item.show_title == "Severance"


async def test_a_movie_and_an_episode_carry_no_show_title():
    """The field is filled for a SEASON and nothing else.

    Only the season poster draws it (``render/pipeline.py``'s
    ``show_title_for`` refuses every other art kind), and only the season
    producers have the show in scope in all three places -- the adoption
    walk's ``_resolved_episode`` is handed the SEASON, not the show. Filling
    it for an episode would mean threading a fourth argument through that walk
    to feed a field nothing reads.
    """
    show = _show_with_season_and_episode()
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    movies = FakeSection("Movies", "/mnt/Media/Movies", [
        FakeItem("12345", "Dune: Part Two", 2024,
                 "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
                 ["tmdb://693134"]),
    ])
    client = PlexClient(server=FakeServer([movies, shows]), excluded_libraries=[])

    movie = await client.resolve(
        RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)
    )
    episode = await client.resolve(
        RenderIntent(
            kind="episode", title="Severance", tvdb_id=371980,
            season_number=2, episode_number=3,
        )
    )

    assert movie.show_title is None
    assert episode.show_title is None


async def test_an_episode_intent_never_queries_a_movie_library():
    """The section walk is constrained by the intent's kind.

    A show/season/episode intent can only ever match in a show library, so a
    movie library must not even be asked: production had a Movies section
    answer an episode intent for ``tmdb://64677`` with the movie that happens
    to carry the same TMDB id, and the resolver then crashed navigating into
    it. Asking only the libraries that can honestly answer is the first line
    of defence; the item-type check below is the second.
    """
    show = _show_with_season_and_episode(guids=["tmdb://64677"])
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    colliding_movie = FakeItem(
        "999", "The Colliding Movie", 2011,
        "/mnt/Media/Movies/The Colliding Movie (2011)/movie.mkv",
        ["tmdb://64677"],
    )
    # Movies first, as in production: it answered before the show library was
    # ever reached.
    movies = FakeSection("Movies", "/mnt/Media/Movies", [colliding_movie])
    server = FakeServer([movies, shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Severance", tmdb_id=64677,
        season_number=2, episode_number=3,
    )

    item = await client.resolve(intent)

    assert item.rating_key == "557"
    assert movies.getguid_calls == []
    assert shows.getguid_calls == ["tmdb://64677"]


async def test_an_episode_intent_refuses_a_movie_matched_by_a_colliding_guid():
    """A GUID match of the wrong type reads as "no Plex item", never as a hit.

    TMDB numbers movies and TV series in separate namespaces, so the same id
    names a different title in each — collisions are routine, not exotic. In
    production an episode intent carrying ``tmdb://64677`` (the show's id)
    matched the movie whose TMDB id is also 64677, and ``item.episode(...)``
    on it raised ``'Movie' object has no attribute 'episode'``, burning the
    job's generic-failure retries until it parked.

    The movie is in a show-type section here so that the check under test is
    the item-type one rather than the section filter: ``getGuid`` resolves the
    id through the library's *agent* and re-searches, so what it hands back is
    not something the caller can infer from the section alone.
    """
    colliding_movie = FakeItem(
        "999", "The Colliding Movie", 2011,
        "/mnt/Media/Shows/The Colliding Movie (2011)/movie.mkv",
        ["tmdb://64677"],
    )
    shows = FakeSection("Shows", "/mnt/Media/Shows", [colliding_movie], section_type="show")
    server = FakeServer([shows])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Severance", tmdb_id=64677,
        season_number=2, episode_number=3,
    )

    with pytest.raises(ItemNotFound) as exc_info:
        await client.resolve(intent)

    assert "no Plex item for episode" in str(exc_info.value)


async def test_a_movie_intent_refuses_a_show_matched_by_a_colliding_guid():
    """The same refusal in the other direction.

    Nothing crashes here — a movie intent never navigates — which is what
    makes it worth pinning: accepting the show would have resolved the movie
    intent to a show's rating key and written a movie's artwork onto it. The
    message assertion is load-bearing: without the type check the show is
    accepted and merely rejected later for having no media parts, i.e. the
    wrong item blamed for the wrong reason.
    """
    show = FakeShow("555", "Severance", 2022, None, ["tmdb://64677"])
    show.locations = ["/mnt/Media/Movies/Severance (2022)"]
    movies = FakeSection("Movies", "/mnt/Media/Movies", [show])
    server = FakeServer([movies])
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(kind="movie", title="The Colliding Movie", tmdb_id=64677)

    with pytest.raises(ItemNotFound) as exc_info:
        await client.resolve(intent)

    assert "no Plex item for movie" in str(exc_info.value)


# --- resolving by the rating key we already stored ---


def _show_library(show, section_title="Shows", indexed=True):
    """A show section holding ``show``, on a server that can also fetch every
    item in it by rating key."""
    section = FakeSection(section_title, "/mnt/Media/Shows", [show], section_type="show")
    server = FakeServer(
        [section], items_by_key=_by_key(show) if indexed else {}
    )
    return section, server


async def test_an_episode_with_a_rating_key_resolves_without_any_guid_search():
    """The production case, and the reason this path exists.

    Adoption stored each episode's OWN external ids in ``media_items``: for
    'The Pirate Solution' that is tmdb 64677 / tvdb 1123661, *episode*-level
    numbers. ``_search_sync`` reads an episode intent's ids as the SERIES'
    ids -- true on the webhook path, where Sonarr supplies them -- so those
    ids match nothing, the job retries as "waiting for Plex" and eventually
    parks. Roughly 14,000 jobs of one post-adoption full pass behaved this
    way. The rating key adoption also stored is the item's exact Plex
    identity, so it settles the question without asking any agent at all.

    The episode-level ids are on the intent here and match nothing in the
    library, which is what makes the zero-``getGuid`` assertion load-bearing:
    without the direct fetch this test cannot resolve at all.
    """
    show = _show_with_season_and_episode()
    shows, server = _show_library(show)
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Who Is Alive?", tmdb_id=64677, tvdb_id=1123661,
        season_number=2, episode_number=3, rating_key="557",
    )

    item = await client.resolve(intent)

    assert item.rating_key == "557"
    assert item.title == "Who Is Alive?"
    assert item.parent_rating_key == "556"
    assert item.library == "Shows"
    assert item.root_folder == "Severance (2022)"
    assert shows.getguid_calls == []


async def test_a_season_with_a_rating_key_resolves_without_any_guid_search():
    show = _show_with_season_and_episode()
    shows, server = _show_library(show)
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="season", title="Season 2", tvdb_id=1123661, season_number=2,
        rating_key="556",
    )

    item = await client.resolve(intent)

    assert item.rating_key == "556"
    assert item.title == "Season 2"
    assert item.parent_rating_key == "555"
    assert item.root_folder == "Severance (2022)"
    assert shows.getguid_calls == []


async def test_a_movie_with_a_rating_key_resolves_directly_with_its_own_file():
    """A movie is its own container, so the file path comes off the fetched
    item itself -- and ``resolve`` still refuses a movie with no media parts."""
    movie = FakeItem(
        "12345", "Dune: Part Two", 2024,
        "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        ["tmdb://693134", "imdb://tt15239678"], library_section_title="Movies",
    )
    movies = FakeSection("Movies", "/mnt/Media/Movies", [movie])
    server = FakeServer([movies], items_by_key={12345: movie})
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="movie", title="Dune: Part Two", tmdb_id=693134, rating_key="12345"
    )

    item = await client.resolve(intent)

    assert item.rating_key == "12345"
    assert item.root_folder == "Dune Part Two (2024)"
    assert item.file_path == "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv"
    assert item.imdb_id == "tt15239678"
    assert movies.getguid_calls == []


async def test_the_rating_key_shortcut_returns_exactly_what_the_guid_search_would():
    """A rating key is a faster route to the same answer, never a different one.

    Everything below the item's own identity -- root folder, file path, the
    external ids written back to ``media_items`` -- is taken from the *show*
    on both paths, because that is where an episode's assets and agent ids
    live. If the two paths disagreed, a rating key going stale after a Plex
    rebuild would quietly rewrite the row it had been maintaining.
    """
    show = _show_with_season_and_episode()
    shows, server = _show_library(show)
    client = PlexClient(server=server, excluded_libraries=[])
    fields = dict(
        kind="episode", title="Severance", tvdb_id=371980,
        season_number=2, episode_number=3,
    )

    by_guid = await client.resolve(RenderIntent(**fields))
    by_key = await client.resolve(RenderIntent(**fields, rating_key="557"))

    assert by_key == by_guid
    assert by_guid.file_path is None
    assert shows.getguid_calls == ["tvdb://371980"]


async def test_a_stale_rating_key_falls_back_to_the_guid_search():
    """Plex renumbers on a library rebuild, so a stored key can name nothing.

    ``fetchItem`` raises ``NotFound`` then, and the item must still resolve
    the slow way rather than the job failing -- degrade, do not break.
    """
    show = _show_with_season_and_episode()
    shows, server = _show_library(show, indexed=False)
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980,
        season_number=2, episode_number=3, rating_key="557",
    )

    item = await client.resolve(intent)

    assert item.rating_key == "557"
    assert shows.getguid_calls == ["tvdb://371980"]


async def test_a_rating_key_naming_the_wrong_kind_of_item_falls_back():
    """A renumbered key can land on a real item of the wrong kind.

    Here the key an episode row carries now names the *season*. Accepting it
    would file the episode's title card under the season's identity and
    overwrite the season's ``media_items`` row with episode data, silently --
    so the fetched item's own ``type`` has to match the intent's kind, the
    same refusal the GUID path makes after ``getGuid``.
    """
    show = _show_with_season_and_episode()
    shows, server = _show_library(show)
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980,
        season_number=2, episode_number=3, rating_key="556",
    )

    item = await client.resolve(intent)

    assert item.rating_key == "557"
    assert item.title == "Who Is Alive?"
    assert shows.getguid_calls == ["tvdb://371980"]


async def test_a_rating_key_inside_an_excluded_library_resolves_nothing():
    """Exclusion means invisible, and a rating key is not a way around it.

    ``fetchItem`` knows nothing about sections, so the direct path has to
    re-apply the exclusion itself: the fetched item's library must be one of
    the sections the GUID walk would have been allowed to ask. It is not, so
    the fetch is abandoned and the GUID search takes over -- and that search
    is barred from the same library, so the intent stays unresolved. This
    matches ``test_excluded_libraries_are_never_searched``: excluding the
    only library an item lives in makes it unreachable, not merely slower.
    """
    show = _show_with_season_and_episode()
    shows, server = _show_library(show)
    client = PlexClient(server=server, excluded_libraries=["Shows"])
    intent = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980,
        season_number=2, episode_number=3, rating_key="557",
    )

    with pytest.raises(ItemNotFound):
        await client.resolve(intent)

    assert shows.getguid_calls == []


async def test_the_rating_key_shortcut_returns_exactly_what_the_guid_search_would_for_a_season():
    """The equality pin above, repeated for a season intent.

    A season's ``parent_rating_key`` comes from the container's own rating
    key (the show, reached via ``item.show()``), not from
    ``item.parentRatingKey`` the way an episode's does -- a different code
    path in ``_fetch_by_rating_key_sync`` worth pinning on its own.
    """
    show = _show_with_season_and_episode()
    shows, server = _show_library(show)
    client = PlexClient(server=server, excluded_libraries=[])
    fields = dict(kind="season", title="Severance", tvdb_id=371980, season_number=2)

    by_guid = await client.resolve(RenderIntent(**fields))
    by_key = await client.resolve(RenderIntent(**fields, rating_key="556"))

    assert by_key == by_guid
    assert shows.getguid_calls == ["tvdb://371980"]


async def test_a_renumbered_rating_key_landing_on_the_wrong_episode_falls_back():
    """A stale key can still name a REAL episode after a rebuild -- just the
    wrong one: same type, same library, different season/episode numbers.

    Accepting it would stamp the intent's season 2 / episode 3 numbers onto
    whatever this impostor actually is. The identity check must refuse it and
    fall back to the GUID search, which finds the real episode instead.
    """
    show = _show_with_season_and_episode()
    impostor = FakeItem(
        "557", "Some Other Episode", None, None, [], item_type="episode",
        parent_rating_key="556", library_section_title="Shows", show=show,
        index=9, parent_index=5,
    )
    section = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")
    server = FakeServer([section], items_by_key={557: impostor})
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980,
        season_number=2, episode_number=3, rating_key="557",
    )

    item = await client.resolve(intent)

    assert item.title == "Who Is Alive?"
    assert item.rating_key == "557"
    assert section.getguid_calls == ["tvdb://371980"]


async def test_a_renumbered_rating_key_landing_on_the_wrong_movie_falls_back():
    """Same story for a movie: the key still names a real movie, in the
    right library, just not the one the intent means -- and its guids do not
    overlap the intent's at all. Accepting it would write this movie's
    identity onto the row the intent is meant to maintain.
    """
    impostor = FakeItem(
        "12345", "Some Other Movie", 1999,
        "/mnt/Media/Movies/Some Other Movie (1999)/movie.mkv",
        ["tmdb://999999"], library_section_title="Movies",
    )
    real_movie = FakeItem(
        "77777", "Dune: Part Two", 2024,
        "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        ["tmdb://693134", "imdb://tt15239678"],
    )
    movies = FakeSection("Movies", "/mnt/Media/Movies", [real_movie])
    server = FakeServer([movies], items_by_key={12345: impostor})
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="movie", title="Dune: Part Two", tmdb_id=693134, rating_key="12345",
    )

    item = await client.resolve(intent)

    assert item.rating_key == "77777"
    assert item.title == "Dune: Part Two"
    assert movies.getguid_calls == ["tmdb://693134"]


async def test_a_malformed_rating_key_falls_back_instead_of_raising():
    """``media_items.rating_key`` is text. A row whose key is not a number --
    hand-edited, or imported from somewhere else -- must not turn into a
    TypeError inside the worker thread."""
    show = _show_with_season_and_episode()
    shows, server = _show_library(show)
    client = PlexClient(server=server, excluded_libraries=[])
    intent = RenderIntent(
        kind="episode", title="Severance", tvdb_id=371980,
        season_number=2, episode_number=3, rating_key="not-a-number",
    )

    item = await client.resolve(intent)

    assert item.rating_key == "557"
    assert shows.getguid_calls == ["tvdb://371980"]


async def test_a_discovery_enqueued_item_resolves_by_rating_key_when_the_crosswalk_cannot(
    session,
):
    """Bamse-class regression, end to end through the real discovery enqueue.

    Production case: a show discovered off Plex whose stored tmdb/tvdb ids --
    read straight off the item itself -- the Plex agent crosswalk (``getGuid``)
    could not resolve back to any item. Reported ``ItemNotFound`` forever, one
    duplicate deferred job per sweep. The queued rating key must resolve the
    item directly, without depending on the crosswalk at all.
    """
    show = FakeShow(
        "999", "Bamse", 1980, None, ["tmdb://55645", "tvdb://358385"],
        library_section_title="Shows",
    )
    show.locations = ["/mnt/Media/Shows/Bamse (1980)"]
    shows = FakeSection("Shows", "/mnt/Media/Shows", [show], section_type="show")

    def _crosswalk_always_misses(guid):
        shows.getguid_calls.append(guid)
        raise PlexNotFound(f"Guid '{guid}' is not found in the library")

    shows.getGuid = _crosswalk_always_misses

    enqueued = await enqueue_unknown_items(session, [show], "show", "Shows")
    assert enqueued == 1
    job = (await session.execute(select(Job))).scalars().one()
    intent = RenderIntent(**job.payload)

    server = FakeServer([shows], items_by_key={999: show})
    client = PlexClient(server=server, excluded_libraries=[])
    item = await client.resolve(intent)

    assert item.rating_key == "999"
    assert item.title == "Bamse"


# --- list_items: the section walk the id-mismatch view reads -----------------


def _mismatch_library():
    movie = FakeItem(
        "12345", "Dune: Part Two", 2024,
        "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        ["tmdb://693134", "imdb://tt15239678"],
        locations=["/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv"],
    )
    other = FakeItem(
        "22222", "Private Film", 2001, "/mnt/Media/Hidden/p.mkv", ["tmdb://1"],
        locations=["/mnt/Media/Hidden/p.mkv"],
    )
    show = FakeShow("500", "Severance", 2022, None, ["tvdb://371980"])
    show.locations = ["/mnt/Media/TV/Severance"]
    return FakeServer([
        FakeSection("Movies", "/mnt/Media/Movies", [movie]),
        FakeSection("Hidden", "/mnt/Media/Hidden", [other]),
        FakeSection("TV", "/mnt/Media/TV", [show], section_type="show"),
    ])


async def test_list_items_returns_plain_data_for_one_section_type():
    client = PlexClient(server=_mismatch_library(), excluded_libraries=[])

    items = await client.list_items("movie")

    assert [item.title for item in items] == ["Dune: Part Two", "Private Film"]
    first = items[0]
    assert first.rating_key == "12345"
    assert first.library == "Movies"
    assert first.year == 2024
    assert first.locations == ["/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv"]
    # Parsed, not raw guid strings: every caller wants {agent: id}.
    assert first.guids == {"tmdb": "693134", "imdb": "tt15239678"}


async def test_list_items_asks_only_sections_of_the_type_it_was_given():
    """A show section answering a movie walk would pair series folders against
    Radarr and report every one of them as a mismatch."""
    client = PlexClient(server=_mismatch_library(), excluded_libraries=[])

    shows = await client.list_items("show")

    assert [item.title for item in shows] == ["Severance"]
    assert shows[0].locations == ["/mnt/Media/TV/Severance"]


async def test_list_items_honours_the_library_exclusions():
    client = PlexClient(server=_mismatch_library(), excluded_libraries=["Hidden"])

    items = await client.list_items("movie")

    assert [item.title for item in items] == ["Dune: Part Two"]


async def test_exists_many_answers_each_intent_in_order(server):
    """The pruner's whole question, and it must line up with its input."""
    client = PlexClient(server=server, excluded_libraries=["Photos"])
    intents = [
        RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134),
        RenderIntent(kind="movie", title="Gone", tmdb_id=999999),
        RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134),
    ]

    assert await client.exists_many(intents) == [True, False, True]


async def test_exists_many_uses_the_stored_rating_key_before_any_guid_search():
    """A row carries the exact Plex identity it was resolved under, so the
    probe must try that first -- otherwise every season and episode row would
    be judged by the GUID search, which reads their ids as the series'."""
    episode = FakeItem(
        "555", "An Episode", 2020, None, [], item_type="episode",
        library_section_title="TV Shows", index=3, parent_index=1,
    )
    show = FakeShow("500", "A Show", 2020, None, ["tvdb://77"],
                    library_section_title="TV Shows")
    episode._show = show
    shows = FakeSection("TV Shows", "/mnt/Media/TV", [show], section_type="show")
    server = FakeServer([shows], items_by_key={555: episode})
    client = PlexClient(server=server, excluded_libraries=[])

    intent = RenderIntent(
        kind="episode", title="An Episode", season_number=1, episode_number=3,
        rating_key="555",
    )

    assert await client.exists_many([intent]) == [True]
    assert shows.getguid_calls == [], (
        "the stored rating key resolved it; no GUID search should have happened"
    )


async def test_exists_many_reads_an_item_in_an_excluded_library_as_gone(server):
    """The pipeline never looks inside an excluded library, so neither does
    this: excluding a library is what made the DVR rows unresolvable, and
    reporting them as present would leave them parking forever."""
    client = PlexClient(server=server, excluded_libraries=["Movies", "Photos"])
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)

    assert await client.exists_many([intent]) == [False]


async def test_exists_many_reads_a_movie_with_no_media_parts_as_present():
    """The distinction that stops this being ``resolve()``. ``resolve`` raises
    ItemNotFound for a movie Plex has but has not scanned parts for yet; that
    item is emphatically not gone, and pruning its row would delete a row for
    something Plex still holds."""
    unscanned = FakeItem("900", "Just Added", 2024, None, ["tmdb://5"])
    movies = FakeSection("Movies", "/mnt/Media/Movies", [unscanned])
    client = PlexClient(server=FakeServer([movies]), excluded_libraries=[])
    intent = RenderIntent(kind="movie", title="Just Added", tmdb_id=5)

    with pytest.raises(ItemNotFound):
        await client.resolve(intent)
    assert await client.exists_many([intent]) == [True]


async def test_exists_many_lets_a_probe_failure_out():
    """A dead server must never read as "everything is gone". Nothing but
    NotFound is caught anywhere below this, and the caller refuses the pass."""

    class BrokenServer(FakeServer):
        def fetchItem(self, ekey):
            raise ConnectionError("connection reset")

    movies = FakeSection("Movies", "/mnt/Media/Movies", [])
    client = PlexClient(server=BrokenServer([movies]), excluded_libraries=[])
    intent = RenderIntent(kind="movie", title="Anything", rating_key="1")

    with pytest.raises(ConnectionError):
        await client.exists_many([intent])


async def test_exists_many_offloads_the_whole_walk_in_one_thread(server, monkeypatch):
    """A library-sized sweep is ~16,000 probes. One ``asyncio.to_thread`` per
    probe would be 16,000 hops through the loop that also carries the worker
    pool and the Plex liveness probe -- so the walk is offloaded once, the way
    ``find_orphaned_assets`` offloads its own."""
    import asyncio as asyncio_module
    import threading

    from autoposter.plex import client as client_module

    real_to_thread = asyncio_module.to_thread
    calls = []
    threads = set()

    async def counting_to_thread(func, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    real_sections = client_module.PlexClient._sections

    def spying_sections(self, wanted_type):
        threads.add(threading.current_thread())
        return real_sections(self, wanted_type)

    monkeypatch.setattr(client_module.asyncio, "to_thread", counting_to_thread)
    monkeypatch.setattr(client_module.PlexClient, "_sections", spying_sections)

    client = PlexClient(server=server, excluded_libraries=["Photos"])
    intents = [
        RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)
        for _ in range(5)
    ]

    assert await client.exists_many(intents) == [True] * 5
    assert len(calls) == 1, (
        "the whole walk must go through one asyncio.to_thread, not one per intent"
    )
    assert threads and threading.current_thread() not in threads, (
        "the probes ran on the event loop thread"
    )


async def test_keys_resolve_answers_only_for_the_stored_key(server):
    """The twin merge's survivor election, which ``exists_many`` cannot make.

    ``exists_many`` shares ``_search_sync`` with ``resolve``, so it falls
    through to the GUID walk on any rating-key refusal -- a re-matched item
    "exists" under both its stale key and its live one, and the election
    between two twin rows would be a coin toss. ``keys_resolve`` asks
    ``_fetch_by_rating_key_sync`` and nothing else: it answers whether THIS
    row's stored key is still the item's key.
    """
    movie = FakeItem(
        "12345", "Dune: Part Two", 2024,
        "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        ["tmdb://693134", "imdb://tt15239678"], library_section_title="Movies",
    )
    movies = FakeSection("Movies", "/mnt/Media/Movies", [movie])
    live_server = FakeServer([movies], items_by_key={12345: movie})
    client = PlexClient(server=live_server, excluded_libraries=[])

    live = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                        rating_key="12345")
    stale = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                         rating_key="999")

    assert await client.keys_resolve([live, stale]) == [True, False]
    assert movies.getguid_calls == [], (
        "keys_resolve must never fall through to the GUID walk -- that is the "
        "very fallback it exists to bypass"
    )


async def test_keys_resolve_refuses_an_intent_with_no_stored_key(server):
    """No key means nothing to accept. A webhook-born intent carries none by
    design, and answering True for it would elect a survivor on no evidence."""
    client = PlexClient(server=server, excluded_libraries=["Photos"])
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)

    assert await client.keys_resolve([intent]) == [False]
    assert await client.keys_resolve([]) == []
