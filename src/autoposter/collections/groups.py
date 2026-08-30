"""Which block of the collections tab each managed collection belongs to.

Roadmap row 49. Plex's collections tab is one alphabetical list, so a library
with forty managed collections shows them interleaved with the operator's own
and with Plex's franchise ones. Kometa's answer -- and now ours -- is a sort
title that overrides the alphabet: a ``!<section>_`` prefix puts every
collection of one family together, and a blank "separator" collection with an
extra ``!`` floats above the family as its heading.

Everything here is PURE. No Plex, no database, no clock, no network -- it is
called from ``config/schema.py``'s title-collision validator (through
``engine.definition_titles``) while a settings save is being validated, and a
validator that reached the network would turn a config write into an outage.

**Nothing is imported from this package at module scope, deliberately.** The
three tables that need the catalog, the chart inventory and the award registry
take a local import inside the one function that reads them. ``catalog``
imports ``config.schema``, and ``builders/__init__`` imports ``cs_bucket``
which imports ``reconcile`` -- so a module-scope import here would close a ring
through every reconciler that imports this module.
``tests/test_collection_groups.py`` reads this file's own AST to hold it.

**The section numbers are OURS (NOT_KOMETA).** Kometa assigns a
``collection_section`` per defaults file; only four of those are on record here,
and fetching some forty files to transcribe cosmetic arithmetic fails the value
test this repository applies to every other borrowed table. So the numbers below
are spaced tens in our own canonical order, and where an adopted collection
carries a Kometa prefix, ours replaces it on collections this service manages.
Collections it does not manage are never touched.

**The per-member ordering key is OURS too (NOT_KOMETA, LAW Addendum 1/2).**
A family that supplies a natural order gets one -- Common Sense buckets by
ascending age, zero-padded; award years inverted so the newest ceremony files
first; charts in the chart inventory's own order -- and a family that supplies
none renders the plain two-part shape instead. The grounds are stated because
an earlier draft got them wrong: (a) COLLATION-INDEPENDENCE -- an explicit
zero-padded key makes within-block order ours regardless of Plex's collation,
which Task 1's probe proved differs from Python's; and (b) the MEASURED Oscars
hand-order regression (``docs/research/collection-sort-probe/README.md`` §3a).
NOT the Common Sense "Age 2+ after Age 18+" claim -- that was an unlabelled
Python-collation inference the same probe's capture contradicts for leading
digit runs and leaves unverified mid-string.

The two FORMULAS, by contrast, are transcribed and cited:

- the separator's name and summary -- ``<<key_name>> Collections`` and
  ``Section separator for <<key_name>> Collections.`` --
  ``docs/research/kometa-collections.md:429``;
- the sort title -- ``!<<collection_section>><<pre>><<order>><<title>>`` with
  ``pre: "_"`` and an empty order, i.e. ``!<section>_<title>`` --
  ``.superpowers/sdd/p-prefetch-upstream.md:386-388``. That empty-order
  rendering is upstream's own and is our fallback case, when no ordering key
  applies; ``member_sort_title`` fills the slot for the families that have one.
  The separator template (``collections/reconcile.py:36-44``) is the same with
  an extra ``!`` and never an ordering key -- a section has one heading, so
  there is nothing for a key to order it against.
"""
from dataclasses import dataclass

# The ten groups, in the order the tab shows them. The first nine are exactly
# ``catalog.CATEGORIES``' keys -- Kometa's own defaults taxonomy, which is what
# an operator arriving from Kometa is looking for, and which the collections
# picker already sorts its tabs by. The tenth is ours: an operator's own
# ``definitions:`` entry belongs to no Kometa category, and putting it last is
# the honest place for "everything this table did not name".
#
# Written out rather than derived from ``CATEGORIES``, and that is the point:
# ``CATEGORIES`` declares its keys ALPHABETICALLY (``catalog.py:69-79``), so
# iterating it would file awards ahead of charts and call the result an order
# somebody chose. A test pins that the two differ.
CANONICAL_ORDER: tuple[str, ...] = (
    "charts",
    "awards",
    "content_ratings",
    "content",
    "location",
    "media",
    "people",
    "production",
    "time",
    "operator",
)

