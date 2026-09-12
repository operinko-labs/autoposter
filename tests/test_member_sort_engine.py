"""Roadmap row 269 through ``run_library``: positions follow the builder's
order, the first definition wins a shared member, sort-only creates nothing,
a departed member is released, and an unsettled definition releases nothing.

The fakes are ``tests/test_builder_engine.py``'s own; ``registry_entry`` is
that file's fixture, redefined here because an imported fixture shadows
every test's parameter of the same name (ruff F811).
"""
import logging

import httpx
import pytest
from sqlalchemy import select

from autoposter.collections.builders import REGISTRY, BuilderContext, BuilderResult, register
from autoposter.collections.engine import run_library
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ItemSortPosition, ManagedCollection
from autoposter.db.refs import native_ids

from conftest import seed_media_item
from test_builder_engine import FakeSection, _config


@pytest.fixture
def registry_entry():
    """Register a builder for one test and take it back out again."""
    registered: list[str] = []

    def add(builder):
        register(builder)
        registered.append(builder.type_name)
        return builder

    yield add

    for type_name in registered:
        del REGISTRY[type_name]


class _Listing:
    """A builder returning whatever ids the test gave it, mutable between passes."""

    def __init__(self, type_name, ids):
        self.type_name = type_name
        self._ids = ids

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        return BuilderResult(ids=list(self._ids))


class _Dead:
    type_name = "test_dead_source"

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        raise httpx.HTTPError("down")


async def _seed_items(session, keys):
    """``media_items`` rows plus their Plex refs for the fake section's items,
    one per rating key -- ``conftest.seed_media_item`` writes the pair."""
    for key in keys:
        await seed_media_item(session, key, library="Movies", kind="movie", title=key)


async def _rows(session):
    """``{plex id: (definition_title, base, position, total, released)}``."""
    rows = (await session.execute(select(ItemSortPosition))).scalars().all()
    keys = await native_ids(session, [row.item_id for row in rows], "plex")
    return {
        keys[row.item_id]: (
            row.definition_title, row.base, row.position, row.total,
            row.released_at is not None,
        )
        for row in rows
    }


def _definition(title, builder, **extra):
    return CollectionDefinition(title=title, builder=builder, member_sort=True, **extra)


def _section(*numbers):
    return FakeSection([(f"m{n}", [f"imdb://tt{n}"]) for n in numbers])


@pytest.mark.asyncio
async def test_positions_follow_the_builders_order_after_the_limit(session, registry_entry):
    """The order is the SOURCE's, verbatim: 3, 1, 2 stays 3, 1, 2, and the
    limit counts members rather than re-sorting them."""
    registry_entry(_Listing(
        "test_bond", [("imdb", "tt3"), ("imdb", "tt1"), ("imdb", "tt2"), ("imdb", "tt4")],
    ))
    section = _section(1, 2, 3, 4)
    await _seed_items(session, ["m1", "m2", "m3", "m4"])

    run = await run_library(
        session, section, "Movies", "Movie",
        [_definition("The Bond Collection", "test_bond", limit=3)], _config(),
    )

    assert await _rows(session) == {
        "m3": ("The Bond Collection", "Bond", 1, 3, False),
        "m1": ("The Bond Collection", "Bond", 2, 3, False),
        "m2": ("The Bond Collection", "Bond", 3, 3, False),
    }
    assert {payload["refs"]["plex"] for payload, _ in run.reprocess} == {"m1", "m2", "m3"}
    assert all(payload["kind"] == "movie" for payload, _ in run.reprocess)


@pytest.mark.asyncio
async def test_sort_only_creates_no_collection_and_no_managed_row(session, registry_entry):
    registry_entry(_Listing("test_bond", [("imdb", "tt1")]))
    section = _section(1)
    await _seed_items(session, ["m1"])

    run = await run_library(
        session, section, "Movies", "Movie",
        [_definition("Bond", "test_bond", create_collection=False)], _config(),
    )

    assert "Bond" not in section._existing
    assert (await session.execute(select(ManagedCollection))).scalars().all() == []
    assert (
        "'Bond': sort-only; recorded 1 member position(s), no collection created"
        in run.actions
    )
    assert await _rows(session) == {"m1": ("Bond", "Bond", 1, 1, False)}


