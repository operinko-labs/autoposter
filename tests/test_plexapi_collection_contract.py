"""Pins the plexapi surface this phase depends on.

Not a test of our code. It exists because a hand-written test double will
happily implement an API that plexapi does not have -- which has already
cost this project three bugs that only appeared against a real server. This
asserts against the real classes, offline.
"""
import inspect

import pytest
import requests
from plexapi import utils
from plexapi.collection import Collection
from plexapi.library import LibrarySection
from plexapi.server import PlexServer


@pytest.mark.parametrize(
    "name,required",
    [
        ("createCollection", ["title", "smart", "libtype", "sort", "filters"]),
        ("listFilterChoices", ["field", "libtype"]),
        ("collection", ["title"]),
    ],
)
def test_library_section_methods_take_the_parameters_we_pass(name, required):
    method = getattr(LibrarySection, name)
    params = inspect.signature(method).parameters
    for parameter in required:
        assert parameter in params, "%s lost its %r parameter" % (name, parameter)


@pytest.mark.parametrize(
    "name,required",
    [
        ("updateFilters", ["libtype", "sort", "filters"]),
        ("addLabel", ["labels"]),
        ("removeLabel", ["labels"]),
        ("editSortTitle", ["sortTitle"]),
        ("addItems", ["items"]),
        ("removeItems", ["items"]),
        ("moveItem", ["item", "after"]),
        ("sortUpdate", ["sort"]),
    ],
)
def test_collection_methods_take_the_parameters_we_pass(name, required):
    method = getattr(Collection, name)
    params = inspect.signature(method).parameters
    for parameter in required:
        assert parameter in params, "%s lost its %r parameter" % (name, parameter)


def test_collection_exposes_filters_and_smart():
    """`smart` is assigned in _loadData rather than declared, so accept either."""
    assert hasattr(Collection, "filters")
    assert "smart" in inspect.getsource(Collection._loadData) or hasattr(Collection, "smart")


def test_collections_are_listable_from_a_section():
    assert callable(LibrarySection.collections)


def test_collections_forwards_keyword_filters_to_search():
    """``section.collections(label=...)`` is what keeps the leftovers report
    to one request instead of a ``reload()`` per collection in the library.
    That only works if ``collections`` passes its keywords through rather
    than swallowing them."""
    params = inspect.signature(LibrarySection.collections).parameters
    assert "kwargs" in params and params["kwargs"].kind is inspect.Parameter.VAR_KEYWORD
    source = inspect.getsource(LibrarySection.collections)
    assert "self.search(" in source
    assert "**kwargs" in source
    assert "libtype='collection'" in source


def test_an_unknown_search_keyword_becomes_a_server_side_filter():
    """The filtering must happen on the Plex server, not in plexapi after
    the fact -- a client-side filter would still fetch (and reload) every
    collection. ``_buildSearchKey`` routes any keyword that is not a PlexAPI
    operator through ``_validateFilterField``, which encodes it into the
    query string of the ``/library/sections/<key>/all`` request."""
    source = inspect.getsource(LibrarySection._buildSearchKey)
    assert "OPERATORS" in source, "keywords are no longer split on PlexAPI operators"
    assert "_validateFilterField" in source
    assert "/library/sections/" in source

    validate = inspect.getsource(LibrarySection._validateFilterField)
    assert "urlencode" in validate, "filter fields are no longer encoded into the URL"


def test_label_is_a_documented_search_filter_field():
    """``label`` specifically -- the field the leftovers report filters on."""
    assert "**label** (:class:`~plexapi.media.MediaTag`)" in LibrarySection.search.__doc__


def test_collection_delete_exists_but_we_never_call_it():
    """Deleting a collection is out of scope for this phase. The method is
    pinned here so that if a later phase adds deletion, it is a deliberate
    change against a known API rather than an accident."""
    assert callable(Collection.delete)


def test_collection_labels_is_lazy_and_must_be_reloaded_explicitly():
    """``labels`` is a ``cached_data_property`` -- ``_loadData`` never sets
    it, so a collection fetched via ``section.collections()`` only has it
    populated by an implicit reload gated on ``plexapi.autoreload``. That
    global can be turned off, so our ownership check must call
    ``collection.reload()`` itself rather than rely on it."""
    from plexapi.base import cached_data_property

    assert isinstance(Collection.__dict__.get("labels"), cached_data_property)
    assert callable(Collection.reload)


def test_items_takes_no_arguments_and_returns_the_cached_list():
    assert list(inspect.signature(Collection.items).parameters) == ["self"]
    assert "self._items" in inspect.getsource(Collection.items)


def test_collection_items_are_cached_and_only_reload_invalidates_them():
    """``Collection._items`` is a ``cached_data_property``: ``addItems``,
    ``removeItems`` and ``moveItem`` each issue their query and return
    without invalidating it, so ``items()`` keeps handing back the snapshot
    taken before the write. Anything that reads the membership after a write
    must ``reload()`` first."""
    from plexapi.base import cached_data_property

    assert isinstance(Collection.__dict__.get("_items"), cached_data_property)
    for name in ("addItems", "removeItems", "moveItem"):
        source = inspect.getsource(getattr(Collection, name))
        assert "_items" not in source, "%s now touches the item cache" % name


