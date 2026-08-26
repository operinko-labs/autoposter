"""``tracearr_most_watched``: the household's own most-played titles.

Never touches a real instance -- MockTransport only. The history fixtures are
verbatim cuts of the banked payloads (see ``tests/test_collection_activity.py``
for the provenance), with ``meta.nextCursor`` nulled where a fixture exists to
be a single page.

Four things are pinned here, and only the first is transport:

- **the window and the media type reach the request**, so a 30-day movie
  collection is not silently a 25-record default of everything;
- **a movie's ids come off the record and cost no extra call**, which is the
  short-circuit the harvest proves is safe for movies and only for movies;
- **a show's ids come off its media document**, one call per surviving bucket,
  memoised per pass -- and are never the episode-level ids the records carry;
- **one missing title is dropped and counted, one broken source raises.** An
  unconfigured Tracearr, a dead connection or a 404 on the history window all
  fail the definition; a 404 on one ranked title's media document does not,
  because a title deleted between the two calls must not empty a live
  collection.
"""
import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.tracearr import TracearrBuilderRefused
from autoposter.providers.tracearr import (
    API_PREFIX,
    TracearrClient,
    TracearrNotFound,
    TracearrRefused,
)

FIXTURES = Path(__file__).parent / "fixtures" / "collections"

BASE_URL = "http://tracearr.test.invalid"
API_KEY = "trr_pub_test"
SILO_UUID = "faf036e2-8459-4ace-a8ba-19486b6289c6"
REACHER_UUID = "40082858-dfa4-4508-b833-61b9b2654583"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _matched(payload, status=200):
    return httpx.Response(
        status, json=payload, headers={"x-ratelimit-limit": "240"}
    )


def _routed(routes: dict, seen: list | None = None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        entry = routes.get(request.url.path)
        if entry is None:
            return _matched(
                {"statusCode": 404, "error": "NotFoundError", "message": "Not Found"},
                status=404,
            )
        if callable(entry):
            return entry(request)
        return _matched(entry)

    return httpx.MockTransport(handler)


def _sources(http):
    return SourceClients(tracearr=TracearrClient(http, BASE_URL, API_KEY))


def _ctx(sources, library_type="Show", run_cache=None, **params):
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library,
        library_type=library_type,
        config=params,
        sources=sources,
        run_cache={} if run_cache is None else run_cache,
    )


def _build(ctx):
    return REGISTRY["tracearr_most_watched"].build(ctx)


# --- registration and params --------------------------------------------------


def test_the_builder_is_registered_under_its_own_name():
    assert REGISTRY["tracearr_most_watched"].type_name == "tracearr_most_watched"


def test_the_defaults_are_thirty_days_of_plays_capped_at_twenty():
    from autoposter.collections.builders.tracearr import TracearrMostWatchedParams

    params = TracearrMostWatchedParams()

    assert (params.days, params.metric, params.limit) == (30, "plays", 20)


