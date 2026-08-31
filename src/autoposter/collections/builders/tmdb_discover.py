"""``tmdb_discover``: TMDb's whole query surface, as a table a reviewer can read.

The five existing TMDb "by id" builders each send exactly one discover filter
(``with_companies``, ``with_networks``, ``with_keywords``) and take one param.
This one is the general form: the operator writes TMDb's own parameter names
and this module decides, before a request is sent, whether each one means
anything for the library the pass is running against.

**The table is the deliverable.** ``DISCOVER_PARAMS`` below is the transcribed
``/discover/movie`` + ``/discover/tv`` reference: one row per documented
parameter, carrying TMDb's wire name, the python identifier that stands in for
it, the type, and -- the column that does the work -- which of the two
endpoints actually accepts it. Everything else in this module is generated
from or checked against that table:

- the params model's fields are *generated* from the rows (``create_model``
  below), so the table cannot drift from what loads;
- the request is ``model_dump(by_alias=True)``, so the table's ``name`` column
  is literally what goes on the wire;
- the build-time refusal reads the ``scope`` column.

Generating the model rather than hand-writing forty-eight fields and checking
them against the table is a deliberate choice. A hand-written model duplicates
every row, and the duplicate is the thing that rots: the table would become
documentation *about* the model instead of the model's source, and a reviewer
checking the table would be checking the wrong artifact. The cost is that the
model's fields are not statically typed -- acceptable here because nothing
reads them by name. The model is validated and dumped, never attribute-accessed.

**Why scope is a build-time refusal and not a load-time one.** A definition
with no ``libraries:`` key applies to every library in the pass, so which
media type a build means is only known in ``build`` -- the same reason
``base.require_library_type`` is where it is. And it has to be a refusal
rather than a shrug, because TMDb *ignores* a parameter its endpoint does not
know: ``with_cast`` sent to ``/discover/tv`` does not error, it silently
returns the unfiltered query. An operator would get a full, plausible, wrong
collection with nothing at all to notice. That is the failure this whole
module is shaped around.

**Where the table came from, and what happens when it is wrong.** TMDb
publishes no machine-readable schema for discover and offers no introspection,
so the rows are transcribed from its published ``/discover/{movie,tv}``
reference. That transcription can be out of date in exactly two ways, and
neither is silent: a parameter TMDb has added since is a **load error** naming
the key (``extra="forbid"``, as everywhere), and a row whose type or scope is
wrong is a TMDb **400** out of ``fetch_json``'s ``raise_for_status``. The
dangerous third case -- a parameter that loads, is sent, and does nothing --
is only reachable through the ``scope`` column, which is why every non-obvious
scope decision below carries the reason on the row.

Deliberately absent from the table: ``page`` (the client's pager owns it, and
an operator pinning it would fight the loop for a single page of a ten-page
read) and ``api_key`` (the token is a bearer header and never a parameter).
"""
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, create_model, model_validator

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    LibraryTypeMismatch,
    require_library_type,
)

# The shape validators are ``builders/tmdb.py``'s, reused rather than copied:
# the same two patterns, refusing the same silently-ignored malformed values.
from autoposter.collections.builders.tmdb import _LANGUAGE, _REGION, _TmdbBuilder
from autoposter.collections.default_images import STREAMING_NAMES

logger = logging.getLogger(__name__)

__all__ = [
    "COMPANION_RULES",
    "DISCOVER_PARAMS",
    "SORT_BY",
    "DiscoverParam",
    "TmdbDiscoverBuilder",
    "TmdbDiscoverParams",
    "TmdbSortUnsupported",
    "discover_filters",
]


# Sentinel for "this row has no TMDb-documented default" -- distinct from
# ``None``, which already means "the operator didn't set this field". A real
# default is compared with ``!=`` in the empty-guard below, and the sentinel
# never equals a real value, so a row with no default simply never matches.
_NO_WIRE_DEFAULT = object()