OPERATOR_GROUP = "operator"

# What goes in the ``<<key_name>>`` slot of the transcribed separator formula.
# Singular, because the formula appends "Collections": "Chart Collections", not
# "Charts Collections". ``content_ratings`` maps to "Ratings" because that is
# what the shipped divider is already called on every live server this service
# has run against ("Ratings Collections"), and renaming a live collection is a
# migration nobody asked for.
_KEY_NAMES: dict[str, str] = {
    "charts": "Chart",
    "awards": "Award",
    "content_ratings": "Ratings",
    "content": "Content",
    "location": "Location",
    "media": "Media",
    "people": "People",
    "production": "Production",
    "time": "Time",
}

# The one deliberate exception to the formula. The operator group's key name
# would be "Collections", and the formula would render "Collections
# Collections". The title drops the redundant half; the SUMMARY is built from
# that already-overridden title (``separator_summary`` calls
# ``separator_title``, not the formula directly), so it reads "Section
# separator for Collections." rather than doubling either.
_OPERATOR_TITLE = "Collections"

# Which group a built-in family belongs to, keyed by builder. Only the shipped
# families are here -- the three the catalog's own ``SETTING_PRESETS`` rows
# describe (``collections/catalog.py:546-589``), whose categories these three
# values are pinned against by ``tests/test_collection_groups.py``. Every
# ceremony's year-collections builder is handled by the suffix rule in
# ``builtin_group`` rather than by sixteen rows copied out of
# ``imdb_award.EVENTS``: a second copy of that registry is exactly the drift
# this codebase refuses everywhere else.
_BUILTIN_GROUPS: dict[str, str] = {
    "imdb_chart": "charts",
    "imdb_award": "awards",
    "cs_bucket": "content_ratings",
}

_AWARD_YEARS_SUFFIX = "_award_years"

# The ``Default-Images`` separator artwork stem for each group, as MEASURED --
# see ``docs/research/collection-sort-probe/README.md``. Three of the ten
# resolved, and those three are the hosted half of the hybrid: upstream's own
# art whose baked-in word matches our divider's title, so fetching it is exact
# and costs nothing. The other seven have no such art -- upstream names its
# separators by defaults FILE, not by category, so mapping them to near-miss
# art would put GENRE on a divider titled "Content Collections". They are
# GENERATED instead (``separator_art.py``), which ``separator_poster_key``
# marks with a leading '@'.
SEPARATOR_POSTER_KEYS: dict[str, str] = {
    "charts": "chart",
    "awards": "award",
    # The one already on record: the key the content-ratings divider shipped
    # with before separators were plural. Since then the reconciler passes
    # whatever its ``SeparatorSpec`` carries, and this table is where that
    # comes from -- so this row is the source of that key, not a copy of it.
    "content_ratings": "content_rating",
}

# The 22 separator colour styles, exactly upstream's ``sep_style`` values --
# TRANSCRIBED from ``$colors`` at ``create_default_posters.ps1:5538`` in
# ``Kometa-Team/Defaults-Image-Creation@a9e02e9``, and confirmed against the
# contents-API listing of ``Default-Images/separators/``, which agrees exactly
# (2026-08-30, ``.superpowers/sdd/p-div-font.md`` §2d). ``@base`` is
# deliberately absent: it is the TEXTLESS layer generation draws on, not a
# style an operator can pick. Sorted, and a test pins that, so a future
# addition files predictably.
SEPARATOR_STYLES: tuple[str, ...] = (
    "amethyst", "aqua", "blue", "forest", "fuchsia", "gold", "gray", "green",
    "navy", "ocean", "olive", "orchid", "orig", "pink", "plum", "purple",
    "red", "rust", "salmon", "sand", "stb", "tan",
)

# The marker a generated poster key's stem starts with. '@' because no hosted
# stem can start with it (upstream's only '@' entry is the @base folder
# itself), so the two kinds cannot collide in one namespace.
GENERATED_STEM_PREFIX = "@"


