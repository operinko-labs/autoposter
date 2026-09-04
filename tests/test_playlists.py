"""The playlists pass, through its real entry point.

Nothing here calls a helper. The house rule this file exists to satisfy is the
standing one -- two same-branch defects where the helper tests passed and the
wired path differed -- so every gated behaviour is exercised as
``reconcile_playlists(...)`` with the real config object: gate off, gate on,
and a second pass.

The fakes stand in for plexapi classes this suite has never exercised before,
which is why ``tests/test_plexapi_playlist_contract.py`` was written first.
Every method on ``FakePlaylist`` is one that file pins on the real
``Playlist``, with the same argument names and the same caching behaviour --
in particular ``items()`` hands back the membership as it stands and
``removeItems``/``moveItem`` are the only things that consult it.
"""
import pytest
from sqlalchemy import select

# ``REGISTRY`` only. ``register()`` is deliberately NOT imported -- the fixture
# below writes the dict directly, for the reason the executing-agent note at the
# end of this plan gives -- and ``ruff``'s F401 is on (`select = ["E4","E7","E9","F"]`
# in pyproject.toml, and only tests/test_config_safety.py is per-file-ignored),
# so an import kept "for symmetry" fails the `ruff check src tests` gate.
from autoposter.collections.builders.base import BuilderResult, REGISTRY
from autoposter.collections.playlists import reconcile_playlists
from autoposter.config.schema import PlaylistsConfig
from autoposter.db.models import ManagedPlaylist, ManagedPlaylistUser

from autoposter.collections import playlist_presets
from autoposter.collections.playlist_presets import PlaylistPreset


# --- the plexapi stand-ins ---------------------------------------------------


class FakeGuid:
    def __init__(self, value):
        self.id = value


class FakeItem:
    def __init__(self, rating_key, guids=(), item_type="movie"):
        self.ratingKey = rating_key
        self.guids = [FakeGuid(g) for g in guids]
        self.type = item_type
        self.listType = "video"


class FakeField:
    """``plexapi.media.Field`` -- a name and whether Plex locked it.

    The lock is the marker the summary write leaves and the summary CLEAR
    reads: this service's every summary write locks the field, so an unlocked
    summary was never ours. Pinned on the real class in
    ``tests/test_plexapi_playlist_contract.py``
    (``test_a_summary_can_be_cleared_and_unlocked_through_the_same_call``).
    """

    def __init__(self, name, locked=True):
        self.name = name
        self.locked = locked


class FakePlaylist:
    def __init__(self, rating_key, title, items=(), summary=None, summary_locked=True):
        self.ratingKey = int(rating_key)
        self.title = title
        self.summary = summary
        # Plex fills ``fields`` from the object's XML; a playlist carrying a
        # summary this service wrote carries the lock with it. ``summary_locked``
        # is how a test says "an operator typed this one by hand" -- an unlocked
        # summary is never cleared.
        self.fields = [FakeField("summary", summary_locked)] if summary else []
        self.smart = False
        self.playlistType = "video"
        self._members = list(items)
        self.reloads = 0
        self.deleted = False
        self.deletes_raise = False
        self.moves = []
        self.writes = []

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
        self._members = [i for i in self._members if i is not item]
        position = 0 if after is None else self._members.index(after) + 1
        self._members.insert(position, item)
        return self

    def reload(self):
        self.reloads += 1
        return self

    def editSummary(self, summary, locked=True):
        # ``editField`` sends ``{'summary.value': value or '', 'summary.locked':
        # 1 if locked else 0}`` -- so a clear is ``editSummary("", locked=False)``
        # and it both empties the text and hands the field back to Plex.
        self.writes.append(("summary", summary))
        self.summary = summary or None
        self.fields = [FakeField("summary", locked)] if summary else []
        return self

    def delete(self):
        if self.deletes_raise:
            raise RuntimeError("https://plex.example/playlists/1?X-Plex-Token=SECRET")
        self.deleted = True


class FakeSection:
    def __init__(self, items, section_type="movie"):
        self._items = list(items)
        self.type = section_type

    def all(self):
        return list(self._items)

    def search(self, libtype=None):
        return list(self._items)


class FakeLibrary:
    def __init__(self, sections):
        self._sections = sections
        self.opened = []

    def section(self, name):
        self.opened.append(name)
        return self._sections[name]


MACHINE = "abc123machine"
BASEURL = "http://plex.example:32400"


class FakeServer:
    def __init__(self, sections, playlists=()):
        self.library = FakeLibrary(sections)
        self._playlists = list(playlists)
        self.created = []
        self.listings = 0
        # What ``switchUser`` itself reads, and what the per-user stage builds
        # each user's session from.
        self.machineIdentifier = MACHINE
        self._baseurl = BASEURL

    def playlists(self, **kwargs):
        self.listings += 1
        return list(self._playlists)

    def createPlaylist(self, title, items=None, **kwargs):
        playlist = FakePlaylist(9000 + len(self.created), title, items or [])
        self.created.append(playlist)
        self._playlists.append(playlist)
        return playlist


class FakeUser:
    """``plexapi.myplex.MyPlexUser`` -- one row of ``account.users()``."""

    def __init__(self, user_id, title, token=None, protected=False):
        self.id = user_id
        self.title = title
        self.home = False
        self.protected = protected
        self.restricted = "0"
        self._token = token

    def get_token(self, machineIdentifier):
        return self._token if machineIdentifier == MACHINE else None


class FakeResource:
    def __init__(self, client_identifier, owned=True):
        self.clientIdentifier = client_identifier
        self.owned = owned


class FakeAccount:
    """``plexapi.myplex.MyPlexAccount`` -- the owner's. Counts its own reads,
    because "a deployment that syncs to nobody makes no plex.tv call" is a
    property only a counter can prove."""

    def __init__(self, users, owned=True):
        self._users = list(users)
        self._owned = owned
        self.title = "owner"
        self.username = "owner@example.com"
        self.reads = 0

    def users(self):
        self.reads += 1
        return list(self._users)

    def resources(self):
        self.reads += 1
        return [FakeResource(MACHINE, self._owned)]


def _user_servers(tokens):
    """``(connect, {token: FakeServer})`` -- the injected per-user seam.

    Each user's server is a ``FakeServer`` with its own empty section map: the
    per-user stage never reads a library through it, only ``playlists()`` and
    ``createPlaylist()``.
    """
    servers = {token: FakeServer({}) for token in tokens}

    def connect(baseurl, token):
        assert baseurl == BASEURL
        return servers[token]

    return connect, servers


def _sources(account):
    from autoposter.collections.builders.base import SourceClients

    return SourceClients(plex_account=lambda: account)


# --- the builder the definitions point at ------------------------------------


class _Ids:
    """A registered builder whose output the test sets."""

    type_name = "playlist_test_ids"
    ids: list = []
    raises = False

    async def build(self, ctx):
        if type(self).raises:
            raise RuntimeError("https://provider.example/list?apikey=SECRET")
        return BuilderResult(ids=list(type(self).ids))


@pytest.fixture(autouse=True)
def a_registered_builder():
    builder = _Ids()
    _Ids.ids = []
    _Ids.raises = False
    REGISTRY[builder.type_name] = builder
    try:
        yield builder
    finally:
        REGISTRY.pop(builder.type_name, None)


# --- the fixtures the tests build on -----------------------------------------


MOVIE_A = FakeItem("11", ["tmdb://1"])
MOVIE_B = FakeItem("12", ["tmdb://2"])
SHOW_A = FakeItem("21", ["tmdb://1", "tvdb://9"], item_type="show")


def _server(playlists=()):
    return FakeServer(
        {
            "Movies": FakeSection([MOVIE_A, MOVIE_B]),
            "TV Shows": FakeSection([SHOW_A], section_type="show"),
        },
        playlists=playlists,
    )