@pytest.mark.parametrize(
    "params",
    [
        {"metric": "unique_users"},
        {"days": 0},
        {"days": 4000},
        {"limit": 0},
        {"limit": 1000},
        {"day": 30},
    ],
    ids=["unknown-metric", "zero-days", "too-many-days", "zero-limit",
         "over-the-call-budget", "misspelled-key"],
)
async def test_a_bad_params_block_is_refused(params):
    """``extra="forbid"``, and bounds on both numbers: ``limit`` is what caps
    the ``/media/{ref}`` calls a Show collection spends out of the shared
    240/min budget, so an unbounded one is a budget hazard rather than a
    preference."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(ValidationError):
            await _build(_ctx(_sources(http), **params))


# --- refusals -----------------------------------------------------------------


async def test_an_unconfigured_tracearr_raises_rather_than_building_nothing():
    with pytest.raises(TracearrBuilderRefused, match="not configured"):
        await _build(_ctx(SourceClients()))


async def test_a_library_of_the_wrong_type_is_refused_by_name():
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(LibraryTypeMismatch, match="Movie or Show"):
            await _build(_ctx(_sources(http), library_type="Artist"))


# --- the request --------------------------------------------------------------


async def test_the_window_and_the_media_type_reach_the_history_request():
    seen: list = []
    routes = {f"{API_PREFIX}/history": load("tracearr_history_movies.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await _build(_ctx(_sources(http), library_type="Movie", days=7))

    assert seen[0].url.path == f"{API_PREFIX}/history"
    assert seen[0].url.params["media_type"] == "movie"
    # The instant itself is the clock's; that it is an instant, and that one
    # was sent at all, is the builder's. ``since_instant`` is asserted exactly
    # in tests/test_collection_activity.py, against a fixed ``now``.
    assert seen[0].url.params["since"].endswith("Z")


async def test_a_show_library_asks_for_episodes_because_shows_have_no_plays():
    seen: list = []
    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
    }
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await _build(_ctx(_sources(http)))

    assert seen[0].url.params["media_type"] == "episode"


# --- movies: the short-circuit ------------------------------------------------


async def test_a_movie_collection_takes_its_ids_off_the_records():
    """TMDb first then IMDb, the ``mdblist_list`` Movie preference: which guid
    a Movie library's items are most likely to carry, and every fallback is a
    member that would otherwise be dropped."""
    seen: list = []
    routes = {f"{API_PREFIX}/history": load("tracearr_history_movies.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await _build(_ctx(_sources(http), library_type="Movie", limit=3))

    assert result.ids == [("tmdb", "36647"), ("tmdb", "36648"), ("tmdb", "36586")]
    # One request, and it was the history page: no ``/media/{ref}`` call is
    # needed or made for movies.
    assert [request.url.path for request in seen] == [f"{API_PREFIX}/history"]


async def test_the_movie_short_circuit_agrees_with_the_media_document():
    """The proof that the short-circuit is *safe*, not merely cheap: the ids on
    Blade: Trinity's history record are the same ids its own media document
    carries. (For an episode they would not be -- see the invariant test.)"""
    record = next(
        r for r in load("tracearr_history_movies.json")["data"]
        if r["media_id"] == "79e224e2-5dd6-447f-9ebc-c9e798300128"
    )
    document = load("tracearr_media_movie.json")

    assert record["tmdb_id"] == document["tmdb_id"]
    assert record["imdb_id"] == document["imdb_id"]
    assert record["tvdb_id"] == document["tvdb_id"]


async def test_a_movie_with_no_usable_id_is_dropped_and_logged(caplog):
    """The ``mdblist_list`` judgement: Tracearr knowing no id for one title is
    the resolver's ordinary "the library does not have this" one step earlier,
    and must not take the collection down."""
    page = load("tracearr_history_movies.json")
    page["data"] = [dict(page["data"][0]) | {"tmdb_id": None, "imdb_id": None}]
    routes = {f"{API_PREFIX}/history": page}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with caplog.at_level(logging.DEBUG):
            result = await _build(_ctx(_sources(http), library_type="Movie"))

    assert result.ids == []
    assert "no usable id" in caplog.text


# --- shows: the media document ------------------------------------------------


async def test_a_show_collection_takes_its_ids_off_the_media_document():
    """TVDb first then TMDb then IMDb, the ``mdblist_list`` Show preference."""
    seen: list = []
    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
    }
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await _build(_ctx(_sources(http)))

    assert result.ids == [("tvdb", "403245")]
    assert [request.url.path for request in seen] == [
        f"{API_PREFIX}/history",
        f"{API_PREFIX}/media/{SILO_UUID}",
    ]


async def test_no_id_a_show_collection_emits_is_an_episode_id():
    """THE INVARIANT (adjudication A-resolution, and its own named test).

    On an episode record ``imdb_id``/``tmdb_id``/``tvdb_id`` are the EPISODE's:
    the three Silo plays in this fixture carry tvdb 11751886 and 11751885,
    while Silo the show is tvdb 403245. ``GET /media/show:tvdb:11751886`` is a
    live-verified 404 (banked as
    ``docs/research/tracearr/payloads/v2-media-show-by-tvdb-ref.json``), so an
    episode id offered to a Show library resolves to nothing -- and "matched
    nothing" is indistinguishable from a correct empty collection, which is
    what makes this failure invisible rather than loud.
    """
    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
    }
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await _build(_ctx(_sources(http)))

    episode_ids = set()
    for record in load("tracearr_history_silo.json")["data"]:
        for field in ("imdb_id", "tmdb_id", "tvdb_id"):
            episode_ids.add(str(record[field]))

    assert episode_ids == {"tt41591872", "7173964", "11751886",
                           "tt39182946", "7173963", "11751885"}
    assert result.ids == [("tvdb", "403245")]
    assert not [value for _, value in result.ids if value in episode_ids]


async def test_the_media_document_is_fetched_once_per_pass_per_show():
    """The shared 240/min v2 budget is the binding constraint, so two
    definitions ranking the same show in one pass spend one call, not two.
    ``ctx.run_cache`` is the ``imdb_award``/``mdblist`` precedent."""
    seen: list = []
    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
    }
    run_cache: dict = {}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        sources = _sources(http)
        await _build(_ctx(sources, run_cache=run_cache))
        await _build(_ctx(sources, run_cache=run_cache, metric="watch_time"))

    media_calls = [r for r in seen if r.url.path.startswith(f"{API_PREFIX}/media/")]
    assert len(media_calls) == 1


def _two_show_page():
    """Reacher (4 plays) and Silo (3 plays) out of the banked window.

    Records verbatim; only the selection is the test's. Two buckets is the
    smallest page that can show one title dropping while the other builds.
    """
    wanted = {REACHER_UUID, SILO_UUID}
    data = [
        record for record in load("tracearr_history_window.json")["data"]
        if record.get("show_media_id") in wanted
    ]
    assert len(data) == 7, len(data)
    return {"data": data, "meta": {"nextCursor": None, "pageSize": 100}}


async def test_a_media_document_that_404s_drops_that_title_and_builds_the_rest(caplog):
    """One title gone is not the source gone.

    Reacher outranks Silo, and Reacher's uuid 404s here -- which is what a show
    deleted from Tracearr between the history read and this lookup looks like.
    Failing the whole definition on that would empty a live collection over a
    transient fact about one member; dropping it silently would build a
    plausible, quietly-shorter collection. So it is dropped and counted, the
    ``mdblist_list`` shape, and the count is logged with the exception's class
    name and nothing else.
    """
    routes = {
        f"{API_PREFIX}/history": _two_show_page(),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
        # Reacher's uuid is deliberately unrouted: ``_routed``'s default is the
        # matched 404 envelope, rate-limit header included.
    }
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        # Scoped to the builder's own logger: at root level, INFO would also
        # unlock httpx's own per-request logging, which necessarily carries
        # the URL it just requested -- noise this test has no interest in.
        with caplog.at_level(logging.INFO, logger="autoposter.collections.builders.tracearr"):
            result = await _build(_ctx(_sources(http), limit=10))

    assert result.ids == [("tvdb", "403245")]
    assert "1 ranked title(s) dropped" in caplog.text
    assert "TracearrNotFound" in caplog.text
    assert BASE_URL not in caplog.text


async def test_a_dropped_title_is_not_asked_about_twice_in_one_pass():
    """The failure is memoised too, or a dead id costs one request per
    definition -- ``BuilderContext``'s own rule -- and it stays the same class
    across the memo, so the second definition drops the bucket rather than
    failing on it."""
    seen: list = []
    routes = {
        f"{API_PREFIX}/history": _two_show_page(),
        f"{API_PREFIX}/media/{SILO_UUID}": load("tracearr_media_show.json"),
    }
    run_cache: dict = {}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        sources = _sources(http)
        first = await _build(_ctx(sources, run_cache=run_cache, limit=10))
        second = await _build(_ctx(sources, run_cache=run_cache, limit=10))

    assert first.ids == second.ids == [("tvdb", "403245")]
    media_calls = [r for r in seen if r.url.path.startswith(f"{API_PREFIX}/media/")]
    # One per uuid for the whole pass: one 404 and one document, not two each.
    assert len(media_calls) == 2


async def test_a_transport_failure_still_fails_the_whole_definition():
    """The other half of the split. A connection that will not open is the
    SOURCE failing, and every remaining bucket would fail the same way -- so it
    raises, the engine contains it as one dead source, and the collection is
    left exactly as it was rather than being rebuilt from a partial ranking."""

    def boom(request):
        raise httpx.ConnectError("connection refused")

    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": boom,
    }
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        with pytest.raises(TracearrRefused) as error:
            await _build(_ctx(_sources(http)))

    assert not isinstance(error.value, TracearrNotFound)
    assert "ConnectError" in str(error.value)
    assert BASE_URL not in str(error.value)


async def test_a_history_endpoint_that_404s_still_fails_the_definition():
    """The narrowness of the per-item tolerance, pinned. ``build`` catches
    ``TracearrNotFound`` only around the per-bucket resolution, so the history
    call answering 404 -- the window itself being gone -- is still fatal.
    Reading it as an empty window would mean "remove every member"."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(TracearrRefused):
            await _build(_ctx(_sources(http), library_type="Movie"))


