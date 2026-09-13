"""Which facts-enumerated families this service ships, and how each is built.

The sibling of ``dynamic_types.py``, for the families whose values come from
this service's own database rather than from Plex. The two tables are separate
DELIBERATELY, and the reason is structural rather than stylistic:
``DYNAMIC_TYPES``' only consumer is ``builders/dynamic.DynamicBuilder``, which
is smart end to end -- it enumerates through ``listFilterChoices`` and builds
each key with ``parse_filters`` -> ``build_search_url`` ->
``reconcile_smart_collection``. A facts-enumerated type's MEMBERSHIP cannot be a
Plex smart filter at all, because Plex has no ``origin_country`` field; that
absence is roadmap row 189 in one sentence. A row in that table would therefore
be a row its builder cannot build, and worse, one
``DynamicParams._the_type_must_be_one_this_service_enumerates`` would advertise
to an operator as an option.

**What is shared instead, which is nearly everything that matters.**
``dynamic_keys.derive_keys`` decides which keys become collections and what each
one asks for, and ``dynamic_titles.family_titles`` names them. Both are pure and
both take the enumeration as DATA -- neither has ever known where the values
came from -- so this family shape reuses Kometa's key derivation and title
lifecycle unchanged, including the addon merges that make a synthetic 'Nordic'
bucket out of five country codes, the ``include``-applied-last whitelist, the
``other`` leftovers bucket, and the duplicate-title refusal.

**Three rows.** Kometa builds all three as PLAIN collections from a full-library
TMDb walk (``meta.py:36-37``, ``:971-993``, ``builder.py:360-374``), which is
what this service's facts pipeline already is.
"""
from dataclasses import dataclass

from autoposter.collections.facts_enumeration import FACTS_FIELDS, FactsField

__all__ = ["FACTS_FAMILY_TYPES", "FACTS_FAMILY_TYPE_NAMES", "FactsFamilyType"]


@dataclass(frozen=True)
class FactsFamilyType:
    """One row: a family an operator can write as ``params.type``.

    ``field`` is the ``FACTS_FIELDS`` entry the family enumerates, and it also
    supplies the library types the family can serve -- one authority, so a row
    cannot enumerate one field and build another.

    ``member_builder`` is the ``type_name`` each expanded unit is built by.
    Two answers ship: ``facts_value`` for a family whose members are "the items
    whose facts carry this value", and ``tmdb_collection`` for franchises,
    whose members are better answered by the franchise's own ``parts`` than by
    the items this service happens to have fetched.

    ``key_from`` is which half of the enumeration is the KEY that
    ``include``/``exclude``/``addons`` and the override tables are matched
    against -- ``"value"`` when the enumerated value is itself the key (a
    country code, a language code), and ``"id"`` for the franchise family,
    whose enumeration is ids and whose display name is fetched. It is the same
    distinction ``DynamicType.key_from`` draws, and it decides whether
    ``exclude: [1241]`` works or does nothing.
    """

    name: str
    field: FactsField
    member_builder: str
    key_from: str
    title_format: str
    note: str
    # How the enumeration's ISO codes meet the vendored ``iso_names`` tables,
    # if at all. ``None``: keys and titles are the enumerated values
    # themselves. ``"titles"``: only the TITLE half renders through the table
    # and the KEY stays the code (row 190 -- upstream's own grouping
    # vocabulary for languages is codes, so include/exclude/addons keep
    # speaking ISO, row 156's law untouched). ``"keys"``: BOTH halves become
    # the TMDb display name (row 196 -- upstream's country grouping
    # vocabulary is display names, 647 member strings and zero codes), and
    # the builder folds each name back to its code(s) before the membership
    # query. Either way a code the table cannot serve passes through as
    # itself: never invented.
    names: str | None = None


# The key's own name and nothing else -- the format Kometa gives `country`,
# `network` and the location packs. Shared by all three rows here because none
# of them has a better default: a pack that wants more says so with its own
# `title_format`, which is where every collision this catalog refuses is
# resolved (`tests/test_collection_catalog.py`'s format-collision test).
_BARE_KEY_TITLE = "<<key_name>>"


FACTS_FAMILY_TYPES: dict[str, FactsFamilyType] = {
    row.name: row
    for row in (
        FactsFamilyType(
            "origin_country", FACTS_FIELDS["origin_country"], "facts_value",
            "value", _BARE_KEY_TITLE,
            "Roadmap row 189's first half. Upstream builds this as a PLAIN "
            "collection from a full-library TMDb walk (meta.py:36-37, "
            "builder.py:360-374) and so does this, off the walk the facts "
            "pipeline already makes. The keys are ISO-3166-1 codes in the "
            "DATABASE and TMDb's own English names in the FAMILY -- row "
            "196's normalisation decision: each code maps UP through the "
            "vendored `/configuration/countries` table "
            "(`collections/iso_names.py`) so Kometa's name-keyed grouping "
            "tables (region.yml/continent.yml) apply verbatim, and the "
            "builder folds each name back DOWN to its code(s) for the "
            "`facts_value` query (row 156's law: the stored enumeration "
            "stays ISO). include/exclude/addons therefore speak the same "
            "display-name vocabulary as the `country` dynamic type beside "
            "this one. A code the table cannot name keys and titles as "
            "itself -- never invented, and visible.",
            names="keys",
        ),
        FactsFamilyType(
            "original_language", FACTS_FIELDS["original_language"], "facts_value",
            "value", _BARE_KEY_TITLE,
            "Roadmap row 189's second half. ISO-639-1 KEYS -- "
            "include/exclude/addons and both override tables match the CODE, "
            "row 156's law -- titled with TMDb's English language names "
            "through the vendored `/configuration/languages` table "
            "(`collections/iso_names.py`, row 190), with the code itself as "
            "the title for any code the table misses: upstream's own "
            "fallback branch, never an invention. NOT the same thing as the "
            "shipped `audio_language` dynamic type, which enumerates the "
            "audio STREAMS a Plex item carries -- a dubbed film has several "
            "and one original language, and conflating them is the class of "
            "same-name-different-meaning bug row 156 exists to refuse.",
            names="titles",
        ),
        FactsFamilyType(
            "tmdb_collection", FACTS_FIELDS["tmdb_collection"], "tmdb_collection",
            "id", _BARE_KEY_TITLE,
            "Roadmap row 192, keyed on the ID -- the franchise key is the id, "
            "read off `movie/franchise.yml`'s own integer-keyed "
            "`addons`/`title_override` tables. The single-collection "
            "`tmdb_collection` builder shipped in phase 8b and is what each unit "
            "here runs, so the membership is the franchise's own `parts` rather "
            "than the items this service has fetched -- which also means a "
            "franchise film the library owns but has never gathered facts for "
            "still joins its collection. The enumeration decides WHICH "
            "franchises the library is in; the builder decides what is in each. "
            "The display name is TMDb's own, fetched from the same "
            "`/collection/{id}` response the membership reads, and the "
            "' Collection' suffix upstream's pack strips is a `remove_suffix:` "
            "param rather than something this row does unasked. Movie-only, "
            "from the field: TMDb collections are movie franchises.",
        ),
    )
}

FACTS_FAMILY_TYPE_NAMES: tuple[str, ...] = tuple(FACTS_FAMILY_TYPES)
