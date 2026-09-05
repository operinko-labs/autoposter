"""The TVDb builders: one list, and the two explicit-id forms.

The transport is ``providers/tvdb.py`` -- the client that already exists for
artwork, whose login dance and one-shot re-login on a 401 the list endpoint
inherits by going through the same ``_authenticated_get``. Nothing about
authentication is restated here.

What the builders decide is which half of a list this library meant. A v4 list
entity is ``{"order": n, "seriesId": …, "movieId": …}`` -- one or the other,
never both -- so a TVDb list is *inherently* mixed and cannot be typed as a
whole the way a TMDb franchise collection can. There is therefore nothing to
refuse at the definition level and everything to decide per entry: a Show
library reads ``seriesId`` and a Movie library reads ``movieId``, and the other
kind is dropped.

Dropping it rather than passing it through matters, because TVDb's series ids
and movie ids are *different id spaces sharing one namespace*. Offering a movie
id to a Show library does not merely fail to resolve -- it can resolve to the
series that happens to have that number, which is a plausible, wrong
collection. That is the same collision ``simple_ids`` refuses ``tmdb_movie`` on
a Show library for, and it is why ``tvdb_movie``/``tvdb_show`` -- which do no
network at all and are otherwise just their params model -- still carry a
library-type guard.
"""
import logging
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)

logger = logging.getLogger(__name__)

__all__ = [
    "TvdbBuilderRefused",
    "TvdbIdParams",
    "TvdbListBuilder",
    "TvdbListParams",
    "TvdbMovieBuilder",
    "TvdbShowBuilder",
]

# Which field of a list entity this library's items are, per library type. It
# doubles as the mismatch guard's allowed set -- a library type with no entity
# kind is a library this builder can mean nothing for.
_ENTITY_FIELD = {"Movie": "movieId", "Show": "seriesId"}

# A TVDb slug, and nothing else -- the same one-line pattern as MDBList's
# ``_LIST_REFERENCE``. Unreserved characters only, so it needs no escaping
# into the path, and it refuses a pasted list URL (which would otherwise
# become a path with extra slashes and a 404 to read out of a log).
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]*$")


class TvdbBuilderRefused(Exception):
    """This deployment cannot build from TVDb.

    Only ever "no API key was configured". Everything TVDb itself refuses is
    ``TVDBListRefused``, raised by the client that knows what was asked.
    """


class TvdbListParams(BaseModel):
    """``tvdb_list``'s params: the list, named either way TVDb names it.

    ``id`` is the numeric list id and ``slug`` is the name in a list's URL.
    Exactly one, because they address the same thing and accepting both would
    leave a definition where the two disagree silently building whichever the
    code happened to check first.

    ``gt=0`` for ``TmdbEntityParams``' reason: ``0`` is what a mis-read config
    or an unfilled template renders to, and TVDb answers it with a 404 the
    operator then has to go and read out of a log.

    ``slug`` is restricted to unreserved characters (``_SLUG``), the same
    one-line pattern as MDBList's ``_LIST_REFERENCE``: it is interpolated
    straight into the request path, so a pasted list URL or a value carrying
    ``/``, ``?``, ``#`` would otherwise change which request is made rather
    than failing cleanly here.
    """

    model_config = ConfigDict(extra="forbid")

    id: int | None = Field(default=None, gt=0)
    slug: str | None = None

    @field_validator("slug")
    @classmethod
    def _must_be_a_bare_slug(cls, value: str | None) -> str | None:
        if value is not None and not _SLUG.match(value):
            raise ValueError(
                "that is not a TVDb list slug: write the name from the "
                "list's URL (slug: a-mixed-tvdb-list), not a full URL or a "
                "value containing '/'"
            )
        return value

    @model_validator(mode="after")
    def _exactly_one_way_of_naming_it(self) -> "TvdbListParams":
        if (self.id is None) == (self.slug is None):
            raise ValueError(
                "a tvdb_list needs exactly one of `id:` (the numeric list id) and "
                "`slug:` (the name in the list's URL)"
            )
        return self


class TvdbIdParams(BaseModel):
    """``ids:``, non-empty, every value a TVDb id.

    ``coerce_numbers_to_str`` because YAML hands over ``ids: [81189]`` as an
    integer and an id value is a string in every namespace;
    ``extra="forbid"`` so ``id:`` for ``ids:`` is an error rather than an empty
    default. The digit check catches the one mix-up the library-type guard
    cannot: an IMDb id typed under a TVDb builder resolves to nothing and is
    reported as a count, which looks exactly like a collection of titles the
    library does not own.
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    ids: list[str] = Field(min_length=1)

    @field_validator("ids")
    @classmethod
    def _must_look_like_tvdb_ids(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.isdigit():
                raise ValueError(
                    "that is not a TVDb id: TVDb ids are numbers, like "
                    "'81189'. An id starting 'tt' is an IMDb id -- use the imdb_id "
                    "builder for those."
                )
        return values


class TvdbListBuilder:
    """One TVDb list, in list order, as the ids this library can resolve."""

    type_name = "tvdb_list"
    params_model = TvdbListParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TvdbListParams.model_validate(ctx.config)
        require_library_type("the 'tvdb_list' builder", ctx.library_type, _ENTITY_FIELD)
        client = ctx.sources.tvdb
        if client is None:
            raise TvdbBuilderRefused(
                "TVDb is not configured for this deployment (no API key), so it has "
                "no list to build from"
            )

        field = _ENTITY_FIELD[ctx.library_type]
        entities = await client.list_entities(list_id=params.id, slug=params.slug)
        ids = []
        other_kind = 0
        for entity in entities:
            value = entity.get(field)
            if value is None:
                # Either an entity of the other kind -- ordinary, a TVDb list
                # holds both -- or one naming neither, which is one member the
                # list cannot place. Neither is a reason to fail the whole
                # definition; see the module docstring for why neither is a
                # reason to emit it either.
                other_kind += 1
                continue
            ids.append(("tvdb", str(value)))
        if other_kind:
            logger.debug(
                "%s: TVDb list %r: skipped %d entr%s with no %s",
                ctx.library, params.slug or params.id, other_kind,
                "y" if other_kind == 1 else "ies", field,
            )
        return BuilderResult(ids=ids)


class _TvdbIdBuilder:
    """What ``tvdb_movie`` and ``tvdb_show`` share, which is everything except
    the library each name claims to be for. The ``simple_ids`` shape."""

    params_model = TvdbIdParams
    library_types: tuple[str, ...]

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TvdbIdParams.model_validate(ctx.config)
        require_library_type(
            f"the {self.type_name!r} builder", ctx.library_type, self.library_types
        )
        return BuilderResult(ids=[("tvdb", value) for value in params.ids])


class TvdbMovieBuilder(_TvdbIdBuilder):
    """A hand-picked collection, by TVDb movie id."""

    type_name = "tvdb_movie"
    library_types = ("Movie",)


class TvdbShowBuilder(_TvdbIdBuilder):
    """A hand-picked collection, by TVDb series id."""

    type_name = "tvdb_show"
    library_types = ("Show",)