def separator_poster_key(group: str, config) -> str:
    """``"<style>:<stem>"`` -- one string naming both the art and the style.

    The style lives IN the key -- ``hosted_poster_url``'s own award idiom
    (``"<event>:<stem>"``) -- so that function stays pure and config-free, and
    so the key can fold into ``separator_hash``: a style change then reads as
    a definition change, which is what makes it rewrite once and settle
    (``reconcile.py``'s short-circuit would otherwise never re-poster). A
    group with a measured upstream stem keeps it; every other group gets the
    generated marker, ``"<style>:@<group>"``, which ``reconcile_separator``
    routes to ``separator_art`` and ``hosted_poster_url`` refuses.
    """
    style = getattr(config.collections, "separator_style", None) or "orig"
    stem = SEPARATOR_POSTER_KEYS.get(group)
    if stem is None:
        stem = GENERATED_STEM_PREFIX + group
    return "%s:%s" % (style, stem)


# The ordering key a leftovers/other bucket takes, so it files after the
# buckets that name something. OURS (NOT_KOMETA, LAW Addendum 3): Kometa's own
# ``~`` Not-Rated trick does the opposite of what its own live value needs --
# Task 1's capture measured Plex filing ``!110_~Not Rated`` BEFORE
# ``!110_01_Age 1+`` on this server, contradicting the ASCII-order assumption
# the trick relies on. A plain zero-padded high key is verified instead,
# against the same capture's ascending block: two digits, matching the age
# keys' width, and higher than any of them.
LEFTOVERS_ORDER = "99"

# What an inverted year is subtracted from. Four digits, so every ceremony year
# this dataset can hold renders four digits and the keys compare as strings.
_YEAR_CEILING = 9999

# The ordering key a family's PARENT collection takes, in a group where its own
# expansions are keyed -- so the parent leads its family's block instead of
# filing wherever its title happens to land among the keys (a digit-first key
# always sorts ahead of a letter-first title, so an unkeyed parent otherwise
# files AFTER every keyed expansion). OURS (NOT_KOMETA): the mechanism that
# reproduces Kometa's measured hand-order for the one shipped case that needs
# it (award winners ahead of their ceremony years, ``'!130_Oscars !1'``). Four
# digits wide to match ``year_order``'s width, and "0000" sorts before every
# key that width can produce.
LEADING_ORDER = "0000"


@dataclass(frozen=True)
class SeparatorSpec:
    """One group's blank divider: everything needed to reconcile it.

    Frozen and value-only, so the reconciler takes a description rather than a
    module constant -- which is the whole of "generalise the one into N".
    """

    group: str
    title: str
    summary: str
    sort_title: str
    poster_key: str | None


def effective_order(config) -> tuple[str, ...]:
    """The canonical order, reordered by ``collections.group_order``.

    A partial list is allowed and is the expected use: the groups it names lead,
    in the order it names them, and every group it does not name follows in the
    canonical order. That keeps "put my own collections at the top" a one-line
    setting instead of a ten-name permutation an operator has to keep in sync
    with a table they cannot see.

    Section numbers derive from POSITION, so reordering renumbers -- which is a
    one-off re-write of every managed collection's sort title, exactly like the
    first pass after this feature ships. ``deploy/README.md`` says so plainly,
    in the ``group_order`` entry.
    """
    named = tuple(getattr(config.collections, "group_order", None) or ())
    return named + tuple(group for group in CANONICAL_ORDER if group not in named)


def section_number(group: str, order: tuple[str, ...]) -> str:
    """``"010"`` … ``"100"`` -- the group's 1-based position times ten.

    Spaced tens rather than 1..10 so a group inserted later can take a number
    between two shipped ones without renumbering the collections either side.
    """
    return "%03d" % ((order.index(group) + 1) * 10)


def sort_prefix(group: str, order: tuple[str, ...]) -> str:
    """``"!030_"`` -- what every member of the group's sort title starts with."""
    return "!%s_" % section_number(group, order)


def separator_title(group: str) -> str:
    if group == OPERATOR_GROUP:
        return _OPERATOR_TITLE
    return "%s Collections" % _KEY_NAMES[group]


def separator_summary(group: str) -> str:
    return "Section separator for %s." % separator_title(group)


