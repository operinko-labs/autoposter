"""Pins the plexapi surface ``PlexClient._list_items_sync`` relies on for the
id-mismatch view (``api/mismatches.py``).

Not a test of our code. ``section.all()`` is walked once per Plex type and the
guids/locations it returns are trusted to have come back in that single
listing request -- a per-item reload on a real library of any size would turn
a "seconds" endpoint into a very slow one, invisible against the hand-written
fakes the rest of the suite uses. These assertions touch the real plexapi
classes, offline, so an upstream change to either half of that contract fails
here rather than against an operator's server. See
``test_plexapi_logo_contract.py``'s module docstring for why this project
tests library internals this way at all.
"""

import inspect

from plexapi.base import cached_data_property, PlexPartialObject
from plexapi.library import LibrarySection
from plexapi.video import Movie, Show


def test_search_defaults_includeguids_to_true():
    """``_buildSearchKey`` is what every ``.all()``/``.search()`` call builds
    its request from. If a future plexapi version stopped defaulting
    ``includeGuids`` to true, ``_list_items_sync`` would start reloading every
    single item to get its guids -- exactly the N+1 this contract exists to
    catch before it reaches a real library."""
    source = inspect.getsource(LibrarySection._buildSearchKey)
    assert "includeGuids" in source, "plexapi lost the includeGuids search param"
    assert "kwargs.pop('includeGuids', True)" in source, (
        "plexapi no longer defaults includeGuids to True -- "
        "_list_items_sync's explicit includeGuids=True is now load-bearing, not just documentation"
    )


def test_all_calls_search_so_includeguids_threads_through():
    """``_list_items_sync`` calls ``section.all(includeGuids=True)``, not
    ``.search()`` directly. That only reaches ``_buildSearchKey`` if ``.all()``
    still forwards its kwargs to ``.search()``."""
    source = inspect.getsource(LibrarySection.all)
    assert "self.search(" in source, "plexapi's LibrarySection.all no longer delegates to search()"
    assert "**kwargs" in source, "plexapi's LibrarySection.all no longer forwards its kwargs"


def test_movie_locations_is_a_plain_property_reading_already_fetched_parts():
    """A movie's ``.locations`` is not parsed data -- it's computed from
    ``.media``/``.iterParts()``, which are themselves ``cached_data_property``s
    read off ``self._data``. As long as that stays true, a movie whose listing
    entry actually carries ``Media``/``Part`` elements never reloads for its
    location; only a movie with none (no file at all) would."""
    attribute = inspect.getattr_static(Movie, "locations")
    assert isinstance(attribute, property), "Movie.locations is no longer a plain property"
    source = inspect.getsource(attribute.fget)
    assert "iterParts" in source, "Movie.locations no longer reads iterParts() -- it may now issue its own fetch"


def test_show_guids_and_locations_are_cached_data_properties():
    """Unlike a movie, a show's ``.locations`` is parsed data directly off the
    listing response (``Location`` elements on the ``Directory`` entry) --
    same shape as ``.guids``. Both being ``cached_data_property`` is what
    guarantees they come back with ``section.all()`` and never trigger their
    own request for an item that actually has them."""
    for name in ("guids", "locations"):
        attribute = inspect.getattr_static(Show, name)
        assert isinstance(attribute, cached_data_property), (
            f"plexapi Show.{name} is no longer a cached_data_property"
        )


def test_a_genuinely_empty_attribute_still_trips_the_reload_guard():
    """The residual risk ``client.py`` documents: ``__getattribute__`` cannot
    tell "empty because unmatched" from "empty because not yet loaded" -- both
    read as falsy, and only a falsy value triggers the guard below. That means
    an unmatched Plex item (no guids at all) still costs one reload per item,
    ``includeGuids`` notwithstanding, and no listing parameter can close that
    gap because the ambiguity is in what an *empty answer* means, not in what
    was asked for. If plexapi ever grew a way to tell "loaded-and-empty" apart
    from "not yet loaded" (a sentinel, a `_loaded` flag), this assertion would
    start failing and the comment in ``client.py`` could be revisited."""
    source = inspect.getsource(PlexPartialObject.__getattribute__)
    assert "value not in (None, [])" in source, (
        "plexapi's partial-object reload guard changed shape -- re-check whether "
        "a genuinely empty guids/locations list still forces a per-item reload"
    )
