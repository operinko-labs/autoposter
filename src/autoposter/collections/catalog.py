"""Every collection this service *can* build, whether or not it builds it.

``sources.default_definitions`` is the inventory of what a deployment builds
today; this module is the inventory of what it could build if someone asked.
The two meet in one place: ``preset_definitions`` below is the third term of
``default_definitions``, and an empty ``collections.presets`` makes it an empty
list -- which is what keeps an untouched config producing byte-for-byte the
collections it produced before this module existed.

**Presets are keys, not definitions.** ``collections.presets: [award_cannes]``
is the whole configuration; the definitions it stands for are expanded here, on
the server, every time they are needed. The alternative -- writing the expanded
definitions into the operator's config -- would have frozen a copy of this
table into every deployment on the day they clicked the checkbox, and every
correction made here afterwards would have had to be migrated into their file.

Three properties this module has to keep, none of them optional:

- **It is pure.** ``config/schema.py``'s ``_titles_must_not_collide`` runs
  ``default_definitions`` -- and therefore this expansion -- for every library
  type on EVERY config write. A fetch here would put a network round trip
  inside config validation, where its failure is a 500 on a settings save. No
  HTTP, no Plex, no dataset reads, no file system. The dynamic half of an award
  preset stays dynamic in the builder, where it already was: the years
  definition this emits is the placeholder, and ``ImdbAwardYearsBuilder.expand``
  turns it into the real year collections at run time, against the dataset.
- **It derives rather than restates.** The award rows below carry a
  description, a provenance string and the exceptions -- and nothing else. The
  collection titles, the award keys, the years builder, the library types and
  the year-title shape all come out of ``imdb_award.EVENTS``, which is the
  registry the builders themselves read. A table that restated any of them
  would be a second copy able to drift from the first, silently, in the
  direction of building a collection under a title nothing else expects.
- **It is opinion-free until asked.** ``sources.py``'s Oscars comment is the
  posture this preserves: presets are opinions, and an opinion that ships
  switched on creates collections in deployments that never asked for them.
  Everything here is off until a key names it.

**The Oscars are deliberately not a preset.** They are ``collections.awards``,
the boolean that shipped, and they stay it -- an ``award_oscars`` *preset*
would be a second way to switch on the same four collections, and switching on
both would put two definitions with identical titles into one library's list.
(That is the one collision ``_titles_must_not_collide`` cannot catch: it
compares operator definitions against the built-ins, not the built-ins against
each other.) They still get a row -- an operator looking for the Oscars in the
Awards tab should find one -- but it carries ``Preset.setting`` instead of
being a member of ``BY_KEY``, so it reads ``collections.awards`` for its
``active`` state and can never be listed in ``presets:``. The charts family
and the Common Sense divider get the same treatment, one each, for the same
reason: each already has its own boolean, and a preset key next to it would be
a second way to flip a switch that already exists.

Key naming: ``<category-ish prefix>_<thing>``, ``award_cannes``. The prefix is
not parsed anywhere -- ``category`` is a field -- it is there so a key reads on
its own in a YAML file that also names charts and people.
"""
from dataclasses import dataclass

from autoposter.collections import packs
from autoposter.collections.builders.credits_family import (
    TITLE_FORMATS as CREDIT_TITLE_FORMATS,
)
from autoposter.collections.builders.imdb_award import EVENTS
from autoposter.collections.builders.imdb_chart import (
    CHARTS_FOR as IMDB_CHARTS_FOR,
    CHART_TITLES as IMDB_CHART_TITLES,
)
from autoposter.collections.builders.tmdb import CHART_TITLES as TMDB_CHART_TITLES
from autoposter.collections.dynamic_titles import render_title
from autoposter.collections.dynamic_types import DYNAMIC_TYPES
from autoposter.collections.facts_family import FACTS_FAMILY_TYPES
from autoposter.config.schema import CollectionDefinition
from autoposter.providers.tmdb_lists import CHART_ENDPOINTS

# The ten categories, in the order the picker shows them: Kometa's own defaults
# taxonomy, which is what an operator arriving from Kometa is looking for. Key
# -> the label the UI puts on the tab. The picker's tab strip is derived from
# this dict (``catalog_listing`` below), so a category added here gains its tab
# with no frontend edit.
#
# ``franchises`` is the tenth and it is OURS, not upstream's: Kometa files
# ``franchise.yml`` under content, but 65 live franchise collections are a
# block of their own rather than four rows inside Content -- C2,
# ``.superpowers/sdd/p-dividers-facts.md``. The Universes and DC packs joined
# it on 2026-09-01 (operator directive): they are franchise blocks by every
# reading an operator does, and keeping them a tab away from the enumerated
# family they overlap was the thing that made the overlap hard to see.
CATEGORIES: dict[str, str] = {
    "awards": "Awards",
    "charts": "Charts",
    "content": "Content",
    "content_ratings": "Content Ratings",
    "franchises": "Franchises",
    "location": "Location",
    "media": "Media",
    "people": "People",
    "production": "Production",
    "time": "Time",
}

# Readiness. READY means the machinery this preset needs is shipped and the key
# can be switched on today; GATED means the table knows what the preset would
# be but the builder work is a roadmap row that has not landed, so the picker
# shows it disabled with the row number and the config REFUSES it -- rather
# than accepting a key that would quietly build nothing.
READY = "ready"
GATED = "gated"

# What goes where the year does in an expanding award definition's title. The
# definition is a placeholder that never becomes a collection (``engine.
# definition_titles`` reads the builder's ``TITLE_PATTERN`` instead of this
# title), but it is what an operator sees in a preview, so it says so.
YEARS_PLACEHOLDER = "(recent ceremonies)"


def award_years_title(event) -> str:
    """The placeholder title of one ceremony's year-collections definition.

    Derived from the ceremony's own ``year_title`` so it cannot name a shape
    the builder does not produce. ``sources.AWARD_YEARS_TITLE`` -- the Oscars'
    shipped placeholder, which predates this module -- is exactly this
    derivation applied to the Oscars, and a test pins the two equal.
    """
    return event.year_title % YEARS_PLACEHOLDER


# What goes where the KEY does in a dynamic pack's shape line, and where the
# library type does. Both are placeholders for the same reason
# ``YEARS_PLACEHOLDER`` is one: a family's real titles are decided by something
# this table cannot see -- the dataset's years, or the library's own values --
# and a shape is the honest thing to show instead of a guess.
DYNAMIC_KEY_PLACEHOLDER = "<%s>"
DYNAMIC_LIBRARY_TYPE = "<Library type>"


def _shape_line(what: str, shape: str) -> str:
    """One line for a family this table can name the SHAPE of and not the
    members of. Shared by the three families that have one -- an award
    ceremony's year collections, a dynamic pack and a facts-enumerated pack --
    because the picker renders one string and there is no reason for it to be
    assembled two ways."""
    return 'one per %s, named "%s"' % (what, shape)


def _family_shape(row, params: dict, placeholder_title: str, values_clause: str) -> str:
    """Shared body of ``dynamic_shape`` and ``facts_family_shape``: everything
    but the type table each reads and the clause naming where its values come
    from -- the one sentence an operator needs to read differently between
    them (see each function's own docstring for why)."""
    noun = row.name.replace("_", " ")
    key_name = DYNAMIC_KEY_PLACEHOLDER % noun
    shape = render_title(
        params.get("title_format") or row.title_format,
        key_name,
        DYNAMIC_LIBRARY_TYPE,
        key=key_name,
        values=(),
        auto_type=row.name,
    )
    line = _shape_line("%s %s" % (noun, values_clause), shape)
    return (
        '%s ("%s" above is the reserved definition title -- the family\'s own '
        "collections are the ones per %s, named this way)" % (line, placeholder_title, noun)
    )


def dynamic_shape(params: dict, placeholder_title: str) -> str:
    """The family-shape line for one dynamic pack.

    Derived, not restated. The format is the pack's own pinned
    ``title_format`` when it has one and the TYPE's default when it does not
    (``dynamic_types.DYNAMIC_TYPES``), and it is rendered by the same
    ``dynamic_titles.render_title`` the builder names collections with -- so
    the sentence in the picker cannot describe a shape the builder will not
    produce. The two unknowns are written as placeholders: the key the library
    will supply, and the library type, which differs between a preset's
    libraries and would be a lie if one of them were picked.

    ``placeholder_title`` is the OTHER thing ``titles()`` lists next to this
    shape -- the reserved definition title the engine falls through to when a
    smart builder declares none (``builders/dynamic.py``'s "No ``titles()``,
    deliberately" note), under which no collection is ever created. C3 licenses
    ``titles()`` reporting that title only on the condition that the shape line
    explains it, so the explanation is written into the line itself rather than
    left for the picker to invent.
    """
    row = DYNAMIC_TYPES[params["type"]]
    return _family_shape(row, params, placeholder_title, "the library holds")


def facts_family_shape(params: dict, placeholder_title: str) -> str:
    """The family-shape line for one facts-enumerated pack.

    ``dynamic_shape``'s twin, and deliberately its near-copy rather than a
    generalisation of it: the two read different type tables
    (``FACTS_FAMILY_TYPES`` against ``DYNAMIC_TYPES``) and say a different
    thing about where the values come from, which is the one sentence an
    operator needs to read differently. Everything else is the same contract --
    derived rather than restated, rendered through the same
    ``dynamic_titles.render_title`` the family names collections with, with the
    key and the library type as placeholders because neither is knowable here.

    The clause that differs: a dynamic family's values are the LIBRARY's, and a
    facts family's are the ones this service's own facts pipeline has visited
    so far. A picker line that said "the library holds" of this family would be
    a small lie on every freshly-deployed deployment.
    """
    row = FACTS_FAMILY_TYPES[params["type"]]
    return _family_shape(row, params, placeholder_title, "this service has gathered facts for")


@dataclass(frozen=True)
class _CreditKindRow:
    """What ``_family_shape`` reads, for the one family whose types are not a
    table of rows: a credit kind is a bare string and its title format lives in
    ``builders/credits_family.TITLE_FORMATS``. Four lines here rather than a
    fifth signature for the shared helper."""

    name: str
    title_format: str


def credits_family_shape(params: dict, placeholder_title: str) -> str:
    """The family-shape line for one counted-credits pack.

    ``dynamic_shape``'s third twin, and the same contract: derived rather than
    restated, rendered through the ``render_title`` the family names
    collections with, with the key and the library type as placeholders.

    The clause that differs is the honest one. A dynamic family's values are
    the LIBRARY's and a facts family's are the ones this service's facts
    pipeline has visited; this family's are the people its CREDITS CACHE has
    counted -- a floor rather than a census, because Plex caps its credit list
    at 200 roles per item. "the library's most-credited" would be a claim
    neither this table nor the builder can make.
    """
    kind = params["type"]
    row = _CreditKindRow(name=kind, title_format=CREDIT_TITLE_FORMATS[kind])
    return _family_shape(
        row, params, placeholder_title, "the credits cache has counted"
    )


def collection_title(template: str, library_type: str) -> str:
    """One collection's title for one library type.

    Kometa's packs title a collection either after the thing itself
    (``Arrowverse``) or after the thing AND the library kind
    (``<<key_name>> <<library_typeU>>s`` -> ``Netflix Movies``,
    ``Netflix Shows``). Both shapes live in the same column here, and a ``%s``
    in the template is the second one -- the same ``%``-templating
    ``AwardEvent.year_title`` already uses for a ceremony's year titles, rather
    than a second convention next to it.

    The membership under those two titles is genuinely different (a Movie
    library resolves the movie half of the query, a Show library the show
    half), so they are two collections and not one title used twice.
    """
    return template % library_type if "%s" in template else template


@dataclass(frozen=True)
class PresetCollection:
    """One collection a list-family preset builds.

    The award families derive everything from ``imdb_award.EVENTS`` and need no
    rows like this. The eight other categories have no such registry to derive
    from -- Kometa's packs are YAML tables of ids, provider keys and filter
    values -- so those tables are transcribed here, one row per collection, and
    the transcription is the deliverable the way ``filters.FILTER_ATTRIBUTES``
    and ``tmdb_discover.DISCOVER_PARAMS`` are.

    ``title`` is the template ``collection_title`` reads. ``params`` and
    ``filters`` are pairs rather than dicts so a row stays hashable like the
    rest of a frozen dataclass; a filter value that is a tuple becomes the list
    ``collections.filters`` parses as an any-of.

    One exception, measured rather than assumed: a dynamic pack's ``addons``,
    ``key_name_override`` and ``title_override`` params are MAPPINGS, and
    pydantic will not build a ``dict[str, list[str]]`` from a tuple of pairs
    (``Input should be a valid dictionary``). Those values are dicts, so a row
    carrying one is not hashable -- nothing hashes these rows today, and the
    alternative (a pairs-to-dict conversion keyed on param names) would put one
    builder's vocabulary inside a generic row.

    ``library_types`` narrows a SINGLE collection below its preset's own -- the
    streaming pack is offered for both kinds of library and three of its
    services carry only shows (Kometa's ``allowed_libraries``), exactly the
    relationship ``Preset.award_library_types`` has to a ceremony. ``None``
    means "the preset's own", which is the common case.
    """

    title: str
    builder: str
    params: tuple[tuple[str, object], ...] = ()
    filters: tuple[tuple[str, object], ...] = ()
    library_types: tuple[str, ...] | None = None

    def definition(self, library_type: str) -> CollectionDefinition:
        """This row as a definition. Constructing it is validating it: see
        ``CollectionDefinition``'s builder, params and filters validators."""
        return CollectionDefinition(
            title=collection_title(self.title, library_type),
            builder=self.builder,
            params=dict(self.params),
            # None rather than {} for a row with no filters: an empty mapping
            # is a block an operator wrote and left empty, and the parser
            # refuses that (``CollectionDefinition.filters``).
            filters={
                key: list(value) if isinstance(value, tuple) else value
                for key, value in self.filters
            } or None,
        )


