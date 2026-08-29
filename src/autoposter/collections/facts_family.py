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
            "pipeline already makes. The keys are ISO-3166-1 codes and the "
            "titles are those codes: no code->name table exists here or in "
            "Kometa's own pack files, which is the same absence roadmap row 190 "
            "records for the language families. A pack that wants readable "
            "names supplies `key_name_override`, and `region.yml`'s grouping "
            "table makes the question moot for the packs that ship -- their "
            "titles are the region names.",
        ),
        FactsFamilyType(
            "original_language", FACTS_FIELDS["original_language"], "facts_value",
            "value", _BARE_KEY_TITLE,
            "Roadmap row 189's second half, and the same shape as its sibling "
            "above in every respect: ISO-639-1 keys, titled from the code, and "
            "no name table to do better with. NOT the same thing as the shipped "
            "`audio_language` dynamic type, which enumerates the audio STREAMS "
            "a Plex item carries -- a dubbed film has several and one original "
            "language, and conflating them is the class of same-name-different-"
            "meaning bug row 156 exists to refuse.",
        ),
        FactsFamilyType(
            "tmdb_collection", FACTS_FIELDS["tmdb_collection"], "tmdb_collection",
            "id", _BARE_KEY_TITLE,
            "Roadmap row 192, keyed on the ID -- Task 1 §4's `FRANCHISE KEY: id` "
            "verdict, read off `movie/franchise.yml`'s own integer-keyed "
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