# --- the last resort ----------------------------------------------------------


async def test_an_identity_less_only_bucket_becomes_a_plex_rating_key():
    """The documented last resort (adjudication A-resolution). A Plex rating
    key resolves free against the engine's own index and is dropped, not
    guessed, when it is stale -- and no ``/media/{ref}`` call is possible for a
    play that carries no media id."""
    seen: list = []
    routes = {f"{API_PREFIX}/history": load("tracearr_history_unidentified.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await _build(_ctx(_sources(http)))

    assert result.ids == [("plex", "75916")]
    assert [request.url.path for request in seen] == [f"{API_PREFIX}/history"]


# --- what the collection says about itself ------------------------------------


async def test_the_summary_names_the_metric_the_library_and_the_window():
    routes = {f"{API_PREFIX}/history": load("tracearr_history_movies.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        plays = await _build(_ctx(_sources(http), library_type="Movie", days=14))
        watched = await _build(
            _ctx(_sources(http), library_type="Movie", days=14, metric="watch_time")
        )

    assert plays.summary == (
        "The movies played most often on this server over the past 14 days."
    )
    assert watched.summary == (
        "The movies watched for the longest on this server over the past 14 days."
    )
    # No hosted artwork exists for these, so the collection simply keeps none.
    assert (plays.poster_kind, plays.poster_key) == (None, None)


async def test_no_log_line_and_no_error_carries_the_base_url_or_the_key(caplog):
    """On the failing path, which is the one that reaches
    ``logger.exception`` in the engine with a whole traceback."""
    routes = {
        f"{API_PREFIX}/history": load("tracearr_history_silo.json"),
        f"{API_PREFIX}/media/{SILO_UUID}": lambda request: _matched(
            {"statusCode": 500, "error": "InternalServerError", "message": "boom"},
            status=500,
        ),
    }
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        # Scoped to the builder's own logger for the same reason as the
        # 404-drop test above: root-level DEBUG would also unlock httpx's own
        # per-request logging, which carries the URL by design.
        with caplog.at_level(logging.DEBUG, logger="autoposter.collections.builders.tracearr"):
            with pytest.raises(TracearrRefused) as error:
                await _build(_ctx(_sources(http)))

    assert BASE_URL not in caplog.text
    assert API_KEY not in caplog.text
    assert BASE_URL not in str(error.value)
    assert API_KEY not in str(error.value)


# --- the catalog rows ---------------------------------------------------------


def test_the_two_catalog_rows_are_charts_and_opt_in():
    from autoposter.collections.catalog import BY_KEY, READY

    movies = BY_KEY["chart_tracearr_movies"]
    shows = BY_KEY["chart_tracearr_shows"]

    for preset in (movies, shows):
        assert preset.category == "charts"
        assert preset.readiness == READY
        assert preset.gated_row is None
        # No Kometa defaults file reproduces this; saying so is the rule
        # ``_check_kometa_sources`` exists to make impossible to break.
        assert preset.kometa_source.startswith("no Kometa defaults file -- ")
    assert movies.library_types == ("Movie",)
    assert shows.library_types == ("Show",)
    assert movies.titles() == ["Most Watched Movies"]
    assert shows.titles() == ["Most Watched Shows"]


def test_the_rows_expand_to_the_builder_with_an_explicit_window_and_cap():
    from autoposter.collections.catalog import BY_KEY

    [definition] = BY_KEY["chart_tracearr_shows"].definitions("Show")

    assert definition.title == "Most Watched Shows"
    assert definition.builder == "tracearr_most_watched"
    assert definition.params == {"days": 30, "limit": 20, "metric": "plays"}
    # A Movie-only preset asked for its Show definitions has none.
    assert BY_KEY["chart_tracearr_movies"].definitions("Show") == []
