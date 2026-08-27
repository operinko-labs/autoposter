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

from autoposter.collections.builders.imdb_award import EVENTS
from autoposter.config.schema import CollectionDefinition
from autoposter.providers.tmdb_lists import CHART_ENDPOINTS

# The nine categories, in the order the picker shows them: Kometa's own
# defaults taxonomy, which is what an operator arriving from Kometa is looking
# for. Key -> the label the UI puts on the tab.
CATEGORIES: dict[str, str] = {
    "awards": "Awards",
    "charts": "Charts",
    "content": "Content",
    "content_ratings": "Content Ratings",
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
        """The shape of this preset's dynamic titles, or None if it has none."""
        if self.award_event is None:
            return None
        return EVENTS[self.award_event].year_title % "<year>"


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
            "builds, refreshed from IMDb on every pass."
        ),
        kometa_source="defaults/chart/imdb.yml",
        library_types=("Movie", "Show"),
        setting="collections.charts",
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
        "defaults/both/universe.yml",
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
PERSON_DYNAMIC_ROW = 83    # phase 10c: the dynamic half of the person builders
STRANDED_FILTER_ROW = 155  # the six tier-1 filter attributes the listing strands
FILTER_TIER_TWO_ROW = 96   # the filters subsystem; tier 1 shipped, tier 2 did not
DATE_WINDOW_ROW = 70       # per-collection cadence and date windows: DELIVERED
SEASONAL_WINDOW_ROW = 160  # day-level windows, and a collection fed by several
                           # builders -- filed out of 70, which delivered a
                           # whole-month gate and one builder per definition
KEYWORD_RESOLUTION_ROW = 161  # TMDb keyword name -> id, which no earlier row owns
RELATIVE_YEAR_ROW = 171    # the `current_year`/`current_year-N` value grammar,
                           # the half of that row phase 10a did NOT ship
TMDB_ORIGIN_COUNTRY_ROW = 189  # the two dynamic types that are a TMDb walk and
                               # not an enumeration -- filed by phase 10a-1's
                               # wrap, and cited (not waited on) by the three
                               # location packs, whose values are that walk's

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
# Five of the eight are ``defaults/chart/tmdb.yml``, titles verbatim. The other
# three are charts this service's builder has and that file does not
# (``/movie/now_playing``, ``/movie/upcoming``, ``/trending/*/day``); their
# titles are ours and they say so, because inventing a Kometa attribution is
# worse than admitting there is none.
#
# The IMDb charts are deliberately NOT presets here. They ship behind
# ``collections.charts`` and have a setting-backed row above; a preset key next
# to that boolean would be a second way to build "IMDb Popular", and two
# built-in definitions sharing one title is precisely the collision
# ``_titles_must_not_collide`` cannot catch (module docstring).