@dataclass(frozen=True)
class Preset:
    """One catalog entry: a key an operator can switch on, and what it builds.

    ``library_types`` is which kinds of library the preset means anything for,
    and it is the preset's own -- for an award preset it is the ceremony's
    (``AwardEvent.library_types``), because that is the fact the builders
    already hold. ``award_library_types`` narrows a *single* collection of a
    ceremony below that: the Critics Choice ceremony awards film and
    television, so its year collections belong on both, while its one static
    collection is a best-PICTURE collection and resolves nothing on a Show
    library. That is the only row that needs it (``imdb_award.py``'s
    ``choice`` comment records why, and why ``pca`` and ``sag`` do not).

    ``gated_row`` is the roadmap row a GATED preset is waiting on, and it is
    the whole value of a gated row: "not yet" without a number is a shrug.

    The definition-producing fields are deliberately per-family rather than a
    generic ``builder``/``params`` pair. One award preset produces several
    definitions of two different builders, and the count depends on the
    library type -- a single params dict could not have said that. There are
    two families: ``award_event``, which derives everything from
    ``imdb_award.EVENTS``, and ``collections``, which is a transcribed table
    for the packs that have no such registry behind them. A row carries one or
    neither -- neither is what a GATED row is, and a row that carried both
    would be two presets wearing one key.

    ``setting`` marks a different kind of row: not a preset an operator
    switches on by listing its key in ``presets:``, but a *display* of a
    setting that already exists and already has its own wiring -- a dotted
    config path (``"collections.awards"``) the listing reads to answer
    ``active``. Rows like this are never in ``BY_KEY`` and their
    ``definitions()`` always returns ``[]``: the picker shows them so the
    Oscars, the charts and the Common Sense divider have a row next to the
    presets that switch on their neighbours, but flipping them happens
    through the boolean they name, not through this table.
    """

    key: str
    category: str
    # The label the picker puts on the row. Derived for award presets, so it
    # is the ceremony's name as the builders spell it.
    name: str
    description: str
    # Where this preset comes from upstream, verbatim: the path of the Kometa
    # defaults file it reproduces, or an honest statement that it has none.
    kometa_source: str
    library_types: tuple[str, ...]
    readiness: str = READY
    gated_row: int | None = None
    # A dotted config path (``"collections.awards"``) for a row that is a
    # rendered switch rather than a preset -- see the class docstring. None
    # for every ordinary preset.
    setting: str | None = None
    # --- the definition-producing fields -----------------------------------
    # An award preset is one ceremony of ``imdb_award.EVENTS``: every static
    # winners collection it has, plus its year-collections placeholder.
    award_event: str | None = None
    # ``award key -> the library types that one collection is for``, as pairs
    # rather than a dict so the row stays hashable like the rest of a frozen
    # dataclass. Empty for every ceremony but one.
    award_library_types: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # The other families' producer: the collections this preset builds, one row
    # each. Empty for an award preset (which derives its own from EVENTS) and
    # for every GATED row -- a gated row knows what it WOULD build and has no
    # way to build it, which is the whole point of the readiness column.
    #
    # A setting-backed row (``setting`` set) is the one place this field means
    # something else: its table is a DISPLAY list for the picker, not a
    # producer -- ``definitions()``'s ``setting`` branch below returns [] for
    # every such row before this field is ever read.
    collections: tuple[PresetCollection, ...] = ()

    def definitions(self, library_type: str) -> list[CollectionDefinition]:
        """This preset's definitions for one library type. Pure; no I/O.

        Empty is a real answer: a Movie-only ceremony asked for its Show
        definitions has none, and the caller appends nothing.
        """
        if self.setting is not None:
            # A rendered switch, not a preset -- its family already builds
            # through the boolean it names (``sources.chart_and_award_
            # definitions``, ``cs_bucket``). Explicit rather than left to
            # ``award_event`` being unset, so a row that someday carries both
            # a ``setting`` and a producer still expands to nothing.
            return []
        if self.award_event is not None:
            return self._award_definitions(library_type)
        return [
            collection.definition(library_type)
            for collection in self.collections
            if library_type in (collection.library_types or self.library_types)
        ]

    def _award_definitions(self, library_type: str) -> list[CollectionDefinition]:
        event = EVENTS[self.award_event]
        narrowed = dict(self.award_library_types)
        definitions = [
            CollectionDefinition(
                title=award.title,
                builder="imdb_award",
                # The event explicitly, though ``ImdbAwardParams.event``
                # defaults to the Oscars: a preset's params should read as the
                # whole answer to "which collection is this".
                params={"event": event.key, "award": award_key},
            )
            for award_key, award in event.awards.items()
            if library_type in narrowed.get(award_key, event.library_types)
        ]
        if library_type in event.library_types:
            definitions.append(
                CollectionDefinition(
                    title=award_years_title(event), builder=event.years_builder
                )
            )
        return definitions

    def titles(self) -> list[str]:
        """Every collection title this preset builds, for the picker.

        Static titles only. The year collections are named by the ceremony and
        the year, and neither this module nor the picker can know which years
        the dataset currently carries -- so ``years_title`` below reports the
        shape instead, and no title is claimed that might not appear.

        A GATED row claims nothing at all: its collections are named by a
        builder that has not been written, and a list of titles it cannot
        produce would read as a promise.
        """
        if self.award_event is not None:
            return [award.title for award in EVENTS[self.award_event].awards.values()]
        titles: list[str] = []
        for library_type in self.library_types:
            for collection in self.collections:
                if library_type not in (collection.library_types or self.library_types):
                    continue
                title = collection_title(collection.title, library_type)
                if title not in titles:
                    titles.append(title)
        return titles

    def years_title(self) -> str | None:
        """The shape of this preset's dynamic titles, or None if it has none.

        FOUR families build collections this table cannot name. An award
        ceremony's year collections are named by the years the dataset carries;
        a dynamic pack's family is named by the values the library holds; a
        facts pack's is named by the values this service has gathered facts
        for; a credits pack's is named by the people its credits cache has
        counted. None is knowable here, and all four answer with the same
        one-line SHAPE through the same payload key the picker already renders
        -- one field, one UI branch, no second shape for a second family to
        drift from (10b decision C3).
        """
        if self.award_event is not None:
            return _shape_line(
                "ceremony", EVENTS[self.award_event].year_title % "<year>"
            )
        for collection in self.collections:
            if collection.builder == "dynamic":
                return dynamic_shape(dict(collection.params), collection.title)
            if collection.builder == "facts_family":
                return facts_family_shape(dict(collection.params), collection.title)
            if collection.builder == "credits_family":
                return credits_family_shape(
                    dict(collection.params), collection.title
                )
        return None


# --- the AWARDS category ----------------------------------------------------
#
# One preset per ceremony ``imdb_award.EVENTS`` knows, except the Oscars (the
# module docstring says why). Fifteen rows, and they are BUILT from EVENTS
# rather than written out: a seventeenth ceremony added to that registry
# arrives here on its own, and the note table below is what makes it arrive
# loudly -- a new event with no note raises at import rather than shipping a
# catalog row with an empty description.

# The Kometa defaults file each ceremony reproduces. The file name is the event
# key for fourteen of the fifteen; the Golden Globes' file is ``golden.yml``,
# transcribed in ``collections/awards.py`` when that ceremony shipped. Every
# key is spelled out, none derived from ``event_key`` by falling back to it --
# a ceremony added to ``EVENTS`` without an entry here raises ``KeyError`` at
# import, the same loud-failure shape ``_AWARD_NOTES`` already had, rather than
# inventing a file name nobody checked.
_KOMETA_FILES: dict[str, str] = {
    "golden_globes": "golden",
    "bafta": "bafta",
    "berlinale": "berlinale",
    "cannes": "cannes",
    "cesar": "cesar",
    "choice": "choice",
    "emmy": "emmy",
    "nfr": "nfr",
    "pca": "pca",
    "razzie": "razzie",
    "sag": "sag",
    "spirit": "spirit",
    "sundance": "sundance",
    "tiff": "tiff",
    "venice": "venice",
}

# The sentence a row needs beyond its derived description, for the rows where
# something is not obvious from the title. Empty string = nothing to add, and
# the row still has to be here: that is what makes a new ceremony fail loudly.
_AWARD_NOTES: dict[str, str] = {
    "golden_globes": "",
    "bafta": "",
    "berlinale": "",
    "cannes": "",
    "cesar": "",
    "choice": (
        "The ceremony awards film and television, so its year collections "
        "belong in both kinds of library; the best-picture collection is a "
        "film collection and is offered for Movie libraries only."
    ),
    "emmy": "Television only -- the one ceremony here that is.",
    "nfr": (
        "Not a yearly prize: the static collection is the whole registry, and "
        "the year collections are the films inducted that year."
    ),
    "pca": (
        "Spans both kinds of library: the one static collection's categories "
        "name a favourite movie and a favourite TV show alike."
    ),
    "razzie": "",
    "sag": (
        "Spans both kinds of library: the categories mix the theatrical cast "
        "award with the comedy- and drama-series ensembles."
    ),
    "spirit": "",
    "sundance": "",
    "tiff": "",
    "venice": "",
}

# The one ceremony whose event-level library types are wider than one of its
# own collections wants. See ``imdb_award.py``'s ``choice`` row.
_AWARD_NARROWING: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "choice": (("best", ("Movie",)),),
}


def _check_award_narrowing() -> None:
    """Every ``_AWARD_NARROWING`` key names an award its own ceremony has.

    ``_award_definitions``'s ``narrowed.get(award_key, event.library_types)``
    looks a mistyped key up the same way it looks up a real one -- silently --
    so a typo here would not narrow the wrong award, it would narrow nothing
    and never say why. Called at import, not left to a test to catch.
    """
    for event_key, narrowed in _AWARD_NARROWING.items():
        for award_key, _library_types in narrowed:
            if award_key not in EVENTS[event_key].awards:
                raise AssertionError(
                    "_AWARD_NARROWING[%r] names %r, which is not one of "
                    "%r's awards (%s)" % (
                        event_key, award_key, event_key,
                        ", ".join(sorted(EVENTS[event_key].awards)),
                    )
                )


_check_award_narrowing()


def _award_description(event, note: str) -> str:
    """One ceremony's description, derived from the registry row.

    Counted rather than listed, because the titles are their own field: this
    sentence answers "how much is this", the title list answers "what".
    """
    statics = len(event.awards)
    sentence = (
        "%s: %d winners collection%s, plus one collection per recent ceremony."
        % (event.name, statics, "" if statics == 1 else "s")
    )
    return sentence if not note else "%s %s" % (sentence, note)


def _award_preset(event_key: str) -> Preset:
    event = EVENTS[event_key]
    # KeyError on a ceremony nobody wrote a note for. Deliberate: an EVENTS row
    # added without a catalog note would otherwise ship a preset described by
    # nothing at all.
    note = _AWARD_NOTES[event_key]
    return Preset(
        key="award_%s" % event_key,
        category="awards",
        name=event.name,
        description=_award_description(event, note),
        kometa_source="defaults/award/%s.yml" % _KOMETA_FILES[event_key],
        library_types=event.library_types,
        award_event=event_key,
        award_library_types=_AWARD_NARROWING.get(event_key, ()),
    )


AWARD_PRESETS: tuple[Preset, ...] = tuple(
    _award_preset(key) for key in EVENTS if key != "oscars"
)

# --- the setting-backed rows -------------------------------------------------
#
# Three families that already shipped, each behind its own boolean, before
# this catalog existed: the Oscars (``collections.awards``), the IMDb charts
# (``collections.charts``) and the Common Sense age-rating family's blank
# divider (``collections.separators``). The picker needs a row for each so an
# operator sees them next to the presets that switch on their neighbours --
# but flipping one is not a ``presets:`` key, it is the boolean the row names,
# so these carry ``setting`` and nothing about them is a preset: ``BY_KEY``
# below excludes them (a mistyped ``presets: [oscars]`` is refused as unknown,
# not accepted as a no-op) and ``Preset.definitions`` refuses to expand them.
#
# The Common Sense age-rating collections themselves have **no boolean of
# their own** -- ``sources.default_definitions`` builds the ``cs_bucket``
# family unconditionally (roadmap: none filed). Only the divider inside that
# family is a real, named setting, so that is what this row's ``setting``
# names; it does not claim to switch the age buckets off, because nothing
# does.
_oscars = EVENTS["oscars"]
CONTENT_RATINGS_DIVIDER_TITLE = "Ratings Collections"

# The three IMDb chart collections as a DISPLAY table for the row below.
#
# Roadmap row 163. `collections.charts` is one boolean for three collections
# and the row that shows it named none of them, so the picker printed no
# "Builds:" line where the all-or-nothing shape is easiest to see. Derived
# from the builder's own tables rather than retyped, because
# `sources.chart_and_award_definitions` builds from exactly these -- a chart
# renamed or dropped there moves this row with it.
#
# Not a producer: `Preset.definitions` returns [] for any row carrying a
# `setting`, so these rows only ever reach `titles()` and the picker's
# payload. The charts tab's checksum does not move.
_IMDB_CHART_LIBRARY_TYPES: dict[str, list[str]] = {}
for _library_type, _charts in IMDB_CHARTS_FOR.items():
    for _chart in _charts:
        _IMDB_CHART_LIBRARY_TYPES.setdefault(IMDB_CHART_TITLES[_chart][0], []).append(_library_type)

_IMDB_CHART_COLLECTIONS: tuple[PresetCollection, ...] = tuple(
    PresetCollection(title=title, builder="imdb_chart", library_types=tuple(types))
    for title, types in _IMDB_CHART_LIBRARY_TYPES.items()
)

SETTING_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="oscars",
        category="awards",
        name=_oscars.name,
        description=_award_description(
            _oscars,
            "Switched on by the collections.awards setting, not by a preset "
            "key -- it is these collections' original, shipped toggle.",
        ),
        kometa_source="defaults/award/oscars.yml",
        library_types=_oscars.library_types,
        award_event="oscars",
        setting="collections.awards",
    ),
    Preset(
        key="imdb_charts",
        category="charts",
        name="IMDb Charts",
        description=(
            "IMDb Popular, IMDb Top 250, and IMDb Lowest Rated (Movie "
            "libraries only) -- the chart family collections.charts already "
            "builds, refreshed from IMDb on every pass. The switch is the "
            "FAMILY's: collections.charts builds all three or none, and it "
            "must be false before you write any of them yourself, or the "
            "title collides with the built-in one. To build a subset, write "
            "definitions with builder: imdb_chart and params: {chart: <key>} "
            "-- popular_movies, top_movies, or lowest_rated, each narrowed "
            "with libraries: to a Movie library; popular_shows or "
            "top_shows, each narrowed with libraries: to a Show library -- "
            "one definition per title and library type."
        ),
        kometa_source="defaults/chart/imdb.yml",
        library_types=("Movie", "Show"),
        setting="collections.charts",
        collections=_IMDB_CHART_COLLECTIONS,
    ),
    Preset(
        key="content_ratings_divider",
        category="content_ratings",
        name="%s divider" % CONTENT_RATINGS_DIVIDER_TITLE,
        description=(
            "The blank %r section divider that belongs to the Common Sense "
            "age-rating family. The age-rating collections themselves are "
            "always built and have no switch of their own; this divider is "
            "the one part of the family collections.separators turns off."
            % CONTENT_RATINGS_DIVIDER_TITLE
        ),
        kometa_source="defaults/both/content_rating_cs.yml",
        library_types=("Movie", "Show"),
        setting="collections.separators",
    ),
)

