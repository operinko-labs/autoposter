"""The explicit-id builders: whatever the operator typed, namespaced.

These are the ``plex_id`` shape with a different namespace on the front, so
what is worth testing is not the mapping but the *refusals*. An id list is the
one place where a wrong-but-plausible value is invisible: ``ids: [438631]``
under ``imdb_id`` resolves to nothing at all, and an unresolvable id is
already a normal outcome (the library simply does not own it), so nothing
downstream would ever report it. The params models are therefore the whole
guard, and each one is tested against the mistake it exists to catch.
"""
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import NAMESPACES, REGISTRY, BuilderContext
from autoposter.collections.builders.base import LibraryTypeMismatch


def _ctx(library_type: str = "Movie", **params) -> BuilderContext:
    """A context for a builder that needs neither HTTP nor a client."""
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(library=library, library_type=library_type, config=params)


async def test_imdb_id_returns_the_ids_in_the_order_they_were_written():
    result = await REGISTRY["imdb_id"].build(
        _ctx(ids=["tt0111161", "tt0068646", "tt0111161"])
    )

    assert result.ids == [
        ("imdb", "tt0111161"), ("imdb", "tt0068646"), ("imdb", "tt0111161")
    ]
    assert all(namespace in NAMESPACES for namespace, _ in result.ids)
    assert result.summary is None


async def test_imdb_id_refuses_a_bare_number():
    """The mistake this builder exists to catch: a TMDb id pasted under
    ``imdb_id`` is a well-formed config that silently builds nothing."""
    with pytest.raises(ValidationError, match="tt"):
        await REGISTRY["imdb_id"].build(_ctx(ids=["438631"]))


async def test_imdb_id_refuses_an_id_that_is_not_shaped_like_an_imdb_id():
    with pytest.raises(ValidationError):
        await REGISTRY["imdb_id"].build(_ctx(ids=["tt"]))


async def test_imdb_id_refuses_an_empty_list():
    """Raising, not returning empty -- empty is "make no changes" downstream."""
    with pytest.raises(ValidationError):
        await REGISTRY["imdb_id"].build(_ctx(ids=[]))


async def test_imdb_id_refuses_params_it_does_not_understand():
    with pytest.raises(ValidationError):
        await REGISTRY["imdb_id"].build(_ctx(ids=["tt0111161"], id=["tt0068646"]))


async def test_tmdb_movie_returns_the_ids_in_order():
    result = await REGISTRY["tmdb_movie"].build(_ctx(ids=["438631", "693134"]))

    assert result.ids == [("tmdb", "438631"), ("tmdb", "693134")]


async def test_tmdb_movie_coerces_yaml_integers_to_strings():
    """``ids: [438631]`` is what an operator writes and what YAML hands over;
    an external id value is a string in every namespace."""
    result = await REGISTRY["tmdb_movie"].build(_ctx(ids=[438631]))

    assert result.ids == [("tmdb", "438631")]


async def test_tmdb_movie_refuses_an_imdb_id():
    """The mirror of the bare-number refusal: a ``tt...`` id under a TMDb
    builder resolves to nothing and reports nothing."""
    with pytest.raises(ValidationError, match="tt0111161"):
        await REGISTRY["tmdb_movie"].build(_ctx(ids=["tt0111161"]))


async def test_tmdb_movie_refuses_an_empty_list():
    with pytest.raises(ValidationError):
        await REGISTRY["tmdb_movie"].build(_ctx(ids=[]))


async def test_tmdb_show_returns_the_ids_in_order():
    result = await REGISTRY["tmdb_show"].build(
        _ctx(library_type="Show", ids=["95396", 1396])
    )

    assert result.ids == [("tmdb", "95396"), ("tmdb", "1396")]


async def test_tmdb_show_refuses_params_it_does_not_understand():
    with pytest.raises(ValidationError):
        await REGISTRY["tmdb_show"].build(
            _ctx(library_type="Show", ids=["95396"], tmdb_ids=["1396"])
        )


async def test_tmdb_show_refuses_a_movie_library():
    """TMDb's movie and TV ids share one namespace, so nothing about the ids
    themselves can catch this: on the wrong library they simply resolve to
    nothing, which is what a correct collection of unowned titles looks like.
    A definition with no ``libraries:`` key runs against every library in the
    pass, so this is the only place it can be caught."""
    with pytest.raises(LibraryTypeMismatch, match="Show"):
        await REGISTRY["tmdb_show"].build(_ctx(ids=["95396"]))


async def test_tmdb_movie_refuses_a_show_library():
    with pytest.raises(LibraryTypeMismatch, match="Movie"):
        await REGISTRY["tmdb_movie"].build(_ctx(library_type="Show", ids=["438631"]))


async def test_imdb_id_is_at_home_on_either_library():
    """The mirror of the two above: an IMDb id says nothing about media type
    and resolves on whichever library owns the title, so guarding it would
    refuse a definition that is perfectly correct."""
    result = await REGISTRY["imdb_id"].build(
        _ctx(library_type="Show", ids=["tt0903747"])
    )

    assert result.ids == [("imdb", "tt0903747")]


async def test_plex_rating_key_is_kometas_other_name_for_plex_id():
    """Kometa documents ``plex_id`` and ``plex_rating_key`` as one builder
    under two names, and an operator porting a config writes whichever their
    Kometa config used. Both names must reach the same behaviour."""
    result = await REGISTRY["plex_rating_key"].build(_ctx(ids=["9", "3", "7"]))

    assert result.ids == [("plex", "9"), ("plex", "3"), ("plex", "7")]
    assert REGISTRY["plex_rating_key"].params_model is REGISTRY["plex_id"].params_model


async def test_plex_rating_key_refuses_params_it_does_not_understand():
    """The alias is a real registry entry with its own name, not a dict alias
    -- so it has to carry the same params guard, and this is what proves the
    aliasing did not lose it."""
    with pytest.raises(ValidationError):
        await REGISTRY["plex_rating_key"].build(_ctx(ids=["9"], id=["3"]))
