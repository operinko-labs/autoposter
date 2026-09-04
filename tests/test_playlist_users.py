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

from sqlalchemy import select

from autoposter.collections.playlist_users import (
    UserSync,
    apply_user_sync,
    plan_user_sync,
    user_sweep_candidates,
)
from autoposter.config.schema import PlaylistDefinition, PlaylistsConfig
from autoposter.db.models import ManagedPlaylistUser

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


# --- planning the fan-out ----------------------------------------------------


def _row(session, *, user_id=1, title="alice", rating_key="7001", digest="old"):
    row = ManagedPlaylistUser(
        definition_key="Timeline", plex_user_id=user_id, plex_user_title=title,
        plex_rating_key=rating_key, definition_hash=digest,
    )
    session.add(row)
    return row


async def test_a_user_with_no_row_is_planned_as_a_create(session):
    """No row means no copy, whatever is in that account: ownership is the
    row, never the title."""
    connector = Connector()
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )
    definition = _definition(sync_to_users=["alice"])

    plan = await plan_user_sync(
        session, sync, config, [definition],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    assert plan.refusal is None
    assert [e.playlist for e in plan.plans] == [None]
    assert plan.writes == 1
    # ``previews`` are the "would" lines and ``actions`` the skips: a pass that
    # APPLIES reports what it did, not both.
    assert plan.actions == []
    assert plan.previews == ["'Timeline' -> 'alice': would create with 2 item(s)"]
    assert plan.results["Timeline"][0].creating == 2
    # Planning writes nothing. That is the property the caps depend on.
    assert connector.servers[TOKEN].created == []


async def test_a_user_whose_copy_is_behind_is_planned_as_a_diff(session):
    """The 98a subtraction, unchanged, one level down: member rating keys are
    server-wide, so an admin-fetched desired list compares correctly against a
    user-fetched membership."""
    copy = FakeUserPlaylist(7001, "Timeline", [ITEM_A, ITEM_C])
    connector = Connector(servers={TOKEN: FakeUserServer(TOKEN, [copy])})
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )
    _row(session)
    await session.commit()

    plan = await plan_user_sync(
        session, sync, config, [_definition(sync_to_users=["alice"])],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    entry = plan.plans[0]
    assert [i.ratingKey for i in entry.adding] == ["12"]
    assert [i.ratingKey for i in entry.removing] == ["13"]
    # One add BATCH plus one DELETE: removeItems issues one request per item.
    assert plan.writes == 2
    assert plan.actions == []
    assert plan.previews == ["'Timeline' -> 'alice': would update +1 -1"]
    assert copy.writes == []


async def test_a_user_whose_copy_is_current_costs_no_membership_read(session):
    """The gate that makes a steady-state pass cheap. The user's LISTING is
    read -- that is the ownership map and there is no way around it -- and
    their membership is not."""

    class Counting(FakeUserPlaylist):
        membership_reads = 0

        def items(self, libtype=None):
            type(self).membership_reads += 1
            return super().items(libtype)

    Counting.membership_reads = 0
    copy = Counting(7001, "Timeline", [ITEM_A, ITEM_B])
    connector = Connector(servers={TOKEN: FakeUserServer(TOKEN, [copy])})
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )
    _row(session, digest="hash-1")
    await session.commit()

    plan = await plan_user_sync(
        session, sync, config, [_definition(sync_to_users=["alice"])],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    assert plan.actions == []
    assert plan.previews == []
    assert plan.writes == 0
    assert [e.result for e in plan.plans] == [None]
    assert Counting.membership_reads == 0


async def test_a_same_title_playlist_in_a_users_account_is_never_ours(session):
    """The admin pass's refusal one level down, with a sharper reason: a
    playlist somebody made in their OWN account under our title is theirs.
    Plex permits duplicate titles, so creating ours beside it was available
    and is refused -- nobody's account should sprout a second playlist of the
    same name."""
    stranger = FakeUserPlaylist(5555, "Timeline", [ITEM_C])
    connector = Connector(servers={TOKEN: FakeUserServer(TOKEN, [stranger])})
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )

    plan = await plan_user_sync(
        session, sync, config, [_definition(sync_to_users=["alice"])],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    assert plan.plans == []
    assert plan.writes == 0
    assert plan.actions == [
        "'Timeline' -> 'alice': skipped: a playlist titled 'Timeline' already "
        "exists in this account and is not ours"
    ]
    assert stranger.writes == []


async def test_an_empty_desired_list_plans_nothing_for_anybody(session):
    """The empty-result law, one level down and for a bigger reason: a failed
    source returning nothing would, taken literally, empty seventeen people's
    playlists at once."""
    connector = Connector()
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )

    plan = await plan_user_sync(
        session, sync, config, [_definition(sync_to_users=["alice"])],
        {}, {},
    )

    assert plan.plans == []
    assert plan.actions == []
    assert plan.previews == []
    assert connector.calls == []


