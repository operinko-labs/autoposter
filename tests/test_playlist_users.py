"""The per-user playlist sync: who is resolved, how, and what is never leaked.

Nothing here calls plexapi. Every stand-in below is a class
``tests/test_plexapi_user_switch_contract.py`` pins on the real library, with
the same attribute names and the same failure behaviour -- in particular
``FakeUser.get_token`` returns ``None`` rather than raising for a user with no
share, because the real one swallows its own exception and does exactly that.

``TOKEN`` is a sentinel that cannot occur by accident. Four tests plant it and
assert it appears in no SERVED string: not in a repr, not in a database
column, not in an action string, and not in the part of a log line this
service writes. That is the same proof ``tests/test_builder_plex_watchlist.py``
makes one level up, and it is the reason this module holds a callable rather
than a string.

The fakes below raise exceptions whose MESSAGE carries
``?X-Plex-Token=<the token>``. That is deliberately worse than reality --
every Plex fetch in ``src/`` sends the token as a header and plexapi omits it
from its exception text unless ``_showSecrets`` is set, which nothing here
does -- so a proof that holds against these fakes holds against anything
plexapi actually raises.
"""
import io
import logging
from types import SimpleNamespace

from autoposter.collections.playlist_users import UserSync
from autoposter.config.schema import PlaylistDefinition, PlaylistsConfig

MACHINE = "abc123machine"
BASEURL = "http://plex.example:32400"
TOKEN = "sentinel-user-token-never-logged"


# --- the plexapi stand-ins ---------------------------------------------------


class FakeShare:
    """``plexapi.myplex.MyPlexServerShare`` -- parsed for free by users()."""

    def __init__(self, machine_identifier, all_libraries=True):
        self.machineIdentifier = machine_identifier
        self.allLibraries = all_libraries


class FakeUser:
    """``plexapi.myplex.MyPlexUser`` -- one row of ``account.users()``.

    ``home``, ``protected`` and ``id`` are the three attributes the fan-out
    reads, and ``restricted`` is a str here because it is a str there.
    """

    def __init__(
        self, user_id, title, *, token=None, home=False, protected=False,
        machine=MACHINE, raises=False,
    ):
        self.id = user_id
        self.title = title
        self.home = home
        self.protected = protected
        self.restricted = "0"
        self.servers = [FakeShare(machine)] if token is not None else []
        self._token = token
        self.raises = raises
        self.token_reads = 0

    def get_token(self, machineIdentifier):
        self.token_reads += 1
        if self.raises:
            raise RuntimeError("https://plex.tv/api/servers/x?X-Plex-Token=SECRET")
        if machineIdentifier != MACHINE:
            return None
        return self._token


class FakeSwitchedAccount:
    """What ``switchHomeUser`` hands back: an account, not a server."""

    def __init__(self, token):
        self.authenticationToken = token


class FakeResource:
    def __init__(self, client_identifier, owned):
        self.clientIdentifier = client_identifier
        self.owned = owned


class FakeAccount:
    """``plexapi.myplex.MyPlexAccount`` -- the owner's."""

    def __init__(
        self, users, *, owned=True, machine=MACHINE, title="owner",
        username="owner@example.com", home_tokens=None, switch_raises=False,
    ):
        self._users = list(users)
        self._owned = owned
        self._machine = machine
        self.title = title
        self.username = username
        self.home_tokens = dict(home_tokens or {})
        self.switch_raises = switch_raises
        self.user_reads = 0
        self.resource_reads = 0
        self.switches = []

    def users(self):
        self.user_reads += 1
        return list(self._users)

    def resources(self):
        self.resource_reads += 1
        return [FakeResource(self._machine, self._owned)]

    def switchHomeUser(self, user, pin=None):
        self.switches.append((user.title, pin))
        if self.switch_raises:
            raise RuntimeError("plex.tv said no")
        return FakeSwitchedAccount(self.home_tokens.get(user.id, ""))


class FakeAdminServer:
    """Only the two attributes a per-user connection is built from."""

    def __init__(self, machine=MACHINE, baseurl=BASEURL):
        self.machineIdentifier = machine
        self._baseurl = baseurl


