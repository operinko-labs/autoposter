"""The explicit-id builders: a collection is the ids the operator listed.

``plex_id`` (``base.py``) is the same idea against rating keys. These three are
the external-namespace forms, and they exist for the case a chart or a list
cannot express: a hand-curated set of titles the operator maintains in the
config itself.

They do no network, no translation and no lookup, which makes the params model
the entire builder -- and the entire *guard*. That matters more here than it
looks. An id in the wrong namespace is a well-formed config: ``ids: [438631]``
under ``imdb_id`` produces ``("imdb", "438631")``, which resolves to nothing,
and an id the library does not own is already an ordinary outcome the engine
reports as a count rather than an error. So a namespace mix-up would build a
smaller collection every pass and never say why. Each model therefore refuses
the *shape* of the other namespace's ids, and says which one it wanted.
"""
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    PlexIdBuilder,
)

__all__ = [
    "ImdbIdBuilder",
    "ImdbIdParams",
    "PlexRatingKeyBuilder",
    "TmdbMovieBuilder",
    "TmdbShowBuilder",
    "TmdbIdParams",
]

# IMDb title ids are "tt" and at least one digit. The digit count is not fixed
# (seven today, eight for recent titles), so it is deliberately not pinned.
_IMDB_ID = re.compile(r"^tt\d+$")


class _IdListParams(BaseModel):
    """``ids:``, non-empty, every value a string.

    ``coerce_numbers_to_str`` for ``PlexIdParams``' reason: YAML hands over
    ``ids: [438631]`` as an integer, an id value is a string in every
    namespace, and refusing the unquoted form would be a pointless failure.
    ``extra="forbid"`` so ``id:`` for ``ids:`` is an error rather than an
    empty default.
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    ids: list[str] = Field(min_length=1)


class ImdbIdParams(_IdListParams):
    @field_validator("ids")
    @classmethod
    def _must_look_like_imdb_ids(cls, values: list[str]) -> list[str]:
        for value in values:
            if not _IMDB_ID.match(value):
                raise ValueError(
                    f"{value!r} is not an IMDb id: IMDb ids look like 'tt0111161'. "
                    "A bare number is a TMDb or TVDb id, which belongs to a "
                    "builder for that namespace."
                )
        return values


class TmdbIdParams(_IdListParams):
    @field_validator("ids")
    @classmethod
    def _must_look_like_tmdb_ids(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.isdigit():
                raise ValueError(
                    f"{value!r} is not a TMDb id: TMDb ids are numbers, like "
                    "'438631'. An id starting 'tt' is an IMDb id -- use the "
                    "imdb_id builder for those."
                )
        return values


class ImdbIdBuilder:
    """A hand-picked collection, by IMDb id."""

    type_name = "imdb_id"
    params_model = ImdbIdParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbIdParams.model_validate(ctx.config)
        return BuilderResult(ids=[("imdb", value) for value in params.ids])


class TmdbMovieBuilder:
    """A hand-picked collection, by TMDb movie id."""

    type_name = "tmdb_movie"
    params_model = TmdbIdParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbIdParams.model_validate(ctx.config)
        return BuilderResult(ids=[("tmdb", value) for value in params.ids])


class TmdbShowBuilder:
    """A hand-picked collection, by TMDb show id.

    Identical to ``tmdb_movie`` in what it produces -- TMDb movie and TV ids
    share the ``tmdb`` namespace, and the library the pass is running against
    already decides which kind of item an id can resolve to. The two names
    exist because Kometa has two, and an operator porting a config writes the
    one their Kometa config used.
    """

    type_name = "tmdb_show"
    params_model = TmdbIdParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbIdParams.model_validate(ctx.config)
        return BuilderResult(ids=[("tmdb", value) for value in params.ids])


class PlexRatingKeyBuilder(PlexIdBuilder):
    """``plex_id`` under Kometa's other name for it.

    Kometa documents ``plex_id`` and ``plex_rating_key`` as one builder with
    two spellings, so a config being ported can arrive with either. The
    registry keys on ``type_name`` and refuses a duplicate -- deliberately,
    since the loser of an overwrite would still be named in someone's config
    -- so an alias cannot be a second key pointing at the same object. It is a
    second *registration*: its own name, its own instance, the behaviour
    inherited rather than restated, which is what keeps the two names from
    ever drifting apart -- ``params_model`` and ``build`` are the base class's,
    so a change to ``plex_id`` is a change to both by construction.
    """

    type_name = "plex_rating_key"