@dataclass(frozen=True)
class DiscoverParam:
    """One row of the matrix.

    ``name`` is TMDb's own parameter name and is what goes on the wire and what
    the operator writes in YAML. ``field`` is the python identifier standing in
    for it, which differs only for TMDb's dotted names (``vote_average.gte``) --
    a dot is not legal in an identifier, and pydantic carries the real name as
    the field's alias. ``scope`` is which discover endpoint accepts it:
    ``"movie"``, ``"tv"``, or ``"shared"`` for the ones both document.

    ``wire_default`` is TMDb's own default for this parameter, when one is
    documented and the model can express it -- see ``_needs_at_least_one_attribute``
    for why it matters: a value equal to what TMDb already assumes with the
    field unset sends the same unfiltered request as leaving it unset. Only
    ``include_adult`` carries one today; the rest of the matrix's boolean rows
    (``include_video``, ``include_null_first_air_dates``,
    ``screened_theatrically``) have no "defaults to" note on their row, so
    unlike ``include_adult`` their TMDb default was never pinned during
    transcription and is left alone rather than guessed at.
    """

    name: str
    type: Any
    scope: str
    note: str = ""
    wire_default: Any = _NO_WIRE_DEFAULT

    @property
    def field(self) -> str:
        """The python identifier for this parameter. Dotted names only."""
        return self.name.replace(".", "_")


def _a_country_code(value: str) -> str:
    if not _REGION.match(value):
        raise ValueError(
            f"{value!r} is not an ISO-3166-1 country code: TMDb wants two letters, "
            "like 'FI'"
        )
    # Upper-cased rather than refused, as ``TmdbChartParams`` does it: TMDb only
    # accepts the upper form and the lower one is the same request an operator
    # meant to make.
    return value.upper()


def _a_language_tag(value: str) -> str:
    if not _LANGUAGE.match(value):
        raise ValueError(
            f"{value!r} is not an ISO-639-1 code: TMDb wants two letters, optionally "
            "with a country, like 'fi' or 'fi-FI'"
        )
    return value


# ISO-3166-1 country codes and bare ISO-639-1 language codes are both exactly
# two letters, so this reuses ``_REGION``'s pattern under a name that says what
# it actually checks here, rather than a byte-identical second regex.
_BARE_TWO_LETTER_CODE = _REGION


def _a_bare_language_code(value: str) -> str:
    """``with_original_language`` is the one language field with no country half.

    TMDb matches it against a title's *original language*, which is a bare
    ISO-639-1 code -- ``fi-FI`` matches no title at all and TMDb says nothing
    about it, so a well-formed-looking regional tag would quietly produce an
    empty collection. ``language`` above is the opposite case and stays lenient.
    """
    if not _BARE_TWO_LETTER_CODE.match(value):
        raise ValueError(
            f"{value!r} is not a bare ISO-639-1 code: `with_original_language` matches "
            "a title's original language, which TMDb writes as two letters with no "
            "country, like 'fi' -- a regional tag like 'fi-FI' matches nothing"
        )
    return value.lower()


# ``sort_by``'s vocabulary is per endpoint, not shared, even though the
# parameter name is: TMDb sorts movies by revenue and shows by first air date,
# and neither key exists on the other endpoint. Load enforces membership in the
# union (a typo is caught at the edit); ``discover_filters`` enforces membership
# in the media type's own set, at build, where the media type is known.
SORT_BY: dict[str, frozenset[str]] = {
    "movie": frozenset(
        f"{key}.{direction}"
        for key in (
            "popularity",
            "release_date",
            "primary_release_date",
            "revenue",
            "original_title",
            "title",
            "vote_average",
            "vote_count",
        )
        for direction in ("asc", "desc")
    ),
    "tv": frozenset(
        f"{key}.{direction}"
        for key in (
            "popularity",
            "first_air_date",
            "name",
            "original_name",
            "vote_average",
            "vote_count",
        )
        for direction in ("asc", "desc")
    ),
}
_EVERY_SORT = SORT_BY["movie"] | SORT_BY["tv"]


def _a_known_sort(value: str) -> str:
    if value not in _EVERY_SORT:
        raise ValueError(
            f"unknown TMDb sort {value!r}: TMDb's discover sorts are "
            + ", ".join(sorted(_EVERY_SORT))
        )
    return value


