"""Pins the plexapi user-switch surface phase 98c drives itself.

Not a test of our code. Its sibling ``test_plexapi_playlist_contract.py`` opens
with the reason: "a hand-written test double will happily implement an API that
plexapi does not have -- which has already cost this project three bugs that
only appeared against a real server."

98c has MORE of that exposure than 98a did, because the chain it drives is one
plexapi itself packages as ``Playlist.copyToUser`` -- and this phase
deliberately does NOT call that method. It calls the four steps underneath it
by hand, so the exposure is four surfaces instead of one, and two of the pins
below assert not what a call does but what it does WRONG: ``get_token``
swallows every exception and returns ``None``, and both ``PlexServer`` and
``MyPlexAccount`` fall back to plexapi's own CONFIG FILE token when handed a
falsy one. Those two facts together are why an empty token has to be an
explicit refusal in our code and never a value we pass on.

Asserted against the real classes, offline, at plexapi 4.18.2 -- what
``pyproject.toml``'s ``plexapi>=4.16`` resolves to, and the version the two
shipped pin files are written against.
"""
import inspect

import plexapi.myplex as myplex
import pytest
from plexapi.myplex import (
    MyPlexAccount,
    MyPlexResource,
    MyPlexServerShare,
    MyPlexUser,
    Section,
)
from plexapi.playlist import Playlist
from plexapi.server import PlexServer


# --- the account-level surface ----------------------------------------------


def test_the_account_lists_users_with_no_required_argument():
    """``account.users()`` is the ONE call this phase caches per pass: plexapi
    caches nothing here (each call is a fresh plex.tv GET of
    ``https://plex.tv/api/users/``), and the fan-out asks for the user list
    once per definition."""
    required = [
        name for name, parameter in
        inspect.signature(MyPlexAccount.users).parameters.items()
        if name != "self" and parameter.default is inspect.Parameter.empty
    ]
    assert required == []
    assert MyPlexUser.key == "https://plex.tv/api/users/"


def test_the_account_lists_resources_and_a_resource_says_whether_we_own_it():
    """C13 A9's owner pre-check, in one plex.tv GET. ``clientIdentifier`` is
    what ``PlexServer.machineIdentifier`` is matched against and ``owned`` is
    the answer; without this check a non-owner token discovers itself as a 401
    partway through a fan-out that has already written to somebody."""
    required = [
        name for name, parameter in
        inspect.signature(MyPlexAccount.resources).parameters.items()
        if name != "self" and parameter.default is inspect.Parameter.empty
    ]
    assert required == []
    source = inspect.getsource(MyPlexResource._loadData)
    assert "self.clientIdentifier = data.attrib.get('clientIdentifier')" in source
    assert "self.owned = utils.cast(bool, data.attrib.get('owned'))" in source


def test_there_is_no_home_user_class_at_all():
    """Corrects the symbol the 98c pass reached for. ``account.users()``
    returns ``MyPlexUser`` for Home users and shared friends alike, so the
    classifier is a FLAG on that one class, not a second class."""
    assert not hasattr(myplex, "MyPlexHomeUser")


@pytest.mark.parametrize(
    "line",
    [
        "self.home = utils.cast(bool, data.attrib.get('home'))",
        "self.protected = utils.cast(bool, data.attrib.get('protected'))",
        "self.restricted = data.attrib.get('restricted')",
        "self.id = utils.cast(int, data.attrib.get('id'))",
        "self.title = data.attrib.get('title', '')",
    ],
)
def test_a_user_carries_the_five_attributes_the_fan_out_reads(line):
    """Instance attributes assigned in ``_loadData``, not class attributes, so
    they are pinned through the source the way ``Video.listType`` is in the
    playlist contract file.

    ``restricted`` is a **str**, not a bool, unlike its ``MyPlexAccount`` twin
    -- pinned so nobody writes ``if user.restricted:`` believing otherwise.
    ``protected`` is the PIN flag we skip on, and plexapi's own docstring for
    it says "Unknown (possibly SSL enabled?)" -- the operator's read-only probe
    (facts C14) is what licenses reading it as the PIN flag, not the library.
    """
    assert line in inspect.getsource(MyPlexUser._loadData)


