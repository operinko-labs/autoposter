"""The collections pass keeps its Plex work off the shared event loop (perf C1).

Every test here drives a real entry point -- ``run_library``,
``reconcile_libraries``, a reconciler, a builder -- over the doubles in
``plex_offload_doubles``, which record the thread each Plex touch ran on. The
assertion is about the thread, not the clock: a touch recorded on the loop's own
thread is a stall however fast the double answered. One test per spec probe
also measures the loop with ``max_loop_lag`` against a double that really
blocks, and prints the number the lane records.
"""
import threading
from types import SimpleNamespace

import pytest

from autoposter.collections.builders import REGISTRY, BuilderResult, register
from autoposter.collections.engine import run_library
from autoposter.config.schema import CollectionDefinition

from plex_offload_doubles import (
    BlockingCollection,
    BlockingItem,
    BlockingSection,
    BlockingServer,
    CallLog,
    max_loop_lag,
)

LABEL = "autoposter"


def _config(**overrides):
    options = {
        "ownership_label": LABEL, "apply_to_plex": True, "adopt": False,
        "adopt_from": ["Kometa"], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "charts": False, "awards": False,
        "separators": False, "definitions": [], "presets": [], "libraries": ["Movies"],
        "delete_unconfigured": False, "max_deletes": 5, "enabled": True,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


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


class _Ids:
    """A builder returning the ids it was given."""

    def __init__(self, type_name, ids):
        self.type_name = type_name
        self._ids = ids

    async def build(self, ctx) -> BuilderResult:
        return BuilderResult(ids=list(self._ids))


def _library(log, *, all_delay=0.0, choices=()):
    """Three movies, ``101``-``103``, each claiming ``imdb://tt<key>``."""
    server = BlockingServer(log)
    items = [BlockingItem(log, key, ["imdb://tt%s" % key]) for key in ("101", "102", "103")]
    section = BlockingSection(
        log, server, items=items, choices=choices, all_delay=all_delay
    )
    return server, section, items


# --- C1 phase 1: the owned index and the collection listing ------------------


async def test_the_library_walk_and_the_listing_run_off_the_event_loop(
    session, registry_entry
):
    registry_entry(_Ids("test_offload_ids", [("imdb", "tt101"), ("imdb", "tt102")]))
    log = CallLog()
    _, section, _ = _library(log)
    loop_thread = threading.get_ident()

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Offloaded", builder="test_offload_ids")],
        _config(),
    )

    assert run.definitions[0].failed is False
    touched = log.names()
    assert "section.all" in touched and "section.collections" in touched
    on_loop = [
        name for name in log.on_thread(loop_thread)
        if name in ("section.all", "section.collections") or name.endswith(".guids")
    ]
    assert on_loop == []


async def test_a_one_second_library_walk_does_not_stall_the_event_loop(
    session, registry_entry
):
    """The spec's probe: ``all()`` blocks for 1 s, and a concurrent 50 ms
    sleep must not wake late by anything like that second.

    The spec's bound is 200 ms; the assertion allows 500 ms. The walk this
    fakes blocks for a full 1,000 ms, so 500 ms still fails a walk left on the
    loop by half a second, while a busy ``-n auto`` runner scheduling the probe
    late cannot flake it. The printed figure is what the lane records against
    the 200 ms bound, and the thread check in the test above is the primary
    proof."""
    registry_entry(_Ids("test_offload_slow", [("imdb", "tt101")]))
    log = CallLog()
    _, section, _ = _library(log, all_delay=1.0)

    run, lag = await max_loop_lag(run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Slow", builder="test_offload_slow")],
        _config(),
    ))

    print(
        "[pcs-measure] owned_index walk (section.all blocks 1 s): "
        "worst loop lag %.0f ms" % (lag * 1000)
    )
    assert run.definitions[0].failed is False
    assert lag < 0.5, "the library walk stalled the event loop for %.0f ms" % (lag * 1000)


# --- C1 phase 2: the list reconciler's claim and write phases -----------------


async def test_the_list_write_phase_runs_off_the_event_loop(session, registry_entry):
    """Claim, diff, add, remove, reorder, settings and item labels -- every one
    a Plex call, the reorder a reload plus a move per member, the item labels a
    write per member per tag -- all in the write phase's thread hop."""
    registry_entry(_Ids("test_offload_members", [("imdb", "tt102"), ("imdb", "tt101")]))
    log = CallLog()
    server, section, items = _library(log)
    by_key = {item.ratingKey: item for item in items}
    section.add(BlockingCollection(
        log, server, "Members", items=[by_key["101"], by_key["103"]], labels=[LABEL],
    ))
    loop_thread = threading.get_ident()

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(
            title="Members", builder="test_offload_members", item_label=["offloaded"],
        )],
        _config(),
    )

    [result] = [r for r in run.definitions if r.title == "Members"]
    assert (result.added, result.removed) == (1, 1)
    assert by_key["102"].labels_added == ["offloaded"]
    assert {
        "collection 'Members'.reload", "collection 'Members'.addItems",
        "collection 'Members'.removeItems", "collection 'Members'.moveItem",
        "item 102.addLabel",
    } <= set(log.names())
    assert log.on_thread(loop_thread) == []


async def test_a_created_list_collection_is_written_off_the_event_loop(
    session, registry_entry
):
    registry_entry(_Ids("test_offload_create", [("imdb", "tt101")]))
    log = CallLog()
    _, section, _ = _library(log)
    loop_thread = threading.get_ident()

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Fresh", builder="test_offload_create")],
        _config(),
    )

    assert "created 'Fresh' with 1 item(s)" in run.actions
    assert {"section.createCollection", "collection 'Fresh'.addLabel"} <= set(log.names())
    assert log.on_thread(loop_thread) == []


async def test_a_preview_reads_the_collection_off_the_event_loop(session, registry_entry):
    registry_entry(_Ids("test_offload_preview", [("imdb", "tt102")]))
    log = CallLog()
    server, section, items = _library(log)
    section.add(BlockingCollection(log, server, "Previewed", items=[items[0]], labels=[LABEL]))
    loop_thread = threading.get_ident()

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Previewed", builder="test_offload_preview")],
        _config(), dry_run=True, preview=True,
    )

    [result] = [r for r in run.definitions if r.title == "Previewed"]
    assert (result.adding, result.removing) == (1, 1)
    assert "collection 'Previewed'.items" in log.names()
    assert log.on_thread(loop_thread) == []


async def test_a_dry_run_on_an_existing_list_collection_stays_off_the_event_loop(
    session, registry_entry
):
    """A dry run reports "would update" for a collection that exists -- and
    must decide that without a truth test on the plexapi object, whose
    ``__len__`` is a membership fetch. An existing EMPTY collection is still
    an update, never a create."""
    registry_entry(_Ids("test_offload_dry", [("imdb", "tt101")]))
    log = CallLog()
    server, section, _ = _library(log)
    section.add(BlockingCollection(log, server, "Dry", labels=[LABEL]))
    loop_thread = threading.get_ident()

    run = await run_library(
        session, section, "Movies", "Movie",
        [CollectionDefinition(title="Dry", builder="test_offload_dry")],
        _config(), dry_run=True,
    )

    assert "would update 'Dry' with 1 item(s)" in run.actions
    assert log.on_thread(loop_thread) == []