# --- provenance, for every row that is not an award ---------------------------
#
# Every Kometa defaults file this catalog reproduces, pinned. A row's
# ``kometa_source`` is checked against this set at import
# (``_check_kometa_sources`` below), so a mistyped path fails at startup rather
# than shipping a provenance line that points at a file nobody can open -- the
# strictness ``_KOMETA_FILES`` already gives the fifteen ceremonies, widened to
# the whole table. The fifteen award paths are DERIVED from ``_KOMETA_FILES``
# rather than written out again: a second copy of the same file names is
# exactly the drift this module refuses everywhere else.
#
# Paths are relative to the Kometa repository root
# (github.com/Kometa-Team/Kometa, ``defaults/``). Every file listed here was
# fetched and read while these rows were written; the ids, provider keys, list
# ids and filter values below are transcriptions of those files, not
# recollections of them.
#
# ``NOT_KOMETA`` is the other half of the same discipline and the reason this
# check can be strict at all: a row with no upstream file says so in its own
# words instead of borrowing a neighbour's citation. An invented
# ``defaults/both/something.yml`` is what the check refuses, and a
# "reproduces Kometa's X" on a row that reproduces nothing is what the prefix
# makes impossible to write by accident.
NOT_KOMETA = "no Kometa defaults file -- "

_KOMETA_DEFAULTS: frozenset[str] = frozenset(
    ["defaults/award/%s.yml" % stem for stem in _KOMETA_FILES.values()]
    + [
        "defaults/award/oscars.yml",
        "defaults/both/actor.yml",
        "defaults/both/aspect.yml",
        "defaults/both/audio_language.yml",
        "defaults/both/based.yml",
        "defaults/both/content_rating_au.yml",
        "defaults/both/content_rating_cs.yml",
        "defaults/both/content_rating_de.yml",
        "defaults/both/content_rating_mal.yml",
        "defaults/both/content_rating_nz.yml",
        "defaults/both/content_rating_uk.yml",
        "defaults/both/genre.yml",
        "defaults/both/resolution.yml",
        "defaults/both/streaming.yml",
        "defaults/both/studio.yml",
        "defaults/both/subtitle_language.yml",
        "defaults/both/year.yml",
        "defaults/chart/imdb.yml",
        "defaults/chart/tmdb.yml",
        "defaults/movie/content_rating_us.yml",
        "defaults/movie/continent.yml",
        "defaults/movie/country.yml",
        "defaults/movie/decade.yml",
        "defaults/movie/director.yml",
        "defaults/movie/franchise.yml",
        "defaults/movie/producer.yml",
        "defaults/movie/region.yml",
        "defaults/movie/seasonal.yml",
        "defaults/movie/writer.yml",
        "defaults/show/content_rating_us.yml",
        "defaults/show/network.yml",
    ]
)

# The roadmap rows a GATED row waits on, named. Bare integers on eighteen rows
# would hide the fact that most of them are waiting on ONE thing, and which
# one; a reader of the table should be able to see the blockers group.
#
# ``docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`` is the document,
# and a test reads the row numbers out of it rather than trusting these -- a
# citation nobody can look up is worse than no citation at all.
DYNAMIC_ENGINE_ROW = 102   # phase 10a: one collection per distinct value
PERSON_SCAN_ROW = 194      # the library-wide credit scan the four Top-* people
                           # packs needed to NAME anyone, plus row 169's people
                           # search attributes, which the per-person query
                           # needed to exist at all. BOTH SHIPPED IN PHASE B --
                           # the scan and its `item_credits` cache
                           # (`collections/credits.py`, with a weekly job), the
                           # counted enumeration behind `builder:
                           # credits_family`, and the four search rows in
                           # `filters.FILTER_ATTRIBUTES` -- so the four packs
                           # are READY and nothing waits on this row any more.
                           # A CITED row now, not a blocker, the shape
                           # `TMDB_ORIGIN_COUNTRY_ROW` already has: the
                           # Director starter set names it as the scan its
                           # enumerated sibling is built on. Row 83 (the person
                           # BUILDERS) closed in 10c-lite: the filmographies,
                           # their TMDb biographies and their profile photos
                           # all ship.
                           #
                           # What the packs may NOT claim, and the row says so:
                           # the counts are a FLOOR. Plex caps `<Role>` at 200
                           # children per item server-side (phase-B probe b --
                           # 54 of 200 sampled shows land on exactly 200, and a
                           # single-key fetch does not escape it), so a person
                           # whose every appearance is in a >200-role cast can
                           # be missing from the ranking entirely.
STRANDED_FILTER_ROW = 155  # the six tier-1 filter attributes the listing strands
FILTER_TIER_TWO_ROW = 96   # the filters subsystem; tier 1 shipped, tier 2 did not
DATE_WINDOW_ROW = 70       # per-collection cadence and date windows: DELIVERED
SEASONAL_WINDOW_ROW = 160  # day-level windows, and a collection fed by several
                           # builders -- filed out of 70, which delivered a
                           # whole-month gate and one builder per definition
KEYWORD_RESOLUTION_ROW = 161  # TMDb keyword name -> id, which no earlier row owns
RELATIVE_YEAR_ROW = 171    # the `current_year`/`current_year-N` value grammar,
                           # the half of that row phase 10a did NOT ship
TMDB_COUNTRY_NAME_ROW = 196  # the region/continent packs group country display
                             # NAMES and `origin_country` carries ISO codes --
                             # filed by the prefetch phase, CLOSED by the
                             # location-names phase: the code->name table is
                             # vendored (`collections/iso_names.py`, fetched
                             # from TMDb's /configuration/countries) and the
                             # family maps codes UP into upstream's names. A
                             # CITED row now, not a blocker: both packs ship
                             # on it and name it as what closed
TMDB_ORIGIN_COUNTRY_ROW = 189  # the two dynamic types that were a TMDb walk and
                               # not an enumeration -- filed by phase 10a-1's
                               # wrap and CLOSED by the prefetch phase, which
                               # made the walk this service's own facts pipeline.
                               # A CITED row now, not a blocker: the two location
                               # packs build on the values it delivered, and
                               # name it beside row 196, the join that let them
                               # ship
TMDB_COLLECTION_TYPE_ROW = 192  # the franchise pack's enumeration: a TMDb walk
                                # over every item's belongs_to_collection, not a
                                # listFilterChoices enumeration -- filed by phase
                                # 10b when row 102 closed without being able to
                                # help it, and CLOSED by the prefetch phase.
                                # A CITED row now, not a blocker:
                                # `content_franchises` ships on it
TMDB_LANGUAGE_NAME_ROW = 190  # upstream names a language bucket from TMDb's
                              # ISO-639-1 table; the two Plex-enumerated
                              # families keep Plex's own titles by decision
                              # (Plex titles locale variants better -- es-419
                              # is "Spanish (Latin America)"), and the
                              # `original_language` facts family titles
                              # through the vendored table
                              # (`collections/iso_names.py`). Names only,
                              # never membership. CLOSED by the location-names
                              # phase; CITED, never a blocker.

_BOTH = ("Movie", "Show")
_MOVIE = ("Movie",)
_SHOW = ("Show",)


# --- the CHARTS category ------------------------------------------------------
#
# One preset per chart ``tmdb_chart`` knows. The library types are the endpoint
# table's (``providers/tmdb_lists.CHART_ENDPOINTS``) rather than a
# transcription of Kometa's ``allowed_libraries``: a chart with no form for a
# library type has no endpoint entry for it either, so the two say the same
# thing and only one of them can go stale.
#
# Five of the eight are ``defaults/chart/tmdb.yml``, and their titles are not
# repeated here: the column reads them from ``builders/tmdb.CHART_TITLES``,
# which is where the same file's summaries are transcribed, so a title and the
# summary that has to agree with it cannot drift apart. The import direction is
# the one that already exists -- ``catalog`` imports builders, never the
# reverse. The other three are charts this service's builder has and that file
# does not (``/movie/now_playing``, ``/movie/upcoming``, ``/trending/*/day``);
# their titles are ours and they say so, because inventing a Kometa attribution
# is worse than admitting there is none.
#
# The IMDb charts are deliberately NOT presets here. They ship behind
# ``collections.charts`` and have a setting-backed row above; a preset key next
# to that boolean would be a second way to build "IMDb Popular", and two
# built-in definitions sharing one title is precisely the collision
# ``_titles_must_not_collide`` cannot catch (module docstring).

# chart key -> (title, kometa_source, what the chart is)
_TMDB_CHARTS: tuple[tuple[str, str, str, str], ...] = (
    ("popular", TMDB_CHART_TITLES["popular"][0], "defaults/chart/tmdb.yml",
     "what TMDb's audience is looking at right now"),
    ("top_rated", TMDB_CHART_TITLES["top_rated"][0], "defaults/chart/tmdb.yml",
     "TMDb's highest-scored titles, by its own weighted rating"),
    ("trending_week", TMDB_CHART_TITLES["trending_week"][0], "defaults/chart/tmdb.yml",
     "TMDb's trending list over the past week"),
    ("airing_today", TMDB_CHART_TITLES["airing_today"][0], "defaults/chart/tmdb.yml",
     "series with an episode airing today"),
    ("on_the_air", TMDB_CHART_TITLES["on_the_air"][0], "defaults/chart/tmdb.yml",
     "series airing an episode in the next week"),
    ("now_playing", "TMDb Now Playing", NOT_KOMETA + "the title is ours",
     "films in cinemas now. TMDb publishes this chart and this service's "
     "builder reads it; Kometa's chart defaults do not include it, so the "
     "title above was written here rather than transcribed"),
    ("upcoming", "TMDb Upcoming", NOT_KOMETA + "the title is ours",
     "films with a release date still ahead. Not in Kometa's chart defaults "
     "either, so the title is ours, as with TMDb Now Playing"),
    ("trending_day", "TMDb Trending Daily", NOT_KOMETA + "the title is ours",
     "TMDb's trending list over the past day. Kometa's TMDb Trending is the "
     "WEEKLY list, which has its own row above; this is the daily one, which "
     "its chart defaults do not include"),
)


def _tmdb_chart_preset(chart: str, title: str, source: str, note: str) -> Preset:
    # KeyError on a chart the endpoint table does not have, deliberately: a row
    # here naming one would otherwise ship a preset whose definition fails its
    # own params model at the first config load.
    library_types = tuple(CHART_ENDPOINTS[chart])
    return Preset(
        key="chart_tmdb_%s" % chart,
        category="charts",
        name=title,
        description="%s: %s, re-read from TMDb on every pass." % (title, note),
        kometa_source=source,
        library_types=library_types,
        collections=(
            PresetCollection(
                title=title, builder="tmdb_chart", params=(("chart", chart),)
            ),
        ),
    )


CHART_PRESETS: tuple[Preset, ...] = tuple(
    _tmdb_chart_preset(*row) for row in _TMDB_CHARTS
)


# The two Tracearr rows, in the same category and for the same reason the TMDb
# charts are here: they answer "what should I watch, going by what is being
# watched". What makes them different is where the ranking comes from -- TMDb
# publishes its charts and Tracearr publishes no ranking at all, in either API
# version, so these two are computed from the deployment's OWN watch history
# (``collections/activity.py``). That is also why they carry no Kometa
# attribution: Kometa's equivalent is its Tautulli chart family, which reads a
# different service through a different API, and claiming its defaults file
# here would be a citation that does not describe what this builds.
#
# Two rows rather than a tenth category. Two collections do not make a
# taxonomy, and an operator looking for "most watched" looks under Charts.
_TRACEARR_SOURCE = NOT_KOMETA + (
    "the role Kometa's Tautulli chart defaults play, computed from this "
    "deployment's own Tracearr watch history; the title is ours"
)

_TRACEARR_DESCRIPTION = (
    "The %s played most often on this server over the past 30 days, ranked "
    "from Tracearr's own watch history and recomputed on every pass. Needs "
    "tracearr.enabled, tracearr.base_url and AUTOPOSTER_TRACEARR_APIKEY; "
    "without them the collection reports itself failed rather than emptying."
)

TRACEARR_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="chart_tracearr_movies",
        category="charts",
        name="Most Watched Movies",
        description=_TRACEARR_DESCRIPTION % "films",
        kometa_source=_TRACEARR_SOURCE,
        library_types=_MOVIE,
        collections=(
            PresetCollection(
                title="Most Watched Movies",
                builder="tracearr_most_watched",
                # Spelled out rather than left to the params model's defaults:
                # a preset's params should read as the whole answer to "which
                # collection is this", the way the award rows name their event.
                params=(("days", 30), ("limit", 20), ("metric", "plays")),
            ),
        ),
    ),
    Preset(
        key="chart_tracearr_shows",
        category="charts",
        name="Most Watched Shows",
        description=_TRACEARR_DESCRIPTION % "series",
        kometa_source=_TRACEARR_SOURCE,
        library_types=_SHOW,
        collections=(
            PresetCollection(
                title="Most Watched Shows",
                builder="tracearr_most_watched",
                params=(("days", 30), ("limit", 20), ("metric", "plays")),
            ),
        ),
    ),
)


