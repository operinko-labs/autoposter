"""The Oscars collections, as builders.

Two shapes, one dataset. ``imdb_award`` builds a static winners collection for a
set of categories across every ceremony; ``imdb_award_years`` builds one
collection per recent ceremony year -- and since which years those are moves
with the dataset, that definition *expands* at run time into the concrete
collections rather than being five definitions somebody has to edit every
January.

The whole event file holds every year, so all seven collections resolve from a
single fetch. That is not an optimisation to preserve loosely: the fetch is
memoised on the pass's ``run_cache``, failure included, so a dead source is
requested once and every collection that needed it is left untouched.
"""
import re

from pydantic import BaseModel, ConfigDict, field_validator

from autoposter.collections.awards import (
    BEST_DIRECTOR,
    BEST_PICTURE,
    fetch_event,
    recent_years,
    winners_for_categories,
    winners_for_year,
)
from autoposter.collections.builders.base import BuilderContext, BuilderResult
from autoposter.config.schema import CollectionDefinition

_OSCAR_SUMMARY = (
    "The Academy Award for Best %s is one of the Academy Awards presented "
    "annually by the Academy of Motion Picture Arts and Sciences since the "
    "awards debuted in 1929."
)

# award key -> (collection title, categories, summary, poster key). The two
# static winner collections, movies only.
AWARDS: dict[str, tuple[str, tuple[str, ...], str, str]] = {
    "best_picture": (
        "Oscars Best Picture Winners",
        BEST_PICTURE,
        _OSCAR_SUMMARY % "Picture",
        "best_picture_winner",
    ),
    "best_director": (
        "Oscars Best Director Winners",
        BEST_DIRECTOR,
        _OSCAR_SUMMARY % "Director",
        "best_director_winner",
    ),
}

YEAR_TITLE = "Oscars Winners %s"
YEAR_SUMMARY = "Academy Awards (Oscars) Winners for %s."

# §2.4: the dynamic year collections override collection_order to release; only
# the two static winner collections keep the custom order.
YEAR_SORT = "release"

# The dynamic titles are not statically listable, so the leftovers report
# recognises them by the pattern instead -- see ``engine.definition_titles``.
TITLE_PATTERN = re.compile(r"^Oscars Winners \d{4}$")

_EVENT = "imdb_award.event"


async def _event(ctx: BuilderContext) -> dict:
    """The ceremony dataset, fetched at most once per pass.

    A failure is memoised as the exception and re-raised, so the seven
    collections that share this dataset cost one request whether it works or
    not. Re-raising rather than returning None keeps the builder contract --
    ``build`` raises, the engine contains -- and the engine's containment is
    what turns this into "leave the collection alone", exactly as the
    ``_award_event`` wrapper it replaces did.
    """
    if _EVENT not in ctx.run_cache:
        try:
            ctx.run_cache[_EVENT] = await fetch_event(ctx.http)
        except Exception as error:  # noqa: BLE001 - memoised and re-raised below
            ctx.run_cache[_EVENT] = error
    event = ctx.run_cache[_EVENT]
    if isinstance(event, BaseException):
        raise event
    return event


class ImdbAwardParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    award: str

    @field_validator("award")
    @classmethod
    def _must_be_a_known_award(cls, v: str) -> str:
        if v not in AWARDS:
            raise ValueError(
                f"unknown Oscars award {v!r}: known awards are " + ", ".join(sorted(AWARDS))
            )
        return v


class ImdbAwardBuilder:
    """Every winner of one award, across every ceremony, newest first."""

    type_name = "imdb_award"
    params_model = ImdbAwardParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbAwardParams.model_validate(ctx.config)
        _, categories, summary, poster_key = AWARDS[params.award]
        event = await _event(ctx)
        return BuilderResult(
            ids=[("imdb", imdb_id) for imdb_id in winners_for_categories(event, categories)],
            summary=summary,
            poster_kind="award_static",
            poster_key=poster_key,
        )


class ImdbAwardYearParams(BaseModel):
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    year: str


class ImdbAwardYearsBuilder:
    """One collection per recent ceremony year.

    The definition an operator (or ``default_definitions``) writes is a single
    entry with no year in it; ``expand`` turns it into the concrete collections
    for whichever years the dataset currently has data for. Keeping the
    expansion here rather than in the definition list is what lets the config
    stay still while the collections track the ceremonies.
    """

    type_name = "imdb_award_years"
    params_model = ImdbAwardYearParams
    TITLE_PATTERN = TITLE_PATTERN

    async def expand(self, ctx: BuilderContext) -> list[CollectionDefinition]:
        event = await _event(ctx)
        return [
            CollectionDefinition(
                title=YEAR_TITLE % year,
                builder=self.type_name,
                params={"year": year},
                summary=YEAR_SUMMARY % year,
                sort=YEAR_SORT,
            )
            for year in recent_years(event)
        ]

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbAwardYearParams.model_validate(ctx.config)
        event = await _event(ctx)
        return BuilderResult(
            ids=[
                ("imdb", imdb_id) for imdb_id in winners_for_year(event, params.year)
            ],
            summary=YEAR_SUMMARY % params.year,
            poster_kind="award_year",
            poster_key=params.year,
        )