_Country = Annotated[str, AfterValidator(_a_country_code)]
_Language = Annotated[str, AfterValidator(_a_language_tag)]
_OriginalLanguage = Annotated[str, AfterValidator(_a_bare_language_code)]
_Sort = Annotated[str, AfterValidator(_a_known_sort)]

# --- THE MATRIX --------------------------------------------------------------
#
# TMDb's documented `/discover/movie` (37 parameters) and `/discover/tv` (32)
# minus `page`, unioned: 21 shared, 16 movie-only, 11 tv-only. A row with no
# note is one whose scope is the obvious reading of its name.
#
# Types follow TMDb's documentation except where noted on the row. The
# comma/pipe-separated "AND/OR" filters (`with_genres: "18,80"`,
# `with_keywords: "9715|818"`) are typed `str` because that *is* their type on
# the wire; the model's `coerce_numbers_to_str` means the single-id form can
# still be written unquoted, as `PlexIdParams` allows for rating keys.
DISCOVER_PARAMS: tuple[DiscoverParam, ...] = (
    # -- shared: both endpoints document these ---------------------------------
    DiscoverParam(
        "language", _Language, "shared",
        # Accepted here although `TmdbEntityParams` deliberately refuses it for
        # the by-id builders, and the difference is real rather than a reversal:
        # discover can be sorted by `title`/`original_title`/`name`, which TMDb
        # orders on the *localised* title. Order is this builder's output --
        # it becomes the collection's custom order, and with a ten-page cap it
        # also decides which titles are inside it. It still does not filter.
        "sorted-on title is localised, so this changes order and therefore the cap",
    ),
    DiscoverParam("sort_by", _Sort, "shared", "name is shared; the vocabulary is not -- see SORT_BY"),
    DiscoverParam(
        "watch_region", _Country, "shared",
        "the companion every with/without_watch_* filter needs -- see COMPANION_RULES",
    ),
    DiscoverParam("with_watch_providers", str, "shared"),
    DiscoverParam("without_watch_providers", str, "shared"),
    DiscoverParam("with_watch_monetization_types", str, "shared"),
    DiscoverParam(
        "include_adult", bool, "shared", "TMDb defaults it to false; unset leaves that alone",
        wire_default=False,
    ),
    DiscoverParam("with_companies", str, "shared", "a company produces both films and shows"),
    DiscoverParam("without_companies", str, "shared"),
    DiscoverParam("with_genres", str, "shared"),
    DiscoverParam("without_genres", str, "shared"),
    DiscoverParam("with_keywords", str, "shared"),
    DiscoverParam("without_keywords", str, "shared"),
    DiscoverParam(
        "with_origin_country", str, "shared",
        # Left unvalidated although it holds country codes: TMDb documents it as
        # a free string and the comma/pipe form is legal, so a strict two-letter
        # check would refuse `US,GB`, a request TMDb answers.
        "documented as a free string; the AND/OR form makes a shape check wrong",
    ),
    DiscoverParam("with_original_language", _OriginalLanguage, "shared"),
    DiscoverParam("with_runtime.gte", int, "shared"),
    DiscoverParam("with_runtime.lte", int, "shared"),
    DiscoverParam("vote_average.gte", float, "shared"),
    DiscoverParam("vote_average.lte", float, "shared"),
    DiscoverParam(
        "vote_count.gte", int, "shared",
        # TMDb's schema types both as float32. A vote *count* is an integer, and
        # narrowing keeps the sent value `100` rather than `100.0`; the only
        # request this refuses is a fractional vote count, which means nothing.
        "narrowed from TMDb's float32: a count is an integer",
    ),
    DiscoverParam("vote_count.lte", int, "shared", "narrowed from TMDb's float32, as above"),
    # -- movie-only ------------------------------------------------------------
    DiscoverParam(
        "region", _Country, "movie",
        # The scope decision most worth checking. `/discover/movie` documents
        # `region` (it selects whose release dates the release-date and
        # release-type filters are read against); `/discover/tv` documents no
        # `region` at all and would ignore it. Note this is a *different*
        # question from `TmdbChartParams`'s region guard, which is about which
        # chart endpoints honour it.
        "/discover/tv documents no region; it would be silently ignored there",
    ),
    DiscoverParam("certification", str, "movie", "TV has no certification filter on discover"),
    DiscoverParam("certification.gte", str, "movie"),
    DiscoverParam("certification.lte", str, "movie"),
    DiscoverParam("certification_country", _Country, "movie"),
    DiscoverParam(
        "include_video", bool, "movie",
        "discover's video flag is a movie concept; /discover/tv has no equivalent",
    ),
    DiscoverParam("primary_release_year", int, "movie"),
    DiscoverParam("primary_release_date.gte", dt.date, "movie"),
    DiscoverParam("primary_release_date.lte", dt.date, "movie"),
    DiscoverParam("release_date.gte", dt.date, "movie"),
    DiscoverParam("release_date.lte", dt.date, "movie"),
    DiscoverParam("year", int, "movie", "TV's equivalent is first_air_date_year, a separate row"),
    DiscoverParam("with_cast", str, "movie", "credit filters are movie-only on discover"),
    DiscoverParam("with_crew", str, "movie", "credit filters are movie-only on discover"),
    DiscoverParam("with_people", str, "movie", "credit filters are movie-only on discover"),
    DiscoverParam(
        "with_release_type", str, "movie",
        # Documented as int32, typed str here for the same reason as the genre
        # filters: TMDb accepts the comma/pipe OR-form (`2|3`) and an int cannot
        # carry it.
        "widened from TMDb's int32 to carry the documented `2|3` OR-form",
    ),
    # -- tv-only ---------------------------------------------------------------
    DiscoverParam("air_date.gte", dt.date, "tv", "an episode air-date window; films do not air"),
    DiscoverParam("air_date.lte", dt.date, "tv"),
    DiscoverParam("first_air_date.gte", dt.date, "tv"),
    DiscoverParam("first_air_date.lte", dt.date, "tv"),
    DiscoverParam("first_air_date_year", int, "tv"),
    DiscoverParam("include_null_first_air_dates", bool, "tv"),
    DiscoverParam("screened_theatrically", bool, "tv", "asks whether a *show* was screened in cinemas"),
    DiscoverParam(
        "timezone", str, "tv",
        "resolves /discover/tv's air-date filters; movie release dates use region",
    ),
    DiscoverParam(
        "with_networks", str, "tv",
        # Widened from the obvious `int` for the same reason as
        # `with_release_type` above: TMDb accepts the comma/pipe OR-form
        # (`213|49`) and an int cannot carry it.
        "TMDb has no movie form of a network, in any API; widened to carry the "
        "documented `213|49` OR-form, as with_release_type",
    ),
    DiscoverParam("with_status", str, "tv", "a series' production status: 0 Returning .. 5 Pilot"),
    DiscoverParam("with_type", str, "tv", "a series' type: 0 Documentary .. 6 Video"),
)