def _config(config_factory, **playlists):
    config = config_factory()
    config.playlists = PlaylistsConfig.model_validate(playlists)
    return config


def _definition(**overrides):
    return {
        "title": "Timeline",
        "builder": "playlist_test_ids",
        "params": {},
        **overrides,
    }


# --- the entry-point law: off, on, again -------------------------------------


async def test_the_gate_off_writes_nothing_and_says_what_it_would_do(
    session, config_factory
):
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    server = _server()
    config = _config(config_factory, definitions=[_definition()])

    run = await reconcile_playlists(session, server, config)

    assert server.created == []
    assert [r.title for r in run.playlists] == ["Timeline"]
    assert run.playlists[0].actions == [
        "would create 'Timeline' with 2 item(s) from Movies, TV Shows"
    ]
    rows = (await session.execute(select(ManagedPlaylist))).scalars().all()
    assert rows == []


async def test_the_gate_on_creates_the_playlist_and_records_the_row(
    session, config_factory
):
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    server = _server()
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    run = await reconcile_playlists(session, server, config)

    assert len(server.created) == 1
    created = server.created[0]
    assert created.title == "Timeline"
    assert [i.ratingKey for i in created.items()] == ["11", "12"]
    assert run.playlists[0].actions == ["created 'Timeline' with 2 item(s)"]

    row = (await session.execute(select(ManagedPlaylist))).scalar_one()
    assert row.title == "Timeline"
    assert row.plex_rating_key == str(created.ratingKey)
    assert row.libraries == ["Movies", "TV Shows"]
    assert row.member_count == 2
    assert row.last_added == 2
    assert row.last_removed == 0


async def test_a_second_pass_with_the_same_definition_writes_nothing(
    session, config_factory
):
    """The hash short-circuit, proved with a sentinel that cannot be
    reproduced: the second pass is handed a playlist whose write methods would
    record any call, and the assertion is that none was made."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    server = _server()
    await reconcile_playlists(session, server, config)
    created = server.created[0]
    created.writes.clear()
    created.moves.clear()
    created.reloads = 0

    second = FakeServer(
        {
            "Movies": FakeSection([MOVIE_A, MOVIE_B]),
            "TV Shows": FakeSection([SHOW_A], section_type="show"),
        },
        playlists=[created],
    )
    run = await reconcile_playlists(session, second, config)

    assert second.created == []
    assert created.writes == []
    assert created.moves == []
    assert run.playlists[0].actions == []


# --- ownership, both directions ---------------------------------------------


async def test_a_same_title_playlist_we_did_not_create_is_never_ours(
    session, config_factory
):
    """A2, the first direction. Title match alone is never ownership, so the
    stranger's playlist is neither adopted nor overwritten -- and no second
    playlist is created beside it, which would be the other way to get this
    wrong."""
    _Ids.ids = [("tmdb", "1")]
    stranger = FakePlaylist(5555, "Timeline", [MOVIE_B])
    server = _server(playlists=[stranger])
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    run = await reconcile_playlists(session, server, config)

    assert server.created == []
    assert stranger.writes == []
    assert [i.ratingKey for i in stranger.items()] == ["12"]
    assert run.playlists[0].actions == [
        "'Timeline' already exists on this server and is not ours -- no "
        "managed_playlists row names it. Leaving it untouched; rename it, or "
        "rename this definition"
    ]
    assert (await session.execute(select(ManagedPlaylist))).scalars().all() == []


async def test_one_of_ours_that_was_renamed_in_plex_is_still_ours(
    session, config_factory
):
    """A2, the second direction. A rename does not move the rating key, so the
    row still names the object -- and the rename is REPORTED rather than
    reverted: the title an operator typed into Plex is not this pass's to
    overwrite, and 98a has no adoption ceremony that would justify it."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    server = _server()
    await reconcile_playlists(session, server, config)
    ours = server.created[0]
    ours.title = "Timeline (mine now)"
    ours.writes.clear()

    _Ids.ids = [("tmdb", "1")]
    second = FakeServer(
        {
            "Movies": FakeSection([MOVIE_A, MOVIE_B]),
            "TV Shows": FakeSection([SHOW_A], section_type="show"),
        },
        playlists=[ours],
    )
    run = await reconcile_playlists(session, second, config)

    assert second.created == []
    assert [i.ratingKey for i in ours.items()] == ["11"]
    assert any("is titled 'Timeline (mine now)' in Plex" in a
               for a in run.playlists[0].actions)


async def test_a_stale_row_whose_playlist_is_gone_is_recreated(
    session, config_factory
):
    """Ours was deleted in Plex. The row's rating key names nothing on the
    server, so there is no owned playlist -- and no stranger holding the title
    either, so the honest answer is to build it again and re-point the row."""
    _Ids.ids = [("tmdb", "1")]
    session.add(ManagedPlaylist(
        title="Timeline", plex_rating_key="404404", definition_hash="stale",
        libraries=["Movies"],
    ))
    await session.flush()
    server = _server()
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    await reconcile_playlists(session, server, config)

    assert len(server.created) == 1
    row = (await session.execute(select(ManagedPlaylist))).scalar_one()
    assert row.plex_rating_key == str(server.created[0].ratingKey)


# --- the diff, and the order ------------------------------------------------


async def test_an_update_adds_removes_and_reorders_by_diff_only(
    session, config_factory
):
    """A8. The playlist is brought in line by deltas, never rebuilt -- and
    ``reload()`` is called before ``removeItems`` because the removal resolves
    each item through the CACHED membership (pinned in the contract file)."""
    _Ids.ids = [("tmdb", "2"), ("tmdb", "1")]
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    ours = FakePlaylist(6001, "Timeline", [MOVIE_A, SHOW_A])
    session.add(ManagedPlaylist(
        title="Timeline", plex_rating_key="6001", definition_hash="old",
        libraries=["Movies", "TV Shows"],
    ))
    await session.flush()
    server = _server(playlists=[ours])

    run = await reconcile_playlists(session, server, config)

    kinds = [kind for kind, _ in ours.writes]
    assert kinds == ["add", "remove"]
    assert ours.reloads >= 1
    assert [i.ratingKey for i in ours.items()] == ["12", "11"]
    assert any("updated 'Timeline': +1 -1" in a for a in run.playlists[0].actions)


async def test_append_mode_never_removes_and_never_reorders(session, config_factory):
    _Ids.ids = [("tmdb", "2")]
    config = _config(
        config_factory, apply_to_plex=True,
        definitions=[_definition(sync_mode="append")],
    )
    ours = FakePlaylist(6002, "Timeline", [SHOW_A])
    session.add(ManagedPlaylist(
        title="Timeline", plex_rating_key="6002", definition_hash="old",
        libraries=["Movies", "TV Shows"],
    ))
    await session.flush()

    await reconcile_playlists(session, _server(playlists=[ours]), config)

    assert [kind for kind, _ in ours.writes] == ["add"]
    assert ours.moves == []
    assert [i.ratingKey for i in ours.items()] == ["21", "12"]