# --- the CONTENT category -----------------------------------------------------
#
# ``defaults/both/universe.yml`` is the Content pack this catalog transcribes
# most closely: Kometa builds each universe from a hand-maintained list, and
# nine of its sixteen are public IMDb lists whose ids are written out in the
# file (``imdb_url``). Eight of those nine are transcribed here as
# ``imdb_list`` definitions with the file's own ``data`` names, verbatim. The
# ninth, DC, is deliberately NOT reproduced: it split into the
# three-collection ``content_dc`` preset below (operator directive,
# 2026-08-30 -- the DCU, the DCEU and the rest of DC are not one universe),
# and the one-line history above the Fast & Furious row is what remains of it
# in this table.
#
# The other seven universes are MDBList-hosted, and six of those seven URLs are
# of the ``mdblist.com/lists/k0meta/external/<id>`` shape (the seventh is
# ``johnfawkes/dca``) -- a reference ``MdblistListParams`` cannot take, and
# guessing that the trailing number is the numeric list id is the kind of guess
# this table exists not to make. They are left out rather than approximated,
# and the description says so.
_UNIVERSE_LISTS: tuple[tuple[str, str, tuple[str, ...] | None], ...] = (
    ("Alien / Predator", "ls543971628", _MOVIE),
    ("Arrowverse", "ls566667558", None),
    ("Conjuring Universe", "ls068768438", _MOVIE),
    # No DC row any more. Its history, in one line: Kometa's ls524274984 went
    # private (verified 2026-08-29), was re-pointed at ls046609392, and on
    # 2026-08-30 the whole row split into the three-list ``content_dc``
    # preset below.
    ("Fast & Furious", "ls4102351575", _MOVIE),
    ("Marvel Cinematic Universe", "ls539646485", None),
    ("Star Trek", "ls547463722", None),
    ("Star Wars Universe", "ls501373412", None),
    ("X-Men Universe", "ls567618635", None),
)

_DC_LISTS: tuple[tuple[str, str, tuple[str, object], tuple[str, ...] | None], ...] = (
    ("DC Universe", "tmdb_list", ("id", 8642250), None),
    ("DC Extended Universe", "mdblist_list", ("list", "fa11en82/dc-extended-universe"), None),
    ("In Association With DC", "mdblist_list", ("list", "fa11en82/in-association-with-dc"), None),
)

CONTENT_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="content_universes",
        category="franchises",
        name="Universes",
        description=(
            "Eight cross-franchise universes -- %s -- each built from the "
            "public IMDb list Kometa's own defaults name for it. Kometa's "
            "ninth universe, DC, is not here: it is the three-collection DC "
            "preset beside this one (`content_dc`), which replaced the single "
            "DC Universe row this pack used to carry -- tick that preset to "
            "have DC, in three collections instead of one. The three that "
            "are film-only are offered for Movie libraries only, as Kometa's "
            "allowed_libraries has them. Kometa's remaining seven universes "
            "are MDBList-hosted under a URL shape this service's mdblist_list "
            "builder cannot address, so they are absent rather than guessed "
            "at."
            % ", ".join(title for title, _list, _types in _UNIVERSE_LISTS)
        ),
        kometa_source=NOT_KOMETA
        + (
            "the eight lists are defaults/both/universe.yml's transcriptions, "
            "but not the file whole: its DC row is deliberately not "
            "reproduced (it split into content_dc, whose three lists are not "
            "Kometa's), and its seven MDBList universes are absent"
        ),
        library_types=_BOTH,
        collections=tuple(
            PresetCollection(
                title=title,
                builder="imdb_list",
                params=(("list", list_id),),
                library_types=library_types,
            )
            for title, list_id, library_types in _UNIVERSE_LISTS
        ),
    ),
    # The three-way DC split (operator directive, 2026-08-30): the DCU, the
    # DCEU and everything DC that is canonically in neither are three
    # different continuities, so they are three collections rather than one
    # row of ``_UNIVERSE_LISTS``. Sources, the directive's own mapping:
    #
    #   themoviedb.org/list/8642250-dc-studios-dcu       -> "DC Universe"
    #   mdblist.com/lists/fa11en82/dc-extended-universe  -> "DC Extended Universe"
    #   mdblist.com/lists/fa11en82/in-association-with-dc -> "In Association With DC"
    #
    # All three verified live before transcription (the phase's T1 probe;
    # counts and composition in its PR). Each row is (title, builder, one
    # params pair, library_types) -- the params KEY differs per builder
    # (``tmdb_list`` takes ``id``, ``mdblist_list`` takes ``list``), so the
    # pair is written whole rather than derived. ``library_types`` narrows a
    # single-media list below the preset's Movie+Show, exactly as
    # ``_UNIVERSE_LISTS``' column does. The drift guard is
    # ``test_the_dc_source_refs_have_not_drifted_by_one_entry``: editing a
    # ref here is a two-site change, table plus digest.
    Preset(
        key="content_dc",
        category="franchises",
        name="DC",
        description=(
            "Three DC collections where the Universes pack used to have one, "
            "because the three are technically not the same universe. 'DC "
            "Universe' is DC Studios' current DCU slate, film and TV, from "
            "public TMDb list 8642250. 'DC Extended Universe' is the "
            "2013-2023 DCEU, from the public MDBList "
            "fa11en82/dc-extended-universe. 'In Association With DC' is "
            "everything DC that is not canonically in either universe, from "
            "fa11en82/in-association-with-dc. Enabling this beside the "
            "Universes pack does not collide: that pack's DC row moved here. "
            "An operator who had the old DC Universe collection gets it back "
            "under the same title with the new membership by ticking this "
            "preset; until then the pass reports the old collection as "
            "unmanaged every pass and deletes nothing (deleting it takes "
            "collections.delete_unconfigured, which is off by default)."
        ),
        kometa_source=NOT_KOMETA
        + (
            "the three lists are an operator directive (TMDb 8642250; "
            "MDBList fa11en82/dc-extended-universe and "
            "fa11en82/in-association-with-dc) -- Kometa's universe.yml has "
            "one DC row, not three"
        ),
        library_types=_BOTH,
        collections=tuple(
            PresetCollection(
                title=title,
                builder=builder,
                params=(param,),
                library_types=library_types,
            )
            for title, builder, param, library_types in _DC_LISTS
        ),
    ),
    Preset(
        key="content_genres",
        category="content",
        name="Genres",
        description=(
            "One collection per genre the library actually holds -- Kometa's "
            "largest pack, transcribed from `defaults/both/genre.yml` (its "
            "addon merges, which fold 'Action & Adventure' into both Action and "
            "Adventure and 'Biography' into Biopic, are "
            "`collections/packs.py`'s table). Built by the per-value engine "
            "phase 10a shipped: `builder: dynamic`, `type: genre`, one "
            "definition that expands against the library on every pass, titled "
            "in Kometa's own shape. 21 genres on the production movie library "
            "and 14 on the shows, inside the engine's default "
            "`max_collections` of 50, so this pack pins no cap. Ordered "
            "newest-first, which is upstream's own `release.desc`, and each "
            "collection holds every title that matches rather than a top-N: "
            "upstream sets no per-collection limit and neither does this "
            "pack. The genre attribute's own caveat stands and is why the "
            "family is built by SEARCH rather than from the section listing, "
            "which phase 9a's probe found truncates to two tags per item (row "
            "%d). To build something other than what this pack builds, copy it "
            "into a `definitions:` entry of your own and edit it there -- a "
            "preset is a key, not a copy of the definitions it stands for."
            % STRANDED_FILTER_ROW
        ),
        kometa_source="defaults/both/genre.yml",
        library_types=_BOTH,
        collections=(
            PresetCollection(
                title="Genres", builder="dynamic", params=packs.GENRE_PARAMS,
            ),
        ),
    ),
    Preset(
        key="content_franchises",
        category="franchises",
        name="Franchises",
        description=(
            "One collection per TMDb franchise collection the library holds, "
            "with Kometa's addon merges (Prometheus into Alien, Minions into "
            "Despicable Me) and its 'Collection' suffix removed, transcribed "
            "from `defaults/movie/franchise.yml`. Movie libraries only, as "
            "upstream has it: TMDb collections are movie franchises. Built by "
            "`builder: facts_family`, `type: tmdb_collection` -- the family "
            "enumerates the franchises this library's items belong to from the "
            "`belongs_to_collection` id the facts pipeline stores off the "
            "`/movie/{id}` read it already makes, and each collection's MEMBERS "
            "are that franchise's own parts through the `tmdb_collection` "
            "builder, so a film the library owns but has not been gathered yet "
            "still joins its collection. That enumeration is what row %d was "
            "filed for, and it closed with this pack. "
            "What that costs, said plainly: the family is as complete as the "
            "facts pipeline's coverage. A freshly-deployed library starts small "
            "and grows as the ratings-drift sweep works through it "
            "(`scheduler.drift_days`, `scheduler.drift_batch_size` -- 500 items "
            "a week by default), and each pass reports how many items it has "
            "visited. "
            "`max_collections` is pinned at %d, which is this service's "
            "judgement and not Kometa's -- upstream caps nothing, and unlike "
            "the packs beside this one there is no include list to compute a "
            "ceiling from, so the number is ten times the engine's own default "
            "of 50: a guard against runaway enumeration (a mount or filter "
            "break producing thousands of buckets from nothing), not against "
            "a large library's organic franchise count. Past it the family "
            "creates nothing and reports both numbers rather than building a "
            "plausible fraction of itself. "
            "Three more things this pack does differently from upstream, "
            "because an operator can see all three. Kometa builds no "
            "franchise collection until the library holds TWO of its films "
            "(`minimum_items: 2`); this family has no such floor, so a "
            "franchise you own one film of still gets a collection. Where "
            "Kometa's addons fold two TMDb collections into one (Prometheus "
            "into Alien), the builder here takes a single collection id -- so "
            "such a bucket builds the first of the two and names the other in "
            "the pass report, where you can write it as a definition of your "
            "own. Twelve buckets can do that, and only if you hold both "
            "halves. And where upstream merges extra movie ids into a "
            "collection's own membership (`template_variables.movie` -- "
            "Hobbs & Shaw into The Fast and the Furious, Once Upon a Deadpool "
            "into X-Men), this family's membership is the franchise's own "
            "`parts` through the `tmdb_collection` builder alone, so 29 "
            "franchise collections are missing the films Kometa adds to "
            "them. "
            "To build something other than what this pack builds, copy it into "
            "a `definitions:` entry of your own and edit it there -- a preset "
            "is a key, not a copy of the definitions it stands for."
            % (
                TMDB_COLLECTION_TYPE_ROW,
                dict(packs.FRANCHISE_PARAMS)["max_collections"],
            )
        ),
        kometa_source="defaults/movie/franchise.yml",
        library_types=_MOVIE,
        collections=(
            PresetCollection(
                title="Franchises", builder="facts_family",
                params=packs.FRANCHISE_PARAMS,
            ),
        ),
    ),
    Preset(
        key="content_based_on",
        category="content",
        name="Based on...",
        description=(
            "Based on a Book, a Comic, a True Story, a Video Game. Kometa "
            "builds these from TMDb keyword NAMES ('based on novel', 'based on "
            "comic book'), which it resolves to ids by searching TMDb at run "
            "time; the tmdb_keyword builder here takes an id, and the expansion "
            "is pure and cannot search. A keyword id written out here from "
            "memory is exactly the invention this table refuses, so the pack "
            "waits for the resolution step, which is row %d. Not the per-value "
            "engine: based.yml is a FIXED four-collection pack, and row %d "
            "has since landed (phase 10a) without closing this one, exactly as "
            "this row said it would not." % (
                KEYWORD_RESOLUTION_ROW, DYNAMIC_ENGINE_ROW
            )
        ),
        kometa_source="defaults/both/based.yml",
        library_types=_BOTH,
        readiness=GATED,
        gated_row=KEYWORD_RESOLUTION_ROW,
    ),
)


# --- the CONTENT RATINGS category ---------------------------------------------
#
# ``defaults/movie/content_rating_us.yml`` is a per-value pack whose values are
# a FIXED include list, so unlike the genre and country packs it does not need
# the enumeration engine: five buckets, each a ``plex_all`` definition filtered
# to the certifications Kometa's ``addons`` table folds into that bucket. The
# value lists below are that table, verbatim -- Kometa's own answer to "which
# of the world's certifications count as PG-13 here".
#
# Two deliberate omissions. Kometa's ``other_name`` bucket ("Not Rated Movies")
# is everything the five did not claim, which is a set complement the engine
# owns and this expansion cannot compute -- and the Common Sense family already
# builds a collection under that exact title (``buckets.derive_buckets``), so
# building it here would be the duplicate-title collision twice over. And the
# Show form lives in ``defaults/show/content_rating_us.yml`` with a different
# include list (TV-G..TV-MA); it is a separate transcription -- the table
# below this one -- so this row is Movie-only rather than quietly reusing film
# certifications for television.
_US_CONTENT_RATINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("G", (
        "G", "gb/U", "gb/0+", "U", "TV-Y", "TV-G", "E", "gb/E",
        "1", "2", "3", "4", "5", "6", "01", "02", "03", "04", "05", "06",
        "G - All Ages", "A", "no/A",
    )),
    ("PG", (
        "PG", "gb/PG", "gb/9+", "TV-PG", "TV-Y7", "TV-Y7-FV",
        "7", "8", "9", "07", "08", "09", "10", "11", "PG - Children",
        "no/5", "no/05", "no/6", "no/06", "no/7", "no/07",
    )),
    ("PG-13", (
        "PG-13", "gb/12A", "gb/12", "12+", "TV-13", "gb/14+", "gb/15", "TV-14",
        "12", "13", "14", "15", "16", "PG-13 - Teens 13 or older",
        "no/9", "no/09", "no/10", "no/11", "no/12",
    )),
    ("R", (
        "R", "17", "18", "gb/18", "MA-17", "TVMA", "TV-MA",
        "R - 17+ (violence & profanity)", "R+ - Mild Nudity",
        "no/15", "no/16", "no/18",
    )),
    ("NC-17", ("NC-17", "gb/R18", "gb/X", "R18", "X", "Rx - Hentai")),
)

# The Show half of the same pack, ``defaults/show/content_rating_us.yml``: the
# same fixed-include shape as the table above, over the US TV Parental
# Guidelines instead of the MPA's film certifications. Five buckets, 73 values
# with each key prepended to its own addon list -- and no bucket lists its own
# key, so nothing de-duplicates away and the counts below are the file's.
#
# Kometa's ``other_name`` bucket ("Not Rated Shows") is omitted for the reason
# it is omitted above: a set complement the engine owns, under a title the
# Common Sense family already builds on Show libraries.
#
# These titles need no disambiguating prefix. TV-G..TV-MA share no name with
# anything else the catalog builds -- the Movie table's G/PG/PG-13/R/NC-17 are
# a different alphabet -- so unlike a regional family this row can carry
# Kometa's titles bare, and the all-presets-on collision test proves it.
_US_SHOW_CONTENT_RATINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("TV-G", (
        "TV-G", "gb/U", "gb/0+", "U", "G", "1", "2", "3", "4", "5", "6", "01",
        "02", "03", "04", "05", "06", "G - All Ages", "A", "no/A",
    )),
    ("TV-Y", (
        "TV-Y", "TV-Y7", "TV-Y7-FV", "7", "8", "9", "07", "08", "09", "no/5",
        "no/05", "no/6", "no/06", "no/7", "no/07",
    )),
    ("TV-PG", (
        "TV-PG", "gb/PG", "gb/9+", "10", "11", "12", "13", "PG - Children",
        "no/9", "no/09", "no/10", "no/11", "no/12",
    )),
    ("TV-14", (
        "TV-14", "gb/12A", "12+", "PG-13", "TV-13", "gb/14+", "gb/15", "14",
        "15", "16", "17", "PG-13 - Teens 13 or older", "no/15", "no/16",
    )),
    ("TV-MA", (
        "TV-MA", "18", "gb/18", "MA-17", "NC-17", "R", "TVMA",
        "R - 17+ (violence & profanity)", "R+ - Mild Nudity", "Rx - Hentai",
        "no/18",
    )),
)