class FakeField:
    def __init__(self, name, locked=True):
        self.name = name
        self.locked = locked


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key
        self.listType = "video"


class FakeUserPlaylist:
    """``plexapi.playlist.Playlist`` as it appears in a USER's listing."""

    def __init__(self, rating_key, title, items=(), summary=None):
        self.ratingKey = int(rating_key)
        self.title = title
        self.summary = summary
        self.fields = [FakeField("summary", True)] if summary else []
        self._members = list(items)
        self.writes = []
        self.moves = []
        self.reloads = 0
        self.deleted = False
        self.deletes_raise = False

    def items(self, libtype=None):
        return list(self._members)

    def addItems(self, items):
        self.writes.append(("add", [i.ratingKey for i in items]))
        self._members.extend(items)
        return self

    def removeItems(self, items):
        self.writes.append(("remove", [i.ratingKey for i in items]))
        keys = {str(i.ratingKey) for i in items}
        self._members = [i for i in self._members if str(i.ratingKey) not in keys]
        return self

    def moveItem(self, item, after=None):
        self.moves.append((item.ratingKey, None if after is None else after.ratingKey))
        return self

    def reload(self):
        self.reloads += 1
        return self

    def editSummary(self, summary, locked=True):
        self.writes.append(("summary", summary))
        self.summary = summary or None
        self.fields = [FakeField("summary", locked)] if summary else []
        return self

    def delete(self):
        if self.deletes_raise:
            raise RuntimeError("https://plex.example/playlists/1?X-Plex-Token=SECRET")
        self.deleted = True


class FakeUserServer:
    def __init__(self, token, playlists=()):
        self.token = token
        self._playlists = list(playlists)
        self.created = []
        self.listings = 0

    def playlists(self, **kwargs):
        self.listings += 1
        return list(self._playlists)

    def createPlaylist(self, title, items=None, **kwargs):
        playlist = FakeUserPlaylist(8000 + len(self.created), title, items or [])
        self.created.append(playlist)
        self._playlists.append(playlist)
        return playlist


class Connector:
    """The injected ``connect(baseurl, token)`` seam.

    The only place in these tests where a token becomes an object, which is
    what lets a test assert both that a connection was made under the right
    token and -- the load-bearing one -- that none was made at all.
    """

    def __init__(self, servers=None, raises_for=()):
        self.servers = dict(servers or {})
        self.raises_for = set(raises_for)
        self.calls = []

    def __call__(self, baseurl, token):
        self.calls.append((baseurl, token))
        if token in self.raises_for:
            raise RuntimeError("https://plex.example/?X-Plex-Token=%s" % token)
        return self.servers.setdefault(token, FakeUserServer(token))


# --- the fixtures the tests build on -----------------------------------------

ITEM_A = FakeItem("11")
ITEM_B = FakeItem("12")
ITEM_C = FakeItem("13")

A_DEFINITION = {
    "title": "Timeline",
    "builder": "imdb_list",
    "params": {"list": "ls539646485"},
}


def _config(**section):
    """A stand-in whose only attribute this module reads is ``playlists``.

    The same shim shape ``config/schema.py``'s own validators use
    (``SimpleNamespace(collections=self)``), and the same one
    ``tests/test_playlist_presets.py`` uses.
    """
    return SimpleNamespace(playlists=PlaylistsConfig.model_validate(section))


def _definition(**overrides):
    return PlaylistDefinition.model_validate({**A_DEFINITION, **overrides})


def _sync(users, *, connector=None, owned=True, config=None, **account):
    account_stub = FakeAccount(users, owned=owned, **account)
    return UserSync(
        account_stub,
        FakeAdminServer(),
        config or _config(),
        connect=connector or Connector(),
    ), account_stub


# --- resolving who a definition reaches --------------------------------------


def test_the_user_list_is_read_once_per_pass_however_many_definitions_ask():
    """plexapi caches nothing here -- every ``account.users()`` is a fresh
    plex.tv GET -- so the pass caches it instead. Seventeen users and nine
    definitions is one request, not nine."""
    sync, account = _sync([FakeUser(1, "alice", token=TOKEN)])

    for _ in range(3):
        sync.targets_for(_definition(sync_to_users=["alice"]))

    assert account.user_reads == 1


