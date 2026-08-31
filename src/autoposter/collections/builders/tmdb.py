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
- **Which half of a mixed list this library meant.** ``tmdb_list`` is the one
  source here that answers with both media types, and the hazard is the
  opposite one: a show's id in a Movie library can resolve to an unrelated
  film, because TMDb's two id spaces share one namespace. The client filters
  by media type; see ``TmdbListBuilder``.
- **That absence is an error.** No token means no client means raise, per
  ``SourceClients``; a 404 raises out of the client for the same reason. An
  empty membership one layer down means "remove every member".

No summary, and a poster on one builder only, unlike ``imdb_chart``: that
builder's title and summary are Kometa translation strings transcribed
verbatim in ``docs/research/kometa-collections.md`` §5, and no such
transcription exists for TMDb's charts. A summary invented here would not be
parity, and a guessed poster key is a hosted URL that 404s and leaves the
collection quietly without artwork (``posters.hosted_poster_url``). The
definition's own ``summary:`` is the way to set one until the strings are
recorded. ``tmdb_collection`` is the exception on the poster half alone, and
only because upstream's franchise art is keyed by a name the definition
already carries; see that builder.
"""
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)
from autoposter.collections.default_images import UNIVERSE_CODES
from autoposter.providers.tmdb_lists import CHART_ENDPOINTS, CHART_ENDPOINTS_ACCEPTING_REGION

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
    "TmdbRegionUnsupported",
]

# ISO-3166-1 alpha-2, and ISO-639-1 optionally qualified by one ("fi", "fi-FI").
_REGION = re.compile(r"^[A-Za-z]{2}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2}(-[A-Za-z]{2})?$")


class TmdbBuilderRefused(Exception):
    """This deployment cannot build from TMDb.

    Only ever "no token was configured": everything TMDb itself refuses is
    ``TmdbListRefused``, raised by the client that knows what was asked.
    """


class TmdbRegionUnsupported(Exception):
    """``region`` was set on a chart whose resolved endpoint ignores it.

    Its own class rather than reusing ``LibraryTypeMismatch`` so the engine's
    log line -- which carries only the exception class name -- says what kind
    of failure this was. See ``CHART_ENDPOINTS_ACCEPTING_REGION`` for which
    endpoints actually apply the filter.
    """


class TmdbChartParams(BaseModel):
    """``tmdb_chart``'s params: which chart, and optionally for where.

    The chart key is validated against ``CHART_ENDPOINTS`` rather than a copy
    of it, so a key this module accepted but the endpoint table did not know
    would be a ``KeyError`` mid-pass rather than a config error at load.

    ``region`` and ``language`` are shape-checked because TMDb *ignores* a
    malformed one rather than complaining: ``region: Finland`` would silently
    produce the unfiltered chart, and an operator who asked for a regional
    chart would get a global one with nothing to notice. A well-formed
    ``region`` on a chart whose endpoint ignores it entirely is the same
    hazard by another route -- TMDb only honours ``region`` on the
    `/movie/popular`, `/movie/top_rated`, `/movie/now_playing` and
    `/movie/upcoming` endpoints, dropping it silently everywhere else -- so
    that half of the guard lives in ``TmdbChartBuilder.build``, which knows
    the endpoint this chart resolves to and refuses before the request is
    sent. Between the two, enforcement here is complete: shape catches a
    malformed value, the builder catches a well-formed one TMDb would ignore.
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
    second cache key for an identical answer (the person builders' second
    read, the biography, *would* vary by language; see ``person_detail``).

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


def _require_region_supported(chart: str, path: str, region: str | None) -> None:
    """Refuse a ``region`` that TMDb would silently drop for this endpoint.

    Only the four ``/movie/*`` list endpoints apply ``region`` to membership
    (``CHART_ENDPOINTS_ACCEPTING_REGION``); every other chart -- the four
    ``/tv/*`` forms and all ``/trending/*`` variants -- accepts the parameter
    over HTTP and ignores it, which is the same "operator asked for a
    regional chart and got the global one with nothing to notice" hazard
    ``TmdbChartParams``'s shape validators guard against, plus a wasted,
    distinct cache key for an identical answer.
    """
    if region is None or path in CHART_ENDPOINTS_ACCEPTING_REGION:
        return
    accepting = sorted(
        key
        for key, endpoints in CHART_ENDPOINTS.items()
        if any(candidate in CHART_ENDPOINTS_ACCEPTING_REGION for candidate in endpoints.values())
    )
    raise TmdbRegionUnsupported(
        f"the TMDb {chart!r} chart resolves to {path!r} here, and TMDb does not "
        f"honour `region` on that endpoint -- it applies `region` only on the "
        + ", ".join(accepting)
        + " charts (built against a Movie library). Remove `region` from this "
        "definition; sending it would silently return the unfiltered chart."
    )


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
        path = endpoints[ctx.library_type]
        _require_region_supported(params.chart, path, params.region)
        client = self._client(ctx)
        ids = await client.chart(path, region=params.region, language=params.language)
        return BuilderResult(ids=[("tmdb", value) for value in ids])