async def test_a_deleted_summary_is_cleared_in_plex_and_only_once(
    session, config_factory
):
    """Roadmap row 187's semantics, ported to the playlist side.

    ``_members_hash`` puts ``summary or ""`` in its payload, so DELETING a
    ``summary:`` from a definition changes the hash and reaches the update
    branch -- where, with no clear, ``if definition.summary`` is false, nothing
    is written, and the NEW hash is stored anyway. Every later pass then
    short-circuits on it and the summary Plex holds is the old one forever: a
    setting that reads as applied and silently is not, which is the exact
    failure ``_REFUSED_PLAYLIST_FIELDS`` exists to prevent, reappearing on a
    field this section does offer.

    The clear is licensed here in a way it is not on the collections side.
    There, ``summary_asserted`` exists because an absent effective summary may
    be a ``tmdb_summary:`` pull that FAILED, and clearing on that fallback
    would wipe what the last healthy pass wrote. A playlist definition has no
    such fallback -- ``tmdb_summary`` is in ``_REFUSED_PLAYLIST_FIELDS`` -- so
    an absent ``summary`` is always the definition asserting there is none. The
    LOCK is what still gates the write: this service locks every summary it
    sets, so an unlocked one was never ours.
    """
    _Ids.ids = [("tmdb", "1")]
    server = _server()
    await reconcile_playlists(session, server, _config(
        config_factory, apply_to_plex=True,
        definitions=[_definition(summary="The films, in release order.")],
    ))
    ours = server.created[0]
    assert ours.summary == "The films, in release order."
    ours.writes.clear()

    # The same definition with `summary:` deleted.
    without = _config(config_factory, apply_to_plex=True, definitions=[_definition()])
    second = FakeServer(
        {
            "Movies": FakeSection([MOVIE_A, MOVIE_B]),
            "TV Shows": FakeSection([SHOW_A], section_type="show"),
        },
        playlists=[ours],
    )
    run = await reconcile_playlists(session, second, without)

    assert ours.summary is None
    assert ours.writes == [("summary", "")]
    assert ours.fields == [], "the clear must hand the field back to Plex, unlocked"
    assert any("cleared the summary of 'Timeline'" in a
               for a in run.playlists[0].actions)

    # Once, not every pass: the stored hash now matches, and the field is
    # unlocked anyway, so neither route can write again.
    ours.writes.clear()
    third = FakeServer(
        {
            "Movies": FakeSection([MOVIE_A, MOVIE_B]),
            "TV Shows": FakeSection([SHOW_A], section_type="show"),
        },
        playlists=[ours],
    )
    await reconcile_playlists(session, third, without)

    assert ours.writes == []


async def test_a_summary_the_operator_typed_by_hand_is_never_cleared(
    session, config_factory
):
    """The other half of the lock. An UNLOCKED summary is one this service never
    wrote, so the clear above leaves it exactly where it is -- the same
    narrowing ``reconcile._clear_collection_summary`` makes, for the same
    reason."""
    _Ids.ids = [("tmdb", "1")]
    ours = FakePlaylist(
        6005, "Timeline", [MOVIE_A], summary="Typed in Plex", summary_locked=False
    )
    session.add(ManagedPlaylist(
        title="Timeline", plex_rating_key="6005", definition_hash="old",
        libraries=["Movies"],
    ))
    await session.flush()
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    run = await reconcile_playlists(session, _server(playlists=[ours]), config)

    assert ours.summary == "Typed in Plex"
    assert ours.writes == []
    assert not any("cleared the summary" in a for a in run.playlists[0].actions)


async def test_the_libraries_are_searched_in_the_order_the_definition_names(
    session, config_factory
):
    """A3, through the real pass: ``tmdb://1`` is owned by BOTH libraries, and
    which item lands in the playlist is decided by the order written."""
    _Ids.ids = [("tmdb", "1")]
    config = _config(
        config_factory, apply_to_plex=True,
        definitions=[_definition(libraries=["TV Shows", "Movies"])],
    )

    await reconcile_playlists(session, (server := _server()), config)

    assert [i.ratingKey for i in server.created[0].items()] == ["21"]


async def test_the_owned_index_is_built_once_per_library_per_pass(
    session, config_factory
):
    """Two definitions over the same two libraries pay for two
    ``section.all()`` walks, not four -- the index cache is the pass's, not the
    definition's. ``build_owned_index`` was measured at 1,954 movies in 2.8
    seconds against production, so this is a real cost, not a micro-optimisation."""
    _Ids.ids = [("tmdb", "1")]
    config = _config(
        config_factory, apply_to_plex=True,
        definitions=[_definition(), _definition(title="Timeline Two")],
    )
    server = _server()

    await reconcile_playlists(session, server, config)

    assert server.library.opened.count("Movies") == 1
    assert server.library.opened.count("TV Shows") == 1
    assert server.listings == 1


# --- the laws carried over from the collections engine -----------------------


async def test_an_empty_source_leaves_a_live_playlist_untouched(
    session, config_factory
):
    _Ids.ids = []
    ours = FakePlaylist(6003, "Timeline", [MOVIE_A])
    session.add(ManagedPlaylist(
        title="Timeline", plex_rating_key="6003", definition_hash="old",
        libraries=["Movies"],
    ))
    await session.flush()
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    run = await reconcile_playlists(session, _server(playlists=[ours]), config)

    assert ours.writes == []
    assert run.playlists[0].actions == [
        "'Timeline': source returned no items; leaving the playlist untouched"
    ]


async def test_a_failed_builder_is_contained_and_never_echoes_its_message(
    session, config_factory, caplog
):
    """The containment invariant. The builder raises with a URL carrying a
    credential in the message; nothing derived from that exception may reach an
    action string, and the empty result it is replaced with is what the law
    above reads as "change nothing"."""
    _Ids.raises = True
    ours = FakePlaylist(6004, "Timeline", [MOVIE_A])
    session.add(ManagedPlaylist(
        title="Timeline", plex_rating_key="6004", definition_hash="old",
        libraries=["Movies"],
    ))
    await session.flush()
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    run = await reconcile_playlists(session, _server(playlists=[ours]), config)

    assert run.playlists[0].failed is True
    assert run.failures == ["Timeline"]
    assert ours.writes == []
    for action in run.playlists[0].actions + run.actions:
        assert "SECRET" not in action
        assert "http" not in action
    assert "SECRET" not in run.detail


async def test_a_schedule_gate_skips_without_touching_the_playlist(
    session, config_factory
):
    _Ids.ids = [("tmdb", "1")]
    config = _config(
        config_factory, apply_to_plex=True,
        definitions=[_definition(schedule={"every_n_runs": 2})],
    )

    run = await reconcile_playlists(session, (server := _server()), config, run_index=1)

    assert server.created == []
    assert run.playlists[0].skipped is True
    assert run.playlists[0].failed is False


async def test_the_limit_is_applied_after_resolution(session, config_factory):
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    config = _config(
        config_factory, apply_to_plex=True, definitions=[_definition(limit=1)]
    )

    await reconcile_playlists(session, (server := _server()), config)

    assert [i.ratingKey for i in server.created[0].items()] == ["11"]


# --- A7's real refusal point -------------------------------------------------


async def test_a_non_video_library_is_refused_before_the_builder_runs(
    session, config_factory
):
    """The config holds library NAMES and nothing that says a name is a Music
    section, so this cannot be a config-load refusal. It is the earliest point
    that CAN know -- the section is opened, its type is read, and the
    definition refuses BEFORE the builder is asked for anything and before any
    Plex write."""
    _Ids.ids = [("tmdb", "1")]
    server = FakeServer({
        "Movies": FakeSection([MOVIE_A]),
        "Music": FakeSection([], section_type="artist"),
    })
    config = config_factory()
    config.collections.libraries = ["Movies", "Music"]
    config.playlists = PlaylistsConfig.model_validate({
        "apply_to_plex": True,
        "definitions": [_definition(libraries=["Movies", "Music"])],
    })

    run = await reconcile_playlists(session, server, config)

    assert server.created == []
    assert run.playlists[0].failed is True
    action = run.playlists[0].actions[0]
    assert "'Music'" in action
    assert "artist" in action
    assert "Can not mix media types when building a playlist" in action


async def test_a_movie_library_and_a_show_library_may_share_one_playlist(
    session, config_factory
):
    """The other half of the same fact: Movie, Show, Season and Episode all
    answer ``listType == 'video'``, so mixing THOSE is fine and is not refused."""
    _Ids.ids = [("tmdb", "2"), ("tvdb", "9")]
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    await reconcile_playlists(session, (server := _server()), config)

    assert [i.ratingKey for i in server.created[0].items()] == ["12", "21"]