def separator_sort_title(group: str, order: tuple[str, ...]) -> str:
    """The separator's own sort title: the members' prefix plus a second ``!``.

    That extra bang is the whole trick -- it sorts before every member of the
    same section, so the blank collection floats to the top of its block and
    reads as the block's heading.
    """
    return "%s!%s" % (sort_prefix(group, order), separator_title(group))


def member_sort_title(prefix: str, title: str, order: str | None = None) -> str:
    """``"!030_13_Age 13+ Movies"``, or ``"!100_Hand Picked"`` unordered.

    The ordering key occupies the slot Kometa's own template leaves empty (see
    the module docstring's second citation), separated by the same ``_``.
    """
    if not order:
        return prefix + title
    return "%s%s_%s" % (prefix, order, title)


def age_order(bucket_key: str) -> str:
    """A Common Sense bucket's ordering key: the bare age, zero-padded.

    ``buckets.derive_buckets`` keys the age buckets by the age itself (``"1"``
    … ``"18"``) and the catch-all by ``"other"``, so this is the whole rule:
    two digits for an age, the leftovers sentinel for anything else. Padding is
    what makes ``Age 2+`` file before ``Age 13+`` without depending on whether
    Plex collates a digit run numerically -- which Task 1 measured it doing for
    LEADING runs and left unverified mid-string, and which is exactly the
    uncertainty an explicit key removes.

    Two digits wide, matching ``LEFTOVERS_ORDER``: an age of 100 or more would
    overflow that width and break the comparison this relies on, but
    ``derive_buckets``'s current keys (1..18) never approach it.
    """
    if not bucket_key.isdigit():
        return LEFTOVERS_ORDER
    return "%02d" % int(bucket_key)


def year_order(year: int) -> str:
    """An award year's ordering key, inverted so the newest ceremony leads.

    Kometa hand-orders its ceremony collections newest-first
    (``'!130_Oscars !1'``, measured); a plain year would reverse that, which is
    the one regression Task 1 measured rather than inferred. Subtracting from a
    four-digit ceiling keeps the key a fixed width, so string order and year
    order stay the same relation for every ceremony in the dataset.
    """
    return "%04d" % (_YEAR_CEILING - year)


def builtin_group(builder: str) -> str | None:
    """The group a shipped family's builder belongs to, or None.

    The suffix rule covers all sixteen ceremonies at once. Every
    ``AwardEvent.years_builder`` is spelled ``<event>_award_years``
    (``builders/imdb_award.py:208-544``), and a test walks ``EVENTS`` to prove
    this answers "awards" for each of them -- so the registry stays the single
    source of ceremony names and this module holds none of them.
    """
    if builder in _BUILTIN_GROUPS:
        return _BUILTIN_GROUPS[builder]
    if builder.endswith(_AWARD_YEARS_SUFFIX):
        return "awards"
    return None


def definition_order(definition, library_type: str) -> str | None:
    """The ordering key a shipped family gives this whole definition, or None.

    Three families have one at definition level. Award winners (``imdb_award``)
    take ``LEADING_ORDER``, so they lead their own family's ceremony-year
    expansions instead of filing after them (Important 1 -- a group mixing an
    unkeyed parent with a keyed expansion otherwise sorts the parent last,
    the reverse of Kometa's measured hand-order). A CEREMONY YEAR takes
    ``year_order`` of the year it names: it reaches the engine as an ordinary
    definition of exactly one collection (``imdb_award.expand`` returns one per
    ceremony, carrying ``params={"year": ...}``), so by the time anything asks,
    its key is decided the same way a chart's is. The PLACEHOLDER above it names
    no year and gets none, which is right -- it never becomes a collection.
    Charts take a position in the chart inventory, and that is not an omission
    on their part either: a chart definition is one collection, so its ordering
    key is decided the moment the definition and the library type are in hand.
    The remaining ordered family -- Common Sense buckets -- expands INSIDE its
    reconciler into collections no definition ever names -- an age bucket per
    rating -- so its key cannot be decided here and is ``age_order``'s, applied
    per bucket by ``reconcile_content_ratings``. Everything else has no natural
    order at all and takes the plain ``!<NNN>_<title>`` shape.

    The chart inventory is read rather than copied, and read locally: it lives
    behind ``builders/__init__``, which imports the reconciler this module is
    imported by. A chart the library does not build has no position in that
    library's inventory and therefore no key.
    """
    if definition.builder == "imdb_award":
        return LEADING_ORDER
    if definition.builder.endswith(_AWARD_YEARS_SUFFIX):
        # The year is a STRING on the way through ("2026" -- the ceremony keys
        # of the dataset it comes from are, and ``ImdbAwardYearParams.year`` is
        # declared ``str`` to match), and ``params`` here is the raw dict, which
        # that model has not seen. Anything not a plain number has no place on
        # the scale ``year_order`` inverts and takes no key at all, rather than
        # raising inside a function the config validator reaches.
        year = str(definition.params.get("year", ""))
        return year_order(int(year)) if year.isdigit() else None
    if definition.builder != "imdb_chart":
        return None

    from autoposter.collections.builders.imdb_chart import CHARTS_FOR

    charts = CHARTS_FOR.get(library_type, [])
    chart = definition.params.get("chart")
    if chart not in charts:
        return None
    return "%02d" % charts.index(chart)


