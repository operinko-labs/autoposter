"""The award-ceremony collections, as builders.

Two shapes, one dataset per ceremony. ``imdb_award`` builds a static winners
collection for a set of categories across every ceremony; the expanding
builders build one collection per recent ceremony year -- and since which years
those are moves with the dataset, that definition *expands* at run time into
the concrete collections rather than being five definitions somebody has to
edit every January.

The whole event file holds every year, so all of one ceremony's collections
resolve from a single fetch. That is not an optimisation to preserve loosely:
the fetch is memoised on the pass's ``run_cache``, failure included, so a dead
source is requested once and every collection that needed it is left untouched.
The memo is keyed by event id, so two ceremonies in one pass cost one fetch
each rather than sharing one wrong answer.

**Ceremonies live in ``EVENTS``**, one row each: the event id, the award
vocabularies, the collection titles and summaries, and the year-title format.
Every string in a row is transcribed from Kometa -- its ``defaults/award/*.yml``
and the ``Kometa-Team/Translations`` file those name through
``translation_key`` -- because these collections exist to replace Kometa's, and
prose we invented would be a difference an operator did not ask for. A field
Kometa has no value for is ``None``; nothing here is guessed.

**Addressing a ceremony.** The static builder takes it as a param
(``params: {event: golden_globes, award: best_picture}``), which is the shape
Kometa itself uses (``imdb_award: {event_id: ...}``) and keeps one builder for
every ceremony. The *expanding* builder cannot: its collections' titles exist
only at run time and are recognised by ``TITLE_PATTERN``, which
``engine.definition_titles`` (:702-732) reads off the **registry entry**, with
no definition in hand -- so one class serving every ceremony could only offer
one pattern for all of them. That pattern would then be wrong in both
directions: broad enough to match every ceremony, it hides another ceremony's
leftover collections from the leftovers report; narrow enough to match one, it
tells the delete sweep that a ceremony we *do* build is unmanaged, and the
sweep deletes it. So the expanding builder is registered once per ceremony
(``AwardEvent.years_builder`` names each registration), and the asymmetry
between the two builders is the engine's requirement rather than a preference.
"""
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, model_validator