# The five REGIONAL families, ``defaults/both/content_rating_{uk,de,au,nz,
# mal}.yml``: the same fixed-include shape as the two US tables above, over
# five other certification systems. Kometa ships each as ONE ``both`` file
# rather than a movie/show pair, so each row here is a both-libraries row and
# the same bucket builds ``UK 12 Movies`` on a film library and ``UK 12
# Shows`` on a television one.
#
# **These titles carry a region prefix and the US ones do not.** Upstream
# titles all seven families identically (``<<key_name>> <<library_typeU>>s``),
# which across six co-enabled families collides eight ways: AU, NZ and MAL all
# have a ``G`` bucket and so does the US movie table, ``PG``, ``M``, ``R``,
# ``PG-13``, ``18`` and ``R18`` repeat likewise, and UK's ``12`` collides with
# DE's own ``12``. Two built-in definitions
# sharing one title overwrite each other on every pass and the members hash
# flaps between them forever -- and that is exactly the collision
# ``_titles_must_not_collide`` structurally cannot catch (module docstring),
# because it compares operator definitions against the built-ins and never the
# built-ins against each other. So every regional bucket carries its country
# code, which is upstream's OWN answer to the same problem one object along:
# Kometa's separator collection for each of these files is region-prefixed
# (``UK Ratings Collections``, ``DE Ratings Collections``) while the US pack's
# is bare. The US buckets stay bare for that reason and a harder one -- the
# ``G Movies`` row shipped, deployments already build it, and renaming it here
# would strand the collection already sitting in their library.
#
# Four transcription details below, each deliberate:
#
# - AU's ``G`` and ``PG`` and NZ's ``G``, ``PG`` and ``R18`` addon lists
#   already contain their own bucket key, which the ``(key,) + addons``
#   prepend the US tables use would duplicate. De-duped order-preserving, so
#   the stored counts are BELOW the files': AU 100 values of 102, NZ 108 of
#   111.
# - MAL's ``G`` lists both ``0`` and ``"0"`` -- a YAML int and a YAML string
#   for one certification -- which is one value once Plex sees it, so MAL
#   stores 73 of its file's 74.
# - DE's and UK's bucket keys are YAML integers (``0:``, ``12:``). They are
#   certifications rather than numbers and are stored as strings.
# - UK's ``18`` bucket carries ``gb/18+ `` with a trailing space in the raw
#   file. YAML strips it and so does this table: a filter value with a
#   trailing space matches nothing.
#
# Kometa's ``other_name`` bucket is omitted for all five, for the reason it is
# omitted for the US tables: a set complement the engine owns, under a title
# the Common Sense family already builds on both kinds of library.
_UK_CONTENT_RATINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("U", (
        "U", "gb/U", "gb/Uc", "gb/0+", "gb/6+", "gb/Kids & Family", "G",
        "TV-Y", "TV-G", "E", "gb/E", "0", "1", "2", "3", "4", "5", "6", "01",
        "02", "03", "04", "05", "06", "G - All Ages", "A", "no/A",
    )),
    ("PG", (
        "PG", "gb/PG", "gb/9+", "gb/7", "gb/7+", "TV-PG", "TV-Y7", "TV-Y7-FV",
        "7", "8", "9", "10", "11", "07", "08", "09", "PG - Children", "no/5",
        "no/05", "no/6", "no/06", "no/7", "no/07",
    )),
    ("12", (
        "12", "gb/12", "gb/A", "gb/Caution", "gb/G",
        "PG-13 - Teens 13 or older", "no/9", "no/09", "no/10", "no/11",
        "no/12",
    )),
    ("12A", (
        "12A", "gb/12A", "12+", "PG-13", "TV-13", "12",
        "PG-13 - Teens 13 or older", "no/9", "no/09", "no/10", "no/11",
        "no/12",
    )),
    ("15", (
        "15", "gb/15", "gb/14+", "gb/16", "gb/16+", "gb/AA", "TV-14", "13",
        "14", "PG-13 - Teens 13 or older", "no/15", "no/16",
    )),
    ("18", (
        "18", "gb/18", "gb/18+", "MA-17", "TVMA", "TV-MA", "R", "16", "17",
        "NC-17", "R - 17+ (violence & profanity)", "gb/X", "no/18",
    )),
    ("R18", ("R18", "gb/R18", "X", "R+ - Mild Nudity", "Rx - Hentai")),
)

_DE_CONTENT_RATINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("0", (
        "0", "de/0", "U", "1", "2", "3", "4", "5", "01", "02", "03", "04",
        "05", "G", "TV-G", "TV-Y", "G - All Ages", "gb/U", "gb/0+", "E",
        "gb/E", "A", "no/A", "no/5", "no/05",
    )),
    ("6", (
        "6", "de/6", "gb/9+", "TV-PG", "TV-Y7", "TV-Y7-FV", "PG", "7", "8",
        "9", "10", "11", "07", "08", "09", "PG - Children", "no/6", "no/06",
        "no/7", "no/07", "no/9", "no/09", "no/10", "no/11",
    )),
    ("12", (
        "12", "de/12", "gb/12", "no/12", "gb/15", "gb/14+", "TV-14", "13",
        "14", "15", "PG-13 - Teens 13 or older", "PG-13", "no/15",
    )),
    ("16", (
        "16", "de/16", "no/16", "A-17", "TVMA", "TV-MA", "R", "17", "M/PG",
    )),
    ("18", (
        "18", "de/18", "gb/18", "M", "no/18", "R18", "gb/R18", "gb/X", "X",
        "NC-17", "R+ - Mild Nudity", "Rx - Hentai",
    )),
    ("BPjM", ("BPjM", "de/BPjM Restricted", "BPjM Restricted")),
)

# ``G`` and ``PG`` each list their own key upstream: 102 values in the file,
# 100 here.
_AU_CONTENT_RATINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("G", (
        "G", "au/G", "de/0", "U", "0", "1", "2", "3", "4", "5", "6", "01",
        "02", "03", "04", "05", "06", "TV-G", "TV-Y", "G - All Ages", "gb/U",
        "gb/0+", "E", "gb/E", "A", "no/A", "no/5", "no/05",
    )),
    ("PG", (
        "PG", "au/PG", "de/6", "gb/9+", "TV-PG", "TV-Y7", "TV-Y7-FV", "7",
        "8", "9", "10", "11", "07", "08", "09", "PG - Children", "no/6",
        "no/06", "no/7", "no/07", "no/9", "no/09", "no/10", "no/11",
    )),
    ("M", (
        "M", "au/M", "de/12", "gb/12", "no/12", "gb/15", "gb/14+", "TV-14",
        "12", "13", "14", "15", "PG-13 - Teens 13 or older", "PG-13", "no/15",
    )),
    ("MA15+", (
        "MA15+", "au/MA15+", "au/MA 15+", "de/16", "no/16", "A-17", "TVMA",
        "TV-MA", "R", "16", "17", "M/PG",
    )),
    ("R18+", (
        "R18+", "au/R 18+", "au/R18+", "de/18", "gb/18", "M", "18",
        "R - 17+ (violence & profanity)", "no/18", "R18", "gb/X", "X",
        "NC-17", "R+ - Mild Nudity", "Rx - Hentai",
    )),
    ("X18+", (
        "X18+", "gb/R18", "au/X 18+", "au/X18+", "de/BPjM Restricted",
        "BPjM Restricted",
    )),
)

# ``G``, ``PG`` and ``R18`` each list their own key upstream: 111 values in the
# file, 108 here. The R13/RP13, R16/RP16 and R18/RP18 pairs overlap on purpose
# -- Kometa's own tables, transcribed as they are.
_NZ_CONTENT_RATINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("G", (
        "G", "au/G", "de/0", "U", "0", "1", "2", "3", "4", "5", "6", "01",
        "02", "03", "04", "05", "06", "TV-G", "TV-Y", "G - All Ages", "gb/U",
        "gb/0+", "E", "gb/E", "A", "no/A", "no/5", "no/05",
    )),
    ("PG", (
        "PG", "au/PG", "de/6", "gb/9+", "TV-PG", "TV-Y7", "TV-Y7-FV", "7",
        "8", "9", "10", "11", "07", "08", "09", "PG - Children", "no/6",
        "no/06", "no/7", "no/07", "no/9", "no/09", "no/10", "no/11",
    )),
    ("M", (
        "M", "au/M", "de/12", "gb/12", "no/12", "gb/15", "gb/14+", "TV-14",
        "12", "13", "14", "15", "PG-13 - Teens 13 or older", "PG-13", "no/15",
    )),
    ("R13", ("R13", "13", "14")),
    ("RP13", ("RP13", "14")),
    ("R15", (
        "R15", "au/MA15+", "de/16", "no/16", "A-17", "TVMA", "TV-MA", "R",
        "16", "17", "M/PG",
    )),
    ("R16", ("R16", "16", "17")),
    ("RP16", ("RP16", "17")),
    ("R18", (
        "R18", "au/R 18+", "de/18", "gb/18", "M", "18",
        "R - 17+ (violence & profanity)", "no/18", "gb/R18", "gb/X", "X",
        "NC-17", "R+ - Mild Nudity", "Rx - Hentai",
    )),
    ("RP18", ("RP18", "18")),
    ("R", ("R", "au/X 18+", "de/BPjM Restricted", "BPjM Restricted")),
)

# ``G`` lists ``0`` twice, once as a YAML int and once as a string: 74 values
# in the file, 73 here.
_MAL_CONTENT_RATINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("G", (
        "G", "gb/U", "gb/0+", "U", "0", "1", "2", "3", "4", "5", "6", "01",
        "02", "03", "04", "05", "06", "G - All Ages", "TV-G", "A", "no/A",
    )),
    ("PG", (
        "PG", "TV-Y7", "TV-Y7-FV", "7", "8", "9", "07", "08", "09", "gb/PG",
        "gb/9+", "10", "11", "12", "PG - Children", "no/5", "no/05", "no/6",
        "no/06", "no/7", "no/07",
    )),
    ("PG-13", (
        "PG-13", "13", "gb/12A", "12+", "TV-13", "gb/14+", "gb/15", "14",
        "15", "16", "PG-13 - Teens 13 or older", "no/9", "no/09", "no/10",
        "no/11", "no/12",
    )),
    ("R", (
        "R", "17", "18", "gb/18", "MA-17", "NC-17", "TVMA",
        "R - 17+ (violence & profanity)", "no/15", "no/16", "no/18",
    )),
    ("R+", ("R+", "R+ - Mild Nudity")),
    ("Rx", ("Rx", "Rx - Hentai")),
)

# One row per regional family: the country code (the key suffix), the title
# prefix (``code.upper()`` for all five rows today, kept as its own column
# rather than derived so a family whose prefix must differ from its code has
# somewhere to put it), the picker's label, the table, the sentence naming the
# rating system, and the note its semantics need. The trap notes are the
# point of the last column -- a bucket whose letter means something else in
# another country is a collection an operator would only find wrong by opening
# it.
_REGIONAL_RATINGS: tuple[
    tuple[str, str, str, tuple[tuple[str, tuple[str, ...]], ...], str, str],
    ...,
] = (
    ("uk", "UK", "UK certificates", _UK_CONTENT_RATINGS,
     "Seven collections -- %s -- grouping the library by its BBFC "
     "certificate.",
     "'UK 12' and 'UK 12A' are two buckets rather than one because Kometa "
     "keeps the video certificate and the cinema one apart."),
    ("de", "DE", "German FSK ratings", _DE_CONTENT_RATINGS,
     "Six collections -- %s -- grouping the library by its FSK age rating.",
     "'DE BPjM' is not an age band: it is the bucket for titles on the German "
     "restricted index, which is a different kind of classification."),
    ("au", "AU", "Australian classifications", _AU_CONTENT_RATINGS,
     "Six collections -- %s -- grouping the library by its Australian "
     "Classification Board rating.",
     "'AU M' is the Australian advisory rating and NOT the US 'M'; the two "
     "systems happen to share a letter and mean different things."),
    ("nz", "NZ", "New Zealand classifications", _NZ_CONTENT_RATINGS,
     "Eleven collections -- %s -- grouping the library by its New Zealand "
     "classification.",
     "Three things worth knowing, all Kometa's own and all kept verbatim: "
     "'NZ R' is the X-rated RESTRICTED bucket rather than anything "
     "R-for-mature; 'NZ M' is not the US 'M'; and the RP buckets overlap the "
     "R ones by design, so a title rated 14 lands in 'NZ R13' AND in "
     "'NZ RP13'."),
    ("mal", "MAL", "MyAnimeList ratings", _MAL_CONTENT_RATINGS,
     "Six collections -- %s -- grouping the library by its MyAnimeList "
     "rating.",
     "MyAnimeList's own scale rather than a national board's, which is why "
     "'MAL Rx' exists and why an anime library is the one this row is for."),
)

_REGIONAL_RATING_PRESETS: tuple[Preset, ...] = tuple(
    Preset(
        key="content_ratings_%s" % code,
        category="content_ratings",
        name=name,
        description=(
            "%s Each bucket carries Kometa's own addon list, so a title "
            "certified under another system lands in the matching bucket "
            "rather than in nothing. %s The titles carry the "
            "'%s' prefix -- unlike the US rows, which shipped bare -- because "
            "these families share bucket letters with each other and switching "
            "two of them on would otherwise build two collections under one "
            "title."
            % (
                headline % ", ".join(
                    "%s %s" % (prefix, key) for key, _values in table
                ),
                note,
                prefix,
            )
        ),
        kometa_source="defaults/both/content_rating_%s.yml" % code,
        library_types=_BOTH,
        collections=tuple(
            PresetCollection(
                title="%s %s %%ss" % (prefix, key),
                builder="plex_all",
                filters=(("content_rating", values),),
            )
            for key, values in table
        ),
    )
    for code, prefix, name, table, headline, note in _REGIONAL_RATINGS
)

