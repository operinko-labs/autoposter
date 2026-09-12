"""``facts_value``: one collection whose membership is a facts column.

The single-purpose half of the facts families -- one collection, one field, one
or more values -- and the builder each expanded unit of an ``origin_country``
or ``original_language`` family runs. Useful on its own: an operator who wants
exactly "the Finnish films" writes this and nothing else.
"""
import pytest
from sqlalchemy import func, select, text

from autoposter.collections.builders import facts_value as facts_value_module
from autoposter.collections.builders.base import BuilderContext
from autoposter.collections.builders.facts_value import (
    FactsValueBuilder,
    FactsValueParams,
)
from autoposter.db.models import ItemFacts, MediaItem

from conftest import seed_media_item


async def _item(session, rating_key, *, library="Movies", kind="movie", title="X",
                **facts):
    item = await seed_media_item(session, rating_key, library=library, kind=kind, title=title)
    if facts:
        session.add(ItemFacts(item_id=item.id, **facts))
        await session.flush()
    return item


async def test_it_builds_the_items_carrying_the_value(session):
    await _item(session, "10", tmdb_origin_country=["FI"], title="A")
    await _item(session, "11", tmdb_origin_country=["US"], title="B")
    result = await FactsValueBuilder().build(BuilderContext(
        library="Movies", library_type="Movie", session=session,
        config={"field": "origin_country", "values": ["FI"]},
    ))
    assert result.ids == [("plex", "10")]


async def test_a_rating_key_is_the_identity_and_needs_no_lookup(session):
    """``plex`` is a namespace exactly because a rating key IS an owned item's
    identity (``collections/ids.py:13-16``). Producing tmdb ids here would ask
    the resolver to look up rows this query already has -- and would silently
    drop every item whose ``tmdb_id`` is NULL, which is a plausible wrong
    membership rather than a visible failure."""
    await _item(session, "42", tmdb_original_language="fi", title="A")
    result = await FactsValueBuilder().build(BuilderContext(
        library="Movies", library_type="Movie", session=session,
        config={"field": "original_language", "values": ["fi"]},
    ))
    assert result.ids == [("plex", "42")]


async def test_several_values_are_an_or(session):
    await _item(session, "1", tmdb_origin_country=["FI"], title="A")
    await _item(session, "2", tmdb_origin_country=["SE"], title="B")
    await _item(session, "3", tmdb_origin_country=["US"], title="C")
    result = await FactsValueBuilder().build(BuilderContext(
        library="Movies", library_type="Movie", session=session,
        config={"field": "origin_country", "values": ["FI", "SE"]},
    ))
    assert result.ids == [("plex", "1"), ("plex", "2")]


async def test_a_field_this_service_does_not_enumerate_is_refused_at_config_load():
    with pytest.raises(ValueError) as caught:
        FactsValueParams.model_validate({"field": "nonsense", "values": ["x"]})
    assert "origin_country" in str(caught.value)


async def test_a_field_the_library_type_cannot_carry_is_refused(session):
    """A ``tmdb_collection`` family on a Show library would match nothing at
    all, and "matched nothing" looks exactly like a correct collection of
    titles the library does not own -- ``require_library_type``'s whole
    reason."""
    from autoposter.collections.builders.base import LibraryTypeMismatch

    with pytest.raises(LibraryTypeMismatch):
        await FactsValueBuilder().build(BuilderContext(
            library="TV", library_type="Show", session=session,
            config={"field": "tmdb_collection", "values": ["1241"]},
        ))


async def test_no_session_raises_rather_than_building_an_empty_collection():
    """``build`` RAISES on failure -- returning an empty list would be read one
    layer down as "make no changes" (``builders/base.py``'s first rule), so a
    context with no session must not look like a library with no Finnish
    films."""
    with pytest.raises(ValueError):
        await FactsValueBuilder().build(BuilderContext(
            library="Movies", library_type="Movie",
            config={"field": "origin_country", "values": ["FI"]},
        ))


async def test_an_empty_values_list_is_refused_at_config_load():
    """A membership query with no terms matches the whole library."""
    with pytest.raises(ValueError):
        FactsValueParams.model_validate({"field": "origin_country", "values": []})


async def test_a_failing_query_leaves_the_session_usable_for_what_runs_next(
    session, monkeypatch,
):
    """``engine.py:447`` contains every builder exception and substitutes an
    empty result, on the invariant that a dead source does not stop the pass.
    That invariant is false unless a failed read here leaves the shared
    `AsyncSession`'s transaction usable for whatever the pass runs next --
    without the `begin_nested()` savepoint around the read, a real DB error
    (a transient one, or Important 1's type mismatch before its own fix)
    poisons the transaction and every later statement in the pass raises too."""
    async def _broken_query(*args, **kwargs):
        # A genuine Postgres-level error, not a Python one, so it poisons the
        # transaction the same way a live failure would.
        await session.execute(text("SELECT * FROM this_table_does_not_exist"))
        return []  # pragma: no cover - the execute above always raises first

    monkeypatch.setattr(facts_value_module, "items_with_values", _broken_query)

    with pytest.raises(Exception):
        await FactsValueBuilder().build(BuilderContext(
            library="Movies", library_type="Movie", session=session,
            config={"field": "origin_country", "values": ["FI"]},
        ))

    # The pass continues: the next statement on the same session -- standing
    # in for the next definition's query, or the engine's own bookkeeping --
    # must succeed rather than raise the poisoned-transaction cascade.
    result = await session.execute(select(func.count()).select_from(MediaItem))
    assert result.scalar_one() == 0