# TMDb name -> row. Also the uniqueness proof: a duplicated name would collapse
# here and the model would come out short a field.
BY_NAME: dict[str, DiscoverParam] = {row.name: row for row in DISCOVER_PARAMS}

# Python identifier -> row, the same map keyed the other way: the empty-guard
# below walks ``model_fields_set``, which yields ``field`` values, not TMDb's
# ``name``.
BY_FIELD: dict[str, DiscoverParam] = {row.field: row for row in DISCOVER_PARAMS}

# Filters TMDb documents as needing a companion, and which one. Both of these
# are silent-no-op hazards rather than errors: TMDb answers a watch-provider
# filter with no `watch_region` by ignoring the filter, so the operator gets
# every title instead of the ones on their streaming service. Unlike scope,
# this is knowable at load -- both halves live in the same params block -- so
# it is refused at the moment of the edit.
COMPANION_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("with_watch_providers", "without_watch_providers", "with_watch_monetization_types"),
        "watch_region",
    ),
    (("certification", "certification.gte", "certification.lte"), "certification_country"),
)

# The four names that order or scope a query rather than filter it: `sort_by`
# alone (or alongside `language`/`region`/`watch_region`) still builds TMDb's
# default-ordered, unfiltered result -- `tmdb_chart` under another name -- so
# the "at least one attribute" guard below must not count them.
#
# The *filtering* set is derived as everything else in the matrix, rather than
# hand-picked, so a row added to ``DISCOVER_PARAMS`` later is a filter by
# default and the guard cannot silently forget it.
_NON_FILTERING_NAMES = frozenset({"sort_by", "language", "region", "watch_region"})
_FILTERING_FIELDS: frozenset[str] = frozenset(
    row.field for row in DISCOVER_PARAMS if row.name not in _NON_FILTERING_NAMES
)