class TmdbListBuilder(_TmdbBuilder):
    """A public TMDb list, in list order, as this library's half of it.

    Nothing is refused at the definition level -- a TMDb list may hold movies
    and shows at once, so unlike a franchise collection it cannot be typed as
    a whole -- and everything is decided per entry, which is the shape
    ``tvdb_list`` has for the same reason.

    The other media type is dropped (in the client, see
    ``providers/tmdb_lists._of_media_type``) rather than handed to the
    resolver to miss. TMDb's movie and show ids are different id spaces
    sharing one namespace, so a show's id offered to a Movie library can
    resolve to an unrelated *film*: a plausible wrong member, not an absent
    one. ``mdblist_list`` drops the other media type for exactly this.

    ``media_types`` maps this library's type to TMDb's word for it and
    doubles as the mismatch guard's allowed set, as ``_DiscoverBuilder``'s
    does -- a library type it cannot name has no filter to apply, and reading
    the list unfiltered there is the collision above.
    """

    type_name = "tmdb_list"
    params_model = TmdbEntityParams
    media_types = {"Movie": "movie", "Show": "tv"}

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbEntityParams.model_validate(ctx.config)
        require_library_type(
            f"the {self.type_name!r} builder", ctx.library_type, self.media_types
        )
        client = self._client(ctx)
        ids = await client.list_items(
            params.id, media_type=self.media_types[ctx.library_type]
        )
        # One TMDb list is a universe -- the DC split's 'DC Universe', list
        # 8642250 (``catalog._DC_LISTS``) -- and it joins the universe table by
        # the same list ref the IMDb and MDBList universes do. The id is
        # stringified because the table's keys are refs as written, and MDBList
        # refs are not numbers.
        code = UNIVERSE_CODES.get(str(params.id))
        return BuilderResult(
            ids=[("tmdb", value) for value in ids],
            poster_kind="universe" if code else None,
            poster_key=code,
        )


class TmdbCollectionBuilder(_TmdbBuilder):
    """A franchise collection's parts. Movie libraries only: TMDb collections
    are movie franchises and their ``parts`` are movies.

    The one builder here that carries a poster key, and it is the collection's
    own TITLE rather than its id. ``Kometa-Team/Default-Images`` keys franchise
    art by display name -- ``franchise/Jurassic Park.jpg`` -- and holds no
    id-based naming anywhere in the repository
    (``.superpowers/sdd/p-defimg-probe.md`` §1, and its "Confirmed
    non-findings" section). For a unit the ``content_franchises`` pack expanded,
    the definition's title IS TMDb's own collection name with the pack's
    ``remove_suffix: [' Collection']`` applied and any ``title_override``
    honoured, so it is both the best name we have and the one an operator can
    correct by hand. Upstream curates 116 franchises against TMDb's whole
    collection space, so a MISS is ordinary: ``default_images`` answers None,
    and the collection keeps whatever poster it had.

    A direct caller has no definition, and a key invented from the id would be
    a URL that 404s quietly -- so both fields stay None there.
    """

    type_name = "tmdb_collection"
    params_model = TmdbEntityParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbEntityParams.model_validate(ctx.config)
        require_library_type("the 'tmdb_collection' builder", ctx.library_type, ("Movie",))
        client = self._client(ctx)
        ids = await client.collection_parts(params.id)
        title = getattr(ctx.definition, "title", None)
        return BuilderResult(
            ids=[("tmdb", value) for value in ids],
            poster_kind="franchise" if title else None,
            poster_key=title or None,
        )


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
