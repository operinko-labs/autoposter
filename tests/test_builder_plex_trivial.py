"""``plex_all``: the library itself, as a collection.

The interesting property is a cost one, so the fakes here count calls. A
builder that listed the library by asking Plex would double the most
expensive call in a pass; this one reads the index the engine had already
committed to building.
"""
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import (
    REGISTRY,
    BuilderContext,
    PlexSectionAccess,
    SourceClients,
)
from autoposter.collections.builders.plex_trivial import PlexLibraryUnavailable
from autoposter.collections.resolve import build_owned_index, resolve_external


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, title, guids=()):
        self.title = title
        self.ratingKey = title
        self.guids = [FakeGuid(g) for g in guids]


class CountingSection:
    """Counts every call a builder might be tempted to make."""

    def __init__(self, items):
        self._items = items
        self.calls: list[str] = []

    def all(self, *args, **kwargs):
        self.calls.append("all")
        return self._items

    def search(self, *args, **kwargs):
        self.calls.append("search")
        return []

    def searchEpisodes(self, *args, **kwargs):
        self.calls.append("searchEpisodes")
        return []


def _library():
    """A section plus the engine's own lazy accessor over it."""
    section = CountingSection([
        FakeItem("Shawshank", ["imdb://tt0111161", "tmdb://278"]),
        FakeItem("Godfather", ["tmdb://238"]),
        FakeItem("NoGuids"),
    ])
    index = {}

    def owned_index():
        if not index:
            index.update(build_owned_index(section))
        return index

    return section, PlexSectionAccess(section, owned_index)


def _ctx(access, library_type: str = "Movie", **params) -> BuilderContext:
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library, library_type=library_type, config=params,
        sources=SourceClients(plex=access),
    )


async def test_every_owned_item_becomes_a_plex_id():
    _, access = _library()
    result = await REGISTRY["plex_all"].build(_ctx(access))

    assert result.ids == [
        ("plex", "Shawshank"), ("plex", "Godfather"), ("plex", "NoGuids")
    ]


async def test_an_item_with_no_guids_is_still_included():
    """``plex_all`` is the one builder that cannot lose an item to a missing
    guid: the rating key IS the identity, so a library item Plex never matched
    to any agent still belongs in "everything"."""
    _, access = _library()
    result = await REGISTRY["plex_all"].build(_ctx(access))

    assert ("plex", "NoGuids") in result.ids


async def test_the_order_is_the_order_plex_lists_the_library_in():
    """Section order, because it becomes the collection's custom order."""
    section = CountingSection([FakeItem(name) for name in ("C", "A", "B")])
    index = {}

    def owned_index():
        if not index:
            index.update(build_owned_index(section))
        return index

    result = await REGISTRY["plex_all"].build(
        _ctx(PlexSectionAccess(section, owned_index))
    )
    assert [value for _, value in result.ids] == ["C", "A", "B"]


async def test_plex_all_makes_no_plex_call_of_its_own():
    """The whole point. ``section.all()`` is called once, by the engine's own
    lazy index, and the builder adds nothing to that -- no second ``all()``,
    no ``search``. Mutation proof: add any ``access.section()`` call to
    ``PlexAllBuilder.build`` and this goes red."""
    section, access = _library()
    access.owned_index()  # the engine resolving some earlier definition
    before = list(section.calls)

    await REGISTRY["plex_all"].build(_ctx(access))

    assert before == ["all"]
    assert section.calls == before, (
        "plex_all made %r on top of the engine's index" % section.calls[len(before):]
    )


async def test_a_cold_context_costs_exactly_one_listing():
    """And when ``plex_all`` is the first definition in the pass, the index it
    triggers is the one the engine would have built to resolve it anyway."""
    section, access = _library()
    await REGISTRY["plex_all"].build(_ctx(access))

    assert section.calls == ["all"]


async def test_the_ids_resolve_against_the_index_they_came_from():
    """End to end against the real resolver: every id ``plex_all`` produces is
    one the engine can turn back into an owned item, none unresolved."""
    _, access = _library()
    result = await REGISTRY["plex_all"].build(_ctx(access))

    resolved = resolve_external(access.owned_index(), result.ids)
    assert [item.title for item in resolved.items] == [
        "Shawshank", "Godfather", "NoGuids"
    ]
    assert resolved.unresolved == 0


async def test_plex_all_runs_on_a_show_library_too():
    """"Everything" means everything, whatever the library holds."""
    _, access = _library()
    result = await REGISTRY["plex_all"].build(_ctx(access, library_type="Show"))

    assert len(result.ids) == 3


async def test_plex_all_refuses_params_it_does_not_understand():
    """It takes none at all, so ``limit`` in ``params`` -- which belongs on
    the definition -- is an error rather than a silently ignored key."""
    _, access = _library()
    with pytest.raises(ValidationError):
        await REGISTRY["plex_all"].build(_ctx(access, limit=50))


async def test_plex_all_raises_when_the_context_has_no_library():
    with pytest.raises(PlexLibraryUnavailable):
        await REGISTRY["plex_all"].build(
            BuilderContext(library="Movies", library_type="Movie")
        )


def test_plex_all_is_registered_under_its_own_name():
    assert REGISTRY["plex_all"].type_name == "plex_all"