def test_a_definition_naming_a_user_resolves_them_by_title_case_insensitively():
    """``title`` is the only field both kinds of user carry, and Plex is not
    case-consistent about display names -- the same casefold-both-sides rule
    the label ownership check learned."""
    sync, _ = _sync([FakeUser(7, "Alice", token=TOKEN)])

    targets, skips = sync.targets_for(_definition(sync_to_users=["alice"]))

    assert skips == []
    assert [(t.user_id, t.title) for t in targets] == [(7, "Alice")]


def test_an_unknown_name_is_skipped_by_name():
    sync, _ = _sync([FakeUser(1, "alice", token=TOKEN)])

    targets, skips = sync.targets_for(_definition(sync_to_users=["nobody"]))

    assert targets == []
    assert [(s.title, s.reason) for s in skips] == [
        ("nobody", "no such user on this account")
    ]


def test_the_owner_is_never_a_sync_target():
    """``account.users()`` does not contain the owner, so without this branch
    naming them would read as "no such user" -- true and unhelpful. Kometa
    reaches ``switchUser(admin)`` here and is non-destructive only because the
    NotFound it raises is swallowed."""
    sync, _ = _sync([FakeUser(1, "alice", token=TOKEN)], title="owner")

    targets, skips = sync.targets_for(_definition(sync_to_users=["Owner"]))

    assert targets == []
    assert skips[0].reason == (
        "the server owner is not a sync target; the admin playlist IS this "
        "definition"
    )


def test_a_pin_protected_user_is_skipped_before_any_plex_tv_call():
    """C13 A8. ``switchHomeUser`` without a PIN simply omits the parameter and
    leaves plex.tv to answer with a failure the pass would have to interpret;
    the flag check makes that unreachable. Proved by the counters: zero token
    reads, zero switches, zero connections."""
    kid = FakeUser(9, "Kids", home=True, protected=True)
    connector = Connector()
    sync, account = _sync([kid], connector=connector)

    targets, skips = sync.targets_for(_definition(sync_to_users=["Kids"]))

    assert targets == []
    assert skips[0].reason == (
        "PIN-protected; this service never holds a Plex PIN"
    )
    assert kid.token_reads == 0
    assert account.switches == []
    assert connector.calls == []


def test_all_resolves_to_every_user_minus_the_exclusions():
    """``all`` is the section-level word; ``exclude_users`` is its companion,
    because otherwise it is an all-or-nothing switch on an account that has a
    Kids profile in it."""
    sync, _ = _sync(
        [
            FakeUser(1, "alice", token=TOKEN),
            FakeUser(2, "bob", token=TOKEN),
            FakeUser(3, "Kids", token=TOKEN),
        ],
        config=_config(sync_all_users=True, exclude_users=["kids"]),
    )

    targets, skips = sync.targets_for(_definition(sync_to_users="all"))

    assert [t.title for t in targets] == ["alice", "bob"]
    assert skips == []


def test_a_duplicate_name_resolves_once():
    """A name written twice is one user; resolving them twice would plan two
    creates for one account."""
    sync, _ = _sync([FakeUser(1, "alice", token=TOKEN)])

    targets, _skips = sync.targets_for(
        _definition(sync_to_users=["alice", "Alice"])
    )

    assert [t.user_id for t in targets] == [1]


def test_an_excluded_name_written_explicitly_is_skipped_by_name():
    """``exclude_users`` is stronger than a definition naming somebody: it is
    the section-level statement that this deployment does not write into that
    account, and a silent drop would look like a typo in the definition."""
    sync, _ = _sync(
        [FakeUser(3, "Kids", token=TOKEN)],
        config=_config(exclude_users=["Kids"]),
    )

    targets, skips = sync.targets_for(_definition(sync_to_users=["Kids"]))

    assert targets == []
    assert skips[0].reason == "excluded by playlists.exclude_users"


# --- the owner pre-check -----------------------------------------------------


def test_a_non_owner_token_refuses_the_whole_fan_out():
    """C13 A9. "An account token" is not "this server's owner's account
    token", and without this the difference surfaces as a 401 partway through
    a fan-out that has already written to somebody."""
    sync, account = _sync([FakeUser(1, "alice", token=TOKEN)], owned=False)

    refusal = sync.owner_refusal()

    assert refusal is not None
    assert "not this server's owner" in refusal
    assert account.resource_reads == 1