async def test_episode_level_members_are_refused_against_a_non_show_library(
    session, config_factory
):
    """``engine._run_one``'s SECOND post-level guard, ported with the first.

    ``builder_level: episode`` resolves against ``build_owned_index(section,
    "episode")``, which is ``section.search(libtype="episode")`` -- a Movie
    library answers that with nothing at all. Without this refusal the pass
    reaches the empty-result law and reports "source returned no items;
    leaving the playlist untouched": a SOURCE diagnosis for a SCOPE mistake,
    which sends the operator to look at the list instead of at
    ``libraries:``.

    Scoped-library-wise rather than per-pass, because a playlist names several
    and the engine's version only ever sees one: every library in scope is
    checked, and any that is not a Show library refuses the whole definition
    by name."""
    _Ids.ids = [("tmdb", "1")]
    config = _config(
        config_factory, apply_to_plex=True,
        definitions=[_definition(builder_level="episode",
                                 libraries=["TV Shows", "Movies"])],
    )

    run = await reconcile_playlists(session, (server := _server()), config)

    assert server.created == []
    assert run.playlists[0].failed is True
    assert run.playlists[0].skipped is True
    action = run.playlists[0].actions[0]
    assert "episode-level members exist only in a Show library" in action
    assert "'Movies' (a Movie library)" in action
    assert "'TV Shows'" not in action, (
        "the Show library in scope is fine and must not be named as the problem"
    )


async def test_episode_level_members_are_allowed_when_every_library_is_a_show_one(
    session, config_factory
):
    """The other half: the refusal above is about SCOPE, not about
    ``builder_level`` itself, so a definition narrowed to Show libraries builds
    normally."""
    _Ids.ids = [("tvdb", "9")]
    config = _config(
        config_factory, apply_to_plex=True,
        definitions=[_definition(builder_level="episode", libraries=["TV Shows"])],
    )

    run = await reconcile_playlists(session, (server := _server()), config)

    assert run.playlists[0].failed is False
    assert [i.ratingKey for i in server.created[0].items()] == ["21"]


# --- the sweep ---------------------------------------------------------------


async def test_the_sweep_reports_an_orphan_while_delete_unconfigured_is_off(
    session, config_factory
):
    orphan = FakePlaylist(7001, "Gone", [MOVIE_A])
    session.add(ManagedPlaylist(
        title="Gone", plex_rating_key="7001", definition_hash="x", libraries=["Movies"],
    ))
    await session.flush()
    config = _config(config_factory, apply_to_plex=True, definitions=[])

    run = await reconcile_playlists(session, _server(playlists=[orphan]), config)

    assert orphan.deleted is False
    assert any(
        "set playlists.delete_unconfigured to delete it" in a for a in run.actions
    )
    assert (await session.execute(select(ManagedPlaylist))).scalars().all() != []


async def test_the_sweep_deletes_when_opted_in_and_leaves_an_audit_row(
    session, config_factory
):
    from autoposter.db.models import EventLog

    orphan = FakePlaylist(7002, "Gone", [MOVIE_A])
    session.add(ManagedPlaylist(
        title="Gone", plex_rating_key="7002", definition_hash="x", libraries=["Movies"],
    ))
    await session.flush()
    config = _config(
        config_factory, apply_to_plex=True, delete_unconfigured=True, definitions=[]
    )

    await reconcile_playlists(session, _server(playlists=[orphan]), config)

    assert orphan.deleted is True
    assert (await session.execute(select(ManagedPlaylist))).scalars().all() == []
    event = (await session.execute(
        select(EventLog).where(EventLog.event_type == "playlist_deleted")
    )).scalar_one()
    assert event.payload["title"] == "Gone"
    assert event.payload["rating_key"] == "7002"
    assert event.payload["plex_title"] == "Gone"


async def test_the_sweep_names_the_live_title_when_ours_was_renamed_in_plex(
    session, config_factory
):
    """The report and the audit row must name the playlist that actually went.

    A rename in Plex is reported and never reverted, and ``row.title`` is the
    table's unique key so it cannot move -- so once the definition is removed,
    the row says one thing and the object on the server says another. For the
    one irreversible action this phase performs, naming only the row's title
    would leave an audit trail for a playlist that was never on the server
    under that name. Both are named."""
    from autoposter.db.models import EventLog

    orphan = FakePlaylist(7004, "Gone (mine now)", [MOVIE_A])
    session.add(ManagedPlaylist(
        title="Gone", plex_rating_key="7004", definition_hash="x", libraries=["Movies"],
    ))
    await session.flush()
    config = _config(
        config_factory, apply_to_plex=True, delete_unconfigured=True, definitions=[]
    )

    run = await reconcile_playlists(session, _server(playlists=[orphan]), config)

    assert orphan.deleted is True
    assert any("'Gone' (titled 'Gone (mine now)' in Plex)" in a for a in run.actions)
    event = (await session.execute(
        select(EventLog).where(EventLog.event_type == "playlist_deleted")
    )).scalar_one()
    assert event.payload["title"] == "Gone"
    assert event.payload["plex_title"] == "Gone (mine now)"
    assert event.payload["rating_key"] == "7004"


async def test_a_delete_that_raised_fails_the_pass(session, config_factory):
    """A raised delete used to leave ``failed`` at its default, so
    ``PlaylistRun.failures`` stayed empty, the job raised nothing and
    ``scheduled_runs.last_status`` recorded ``ok`` for a pass in which an
    irreversible operation errored. ``service.reconcile_libraries``' own
    per-library handler is the precedent: it appends an outcome carrying an
    error, and that is exactly what makes ``ReconcileResult.failed`` true.

    The row survives, because the object did -- the next pass finds it again
    and reports it again."""
    orphan = FakePlaylist(7005, "Gone", [MOVIE_A])
    orphan.deletes_raise = True
    session.add(ManagedPlaylist(
        title="Gone", plex_rating_key="7005", definition_hash="x", libraries=["Movies"],
    ))
    await session.flush()
    config = _config(
        config_factory, apply_to_plex=True, delete_unconfigured=True, definitions=[]
    )

    run = await reconcile_playlists(session, _server(playlists=[orphan]), config)

    assert orphan.deleted is False
    assert run.failed is True
    assert run.failures == ["Gone"]
    assert (await session.execute(select(ManagedPlaylist))).scalars().all() != []
    # The failure carried a URL with a token in its message; nothing derived
    # from it may reach an action string or the served detail.
    for action in run.actions:
        assert "SECRET" not in action
        assert "http" not in action
    assert "SECRET" not in run.detail


async def test_past_the_cap_the_sweep_refuses_entirely(session, config_factory):
    """Deleting "the first one" of three would be the same accident spread over
    passes -- the ``cleanup.max_orphans`` precedent, and the collections sweep's
    own rule."""
    orphans = [FakePlaylist(7100 + n, "Gone %d" % n, [MOVIE_A]) for n in range(3)]
    for orphan in orphans:
        session.add(ManagedPlaylist(
            title=orphan.title, plex_rating_key=str(orphan.ratingKey),
            definition_hash="x", libraries=["Movies"],
        ))
    await session.flush()
    config = _config(
        config_factory, apply_to_plex=True, delete_unconfigured=True,
        max_deletes=2, definitions=[],
    )

    run = await reconcile_playlists(session, _server(playlists=orphans), config)

    assert [o.deleted for o in orphans] == [False, False, False]
    assert any(
        "refusing to delete 3 unconfigured playlist(s): more than the "
        "max_deletes cap of 2" in a
        for a in run.actions
    )


