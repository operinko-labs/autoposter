"""Pins the plexapi playlist surface phase 98a depends on.

Not a test of our code. Its sibling ``test_plexapi_collection_contract.py``
opens with the reason: "a hand-written test double will happily implement an
API that plexapi does not have -- which has already cost this project three
bugs that only appeared against a real server." Playlists start with MORE of
that exposure than collections did, because nothing playlist-shaped existed in
``src/`` before this phase (``git grep -i playlist`` over ``src/`` on the
branch point returned zero hits), so every fake in ``tests/test_playlists.py``
stands in for a class this suite has never exercised.

Asserted against the real classes, offline, at plexapi 4.18.2 -- what
``pyproject.toml``'s ``plexapi>=4.16`` resolves to, and the version the
collection pin file is written against.
"""
import inspect

import pytest
from plexapi.audio import Audio
from plexapi.base import cached_data_property
from plexapi.media import Field
from plexapi.mixins import EditFieldMixin, LabelMixin, SummaryMixin
from plexapi.photo import Photo
from plexapi.playlist import Playlist
from plexapi.server import PlexServer
from plexapi.video import Episode, Movie, Season, Show, Video


@pytest.mark.parametrize(
    "name,required",
    [
        ("addItems", ["items"]),
        ("removeItems", ["items"]),
        ("moveItem", ["item", "after"]),
        ("editSummary", ["summary"]),
        ("items", ["libtype"]),
    ],
)
def test_playlist_methods_take_the_parameters_we_pass(name, required):
    method = getattr(Playlist, name)
    params = inspect.signature(method).parameters
    for parameter in required:
        assert parameter in params, "Playlist.%s lost its %r parameter" % (
            name, parameter
        )


def test_move_item_has_the_same_signature_as_the_collection_form():
    """``lists._enforce_order`` is IMPORTED by ``collections/playlists.py``
    rather than copied, and this is the fact that licenses it: the minimal-move
    reorder calls exactly ``obj.reload()``, ``obj.items()`` and
    ``obj.moveItem(item, after=previous)``, and both classes answer all three
    the same way. If these signatures ever diverge, the shared function has to
    become two."""
    from plexapi.collection import Collection

    assert (
        list(inspect.signature(Playlist.moveItem).parameters)
        == list(inspect.signature(Collection.moveItem).parameters)
    )


def test_playlist_delete_takes_no_arguments():
    """The one irreversible call this phase makes -- ``_sweep_playlists``
    behind ``playlists.delete_unconfigured``, and the ops endpoint behind
    ``confirm: true``. Pinned rather than assumed for the reason the collection
    twin gives: an extra required parameter would surface as a TypeError
    mid-sweep, against a real server, with some playlists already gone."""
    assert callable(Playlist.delete)
    required = [
        parameter for name, parameter in
        inspect.signature(Playlist.delete).parameters.items()
        if name != "self" and parameter.default is inspect.Parameter.empty
    ]
    assert required == [], "Playlist.delete grew a required parameter"


def test_a_playlist_carries_no_labels_at_all():
    """THE negative assertion. Every ownership, adoption and protection
    predicate in ``collections/reconcile.py`` is label-typed -- ``load_labels``,
    ``has_label``, ``prior_tool_label``, ``protected_label``,
    ``adoptable_labels``, ``claim_ownership`` -- and none of them compiles
    against a playlist, because plexapi's ``Playlist`` is not a ``LabelMixin``
    and exposes no label surface. That fact, not a preference, is why 98a
    invents the rating-key ownership predicate instead of porting the label
    one, and it is asserted here so the design is pinned to the library rather
    than to a reading of it."""
    assert not issubclass(Playlist, LabelMixin)
    for name in ("addLabel", "removeLabel", "labels"):
        assert not hasattr(Playlist, name), (
            "Playlist grew %r -- the label-free ownership argument in "
            "collections/playlists.py has to be re-made" % name
        )


def test_playlist_create_refuses_an_empty_item_list():
    """There is no blank-playlist create, unlike the collection side (which
    needed ``reconcile.create_blank_collection``'s hand-rolled POST for exactly
    this reason). The reconciler never reaches ``createPlaylist`` with an empty
    list anyway -- the empty-result law returns first -- and this pins the
    hazard that law is protecting against."""
    source = inspect.getsource(Playlist._create)
    assert "if not items:" in source
    assert "raise BadRequest" in source
    assert "Must include items" in source


def test_playlist_create_takes_its_type_from_the_first_item():
    """The first resolved member decides the playlist's type forever, and a
    mixed list raises. This is the plexapi constraint the pass's pre-write
    library-type refusal quotes."""
    source = inspect.getsource(Playlist._create)
    assert "listType = items[0].listType" in source
    assert "Can not mix media types when building a playlist" in source


def test_add_items_refuses_a_media_type_mix_and_a_smart_playlist():
    source = inspect.getsource(Playlist.addItems)
    assert "if self.smart:" in source
    assert "Cannot add items to a smart playlist" in source
    assert "if item.listType != self.playlistType:" in source