def test_an_account_that_cannot_see_this_server_refuses_the_fan_out():
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], machine="some-other-machine"
    )

    refusal = sync.owner_refusal()

    assert refusal is not None
    assert "sees no server matching this one" in refusal


def test_the_owner_check_passes_for_the_owner():
    sync, _ = _sync([FakeUser(1, "alice", token=TOKEN)])

    assert sync.owner_refusal() is None


# --- minting a token, and refusing to ----------------------------------------


def test_an_empty_user_token_is_refused_and_never_becomes_a_plexserver():
    """The Gap-3 refusal, and the assertion that matters is the negative one:
    ``PlexServer`` is not constructed at all. plexapi's ``get_token`` returns
    ``None`` for a plex.tv outage and for a user with no share alike, and
    ``PlexServer(token=None)`` reads a token out of plexapi's own config file
    -- so a fallthrough here is a write under the wrong identity."""
    connector = Connector()
    sync, _ = _sync([FakeUser(4, "carol", token=None)], connector=connector)

    targets, _skips = sync.targets_for(_definition(sync_to_users=["carol"]))
    server, reason = sync.server_for(targets[0])

    assert server is None
    assert reason == "plex.tv returned no access token for this user"
    assert connector.calls == []


def test_a_share_token_is_preferred_over_the_home_switch():
    """Ordered by cost, not by the ``home`` flag: ``get_token`` is one plex.tv
    GET and the operator's probe found it answers for their Home users too,
    while the switch costs two (the POST plus the ``/api/v2/user`` GET inside
    the account constructor)."""
    connector = Connector()
    sync, account = _sync(
        [FakeUser(5, "dave", token=TOKEN, home=True)], connector=connector
    )

    targets, _skips = sync.targets_for(_definition(sync_to_users=["dave"]))
    server, reason = sync.server_for(targets[0])

    assert reason is None
    assert server.token == TOKEN
    assert account.switches == []


def test_a_home_user_without_a_share_token_is_switched_to_instead():
    """The fallback C13 A8 names, kept because an account whose Home users
    have no share entries is exactly the shape the ruling was written for."""
    connector = Connector()
    sync, account = _sync(
        [FakeUser(6, "erin", token=None, home=True)],
        connector=connector,
        home_tokens={6: TOKEN},
    )

    targets, _skips = sync.targets_for(_definition(sync_to_users=["erin"]))
    server, reason = sync.server_for(targets[0])

    assert reason is None
    assert server.token == TOKEN
    assert account.switches == [("erin", None)]


def test_a_failed_home_switch_is_a_refusal_not_a_fallthrough():
    connector = Connector()
    sync, _ = _sync(
        [FakeUser(6, "erin", token=None, home=True)],
        connector=connector,
        switch_raises=True,
    )

    targets, _skips = sync.targets_for(_definition(sync_to_users=["erin"]))
    server, reason = sync.server_for(targets[0])

    assert server is None
    assert reason == "plex.tv returned no access token for this user"
    assert connector.calls == []


def test_a_shared_user_whose_token_read_raises_is_refused_not_retried():
    """plexapi swallows this one itself, so ours is the second lock -- and a
    fake that raises is the only way to prove our own handler exists."""
    connector = Connector()
    sync, _ = _sync(
        [FakeUser(7, "frank", token=TOKEN, raises=True)], connector=connector
    )

    targets, _skips = sync.targets_for(_definition(sync_to_users=["frank"]))
    server, reason = sync.server_for(targets[0])

    assert server is None
    assert reason == "plex.tv returned no access token for this user"
    assert connector.calls == []


def test_a_connection_that_fails_is_reported_by_class_name_only():
    """A plexapi failure's message carries the URL it failed on and in some
    shapes the token; the class name is what reaches a report."""
    connector = Connector(raises_for=[TOKEN])
    sync, _ = _sync([FakeUser(1, "alice", token=TOKEN)], connector=connector)

    targets, _skips = sync.targets_for(_definition(sync_to_users=["alice"]))
    server, reason = sync.server_for(targets[0])

    assert server is None
    assert reason == "connecting to Plex as this user failed (RuntimeError)"
    assert TOKEN not in reason


