"""``tmdb_discover``: the attribute matrix, and what it refuses.

The transport is pinned in ``tests/test_tmdb_lists_client.py`` and the narrow
by-id discover builders in ``tests/test_builder_tmdb.py``. What these tests are
about is the table in
``src/autoposter/collections/builders/tmdb_discover.py`` and the three things
it has to get right:

- **an attribute TMDb does not have is a load error**, not a filter that
  quietly never applies -- including the identifier spelling of a dotted name,
  because there is exactly one spelling and it is TMDb's;
- **an attribute the other endpoint has is a build-time refusal naming it**.
  TMDb ignores a parameter its endpoint does not know, so ``with_cast`` sent to
  ``/discover/tv`` returns the unfiltered query: a full, plausible, wrong
  collection with nothing to notice. The refusal is at build because a
  definition with no ``libraries:`` applies to every library in the pass;
- **what the operator wrote is what goes on the wire, under TMDb's own name**
  -- the dotted aliases in particular -- and nothing else does.
"""
import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.tmdb import TmdbBuilderRefused
from autoposter.collections.builders.tmdb_discover import (
    BY_NAME,
    COMPANION_RULES,
    DISCOVER_PARAMS,
    SORT_BY,
    TmdbDiscoverParams,
    TmdbSortUnsupported,
)
from autoposter.config.schema import CollectionDefinition
from autoposter.providers.tmdb_lists import TmdbListClient

FIXTURES = Path(__file__).parent / "fixtures" / "collections"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _path(request) -> str:
    return request.url.path.removeprefix("/3")


def _routed(routes: dict, seen: list | None = None):
    """One payload per path, for the single-page cases."""

    def handler(request):
        if seen is not None:
            seen.append(request)
        payload = routes.get(_path(request))
        if payload is None:
            return httpx.Response(404, json=load("tmdb_not_found.json"))
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _paged(pages: dict, seen: list | None = None):
    """One payload per ``(path, page)`` -- what an order-across-pages test needs.

    A page the fixture does not cover answers with an empty ``results``, which
    is TMDb's own "past the end" shape rather than a 404.
    """

    def handler(request):
        if seen is not None:
            seen.append(request)
        page = int(request.url.params.get("page", 1))
        payload = pages.get((_path(request), page))
        if payload is None:
            return httpx.Response(200, json={"page": page, "results": []})
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def _sources(http):
    return SourceClients(tmdb=TmdbListClient("a-read-access-token", http))


def _ctx(sources: SourceClients, library_type: str = "Movie", **params) -> BuilderContext:
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library, library_type=library_type, config=params, sources=sources
    )


def _definition(**params) -> CollectionDefinition:
    """The load path: what ``config/schema.py`` does with a definition's params."""
    return CollectionDefinition(title="Discovered", builder="tmdb_discover", params=params)


# --- the matrix itself -------------------------------------------------------


def test_the_matrix_covers_the_documented_discover_surface():
    """The count is the transcription's own checksum. TMDb documents 37
    parameters on ``/discover/movie`` and 32 on ``/discover/tv`` besides
    ``page``, overlapping in 21 -- so a row silently lost, duplicated or
    mis-scoped in an edit shows up here rather than as a filter an operator
    writes and TMDb ignores."""
    movie = [row for row in DISCOVER_PARAMS if row.scope in ("shared", "movie")]
    tv = [row for row in DISCOVER_PARAMS if row.scope in ("shared", "tv")]
    shared = [row for row in DISCOVER_PARAMS if row.scope == "shared"]

    assert (len(movie), len(tv), len(shared)) == (37, 32, 21)
    assert len(DISCOVER_PARAMS) == 48
    assert {row.scope for row in DISCOVER_PARAMS} == {"shared", "movie", "tv"}


def test_every_matrix_row_has_a_distinct_name_and_a_usable_identifier():
    """``BY_NAME`` is keyed on the wire name, so a duplicate would collapse
    there and the generated model would come out a field short with nothing to
    say so."""
    assert len(BY_NAME) == len(DISCOVER_PARAMS)
    for row in DISCOVER_PARAMS:
        assert row.field.isidentifier(), row.name
        assert "." not in row.field