CONTENT_RATING_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="content_ratings_us",
        category="content_ratings",
        name="US certifications",
        description=(
            "Five collections -- %s -- grouping the library's films by MPA "
            "certification. Each bucket carries Kometa's own addon list, so a "
            "film certified TV-14 or gb/12A lands in PG-13 rather than in "
            "nothing. Plex's own contentRating, deliberately distinct from the "
            "Common Sense age buckets in this same tab: same idea, different "
            "rating system, and the two never share a title."
            % ", ".join("%s Movies" % key for key, _values in _US_CONTENT_RATINGS)
        ),
        kometa_source="defaults/movie/content_rating_us.yml",
        library_types=_MOVIE,
        collections=tuple(
            PresetCollection(
                title="%s %%ss" % key,
                builder="plex_all",
                filters=(("content_rating", values),),
            )
            for key, values in _US_CONTENT_RATINGS
        ),
    ),
    Preset(
        key="content_ratings_us_show",
        category="content_ratings",
        name="US TV ratings",
        description=(
            "Five collections -- %s -- grouping the library's series by their "
            "TV Parental Guidelines rating. Each bucket carries Kometa's own "
            "addon list, so a series certified PG-13 or gb/12A lands in TV-14 "
            "rather than in nothing. The television counterpart of "
            "'content_ratings_us', which is Movie-only: Kometa ships the two "
            "as separate files with separate include lists, and so does this "
            "catalog rather than reusing film certifications for television. "
            "Plex's own contentRating, deliberately distinct from the Common "
            "Sense age buckets in this same tab: same idea, different rating "
            "system, and the two never share a title."
            % ", ".join(
                "%s Shows" % key for key, _values in _US_SHOW_CONTENT_RATINGS
            )
        ),
        kometa_source="defaults/show/content_rating_us.yml",
        library_types=_SHOW,
        collections=tuple(
            PresetCollection(
                title="%s %%ss" % key,
                builder="plex_all",
                filters=(("content_rating", values),),
            )
            for key, values in _US_SHOW_CONTENT_RATINGS
        ),
    ),
) + _REGIONAL_RATING_PRESETS


# --- the LOCATION category ----------------------------------------------------
#
# Three packs, all shipping. ``location_country`` groups Plex's own ``country``
# tag through the dynamic engine. The other two group TMDb's ``origin_country``
# through the ``facts_family`` engine -- gated at row 196 until the
# location-names phase vendored the code->name table
# (``collections/iso_names.py``, fetched from /configuration/countries) and
# MEASURED the join between every reachable TMDb name and the grouping tables'
# 647 member strings (docs/research/tmdb-iso-names/README.md) before either
# pack flipped. The two value sets disagree on exactly the co-productions an
# operator would notice, and all three descriptions say so.
_COUNTRY_PRESET = Preset(
    key="location_country",
    category="location",
    name="Countries",
    description=(
        "One collection per country the library's own metadata names, with "
        "Kometa's own list of 255 country names and the addon merges that fold "
        "Plex's spellings into them -- 'Bolivarian Republic of Venezuela' "
        "becomes Venezuela -- transcribed from `defaults/movie/country.yml`. "
        "Movie libraries only, as upstream has it. Built by `builder: "
        "dynamic`, `type: country`: 63 values on the production movie library, "
        "measured by phase 10a's probe, and the same Plex `country` tag "
        "upstream enumerates, so this is Kometa's own value set and not a "
        "near-miss for it. Kometa's title shape (the country's name, and "
        "nothing else) ships unchanged. `max_collections` is pinned at 256 -- "
        "that include list plus the leftovers bucket, which is the most this "
        "family can ever build -- so a library with a wider spread than the "
        "one measured is not refused by a number nobody set. What the include "
        "list costs: a Plex country title outside those 255 names lands in "
        "'Other Countries' rather than getting a collection of its own. "
        "The `Regions` and `Continents` packs beside this one group a "
        "different field with the same bare title shape, and their lists "
        "touch this one's: co-enable `Regions` and two names -- 'Antarctica' "
        "and 'Micronesia' -- are a collection in each pack, so two managed "
        "rows rebuild one title from different value sets on every pass; "
        "with `Continents` it is 'Antarctica' alone. Upstream's own files "
        "overlap the same way, so all three rows disclose it rather than "
        "renaming a transcription. "
        "Ordered newest-first, upstream's own `release.desc`, and each "
        "collection holds every title that matches rather than a top-N, which "
        "is also upstream's -- its pack sets no per-collection limit. To "
        "build something else, copy this pack into a `definitions:` entry of "
        "your own and edit it there."
    ),
    kometa_source="defaults/movie/country.yml",
    library_types=_MOVIE,
    collections=(
        PresetCollection(
            title="Countries", builder="dynamic", params=packs.COUNTRY_PARAMS,
        ),
    ),
)

_REGION_PRESET = Preset(
    key="location_region",
    category="location",
    name="Regions",
    description=(
        "One collection per world region (Northern Europe, South-Eastern "
        "Asia, the Caribbean and the rest of Kometa's 23-entry include "
        "list), grouping TMDb's origin-country field with the name-keyed "
        "addon tables transcribed verbatim -- alias spellings and all -- "
        "from `defaults/movie/region.yml`. Movie libraries only, as "
        "upstream has it. Built by `builder: facts_family`, `type: "
        "origin_country`: the enumeration is the ISO-3166-1 codes the facts "
        "pipeline stores off the `/movie/{id}` read it already makes "
        "(shipped when row %d closed), and each code maps UP to TMDb's own "
        "English name through the vendored `/configuration/countries` table "
        "(`collections/iso_names.py`) -- the code->name join row %d was "
        "filed for, measured per-name before this pack shipped "
        "(docs/research/tmdb-iso-names/README.md) and closed with it. Ten of "
        "TMDb's 251 country codes -- 250 unique names, one of them carried "
        "by two codes -- are named differently by upstream's tables "
        "('Faeroe Islands' for 'Faroe Islands'); each of those "
        "spellings is added to the group its upstream twin already sits in, "
        "which is this catalog's own addition and the only thing added to "
        "upstream's grouping tables. This "
        "is a DIFFERENT value set from the `Countries` pack beside this "
        "one, which groups Plex's own `country` tag: enable both and they "
        "will disagree on exactly the co-productions you would notice. They "
        "also share a title shape with it and with `Continents`: "
        "'Antarctica' and 'Micronesia' are a region here and a country "
        "there, and 'Antarctica' is a continent too, so co-enabling any two "
        "of the three means two managed collections rebuilding one title "
        "from different value sets on every pass. Upstream's own files "
        "overlap the same way; these rows disclose it rather than renaming "
        "a transcription. A "
        "country the tables do not place falls into 'Other Regions' "
        "honestly, upstream's own leftovers rule. Titled with Kometa's own "
        "title shape (the region's name, nothing else). What the family "
        "builds tracks what the facts pipeline has VISITED: a fresh library "
        "starts small, correct and growing as the drift sweep works through "
        "the rest (`scheduler.drift_days`, `scheduler.drift_batch_size` -- "
        "500 items a week by default, so a library of a couple of thousand "
        "movies and shows fills in over about five weekly passes), and each "
        "pass reports its coverage. "
        "`max_collections` is pinned at %d -- the include list plus the "
        "leftovers bucket, the most this family can ever build -- so no "
        "library is refused by a number nobody set. To build something "
        "else, copy this pack into a `definitions:` entry of your own."
        % (TMDB_ORIGIN_COUNTRY_ROW, TMDB_COUNTRY_NAME_ROW,
           len(packs._REGION_INCLUDE) + 1)
    ),
    kometa_source="defaults/movie/region.yml",
    library_types=_MOVIE,
    collections=(
        PresetCollection(
            title="Regions", builder="facts_family", params=packs.REGION_PARAMS,
        ),
    ),
)

_CONTINENT_PRESET = Preset(
    key="location_continent",
    category="location",
    name="Continents",
    description=(
        "One collection per continent -- the coarsest of the three location "
        "packs, its 6-entry include list and addon tables transcribed "
        "verbatim from `defaults/movie/continent.yml`. Movie libraries "
        "only, as upstream has it. Built by `builder: facts_family`, "
        "`type: origin_country`, exactly as the Regions pack beside it: "
        "stored ISO codes (row %d) mapped UP to TMDb's English names "
        "through the vendored table (`collections/iso_names.py`, the join "
        "row %d was filed for, measured per-name first -- "
        "docs/research/tmdb-iso-names/README.md), with the same ten TMDb "
        "spellings upstream's tables miss added to their own groups, this "
        "catalog's own addition beside the transcription. A DIFFERENT value "
        "set "
        "from the `Countries` pack, which groups Plex's own `country` tag "
        "-- the two disagree on exactly the co-productions you would "
        "notice. 'Antarctica' is also an entry in the `Countries` and "
        "`Regions` lists, and all three packs title with the bare name, so "
        "co-enabling any two of them means two managed collections "
        "rebuilding one title on every pass -- upstream's own files overlap "
        "the same way, and these rows disclose it rather than renaming a "
        "transcription. A country the tables do not place falls into 'Other "
        "Continents' honestly. Titled with Kometa's own title shape (the "
        "continent's name, nothing else). Coverage tracks what the facts "
        "pipeline has VISITED and converges via the drift sweep "
        "(`scheduler.drift_days`, `scheduler.drift_batch_size` -- 500 "
        "items a week by default, so a library of a couple of thousand "
        "movies and shows fills in over about five weekly passes). "
        "`max_collections` is pinned at %d -- "
        "the include list plus the leftovers bucket, the most this family "
        "can ever build. To build something else, copy this pack into a "
        "`definitions:` entry of your own."
        % (TMDB_ORIGIN_COUNTRY_ROW, TMDB_COUNTRY_NAME_ROW,
           len(packs._CONTINENT_INCLUDE) + 1)
    ),
    kometa_source="defaults/movie/continent.yml",
    library_types=_MOVIE,
    collections=(
        PresetCollection(
            title="Continents", builder="facts_family",
            params=packs.CONTINENT_PARAMS,
        ),
    ),
)

LOCATION_PRESETS: tuple[Preset, ...] = (
    _COUNTRY_PRESET, _REGION_PRESET, _CONTINENT_PRESET,
)


# --- the MEDIA category -------------------------------------------------------
#
# ``defaults/both/resolution.yml`` reproduces exactly, and it is the one pack
# here that does: its ``include`` list is fixed (4k, 1080, 720, 480) and its
# ``addons`` fold the neighbouring resolutions into each bucket, so four
# ``plex_all`` definitions filtered on ``resolution`` say the same thing.
#
# Movie libraries only, and not because Kometa says so -- it offers the pack
# for both. A show's resolution is a property of its episodes, and the tier-1
# ``resolution`` accessor reads ``<Media videoResolution>`` off the section
# listing, which on a Show library lists shows (``filters.FILTER_ATTRIBUTES``,
# the resolution row). A Show form would need per-episode traversal, so the
# preset narrows rather than shipping a filter that answers nothing.
_RESOLUTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("4k", ("4k", "8k")),
    ("1080", ("1080", "2k")),
    ("720", ("720",)),
    ("480", ("480", "144", "240", "360", "sd", "576")),
)

