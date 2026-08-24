"""The TMDb list builders: charts, and the five "by id" sources.

The transport is ``providers/tmdb_lists.py``; what these builders add is the
three decisions that are not TMDb's to make.

- **Which endpoint a chart means here.** ``chart: popular`` is
  ``/movie/popular`` on a Movie library and ``/tv/popular`` on a Show library,
  so one definition serves both -- which is the whole reason the chart is a
  *param* rather than eight builder names the way Kometa spells it
  (``tmdb_popular``, ``tmdb_airing_today``, …). ``imdb_chart`` collapses
  Kometa's three IMDb chart builders the same way.
- **Whether it can mean anything at all.** A chart with no form for this
  library type, a franchise collection on a Show library, a network on a Movie
  library: each would resolve to nothing and report a count, which is exactly
  what a correct collection of unowned titles looks like. ``base``'s
  ``require_library_type`` refuses them instead, before any request.
- **That absence is an error.** No token means no client means raise, per
  ``SourceClients``; a 404 raises out of the client for the same reason. An
  empty membership one layer down means "remove every member".

No summary and no poster, unlike ``imdb_chart``: that builder's title and
summary are Kometa translation strings transcribed verbatim in
``docs/research/kometa-collections.md`` §5, and no such transcription exists
for TMDb's charts. A summary invented here would not be parity, and a guessed
poster key is a hosted URL that 404s and leaves the collection quietly without
artwork (``posters.hosted_poster_url``). The definition's own ``summary:`` is
the way to set one until the strings are recorded.
"""
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)
from autoposter.providers.tmdb_lists import CHART_ENDPOINTS

__all__ = [
    "TmdbBuilderRefused",
    "TmdbChartBuilder",
    "TmdbChartParams",
    "TmdbCollectionBuilder",
    "TmdbCompanyBuilder",
    "TmdbEntityParams",
    "TmdbKeywordBuilder",
    "TmdbListBuilder",
    "TmdbNetworkBuilder",
]

# ISO-3166-1 alpha-2, and ISO-639-1 optionally qualified by one ("fi", "fi-FI").
_REGION = re.compile(r"^[A-Za-z]{2}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2}(-[A-Za-z]{2})?$")


class TmdbBuilderRefused(Exception):
    """This deployment cannot build from TMDb.

    Only ever "no token was configured": everything TMDb itself refuses is
    ``TmdbListRefused``, raised by the client that knows what was asked.
    """


class TmdbChartParams(BaseModel):
    """``tmdb_chart``'s params: which chart, and optionally for where.

    The chart key is validated against ``CHART_ENDPOINTS`` rather than a copy
    of it, so a key this module accepted but the endpoint table did not know
    would be a ``KeyError`` mid-pass rather than a config error at load.

    ``region`` and ``language`` are shape-checked because TMDb *ignores* a
    malformed one rather than complaining: ``region: Finland`` would silently
    produce the unfiltered chart, and an operator who asked for a regional
    chart would get a global one with nothing to notice.
    """

    model_config = ConfigDict(extra="forbid")

    chart: str
    region: str | None = None
    language: str | None = None

    @field_validator("chart")
    @classmethod
    def _must_be_a_known_chart(cls, value: str) -> str:
        if value not in CHART_ENDPOINTS:
            raise ValueError(
                f"unknown TMDb chart {value!r}: known charts are "
                + ", ".join(sorted(CHART_ENDPOINTS))
            )
        return value

    @field_validator("region")
    @classmethod
    def _must_be_a_country_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _REGION.match(value):
            raise ValueError(
                f"region {value!r} is not an ISO-3166-1 country code: TMDb wants "
                "two letters, like 'FI'"
            )
        # Upper-cased rather than refused: TMDb only accepts the upper form and
        # the lower one is the same request an operator meant to make.
        return value.upper()

    @field_validator("language")
    @classmethod
    def _must_be_a_language_code(cls, value: str | None) -> str | None:
        if value is not None and not _LANGUAGE.match(value):
            raise ValueError(
                f"language {value!r} is not an ISO-639-1 code: TMDb wants two "
                "letters, optionally with a country, like 'fi' or 'fi-FI'"
            )
        return value


