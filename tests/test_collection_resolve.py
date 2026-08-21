"""Mapping IMDb ids to owned Plex items."""
from autoposter.collections.resolve import build_imdb_index, resolve_ids


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, title, guids):
        self.title = title
        self.ratingKey = title
        self.guids = [FakeGuid(g) for g in guids]


class FakeSection:
    def __init__(self, items):
        self._items = items
        self.all_calls = 0

    def all(self):
        self.all_calls += 1
        return self._items


def _section():
    return FakeSection([
        FakeItem("Shawshank", ["imdb://tt0111161", "tmdb://278"]),
        FakeItem("Godfather", ["tmdb://238", "imdb://tt0068646"]),
        FakeItem("NoGuids", []),
        FakeItem("TmdbOnly", ["tmdb://999"]),
    ])


def test_the_index_is_built_from_one_call():
    """section.all() returns guids already populated; reloading per item
    would turn one request into thousands."""
    section = _section()
    build_imdb_index(section)
    assert section.all_calls == 1


def test_the_index_maps_imdb_ids_to_items():
    index = build_imdb_index(_section())
    assert index["tt0111161"].title == "Shawshank"
    assert index["tt0068646"].title == "Godfather"


def test_items_without_an_imdb_guid_are_absent():
    index = build_imdb_index(_section())
    assert len(index) == 2
    assert all(key.startswith("tt") for key in index)


def test_resolution_preserves_the_requested_order():
    """The order is the chart rank."""
    index = build_imdb_index(_section())
    items = resolve_ids(index, ["tt0068646", "tt0111161"])
    assert [i.title for i in items] == ["Godfather", "Shawshank"]


def test_unowned_ids_are_dropped_not_guessed():
    index = build_imdb_index(_section())
    items = resolve_ids(index, ["tt0111161", "tt9999999", "tt0068646"])
    assert [i.title for i in items] == ["Shawshank", "Godfather"]


def test_resolving_an_empty_list_yields_nothing():
    assert resolve_ids(build_imdb_index(_section()), []) == []


def test_duplicate_ids_resolve_once():
    index = build_imdb_index(_section())
    items = resolve_ids(index, ["tt0111161", "tt0111161"])
    assert len(items) == 1