def test_sort_update_accepts_the_two_orders_this_phase_uses():
    """``custom`` for the static collections, ``release`` for the dynamic
    Oscars year collections (kometa-collections.md §2.4)."""
    source = inspect.getsource(Collection.sortUpdate)
    assert "'custom'" in source and "'release'" in source


# --- The raw POST used to create an empty collection (reconcile._create_separator) ---
#
# ``section.createCollection`` raises ``BadRequest`` given no items, so the
# separator collection (permanently empty by design) cannot go through it.
# Kometa's own client bypasses this the same way: a direct POST to
# ``/library/collections`` with a ``uri`` that names no item keys. These pin
# the three plexapi internals that POST depends on.


def test_plex_server_uri_root_has_the_shape_the_raw_post_expects():
    """``_uriRoot()`` supplies the ``uri`` argument's prefix. It is a private
    method (leading underscore) that could move -- pinned here so a future
    plexapi upgrade breaking it fails loudly in this suite instead of only
    against a real server."""
    assert callable(PlexServer._uriRoot)
    source = inspect.getsource(PlexServer._uriRoot)
    assert "com.plexapp.plugins.library" in source
    assert "machineIdentifier" in source


def test_plex_server_query_accepts_a_method_override_for_post():
    """The raw POST needs ``query(key, method=server._session.post)`` rather
    than the default GET."""
    params = inspect.signature(PlexServer.query).parameters
    assert "key" in params
    assert "method" in params
    source = inspect.getsource(PlexServer.query)
    assert "method = method or" in source, "query must still default to GET when method is omitted"


def test_plex_server_exposes_the_requests_session_the_post_is_issued_through():
    """``server._session.post`` is the ``method`` handed to ``query``. Like
    ``_uriRoot`` it is private, and the separator's fake stands in for it, so
    it is pinned here rather than only discovered against a real server."""
    assert "session" in inspect.signature(PlexServer.__init__).parameters
    source = inspect.getsource(PlexServer.__init__)
    assert "self._session = session or requests.Session()" in source
    assert callable(requests.Session().post)


def test_join_args_url_encodes_a_dict_into_a_query_string():
    """Pins the exact encoding: a leading ``?``, one arg per ``&``-joined
    pair, values (not keys) percent-encoded. A ``uri`` value containing
    ``/`` and ``:`` must come out fully encoded, matching what Kometa's own
    call produces."""
    encoded = utils.joinArgs({"smart": 0, "uri": "server://abc/com.plexapp.plugins.library"})
    assert encoded.startswith("?")
    assert "smart=0" in encoded
    assert "uri=server%3A%2F%2Fabc%2Fcom.plexapp.plugins.library" in encoded


# --- Why the collection summary is written by hand (reconcile._edit_collection_summary) ---
#
# ``Collection.editSummary`` is NOT in the parametrized pin above, and that is
# deliberate: this service does not call it. It routes through the section,
# and that route 404s for collection summaries on Plex 1.43.3.10896-cb3ebc72d.
# These pin the routing (so an upgrade that fixed it would be noticed) and the
# ``_session.put`` the replacement is issued through.


def test_collection_edit_summary_still_routes_through_the_section():
    """``editSummary`` -> ``editField`` -> ``PlexPartialObject._edit`` ->
    ``self.section()._edit(...)``, i.e. a
    ``PUT /library/sections/{id}/all?type=18&id={ratingKey}&summary.value=...``.
    That request returns 404 for every collection summary on the target
    server -- while the same route serves a movie's summary, and a
    collection's ``title.value``, with a 200. Collection has no ``_edit`` of
    its own, so the inherited one is what runs.

    If a future plexapi stops routing this way, this test fails and
    ``reconcile._edit_collection_summary`` should be re-evaluated against a
    live server rather than assumed still necessary.
    """
    from plexapi.base import PlexPartialObject
    from plexapi.mixins import EditFieldMixin

    assert "editField(" in inspect.getsource(Collection.editSummary)
    assert "self._edit(" in inspect.getsource(EditFieldMixin.editField)
    assert [c for c in Collection.__mro__ if "_edit" in c.__dict__] == [PlexPartialObject]
    assert "self.section()._edit(" in inspect.getsource(PlexPartialObject._edit)


def test_plex_server_exposes_the_put_the_item_level_summary_write_uses():
    """``server._session.put`` is the ``method`` handed to ``query`` for
    ``PUT /library/metadata/{ratingKey}?summary.value=...`` -- the route that
    does return 200. Pins the same source guarantee as
    ``test_plex_server_exposes_the_requests_session_the_post_is_issued_through``
    above: ``_session`` is a real ``requests.Session``, on which ``put`` is
    just as available as ``post``."""
    assert "session" in inspect.signature(PlexServer.__init__).parameters
    source = inspect.getsource(PlexServer.__init__)
    assert "self._session = session or requests.Session()" in source
    assert callable(requests.Session().put)


def test_library_section_collection_fetches_by_title():
    """After the raw POST, the newly created collection is fetched back
    through the normal, public ``section.collection(title)`` rather than by
    hand-parsing the POST response."""
    assert callable(LibrarySection.collection)
    params = inspect.signature(LibrarySection.collection).parameters
    assert "title" in params
