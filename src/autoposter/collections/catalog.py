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
    assembled two ways.

    "one collection per" rather than "one per": the reader of this line is
    looking at a picker row and deciding whether to switch it on, and the noun
    it is one of is the thing they will find in Plex afterwards.
    """
    return 'one collection per %s, named "%s"' % (what, shape)


def _family_shape(row, params: dict, values_clause: str) -> str:
    """Shared body of ``dynamic_shape`` and ``facts_family_shape``: everything
    but the type table each reads and the clause naming where its values come
    from -- the one sentence an operator needs to read differently between
    them (see each function's own docstring for why).

    The line ends at the title it names. It used to carry a trailing
    parenthetical explaining that the pack's reserved definition title is not
    one of the collections; that sentence was written for a reader of this
    module and printed to an operator choosing a checkbox, on fourteen rows at
    once, and it is the clause the operator asked to be rid of. What the
    reserved title IS stays where it belongs, in ``dynamic_shape``'s docstring
    and in ``titles()``'s.
    """
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
    return _shape_line("%s %s" % (noun, values_clause), shape)


def dynamic_shape(params: dict) -> str:
    """The family-shape line for one dynamic pack.

    Derived, not restated. The format is the pack's own pinned
    ``title_format`` when it has one and the TYPE's default when it does not
    (``dynamic_types.DYNAMIC_TYPES``), and it is rendered by the same
    ``dynamic_titles.render_title`` the builder names collections with -- so
    the sentence in the picker cannot describe a shape the builder will not
    produce. The two unknowns are written as placeholders: the key the library
    will supply, and the library type, which differs between a preset's
    libraries and would be a lie if one of them were picked.

    ``titles()`` reports one more thing next to this shape -- the reserved
    definition title the engine falls through to when a smart builder declares
    none (``builders/dynamic.py``'s "No ``titles()``, deliberately" note),
    under which no collection is ever created. It is not explained in the
    served line any more: the explanation was a paragraph of this module's own
    vocabulary printed on fourteen picker rows, and the line reads correctly
    without it.
    """
    row = DYNAMIC_TYPES[params["type"]]
    return _family_shape(row, params, "in your library")


def facts_family_shape(params: dict) -> str:
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
    return _family_shape(row, params, "this service has gathered facts for")


@dataclass(frozen=True)
class _CreditKindRow:
    """What ``_family_shape`` reads, for the one family whose types are not a
    table of rows: a credit kind is a bare string and its title format lives in
    ``builders/credits_family.TITLE_FORMATS``. Four lines here rather than a
    fifth signature for the shared helper."""

    name: str
    title_format: str


def credits_family_shape(params: dict) -> str:
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
    return _family_shape(row, params, "the credits cache has counted")


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
                return dynamic_shape(dict(collection.params))
            if collection.builder == "facts_family":
                return facts_family_shape(dict(collection.params))
            if collection.builder == "credits_family":
                return credits_family_shape(dict(collection.params))
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
    "emmy": "Television only, the one ceremony here that is.",
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
            "Switched on by the Awards switch in Settings rather than by this "
            "catalog.",
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
            "IMDb Popular, IMDb Top 250 and IMDb Lowest Rated, refreshed from "
            "IMDb on every run. The Charts switch in Settings builds all three "
            "or none, and IMDb Lowest Rated is offered for film libraries only."
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
            "age-rating collections. Those collections are always built; the "
            "Separators switch in Settings turns this divider off."
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
     "films in cinemas now, with a title of ours because Kometa does not list "
     "this chart"),
    ("upcoming", "TMDb Upcoming", NOT_KOMETA + "the title is ours",
     "films with a release date still ahead, with a title of ours as with "
     "TMDb Now Playing"),
    ("trending_day", "TMDb Trending Daily", NOT_KOMETA + "the title is ours",
     "TMDb's trending list over the past day, where the TMDb Trending row "
     "above is the weekly one"),
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
        description="%s: %s, re-read from TMDb on every run." % (title, note),
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
    "from Tracearr's own watch history and recomputed on every run. Needs "
    "Tracearr connected in Settings, with its address and API key; without "
    "them the collection reports itself failed rather than emptying."
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
            "Eight collections for the big crossover worlds, among them the "
            "Marvel Cinematic Universe, Star Wars Universe and Arrowverse, "
            "each built from a public IMDb list. Alien / Predator, Conjuring "
            "Universe and Fast & Furious are offered for film libraries only. "
            "DC has its own row beside this one."
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
            "Three DC collections, because the three are not one universe: DC "
            "Universe is the current slate, DC Extended Universe is the 2013 "
            "to 2023 films, and In Association With DC is everything else. "
            "Turning this on alongside Universes does not clash, and an old DC "
            "Universe collection is taken over rather than left behind."
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
            "One collection per genre in your library, such as Action Movies, "
            "Horror Movies and Comedy Shows, holding everything that matches, "
            "newest first. To change the count, the titles or the order, copy "
            "this set into a definition of your own."
        ),
        kometa_source="defaults/both/genre.yml",
        library_types=_BOTH,
        collections=(
            PresetCollection(
                title="Genres", builder="dynamic", params=packs.GENRE_PARAMS,
            ),
        ),
    ),
    # What this pack does differently from upstream, kept here rather than in
    # the picker's prose: Kometa builds no franchise collection until the
    # library holds two of its films (``minimum_items: 2``) and this family has
    # no such floor; where Kometa's addons fold two TMDb collections into one
    # (Prometheus into Alien) the builder here takes a single collection id, so
    # twelve buckets build the first of the two and name the other in the pass
    # report; and where upstream merges extra movie ids into a collection's own
    # membership (``template_variables.movie``), 29 franchise collections here
    # are missing those films. ``max_collections`` is pinned at 500, ten times
    # the engine's own default: a guard against runaway enumeration, not
    # against a large library's organic franchise count.
    Preset(
        key="content_franchises",
        category="franchises",
        name="Franchises",
        description=(
            "One collection per film franchise in your library, such as James "
            "Bond, The Lord of the Rings and Toy Story. Film libraries only, "
            "and a new install starts small and fills in over a few weeks. To "
            "change the count, the titles or the order, copy this set into a "
            "definition of your own."
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
            "Not built yet. Four collections for films and shows based on a "
            "book, a comic, a true story or a video game."
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
     "Seven collections, %s U through %s R18, grouping your library by its "
     "BBFC certificate.",
     "'UK 12' and 'UK 12A' are two buckets rather than one because Kometa "
     "keeps the video certificate and the cinema one apart."),
    ("de", "DE", "German FSK ratings", _DE_CONTENT_RATINGS,
     "Six collections, %s 0 through %s BPjM, grouping your library by its FSK "
     "age rating.",
     "'DE BPjM' is not an age band: it is the bucket for titles on the German "
     "restricted index."),
    ("au", "AU", "Australian classifications", _AU_CONTENT_RATINGS,
     "Six collections, %s G through %s X18+, grouping your library by its "
     "Australian Classification Board rating.",
     "'AU M' is the Australian advisory rating, not the US 'M'; the two "
     "systems share a letter and mean different things."),
    ("nz", "NZ", "New Zealand classifications", _NZ_CONTENT_RATINGS,
     "Eleven collections, %s G through %s R, grouping your library by its New "
     "Zealand classification.",
     "'NZ R' is the restricted bucket, 'NZ M' is not the US 'M', and a title "
     "rated 14 lands in both 'NZ R13' and 'NZ RP13'."),
    ("mal", "MAL", "MyAnimeList ratings", _MAL_CONTENT_RATINGS,
     "Six collections, %s G through %s Rx, grouping your library by its "
     "MyAnimeList rating.",
     "MyAnimeList's own scale rather than a national board's, which is why "
     "'MAL Rx' exists and why an anime library is the one this row is for."),
)

_REGIONAL_RATING_PRESETS: tuple[Preset, ...] = tuple(
    Preset(
        key="content_ratings_%s" % code,
        category="content_ratings",
        name=name,
        description=(
            "%s Each one also takes the matching certificates from other "
            "countries, so a title classified elsewhere still lands somewhere. "
            "%s" % (headline % (prefix, prefix), note)
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
            "Five collections, %s, grouping your films by their MPA "
            "certificate. Each one also takes the equivalent certificates from "
            "other countries, so a film rated TV-14 lands in PG-13 rather than "
            "nowhere. Separate from the Common Sense age collections in this "
            "same tab, and the two never share a title."
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
            "Five collections, %s, grouping your series by their TV Parental "
            "Guidelines rating. Each one also takes the equivalent ratings from "
            "other systems, so a series rated PG-13 lands in TV-14 rather than "
            "nowhere. The television counterpart of the US certifications row, "
            "which is for films only."
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
        "One collection per country your films come from, such as France, "
        "Japan and Mexico, chosen from Kometa's 255 country names; movie "
        "libraries only. Turning on Regions or Continents rebuilds one title "
        "twice: Antarctica is in all three lists, Micronesia in Regions. To "
        "change the count, the titles or the order, copy this set into a "
        "definition of your own."
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
        "One collection per world region, such as Northern Europe and the "
        "Caribbean; a new install starts small and fills in over a few weeks. "
        "Film libraries only, and with Countries or Continents on, Antarctica "
        "and Micronesia build one title twice. To change the count, the titles "
        "or the order, copy this set into a definition of your own."
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
        "One collection per continent, such as Europe, Asia and South "
        "America; a new install starts small and fills in over a few weeks. "
        "Film libraries only, and with Countries or Regions on, Antarctica "
        "builds one title twice. To change the count, the titles or the order, "
        "copy this set into a definition of your own."
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

# The measured value counts behind the three search-backed families, recorded
# here rather than in the picker's prose. Phase 9b's live probe read 46
# audioLanguage values and 115 subtitleLanguage values straight off the
# production movie library and searched them; the same probe read 91 networks
# off the show library and a search on one returned its shows, whose `studio`
# values (S4C, ITV1) disagree with the network name, so `network` carries
# information no other shipped attribute does
# (docs/research/plex-search-probe/README.md). The client-side path is strand-
# ed for all three -- 9a's probe found the section listing stops at Part, zero
# stream elements across 200 movies, and Plex 1.43.4 emits no `network` item
# attribute at all -- which is why these families are built by search and not
# out of a library walk (roadmap row 155).
#
# Upstream additionally gates the network type on the New Plex TV Agent and
# this service does not, so a library whose agent cannot answer enumerates
# nothing and the family refuses rather than creating a partial set.
MEDIA_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="media_resolution",
        category="media",
        name="Resolutions",
        description=(
            "Four collections, %s, grouping films by video resolution, with "
            "Kometa's merges (8k into 4k, and 576, 360, 240, 144 and sd into "
            "480). Film libraries only: a show's resolution belongs to its "
            "episodes."
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
            "Not built yet. One collection per picture shape, from the "
            "squarish Academy frame to the widest Cinerama."
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
            "One collection per audio language in your library, such as "
            "English Audio, French Audio and Japanese Audio, holding "
            "everything that matches. A language outside Kometa's list of 187 "
            "goes into Other Audio rather than getting a collection of its "
            "own. To change the count, the titles or the order, copy this set "
            "into a definition of your own."
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
            "One collection per subtitle language in your library, such as "
            "English Subtitles, Spanish Subtitles and Korean Subtitles, "
            "holding everything that matches. A language outside Kometa's list "
            "of 187 goes into Other Subtitles rather than getting a collection "
            "of its own. To change the count, the titles or the order, copy "
            "this set into a definition of your own."
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
#
# Two things the four packs no longer say in the picker, because they are
# mechanism and the row has sixty words: the membership is a Plex search on the
# person's TAG -- the library's own credit data, not a TMDb filmography, which
# credits people the library's files do not name -- and `limit:` is filled from
# the people this library's tag vocabulary can actually be SEARCHED for
# (roadmap row 224), so somebody the credits cache counted but Plex will not
# answer a tag search on does not take one of the twenty-five slots.
_PERSON_PACKS: tuple[tuple[str, str, str, str, str, tuple[str, ...]], ...] = (
    ("people_top_actors", "Top actors", "defaults/both/actor.yml", "actor",
     "actors", _BOTH),
    ("people_top_directors", "Top directors", "defaults/movie/director.yml",
     "director", "directors", _MOVIE),
    ("people_top_writers", "Top writers", "defaults/movie/writer.yml", "writer",
     "writers", _MOVIE),
    ("people_top_producers", "Top producers", "defaults/movie/producer.yml",
     "producer", "producers", _MOVIE),
)

PEOPLE_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="people_directors",
        category="people",
        name="Director starter set",
        description=(
            "A collection for each of six directors: %s. Each one takes its "
            "summary and poster from TMDb unless you supply your own. The six "
            "are our pick, not Kometa's."
            % ", ".join(name for name, _id in _STARTER_DIRECTORS)
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
            "One collection per person, for the twenty-five %s your library "
            "credits most often. The list comes from what Plex credits, and "
            "Plex stops at two hundred people per title, so somebody in a "
            "large cast can be missing. To change the count, the titles or the "
            "order, copy this set into a definition of your own." % what
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
            "One collection per streaming service, such as Netflix Movies, "
            "Disney+ Shows and Prime Video Movies, from TMDb's watch-provider "
            "data for the %s region. Crunchyroll, discovery+ and hayu are "
            "offered for show libraries only. The services Kometa switches off "
            "outside Britain, Spain and Canada are not here."
            % _STREAMING_REGION
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
    # Two divergences from upstream, kept here rather than in the picker's
    # prose. The title format is qualified ('Top <studio> Movies') where Kometa
    # titles bare, because the country and network packs title bare too and
    # this is the pack in both of those pairs. And Kometa additionally matches
    # its 20th Century Studios bucket on a substring, which one search block
    # cannot express, so that bucket matches the exact names in its addon list.
    Preset(
        key="production_studio",
        category="production",
        name="Studios",
        description=(
            "One collection per studio, such as Top Pixar Movies and Top "
            "Studio Ghibli Movies, chosen from Kometa's 485 studio names. A "
            "studio outside those names gets no collection at all. To change "
            "the count, the titles or the order, copy this set into a "
            "definition of your own."
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
            "One collection per TV network, such as HBO, BBC One and Netflix, "
            "from Kometa's 272 names, with sister channels folded into the "
            "parent, Sky Atlantic counting as Sky. Show libraries only, and a "
            "network outside those names gets no collection. To change the "
            "count, the titles or the order, copy this set into a definition "
            "of your own."
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
            "Not built yet. One collection for each of the last ten years, "
            "holding the ten highest-rated titles released in it."
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
            "One collection per decade your library covers, such as Best of "
            "the 1980s and Best of the 1990s, each holding the hundred "
            "highest-rated films of that decade. Film libraries only, because "
            "Plex knows a decade for films but not for shows. To change the "
            "count, the titles or the order, copy this set into a definition "
            "of your own."
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
            "Not built yet. Nineteen collections that appear around their own "
            "dates, among them Christmas, Halloween and Valentine's Day."
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
