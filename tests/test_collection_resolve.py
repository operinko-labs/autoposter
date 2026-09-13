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


# --- roadmap row 143: the index at season and episode level -----------------
#
# A Show library's ``section.all()`` returns Shows only, and a Show carries no
# per-episode guid, so an episode-level definition has nothing to resolve
# against. ``section.search(libtype="episode")`` returns episodes in one call.
# What was missing is the CONTRACT, not the query.


class FakeSearchSection(FakeSection):
    """A section that answers ``all()`` and ``search(libtype=...)`` separately.

    Deliberately DIFFERENT objects per level: an implementation that walked
    ``all()`` and filtered would find episodes that are not there, which is the
    whole point of the second traversal.
    """

    def __init__(self, items, by_libtype=None):
        super().__init__(items)
        self._by_libtype = dict(by_libtype or {})
        self.searches: list[str] = []

    def search(self, libtype=None, **kwargs):
        self.searches.append(libtype)
        return list(self._by_libtype.get(libtype, []))


def _show_section():
    return FakeSearchSection(
        [FakeItem("Severance", ["tvdb://371980"])],
        {
            "episode": [
                FakeItem("S01E01", ["tvdb://7645236", "imdb://tt11248124"]),
                FakeItem("NoEpisodeGuids", []),
            ],
            "season": [FakeItem("Season 1", [])],
        },
    )


def test_the_item_level_index_is_still_one_all_call_and_no_search():
    """Gate-off byte-identity: an item-level pass must not gain a request."""
    section = _show_section()
    build_owned_index(section)
    assert section.all_calls == 1
    assert section.searches == []


def test_an_episode_level_index_comes_from_a_libtype_search_not_from_all():
    section = _show_section()
    index = build_owned_index(section, "episode")
    assert section.searches == ["episode"]
    assert section.all_calls == 0
    assert index["tvdb"]["7645236"].title == "S01E01"
    assert index["imdb"]["tt11248124"].title == "S01E01"


def test_a_season_level_index_comes_from_the_season_libtype_search():
    section = _show_section()
    index = build_owned_index(section, "season")
    assert section.searches == ["season"]
    assert index["plex"]["Season 1"].title == "Season 1"


def test_an_episode_with_no_guids_is_still_reachable_by_rating_key():
    """Season and episode guid coverage is agent-dependent; the rating key is
    always there, which is what makes ``plex_id``-shaped episode definitions
    work on a library whose agent populates no episode guids."""
    index = build_owned_index(_show_section(), "episode")
    assert index["plex"]["NoEpisodeGuids"].title == "NoEpisodeGuids"


def test_the_show_and_the_episode_index_are_separate_answers():
    """The show's own tvdb id must not appear in the episode index, and the
    episode's must not appear in the item index: they are different id spaces
    and merging them would resolve a series id to an episode."""
    section = _show_section()
    items = build_owned_index(section, "item")
    episodes = build_owned_index(section, "episode")
    assert "371980" in items["tvdb"] and "371980" not in episodes["tvdb"]
    assert "7645236" in episodes["tvdb"] and "7645236" not in items["tvdb"]


def test_an_unknown_level_is_a_key_error_not_a_silent_item_walk():
    """A typo must not quietly resolve the whole library at item level, which
    is a full, plausible, wrong membership."""
    import pytest
    with pytest.raises(KeyError):
        build_owned_index(_show_section(), "chapter")


def test_resolving_against_an_episode_index_needs_no_new_resolver():
    """``resolve_external`` is namespace-generic: the level is a property of
    the INDEX, not of the lookup. This test exists so a future refactor that
    adds a level parameter to the resolver has to delete it deliberately."""
    index = build_owned_index(_show_section(), "episode")
    resolved = resolve_external(index, [("tvdb", "7645236"), ("tvdb", "999")])
    assert [i.title for i in resolved.items] == ["S01E01"]
    assert resolved.unresolved == 1
