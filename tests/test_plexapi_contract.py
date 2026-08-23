"""Assert that the way we call plexapi matches the real plexapi.

The unit tests for `PlexClient` drive a hand-written fake server. A fake encodes
the author's *belief* about a library's interface, so it validates the belief
rather than the library — and two real bugs shipped behind a green suite
because of exactly that:

1. The code called ``server.library()``. `PlexServer.library` is a property, so
   against a real server that raised ``TypeError: 'Library' object is not
   callable``. The fake had defined ``library()`` as a method.
2. The code called ``section.search(guid="tvdb://415089")``. Under the Plex
   Movie/TV agents an item's *primary* ``guid`` is a ``plex://`` URI and the
   external ids live in its ``guids`` list, so that search matches nothing —
   verified against a real library, where it returned 0 results while
   ``getGuid`` found the show. The fake had implemented ``search(guid=...)`` as
   matching the ``guids`` list, i.e. the behaviour we wished existed.

These tests touch the real plexapi classes and no network. They fail if a call
site drifts from the library, or if a plexapi upgrade moves the surface we
depend on — which is the failure the fakes cannot catch.
"""

import inspect

from plexapi.library import LibrarySection, MovieSection, ShowSection
from plexapi.server import PlexServer
from plexapi.video import Episode, Movie, Season, Show


def test_library_is_an_attribute_not_a_method():
    """``server.library`` — never ``server.library()``."""
    attribute = inspect.getattr_static(PlexServer, "library")
    assert not inspect.isfunction(attribute), (
        "PlexServer.library became a method; PlexClient accesses it as an attribute"
    )
    assert hasattr(attribute, "__get__"), "expected a descriptor (property-like)"


def test_sections_is_a_method_on_library():
    from plexapi.library import Library

    assert inspect.isfunction(inspect.getattr_static(Library, "sections"))


def test_getguid_exists_for_external_id_lookup():
    """External ids resolve through ``getGuid``, not ``search(guid=...)``."""
    assert inspect.isfunction(inspect.getattr_static(LibrarySection, "getGuid"))
    signature = inspect.signature(LibrarySection.getGuid)
    assert list(signature.parameters) == ["self", "guid"]


def test_show_exposes_season_and_episode_navigation():
    """A season/episode intent navigates down from the matched show."""
    season = inspect.signature(Show.season)
    assert "season" in season.parameters
    episode = inspect.signature(Show.episode)
    assert "season" in episode.parameters
    assert "episode" in episode.parameters


def _provides(cls, name: str) -> bool:
    """True if plexapi exposes ``name`` on ``cls``, by either mechanism it uses.

    Some attributes are descriptors on the class (``guids`` is a
    ``cached_data_property``); others are assigned during ``_loadData`` at parse
    time and so never appear on the type. Accept both.
    """
    if inspect.getattr_static(cls, name, None) is not None:
        return True
    source = "".join(
        inspect.getsource(base._loadData)
        for base in cls.__mro__
        if "_loadData" in vars(base)
    )
    return f"self.{name}" in source


def test_attributes_the_resolver_reads_are_exposed_by_plexapi():
    """Guards the fields `_search_sync` pulls out inside the worker thread.

    A rename upstream fails here rather than at runtime against a live server —
    which is precisely how the two resolver bugs reached production-shaped code
    behind a green suite.
    """
    for cls, expected in (
        (Show, ("title", "ratingKey", "guids")),
        (Season, ("title", "ratingKey", "guids", "parentRatingKey")),
        (Episode, ("title", "ratingKey", "guids", "parentRatingKey")),
    ):
        for attribute in expected:
            assert _provides(cls, attribute), (
                f"plexapi {cls.__name__} no longer exposes {attribute!r}"
            )

    assert _provides(LibrarySection, "title")
    assert _provides(LibrarySection, "locations")


def test_the_type_strings_the_resolver_filters_on():
    """``_search_sync`` searches only sections whose ``type`` matches the intent
    and refuses a GUID match whose own ``type`` is wrong — both by comparing
    against the literal strings ``"movie"`` and ``"show"``. The fakes assert the
    same literals, so if plexapi renamed either the suite would stay green while
    a real server resolved nothing.
    """
    assert MovieSection.TYPE == "movie"
    assert ShowSection.TYPE == "show"
    assert Movie.TYPE == "movie"
    assert Show.TYPE == "show"
    assert _provides(LibrarySection, "type")
    assert _provides(Movie, "type")
    assert _provides(Show, "type")


def test_notfound_is_the_exception_getguid_raises():
    from plexapi.exceptions import NotFound

    assert issubclass(NotFound, Exception)
    assert "NotFound" in inspect.getsource(LibrarySection.getGuid)
