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
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, model_validator

from autoposter.collections.awards import (
    BAFTA_BEST_FILM,
    BERLINALE_GOLDEN_BEAR_AWARDS,
    BEST_DIRECTOR,
    BEST_PICTURE,
    CANNES_PALME_DOR_AWARDS,
    CESAR_AWARDS,
    CESAR_BEST_FILM,
    CRITICS_CHOICE_BEST_PICTURE,
    EMMY_BEST_IN_CATEGORY,
    EVENT_ID,
    GOLDEN_GLOBES_BEST_DIRECTOR,
    GOLDEN_GLOBES_BEST_PICTURE,
    PEOPLES_CHOICE_FAVOURITE,
    RAZZIE_WORST_PICTURE,
    SAG_BEST_ENSEMBLE,
    SPIRIT_BEST_FEATURE,
    SUNDANCE_GRAND_JURY_AWARDS,
    TIFF_PEOPLES_CHOICE,
    TIFF_PEOPLES_CHOICE_AWARDS,
    VENICE_GOLDEN_LION,
    VENICE_GOLDEN_LION_AWARDS,
    fetch_event,
    fetch_event_validation,
    recent_years,
    require_known_event,
    winners_for_categories,
    winners_for_year,
)
from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)
from autoposter.config.schema import CollectionDefinition

_OSCAR_SUMMARY = (
    "The Academy Award for Best %s is one of the Academy Awards presented "
    "annually by the Academy of Motion Picture Arts and Sciences since the "
    "awards debuted in 1929."
)


class Award(NamedTuple):
    """One static winners collection of one ceremony.

    ``poster_stem`` is the file name under this event's ``Default-Images``
    folder, or ``None`` when the repository has no image for it -- in which
    case the collection keeps no poster rather than being pointed at a guessed
    path (``collections/posters.AWARD_SEGMENTS`` maps the folder).

    ``award_filter`` is the ceremony's *award group*, Kometa's outer filter
    (roadmap row 149). One event file can hold several groups -- BAFTA's
    ``ev0000123`` splits film, television and games -- and a collection of the
    film ceremony's best picture wants only the film group's categories, even
    where the television group spells a category the same way. ``None`` is
    every group, which is what a single-medium ceremony means and what both
    shipped ones did before the field existed.

    ``categories`` is ``None`` for every category of whatever the award filter
    kept. Four of the sixteen ceremonies are configured that way upstream --
    Cannes' collection *is* the Palme d'Or group, and nobody enumerates its
    categories; the National Film Registry filters on neither axis because its
    event has one group and one category. Spelled ``None`` rather than ``()``
    because it is a row saying "no filter here", not a row with an empty one;
    the resolver follows Kometa's falsiness rule and reads both the same way.

    A named record rather than the 4-tuple this was, because the fifth element
    would have been a second ``... | None`` next to ``poster_stem``, positional,
    in every one of the rows the next task adds.
    """

    title: str
    categories: tuple[str, ...] | None
    summary: str | None
    poster_stem: str | None
    award_filter: tuple[str, ...] | None = None


@dataclass(frozen=True)
class AwardEvent:
    """One ceremony: everything that differs between them, in one place.

    ``awards`` is ``award key -> Award``, the static winner collections.

    ``library_types`` is which kinds of library this ceremony's collections
    can mean anything for (roadmap row 150). A definition with no
    ``libraries:`` key runs against every library in the pass, so without this
    an Oscars definition reaches Show libraries too -- where every id resolves
    to nothing, which looks exactly like a ceremony that had no winners. It is
    per ceremony rather than per collection because it is the ceremony that
    awards films, or television, or both.

    ``year_pattern`` must not be able to match another ceremony's year titles;
    ``tests/test_collection_awards.py`` holds both directions of that to the
    two shipped patterns.
    """

    key: str
    name: str
    event_id: str
    awards: dict[str, Award]
    year_title: str
    year_summary: str | None
    year_pattern: re.Pattern[str]
    years_builder: str
    # §2.4: the dynamic year collections override collection_order to release;
    # only the static winner collections keep the custom order.
    year_sort: str = field(default="release")
    library_types: tuple[str, ...] = ("Movie",)