class _TmdbDiscoverParamsBase(BaseModel):
    """Config and the cross-field rules; the fields themselves are generated.

    ``extra="forbid"`` is what makes a mis-spelled TMDb parameter a load error
    rather than a filter that quietly never applies, and it is load-enforced for
    free through ``CollectionDefinition._params_must_satisfy_the_builders_own_model``.
    Note the interaction with the generated fields' aliases: every field carries
    TMDb's name as its alias and pydantic does not populate by field name, so
    ``vote_average_gte:`` -- the identifier rather than TMDb's spelling -- is an
    unknown key here too. There is exactly one spelling, and it is TMDb's.
    """

    # coerce_numbers_to_str, as ``PlexIdParams`` has it: TMDb's id-list filters
    # are strings on the wire and YAML hands over `with_genres: 18` as an int.
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    @model_validator(mode="after")
    def _needs_at_least_one_attribute(self) -> "_TmdbDiscoverParamsBase":
        """An empty discover is the popularity chart under another name.

        TMDb answers an unfiltered ``/discover`` with its default sort, so this
        would build "two hundred arbitrarily popular titles" -- which
        ``tmdb_chart`` already builds deliberately, and which in ``sync_mode``
        would write those titles into a collection that was meant to say
        something. An operator who wrote no attributes did not mean this --
        and neither did one who wrote only ``sort_by`` (or ``language``,
        ``region``, ``watch_region``): none of those four filter anything, so
        ``{sort_by: popularity.desc}`` alone is the exact same unfiltered
        result with a redundant order on top. Only ``_FILTERING_FIELDS``
        counts here; see its definition for why that set, not this guard, is
        where a newly added row has to be excluded.

        A value equal to TMDb's own documented default is the same hole with
        the opposite shape: ``{include_adult: false}`` alone sets a field, but
        ``false`` is what TMDb already assumes with the field unset, so the
        request sent is the unfiltered query again -- the exact outcome this
        guard exists to refuse, and the exact hole ``votes_gte: ge=1`` closes
        on the IMDb search side. ``{include_adult: true}`` genuinely widens
        the query and counts normally. ``DiscoverParam.wire_default`` carries
        this per row; see its docstring for which rows have one.
        """
        if not any(
            name in _FILTERING_FIELDS
            and (value := getattr(self, name)) is not None
            and value != BY_FIELD[name].wire_default
            for name in self.model_fields_set
        ):
            raise ValueError(
                "tmdb_discover needs at least one attribute: an unfiltered discover is "
                "TMDb's popularity chart, which `tmdb_chart` already builds"
            )
        return self

    @model_validator(mode="after")
    def _filters_that_need_a_companion_have_one(self) -> "_TmdbDiscoverParamsBase":
        for names, companion in COMPANION_RULES:
            if getattr(self, BY_NAME[companion].field) is not None:
                continue
            present = [
                name for name in names if getattr(self, BY_NAME[name].field) is not None
            ]
            if present:
                raise ValueError(
                    f"{', '.join(present)} needs `{companion}` set alongside it: TMDb "
                    f"ignores the filter without it and answers the unfiltered query, "
                    "so the collection would silently hold every title instead"
                )
        return self


# The model, generated from the matrix -- see the module docstring for why this
# is generated rather than hand-written and checked. Every field is optional and
# defaults to None, and only the ones the operator actually wrote are sent:
# ``exclude_unset`` in ``discover_filters`` is what keeps an unconfigured
# parameter out of the request, and therefore out of the cache key.
TmdbDiscoverParams = create_model(
    "TmdbDiscoverParams",
    __base__=_TmdbDiscoverParamsBase,
    __module__=__name__,
    **{
        row.field: (row.type | None, Field(default=None, alias=row.name))
        for row in DISCOVER_PARAMS
    },
)