def test_remove_items_issues_one_delete_per_item():
    """Unlike ``Collection.removeItems``, which sends one request. The cost is
    disclosed in ``collections/playlists.py``'s docstring rather than
    discovered; it is also why the reconciler diffs instead of recreating."""
    source = inspect.getsource(Playlist.removeItems)
    assert "for item in items:" in source
    assert "self._server._session.delete" in source


def test_remove_and_move_resolve_an_item_through_the_cached_membership():
    """``_getPlaylistItemID`` walks ``self.items()`` -- the cached ``_items``
    -- to turn a library item into the playlist-scoped id the DELETE and the
    move need. That is why the reconciler calls ``reload()`` before
    ``removeItems``: a cache taken before the pass's own ``addItems`` is a
    membership that no longer matches the object."""
    source = inspect.getsource(Playlist._getPlaylistItemID)
    assert "self.items()" in source
    assert "playlistItemID" in source
    for name in ("removeItems", "moveItem"):
        assert "_getPlaylistItemID" in inspect.getsource(getattr(Playlist, name))


def test_playlist_items_are_cached_and_only_reload_invalidates_them():
    """The same guarantee ``lists._enforce_order`` is written around for
    collections: ``items()`` hands back a snapshot that the write methods leave
    in place, so the order is read exactly once from a freshly reloaded object.
    ``addItems`` is checked against ``self._items`` specifically -- its body
    has a LOCAL named ``_items`` (the per-server ``groupby`` variable), so the
    collection twin's bare substring check would false-positive here."""
    assert isinstance(Playlist.__dict__.get("_items"), cached_data_property)
    assert "self._items" in inspect.getsource(Playlist.items)
    assert "self._items" not in inspect.getsource(Playlist.addItems)
    for name in ("removeItems", "moveItem"):
        assert "_items" not in inspect.getsource(getattr(Playlist, name))
    assert callable(Playlist.reload)


def test_a_playlist_summary_is_written_through_the_playlists_own_edit():
    """Collections need ``reconcile._edit_collection_summary``'s hand-rolled
    PUT because ``Collection.editSummary`` routes through the SECTION, which
    404s for a collection summary on the target server. Playlists do not:
    ``Playlist`` overrides ``_edit`` with a PUT straight at its own key, so
    ``editSummary`` is safe to call directly and this phase writes no hand-rolled
    request at all."""
    assert "editField(" in inspect.getsource(SummaryMixin.editSummary)
    assert "self._edit(" in inspect.getsource(EditFieldMixin.editField)
    assert [c.__name__ for c in Playlist.__mro__ if "_edit" in c.__dict__] == [
        "Playlist", "PlexPartialObject",
    ]
    source = inspect.getsource(Playlist._edit)
    assert "key = f'{self.key}{utils.joinArgs(kwargs)}'" in source
    assert "self._server._session.put" in source


def test_a_summary_can_be_cleared_and_unlocked_through_the_same_call():
    """What lets the pass CLEAR a summary an operator deleted from a definition.

    On the collections side that clear is ``reconcile._clear_collection_summary``
    -- a hand-rolled PUT of ``{"summary.value": "", "summary.locked": 0}``,
    written that way only because ``Collection.editSummary`` routes through the
    section and 404s. A playlist needs no such workaround: ``editSummary``
    already builds exactly those two edits (``value or ''`` collapses ``""`` and
    ``None`` to the empty string, and ``locked=False`` sends ``0``), and
    ``Playlist._edit`` PUTs them straight at the playlist's own key. So the
    playlist clear is one library call, ``editSummary("", locked=False)``, and
    this pins the two halves of it.

    ``fields`` is the other half: it is the LOCK, and the lock is the only
    marker saying a summary is this service's to revert -- every set this pass
    issues locks the field, so an UNLOCKED summary was never ours and is left
    alone. ``Playlist`` defines ``fields`` itself (it is not inherited), as the
    same kind of ``cached_data_property`` as ``_items``, so it is a snapshot
    that only ``reload()`` invalidates."""
    source = inspect.getsource(EditFieldMixin.editField)
    assert "f'{field}.value': value or ''" in source
    assert "f'{field}.locked': 1 if locked else 0" in source
    assert "locked=True" in inspect.getsource(SummaryMixin.editSummary), (
        "editSummary stopped locking by default, so the lock is no longer the "
        "marker collections/playlists.py's summary clear reads"
    )

    assert isinstance(Playlist.__dict__.get("fields"), cached_data_property)
    loader = inspect.getsource(Playlist.__dict__["fields"].func)
    assert "media.Field" in loader
    for name in ("name", "locked"):
        assert name in inspect.getsource(Field._loadData), (
            "plexapi's Field no longer carries %r, which is what the summary "
            "clear's ownership test reads" % name
        )