def test_a_share_says_which_server_and_whether_all_libraries_are_shared():
    """``MyPlexUser.servers`` is parsed for free by ``account.users()``, so
    "does this user have access to THIS server at all" costs no extra request.
    ``Section.shared`` costs one GET per user and is what a later slice would
    need for per-library refusal; pinned now so the shape is known rather than
    guessed at when that slice is written."""
    share = inspect.getsource(MyPlexServerShare._loadData)
    assert "self.machineIdentifier = data.attrib.get('machineIdentifier')" in share
    assert "self.allLibraries = utils.cast(bool, data.attrib.get('allLibraries'))" in share
    section = inspect.getsource(Section._loadData)
    assert "self.shared = utils.cast(bool, data.attrib.get('shared', '0'))" in section
    assert "self.title = data.attrib.get('title')" in section


# --- the two footguns this phase exists to route around ---------------------


def test_get_token_swallows_every_failure_and_returns_none():
    """THE pin. ``get_token`` catches ``Exception``, logs, and falls off the
    end returning ``None`` -- for a user with no share for this machine AND for
    a plex.tv outage, indistinguishably. Our wrapper therefore treats a falsy
    token as an explicit REFUSAL with a named report line, and never passes it
    on."""
    assert "machineIdentifier" in inspect.signature(MyPlexUser.get_token).parameters
    source = inspect.getsource(MyPlexUser.get_token)
    assert "except Exception:" in source
    assert "return item.attrib.get('accessToken')" in source
    assert "raise" not in source


@pytest.mark.parametrize("cls", [PlexServer, MyPlexAccount])
def test_a_falsy_token_falls_back_to_plexapis_own_config_file(cls):
    """The other half of the same footgun, and the reason the first half is
    dangerous rather than merely unhelpful. Hand either constructor a ``None``
    token and it reads ``auth.server_token`` out of plexapi's config file -- so
    a write would go out under whatever identity that file names, or
    anonymously. This is asserted, not assumed, because our whole refusal
    exists for it."""
    source = inspect.getsource(cls.__init__)
    assert "token or CONFIG.get('auth.server_token')" in source


def test_copy_to_user_is_exactly_the_chain_we_refuse_to_call():
    """Pinned so the refusal is checkable rather than asserted in prose:
    ``copyToUser`` routes through ``switchUser``, which routes through
    ``get_token`` and hands its result -- ``None`` included -- straight to
    ``PlexServer``. Neither identifier appears anywhere in ``src/`` on this
    branch; the last step below proves that half."""
    assert "self._server.switchUser(user)" in inspect.getsource(Playlist.copyToUser)
    switch = inspect.getsource(PlexServer.switchUser)
    assert "userToken = user.get_token(self.machineIdentifier)" in switch
    assert "return PlexServer(self._baseurl, token=userToken" in switch


# --- the Home switch --------------------------------------------------------


def test_switch_home_user_takes_an_optional_pin_and_omits_it_when_absent():
    """C13 A8: a PIN-protected Home user is skipped BEFORE this call, because
    calling it without a PIN simply omits the parameter and leaves plex.tv to
    answer with a failure the pass would have to interpret. The flag check
    makes that unreachable, which is the point."""
    parameters = inspect.signature(MyPlexAccount.switchHomeUser).parameters
    assert "user" in parameters
    assert parameters["pin"].default is None
    source = inspect.getsource(MyPlexAccount.switchHomeUser)
    assert "if pin:" in source
    assert "params['pin'] = pin" in source


def test_switch_home_user_returns_an_account_whose_token_is_readable():
    """It returns a ``MyPlexAccount``, not a server -- the ``PlexServer``
    construction is ours to write -- and the token comes back off the public
    ``authenticationToken`` property rather than a private attribute."""
    source = inspect.getsource(MyPlexAccount.switchHomeUser)
    assert "userToken = data.attrib.get('authenticationToken')" in source
    assert "return MyPlexAccount(token=userToken" in source
    assert isinstance(MyPlexAccount.authenticationToken, property)
    assert "return self.authToken" in inspect.getsource(
        MyPlexAccount.authenticationToken.fget
    )