class TmdbSortUnsupported(Exception):
    """``sort_by`` was set to a key this media type's ``/discover`` has no sort for.

    Its own class rather than reusing ``LibraryTypeMismatch`` -- as
    ``TmdbRegionUnsupported`` in ``builders/tmdb.py`` reuses neither -- because
    a wrong sort key is not a library/media-type mismatch, and the engine's log
    line, which carries only the exception class name, would otherwise mislabel
    it as one. ``sort_by`` is a shared parameter name whose vocabulary is not:
    see ``SORT_BY``.
    """


def discover_filters(params: BaseModel, media_type: str, library_type: str) -> dict[str, object]:
    """The wire filters for one build, or a refusal naming the offending field.

    ``by_alias`` is the alias map doing its one job: the keys that come out are
    the matrix's ``name`` column, dots and all, which is exactly what TMDb reads.
    ``mode="json"`` is not cosmetic -- a ``date`` field would otherwise leave here
    as a ``datetime.date``, which httpx cannot encode into a query string.
    """
    filters = {
        name: value
        for name, value in params.model_dump(mode="json", by_alias=True, exclude_unset=True).items()
        # An explicit `region:` with nothing after it is YAML for None, and
        # sending `region=` empty is not what the operator wrote.
        if value is not None
    }
    for name in filters:
        row = BY_NAME[name]
        if row.scope in ("shared", media_type):
            continue
        wanted = "Movie" if row.scope == "movie" else "Show"
        raise LibraryTypeMismatch(
            f"`{name}` is a TMDb /discover/{row.scope} filter, but this pass is running "
            f"against a {library_type} library, where TMDb reads /discover/{media_type} "
            f"-- which does not accept it and would silently answer the unfiltered "
            f"query instead. Narrow the definition with `libraries:` so it only targets "
            f"{wanted} libraries, or drop `{name}`."
        )
    sort = filters.get("sort_by")
    if sort is not None and sort not in SORT_BY[media_type]:
        raise TmdbSortUnsupported(
            f"TMDb cannot sort /discover/{media_type} by {sort!r}, so this pass against a "
            f"{library_type} library would fall back to its default order. The sorts "
            f"/discover/{media_type} accepts are " + ", ".join(sorted(SORT_BY[media_type]))
        )
    return filters


class TmdbDiscoverBuilder(_TmdbBuilder):
    """A TMDb discover query, in TMDb's order, for this library's media type.

    ``media_types`` maps the library type to the discover endpoint that answers
    for it and doubles as the mismatch guard's allowed set, exactly as the
    narrow by-id discover builders in ``builders/tmdb.py`` use theirs. Both
    entries are present because discover has a movie form and a TV form; which
    *filters* survive the crossing is the matrix's business, one layer down.
    """

    type_name = "tmdb_discover"
    params_model = TmdbDiscoverParams
    media_types = {"Movie": "movie", "Show": "tv"}

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbDiscoverParams.model_validate(ctx.config)
        require_library_type(
            f"the {self.type_name!r} builder", ctx.library_type, self.media_types
        )
        media_type = self.media_types[ctx.library_type]
        filters = discover_filters(params, media_type, ctx.library_type)
        client = self._client(ctx)
        ids = await client.discover(media_type, filters)
        # The streaming pack's fifteen definitions differ from every other
        # discover definition -- and from each other -- by exactly one field:
        # the watch-provider id. That id is what names the service, so it is
        # what resolves the poster; a discover definition that carries no
        # provider is not a streaming collection and keeps no default artwork.
        # Upstream keys the files by service NAME
        # (``.superpowers/sdd/p-defimg-probe.md`` §6), which the table
        # translates, and a provider the table does not name gets None rather
        # than a URL built from a number upstream never used.
        service = STREAMING_NAMES.get(str(ctx.config.get("with_watch_providers", "")))
        return BuilderResult(
            ids=[("tmdb", value) for value in ids],
            poster_kind="streaming" if service else None,
            poster_key=service,
        )