# chart key -> (title, kometa_source, what the chart is)
_TMDB_CHARTS: tuple[tuple[str, str, str, str], ...] = (
    ("popular", "TMDb Popular", "defaults/chart/tmdb.yml",
     "what TMDb's audience is looking at right now"),
    ("top_rated", "TMDb Top Rated", "defaults/chart/tmdb.yml",
     "TMDb's highest-scored titles, by its own weighted rating"),
    ("trending_week", "TMDb Trending", "defaults/chart/tmdb.yml",
     "TMDb's trending list over the past week"),
    ("airing_today", "TMDb Airing Today", "defaults/chart/tmdb.yml",
     "series with an episode airing today"),
    ("on_the_air", "TMDb On The Air", "defaults/chart/tmdb.yml",
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
# ``defaults/both/universe.yml`` is the one Content pack that reproduces
# exactly: Kometa builds each universe from a hand-maintained list, and nine of
# its sixteen are public IMDb lists whose ids are written out in the file
# (``imdb_url``). Those nine are transcribed here as ``imdb_list`` definitions
# and the titles are the file's own ``data`` names.
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
    ("DC Universe", "ls524274984", None),
    ("Fast & Furious", "ls4102351575", _MOVIE),
    ("Marvel Cinematic Universe", "ls539646485", None),
    ("Star Trek", "ls547463722", None),
    ("Star Wars Universe", "ls501373412", None),
    ("X-Men Universe", "ls567618635", None),
)

CONTENT_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="content_universes",
        category="content",
        name="Universes",
        description=(
            "Nine cross-franchise universes -- %s -- each built from the public "
            "IMDb list Kometa's own defaults name for it. The three that are "
            "film-only are offered for Movie libraries only, as Kometa's "
            "allowed_libraries has them. Kometa's remaining seven universes are "
            "MDBList-hosted under a URL shape this service's mdblist_list "
            "builder cannot address, so they are absent rather than guessed at."
            % ", ".join(title for title, _list, _types in _UNIVERSE_LISTS)
        ),
        kometa_source="defaults/both/universe.yml",
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
    Preset(
        key="content_genres",
        category="content",
        name="Genres",
        description=(
            "One collection per genre the library actually holds -- Kometa's "
            "largest pack. The per-value engine it needs SHIPPED in phase 10a "
            "(`builder: dynamic`, `type: genre`), so an operator can build this "
            "family today by writing a definition. What is still missing is the "
            "PRESET: turning one catalog row into a family of collections is "
            "the preset-expansion story, phase 10b, and it is what row %d "
            "tracks. The genre attribute's own caveat stands and is why the "
            "family is built by SEARCH rather than from the section listing, "
            "which Phase 9a's probe found truncates to two tags per item "
            "(row %d)." % (DYNAMIC_ENGINE_ROW, STRANDED_FILTER_ROW)
        ),
        kometa_source="defaults/both/genre.yml",
        library_types=_BOTH,
        readiness=GATED,
        gated_row=DYNAMIC_ENGINE_ROW,
    ),
    Preset(
        key="content_franchises",
        category="content",
        name="Franchises",
        description=(
            "One collection per TMDb franchise collection the library holds, "
            "with Kometa's addon merges (Prometheus into Alien, Minions into "
            "Despicable Me) and its 'Collection' suffix removed. The pack is a "
            "per-value enumeration of what the library owns, not a fixed list "
            "of franchises. The engine that does per-value enumeration SHIPPED "
            "in phase 10a -- but not a type this pack can use: "
            "`tmdb_collection` is not a library enumeration at all, and "
            "`collections/dynamic_types.py`'s scope note names it among the "
            "deliberate absences, so there is no `type:` an operator can write "
            "for this family today. The single-collection tmdb_collection "
            "builder it would call is already here; what is outstanding is "
            "that dynamic type, and then the PRESET -- the preset-expansion "
            "story of phase 10b, which is what row %d tracks."
            % DYNAMIC_ENGINE_ROW
        ),
        kometa_source="defaults/movie/franchise.yml",
        library_types=_MOVIE,
        readiness=GATED,
        gated_row=DYNAMIC_ENGINE_ROW,
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
# Three packs, one blocker, and it moved in phase 10a. The include/exclude,
# addon-merge and title-format machinery each of these three files configures
# IS the dynamic engine, and that engine SHIPPED -- so "there is nothing to
# transcribe until it exists" is no longer what these rows are waiting for.
# What is left is named in each description: Kometa reads their values off
# TMDb's origin-country data, which is a full-library TMDb walk rather than a
# ``listFilterChoices`` enumeration, and the Plex ``country`` TAG that DID ship
# as a dynamic type is a different value set.
_LOCATION_PACKS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("location_country", "Countries", "defaults/movie/country.yml",
     "One collection per country of origin, with Kometa's per-country name and "
     "flag styling.", _MOVIE),
    ("location_region", "Regions", "defaults/movie/region.yml",
     "One collection per world region (Nordic, Balkan, Southeast Asia and the "
     "rest of Kometa's grouping).", _MOVIE),
    ("location_continent", "Continents", "defaults/movie/continent.yml",
     "One collection per continent -- the coarsest of the three location "
     "packs.", _MOVIE),
)

LOCATION_PRESETS: tuple[Preset, ...] = tuple(
    Preset(
        key=key,
        category="location",
        name=name,
        description=(
            "%s The per-value engine SHIPPED in phase 10a, so the machinery "
            "this pack is made of -- enumeration, addon merges, key-name "
            "overrides, title formats -- is here. Two things are not. Its "
            "values are Kometa's, read off TMDb's origin-country data, and "
            "that is a full-library TMDb walk rather than an enumeration, "
            "which is why `origin_country` is not one of the ten dynamic types "
            "that shipped (row %d); the Plex `country` tag IS enumerable (63 "
            "values on the production movie library, measured by phase 10a's "
            "probe) but it is a different value set. And the PRESET itself is "
            "the preset-expansion story of phase 10b, which is what row %d "
            "tracks."
            % (description, TMDB_ORIGIN_COUNTRY_ROW, DYNAMIC_ENGINE_ROW)
        ),
        kometa_source=source,
        library_types=library_types,
        readiness=GATED,
        gated_row=DYNAMIC_ENGINE_ROW,
    )
    for key, name, source, description, library_types in _LOCATION_PACKS
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
            "now exists and so does the enumerator: one collection per distinct "
            "value, with the naming and lifecycle machinery, SHIPPED in phase "
            "10a as `builder: dynamic`, `type: audio_language`, so an operator "
            "can build this family today by writing a definition. What is "
            "still missing is the PRESET -- turning one catalog row into a "
            "family is the preset-expansion story, phase 10b, and it is what "
            "row %d tracks. Two things any such preset must carry: Kometa "
            "expands a base code to every variant the library holds and joins "
            "them with "
            "the enclosing block's conjunction, so a language predicate under "
            "`all:` matches NOTHING (0 against 24 under `any:`, measured); "
            "and the value vocabulary is a mix of 2-letter, locale, 3-letter, "
            "script-qualified and one literal english, so no single "
            "normalisation target is correct."
            % (STRANDED_FILTER_ROW, DYNAMIC_ENGINE_ROW)
        ),
        kometa_source="defaults/both/audio_language.yml",
        library_types=_BOTH,
        readiness=GATED,
        gated_row=DYNAMIC_ENGINE_ROW,
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
            "`any:`. The per-value enumerator is no longer missing either: "
            "`type: subtitle_language` SHIPPED in phase 10a and a definition "
            "builds this family today. What is missing is the PRESET, which is "
            "phase 10b's preset-expansion story and what row %d tracks -- with "
            "one caveat the enumeration itself measured: 115 values is past "
            "the engine's default `max_collections` of 50, so this family "
            "refuses until that cap is raised on purpose."
            % DYNAMIC_ENGINE_ROW
        ),
        kometa_source="defaults/both/subtitle_language.yml",
        library_types=_BOTH,
        readiness=GATED,
        gated_row=DYNAMIC_ENGINE_ROW,
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