async def test_a_row_whose_playlist_is_gone_is_not_a_sweep_candidate(
    session, config_factory
):
    """The object is already gone; there is nothing to delete and nothing to
    report as deletable. The row stays, and a definition pointed back at that
    title re-points it (see the stale-row test above)."""
    session.add(ManagedPlaylist(
        title="Gone", plex_rating_key="999999", definition_hash="x", libraries=["Movies"],
    ))
    await session.flush()
    config = _config(
        config_factory, apply_to_plex=True, delete_unconfigured=True, definitions=[]
    )

    run = await reconcile_playlists(session, _server(), config)

    assert run.actions == []
    assert (await session.execute(select(ManagedPlaylist))).scalars().all() != []


async def test_the_sweep_does_not_run_against_a_filtered_subset(
    session, config_factory
):
    """``title=`` narrows the pass to one definition, and against a subset every
    definition left out looks unaccounted for -- the same rule
    ``engine.run_library``'s ``sweep`` argument encodes."""
    orphan = FakePlaylist(7003, "Gone", [MOVIE_A])
    session.add(ManagedPlaylist(
        title="Gone", plex_rating_key="7003", definition_hash="x", libraries=["Movies"],
    ))
    await session.flush()
    _Ids.ids = [("tmdb", "1")]
    config = _config(
        config_factory, apply_to_plex=True, delete_unconfigured=True,
        definitions=[_definition()],
    )

    run = await reconcile_playlists(session, _server(playlists=[orphan]), config,
                                    title="Timeline")

    assert orphan.deleted is False
    assert not any("Gone" in a for a in run.actions)


# --- the wiring: the pass runs where the collections pass runs ---------------


async def test_the_scheduled_job_runs_the_playlists_half_after_the_collections_half(
    session_factory, config_factory, monkeypatch
):
    """The sibling relationship, asserted at the seam that has it. Ordering
    matters: the playlists pass reads the same libraries the collections pass
    just walked, and a failure in either must not stop the other from being
    attempted."""
    from autoposter.config.holder import ConfigHolder
    from autoposter.scheduler import jobs as jobs_module

    from autoposter.collections.playlists import PlaylistRun

    calls: list[str] = []

    async def fake_libraries(session, server, config, http, **kwargs):
        from autoposter.collections.service import ReconcileResult

        calls.append("collections")
        return ReconcileResult()

    async def fake_playlists(session, server, config, http=None, **kwargs):
        calls.append("playlists")
        return PlaylistRun()

    monkeypatch.setattr(jobs_module, "reconcile_libraries", fake_libraries)
    monkeypatch.setattr(jobs_module, "reconcile_playlists", fake_playlists)

    config = config_factory()
    job = jobs_module.make_collections_job(
        ConfigHolder(config), lambda: object(), http=None,
    )
    async with session_factory() as session:
        summary = await job.run(session)

    assert calls == ["collections", "playlists"]
    assert "playlists: 0 action(s)" in summary


async def test_playlists_still_run_when_collections_are_switched_off(
    session_factory, config_factory, monkeypatch
):
    """Two switches, two answers, INSIDE the job body.

    Skipping the playlists half because ``collections.enabled`` is off would be
    a setting that reads as configured and silently is not -- the exact failure
    this codebase's refusal tables exist to prevent.

    This test proves only the body. Whether the job EXISTS at all in that
    configuration is decided one level up, in ``app.py``, and monkeypatching
    ``jobs_module`` cannot see it -- which is precisely the standing lesson
    (two same-branch defects where the helper tests passed and the wired path
    differed). ``tests/test_app.py``'s
    ``test_the_collections_job_is_registered_for_playlists_alone`` is the
    other half, and it drives the real lifespan."""
    from autoposter.config.holder import ConfigHolder
    from autoposter.scheduler import jobs as jobs_module

    calls: list[str] = []

    async def fake_libraries(session, server, config, http, **kwargs):
        calls.append("collections")
        raise AssertionError("the collections half must not run")

    async def fake_playlists(session, server, config, http=None, **kwargs):
        from autoposter.collections.playlists import PlaylistRun

        calls.append("playlists")
        return PlaylistRun()

    monkeypatch.setattr(jobs_module, "reconcile_libraries", fake_libraries)
    monkeypatch.setattr(jobs_module, "reconcile_playlists", fake_playlists)

    config = config_factory()
    config.collections.enabled = False
    job = jobs_module.make_collections_job(
        ConfigHolder(config), lambda: object(), http=None,
    )
    async with session_factory() as session:
        summary = await job.run(session)

    assert calls == ["playlists"]
    assert "collections disabled" in summary


async def test_both_switched_off_is_one_honest_skip(
    session_factory, config_factory, monkeypatch
):
    from autoposter.config.holder import ConfigHolder
    from autoposter.scheduler import jobs as jobs_module

    async def refuse(*args, **kwargs):
        raise AssertionError("nothing should run")

    monkeypatch.setattr(jobs_module, "reconcile_libraries", refuse)
    monkeypatch.setattr(jobs_module, "reconcile_playlists", refuse)

    config = config_factory()
    config.collections.enabled = False
    config.playlists.enabled = False
    job = jobs_module.make_collections_job(
        ConfigHolder(config), lambda: object(), http=None,
    )
    async with session_factory() as session:
        summary = await job.run(session)

    assert summary == "skipped: collections and playlists disabled"


def test_the_job_set_did_not_grow_a_new_scheduled_job():
    """The playlists pass rides the existing ``collections_reconcile`` job. If
    that ever changes, ``SCHEDULED_JOB_NAMES`` and the agreement guard in
    ``tests/test_api_scheduled_runs.py`` both have to move with it -- and this
    is the test that says so out loud."""
    from autoposter.api.routes import SCHEDULED_JOB_NAMES

    assert "playlists_reconcile" not in SCHEDULED_JOB_NAMES
    assert "collections_reconcile" in SCHEDULED_JOB_NAMES


def test_playlists_enabled_is_frozen_and_nothing_else_about_playlists_is():
    """``playlists.enabled`` is read once, at startup, and the editor says so.

    After this phase ``app.py`` registers the ``collections_reconcile`` job when
    ``collections.enabled or playlists.enabled``, which means turning
    ``playlists.enabled`` on inside a process that registered no job does not
    reach it -- a swap cannot rebuild the job set (``config/live.py``'s
    ``swap_config`` says so in as many words). That is a real restart
    requirement, so it belongs in ``FROZEN_SECTIONS`` beside its twin
    ``collections.enabled``, and its reason has to name the registration rather
    than shrug.

    Everything ELSE about playlists is live: the definitions, the scope,
    ``apply_to_plex``, ``delete_unconfigured``, ``max_deletes`` are all read off
    the holder per pass. A broader ``playlists`` prefix would be a restart
    requirement that does not exist, and ``frozen_reason``'s longest-prefix rule
    means it would swallow all five."""
    from autoposter.config.live import FROZEN_SECTIONS, frozen_reason

    assert [path for path in FROZEN_SECTIONS if path.startswith("playlists")] == [
        "playlists.enabled"
    ]
    assert "registered" in FROZEN_SECTIONS["playlists.enabled"]
    assert frozen_reason("playlists.enabled")
    for live in (
        "playlists.definitions", "playlists.libraries", "playlists.apply_to_plex",
        "playlists.delete_unconfigured", "playlists.max_deletes",
    ):
        assert frozen_reason(live) is None, (
            "%s is not read once at startup, so telling an operator a restart "
            "is needed would be a false claim" % live
        )


# --- presets, and the empty scope L-6 found -----------------------------------