# Every ceremony Kometa ships an award default for, and only those. Its
# ``defaults/award/`` holds sixteen ``<ceremony>.yml`` files plus
# ``separator_award.yml`` -- which builds no collection of members at all, only
# the section separator, so it is not an event and has no row here.
#
# Kometa sources, oscars and golden fetched 2026-08-25, the other fourteen
# 2026-08-26, all from ``master``:
#   filters (event id, category_filter, award_filter, allowed_libraries)
#     defaults/award/<file>.yml
#   titles and summaries
#     Kometa-Team/Translations defaults/en.yml, at the ``translation_key`` the
#     defaults file names -- which is why two collection titles here are not
#     the key Kometa's own yml files use (``Spirit Best Feature Winners``, not
#     ``Independent Spirit Best Feature Winners``; ``People's Choice Award
#     Winners``, not ``Peoples Choice Award Winners``). The translation is what
#     Kometa actually names the collection.
#   year title format
#     ``title_format:`` of the ceremony's ``dynamic_collections:`` block
#   poster folders and stems
#     ``image:`` of both blocks -- see ``posters.AWARD_SEGMENTS``, which needs
#     both because they are not the same folder for thirteen of the sixteen.
#   the award key of each row (``params: {event: venice, award: golden}``)
#     the collection's own ``variables: key:``. Opaque on its own, but it is
#     Kometa's name for that collection and the two shipped rows already use
#     it (``best_picture``, ``best_director``).
EVENTS: dict[str, AwardEvent] = {
    "oscars": AwardEvent(
        key="oscars",
        name="Oscars",
        event_id=EVENT_ID,
        awards={
            "best_picture": Award(
                "Oscars Best Picture Winners",
                BEST_PICTURE,
                _OSCAR_SUMMARY % "Picture",
                "best_picture_winner",
            ),
            "best_director": Award(
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
            "best_picture": Award(
                "Golden Globes Best Picture Winners",
                GOLDEN_GLOBES_BEST_PICTURE,
                "Golden Globes Best Picture Winners.",
                "best_picture_winner",
            ),
            "best_director": Award(
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
    "bafta": AwardEvent(
        key="bafta",
        name="BAFTA",
        event_id="ev0000123",
        awards={
            "best": Award(
                "BAFTA Best Films",
                BAFTA_BEST_FILM,
                "British Academy of Film and Television Arts Best Film Winners.",
                "winner",
            ),
        },
        year_title="BAFTA %s",
        year_summary="%s BAFTA Awards.",
        year_pattern=re.compile(r"^BAFTA \d{4}$"),
        years_builder="bafta_award_years",
        # ``allowed_libraries: movie`` on both of Kometa's blocks, and no
        # ``award_filter`` on either. That is worth stating because the event
        # is the multi-medium one -- ``ev0000123`` holds ``bafta film award``,
        # ``bafta tv award``, ``bafta games award`` and three more groups --
        # and it would be reasonable to expect the film collections to be
        # narrowed to the film group. Kometa does not narrow them; the two
        # category names it filters on are film-award names, so reading every
        # group costs nothing, and inventing a filter it does not have would
        # be the invention this table exists to avoid.
        library_types=("Movie",),
    ),
    "berlinale": AwardEvent(
        key="berlinale",
        name="Berlinale",
        event_id="ev0000091",
        awards={
            "golden": Award(
                "Berlinale Golden Bears",
                # No ``category_filter``: the Golden Bear *is* an award group,
                # and Kometa takes every category inside it.
                None,
                "Up to 400 films are shown every year as part of the Berlinale's "
                "(Berlin International Film Festival) public programme, the vast "
                "majority of which are world or European premieres. Films of every "
                "genre, length and format can be submitted for consideration. The "
                "Golden Bear (German Goldener Bär) is the highest prize awarded "
                "for the best film shown during this festival.",
                "winner",
                BERLINALE_GOLDEN_BEAR_AWARDS,
            ),
        },
        year_title="Berlinale %s",
        year_summary="%s Berlinale Award Winners.",
        year_pattern=re.compile(r"^Berlinale \d{4}$"),
        years_builder="berlinale_award_years",
    ),
    "cannes": AwardEvent(
        key="cannes",
        name="Cannes",
        event_id="ev0000147",
        awards={
            "palm": Award(
                "Cannes Golden Palm Winners",
                None,
                "Cannes Golden Palm Winners.",
                "winner",
                CANNES_PALME_DOR_AWARDS,
            ),
        },
        year_title="Cannes %s",
        year_summary="%s Cannes Awards.",
        year_pattern=re.compile(r"^Cannes \d{4}$"),
        years_builder="cannes_award_years",
    ),
    "cesar": AwardEvent(
        key="cesar",
        name="César",
        event_id="ev0000157",
        awards={
            "best": Award(
                "César Best Film Winners",
                CESAR_BEST_FILM,
                "The César Award is the national film award of France, first "
                "given out in 1975. The nominations are selected by the members of "
                "the Académie des Arts et Techniques du Cinéma. The name of "
                "the award comes from the sculptor César Baldaccini. They are "
                "considered to be the French equivalent of the American Academy "
                "Awards.",
                "winner",
                CESAR_AWARDS,
            ),
        },
        year_title="César %s",
        year_summary="%s César Award Winners.",
        year_pattern=re.compile(r"^César \d{4}$"),
        years_builder="cesar_award_years",
    ),
    "choice": AwardEvent(
        key="choice",
        name="Critics Choice",
        event_id="ev0000133",
        awards={
            "best": Award(
                "Critics Choice Best Picture Winners",
                CRITICS_CHOICE_BEST_PICTURE,
                "Critics Choice Best Picture Winners.",
                "winner",
            ),
        },
        year_title="Critics Choice Awards %s",
        year_summary="%s Critics Choice Awards.",
        year_pattern=re.compile(r"^Critics Choice Awards \d{4}$"),
        years_builder="choice_award_years",
        # The ceremony awards both: 40 of the 103 categories ``ev0000133``
        # carries are television ones, and Kometa's year block sets no
        # ``allowed_libraries`` (its static one says ``movie``). This is the
        # one row where the event's types are wider than one of its own
        # collections wants -- "best picture" is a film award, so on a Show
        # library that collection resolves nothing. Narrowed on the catalog
        # side, not here: ``Preset.award_library_types`` in
        # ``collections/catalog.py``'s ``_AWARD_NARROWING`` drops the
        # ``best`` collection to ``("Movie",)`` alone while this event's own
        # ``library_types`` -- what the year collections use -- stays both.
        library_types=("Movie", "Show"),
    ),
    "emmy": AwardEvent(
        key="emmy",
        name="Emmys",
        event_id="ev0000223",
        awards={
            "best": Award(
                "Emmys Best in Category Winners",
                EMMY_BEST_IN_CATEGORY,
                "Emmys Best in Category Winners.",
                "winner",
            ),
        },
        year_title="Emmys %s",
        year_summary="%s Emmy Winners.",
        year_pattern=re.compile(r"^Emmys \d{4}$"),
        years_builder="emmy_award_years",
        # ``allowed_libraries: show``, and the only ceremony here that is
        # television alone. The gate is the point of the row: without it these
        # definitions would also run against Movie libraries, where every id
        # resolves to nothing.
        library_types=("Show",),
    ),
    "nfr": AwardEvent(
        key="nfr",
        name="National Film Registry",
        event_id="ev0000468",
        awards={
            "all_time": Award(
                "National Film Registry All Time",
                # Neither filter: ``ev0000468`` has one award group and one
                # category, so Kometa configures no narrowing at all.
                None,
                "National Film Registry All Time.",
                "all_time",
            ),
        },
        year_title="National Film Registry %s",
        year_summary="%s National Film Registry.",
        year_pattern=re.compile(r"^National Film Registry \d{4}$"),
        years_builder="nfr_award_years",
    ),
    "pca": AwardEvent(
        key="pca",
        name="People's Choice",
        event_id="ev0000530",
        awards={
            "best": Award(
                "People's Choice Award Winners",
                PEOPLES_CHOICE_FAVOURITE,
                "People's Choice Award Winners.",
                "winner",
            ),
        },
        year_title="People's Choice Awards %s",
        year_summary="%s People's Choice Award Winners.",
        year_pattern=re.compile(r"^People's Choice Awards \d{4}$"),
        years_builder="pca_award_years",
        # Kometa sets no ``allowed_libraries`` on either block, and the
        # collection's own category list is why: "favorite movie" and
        # "favorite tv show" are both in it. Unlike ``choice`` this needs no
        # per-collection narrowing later -- the one static collection really
        # does span both media.
        library_types=("Movie", "Show"),
    ),
    "razzie": AwardEvent(
        key="razzie",
        name="Razzies",
        event_id="ev0000558",
        awards={
            "golden": Award(
                "Razzies Golden Raspberry Winners",
                RAZZIE_WORST_PICTURE,
                "The Golden Raspberry Award for Worst Picture is an award given out "
                "at the annual Golden Raspberry Awards to the worst film of the past "
                "year.",
                "winner",
            ),
        },
        year_title="Razzie %s",
        year_summary="%s Razzie Award Winners.",
        year_pattern=re.compile(r"^Razzie \d{4}$"),
        years_builder="razzie_award_years",
    ),
    "sag": AwardEvent(
        key="sag",
        name="Screen Actors Guild",
        event_id="ev0000598",
        awards={
            "best": Award(
                "Screen Actors Guild Award Winners",
                SAG_BEST_ENSEMBLE,
                "Screen Actors Guild Award Winners.",
                "winner",
            ),
        },
        year_title="Screen Actors Guild Awards %s",
        year_summary="%s Screen Actors Guild Award Winners.",
        year_pattern=re.compile(r"^Screen Actors Guild Awards \d{4}$"),
        years_builder="sag_award_years",
        # As ``pca``: no ``allowed_libraries`` upstream, and the collection's
        # own categories mix the theatrical cast award with the comedy- and
        # drama-series ensembles.
        library_types=("Movie", "Show"),
    ),
    "spirit": AwardEvent(
        key="spirit",
        name="Independent Spirit",
        event_id="ev0000349",
        awards={
            "best": Award(
                "Spirit Best Feature Winners",
                SPIRIT_BEST_FEATURE,
                "Spirit Best Feature Winners.",
                "winner",
            ),
        },
        year_title="Independent Spirit Awards %s",
        year_summary="%s Independent Spirit Awards.",
        year_pattern=re.compile(r"^Independent Spirit Awards \d{4}$"),
        years_builder="spirit_award_years",
    ),
    "sundance": AwardEvent(
        key="sundance",
        name="Sundance",
        event_id="ev0000631",
        awards={
            "grand": Award(
                "Sundance Grand Jury Winners",
                None,
                "The Sundance Film Festival is a film festival that takes place "
                "annually in the state of Utah, in the United States. It is the "
                "largest independent cinema festival in the U.S. Held in January, "
                "the festival is the premier showcase for new work from American and "
                "international independent filmmakers. The festival comprises "
                "competitive sections for American and international dramatic and "
                "documentary films, both feature-length films and short films, and a "
                "group of non-competitive showcase sections.",
                "grand_jury_winner",
                SUNDANCE_GRAND_JURY_AWARDS,
            ),
        },
        year_title="Sundance Film Festival %s",
        year_summary="Sundance Film Festival of %s.",
        year_pattern=re.compile(r"^Sundance Film Festival \d{4}$"),
        years_builder="sundance_award_years",
    ),
    "tiff": AwardEvent(
        key="tiff",
        name="Toronto International Film Festival",
        event_id="ev0000659",
        awards={
            "best": Award(
                "Toronto People's Choice Award Winners",
                TIFF_PEOPLES_CHOICE,
                "Toronto International Film Festival People's Choice Award Winners.",
                "winner",
                TIFF_PEOPLES_CHOICE_AWARDS,
            ),
        },
        year_title="Toronto International Film Festival %s",
        year_summary="%s Toronto International Film Festival Award Winners.",
        year_pattern=re.compile(r"^Toronto International Film Festival \d{4}$"),
        years_builder="tiff_award_years",
    ),
    "venice": AwardEvent(
        key="venice",
        name="Venice",
        event_id="ev0000681",
        awards={
            "golden": Award(
                "Venice Golden Lions",
                VENICE_GOLDEN_LION,
                "The Venice Film Festival is the oldest film festival in the world. "
                "Founded 1932, the festival has since taken place every year in "
                "Venice, Italy. It is part of the Venice Biennale, a major biennial "
                "exhibition and festival for contemporary art. The festival's Leone "
                "d'Oro (Golden Lion) prize is awarded to the best film screened at "
                "the festival.",
                "winner",
                VENICE_GOLDEN_LION_AWARDS,
            ),
        },
        year_title="Venice %s",
        year_summary="%s Venice Award Winners.",
        year_pattern=re.compile(r"^Venice \d{4}$"),
        years_builder="venice_award_years",
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
        award = event.awards[params.award]
        # Above the fetch: a refusal that has already spent the request has
        # only told the operator something a wasted round trip taught it.
        require_library_type(
            "the %s %r collection" % (event.name, params.award),
            ctx.library_type,
            event.library_types,
        )
        data = await _event(ctx, event)
        poster_key = _poster(event, award.poster_stem)
        return BuilderResult(
            ids=[
                ("imdb", imdb_id)
                for imdb_id in winners_for_categories(
                    data, award.categories, award.award_filter
                )
            ],
            summary=award.summary,
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
        # The whole family is gated here rather than per unit in ``build``:
        # this is the earliest point that knows the library type, it is above
        # the fetch, and ``build`` is only ever reached through the units this
        # returns (``engine._expand`` calls it for any builder that has it, and
        # ``engine._run_one`` builds only what came back). Refusing here also
        # leaves nothing half-done -- the engine logs the expansion failure and
        # the pass carries on, which is what a definition aimed at the wrong
        # kind of library deserves.
        require_library_type(
            "the %s year collections" % event.name,
            ctx.library_type,
            event.library_types,
        )
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