def test_the_model_is_generated_from_the_matrix_and_carries_tmdbs_names():
    """The generation is the anti-drift mechanism: there is no second list of
    fields to forget to update. Every field's alias is the matrix's wire name,
    which is what makes ``by_alias`` the alias map."""
    aliases = {info.alias for info in TmdbDiscoverParams.model_fields.values()}

    assert aliases == {row.name for row in DISCOVER_PARAMS}
    assert set(TmdbDiscoverParams.model_fields) == {row.field for row in DISCOVER_PARAMS}


def test_every_companion_rule_names_parameters_the_matrix_has():
    for names, companion in COMPANION_RULES:
        for name in (*names, companion):
            assert name in BY_NAME


def test_the_sort_vocabularies_are_per_endpoint_and_really_differ():
    """A shared parameter name whose values are not shared -- which is why
    ``sort_by`` is checked twice, once at load against the union and once at
    build against the media type."""
    assert "revenue.desc" in SORT_BY["movie"] and "revenue.desc" not in SORT_BY["tv"]
    assert "first_air_date.desc" in SORT_BY["tv"] and "first_air_date.desc" not in SORT_BY["movie"]
    assert "popularity.desc" in SORT_BY["movie"] & SORT_BY["tv"]


# --- load-time enforcement ---------------------------------------------------


def test_a_definition_written_in_tmdbs_vocabulary_loads():
    """The positive half: the dotted names an operator actually writes have to
    survive ``CollectionDefinition``'s validation, or the refusals below would
    be proving nothing more than "everything fails"."""
    definition = _definition(
        **{"vote_average.gte": 7.5, "with_genres": "18", "sort_by": "revenue.desc"}
    )

    assert definition.params["vote_average.gte"] == 7.5


def test_an_attribute_the_matrix_does_not_have_is_a_load_error():
    """``extra="forbid"`` through ``_params_must_satisfy_the_builders_own_model``:
    caught at the moment of the edit, not mid-pass hours later."""
    with pytest.raises(ValueError, match="with_actors"):
        _definition(with_actors="1892")


def test_the_identifier_spelling_of_a_dotted_attribute_is_refused_too():
    """The alias is the only accepted spelling. ``vote_average_gte`` is a
    plausible guess that TMDb has never heard of, so accepting it would send
    nothing and filter nothing."""
    with pytest.raises(ValueError, match="vote_average_gte"):
        _definition(vote_average_gte=7.5)


def test_a_wrongly_typed_attribute_is_a_load_error():
    with pytest.raises(ValueError, match="vote_average.gte"):
        _definition(**{"vote_average.gte": "high"})


def test_a_malformed_region_is_refused_at_load():
    with pytest.raises(ValueError, match="ISO-3166-1"):
        _definition(region="Finland", **{"vote_average.gte": 7})


def test_a_regional_tag_for_the_original_language_is_refused():
    """``with_original_language: fi-FI`` matches no title at all and TMDb says
    nothing about it -- a well-formed-looking value that empties a collection."""
    with pytest.raises(ValueError, match="with_original_language"):
        _definition(with_original_language="fi-FI")


def test_an_unknown_sort_is_a_load_error():
    with pytest.raises(ValueError, match="sort_by"):
        _definition(sort_by="box_office.desc")


def test_an_empty_discover_is_refused():
    """An unfiltered discover is TMDb's popularity chart under another name, and
    in sync mode it would write two hundred arbitrary titles into a collection
    that was meant to say something."""
    with pytest.raises(ValueError, match="at least one attribute"):
        _definition()


def test_sort_by_alone_does_not_satisfy_the_empty_guard():
    """``sort_by`` orders a result, it does not filter one: ``{sort_by:
    popularity.desc}`` and nothing else is TMDb's default popularity chart
    with a redundant sort, the exact outcome the guard exists to refuse."""
    with pytest.raises(ValueError, match="at least one attribute") as caught:
        _definition(sort_by="popularity.desc")
    assert "tmdb_chart" in str(caught.value)


def test_a_genuine_filter_alongside_sort_by_satisfies_the_guard():
    definition = _definition(sort_by="popularity.desc", with_genres="18")

    assert definition.params["with_genres"] == "18"