@pytest.fixture
def a_preset(monkeypatch):
    """One preset pointing at this file's registered test builder.

    The nine shipped presets sit on ``imdb_list`` and ``mdblist_list``, whose
    builders reach the network -- which conftest's autouse
    ``no_outbound_network`` fixture forbids and the pass's containment
    invariant would then turn into a contained failure. Testing the expansion
    through the REAL pass therefore means swapping the table, not the builder:
    what is under test is that ``reconcile_playlists`` builds what
    ``playlist_definitions`` returns, and that is exactly as true of one row as
    of nine.

    Both module attributes are patched because both are read by name at call
    time -- ``preset_definitions`` scans ``PLAYLIST_PRESETS``, and
    ``PlaylistsConfig._presets_must_be_known`` re-imports ``BY_KEY`` from the
    module on every validation.
    """
    preset = PlaylistPreset(
        key="test_timeline",
        title="Test Timeline",
        builder="playlist_test_ids",
        params={},
    )
    monkeypatch.setattr(playlist_presets, "PLAYLIST_PRESETS", (preset,))
    monkeypatch.setattr(playlist_presets, "BY_KEY", {preset.key: preset})
    monkeypatch.setattr(playlist_presets, "BY_TITLE", {preset.title: preset})
    return preset


@pytest.fixture
def an_mdblist_preset(monkeypatch):
    """One preset on the REAL ``mdblist_list`` builder, for the no-key report.

    The builder itself is the point here rather than an obstacle: with no
    MDBList client on the bundle it raises ``MdblistBuilderRefused`` before
    reaching the network, which is exactly the deployment this test describes.
    ``46555`` is a real numeric list reference so ``MdblistListParams`` (which
    ``PlaylistDefinition`` runs ``params`` through) accepts it.
    """
    preset = PlaylistPreset(
        key="test_mdblist_timeline",
        title="Test MDBList Timeline",
        builder="mdblist_list",
        params={"list": "46555"},
    )
    monkeypatch.setattr(playlist_presets, "PLAYLIST_PRESETS", (preset,))
    monkeypatch.setattr(playlist_presets, "BY_KEY", {preset.key: preset})
    monkeypatch.setattr(playlist_presets, "BY_TITLE", {preset.title: preset})
    return preset


async def test_a_switched_on_preset_is_built_by_the_real_pass(
    session, config_factory, a_preset
):
    """Through ``reconcile_playlists`` itself, not ``playlist_definitions``:
    the standing lesson here is two same-branch defects where the helper
    passed and the wired path differed."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    server = _server()
    config = _config(config_factory, apply_to_plex=True, presets=["test_timeline"])

    # No ``run =``: this test asserts on the server and the rows, and ruff's
    # ``F`` rules are on (`select = ["E4","E7","E9","F"]`, pyproject.toml), so
    # an unused binding kept "for symmetry" fails Step 19's lint gate.
    await reconcile_playlists(session, server, config)

    assert [p.title for p in server.created] == ["Test Timeline"]
    rows = (await session.execute(select(ManagedPlaylist))).scalars().all()
    assert [row.title for row in rows] == ["Test Timeline"]


async def test_the_sweep_does_not_orphan_a_preset_the_config_switched_on(
    session, config_factory, a_preset
):
    """The bug this composition exists to prevent: a sweep enumerating
    ``config.playlists.definitions`` alone would call every preset's playlist
    an orphan on the pass after it created it.

    Two passes, with ``delete_unconfigured`` deliberately **on**. Off means
    "reported, not deleted", so a test run under the default would prove only
    that a guard the sweep already has still works; on is the setting under
    which the composition defect actually loses an operator's playlist.
    """
    _Ids.ids = [("tmdb", "1")]
    server = _server()
    config = _config(
        config_factory,
        apply_to_plex=True,
        delete_unconfigured=True,
        presets=["test_timeline"],
    )

    # Pass one builds it. No ``run =`` for the same F841 reason as above.
    await reconcile_playlists(session, server, config)

    assert [p.title for p in server.created] == ["Test Timeline"]
    created = server.created[0]
    row = (await session.execute(select(ManagedPlaylist))).scalar_one()
    assert row.title == "Test Timeline"

    # Pass two sweeps. The row's title is accounted for only if the sweep reads
    # the composition; reading ``config.playlists.definitions`` -- empty here --
    # makes this playlist a candidate and deletes it.
    second = await reconcile_playlists(session, server, config)

    assert not any("no playlist definition builds it" in a for a in second.actions)
    # Deletion is recorded per playlist, on ``FakePlaylist.deleted`` (a bool
    # initialised False) -- ``FakeServer`` has no ``deleted`` attribute at all.
    assert created.deleted is False, "the sweep deleted the preset's playlist"
    # ``_swept`` counts a deletion on ``PlaylistResult.deleting``, and the
    # sweep's results land in ``run.playlists`` beside the definitions'.
    assert sum(result.deleting for result in second.playlists) == 0
    rows = (await session.execute(select(ManagedPlaylist))).scalars().all()
    assert [r.title for r in rows] == ["Test Timeline"]


async def test_an_operator_definition_shadows_the_preset_and_the_pass_says_so(
    session, config_factory, a_preset
):
    """A6's report half, at the real entry point. The operator's definition is
    the one that runs, and the displacement is an action rather than silence.
    """
    _Ids.ids = [("tmdb", "1")]
    server = _server()
    config = _config(
        config_factory,
        presets=["test_timeline"],
        definitions=[_definition(title="Test Timeline")],
    )

    run = await reconcile_playlists(session, server, config)

    assert [r.title for r in run.playlists] == ["Test Timeline"]
    assert (
        "the 'test_timeline' preset is not built: an operator definition "
        "already builds 'Test Timeline'"
    ) in run.actions


async def test_an_mdblist_preset_is_named_when_the_deployment_has_no_key(
    session, config_factory, an_mdblist_preset
):
    """Two of the nine sit on MDBList, and MDBList needs an API key.

    Without one the builder raises before any request and the containment turns
    that into ``"source returned no items; leaving the playlist untouched"`` --
    a SOURCE diagnosis for a CONFIGURATION mistake, the same wrong blame L-6
    fixed one guard along. An operator who switched ``pokemon_timeline`` on
    would read that and go looking at the list. So the pass names the KEY, once
    per pass, beside the conflict report.
    """
    server = _server()
    config = _config(config_factory, presets=["test_mdblist_timeline"])

    run = await reconcile_playlists(session, server, config)

    assert (
        "the 'test_mdblist_timeline' preset is skipped: no mdblist key is "
        "configured for this deployment, so its list cannot be fetched"
    ) in run.actions


async def test_an_empty_library_scope_is_a_named_refusal_not_an_indexerror(
    session, config_factory
):
    """L-6 from the 98a branch review.

    ``collections.libraries`` carries no minimum length and
    ``playlists.libraries`` is unset here, so a definition that omits
    ``libraries:`` inherits an EMPTY scope -- which
    ``_libraries_must_not_be_blank`` never sees, because that validator refuses
    only the explicitly empty list. The old behaviour was
    ``primary = libraries[0]`` raising ``IndexError``, contained by the
    per-definition handler and reported as ``'Timeline': failed (IndexError)``:
    correct in that nothing was written, useless in that the message named
    nothing an operator could act on.
    """
    _Ids.ids = [("tmdb", "1")]
    server = _server()
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])
    config.collections.libraries = []

    run = await reconcile_playlists(session, server, config)

    assert server.created == []
    assert run.playlists[0].failed is True
    assert run.playlists[0].actions == [
        "'Timeline' has no library to resolve its members from: the playlists "
        "section's scope is empty. Name libraries on this playlist, set "
        "playlists.libraries, or configure collections.libraries"
    ]


# --- the per-user stage: off, on, again --------------------------------------


async def test_a_definition_with_no_sync_to_users_costs_no_plex_tv_call(
    session, config_factory
):
    """The cost floor. Building the per-user context constructs an account and
    reads the ownership resource before it can answer anything, so a
    deployment that syncs to nobody must never reach it."""
    _Ids.ids = [("tmdb", "1")]
    account = FakeAccount([FakeUser(1, "alice", token="tok-a")])
    config = _config(config_factory, apply_to_plex=True, definitions=[_definition()])

    await reconcile_playlists(
        session, _server(), config, sources=_sources(account)
    )

    assert account.reads == 0


async def test_the_user_gate_off_reports_what_each_user_would_receive(
    session, config_factory
):
    """``apply_to_plex: true`` with ``sync_to_users_apply: false`` -- admin
    playlists live, user copies reported. The state the second gate exists to
    make expressible."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    account = FakeAccount([FakeUser(1, "alice", token="tok-a")])
    connect, servers = _user_servers(["tok-a"])
    config = _config(
        config_factory, apply_to_plex=True,
        definitions=[_definition(sync_to_users=["alice"])],
    )
    server = _server()

    run = await reconcile_playlists(
        session, server, config, sources=_sources(account), connect_user=connect
    )

    assert len(server.created) == 1
    assert servers["tok-a"].created == []
    assert run.actions == [
        "created 'Timeline' with 2 item(s)",
        "'Timeline' -> 'alice': would create with 2 item(s)",
        "the per-user playlist sync is report-only: set "
        "playlists.sync_to_users_apply to write the copies above",
    ]
    rows = (await session.execute(select(ManagedPlaylistUser))).scalars().all()
    assert rows == []