_PERSON_PACKS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("people_top_actors", "Top actors", "defaults/both/actor.yml",
     "the twenty-five actors with the most appearances in the library", _BOTH),
    ("people_top_directors", "Top directors", "defaults/movie/director.yml",
     "the twenty-five directors with the most films in the library", _MOVIE),
    ("people_top_writers", "Top writers", "defaults/movie/writer.yml",
     "the twenty-five writers with the most films in the library", _MOVIE),
    ("people_top_producers", "Top producers", "defaults/movie/producer.yml",
     "the twenty-five producers with the most films in the library", _MOVIE),
)

PEOPLE_PRESETS: tuple[Preset, ...] = (
    Preset(
        key="people_directors",
        category="people",
        name="Director starter set",
        description=(
            "One filmography collection for each of six directors -- %s -- read "
            "from TMDb's credits. The six are OUR choice, not Kometa's: "
            "defaults/movie/director.yml names no directors at all, it "
            "enumerates them from the library, which needs the credit scan the "
            "Top directors row below waits on. The only thing borrowed from "
            "that file is the '<name> (Director)' title shape."
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
            "One collection per person: %s, with a poster and biography from "
            "TMDb. Waiting on the credit scan -- naming the people means "
            "reading every credit of every item, which is the expensive walk "
            "phase 10c is scoped around." % what
        ),
        kometa_source=source,
        library_types=library_types,
        readiness=GATED,
        gated_row=PERSON_DYNAMIC_ROW,
    )
    for key, name, source, what, library_types in _PERSON_PACKS
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
            "One collection per studio, over the several hundred Kometa's "
            "include list names -- the animation studios above all. The studio "
            "attribute itself IS filterable here (it is one of the nine tier-1 "
            "rows that ship), and the pack is a per-value enumeration with "
            "per-studio name overrides and addon merges -- machinery that is "
            "the engine, not a transcription, and the engine SHIPPED in phase "
            "10a (`builder: dynamic`, `type: studio`), so a definition builds "
            "this family today. What is missing is the PRESET, phase 10b's "
            "preset-expansion story, which is what row %d tracks. One number "
            "any such preset has to answer for: phase 10a's probe measured "
            "824 studio values on the production movie library, far past "
            "the engine's default `max_collections` of 50, so the family "
            "refuses until that cap is raised on purpose."
            % DYNAMIC_ENGINE_ROW
        ),
        kometa_source="defaults/both/studio.yml",
        library_types=_BOTH,
        readiness=GATED,
        gated_row=DYNAMIC_ENGINE_ROW,
    ),
    Preset(
        key="production_network",
        category="production",
        name="Networks",
        description=(
            "One collection per television network -- a per-value "
            "enumeration, and that is now the only thing missing. Phase 9a's "
            "probe found Plex 1.43.4 emits no `network` ITEM attribute at all "
            "(zero of 284 shows in the section listing AND absent from the "
            "per-item metadata endpoint), which strands the client-side "
            "filter for good -- no request budget buys it, row %d. The SEARCH "
            "FIELD is a different mechanism and it answers: 9b's live probe "
            "read 91 networks off the same library and a search on one "
            "returned its shows, whose `studio` values (S4C, ITV1) disagree "
            "with the network name -- so `network` carries information no "
            "other shipped attribute does, and is not a `studio` alias "
            "(docs/research/plex-search-probe/README.md). This preset moved "
            "off the stranded row and onto the engine row %d, and the engine "
            "then landed: `type: network` SHIPPED in phase 10a, show-only, "
            "with its choices listing probed at the same 91 values before it "
            "was allowed to ship -- so a definition builds this family today. "
            "What is still missing is the PRESET, phase 10b's "
            "preset-expansion story, which is what row %d tracks. Breadth "
            "caveat for whoever builds it: the mechanism is proven at ONE "
            "network with two shows; 91 exist, and nothing has yet checked "
            "that they all behave. Upstream additionally gates this type on "
            "the New Plex TV Agent and this service does not, so a library "
            "whose agent cannot answer enumerates nothing and the family "
            "refuses rather than creating a partial pack."
            % (STRANDED_FILTER_ROW, DYNAMIC_ENGINE_ROW, DYNAMIC_ENGINE_ROW)
        ),
        kometa_source="defaults/show/network.yml",
        library_types=_SHOW,
        readiness=GATED,
        gated_row=DYNAMIC_ENGINE_ROW,
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
            "50). What did NOT ship is the counting relative to today: the "
            "`current_year`/`current_year-N` value grammar is the half of row "
            "%d that phase 10a left open, and without it 'the last ten years' "
            "has no expression here. The PRESET is the third thing, and it is "
            "phase 10b's preset-expansion story, which row %d tracks."
            % (RELATIVE_YEAR_ROW, DYNAMIC_ENGINE_ROW)
        ),
        kometa_source="defaults/both/year.yml",
        library_types=_BOTH,
        readiness=GATED,
        gated_row=DYNAMIC_ENGINE_ROW,
    ),
    Preset(
        key="time_decade",
        category="time",
        name="Best of each decade",
        description=(
            "'Best of the 1980s' and its neighbours: one collection per decade "
            "the library covers, each the hundred highest-rated films in it. "
            "The same per-key top-N the year pack needs, over decades the "
            "library turns out to hold -- and both halves SHIPPED in phase "
            "10a: `type: decade` is one of the ten dynamic types (movie-only, "
            "because Plex's decade filter is, which is why this row is too) "
            "and the per-key sort and limit are the engine's. A definition "
            "builds this family today; roughly a dozen decades is well inside "
            "the default `max_collections`. What is missing is the PRESET, "
            "phase 10b's preset-expansion story, which is what row %d tracks."
            % DYNAMIC_ENGINE_ROW
        ),
        kometa_source="defaults/movie/decade.yml",
        library_types=_MOVIE,
        readiness=GATED,
        gated_row=DYNAMIC_ENGINE_ROW,
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
# then the eight other categories in the order ``CATEGORIES`` declares them.
#
# Count checksum, per category rather than as one total (the same shape
# ``tests/test_collection_catalog.py``'s CATALOG_CHECKSUM pins, READY / GATED /
# setting-backed):
#
#   awards           15 / 0 / 1     charts           10 / 0 / 1
#   content           1 / 3 / 0     content_ratings   7 / 0 / 1
#   location          0 / 3 / 0     media             1 / 3 / 0
#   people            1 / 4 / 0     production        1 / 2 / 0
#   time              0 / 3 / 0
#
# -- 57 rows: 36 presets an operator can switch on today, 18 that name what
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