# --- applying it -------------------------------------------------------------


async def test_applying_creates_the_copy_under_that_users_own_server(session):
    connector = Connector()
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )
    plan = await plan_user_sync(
        session, sync, config, [_definition(sync_to_users=["alice"])],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    actions = await apply_user_sync(session, plan)

    created = connector.servers[TOKEN].created
    assert len(created) == 1
    assert created[0].title == "Timeline"
    assert [i.ratingKey for i in created[0].items()] == ["11", "12"]
    assert actions == ["'Timeline' -> 'alice': created with 2 item(s)"]

    row = (await session.execute(select(ManagedPlaylistUser))).scalar_one()
    assert row.definition_key == "Timeline"
    assert row.plex_user_id == 1
    assert row.plex_user_title == "alice"
    assert row.plex_rating_key == str(created[0].ratingKey)
    assert row.definition_hash == "hash-1"
    assert row.member_count == 2
    assert row.last_added == 2
    assert row.last_removed == 0


async def test_applying_diffs_rather_than_recreating_and_reloads_before_removing(
    session,
):
    """A8, one level down. The reload is mandatory: ``removeItems`` resolves
    each item through ``_getPlaylistItemID``, which walks the CACHED
    ``items()`` that the ``addItems`` above left in place."""
    copy = FakeUserPlaylist(7001, "Timeline", [ITEM_A, ITEM_C])
    connector = Connector(servers={TOKEN: FakeUserServer(TOKEN, [copy])})
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )
    _row(session)
    await session.commit()
    plan = await plan_user_sync(
        session, sync, config, [_definition(sync_to_users=["alice"])],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    actions = await apply_user_sync(session, plan)

    assert connector.servers[TOKEN].created == []
    assert copy.writes == [("add", ["12"]), ("remove", ["13"])]
    assert copy.reloads == 1
    assert copy.deleted is False
    assert actions == ["'Timeline' -> 'alice': updated +1 -1"]


async def test_a_user_copy_is_never_reordered(session):
    """The declared gap. The copy holds exactly the desired members in the
    wrong order and the definition is in sync mode, so the admin path would
    issue moves here; this one issues nothing, re-stamps the row so the next
    pass gates on the new hash instead of re-reading membership forever, and
    reports nothing -- exactly as the admin pass's own gated branch does."""
    copy = FakeUserPlaylist(7001, "Timeline", [ITEM_B, ITEM_A])
    connector = Connector(servers={TOKEN: FakeUserServer(TOKEN, [copy])})
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )
    row = _row(session)
    await session.commit()
    plan = await plan_user_sync(
        session, sync, config,
        [_definition(sync_to_users=["alice"], sync_mode="sync")],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    actions = await apply_user_sync(session, plan)

    assert copy.moves == []
    assert copy.writes == []
    assert actions == []
    assert plan.actions == []
    assert plan.previews == []
    assert row.definition_hash == "hash-1"


async def test_a_failure_against_one_user_does_not_stop_the_next(session):
    """Contained per user, and the row of the one that succeeded survives --
    which is why the flush is per entry rather than once at the end. The
    action names the exception's CLASS and nothing from its message, because
    a plexapi failure's message carries the URL and sometimes the token."""

    class Exploding(FakeUserServer):
        def createPlaylist(self, title, items=None, **kwargs):
            raise RuntimeError("https://plex.example/playlists?X-Plex-Token=SECRET")

    connector = Connector(servers={
        "tok-a": Exploding("tok-a"), "tok-b": FakeUserServer("tok-b"),
    })
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token="tok-a"), FakeUser(2, "bob", token="tok-b")],
        connector=connector, config=config,
    )
    plan = await plan_user_sync(
        session, sync, config,
        [_definition(sync_to_users=["alice", "bob"])],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    actions = await apply_user_sync(session, plan)

    assert actions == [
        "'Timeline' -> 'alice': failed (RuntimeError)",
        "'Timeline' -> 'bob': created with 2 item(s)",
    ]
    assert "SECRET" not in " ".join(actions)
    assert [r.failed for r in plan.results["Timeline"]] == [True, False]
    rows = (await session.execute(select(ManagedPlaylistUser))).scalars().all()
    assert [(r.plex_user_id, r.plex_user_title) for r in rows] == [(2, "bob")]


# --- the caps refuse entirely ------------------------------------------------


async def test_past_the_user_cap_nothing_is_minted_at_all(session):
    """The fan-out's WIDTH, and the load-bearing half is WHEN it is measured.

    An ``all`` that suddenly resolves to two hundred accounts must refuse
    before it has minted two hundred tokens and issued two hundred PMS
    listings. Nothing would have been WRITTEN either way -- but the recon's
    cost model is what the caps were written against, and spending most of it
    and then refusing keeps the letter of C13 A6 while losing its point. So
    ``max_users`` is evaluated against the union of every definition's targets,
    resolved from the cached ``account.users()`` list alone, before a single
    ``get_token`` call.

    The three counters are the whole assertion: ``token_reads`` is bumped by
    ``FakeUser.get_token``, ``connector.calls`` by every session opened, and
    ``account.user_reads`` proves the one read that IS made is the cached one.
    """
    connector = Connector()
    config = _config(sync_to_users_apply=True, max_users=3)
    users = [FakeUser(i, "u%d" % i, token="tok-%d" % i) for i in range(1, 5)]
    sync, account = _sync(users, connector=connector, config=config)
    plan = await plan_user_sync(
        session, sync, config,
        [_definition(sync_to_users=["u1", "u2", "u3", "u4"])],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    actions = await apply_user_sync(session, plan)

    assert plan.refusal == (
        "refusing the user fan-out: 4 user(s) resolved, more than the "
        "max_users cap of 3; nothing was written to any user and the "
        "admin playlists were reconciled"
    )
    assert actions == []
    # Not one token minted, not one session opened, not one listing read.
    assert [user.token_reads for user in users] == [0, 0, 0, 0]
    assert connector.calls == []
    assert connector.servers == {}
    assert account.user_reads == 1
    assert (await session.execute(select(ManagedPlaylistUser))).scalars().all() == []


async def test_past_the_write_cap_the_stored_rows_refuse_before_any_mint(session):
    """The fan-out's DEPTH, refused at the same point and from the same
    materials: the stored rows alone -- not the admin half's resolved items,
    which ``_estimated_writes`` never opens.

    Two users with no row is two creates whatever their accounts hold, so the
    estimate is exact here and the refusal costs nothing -- not a token, not a
    listing. Refusing ENTIRELY rather than spending the budget is
    ``cleanup.max_orphans``' rule: writing "the first one" of four would be the
    same accident spread over four passes.
    """
    connector = Connector()
    config = _config(sync_to_users_apply=True, max_user_writes=1)
    users = [FakeUser(1, "alice", token="tok-a"), FakeUser(2, "bob", token="tok-b")]
    sync, _ = _sync(users, connector=connector, config=config)
    plan = await plan_user_sync(
        session, sync, config, [_definition(sync_to_users=["alice", "bob"])],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    actions = await apply_user_sync(session, plan)

    assert plan.refusal == (
        "refusing the user fan-out: 2 write(s) planned, more than the "
        "max_user_writes cap of 1; nothing was written to any user and the "
        "admin playlists were reconciled"
    )
    assert actions == []
    assert [user.token_reads for user in users] == [0, 0]
    assert connector.calls == []


async def test_a_fan_out_the_stored_rows_underestimate_still_refuses_on_the_exact_count(
    session,
):
    """The write cap's SECOND evaluation, and why there are two.

    The pre-mint estimate is built from the stored rows alone -- it never
    reads the admin half's resolved item list, only whether a row is present
    and hash-fresh. It counts ONE write for a copy whose row is stale and
    cannot know how many REMOVALS that copy needs, because each removal is
    its own DELETE and how many there are lives only in that user's own
    membership. So the estimate
    under-counts here, the fan-out proceeds past the cheap gate, and the exact
    count taken from the listing is what refuses.

    One user, one stale row, a copy holding three items where the definition
    now resolves to one: estimate 1, which does not exceed a cap of 1; exact 2
    -- two DELETEs -- which does. And nothing is written, which is the half
    that makes it a refusal rather than a budget.
    """
    copy = FakeUserPlaylist(7001, "Timeline", [ITEM_A, ITEM_B, ITEM_C])
    connector = Connector(servers={TOKEN: FakeUserServer(TOKEN, [copy])})
    config = _config(sync_to_users_apply=True, max_user_writes=1)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )
    _row(session, digest="stale")
    await session.commit()

    plan = await plan_user_sync(
        session, sync, config, [_definition(sync_to_users=["alice"])],
        {"Timeline": [ITEM_A]}, {"Timeline": "hash-1"},
    )

    actions = await apply_user_sync(session, plan)

    assert plan.writes == 2
    assert plan.refusal == (
        "refusing the user fan-out: 2 write(s) planned, more than the "
        "max_user_writes cap of 1; nothing was written to any user and the "
        "admin playlists were reconciled"
    )
    assert actions == []
    # The listing WAS read -- that is what the cheap gate let through -- and
    # the copy was not touched, which is what the expensive one prevented.
    assert connector.calls == [(BASEURL, TOKEN)]
    assert copy.writes == []


# --- the token stays out of everything, part two -----------------------------


async def test_no_user_token_is_ever_persisted(session):
    """A full create is applied with the sentinel as the user's token. It must
    appear in no column of the row this pass wrote, in no action string, and
    in no repr of the plan the pass carried around."""
    connector = Connector()
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )
    plan = await plan_user_sync(
        session, sync, config, [_definition(sync_to_users=["alice"])],
        {"Timeline": [ITEM_A, ITEM_B]}, {"Timeline": "hash-1"},
    )

    actions = await apply_user_sync(session, plan)

    row = (await session.execute(select(ManagedPlaylistUser))).scalar_one()
    for column in row.__table__.columns.keys():
        assert TOKEN not in str(getattr(row, column))
    assert TOKEN not in " ".join(actions)
    assert TOKEN not in repr(plan)


# --- what the sweep may consider -------------------------------------------


async def test_a_row_no_configuration_asks_for_whose_object_is_live_is_a_candidate(
    session,
):
    """C13 A2's one predicate. This exercises the "user dropped from
    sync_to_users" event; the definition still exists and still syncs, just
    not to this person."""
    copy = FakeUserPlaylist(7001, "Timeline", [ITEM_A])
    connector = Connector(servers={TOKEN: FakeUserServer(TOKEN, [copy])})
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN), FakeUser(2, "bob", token=TOKEN)],
        connector=connector, config=config,
    )
    _row(session)
    await session.commit()

    candidates, unreachable = await user_sweep_candidates(
        session, sync, [_definition(sync_to_users=["bob"])]
    )

    assert unreachable == []
    assert [(row.plex_user_title, playlist is copy) for row, playlist, _t in candidates] == [
        ("alice", True)
    ]
    assert copy.deleted is False


async def test_a_row_whose_object_is_already_gone_is_not_a_candidate(session):
    """One level up's rule verbatim: there is nothing to delete, and reporting
    it as deletable would invite an operator to authorise a deletion that
    cannot happen."""
    connector = Connector(servers={TOKEN: FakeUserServer(TOKEN, [])})
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=TOKEN)], connector=connector, config=config
    )
    _row(session)
    await session.commit()

    candidates, unreachable = await user_sweep_candidates(session, sync, [])

    assert candidates == []
    assert unreachable == []


async def test_a_row_whose_user_is_unreachable_is_reported_rather_than_swept(
    session,
):
    """Ownership is unverifiable, so nothing is deleted on a guess -- and the
    row is returned separately so the caller can name it, because a row that
    silently accumulated would be a copy nobody could ever account for."""
    connector = Connector()
    config = _config(sync_to_users_apply=True)
    sync, _ = _sync(
        [FakeUser(1, "alice", token=None)], connector=connector, config=config
    )
    _row(session)
    await session.commit()

    candidates, unreachable = await user_sweep_candidates(session, sync, [])

    assert candidates == []
    assert [row.plex_user_title for row in unreachable] == ["alice"]
    assert connector.calls == []