async def test_the_user_gate_on_creates_each_users_copy_and_records_the_rows(
    session, config_factory
):
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    account = FakeAccount([
        FakeUser(1, "alice", token="tok-a"), FakeUser(2, "bob", token="tok-b")
    ])
    connect, servers = _user_servers(["tok-a", "tok-b"])
    config = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice", "bob"])],
    )

    run = await reconcile_playlists(
        session, _server(), config, sources=_sources(account),
        connect_user=connect,
    )

    for token in ("tok-a", "tok-b"):
        assert len(servers[token].created) == 1
        assert [i.ratingKey for i in servers[token].created[0].items()] == ["11", "12"]
    assert run.actions == [
        "created 'Timeline' with 2 item(s)",
        "'Timeline' -> 'alice': created with 2 item(s)",
        "'Timeline' -> 'bob': created with 2 item(s)",
    ]
    rows = (await session.execute(select(ManagedPlaylistUser))).scalars().all()
    assert sorted((r.definition_key, r.plex_user_title) for r in rows) == [
        ("Timeline", "alice"), ("Timeline", "bob"),
    ]
    assert run.playlists[0].users[0].added == 2


async def test_a_second_pass_writes_nothing_to_any_user(
    session, config_factory
):
    """The per-user hash gate, proved with a sentinel that cannot be
    reproduced: the second pass is handed each user's copy with write methods
    that would record any call, and the assertion is that none was made."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    account = FakeAccount([FakeUser(1, "alice", token="tok-a")])
    connect, servers = _user_servers(["tok-a"])
    config = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice"])],
    )

    first = _server()
    await reconcile_playlists(
        session, first, config, sources=_sources(account), connect_user=connect
    )
    copy = servers["tok-a"].created[0]
    copy.writes.clear()
    servers["tok-a"].created.clear()

    second = _server(playlists=[first.created[0]])
    run = await reconcile_playlists(
        session, second, config, sources=_sources(account), connect_user=connect
    )

    assert servers["tok-a"].created == []
    assert copy.writes == []
    assert copy.moves == []
    assert run.actions == []


async def test_a_user_added_to_an_already_current_definition_still_gets_a_copy(
    session, config_factory
):
    """C13 A5, proved through the real pass. The resolved user set is NOT in
    the members hash, so the admin half short-circuits on the second pass --
    and the new user still gets a copy, because the gate that matters for a
    user is their OWN row and a new user has none."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    account = FakeAccount([
        FakeUser(1, "alice", token="tok-a"), FakeUser(2, "bob", token="tok-b")
    ])
    connect, servers = _user_servers(["tok-a", "tok-b"])
    first_config = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice"])],
    )
    first = _server()
    await reconcile_playlists(
        session, first, first_config, sources=_sources(account),
        connect_user=connect,
    )
    admin = first.created[0]

    second_config = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice", "bob"])],
    )
    run = await reconcile_playlists(
        session, _server(playlists=[admin]), second_config,
        sources=_sources(account), connect_user=connect,
    )

    assert servers["tok-b"].created != []
    assert run.actions == ["'Timeline' -> 'bob': created with 2 item(s)"]
    rows = (await session.execute(select(ManagedPlaylistUser))).scalars().all()
    assert sorted(r.plex_user_title for r in rows) == ["alice", "bob"]


async def test_no_account_token_is_reported_by_name(session, config_factory):
    """Never silently inert: the deployment asked for a fan-out it has no
    credential for, and one layer down that surfaces as an authentication
    error naming nothing an operator can act on."""
    _Ids.ids = [("tmdb", "1")]
    config = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice"])],
    )

    run = await reconcile_playlists(session, _server(), config)

    assert run.actions[-1] == (
        "the per-user playlist sync is configured but no plex.tv account "
        "token is: set AUTOPOSTER_PLEX_ACCOUNT_TOKEN, which must be this "
        "server's OWNER's account token. Nothing was written to any user"
    )


async def test_a_non_owner_token_refuses_the_fan_out_and_the_admin_pass_lands(
    session, config_factory
):
    """C13 A9 through the real pass, and the second half of the assertion is
    the point: the admin playlist is still reconciled. A refusal here is about
    other people's accounts, not about this one."""
    _Ids.ids = [("tmdb", "1")]
    account = FakeAccount([FakeUser(1, "alice", token="tok-a")], owned=False)
    connect, servers = _user_servers(["tok-a"])
    config = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice"])],
    )
    server = _server()

    run = await reconcile_playlists(
        session, server, config, sources=_sources(account), connect_user=connect
    )

    assert len(server.created) == 1
    assert servers["tok-a"].created == []
    assert run.actions[-1].startswith("refusing the user fan-out:")
    assert "not this server's owner" in run.actions[-1]


# --- one sweep, one gate, one cap -------------------------------------------


async def test_a_user_dropped_from_sync_to_users_is_reported_while_the_switch_is_off(
    session, config_factory
):
    """C13 A2's first removal event. ``delete_unconfigured`` off means
    REPORTED, exactly as it does for an admin playlist."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    account = FakeAccount([
        FakeUser(1, "alice", token="tok-a"), FakeUser(2, "bob", token="tok-b")
    ])
    connect, servers = _user_servers(["tok-a", "tok-b"])
    both = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice", "bob"])],
    )
    first = _server()
    await reconcile_playlists(
        session, first, both, sources=_sources(account), connect_user=connect
    )
    bobs_copy = servers["tok-b"].created[0]

    dropped = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice"])],
    )
    run = await reconcile_playlists(
        session, _server(playlists=[first.created[0]]), dropped,
        sources=_sources(account), connect_user=connect,
    )

    assert bobs_copy.deleted is False
    assert run.actions == [
        "'Timeline' in the account of 'bob': no playlist definition syncs it "
        "to them any more; set playlists.delete_unconfigured to delete it"
    ]
    rows = (await session.execute(select(ManagedPlaylistUser))).scalars().all()
    assert len(rows) == 2
    # Report-only, so no count: nothing WOULD be deleted while the switch is
    # off, and a `deleting: 1` here would tell a preview reader an operator had
    # already authorised something they have not.
    swept = [r for r in run.playlists if r.title == "Timeline" and r.skipped]
    assert [u.deleting for r in swept for u in r.users] == [0]


async def test_a_dropped_user_copy_is_deleted_once_both_switches_are_on(
    session, config_factory
):
    """Both, because a delete in somebody else's account is a per-user write:
    ``delete_unconfigured`` authorises the sweep and ``sync_to_users_apply``
    authorises writing there at all."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    account = FakeAccount([
        FakeUser(1, "alice", token="tok-a"), FakeUser(2, "bob", token="tok-b")
    ])
    connect, servers = _user_servers(["tok-a", "tok-b"])
    both = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice", "bob"])],
    )
    first = _server()
    await reconcile_playlists(
        session, first, both, sources=_sources(account), connect_user=connect
    )
    bobs_copy = servers["tok-b"].created[0]

    dropped = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        delete_unconfigured=True,
        definitions=[_definition(sync_to_users=["alice"])],
    )
    run = await reconcile_playlists(
        session, _server(playlists=[first.created[0]]), dropped,
        sources=_sources(account), connect_user=connect,
    )

    assert bobs_copy.deleted is True
    assert run.actions == [
        "deleted 'Timeline' in the account of 'bob': no playlist definition "
        "syncs it to them any more"
    ]
    rows = (await session.execute(select(ManagedPlaylistUser))).scalars().all()
    assert [r.plex_user_title for r in rows] == ["alice"]
    # The sweep's own per-user row: the ONE place ``deleting`` is written, and
    # what makes the preview say whose account a copy left rather than only
    # that one did.
    swept = [r for r in run.playlists if r.title == "Timeline" and r.skipped]
    assert [(u.title, u.user_id, u.deleting) for r in swept for u in r.users] == [
        ("bob", 2, 1)
    ]


