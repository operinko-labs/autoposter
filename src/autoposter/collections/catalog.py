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

**The Oscars are deliberately not a row.** They are ``collections.awards``,
the boolean that shipped, and they stay it -- an ``award_oscars`` preset would
be a second way to switch on the same four collections, and switching on both
would put two definitions with identical titles into one library's list. (That
is the one collision ``_titles_must_not_collide`` cannot catch: it compares
operator definitions against the built-ins, not the built-ins against each
other.) The picker renders the Oscars from the boolean; this table holds the
fifteen ceremonies that have no boolean of their own.

Key naming: ``<category-ish prefix>_<thing>``, ``award_cannes``. The prefix is
not parsed anywhere -- ``category`` is a field -- it is there so a key reads on
its own in a YAML file that also names charts and people.
"""
from dataclasses import dataclass

from autoposter.collections.builders.imdb_award import EVENTS
from autoposter.config.schema import CollectionDefinition

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
    library type -- a single params dict could not have said that.
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
    # --- the definition-producing fields -----------------------------------
    # An award preset is one ceremony of ``imdb_award.EVENTS``: every static
    # winners collection it has, plus its year-collections placeholder.
    award_event: str | None = None
    # ``award key -> the library types that one collection is for``, as pairs
    # rather than a dict so the row stays hashable like the rest of a frozen
    # dataclass. Empty for every ceremony but one.
    award_library_types: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def definitions(self, library_type: str) -> list[CollectionDefinition]:
        """This preset's definitions for one library type. Pure; no I/O.

        Empty is a real answer: a Movie-only ceremony asked for its Show
        definitions has none, and the caller appends nothing.
        """
        if self.award_event is not None:
            return self._award_definitions(library_type)
        # No other family exists yet -- the eight other categories are Task 5's
        # to populate, and each brings its own producer here. A READY preset
        # that produces nothing for any library type is a preset that does
        # nothing at all, which ``test_collection_catalog.py`` refuses.
        return []

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
        """
        if self.award_event is None:
            return []
        return [award.title for award in EVENTS[self.award_event].awards.values()]

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
# transcribed in ``collections/awards.py`` when that ceremony shipped.
_KOMETA_FILES = {"golden_globes": "golden"}

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
        kometa_source="defaults/award/%s.yml"
        % _KOMETA_FILES.get(event_key, event_key),
        library_types=event.library_types,
        award_event=event_key,
        award_library_types=_AWARD_NARROWING.get(event_key, ()),
    )


AWARD_PRESETS: tuple[Preset, ...] = tuple(
    _award_preset(key) for key in EVENTS if key != "oscars"
)

# The whole table, in picker order. The eight other categories are Task 5's:
# they are absent rather than present-and-empty, because a category with no
# rows is a tab with nothing in it, and an empty placeholder row would be the
# invented content this table exists to avoid.
#
# Count checksum: 15 presets, all in AWARDS -- one per ceremony in EVENTS
# except the Oscars, which are ``collections.awards``.
CATALOG: tuple[Preset, ...] = AWARD_PRESETS

# Key -> row. Built once; every lookup goes through it, including the config
# validator's.
BY_KEY: dict[str, Preset] = {preset.key: preset for preset in CATALOG}


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
        if preset.key in wanted:
            definitions += preset.definitions(library_type)
    return definitions


def catalog_listing(config) -> list[dict]:
    """The catalog as the API serves it: categories, each with its presets.

    Every category is listed, including the ones with no rows yet -- the picker
    shows nine tabs, and a tab that vanished because its rows have not been
    written would read as a category this service does not have.

    ``active`` is read off the config. Presets are the only source of it today;
    the entries that read and write ``collections.charts`` and
    ``collections.awards`` instead arrive with the rows that need them, and
    each carries its own setting name then.
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
                    "active": preset.key in active,
                }
                for preset in CATALOG
                if preset.category == category
            ],
        }
        for category, label in CATEGORIES.items()
    ]
