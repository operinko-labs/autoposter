"""Mapping namespaced external ids to owned Plex items."""
from autoposter.collections.builders.base import NAMESPACES
from autoposter.collections.resolve import build_owned_index, resolve_external


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
        FakeItem("TvdbOnly", ["tvdb://81189"]),
    ])


# --- the owned index ------------------------------------------------------

def test_the_owned_index_is_built_from_one_call():
    """Every namespace comes off the same ``section.all()`` pass; a second
    pass (or a per-item reload) is the thing this index exists to avoid."""
    section = _section()
    build_owned_index(section)
    assert section.all_calls == 1


def test_the_owned_index_carries_every_declared_namespace():
    assert set(build_owned_index(_section())) == set(NAMESPACES)


def test_guids_are_indexed_under_their_own_namespace():
    index = build_owned_index(_section())
    assert index["imdb"]["tt0111161"].title == "Shawshank"
    assert index["tmdb"]["278"].title == "Shawshank"
    assert index["tmdb"]["999"].title == "TmdbOnly"
    assert index["tvdb"]["81189"].title == "TvdbOnly"
    assert "999" not in index["imdb"]


def test_the_plex_namespace_is_keyed_by_the_rating_key_as_a_string():
    """Rating keys arrive from YAML as ints as often as strings."""
    item = FakeItem("Numeric", [])
    item.ratingKey = 12345
    index = build_owned_index(FakeSection([item]))
    assert index["plex"]["12345"] is item


def test_an_item_without_guids_is_still_reachable_by_rating_key():
    index = build_owned_index(_section())
    assert index["plex"]["NoGuids"].title == "NoGuids"
    assert len(index["plex"]) == 5


def test_the_first_guid_in_a_namespace_claims_the_item():
    """The IMDb-only index stopped at an item's first ``imdb://`` guid. The
    generalisation keeps that per namespace, so a second id of the same
    namespace resolves exactly as it did before: not at all."""
    section = FakeSection([FakeItem("Twice", ["imdb://tt1", "imdb://tt2"])])
    index = build_owned_index(section)
    assert "tt1" in index["imdb"]
    assert "tt2" not in index["imdb"]


# --- resolving namespaced ids --------------------------------------------

def test_multi_namespace_resolution_preserves_the_requested_order():
    """The order is the list's rank, whatever namespaces it mixes."""
    index = build_owned_index(_section())
    resolved = resolve_external(index, [
        ("tvdb", "81189"), ("imdb", "tt0068646"), ("tmdb", "999"),
    ])
    assert [i.title for i in resolved.items] == ["TvdbOnly", "Godfather", "TmdbOnly"]


def test_plex_rating_keys_resolve_without_an_external_lookup():
    index = build_owned_index(_section())
    resolved = resolve_external(index, [("plex", "NoGuids")])
    assert [i.title for i in resolved.items] == ["NoGuids"]
    assert resolved.unresolved == 0


def test_an_item_reachable_by_two_namespaces_appears_once_at_its_first_place():
    """Shawshank carries both ids; a list naming both must not put it in the
    collection twice, and must not move it to the second id's position."""
    index = build_owned_index(_section())
    resolved = resolve_external(index, [
        ("imdb", "tt0111161"), ("imdb", "tt0068646"), ("tmdb", "278"),
    ])
    assert [i.title for i in resolved.items] == ["Shawshank", "Godfather"]
    assert resolved.unresolved == 0


def test_unowned_ids_are_counted_not_guessed():
    index = build_owned_index(_section())
    resolved = resolve_external(index, [
        ("imdb", "tt0111161"), ("imdb", "tt9999999"), ("tmdb", "4242"),
    ])
    assert [i.title for i in resolved.items] == ["Shawshank"]
    assert resolved.unresolved == 2


def test_a_repeated_unowned_id_is_counted_once():
    """``unresolved`` reports how many of the list's titles this library is
    missing, so the same missing title asked for twice is one missing title."""
    index = build_owned_index(_section())
    resolved = resolve_external(index, [("imdb", "tt9999999")] * 3)
    assert resolved == ([], 1)


def test_an_id_in_an_unknown_namespace_is_unresolved_not_an_error():
    index = build_owned_index(_section())
    resolved = resolve_external(index, [("letterboxd", "tt0111161")])
    assert resolved.items == []
    assert resolved.unresolved == 1


def test_resolving_no_ids_yields_nothing():
    items, unresolved = resolve_external(build_owned_index(_section()), [])
    assert items == []
    assert unresolved == 0