def preset_groups(config, library_type: str) -> dict[str, str]:
    """``{collection title: group}`` for the catalog presets switched on.

    The catalog already knows each preset's category, and a preset knows the
    definitions it expands to, so the index is the join of the two -- no new
    table, and a preset moved to another category moves its collections with it
    on the next pass rather than after a migration.

    An unknown key expands to nothing rather than raising, the same posture
    ``CollectionsConfig._presets_must_be_known_and_ready`` documents: a
    ``KeyError`` from inside a validator is a 500 on a settings save, and the
    refusal for a bad key belongs to that validator alone.

    Imported locally -- ``catalog`` imports ``config.schema``, and this module is
    imported by every reconciler; see the module docstring.
    """
    from autoposter.collections.catalog import BY_KEY

    index: dict[str, str] = {}
    for key in (getattr(config.collections, "presets", None) or []):
        preset = BY_KEY.get(key)
        if preset is None:
            continue
        for definition in preset.definitions(library_type):
            index[definition.title] = preset.category
    return index


def group_for(definition, index: dict[str, str], parent: str | None = None) -> str:
    """Which block this definition's collections belong to.

    Three sources, most specific first. A preset's own category wins, because
    the catalog said so explicitly. Then the builder table, which is what the
    shipped families are recognised by -- they come from ``sources.py`` and
    carry no preset key at all. Everything else is the operator's, and the
    operator group is last in the canonical order for that reason.

    ``parent`` is the PLACEHOLDER's group, threaded in by the engine for
    expanded units and by nothing else. A ``facts_family`` unit carries the
    member's title AND builder (``builders/facts_family.py:434-443``), so both
    lookups miss and -- before this parameter existed -- 65 live franchises
    fell through to ``!100_``, unheaded, while ``engine._separators`` (which
    resolves from placeholders) never activated the operator divider above
    them. The parent only replaces the FALL-THROUGH: a unit whose own title or
    builder resolves keeps its own answer, which is the C3 expansion trap's
    rule unchanged (a ceremony year is an award because it says so, not
    because its placeholder does).
    """
    group = index.get(definition.title)
    if group is not None:
        return group
    builtin = builtin_group(definition.builder)
    if builtin is not None:
        return builtin
    return parent or OPERATOR_GROUP


def sort_prefix_for(
    definition, index: dict[str, str], order: tuple[str, ...],
    parent: str | None = None,
) -> str:
    return sort_prefix(group_for(definition, index, parent), order)