MEDIA_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="media_resolution",
        category="media",
        name="Resolutions",
        description=(
            "Four collections -- %s -- grouping films by video resolution, with "
            "Kometa's addon merges (8k into 4k, and 576/360/240/144/sd into "
            "480). Movie libraries only: a show's resolution belongs to its "
            "episodes, which the one library walk this service pays for does "
            "not reach."
            % ", ".join("%s Movies" % key for key, _values in _RESOLUTIONS)
        ),
        kometa_source="defaults/both/resolution.yml",
        library_types=_MOVIE,
        collections=tuple(
            PresetCollection(
                title="%s %%ss" % key,
                builder="plex_all",
                filters=(("resolution", values),),
            )
            for key, values in _RESOLUTIONS
        ),
    ),
    Preset(
        key="media_aspect",
        category="media",
        name="Aspect ratios",
        description=(
            "Eight collections, one per aspect ratio Kometa names -- 1.33 "
            "Academy Aperture through 2.77 Cinerama. The values are a fixed "
            "list and would need no enumeration; what is missing is the "
            "attribute. Row %d shipped its tier-1 half, and `aspect` is not "
            "one of the tier-1 rows in collections/filters.py -- it sits in "
            "the filter residue that row carries (55 names after 9a, 53 now "
            "that 9b added plays and last_played to the same table). Note "
            "what 9b did NOT do for it: `aspect` is one of the 44 names in "
            "Kometa's filter vocabulary with no Plex SEARCH field at all, so "
            "the plex_search builder cannot reach it either. This waits on a "
            "client-side metadata budget, not on a query language."
            % FILTER_TIER_TWO_ROW
        ),
        kometa_source="defaults/both/aspect.yml",
        library_types=_BOTH,
        readiness=GATED,
        gated_row=FILTER_TIER_TWO_ROW,
    ),
    Preset(
        key="media_audio_language",
        category="media",
        name="Audio languages",
        description=(
            "One collection per audio language in the library -- a per-value "
            "enumeration, which is why it is gated on the engine and not on "
            "an attribute. Phase 9b moved the blocker without lifting it. The "
            "CLIENT-side path is still what row %d describes: audio languages "
            "live on the streams under <Media><Part>, and 9a's probe found "
            "the section listing stops at Part -- zero stream elements across "
            "200 movies, against four for the same film from the metadata "
            "endpoint. But the SEARCH path answers: 9b's live probe read 46 "
            "audioLanguage values straight off the library and searched them "
            "(docs/research/plex-search-probe/README.md). So the data path "
            "exists and so does the enumerator: one collection per distinct "
            "value, with the naming and lifecycle machinery, SHIPPED in phase "
            "10a, and this row is that engine pointed at Kometa's pack -- "
            "`builder: dynamic`, `type: audio_language`, one definition that "
            "expands against the library on every pass. Two things the preset "
            "carries because 9b measured them: Kometa expands a base code to "
            "every variant the library holds and joins them with the enclosing "
            "block's conjunction, so a language predicate under `all:` matches "
            "NOTHING (0 against 24 under `any:`, measured); and the value "
            "vocabulary is a mix of 2-letter, locale, 3-letter, "
            "script-qualified and one literal english, so no single "
            "normalisation target is correct. Which is why the buckets are "
            "named from Plex's own choice titles -- the fallback branch "
            "upstream itself ships, since Kometa names them from TMDb's "
            "ISO-639-1 table at run time and this service has no such table; "
            "vendoring one is roadmap row %d, and it would change names only, "
            "never membership. Kometa's own 187-code include list ships with "
            "it, so a code outside that list lands in 'Other Audio' rather "
            "than getting a collection of its own; 34 of the 46 the production "
            "movie library holds are on the list. `max_collections` is pinned "
            "at 188 -- that include list plus the leftovers bucket, which is "
            "the most this family can ever build however wide the library is, "
            "so no library is refused by a number nobody set. Ordered "
            "newest-first, upstream's own `release.desc`, and each collection "
            "holds every title that matches rather than a top-N, which is "
            "also upstream's -- its pack sets no per-collection limit. To "
            "build something else, copy this pack into a `definitions:` entry "
            "of your own and edit it there."
            % (STRANDED_FILTER_ROW, TMDB_LANGUAGE_NAME_ROW)
        ),
        kometa_source="defaults/both/audio_language.yml",
        library_types=_BOTH,
        collections=(
            PresetCollection(
                title="Audio languages",
                builder="dynamic",
                params=packs.AUDIO_LANGUAGE_PARAMS,
            ),
        ),
    ),
    Preset(
        key="media_subtitle_language",
        category="media",
        name="Subtitle languages",
        description=(
            "One collection per subtitle language, in exactly the same "
            "position as the audio-language pack and for the same reasons. "
            "Client-side, no stream element reaches the section listing at "
            "all, so the values are readable only at the per-item metadata "
            "cost; through a search they are readable now -- 9b's live probe "
            "read 115 subtitleLanguage values and searched them, and the "
            "`all:`-conjunction trap is worse here, 0 against 442 under "
            "`any:`. Built by the per-value enumeration engine phase 10a "
            "shipped: `builder: dynamic`, `type: subtitle_language`, one "
            "definition "
            "that expands against the library on every pass, titled in "
            "Kometa's own shape. Kometa's 187-code include list ships with it, "
            "transcribed from `defaults/both/subtitle_language.yml` -- whose "
            "list is byte-identical to the audio pack's, so one table serves "
            "both -- and a code outside it lands in 'Other Subtitles' rather "
            "than getting a collection of its own; 57 of the 115 the "
            "production movie library holds are on the list. The buckets are "
            "named from Plex's own choice titles, which is the fallback branch "
            "upstream itself ships, since Kometa names them from TMDb's "
            "ISO-639-1 table at run time and this service has no such table "
            "(vendoring one is roadmap row %d, and it would change names only, "
            "never membership); the value vocabulary is a mix of 2-letter, "
            "locale, 3-letter, script-qualified and one literal english, so no "
            "single normalisation target is correct and some titles are Plex's "
            "wording rather than Kometa's. `max_collections` is pinned at 188 "
            "-- that include list plus the leftovers bucket, which is the most "
            "this family can ever build however wide the library is -- so the "
            "large multilingual library this pack exists for is not refused by "
            "a number nobody set. Ordered newest-first, upstream's own "
            "`release.desc`, and each collection holds every title that "
            "matches rather than a top-N, which is also upstream's. Switching "
            "this off is a family-wide narrowing: every collection it built "
            "becomes a sweep candidate at once. That sweep is off unless "
            "`collections.delete_unconfigured` is on, it never exceeds "
            "`collections.max_deletes` in a pass, it skips protected labels -- "
            "and past that cap it refuses the whole library's sweep and "
            "reports the numbers rather than deleting a prefix of the family. "
            "To build something else, copy this pack into a `definitions:` "
            "entry of your own and edit it there."
            % TMDB_LANGUAGE_NAME_ROW
        ),
        kometa_source="defaults/both/subtitle_language.yml",
        library_types=_BOTH,
        collections=(
            PresetCollection(
                title="Subtitle languages",
                builder="dynamic",
                params=packs.SUBTITLE_LANGUAGE_PARAMS,
            ),
        ),
    ),
)


# --- the PEOPLE category ------------------------------------------------------
#
# Kometa's four people packs name NO people. Every one of them is a dynamic
# collection that scans the library, counts credits and keeps the twenty-five
# names with at least five appearances (``data: {depth: 5, limit: 25}``) -- so
# there is no list of directors in Kometa's defaults to transcribe, and writing
# one and calling it Kometa's would be a fabricated attribution.
#
# What ships instead is a starter set that says whose opinion it is: six
# directors, chosen here, built by the ``tmdb_director`` builder Phase 8c
# delivered. The only thing borrowed from Kometa is the title SHAPE
# (``<<key_name>> (Director)``), and the row's provenance says exactly that.
# Every id below was checked against themoviedb.org while this table was
# written; a TMDb person id recalled rather than looked up resolves to a
# different person and builds a plausible, wrong collection.
_STARTER_DIRECTORS: tuple[tuple[str, int], ...] = (
    ("Steven Spielberg", 488),
    ("Christopher Nolan", 525),
    ("Quentin Tarantino", 138),
    ("Martin Scorsese", 1032),
    ("Stanley Kubrick", 240),
    ("Hayao Miyazaki", 608),
)

# key, name, upstream file, the ``credits_family`` type, the phrase the
# description opens with, library types.
#
# Every phrase says "Plex credits" rather than "are in": the counts behind them
# come from this service's credits cache, which is a FLOOR -- Plex caps its
# credit list at 200 roles per item, so "the twenty-five actors with the most
# appearances" would be a completeness claim the data cannot support. The rest
# of the disclosure is in the shared description below.
_PERSON_PACKS: tuple[tuple[str, str, str, str, str, tuple[str, ...]], ...] = (
    ("people_top_actors", "Top actors", "defaults/both/actor.yml", "actor",
     "the twenty-five actors Plex credits on the most items in the library",
     _BOTH),
    ("people_top_directors", "Top directors", "defaults/movie/director.yml",
     "director",
     "the twenty-five directors Plex credits on the most films in the library",
     _MOVIE),
    ("people_top_writers", "Top writers", "defaults/movie/writer.yml", "writer",
     "the twenty-five writers Plex credits on the most films in the library",
     _MOVIE),
    ("people_top_producers", "Top producers", "defaults/movie/producer.yml",
     "producer",
     "the twenty-five producers Plex credits on the most films in the library",
     _MOVIE),
)

PEOPLE_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="people_directors",
        category="people",
        name="Director starter set",
        description=(
            "One filmography collection for each of six directors -- %s -- read "
            "from TMDb's credits. Each collection takes its summary from the "
            "director's TMDb biography and its poster from their TMDb profile "
            "photo; a `summary:` of your own, or a poster file in the assets "
            "folder, still wins. The six are OUR choice, not Kometa's: "
            "defaults/movie/director.yml names no directors at all, it "
            "enumerates them from the library, which needed the library-wide "
            "credit scan of roadmap row %d. That scan ships now, and the 'Top "
            "directors' row below is the enumerated family -- a DIFFERENT "
            "membership from this one, deliberately: this pack's collections "
            "are TMDb FILMOGRAPHIES, and that pack's are Plex TAG searches "
            "over the files you actually have. The only thing borrowed from "
            "Kometa's file here is the '<name> (Director)' title shape."
            % (", ".join(name for name, _id in _STARTER_DIRECTORS), PERSON_SCAN_ROW)
        ),
        kometa_source=NOT_KOMETA + "the six people are ours",
        library_types=_MOVIE,
        collections=tuple(
            PresetCollection(
                title="%s (Director)" % name,
                builder="tmdb_director",
                params=(("id", tmdb_id),),
            )
            for name, tmdb_id in _STARTER_DIRECTORS
        ),
    ),
) + tuple(
    Preset(
        key=key,
        category="people",
        name=name,
        description=(
            "One smart collection per person: %s. `depth: 5, limit: 25` is "
            "upstream's own data block, and the membership is upstream's own "
            "semantic -- each collection is a Plex search on the person's TAG, "
            "the library's credit data, NOT a TMDb filmography (which credits "
            "people the library's files do not name; the two are different "
            "memberships under the same name, and this pack chooses the tag "
            "on purpose). The people are counted from this service's credits "
            "cache, which the weekly scan fills "
            "(`scheduler.credits_scan_days`): on a library the scan has not "
            "finished, the family is smaller than it will be -- correct, "
            "incomplete, and converging, and the pass's own report says so in "
            "numbers. The counts are a FLOOR and the ranking inherits it: "
            "Plex caps its credit list at 200 people per item, so somebody "
            "whose every appearance is in a large cast can be missing from "
            "this family altogether. \"Most-credited\" here means "
            "most-credited of what Plex answered, which is not the same claim "
            "as most-credited in the library. `limit:` is filled from the "
            "people this library's own tag vocabulary can be searched for "
            "(roadmap row 224): somebody the credits cache counted but Plex "
            "will not answer a tag search on does not take one of the slots, "
            "and the next-most-credited person takes it instead." % what
        ),
        kometa_source=source,
        library_types=library_types,
        collections=(
            PresetCollection(
                title=name,
                builder="credits_family",
                params=(("type", kind), ("depth", 5), ("limit", 25)),
            ),
        ),
    )
    for key, name, source, kind, what, library_types in _PERSON_PACKS
)


# --- the PRODUCTION category --------------------------------------------------
#
# ``defaults/both/streaming.yml`` reproduces, and it is the most valuable row
# in this task: Kometa builds each service's collection from a TMDb
# ``/discover`` query filtered by watch provider, and the provider ids are
# written out in the file (``tmdb_key``). Transcribed below with Kometa's own
# default ``region: US``, which is also what decides WHICH services are in the
# pack -- the file's ``allowed_streaming`` conditionals switch Channel 4, ITVX
# and NOW off outside GB, Movistar/Atresplayer/Filmin outside ES and Crave
# outside CA, so a US deployment gets these fifteen. A deployment elsewhere
# wants a different fifteen; that is a per-region variant of this row and not
# something to fake by shipping every service and hoping.
#
# ``watch_region`` is not optional decoration: TMDb ignores a watch-provider
# filter that arrives without it and answers the UNFILTERED query, which
# ``tmdb_discover.COMPANION_RULES`` refuses at load. ``sort_by`` is the file's
# own ``popularity.desc``.
_STREAMING_REGION = "US"

# key_name -> (TMDb watch provider id(s), library types). The ids are
# ``tmdb_key`` verbatim, pipe forms included -- TMDb reads ``531|1770`` as
# "either of these", which is why the column is a string.
_STREAMING_SERVICES: tuple[tuple[str, str, tuple[str, ...] | None], ...] = (
    ("Apple TV", "350", None),
    ("BET+", "1759", None),
    ("Crunchyroll", "283", _SHOW),
    ("discovery+", "510", _SHOW),
    ("Disney+", "337", None),
    ("HBO Max", "1899", None),
    ("hayu", "223", _SHOW),
    ("Hulu", "15", None),
    ("Netflix", "8", None),
    ("Paramount+", "531|1770", None),
    ("Peacock", "387", None),
    ("Prime Video", "9", None),
    ("AMC+", "528|1854", None),
    ("YouTube", "188", None),
    ("tubi", "73", None),
)

