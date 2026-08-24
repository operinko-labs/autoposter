"""The Radarr/Sonarr builders: "everything the downloader manages", as a list.

The service is already the operator's own inventory, so these builders do no
translation -- Radarr's ``tmdbId`` and Sonarr's ``tvdbId`` are the namespaces
the resolver wants. What they add is a tag filter, and the two things worth
guarding there are that an unknown tag *name* is an error rather than an empty
collection, and that resolving names to ids costs one ``/api/v3/tag`` request
per service per pass however many tag-list definitions the config holds.
"""
import httpx
import pytest
from pydantic import ValidationError

from autoposter.arr.client import RADARR, SONARR, ArrClient
from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.builders.arr import ArrBuilderRefused
from autoposter.collections.builders.base import LibraryTypeMismatch

# ``tags`` on an entry is a list of tag *ids*; the labels live behind
# /api/v3/tag. Entry 4 carries no ids at all, which is what an unmatched item
# in the service looks like and must never be adopted into a collection.
RADARR_MOVIES = [
    {"id": 1, "title": "Dune", "tmdbId": 438631, "tags": [3]},
    {"id": 2, "title": "Paddington", "tmdbId": 116149, "tags": [1, 3]},
    {"id": 3, "title": "The Godfather", "tmdbId": 238, "tags": []},
    {"id": 4, "title": "Unmatched", "tmdbId": 0, "tags": [1]},
    {"id": 5, "title": "No Tags Field", "tmdbId": 122906},
]

SONARR_SERIES = [
    {"id": 1, "title": "Severance", "tvdbId": 371980, "tags": [1]},
    {"id": 2, "title": "Bluey", "tvdbId": 353546, "tags": [1, 2]},
]

RADARR_TAGS = [{"id": 1, "label": "kids"}, {"id": 3, "label": "4K"}]
SONARR_TAGS = [{"id": 1, "label": "kids"}, {"id": 2, "label": "ongoing"}]


def _routed(routes: dict, counts: dict | None = None):
    """A transport answering by path, counting each path it served."""

    def handler(request):
        path = request.url.path
        if counts is not None:
            counts[path] = counts.get(path, 0) + 1
        if path not in routes:
            return httpx.Response(404, json={"message": "no route"})
        return httpx.Response(200, json=routes[path])

    return httpx.MockTransport(handler)


def _radarr(http):
    return ArrClient(http, "https://radarr.example", "secret-key", RADARR)


def _sonarr(http):
    return ArrClient(http, "https://sonarr.example", "secret-key", SONARR)


def _ctx(sources: SourceClients, run_cache: dict | None = None,
         library_type: str = "Movie", **params):
    return BuilderContext(
        library="Movies",
        library_type=library_type,
        config=params,
        sources=sources,
        run_cache={} if run_cache is None else run_cache,
    )


async def test_radarr_all_returns_tmdb_ids_in_listing_order():
    transport = _routed({"/api/v3/movie": RADARR_MOVIES})
    async with httpx.AsyncClient(transport=transport) as http:
        result = await REGISTRY["radarr_all"].build(
            _ctx(SourceClients(radarr=_radarr(http)))
        )

    # Entry 4's id is zero -- the service holds it but has not matched it to
    # anything, so it has no external identity to put in a collection.
    assert result.ids == [
        ("tmdb", "438631"), ("tmdb", "116149"), ("tmdb", "238"), ("tmdb", "122906")
    ]


async def test_sonarr_all_returns_tvdb_ids():
    transport = _routed({"/api/v3/series": SONARR_SERIES})
    async with httpx.AsyncClient(transport=transport) as http:
        result = await REGISTRY["sonarr_all"].build(
            _ctx(SourceClients(sonarr=_sonarr(http)), library_type="Show")
        )

    assert result.ids == [("tvdb", "371980"), ("tvdb", "353546")]


async def test_radarr_all_raises_when_radarr_is_not_configured():
    """Absent means None and None means raise -- the engine contains it as one
    dead source, which reads as "this definition failed" rather than a
    collection that quietly never fills."""
    with pytest.raises(ArrBuilderRefused, match="radarr is not configured"):
        await REGISTRY["radarr_all"].build(_ctx(SourceClients()))


async def test_sonarr_taglist_raises_when_sonarr_is_not_configured():
    with pytest.raises(ArrBuilderRefused, match="sonarr is not configured"):
        await REGISTRY["sonarr_taglist"].build(
            _ctx(SourceClients(), tags=["kids"], library_type="Show")
        )