from autoposter.collections.awards import (
    BEST_DIRECTOR,
    BEST_PICTURE,
    EVENT_ID,
    GOLDEN_GLOBES_BEST_DIRECTOR,
    GOLDEN_GLOBES_BEST_PICTURE,
    fetch_event,
    fetch_event_validation,
    recent_years,
    require_known_event,
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


@dataclass(frozen=True)
class AwardEvent:
    """One ceremony: everything that differs between them, in one place.

    ``awards`` is ``award key -> (collection title, categories, summary,
    poster stem)``, the static winner collections. The poster stem is the file
    name under this event's ``Default-Images`` folder, or ``None`` when the
    repository has no image for it -- in which case the collection keeps no
    poster rather than being pointed at a guessed path
    (``collections/posters.AWARD_SEGMENTS`` maps the folder).

    ``year_pattern`` must not be able to match another ceremony's year titles;
    ``tests/test_collection_awards.py`` holds both directions of that to the
    two shipped patterns.
    """

    key: str
    name: str
    event_id: str
    awards: dict[str, tuple[str, tuple[str, ...], str | None, str | None]]
    year_title: str
    year_summary: str | None
    year_pattern: re.Pattern[str]
    years_builder: str
    # §2.4: the dynamic year collections override collection_order to release;
    # only the static winner collections keep the custom order.
    year_sort: str = field(default="release")


# Kometa sources, all fetched 2026-08-25:
#   Oscars      defaults/award/oscars.yml + translations en.yml (oscars_*)
#   Golden Globes defaults/award/golden.yml + translations en.yml (golden_*)
EVENTS: dict[str, AwardEvent] = {
    "oscars": AwardEvent(
        key="oscars",
        name="Oscars",
        event_id=EVENT_ID,
        awards={
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
        },
        year_title="Oscars Winners %s",
        year_summary="Academy Awards (Oscars) Winners for %s.",
        # The dynamic titles are not statically listable, so the leftovers
        # report recognises them by the pattern instead -- see
        # ``engine.definition_titles``.
        year_pattern=re.compile(r"^Oscars Winners \d{4}$"),
        years_builder="imdb_award_years",
    ),
    "golden_globes": AwardEvent(
        key="golden_globes",
        name="Golden Globes",
        event_id="ev0000292",
        awards={
            "best_picture": (
                "Golden Globes Best Picture Winners",
                GOLDEN_GLOBES_BEST_PICTURE,
                "Golden Globes Best Picture Winners.",
                "best_picture_winner",
            ),
            "best_director": (
                "Golden Globes Best Director Winners",
                GOLDEN_GLOBES_BEST_DIRECTOR,
                "Golden Globes Best Director Winners.",
                "best_director_winner",
            ),
        },
        # Kometa's ``title_format: Golden Globe <<key_name>>`` and its
        # ``golden_year`` translation, verbatim -- the ceremony's year
        # collections are "Golden Globe 2026", not "Golden Globes Winners
        # 2026", and the difference is what keeps this pattern and the Oscars
        # one incapable of matching each other's titles.
        year_title="Golden Globe %s",
        year_summary="%s Golden Globe Winners.",
        year_pattern=re.compile(r"^Golden Globe \d{4}$"),
        years_builder="golden_globes_award_years",
    ),
}

_VALIDATION = "imdb_award.validation"


async def _memoised(ctx: BuilderContext, key: str, fetch):
    """One fetch per pass, failure included.

    Re-raising a memoised failure rather than returning None keeps the builder
    contract -- ``build`` raises, the engine contains -- and the engine's
    containment is what turns this into "leave the collection alone", exactly
    as the ``_award_event`` wrapper this replaced did.
    """
    if key not in ctx.run_cache:
        try:
            ctx.run_cache[key] = await fetch()
        except Exception as error:  # noqa: BLE001 - memoised and re-raised below
            ctx.run_cache[key] = error
    value = ctx.run_cache[key]
    if isinstance(value, BaseException):
        raise value
    return value


async def _event(ctx: BuilderContext, event: AwardEvent) -> dict:
    """This ceremony's dataset, fetched at most once per pass.

    The validation list is consulted first and memoised separately: it is one
    file for every ceremony, so a pass building two of them still asks for it
    once, and an event the dataset dropped is refused in words rather than
    surfacing as a 404 on the event file.
    """
    validation = await _memoised(
        ctx, _VALIDATION, lambda: fetch_event_validation(ctx.http)
    )
    require_known_event(validation, event.event_id)
    return await _memoised(
        ctx,
        "imdb_award.event:%s" % event.event_id,
        lambda: fetch_event(ctx.http, event.event_id),
    )


def _poster(event: AwardEvent, stem: str | None) -> str | None:
    """The poster key for one of this event's images, event-scoped.

    ``collections/posters.py`` splits it: an event it has no folder for keeps
    no poster, rather than every ceremony inheriting the Oscars' artwork,
    which is what a bare stem would have meant.
    """
    return None if stem is None else "%s:%s" % (event.key, stem)


class ImdbAwardParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Defaulted, so ``params: {award: best_picture}`` -- what the shipped
    # Oscars definitions carry -- keeps meaning exactly what it always did.
    event: str = "oscars"
    award: str

    @model_validator(mode="after")
    def _must_be_a_known_award(self) -> "ImdbAwardParams":
        """Both halves, together: an award key means nothing without its
        ceremony. ``best_picture`` exists for both shipped events and could
        exist for neither of the next two."""
        if self.event not in EVENTS:
            raise ValueError(
                f"unknown award event {self.event!r}: known events are "
                + ", ".join(sorted(EVENTS))
            )
        awards = EVENTS[self.event].awards
        if self.award not in awards:
            raise ValueError(
                f"unknown {EVENTS[self.event].name} award {self.award!r}: "
                "known awards are " + ", ".join(sorted(awards))
            )
        return self


class ImdbAwardBuilder:
    """Every winner of one award, across every ceremony, newest first."""

    type_name = "imdb_award"
    params_model = ImdbAwardParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbAwardParams.model_validate(ctx.config)
        event = EVENTS[params.event]
        _, categories, summary, stem = event.awards[params.award]
        data = await _event(ctx, event)
        poster_key = _poster(event, stem)
        return BuilderResult(
            ids=[("imdb", imdb_id) for imdb_id in winners_for_categories(data, categories)],
            summary=summary,
            # Both or neither: ``lists.reconcile_list_collection`` runs the
            # poster step only when it has a kind *and* a key.
            poster_kind="award_static" if poster_key else None,
            poster_key=poster_key,
        )


class ImdbAwardYearParams(BaseModel):
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    year: str


class ImdbAwardYearsBuilder:
    """One collection per recent ceremony year, for one ceremony.

    The definition an operator (or ``default_definitions``) writes is a single
    entry with no year in it; ``expand`` turns it into the concrete collections
    for whichever years the dataset currently has data for. Keeping the
    expansion here rather than in the definition list is what lets the config
    stay still while the collections track the ceremonies.

    Registered once per ceremony -- see the module docstring for why the event
    cannot be a param here the way it is on ``imdb_award``. The instance
    carries its own ``type_name`` and ``TITLE_PATTERN``; both come from the
    registry row, so the name an operator writes and the pattern the leftovers
    report matches cannot drift apart.
    """

    params_model = ImdbAwardYearParams

    def __init__(self, event: str = "oscars") -> None:
        self.event = EVENTS[event]
        self.type_name = self.event.years_builder
        self.TITLE_PATTERN = self.event.year_pattern

    async def expand(self, ctx: BuilderContext) -> list[CollectionDefinition]:
        event = self.event
        data = await _event(ctx, event)
        return [
            CollectionDefinition(
                title=event.year_title % year,
                builder=self.type_name,
                params={"year": year},
                sort=event.year_sort,
                # Set only when the ceremony has one: passing ``summary=None``
                # would mark the field as set, and the engine's ``_completed``
                # reads ``model_fields_set`` -- so a None here would stop the
                # placeholder's own summary reaching the year collections.
                **({"summary": event.year_summary % year} if event.year_summary else {}),
            )
            for year in recent_years(data)
        ]

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbAwardYearParams.model_validate(ctx.config)
        event = self.event
        data = await _event(ctx, event)
        return BuilderResult(
            ids=[
                ("imdb", imdb_id) for imdb_id in winners_for_year(data, params.year)
            ],
            summary=event.year_summary % params.year if event.year_summary else None,
            poster_kind="award_year",
            poster_key=_poster(event, params.year),
        )
