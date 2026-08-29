"""The DB-backed enumeration seam (adjudication C6).

The ten shipped dynamic types enumerate through Plex's ``listFilterChoices``
(``builders/plex_search.LibraryTagResolver``). These values are not in Plex at
all -- ``origin_country`` is TMDb's field and Plex has no equivalent, which is
the whole of roadmap row 189 -- so they are enumerated from the facts this
service already stores.

The missing-value rule row 156 predicted would "do most of the work" is the
shape of every query here: an item with no ``item_facts`` row, or with a NULL /
empty value in the column, is simply ABSENT from the enumeration. It is never a
bucket, never a zero and never an error.
"""
from sqlalchemy import func, update

from autoposter.collections.facts_enumeration import (
    FACTS_FIELDS,
    coverage,
    enumerate_values,
    items_with_values,
)
from autoposter.db.models import ItemFacts, MediaItem


async def _item(session, rating_key, *, library="Movies", kind="movie",
                title="X", **facts):
    item = MediaItem(rating_key=rating_key, library=library, kind=kind, title=title)
    session.add(item)
    await session.flush()
    if facts:
        session.add(ItemFacts(item_id=item.id, **facts))
        await session.flush()
    return item


async def test_a_scalar_column_enumerates_its_distinct_values(session):
    await _item(session, "a", tmdb_original_language="en")
    await _item(session, "b", tmdb_original_language="en")
    await _item(session, "c", tmdb_original_language="fi")
    values = await enumerate_values(
        session, FACTS_FIELDS["original_language"],
        library="Movies", library_type="Movie",
    )
    assert values == [("en", 2), ("fi", 1)]


async def test_an_array_column_enumerates_each_element(session):
    """A co-production carries several origin countries and belongs in each
    bucket -- which is why the column is a JSONB array and the query unnests
    it rather than grouping on the whole array."""
    await _item(session, "a", tmdb_origin_country=["US", "GB"])
    await _item(session, "b", tmdb_origin_country=["US"])
    values = await enumerate_values(
        session, FACTS_FIELDS["origin_country"],
        library="Movies", library_type="Movie",
    )
    assert values == [("US", 2), ("GB", 1)]


async def test_an_item_with_no_facts_row_is_simply_absent(session):
    """The missing-value rule. Not a bucket, not a zero, not an error -- the
    enumeration reflects what the facts pipeline has VISITED, and the pack
    descriptions state that convergence story rather than hiding it."""
    await _item(session, "seen", tmdb_original_language="en")
    await _item(session, "never-fetched")
    values = await enumerate_values(
        session, FACTS_FIELDS["original_language"],
        library="Movies", library_type="Movie",
    )
    assert values == [("en", 1)]


async def test_a_null_or_empty_value_is_absent_too(session):
    await _item(session, "null", tmdb_original_language=None)
    await _item(session, "empty-array", tmdb_origin_country=[])
    assert await enumerate_values(
        session, FACTS_FIELDS["original_language"],
        library="Movies", library_type="Movie",
    ) == []
    assert await enumerate_values(
        session, FACTS_FIELDS["origin_country"],
        library="Movies", library_type="Movie",
    ) == []


async def test_another_librarys_items_are_not_enumerated(session):
    """``media_items.library`` is the Plex section title (``plex/client.py:265``)
    and so is the engine's ``library`` -- a second Movie library must not
    contribute values to this one's family."""
    await _item(session, "here", tmdb_original_language="en")
    await _item(session, "there", library="4K Movies", tmdb_original_language="fi")
    values = await enumerate_values(
        session, FACTS_FIELDS["original_language"],
        library="Movies", library_type="Movie",
    )
    assert values == [("en", 1)]


async def test_a_show_library_enumerates_shows_and_not_episodes(session):
    """Episodes carry a rating and nothing else -- ``gather_facts`` gives an
    episode only the two ratings and a season nothing at all -- so a family on
    a Show library is a family of SHOWS."""
    await _item(session, "s", library="TV", kind="show", tmdb_original_language="ja")
    await _item(session, "e", library="TV", kind="episode", tmdb_original_language="ja")
    values = await enumerate_values(
        session, FACTS_FIELDS["original_language"],
        library="TV", library_type="Show",
    )
    assert values == [("ja", 1)]


