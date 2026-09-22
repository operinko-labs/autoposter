"""plex_collectionless: membership from the batched read, never a reload."""
import pytest

from autoposter.collections.builders.base import REGISTRY, BuilderContext
from autoposter.collections.builders.collectionless import (
    CollectionlessRefused, PlexCollectionlessBuilder,
)
from autoposter.collections.builders.plex_trivial import PlexLibraryUnavailable
from autoposter.collections.builders.sources_bundle import PlexSectionAccess, SourceClients

from plex_offload_doubles import returning


class FakeTag:
    def __init__(self, tag):
        self.tag = tag


class FakeItem:
    def __init__(self, rating_key, collections=()):
        self.ratingKey = rating_key
        self.genres = []
        self.labels = []
        self.collections = [FakeTag(c) for c in collections]
        self.media = []


class FakeSection:
    def __init__(self, items, answer_all=True):
        self._items = {int(i.ratingKey): i for i in items}
        self.answer_all = answer_all
        self.calls = []

    def fetchItems(self, ekey):
        self.calls.append(list(ekey))
        found = [self._items[k] for k in ekey if k in self._items]
        return found if self.answer_all else found[:-1]


def _context(items, params=None, section=None, run_cache=None):
    section = section or FakeSection(items)
    index = {"plex": {str(i.ratingKey): i for i in items}}
    return section, BuilderContext(
        library="Movies", library_type="Movie",
        config=params or {},
        run_cache=run_cache if run_cache is not None else {},
        sources=SourceClients(plex=PlexSectionAccess(section, returning(index))),
    )


async def test_membership_is_the_items_in_no_collection_in_index_order():
    items = [FakeItem("1"), FakeItem("2", ["Alien Collection"]), FakeItem("3")]
    section, ctx = _context(items)
    result = await PlexCollectionlessBuilder().build(ctx)
    assert result.ids == [("plex", "1"), ("plex", "3")]


async def test_excluded_collections_do_not_count():
    items = [FakeItem("1", ["Decade: 1980s"]),
             FakeItem("2", ["Decade: 1980s", "Alien Collection"]),
             FakeItem("3", ["Overlay"])]
    section, ctx = _context(items, params={
        "exclude": ["Overlay"], "exclude_prefix": ["Decade: "],
    })
    result = await PlexCollectionlessBuilder().build(ctx)
    assert result.ids == [("plex", "1"), ("plex", "3")]


async def test_one_batched_fetch_for_the_whole_library():
    items = [FakeItem(str(k)) for k in range(1, 6)]
    section, ctx = _context(items)
    await PlexCollectionlessBuilder().build(ctx)
    assert len(section.calls) == 1 and section.calls[0] == [1, 2, 3, 4, 5]


async def test_a_second_build_reuses_the_run_cache():
    items = [FakeItem("1")]
    run_cache = {}
    section, ctx = _context(items, run_cache=run_cache)
    await PlexCollectionlessBuilder().build(ctx)
    await PlexCollectionlessBuilder().build(ctx)
    assert len(section.calls) == 1


async def test_an_unanswered_item_refuses_the_whole_membership():
    items = [FakeItem("1"), FakeItem("2")]
    section = FakeSection(items, answer_all=False)
    section, ctx = _context(items, section=section)
    with pytest.raises(CollectionlessRefused):
        await PlexCollectionlessBuilder().build(ctx)


async def test_no_library_accessor_raises_by_name():
    ctx = BuilderContext(library="Movies", library_type="Movie")
    with pytest.raises(PlexLibraryUnavailable):
        await PlexCollectionlessBuilder().build(ctx)


def test_plex_collectionless_is_registered_under_its_own_name():
    # Every test above builds the class directly, so deleting the module's
    # ``register(...)`` line stays green. The sibling's own precedent:
    # test_builder_plex_trivial.py::test_plex_all_is_registered_under_its_own_name.
    assert REGISTRY["plex_collectionless"].type_name == "plex_collectionless"