class TmdbEntityParams(BaseModel):
    """``id:``, and nothing else -- what the five non-chart builders take.

    No ``region``/``language``: neither changes which titles a list, a
    collection or a discover-by-id holds, and every extra parameter is a
    second cache key for an identical answer.

    ``gt=0`` because ``0`` is what a mis-read config or an unfilled template
    renders to, and TMDb answers it with a 404 the operator then has to go and
    read out of a log.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0)


class _TmdbBuilder:
    """The client lookup every TMDb builder starts with."""

    def _client(self, ctx: BuilderContext):
        client = ctx.sources.tmdb
        if client is None:
            raise TmdbBuilderRefused(
                "TMDb is not configured for this deployment (no read access "
                "token), so it has no list to build from"
            )
        return client


class TmdbChartBuilder(_TmdbBuilder):
    """One TMDb chart, in TMDb's order, for this library's media type."""

    type_name = "tmdb_chart"
    params_model = TmdbChartParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbChartParams.model_validate(ctx.config)
        endpoints = CHART_ENDPOINTS[params.chart]
        require_library_type(
            f"the TMDb {params.chart!r} chart", ctx.library_type, endpoints
        )
        client = self._client(ctx)
        ids = await client.chart(
            endpoints[ctx.library_type], region=params.region, language=params.language
        )
        return BuilderResult(ids=[("tmdb", value) for value in ids])


class TmdbListBuilder(_TmdbBuilder):
    """A public TMDb list, in list order.

    No library-type guard: a TMDb list may hold movies and shows at once, and
    every member lands in the ``tmdb`` namespace either way -- the library the
    pass runs against already decides which of them can resolve.
    """

    type_name = "tmdb_list"
    params_model = TmdbEntityParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbEntityParams.model_validate(ctx.config)
        client = self._client(ctx)
        ids = await client.list_items(params.id)
        return BuilderResult(ids=[("tmdb", value) for value in ids])


class TmdbCollectionBuilder(_TmdbBuilder):
    """A franchise collection's parts. Movie libraries only: TMDb collections
    are movie franchises and their ``parts`` are movies."""

    type_name = "tmdb_collection"
    params_model = TmdbEntityParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbEntityParams.model_validate(ctx.config)
        require_library_type("the 'tmdb_collection' builder", ctx.library_type, ("Movie",))
        client = self._client(ctx)
        ids = await client.collection_parts(params.id)
        return BuilderResult(ids=[("tmdb", value) for value in ids])


class _DiscoverBuilder(_TmdbBuilder):
    """Everything TMDb attributes to one entity, through ``/discover``.

    ``media_types`` maps this library's type to the discover endpoint that can
    answer for it, and doubles as the mismatch guard's allowed set -- a
    network has a TV form and no movie form, so its map has one entry.
    """

    params_model = TmdbEntityParams

    media_types: dict[str, str]
    filter_key: str

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbEntityParams.model_validate(ctx.config)
        require_library_type(
            f"the {self.type_name!r} builder", ctx.library_type, self.media_types
        )
        client = self._client(ctx)
        ids = await client.discover(
            self.media_types[ctx.library_type], {self.filter_key: params.id}
        )
        return BuilderResult(ids=[("tmdb", value) for value in ids])


class TmdbCompanyBuilder(_DiscoverBuilder):
    """Everything a production company is credited with."""

    type_name = "tmdb_company"
    media_types = {"Movie": "movie", "Show": "tv"}
    filter_key = "with_companies"


class TmdbNetworkBuilder(_DiscoverBuilder):
    """Everything a TV network airs. Show libraries only -- TMDb has no movie
    form of a network, in this API or any other."""

    type_name = "tmdb_network"
    media_types = {"Show": "tv"}
    filter_key = "with_networks"


class TmdbKeywordBuilder(_DiscoverBuilder):
    """Everything tagged with one TMDb keyword."""

    type_name = "tmdb_keyword"
    media_types = {"Movie": "movie", "Show": "tv"}
    filter_key = "with_keywords"