def test_each_user_is_connected_to_once_per_pass():
    connector = Connector()
    sync, _ = _sync([FakeUser(1, "alice", token=TOKEN)], connector=connector)

    targets, _skips = sync.targets_for(_definition(sync_to_users=["alice"]))
    for _ in range(3):
        sync.playlists_for(targets[0])

    assert len(connector.calls) == 1
    assert connector.servers[TOKEN].listings == 1


# --- the token stays out of everything ---------------------------------------


def test_a_target_never_reprs_the_token():
    """``UserTarget`` is a frozen dataclass, so ``repr()`` of one is one
    f-string from every log line and every exception context. What it holds is
    a closure over the token, never the token -- the same shape and the same
    argument as ``SourceClients.plex_account``."""
    sync, _ = _sync([FakeUser(1, "alice", token=TOKEN)])

    targets, _skips = sync.targets_for(_definition(sync_to_users=["alice"]))

    assert TOKEN not in repr(targets[0])
    assert TOKEN not in repr(targets)
    assert TOKEN not in repr(sync)


def test_nothing_the_resolver_writes_or_serves_carries_a_user_token():
    """The assertion is over the FORMATTED output, not ``record.getMessage()``.

    ``getMessage()`` is ``msg % args`` and stops there, so a test written
    against it says nothing about what a handler actually emits. This attaches
    a real ``StreamHandler`` with a real ``Formatter`` to a ``StringIO`` and
    reads the bytes.

    **What is proved, and what is deliberately NOT.** Roadmap row 207
    adjudicated the sink question and closed it: the pod log is the TRUSTED
    sink -- ``exc_info`` tracebacks reach stdout whole, on purpose, because
    they are the compensating control other rows lean on -- and every SERVED
    surface carries redacted text. So this test does not claim the traceback
    is clean; the fakes plant a token in the exception MESSAGE precisely to
    make that traceback dirty, and a passing assertion over the whole stream
    would only mean the fixture had stopped testing anything.

    What it does claim, line by line:

    - the part of each record THIS SERVICE composes -- the ``%(message)s`` our
      format string produced -- names a user title and an exception class and
      carries no token. Those are the header lines, identifiable because our
      formatter puts the level name first and every traceback line begins with
      ``Traceback``, whitespace, or the exception's own class name;
    - every string the resolver hands BACK -- the skip reasons, the
      ``server_for`` reasons, the reprs -- is token-free. Those are the values
      that reach ``PlaylistRun.detail``, ``scheduled_runs.last_detail``,
      ``/api/snapshots`` and the preview response.

    A private ``StringIO`` handler rather than ``caplog``: caplog collects
    records, and the question here is what a HANDLER writes.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("autoposter.collections.playlist_users")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    connector = Connector(raises_for=[TOKEN])
    sync, _ = _sync(
        [
            FakeUser(1, "alice", token=TOKEN),
            FakeUser(2, "bob", token=None, home=True),
            FakeUser(3, "carol", token=TOKEN, raises=True),
        ],
        connector=connector,
    )

    try:
        targets, skips = sync.targets_for(
            _definition(sync_to_users=["alice", "bob", "carol"])
        )
        reasons = [sync.server_for(target)[1] for target in targets]
    finally:
        logger.removeHandler(handler)

    emitted = stream.getvalue()
    ours = [
        line for line in emitted.splitlines()
        if line.startswith(("DEBUG ", "INFO ", "WARNING ", "ERROR ", "CRITICAL "))
    ]

    assert ours, "the failures should have produced at least one line"
    # 1. every line this service composed.
    for line in ours:
        assert TOKEN not in line
    assert any("'alice'" in line for line in ours)
    # 2. and it is still worth reading -- the traceback under those lines is
    #    the whole point of logger.exception, and row 207 wants it there.
    assert "Traceback (most recent call last)" in emitted
    # 3. every string handed back to a caller, which is what gets SERVED.
    for reason in reasons:
        assert reason is None or TOKEN not in reason
    for skip in skips:
        assert TOKEN not in skip.reason
    assert TOKEN not in repr(targets)
    assert TOKEN not in repr(sync)