class _DerivedSortTitle:
    """A definition, read through, with the group's sort title substituted.

    This is how the derived value gets in OUT OF BAND, and both halves of that
    matter. It is not written onto the definition, because ``model_copy`` marks
    the field in ``model_fields_set`` and ``engine._completed`` reads that as
    "the operator set this" -- a placeholder so marked would hand one sort title
    to every collection a family expands into, instead of each getting its own.
    And it is not applied inside ``reconcile._apply_sort_title`` either, because
    that function runs AFTER the hash short-circuit: a value that appeared only
    there would never change the stored hash, so an unchanged pass would skip
    the collection and the sort title would never be written at all. Wrapping
    the settings object puts the value in front of ``lists._settings_parts``,
    which is the one place that decides whether a pass has work to do.

    Read-only, and a ``__getattr__`` proxy rather than a true stand-in: every
    attribute but ``sort_title`` reads through to the definition, but
    ``isinstance(view, CollectionDefinition)``, ``model_dump()`` and
    ``model_copy()`` all see straight through to the wrapped object and would
    return it without the derived sort title.
    """

    __slots__ = ("_definition", "sort_title")

    def __init__(self, definition, sort_title: str):
        object.__setattr__(self, "_definition", definition)
        object.__setattr__(self, "sort_title", sort_title)

    def __getattr__(self, name: str):
        return getattr(self._definition, name)

    def __repr__(self) -> str:
        return "<derived sort_title=%r on %r>" % (
            self.sort_title, getattr(self._definition, "title", None),
        )


def with_derived_sort_title(
    settings, prefix: str | None, title: str, order: str | None = None
):
    """``settings``, with the group-derived sort title filled in if it is wanted.

    ``order`` is the family's per-member ordering key, or None where the family
    supplies none -- see ``member_sort_title``.

    Returns the object unchanged in the three cases where nothing is derived:
    there is no definition, there is no group prefix, or the definition already
    names a sort title. The last is the precedence rule, and it is the same one
    ``engine._summary_for`` applies to summaries: an explicit choice in the
    config wins over anything derived, because a derived value that silently
    overrode it would be a setting that reads as applied and is not.
    """
    if settings is None or prefix is None:
        return settings
    if getattr(settings, "sort_title", None) is not None:
        return settings
    return _DerivedSortTitle(settings, member_sort_title(prefix, title, order))


def separator_groups(definitions, library_type: str, config) -> list[str]:
    """The groups these definitions put collections in, in tab order.

    Empty when ``collections.separators`` is off -- that switch now governs
    every group's divider, not just the Common Sense one.

    Gated-off definitions count, deliberately, for ``definition_titles``'
    reason: a family outside its schedule this pass still has collections in
    the library, and a heading that vanished and came back would be a create
    and a delete every other pass.
    """
    if not getattr(config.collections, "separators", False):
        return []
    index = preset_groups(config, library_type)
    present = {group_for(definition, index) for definition in definitions}
    return [group for group in effective_order(config) if group in present]


def separator_specs(definitions, library_type: str, config) -> list[SeparatorSpec]:
    order = effective_order(config)
    return [
        SeparatorSpec(
            group=group,
            title=separator_title(group),
            summary=separator_summary(group),
            sort_title=separator_sort_title(group, order),
            poster_key=separator_poster_key(group, config),
        )
        for group in separator_groups(definitions, library_type, config)
    ]


def separator_titles(definitions, library_type: str, config) -> set[str]:
    """Every separator title these definitions imply.

    ``engine.definition_titles`` folds this in the way it already folds
    ``cs_bucket.titles()``: a title nothing enumerates is a title the delete
    sweep reads as an orphan, and the leftovers report reads as a prior tool's.
    """
    return {
        separator_title(group)
        for group in separator_groups(definitions, library_type, config)
    }


def group_listing(config) -> list[dict]:
    """Every group as the catalog endpoint serves it, in effective order.

    ``{key, title, section, position}`` per group: the config-legal name, the
    divider's display title, the ``!NNN`` section number, and the group's index
    under the RUNNING config's ``group_order`` — so ``position`` equals the
    array index, and a UI that renders the array in order is rendering the tab.

    This is the group-order panel's enumeration source, and the reason it
    exists is the reason ``CANONICAL_ORDER`` is written out above: the keys
    live in exactly one place. A frontend holding its own copy of the ten
    names would be the drift this module's other tables refuse.
    """
    order = effective_order(config)
    return [
        {
            "key": group,
            "title": separator_title(group),
            "section": section_number(group, order),
            "position": order.index(group),
        }
        for group in order
    ]