PRODUCTION_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="production_streaming",
        category="production",
        name="Streaming services",
        description=(
            "One collection per streaming service -- %s -- built from TMDb's "
            "watch-provider data for the %s region, which is Kometa's own "
            "default. Three of them (Crunchyroll, discovery+, hayu) are offered "
            "for Show libraries only, as Kometa has them. The seven services "
            "Kometa switches off outside GB, ES and CA are not in this row; a "
            "non-US deployment wants a different set, and shipping all of them "
            "would build empty collections nobody asked for."
            % (
                ", ".join(name for name, _id, _types in _STREAMING_SERVICES),
                _STREAMING_REGION,
            )
        ),
        kometa_source="defaults/both/streaming.yml",
        library_types=_BOTH,
        collections=tuple(
            PresetCollection(
                title="%s %%ss" % name,
                builder="tmdb_discover",
                params=(
                    ("with_watch_providers", provider),
                    ("watch_region", _STREAMING_REGION),
                    ("sort_by", "popularity.desc"),
                ),
                library_types=library_types,
            )
            for name, provider, library_types in _STREAMING_SERVICES
        ),
    ),
    Preset(
        key="production_studio",
        category="production",
        name="Studios",
        description=(
            "One collection per studio, over the 485 names Kometa's include "
            "list carries -- the animation studios above all -- with the addon "
            "merges that fold a studio's other spellings into one bucket (Toei "
            "into Toei Animation, MGM into Metro-Goldwyn-Mayer), transcribed "
            "from `defaults/both/studio.yml`. Built by the per-value engine "
            "phase 10a shipped: `builder: dynamic`, `type: studio`, one "
            "definition that expands against the library on every pass. The "
            "number the include list answers for: phase 10a's probe measured "
            "824 studio values on the production movie library, and upstream "
            "ships no leftovers bucket for this pack, so a studio outside "
            "those 485 names builds nothing at all rather than getting a "
            "collection of its own. `max_collections` is pinned at 485 -- "
            "Kometa's include list is that long and a whitelist is applied "
            "last, so the family cannot exceed it whatever the library holds, "
            "and the 824 raw values never reach it. The title format is this "
            "pack's one divergence: Kometa titles these with the studio's name "
            "and nothing else, and gives the country and network packs the "
            "same bare shape -- three families that would then build "
            "identically-named collections the moment an operator switched two "
            "of them on. This pack is the one in both of those pairs, so it "
            "takes a qualified format instead ('Top <studio> Movies', 'Top "
            "<studio> Shows') and the other two keep Kometa's unchanged. One "
            "narrowing relative to upstream, stated rather than hidden: Kometa "
            "additionally matches the 20th Century Studios bucket on a "
            "substring, which this engine's single search block cannot "
            "express, so that bucket matches the exact names in its addon list. "
            "Ordered newest-first, upstream's own `release.desc`, and each "
            "collection holds every title that matches rather than a top-N, "
            "which is also upstream's. Switching this off is a family-wide "
            "narrowing: every collection it built becomes a sweep candidate at "
            "once. That sweep is off unless `collections.delete_unconfigured` "
            "is on, it never exceeds `collections.max_deletes` in a pass, it "
            "skips protected labels -- and past that cap it refuses the whole "
            "library's sweep and reports the numbers rather than deleting a "
            "prefix of the family. To build something else, copy this pack "
            "into a `definitions:` entry of your own and edit it there."
        ),
        kometa_source="defaults/both/studio.yml",
        library_types=_BOTH,
        collections=(
            PresetCollection(
                title="Studios", builder="dynamic", params=packs.STUDIO_PARAMS,
            ),
        ),
    ),
    Preset(
        key="production_network",
        category="production",
        name="Networks",
        description=(
            "One collection per television network -- a per-value "
            "enumeration, over the 272 names Kometa's include list carries. "
            "Phase 9a's "
            "probe found Plex 1.43.4 emits no `network` ITEM attribute at all "
            "(zero of 284 shows in the section listing AND absent from the "
            "per-item metadata endpoint), which strands the client-side "
            "filter for good -- no request budget buys it, row %d. The SEARCH "
            "FIELD is a different mechanism and it answers: 9b's live probe "
            "read 91 networks off the same library and a search on one "
            "returned its shows, whose `studio` values (S4C, ITV1) disagree "
            "with the network name -- so `network` carries information no "
            "other shipped attribute does, and is not a `studio` alias "
            "(docs/research/plex-search-probe/README.md). Built by the "
            "per-value engine phase 10a shipped, show-only: `builder: "
            "dynamic`, `type: network`, one definition that expands against "
            "the library on every pass, with its choices listing probed at the "
            "same 91 values before the type was allowed to ship. Kometa's own "
            "272-name include list ships with it, from "
            "`defaults/show/network.yml`, together with the addon merges that "
            "fold a broadcaster's regional and sibling channels into the "
            "parent (Sky Atlantic and Sky Cinema into Sky, the ESPN family "
            "into ESPN); upstream ships no leftovers bucket here either, so a "
            "network outside those names builds nothing rather than getting a "
            "collection of its own. Kometa's title shape (the network's name, "
            "and nothing else) ships unchanged. `max_collections` is pinned at "
            "272 -- the include list's own length, which a whitelist applied "
            "last makes a hard ceiling however wide the library is. Breadth "
            "caveat, unchanged by shipping the pack: the search mechanism is "
            "proven at ONE network with two shows; 91 exist, and nothing has "
            "yet checked that they all behave. Upstream additionally gates "
            "this type on the New Plex TV Agent and this service does not, so "
            "a library whose agent cannot answer enumerates nothing and the "
            "family refuses rather than creating a partial pack. Ordered "
            "newest-first, upstream's own `release.desc`, and each collection "
            "holds every title that matches rather than a top-N, which is also "
            "upstream's. Switching this off is a family-wide narrowing: every "
            "collection it built becomes a sweep candidate at once. That sweep "
            "is off unless `collections.delete_unconfigured` is on, it never "
            "exceeds `collections.max_deletes` in a pass, it skips protected "
            "labels -- and past that cap it refuses the whole library's sweep "
            "and reports the numbers rather than deleting a prefix of the "
            "family. To build something else, copy this pack into a "
            "`definitions:` entry of your own and edit it there."
            % STRANDED_FILTER_ROW
        ),
        kometa_source="defaults/show/network.yml",
        library_types=_SHOW,
        collections=(
            PresetCollection(
                title="Networks", builder="dynamic", params=packs.NETWORK_PARAMS,
            ),
        ),
    ),
)


# --- the TIME category --------------------------------------------------------
TIME_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="time_year",
        category="time",
        name="Best of each year",
        description=(
            "Kometa's 'Best of <year>' for the last ten years: one collection "
            "per year, each the ten highest-rated titles released in it. Two "
            "of the three things it needed have landed. The per-value engine "
            "SHIPPED in phase 10a with `type: year`, and so did the per-key "
            "sort and limit that make each collection a top-N by rating rather "
            "than a filter -- so a definition builds 'one collection per year "
            "the library holds' today (the probe counted 87 of them on the "
            "production movie library, past the default `max_collections` of "
            "50). "
            "What did NOT ship is the counting relative to today: the "
            "`current_year`/`current_year-N` value grammar is the half of row "
            "%d that phase 10a left open, and without it 'the last ten years' "
            "has no expression here -- so this pack waits on that row and not "
            "on the engine. Phase 10b considered shipping the pack it COULD "
            "build -- one collection per year the library holds -- and "
            "refused: the probe counted 87 years on the production movie "
            "library, and 87 unbounded years is not 'Best of the last ten "
            "years' wearing its name. A preset that builds something other "
            "than the pack it cites is worse than one that waits."
            % RELATIVE_YEAR_ROW
        ),
        kometa_source="defaults/both/year.yml",
        library_types=_BOTH,
        readiness=GATED,
        gated_row=RELATIVE_YEAR_ROW,
    ),
    Preset(
        key="time_decade",
        category="time",
        name="Best of each decade",
        description=(
            "'Best of the 1980s' and its neighbours: one collection per decade "
            "the library covers, each the hundred highest-rated films in it. "
            "Built by the per-value engine phase 10a shipped -- `builder: "
            "dynamic`, `type: decade`, one definition that expands against the "
            "library on every pass -- and movie-only, because Plex's decade "
            "filter is. The title shape and the per-key ordering are "
            "transcribed from `defaults/movie/decade.yml`: this is the one "
            "Kometa pack that pins its own `critic_rating.desc` and `limit: "
            "100` rather than taking its template's defaults, and the hundred "
            "in the sentence above is that pin. Twelve decades on the "
            "production movie library, inside the engine's default "
            "`max_collections` of 50, so this pack pins no cap. To build "
            "something other than what this pack builds -- the 25 best of each "
            "decade, say -- copy it into a `definitions:` entry of your own "
            "and edit it there."
        ),
        kometa_source="defaults/movie/decade.yml",
        library_types=_MOVIE,
        collections=(
            PresetCollection(
                title="Decades", builder="dynamic", params=packs.DECADE_PARAMS,
            ),
        ),
    ),
    Preset(
        key="time_seasonal",
        category="time",
        name="Seasonal",
        description=(
            "Nineteen date-windowed collections -- Christmas, Halloween, "
            "Valentine's Day, Black History Month and the rest -- each visible "
            "only around its own date. Two halves are missing and the second is "
            "the harder one. Kometa windows these by DAY (range(03/20-04/30) "
            "for Easter), where the schedule gate row %d DELIVERED is whole "
            "calendar months; and most of these collections are fed by SEVERAL "
            "sources at once (Halloween is three IMDb lists, ten TMDb franchise "
            "collections and one film), which one definition, being one "
            "builder, cannot express. Both are row %d, filed out of 70 for "
            "exactly this pack." % (DATE_WINDOW_ROW, SEASONAL_WINDOW_ROW)
        ),
        kometa_source="defaults/movie/seasonal.yml",
        library_types=_MOVIE,
        readiness=GATED,
        gated_row=SEASONAL_WINDOW_ROW,
    ),
)


# The whole table, in picker order: the awards, the three setting-backed rows,
# then the other categories in the order ``CATEGORIES`` declares them.
# ``content_franchises`` is the one row whose tuple no longer matches its
# category -- it sits in ``CONTENT_PRESETS`` and files under ``franchises``,
# which the listing below reads from the row itself, not from the tuple.
#
# Count checksum, per category rather than as one total (the same shape
# ``tests/test_collection_catalog.py``'s CATALOG_CHECKSUM pins, READY / GATED /
# setting-backed):
#
#   awards           15 / 0 / 1     charts           10 / 0 / 1
#   content           3 / 1 / 0     content_ratings   7 / 0 / 1
#   franchises        1 / 0 / 0     location          3 / 0 / 0
#   media             3 / 1 / 0     people            5 / 0 / 0
#   production        3 / 0 / 0     time              1 / 2 / 0
#
# -- 58 rows: 51 presets an operator can switch on today, 4 that name what
# they would build and the roadmap row that would let them, and 3 rendered
# switches for families that already ship behind a boolean.
CATALOG: tuple[Preset, ...] = (
    AWARD_PRESETS
    + SETTING_PRESETS
    + CHART_PRESETS
    + TRACEARR_PRESETS
    + CONTENT_PRESETS
    + CONTENT_RATING_PRESETS
    + LOCATION_PRESETS
    + MEDIA_PRESETS
    + PEOPLE_PRESETS
    + PRODUCTION_PRESETS
    + TIME_PRESETS
)


def _check_kometa_sources() -> None:
    """Every row's ``kometa_source`` is a pinned path or an honest disclaimer.

    Called at import, not left to a test: a provenance line is only worth
    anything if it points at a file that exists, and the failure mode of a
    typo -- a citation nobody can follow -- is silent forever otherwise.
    """
    for preset in CATALOG:
        if preset.kometa_source.startswith(NOT_KOMETA):
            continue
        if preset.kometa_source not in _KOMETA_DEFAULTS:
            raise AssertionError(
                "%r cites %r, which is not one of the Kometa defaults files "
                "_KOMETA_DEFAULTS pins. Add the path there if the file is "
                "real and was read, or say so with the NOT_KOMETA prefix."
                % (preset.key, preset.kometa_source)
            )


def _check_one_producer_per_row() -> None:
    """No row carries both families' producer.

    ``Preset.definitions`` asks ``award_event`` first and falls through to
    ``collections``, so a row carrying both would build the ceremony and
    silently drop the table -- half a preset, with nothing to say so. Checked
    at import rather than left to the fall-through's ordering, which is not a
    decision anybody made.
    """
    for preset in CATALOG:
        if preset.award_event is not None and preset.collections:
            raise AssertionError(
                "%r carries both an award_event and a collections table; a row "
                "is one family or the other, and Preset.definitions would "
                "expand only the first" % preset.key
            )


def _check_collection_library_types() -> None:
    """A collection's own library types narrow its preset's; they never widen.

    ``Preset.definitions`` filters on ``collection.library_types or
    self.library_types``, which BYPASSES the preset's own types for a row that
    sets its own -- while ``Preset.titles`` and the picker's payload both
    iterate ``self.library_types``. So a collection naming a library type its
    preset does not would be BUILT there and neither listed among the preset's
    titles nor covered by the library types the picker says the row touches: a
    collection nothing in the UI admits to, which is the failure mode
    ``_check_one_producer_per_row`` exists to prevent one row along.

    Subset and not equality, because narrowing is the whole point of the field
    -- three of the fifteen streaming services are show-only inside a
    both-libraries preset.
    """
    for preset in CATALOG:
        for collection in preset.collections:
            if collection.library_types is None:
                continue
            widened = sorted(set(collection.library_types) - set(preset.library_types))
            if widened:
                raise AssertionError(
                    "%r's %r collection is for %s, which its preset is not "
                    "(%s): a collection narrows its preset's library types and "
                    "never widens them, or it builds a collection the picker "
                    "neither lists nor claims to touch"
                    % (
                        preset.key,
                        collection.title,
                        widened,
                        list(preset.library_types),
                    )
                )


_check_kometa_sources()
_check_one_producer_per_row()
_check_collection_library_types()

# Key -> row, for the config validator's presets: lookup only. Setting-backed
# rows are deliberately absent: they are not something ``presets:`` can name,
# so a key like "oscars" typed there is refused as unknown, the same as any
# other typo, rather than accepted as a no-op nobody asked for.
BY_KEY: dict[str, Preset] = {
    preset.key: preset for preset in CATALOG if preset.setting is None
}


def preset_definitions(config, library_type: str) -> list[CollectionDefinition]:
    """The definitions ``config.collections.presets`` stands for. Pure; no I/O.

    In CATALOG order, not in the order the operator listed the keys: the
    definition order is the order a pass runs them in, and re-ordering two
    lines of YAML is not a change to what a deployment builds.

    A scan of the catalog rather than a lookup of the operator's list, and that
    is not an accident of style. ``_titles_must_not_collide`` calls this during
    validation of the very config that carries the keys, so a lookup that could
    raise would turn a mis-typed preset into a 500 on a settings save. Written
    this way it cannot: an unknown key matches no row and expands to nothing,
    and the refusal that makes it an error at all lives in the validator, where
    it can say which key and what the catalog holds.
    """
    wanted = set(config.collections.presets)
    definitions: list[CollectionDefinition] = []
    for preset in CATALOG:
        if preset.key not in wanted:
            continue
        if preset.readiness == GATED:
            # The validator (``schema.py``'s ``_presets_must_be_known_and_
            # ready``) already refuses a GATED key before it can reach a
            # validated config's ``presets`` list, so this is a second guard
            # for a caller that built ``wanted`` some other way -- a direct
            # call, a future one. Explicit rather than relying on no GATED row
            # carrying a producer, which is true today by accident and not by
            # anything this function enforces.
            continue
        definitions += preset.definitions(library_type)
    return definitions


def _read_setting(config, dotted: str) -> bool:
    """Read a ``Preset.setting`` path (``"collections.awards"``) off config."""
    value = config
    for part in dotted.split("."):
        value = getattr(value, part)
    return bool(value)


def catalog_listing(config) -> list[dict]:
    """The catalog as the API serves it: categories, each with its presets.

    Every category is listed, including the ones with no rows yet -- the picker
    shows nine tabs, and a tab that vanished because its rows have not been
    written would read as a category this service does not have.

    ``active`` is read off the config: for an ordinary preset, whether its key
    is in ``collections.presets``; for a setting-backed row (``preset.setting``
    not None), the boolean that path names -- ``collections.charts`` and
    ``collections.awards`` are exactly this now, and every setting-backed row
    after them follows the same rule rather than needing one of its own.
    """
    active = set(config.collections.presets)
    return [
        {
            "key": category,
            "label": label,
            "presets": [
                {
                    "key": preset.key,
                    "name": preset.name,
                    "titles": preset.titles(),
                    "years_title": preset.years_title(),
                    "description": preset.description,
                    "kometa_source": preset.kometa_source,
                    "library_types": list(preset.library_types),
                    "readiness": preset.readiness,
                    "gated_row": preset.gated_row,
                    "setting": preset.setting,
                    "active": (
                        _read_setting(config, preset.setting)
                        if preset.setting is not None
                        else preset.key in active
                    ),
                }
                for preset in CATALOG
                if preset.category == category
            ],
        }
        for category, label in CATEGORIES.items()
    ]