async def test_radarr_all_refuses_a_show_library_before_checking_configuration():
    """Every other typed builder in this family guards its library type
    (``require_library_type``); ``radarr_all`` did not, which let a Show
    library resolve Radarr's TMDb *movie* ids into its own tmdb namespace --
    plausible wrong members rather than a loud failure. The guard runs before
    client acquisition, so even an unconfigured Radarr reports the mismatch
    rather than "not configured". Mutation proof: drop the guard and this
    goes red with ``ArrBuilderRefused`` (or worse, a result) instead."""
    with pytest.raises(LibraryTypeMismatch):
        await REGISTRY["radarr_all"].build(_ctx(SourceClients(), library_type="Show"))


async def test_radarr_all_on_a_show_library_makes_no_request():
    """The guard runs before any client is touched, so a misdirected
    definition costs no request to Radarr at all."""
    def handler(request):
        raise AssertionError(
            f"radarr_all must not talk to Radarr for a Show library: {request.url}"
        )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(LibraryTypeMismatch):
            await REGISTRY["radarr_all"].build(
                _ctx(SourceClients(radarr=_radarr(http)), library_type="Show")
            )


async def test_sonarr_taglist_refuses_a_movie_library_before_any_request():
    """The Sonarr mirror of the same guard: ``sonarr_*`` on a Movie library
    would emit TVDb *show* ids into the movie library's tvdb namespace.
    Mutation proof: drop the guard and this goes red."""
    def handler(request):
        raise AssertionError(
            f"sonarr_taglist must not talk to Sonarr for a Movie library: {request.url}"
        )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(LibraryTypeMismatch):
            await REGISTRY["sonarr_taglist"].build(
                _ctx(
                    SourceClients(sonarr=_sonarr(http)), tags=["kids"],
                    library_type="Movie",
                )
            )


async def test_radarr_taglist_refuses_a_show_library():
    with pytest.raises(LibraryTypeMismatch):
        await REGISTRY["radarr_taglist"].build(
            _ctx(SourceClients(), tags=["kids"], library_type="Show")
        )


async def test_sonarr_all_refuses_a_movie_library():
    with pytest.raises(LibraryTypeMismatch):
        await REGISTRY["sonarr_all"].build(_ctx(SourceClients(), library_type="Movie"))


async def test_radarr_all_raises_on_a_non_2xx_rather_than_building_nothing():
    transport = _routed({})
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await REGISTRY["radarr_all"].build(_ctx(SourceClients(radarr=_radarr(http))))


async def test_radarr_taglist_matches_any_tag_by_default():
    transport = _routed({"/api/v3/movie": RADARR_MOVIES, "/api/v3/tag": RADARR_TAGS})
    async with httpx.AsyncClient(transport=transport) as http:
        result = await REGISTRY["radarr_taglist"].build(
            _ctx(SourceClients(radarr=_radarr(http)), tags=["kids", "4K"])
        )

    assert result.ids == [("tmdb", "438631"), ("tmdb", "116149")]


async def test_radarr_taglist_can_require_every_tag():
    transport = _routed({"/api/v3/movie": RADARR_MOVIES, "/api/v3/tag": RADARR_TAGS})
    async with httpx.AsyncClient(transport=transport) as http:
        result = await REGISTRY["radarr_taglist"].build(
            _ctx(SourceClients(radarr=_radarr(http)), tags=["kids", "4K"], match="all")
        )

    assert result.ids == [("tmdb", "116149")]


async def test_radarr_taglist_matches_tag_names_case_insensitively():
    """The label is typed twice -- once in Radarr, once in the config -- and
    Radarr's own tag UI lower-cases nothing."""
    transport = _routed({"/api/v3/movie": RADARR_MOVIES, "/api/v3/tag": RADARR_TAGS})
    async with httpx.AsyncClient(transport=transport) as http:
        result = await REGISTRY["radarr_taglist"].build(
            _ctx(SourceClients(radarr=_radarr(http)), tags=["4k"])
        )

    assert result.ids == [("tmdb", "438631"), ("tmdb", "116149")]


async def test_radarr_taglist_raises_on_an_unknown_tag_name_and_lists_the_real_ones():
    """A tag that does not exist matches nothing, and "matches nothing" is
    indistinguishable from a correct empty result. The available names are in
    the message because that is what the operator needs to fix it, and a tag
    label is the operator's own word rather than a secret."""
    transport = _routed({"/api/v3/movie": RADARR_MOVIES, "/api/v3/tag": RADARR_TAGS})
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(ArrBuilderRefused) as caught:
            await REGISTRY["radarr_taglist"].build(
                _ctx(SourceClients(radarr=_radarr(http)), tags=["kids", "childrens"])
            )

    assert "childrens" in str(caught.value)
    assert "kids" in str(caught.value) and "4K" in str(caught.value)