def test_building_an_account_from_a_token_costs_a_round_trip():
    """Not lazy: ``_signin`` short-circuits to ``self.query(self.key)`` whenever
    a token is present, so the Home switch costs TWO plex.tv calls (the switch
    POST plus this GET) against the shared path's one. That asymmetry is why
    the share token is tried first for every user and the Home switch is the
    fallback."""
    source = inspect.getsource(MyPlexAccount._signin)
    assert "if self._token:" in source
    assert "return self.query(self.key), self.key" in source


# --- the per-user server ----------------------------------------------------


def test_the_server_exposes_the_two_attributes_a_per_user_connection_needs():
    """``_baseurl`` and ``machineIdentifier``: the same two ``switchUser``
    itself reads. Pinned because our connector builds ``PlexServer(baseurl,
    token=...)`` by hand."""
    init = inspect.getsource(PlexServer.__init__)
    assert "self._baseurl = baseurl or CONFIG.get('auth.server_baseurl'" in init
    load = inspect.getsource(PlexServer._loadData)
    assert "self.machineIdentifier = data.attrib.get('machineIdentifier')" in load


def test_listing_and_creating_playlists_need_no_required_argument_but_title():
    """The two calls issued under a user's token. ``playlists()`` is
    ``fetchItems('/playlists')`` under whatever token that server holds, which
    is what makes the per-user ownership lookup possible at all; ``title`` is
    ``createPlaylist``'s only required parameter and ``items`` is the ordered
    list we hand it."""
    listing = [
        name for name, parameter in
        inspect.signature(PlexServer.playlists).parameters.items()
        if name != "self" and parameter.default is inspect.Parameter.empty
        and parameter.kind is not inspect.Parameter.VAR_KEYWORD
    ]
    assert listing == []
    create = inspect.signature(PlexServer.createPlaylist).parameters
    assert create["title"].default is inspect.Parameter.empty
    assert "items" in create


def test_a_user_copy_is_a_brand_new_playlist_with_its_own_rating_key():
    """F2, asserted: ``_create`` POSTs on whatever server it is handed and
    returns ``cls(server, data, initpath=key)`` -- a new object with its own
    ratingKey, under that account. Not a share, not a reference. This is the
    whole reason the ownership row is per (definition, user) and the lookup is
    scoped to that user's own listing."""
    source = inspect.getsource(Playlist._create)
    assert "data = server.query(key, method=server._session.post)[0]" in source
    assert "return cls(server, data, initpath=key)" in source


def test_the_create_uri_names_the_same_machine_the_admin_read_from():
    """Which licenses passing ADMIN-fetched item objects to a USER's server:
    the uri is ``server://{machineIdentifier}/library/metadata/<keys>`` built
    from the server the create runs on, and a member's rating key is that
    machine's metadata id -- server-wide, identical for every account."""
    assert "uri = f'{server._uriRoot()}/library/metadata/{ratingKeys}'" in (
        inspect.getsource(Playlist._create)
    )
    assert "server://" in inspect.getsource(PlexServer._uriRoot)


def test_every_write_a_copy_takes_goes_through_its_own_server():
    """The fact that makes "written under that user's token" true rather than
    hoped for. A copy is fetched from the USER's ``server.playlists()``, so its
    ``_server`` is that user's session -- and ``addItems``, ``removeItems`` and
    ``delete`` all issue through ``self._server``, never through the server the
    ITEMS came from. ``addItems`` in particular groups by the item's server to
    build the uri and then queries through its own, which is exactly the
    combination this phase relies on: admin item objects, a user's playlist,
    one PUT under the user's token."""
    add = inspect.getsource(Playlist.addItems)
    assert "key = f\"{self.key}/items{utils.joinArgs(args)}\"" in add
    assert "self._server.query(key, method=self._server._session.put)" in add
    remove = inspect.getsource(Playlist.removeItems)
    assert "self._server.query(key, method=self._server._session.delete)" in remove
    assert (
        "self._server.query(self.key, method=self._server._session.delete)"
        in inspect.getsource(Playlist.delete)
    )