async def test_the_franchise_field_enumerates_ids_as_strings(session):
    """``derive_keys`` matches ``include``/``exclude``/``addons`` as strings
    (its ``_strlist``), so the key column is a string here too -- an operator's
    ``exclude: [1241]`` is YAML integers and would otherwise never meet it."""
    await _item(session, "a", tmdb_collection_id=1241)
    await _item(session, "b", tmdb_collection_id=1241)
    await _item(session, "c", tmdb_collection_id=87096)
    values = await enumerate_values(
        session, FACTS_FIELDS["tmdb_collection"],
        library="Movies", library_type="Movie",
    )
    assert values == [("1241", 2), ("87096", 1)]


async def test_the_franchise_field_membership_binds_ids_as_integers(session):
    """``tmdb_collection_id`` is an Integer column (``db/models.py:237``), but
    every value ``items_with_values`` receives is a string -- ``enumerate_values``
    stringifies the id, and ``FactsValueParams.coerce_numbers_to_str`` does the
    same for a config value. SQLAlchemy infers a bind's Postgres type from the
    Python value it is given, not from the column being compared, so passing
    the string straight through renders ``IN ($1::VARCHAR)`` against an Integer
    column and Postgres refuses it: ``operator does not exist: integer =
    character varying``. ``FactsField.value_type`` is what coerces it back."""
    await _item(session, "a", tmdb_collection_id=1241)
    await _item(session, "b", tmdb_collection_id=87096)
    keys = await items_with_values(
        session, FACTS_FIELDS["tmdb_collection"], ["1241"],
        library="Movies", library_type="Movie",
    )
    assert keys == ["a"]


async def test_the_franchise_field_round_trips_through_enumerate_and_membership(session):
    """The natural pairing the field's own `note` describes: an id enumerated
    as a string has to be usable as a membership value on the same field --
    exactly the query `facts_value` runs when it expands a franchise family."""
    await _item(session, "a", tmdb_collection_id=1241)
    await _item(session, "b", tmdb_collection_id=1241)
    await _item(session, "c", tmdb_collection_id=87096)
    values = await enumerate_values(
        session, FACTS_FIELDS["tmdb_collection"],
        library="Movies", library_type="Movie",
    )
    ids = [value for value, _count in values]
    keys = await items_with_values(
        session, FACTS_FIELDS["tmdb_collection"], ids,
        library="Movies", library_type="Movie",
    )
    assert keys == ["a", "b", "c"]


async def test_membership_is_every_item_carrying_any_of_the_values(session):
    """``DynamicKey.values`` is the key plus every addon member the library
    carries, so membership is an OR over the list -- the same ``any:`` base the
    smart families emit."""
    await _item(session, "us", tmdb_origin_country=["US"], title="A")
    await _item(session, "gb", tmdb_origin_country=["GB"], title="B")
    await _item(session, "fi", tmdb_origin_country=["FI"], title="C")
    keys = await items_with_values(
        session, FACTS_FIELDS["origin_country"], ["US", "GB"],
        library="Movies", library_type="Movie",
    )
    assert keys == ["us", "gb"]


async def test_membership_of_no_values_is_no_items(session):
    """A filter with no terms matching the whole library is the failure
    ``builders/dynamic.py`` refuses by name; here it is refused by returning
    nothing, and the builder turns that into its own refusal."""
    await _item(session, "us", tmdb_origin_country=["US"])
    assert await items_with_values(
        session, FACTS_FIELDS["origin_country"], [],
        library="Movies", library_type="Movie",
    ) == []


async def test_coverage_counts_attempts_and_not_rows(session):
    """The convergence story, measurable. ``facts_attempted_at`` is stamped by
    every ``persist_facts`` call (C4), so an item TMDb had nothing for counts
    as VISITED -- which is exactly the honesty the pack descriptions need."""
    seen = await _item(session, "a", tmdb_original_language="en")
    looked = await _item(session, "b")
    await _item(session, "c")
    await session.execute(
        update(MediaItem)
        .where(MediaItem.id.in_([seen.id, looked.id]))
        .values(facts_attempted_at=func.now())
    )
    assert await coverage(session, library="Movies", library_type="Movie") == (2, 3)