def test_include_adult_false_alone_does_not_satisfy_the_empty_guard():
    """``include_adult: false`` sets a field, but ``false`` is what TMDb
    already assumes with the field unset -- the same unfiltered query as
    ``sort_by`` alone, and the exact hole ``votes_gte: ge=1`` closes on the
    IMDb search side (``builders/imdb_search.py``)."""
    with pytest.raises(ValueError, match="at least one attribute") as caught:
        _definition(include_adult=False)
    assert "tmdb_chart" in str(caught.value)


def test_include_adult_true_alone_satisfies_the_guard():
    """``include_adult: true`` genuinely widens the query past TMDb's own
    default, so unlike ``false`` it counts as a real filter on its own."""
    definition = _definition(include_adult=True)

    assert definition.params["include_adult"] is True


def test_a_watch_provider_filter_without_a_watch_region_is_refused():
    """TMDb ignores the provider filter without ``watch_region`` and answers the
    unfiltered query, so the collection would hold every title rather than the
    ones on the operator's streaming service."""
    with pytest.raises(ValueError, match="watch_region"):
        _definition(with_watch_providers="8")


def test_the_same_filter_loads_once_its_companion_is_there():
    definition = _definition(with_watch_providers="8", watch_region="FI")

    assert definition.params["watch_region"] == "FI"


def test_a_certification_filter_without_its_country_is_refused():
    with pytest.raises(ValueError, match="certification_country"):
        _definition(certification="PG-13")


# --- the alias map, on the wire ----------------------------------------------