@pytest.mark.asyncio
async def test_the_first_definition_in_order_owns_a_shared_member(
    session, registry_entry, caplog
):
    registry_entry(_Listing("test_a", [("imdb", "tt1"), ("imdb", "tt2")]))
    registry_entry(_Listing("test_b", [("imdb", "tt2"), ("imdb", "tt3")]))
    section = _section(1, 2, 3)
    await _seed_items(session, ["m1", "m2", "m3"])

    with caplog.at_level(logging.INFO, logger="autoposter.collections.member_sort"):
        await run_library(
            session, section, "Movies", "Movie",
            [_definition("A", "test_a"), _definition("B", "test_b")], _config(),
        )

    rows = await _rows(session)
    assert rows["m2"] == ("A", "A", 2, 2, False)
    assert rows["m3"] == ("B", "B", 2, 2, False)
    lines = [r.getMessage() for r in caplog.records if "already owns" in r.getMessage()]
    assert lines == [
        "Movies: 'B' lists 1 member(s) 'A' already owns; the first definition wins"
    ]


@pytest.mark.asyncio
async def test_a_departed_member_is_released_and_a_returning_one_re_held(
    session, registry_entry
):
    listing = _Listing("test_bond", [("imdb", "tt1"), ("imdb", "tt2")])
    registry_entry(listing)
    section = _section(1, 2)
    await _seed_items(session, ["m1", "m2"])
    definitions = [_definition("Bond", "test_bond")]

    await run_library(session, section, "Movies", "Movie", definitions, _config())
    listing._ids = [("imdb", "tt1")]
    run = await run_library(session, section, "Movies", "Movie", definitions, _config())

    rows = await _rows(session)
    assert rows["m1"] == ("Bond", "Bond", 1, 1, False)
    assert rows["m2"][4] is True, "released, not deleted"
    assert {payload["refs"]["plex"] for payload, _ in run.reprocess} == {"m1", "m2"}

    listing._ids = [("imdb", "tt1"), ("imdb", "tt2")]
    await run_library(session, section, "Movies", "Movie", definitions, _config())
    assert (await _rows(session))["m2"] == ("Bond", "Bond", 2, 2, False)


@pytest.mark.asyncio
async def test_dropping_member_sort_releases_every_member(session, registry_entry):
    registry_entry(_Listing("test_bond", [("imdb", "tt1")]))
    section = _section(1)
    await _seed_items(session, ["m1"])

    await run_library(
        session, section, "Movies", "Movie", [_definition("Bond", "test_bond")], _config(),
    )
    await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Bond", builder="test_bond")], _config(),
    )

    assert (await _rows(session))["m1"][4] is True


@pytest.mark.asyncio
async def test_a_failed_source_releases_nothing(session, registry_entry):
    """The settled rule: an outage must not strip a franchise's sort titles."""
    registry_entry(_Listing("test_bond", [("imdb", "tt1")]))
    registry_entry(_Dead())
    section = _section(1)
    await _seed_items(session, ["m1"])

    await run_library(
        session, section, "Movies", "Movie", [_definition("Bond", "test_bond")], _config(),
    )
    run = await run_library(
        session, section, "Movies", "Movie",
        [_definition("Bond", "test_dead_source")], _config(),
    )

    assert (await _rows(session))["m1"] == ("Bond", "Bond", 1, 1, False)
    assert run.reprocess == []


@pytest.mark.asyncio
async def test_an_unchanged_pass_enqueues_nothing(session, registry_entry):
    registry_entry(_Listing("test_bond", [("imdb", "tt1")]))
    section = _section(1)
    await _seed_items(session, ["m1"])
    definitions = [_definition("Bond", "test_bond")]

    await run_library(session, section, "Movies", "Movie", definitions, _config())
    run = await run_library(session, section, "Movies", "Movie", definitions, _config())

    assert run.reprocess == []


@pytest.mark.asyncio
async def test_dry_run_records_nothing_and_says_what_it_would(session, registry_entry):
    registry_entry(_Listing("test_bond", [("imdb", "tt1")]))
    section = _section(1)
    await _seed_items(session, ["m1"])

    run = await run_library(
        session, section, "Movies", "Movie", [_definition("Bond", "test_bond")],
        _config(apply_to_plex=False),
    )

    assert await _rows(session) == {}
    assert run.reprocess == []
    assert "Movies: would record 1 sort position(s) and release 0" in run.actions


@pytest.mark.asyncio
async def test_a_member_with_no_media_row_is_skipped_and_counted(
    session, registry_entry, caplog
):
    registry_entry(_Listing("test_bond", [("imdb", "tt1"), ("imdb", "tt2")]))
    section = _section(1, 2)
    await _seed_items(session, ["m1"])

    with caplog.at_level(logging.INFO, logger="autoposter.collections.member_sort"):
        await run_library(
            session, section, "Movies", "Movie",
            [_definition("Bond", "test_bond")], _config(),
        )

    assert set(await _rows(session)) == {"m1"}
    assert any(
        "1 member(s) have no media_items row yet" in r.getMessage() for r in caplog.records
    )