def test_the_playlist_load_data_carries_the_fields_ownership_reads():
    """``ratingKey`` is the ownership predicate's whole basis, and plexapi
    casts it to an INT -- which is why every comparison in
    ``collections/playlists.py`` goes through ``str()``. ``smart`` and
    ``playlistType`` are what the write methods branch on; ``title`` and
    ``summary`` are the two fields the reconciler reads back."""
    source = inspect.getsource(Playlist._loadData)
    assert "self.ratingKey = utils.cast(int, data.attrib.get('ratingKey'))" in source
    assert "self.smart = utils.cast(bool, data.attrib.get('smart'))" in source
    assert "self.playlistType = data.attrib.get('playlistType')" in source
    assert "self.title = data.attrib.get('title')" in source
    assert "self.summary = data.attrib.get('summary')" in source


def test_a_playlist_has_no_library_of_its_own():
    """``librarySectionID`` is on the object, but Plex fills it only for radio
    playlists -- a regular playlist spans whatever sections its members came
    from. This is why ``managed_playlists`` has no ``library`` column and why
    the pass cannot live inside ``service.reconcile_libraries``' per-library
    loop: there is no one library to commit it under."""
    assert "self.librarySectionID" in inspect.getsource(Playlist._loadData)
    assert "library" not in inspect.signature(Playlist._create).parameters


@pytest.mark.parametrize(
    "name,required",
    [
        ("createPlaylist", ["title", "items", "smart"]),
        ("playlists", ["playlistType", "title"]),
        ("playlist", ["title"]),
    ],
)
def test_plex_server_playlist_methods_take_the_parameters_we_pass(name, required):
    params = inspect.signature(getattr(PlexServer, name)).parameters
    for parameter in required:
        assert parameter in params, "PlexServer.%s lost its %r parameter" % (
            name, parameter
        )


def test_server_playlists_lists_every_playlist_in_one_request():
    """The pass builds its ownership map from ONE ``server.playlists()`` call
    and never asks the server about a title. ``playlists()`` with no arguments
    is that one request."""
    source = inspect.getsource(PlexServer.playlists)
    assert "key = f'/playlists{utils.joinArgs(args)}'" in source
    assert "self.fetchItems(key" in source

    parameters = inspect.signature(PlexServer.playlists).parameters
    # 4.18.2's real signature is
    # ``playlists(self, playlistType=None, sectionId=None, title=None,
    # sort=None, **kwargs)``. A VAR_KEYWORD parameter's ``default`` IS
    # ``Parameter.empty`` -- there is no such thing as a default for ``**kwargs``
    # -- so the collection twin's idiom (name != "self" and default is empty)
    # reports ``['kwargs']`` here and the pin fails against a library that is
    # perfectly fine. The KINDS are what "required" means, so they are what is
    # filtered on.
    required = [
        name for name, parameter in parameters.items()
        if name != "self"
        and parameter.default is inspect.Parameter.empty
        and parameter.kind not in (
            inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD
        )
    ]
    assert required == [], "PlexServer.playlists grew a required argument"
    # And the executed truth the filter above is written around, asserted rather
    # than assumed: the one empty-default parameter besides ``self`` is the
    # catch-all. If that ever stops being true the filter is hiding something.
    assert parameters["kwargs"].kind is inspect.Parameter.VAR_KEYWORD


def test_create_playlist_delegates_to_the_regular_create_path():
    """``server.createPlaylist(title=..., items=...)`` is the only create this
    phase issues, and it goes through ``Playlist.create``'s plain-list branch.

    The delegation is asserted as the source line 4.18.2 actually carries --
    ``cls._create(server, title, items)``, **positional**. It was written here
    first in the keyword form and the pin failed: the three arguments are passed
    by position, which is a fact about the library, not a preference of ours."""
    assert "Playlist.create(" in inspect.getsource(PlexServer.createPlaylist)
    source = inspect.getsource(Playlist.create)
    assert "return cls._create(server, title, items)" in source
    # The branch above it, so a future refactor that routes a plain create
    # through the smart path cannot pass this test silently.
    assert "elif smart:" in source


def test_list_type_is_an_instance_attribute_and_video_covers_all_four_kinds():
    """A Movie library and a Show library CAN feed one playlist: ``listType``
    is set on ``Video._loadData``, so Movie, Show, Season and Episode all
    answer 'video'. Audio and Photo do not, which is the mix
    ``Playlist.addItems`` raises on and the pass refuses before it writes.

    Asserted through the SOURCE and not through ``getattr``: ``listType`` is
    assigned in ``_loadData``, so ``Video.listType`` on the class raises
    AttributeError. (The recon read this as a class attribute; it is not.)"""
    assert "self.listType = 'video'" in inspect.getsource(Video._loadData)
    assert "self.listType = 'audio'" in inspect.getsource(Audio._loadData)
    assert "self.listType = 'photo'" in inspect.getsource(Photo._loadData)
    for cls in (Movie, Show, Season, Episode):
        assert issubclass(cls, Video), (
            "%s no longer inherits Video's listType, so it may no longer share "
            "a playlist with the others" % cls.__name__
        )