async def test_a_dotted_attribute_reaches_the_wire_under_tmdbs_own_name():
    """The alias map's whole job, end to end: ``vote_average.gte`` is not a
    legal python identifier, so it lives as ``vote_average_gte`` in the model
    and has to come back out dotted or TMDb ignores it."""
    seen: list = []
    routes = {"/discover/movie": load("tmdb_discover_movie.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tmdb_discover"].build(
            _ctx(_sources(http), **{"vote_average.gte": 7.5, "with_runtime.lte": 120})
        )

    assert result.ids == [("tmdb", "11"), ("tmdb", "1891")]
    assert seen[0].url.params["vote_average.gte"] == "7.5"
    assert seen[0].url.params["with_runtime.lte"] == "120"
    assert "vote_average_gte" not in seen[0].url.params


async def test_region_language_and_sort_reach_the_wire():
    """The four the plan accepts for generic discover, which the narrow by-id
    builders still refuse: they change the answer here (``sort_by`` and
    ``language`` decide the order, and with a page cap the order decides the
    membership; ``region`` decides whose release dates are read)."""
    seen: list = []
    routes = {"/discover/movie": load("tmdb_discover_movie.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["tmdb_discover"].build(
            _ctx(
                _sources(http),
                region="fi",
                language="fi-FI",
                sort_by="revenue.desc",
                watch_region="FI",
                with_watch_providers="8",
            )
        )

    assert seen[0].url.params["region"] == "FI"
    assert seen[0].url.params["language"] == "fi-FI"
    assert seen[0].url.params["sort_by"] == "revenue.desc"
    assert seen[0].url.params["watch_region"] == "FI"


async def test_a_date_attribute_is_sent_as_an_iso_date():
    """YAML parses ``2020-01-01`` into a ``datetime.date``, which httpx cannot
    encode into a query string at all -- so this is also the pin on the dump
    being taken in json mode."""
    seen: list = []
    routes = {"/discover/movie": load("tmdb_discover_movie.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["tmdb_discover"].build(
            _ctx(_sources(http), **{"release_date.gte": "2020-01-01"})
        )

    assert seen[0].url.params["release_date.gte"] == "2020-01-01"


async def test_a_boolean_attribute_is_sent_as_a_flag_tmdb_understands():
    seen: list = []
    routes = {"/discover/movie": load("tmdb_discover_movie.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["tmdb_discover"].build(_ctx(_sources(http), include_adult=True))

    assert seen[0].url.params["include_adult"] == "true"


async def test_only_the_attributes_the_operator_wrote_are_sent():
    """Forty-eight optional fields and no default noise: an unset parameter is
    absent from the request, which keeps TMDb's own defaults and keeps one
    cache key per distinct question rather than one per model revision."""
    seen: list = []
    routes = {"/discover/tv": load("tmdb_discover_tv.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["tmdb_discover"].build(
            _ctx(_sources(http), library_type="Show", with_networks=213)
        )

    assert dict(seen[0].url.params) == {"with_networks": "213", "page": "1"}


async def test_with_networks_accepts_the_or_form():
    """TMDb accepts ``with_networks=213|49`` exactly as it does
    ``with_release_type`` -- both are documented comma/pipe AND/OR filters on
    the wire, and an ``int`` field cannot carry the pipe."""
    seen: list = []
    routes = {"/discover/tv": load("tmdb_discover_tv.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        await REGISTRY["tmdb_discover"].build(
            _ctx(_sources(http), library_type="Show", with_networks="213|49")
        )

    assert seen[0].url.params["with_networks"] == "213|49"


# --- scope: the build-time refusal -------------------------------------------


async def test_a_movie_only_attribute_on_a_show_library_is_refused_naming_it():
    """``/discover/tv`` has no ``with_cast``. TMDb would ignore it and answer
    the unfiltered query, which is a full, plausible, wrong collection."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({}, seen)) as http:
        with pytest.raises(LibraryTypeMismatch) as caught:
            await REGISTRY["tmdb_discover"].build(
                _ctx(_sources(http), library_type="Show", with_cast="1892")
            )

    message = str(caught.value)
    assert "with_cast" in message and "Show" in message and "Movie" in message
    assert seen == [], "the refusal must come before the request, as the chart guard does"


async def test_region_is_refused_on_a_show_library():
    """The scope decision most worth checking: ``/discover/movie`` documents
    ``region`` and ``/discover/tv`` does not, so a definition that carries one
    is only ever a Movie definition."""
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(LibraryTypeMismatch, match="region"):
            await REGISTRY["tmdb_discover"].build(
                _ctx(_sources(http), library_type="Show", region="FI", with_genres="18")
            )


async def test_a_tv_only_attribute_on_a_movie_library_is_refused_naming_it():
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({}, seen)) as http:
        with pytest.raises(LibraryTypeMismatch) as caught:
            await REGISTRY["tmdb_discover"].build(
                _ctx(_sources(http), with_networks=213)
            )

    assert "with_networks" in str(caught.value)
    assert seen == []


@pytest.mark.parametrize(
    "library_type,media_type,ids",
    [
        ("Movie", "movie", [("tmdb", "11"), ("tmdb", "1891")]),
        ("Show", "tv", [("tmdb", "95396"), ("tmdb", "1416")]),
    ],
)
async def test_a_shared_attribute_crosses_to_both_endpoints(library_type, media_type, ids):
    """The other half of the guard: a scope check that refused everything would
    pass every refusal test above and build nothing at all."""
    seen: list = []
    routes = {f"/discover/{media_type}": load(f"tmdb_discover_{media_type}.json")}
    async with httpx.AsyncClient(transport=_routed(routes, seen)) as http:
        result = await REGISTRY["tmdb_discover"].build(
            _ctx(_sources(http), library_type=library_type, **{"vote_average.gte": 7})
        )

    assert result.ids == ids
    assert _path(seen[0]) == f"/discover/{media_type}"
    assert seen[0].url.params["vote_average.gte"] == "7.0"


async def test_a_movie_sort_on_a_show_library_is_refused():
    """``sort_by`` is a shared name with an unshared vocabulary: TMDb cannot
    sort shows by revenue, and falling back to its default order would be an
    operator's ranking quietly not applied.

    ``TmdbSortUnsupported``, not ``LibraryTypeMismatch``: a wrong sort key is
    not a library/media-type mismatch, and the engine's log line carries only
    the exception class name, so the wrong class would mislabel this failure."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed({}, seen)) as http:
        with pytest.raises(TmdbSortUnsupported) as caught:
            await REGISTRY["tmdb_discover"].build(
                _ctx(
                    _sources(http),
                    library_type="Show",
                    sort_by="revenue.desc",
                    with_genres="18",
                )
            )

    assert "revenue.desc" in str(caught.value)
    assert "first_air_date.desc" in str(caught.value)
    assert seen == []


async def test_a_sort_both_endpoints_share_is_allowed_on_either():
    routes = {"/discover/tv": load("tmdb_discover_tv.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["tmdb_discover"].build(
            _ctx(
                _sources(http),
                library_type="Show",
                sort_by="popularity.desc",
                with_genres="18",
            )
        )

    assert result.ids == [("tmdb", "95396"), ("tmdb", "1416")]


async def test_a_library_type_discover_cannot_answer_for_is_refused():
    async with httpx.AsyncClient(transport=_routed({})) as http:
        with pytest.raises(LibraryTypeMismatch):
            await REGISTRY["tmdb_discover"].build(
                _ctx(_sources(http), library_type="Artist", with_genres="18")
            )


# --- paging ------------------------------------------------------------------


async def test_order_is_preserved_across_pages():
    """Order is the builder's output contract -- it becomes the collection's
    custom order -- and a discover query is the one TMDb source that routinely
    runs past a single page."""
    pages = {
        ("/discover/movie", 1): load("tmdb_discover_movie_p1.json"),
        ("/discover/movie", 2): load("tmdb_discover_movie_p2.json"),
    }
    async with httpx.AsyncClient(transport=_paged(pages)) as http:
        result = await REGISTRY["tmdb_discover"].build(
            _ctx(_sources(http), **{"vote_average.gte": 7})
        )

    assert result.ids == [
        ("tmdb", "11"), ("tmdb", "1891"), ("tmdb", "1892"), ("tmdb", "1893"),
    ]


async def test_the_page_cap_warns_rather_than_truncating_in_silence(caplog):
    """A collection that is short because the pager stopped looks exactly like
    a correct one. ``vote_average.gte: 5`` matches tens of thousands of titles,
    so an ordinary definition reaches the cap -- this is not a runaway-upstream
    case."""
    seen: list = []
    page = load("tmdb_discover_movie_p1.json") | {"total_pages": 99}
    pages = {("/discover/movie", n): page for n in range(1, 11)}
    async with httpx.AsyncClient(transport=_paged(pages, seen)) as http:
        with caplog.at_level(logging.WARNING, logger="autoposter.providers.tmdb_lists"):
            result = await REGISTRY["tmdb_discover"].build(
                _ctx(_sources(http), **{"vote_average.gte": 5})
            )

    assert len(seen) == 10
    assert len(result.ids) == 20
    assert len(caplog.records) == 1
    logged = caplog.records[0].getMessage()
    assert "10-page cap" in logged
    assert "vote_average.gte=5" in logged


async def test_a_read_that_ends_on_tmdbs_last_page_does_not_warn(caplog):
    """The falsifiable half: a warning that fired on every read would be no
    signal at all."""
    pages = {
        ("/discover/movie", 1): load("tmdb_discover_movie_p1.json"),
        ("/discover/movie", 2): load("tmdb_discover_movie_p2.json"),
    }
    async with httpx.AsyncClient(transport=_paged(pages)) as http:
        with caplog.at_level(logging.WARNING, logger="autoposter.providers.tmdb_lists"):
            await REGISTRY["tmdb_discover"].build(
                _ctx(_sources(http), **{"vote_average.gte": 7})
            )

    assert caplog.records == []


# --- the rest of the contract ------------------------------------------------


async def test_it_raises_when_tmdb_is_not_configured():
    with pytest.raises(TmdbBuilderRefused, match="TMDb"):
        await REGISTRY["tmdb_discover"].build(_ctx(SourceClients(), with_genres="18"))


async def test_the_builder_validates_its_params_when_called_directly():
    """Defence in depth: the load-time check is not the only one, because a
    builder is also called directly."""
    with pytest.raises(ValidationError):
        await REGISTRY["tmdb_discover"].build(_ctx(SourceClients(), with_actors="1892"))


async def test_a_discover_offers_no_summary_or_poster():
    routes = {"/discover/movie": load("tmdb_discover_movie.json")}
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        result = await REGISTRY["tmdb_discover"].build(
            _ctx(_sources(http), with_genres="18")
        )

    assert result.summary is None
    assert (result.poster_kind, result.poster_key) == (None, None)


def test_the_builder_is_registered_under_its_own_name():
    assert REGISTRY["tmdb_discover"].type_name == "tmdb_discover"
    assert REGISTRY["tmdb_discover"].params_model is TmdbDiscoverParams