async def test_the_tag_list_is_fetched_once_per_service_per_pass():
    """Every tag-list definition in the pass resolves its names against the
    same ``/api/v3/tag`` response. Radarr is the operator's own server, but
    the config may hold a dozen of these and each one is a round trip."""
    counts: dict[str, int] = {}
    transport = _routed(
        {"/api/v3/movie": RADARR_MOVIES, "/api/v3/tag": RADARR_TAGS}, counts
    )
    run_cache: dict = {}
    async with httpx.AsyncClient(transport=transport) as http:
        sources = SourceClients(radarr=_radarr(http))
        for tag in ("kids", "4K", "kids"):
            await REGISTRY["radarr_taglist"].build(
                _ctx(sources, run_cache=run_cache, tags=[tag])
            )

    assert counts["/api/v3/tag"] == 1
    # Only the tag map is shared: the listing is what the collection is made
    # of and each definition reads it for itself.
    assert counts["/api/v3/movie"] == 3


async def test_a_failed_tag_fetch_is_memoised_as_the_failure():
    """Memoise the failure too, or a dead service is re-asked once per
    definition -- the ``imdb_award`` event memo's rule."""
    counts: dict[str, int] = {}
    transport = _routed({"/api/v3/movie": RADARR_MOVIES}, counts)
    run_cache: dict = {}
    async with httpx.AsyncClient(transport=transport) as http:
        sources = SourceClients(radarr=_radarr(http))
        for _ in range(3):
            with pytest.raises(httpx.HTTPStatusError):
                await REGISTRY["radarr_taglist"].build(
                    _ctx(sources, run_cache=run_cache, tags=["kids"])
                )

    assert counts["/api/v3/tag"] == 1


async def test_radarr_and_sonarr_tag_maps_do_not_share_one_memo():
    """Two services, two tag namespaces: ``kids`` is id 1 in both here, but
    ``ongoing`` exists only in Sonarr and ``4K`` only in Radarr."""
    run_cache: dict = {}
    radarr_transport = _routed(
        {"/api/v3/movie": RADARR_MOVIES, "/api/v3/tag": RADARR_TAGS}
    )
    sonarr_transport = _routed(
        {"/api/v3/series": SONARR_SERIES, "/api/v3/tag": SONARR_TAGS}
    )
    async with httpx.AsyncClient(transport=radarr_transport) as radarr_http:
        async with httpx.AsyncClient(transport=sonarr_transport) as sonarr_http:
            sources = SourceClients(
                radarr=_radarr(radarr_http), sonarr=_sonarr(sonarr_http)
            )
            await REGISTRY["radarr_taglist"].build(
                _ctx(sources, run_cache=run_cache, tags=["4K"])
            )
            result = await REGISTRY["sonarr_taglist"].build(
                _ctx(
                    sources, run_cache=run_cache, tags=["ongoing"], library_type="Show"
                )
            )

    assert result.ids == [("tvdb", "353546")]


async def test_sonarr_taglist_filters_the_series_listing():
    transport = _routed({"/api/v3/series": SONARR_SERIES, "/api/v3/tag": SONARR_TAGS})
    async with httpx.AsyncClient(transport=transport) as http:
        result = await REGISTRY["sonarr_taglist"].build(
            _ctx(SourceClients(sonarr=_sonarr(http)), tags=["kids"], library_type="Show")
        )

    assert result.ids == [("tvdb", "371980"), ("tvdb", "353546")]


async def test_taglist_refuses_an_empty_tag_list():
    with pytest.raises(ValidationError):
        await REGISTRY["radarr_taglist"].build(_ctx(SourceClients(), tags=[]))


async def test_taglist_refuses_an_unknown_match_mode():
    with pytest.raises(ValidationError):
        await REGISTRY["radarr_taglist"].build(
            _ctx(SourceClients(), tags=["kids"], match="either")
        )


async def test_taglist_refuses_params_it_does_not_understand():
    with pytest.raises(ValidationError):
        await REGISTRY["radarr_taglist"].build(
            _ctx(SourceClients(), tags=["kids"], tag=["4K"])
        )


async def test_the_all_builders_take_no_params():
    with pytest.raises(ValidationError):
        await REGISTRY["radarr_all"].build(_ctx(SourceClients(), tags=["kids"]))