async def test_a_cap_refusal_leaves_a_sweep_eligible_user_copy_untouched(
    session, config_factory
):
    """A cap refusal must not let the sweep run. ``_sync_users`` reports
    "nothing was written to any user" on a ``max_users`` refusal -- and a
    delete IS a write. Before the fix the refusal handed the sweep the live
    ``UserSync`` anyway, so a copy the sweep is otherwise authorised to
    remove (dropped from ``sync_to_users``, ``delete_unconfigured`` on) could
    be deleted in the very pass whose own message said nothing was written to
    anybody."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    account = FakeAccount([
        FakeUser(1, "alice", token="tok-a"), FakeUser(2, "bob", token="tok-b")
    ])
    connect, servers = _user_servers(["tok-a", "tok-b"])
    both = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice", "bob"])],
    )
    first = _server()
    await reconcile_playlists(
        session, first, both, sources=_sources(account), connect_user=connect
    )
    bobs_copy = servers["tok-b"].created[0]

    capped = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        delete_unconfigured=True, max_users=0,
        definitions=[_definition(sync_to_users=["alice"])],
    )
    run = await reconcile_playlists(
        session, _server(playlists=[first.created[0]]), capped,
        sources=_sources(account), connect_user=connect,
    )

    assert bobs_copy.deleted is False
    assert run.actions == [
        "refusing the user fan-out: 1 user(s) resolved, more than the "
        "max_users cap of 0; nothing was written to any user and the admin "
        "playlists were reconciled"
    ]
    rows = (await session.execute(select(ManagedPlaylistUser))).scalars().all()
    assert sorted(r.plex_user_title for r in rows) == ["alice", "bob"]


async def test_the_delete_cap_counts_admin_playlists_and_user_copies_together(
    session, config_factory
):
    """One cap, deliberately: a definition removed from a two-user fan-out
    puts three objects on the block, and a cap of two must see three. That IS
    the blast radius the cap exists to make an operator look at."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    account = FakeAccount([
        FakeUser(1, "alice", token="tok-a"), FakeUser(2, "bob", token="tok-b")
    ])
    connect, servers = _user_servers(["tok-a", "tok-b"])
    both = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=["alice", "bob"])],
    )
    first = _server()
    await reconcile_playlists(
        session, first, both, sources=_sources(account), connect_user=connect
    )
    admin = first.created[0]

    gone = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        delete_unconfigured=True, max_deletes=2, definitions=[],
    )
    run = await reconcile_playlists(
        session, _server(playlists=[admin]), gone,
        sources=_sources(account), connect_user=connect,
    )

    assert admin.deleted is False
    assert all(s.created[0].deleted is False for s in servers.values())
    assert run.actions == [
        "refusing to delete 3 unconfigured playlist(s), 2 of them user copies: "
        "more than the max_deletes cap of 2; nothing was deleted and "
        "everything else was reconciled"
    ]


async def test_the_delete_cap_excludes_dry_run_reported_user_copies(
    session, config_factory
):
    """``user_dry_run`` (the caller's ``dry_run or not sync_to_users_apply``)
    means the per-user loop only REPORTS "would delete" -- it writes nothing.
    Counting those toward ``max_deletes`` blocked a deletion the pass was
    actually authorised to perform: the advertised state
    (``sync_to_users_apply: false``, ``delete_unconfigured: true``) removed a
    definition fanned out to six users, and the default cap of five refused
    the admin playlist too, forever, because 1 admin + 6 reported user
    copies is 7. Only deletions this pass will actually perform count."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    names = ["alice", "bob", "carol", "dave", "erin", "frank"]
    tokens = ["tok-%s" % name for name in names]
    account = FakeAccount([
        FakeUser(i + 1, name, token=token)
        for i, (name, token) in enumerate(zip(names, tokens))
    ])
    connect, servers = _user_servers(tokens)
    both = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=names)],
    )
    first = _server()
    await reconcile_playlists(
        session, first, both, sources=_sources(account), connect_user=connect
    )
    admin = first.created[0]
    copies = [servers[token].created[0] for token in tokens]

    gone = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=False,
        delete_unconfigured=True, definitions=[],
    )
    run = await reconcile_playlists(
        session, _server(playlists=[admin]), gone,
        sources=_sources(account), connect_user=connect,
    )

    assert admin.deleted is True
    assert all(copy.deleted is False for copy in copies)
    assert run.actions[0] == (
        "deleted 'Timeline': no playlist definition builds it any more"
    )
    user_actions = run.actions[1:]
    assert len(user_actions) == 6
    for name in names:
        assert any(
            "would delete 'Timeline' in the account of %r: no playlist "
            "definition syncs it to them any more" % name == a
            for a in user_actions
        )


async def test_the_delete_cap_still_refuses_the_whole_plan_with_the_user_gate_on(
    session, config_factory
):
    """The other half of the same fix: when the per-user gate IS applied, the
    six user copies are real deletions this pass would perform, so they must
    still count toward the cap and the whole plan -- admin playlist
    included -- still refuses."""
    _Ids.ids = [("tmdb", "1"), ("tmdb", "2")]
    names = ["alice", "bob", "carol", "dave", "erin", "frank"]
    tokens = ["tok-%s" % name for name in names]
    account = FakeAccount([
        FakeUser(i + 1, name, token=token)
        for i, (name, token) in enumerate(zip(names, tokens))
    ])
    connect, servers = _user_servers(tokens)
    both = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        definitions=[_definition(sync_to_users=names)],
    )
    first = _server()
    await reconcile_playlists(
        session, first, both, sources=_sources(account), connect_user=connect
    )
    admin = first.created[0]
    copies = [servers[token].created[0] for token in tokens]
    listings_after_first_pass = {
        token: server.listings for token, server in servers.items()
    }

    gone = _config(
        config_factory, apply_to_plex=True, sync_to_users_apply=True,
        delete_unconfigured=True, definitions=[],
    )
    run = await reconcile_playlists(
        session, _server(playlists=[admin]), gone,
        sources=_sources(account), connect_user=connect,
    )

    assert admin.deleted is False
    assert all(copy.deleted is False for copy in copies)
    # The point of the fix, not just its message: the six stale rows alone
    # already bust the cap, so this pass must refuse before minting a single
    # token or reading a single listing -- not merely before a single delete.
    assert {
        token: server.listings for token, server in servers.items()
    } == listings_after_first_pass
    assert run.actions == [
        "refusing to delete 7 unconfigured playlist(s), 6 of them user "
        "copies: more than the max_deletes cap of 5; nothing was deleted "
        "and everything else was reconciled"
    ]
