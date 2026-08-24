"""``plex_watchlist``: the plex.tv watchlist, translated through its guids.

Offline. ``MyPlexAccount`` is never constructed here -- the real plexapi
surface this leans on is pinned in
``tests/test_plexapi_collection_contract.py`` instead, which asserts against
the real classes rather than against the fakes below.
"""
import logging

import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.plex_watchlist import (
    PlexAccountNotConfigured,
    PlexWatchlistDrift,
)
from autoposter.collections.service import build_source_clients
from autoposter.config.schema import Secrets

TOKEN = "a-plex-tv-account-token"


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeWatchlistItem:
    """What ``account.watchlist()`` hands back: Plex Discover metadata."""

    def __init__(self, title, item_type, guids=()):
        self.title = title
        self.type = item_type
        # A Discover item's own rating key belongs to plex.tv, never to this
        # server -- deliberately present so a builder that reached for it
        # would produce ids that resolve to nothing.
        self.ratingKey = "discover-%s" % title
        self.guid = "plex://%s/abcdef" % item_type
        self.guids = [FakeGuid(value) for value in guids]


class FakeAccount:
    def __init__(self, items, token=TOKEN):
        self._items = items
        self._token = token
        self.calls = 0

    def watchlist(self, *args, **kwargs):
        self.calls += 1
        return self._items


def _factory(account, seen=None):
    def make():
        if seen is not None:
            seen.append(True)
        return account
    return make


def _items():
    return [
        FakeWatchlistItem("Dune", "movie", ["tmdb://438631", "imdb://tt1160419"]),
        FakeWatchlistItem("Severance", "show", ["tvdb://371980"]),
        FakeWatchlistItem("Arrival", "movie", ["imdb://tt2543164"]),
        FakeWatchlistItem("Unmatched", "movie", []),
        FakeWatchlistItem("Andor", "show", ["tmdb://83867"]),
    ]


def _ctx(sources, library_type: str = "Movie", **params) -> BuilderContext:
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library, library_type=library_type, config=params, sources=sources
    )


def _sources(account=None, **kwargs):
    factory = _factory(account) if account is not None else None
    return SourceClients(plex_account=factory, **kwargs)


# --- the happy path -------------------------------------------------------------


async def test_movie_items_become_ids_in_watchlist_order():
    account = FakeAccount(_items())
    result = await REGISTRY["plex_watchlist"].build(_ctx(_sources(account)))

    assert result.ids == [("tmdb", "438631"), ("imdb", "tt2543164")]
    assert account.calls == 1


async def test_the_account_factory_and_watchlist_fetch_run_off_the_loop():
    """``MyPlexAccount()`` and ``.watchlist()`` are synchronous plex.tv round
    trips; run on the event loop they would block every other task in the
    pass for their duration. Both must happen inside the same thread hop --
    ``text_file``'s pattern for its (much cheaper) blocking file read.
    Mutation proof: revert to a bare ``account_factory().watchlist()`` call
    and this goes red because the fake account is never touched off-thread."""
    import threading

    caller_thread = threading.current_thread()
    seen_threads: list[threading.Thread] = []

    class ThreadRecordingAccount(FakeAccount):
        def watchlist(self, *args, **kwargs):
            seen_threads.append(threading.current_thread())
            return super().watchlist(*args, **kwargs)

    account = ThreadRecordingAccount(_items())
    result = await REGISTRY["plex_watchlist"].build(_ctx(_sources(account)))

    assert result.ids == [("tmdb", "438631"), ("imdb", "tt2543164")]
    assert seen_threads and seen_threads[0] is not caller_thread


async def test_a_show_library_gets_the_show_half_of_the_same_watchlist():
    """A watchlist holds films and series together; each library's pass takes
    its own half, and a movie id emitted into a Show library could collide
    with an unrelated series in the same namespace."""
    account = FakeAccount(_items())
    result = await REGISTRY["plex_watchlist"].build(
        _ctx(_sources(account), library_type="Show")
    )

    assert result.ids == [("tvdb", "371980"), ("tmdb", "83867")]


async def test_the_first_usable_guid_wins():
    """Dune carries both tmdb and imdb; the index built by
    ``resolve.build_owned_index`` records both for an owned item, so either
    resolves -- taking the first keeps one id per title rather than inflating
    the unresolved count with a second."""
    account = FakeAccount([
        FakeWatchlistItem("Dune", "movie", ["tmdb://438631", "imdb://tt1160419"])
    ])
    result = await REGISTRY["plex_watchlist"].build(_ctx(_sources(account)))

    assert result.ids == [("tmdb", "438631")]


async def test_the_namespaces_are_the_ones_the_owned_index_records():
    """The builder and the resolver read guids through the same
    ``GUID_PREFIXES``, so this cannot drift into emitting a namespace the
    index never stores."""
    from autoposter.collections.resolve import GUID_PREFIXES

    account = FakeAccount([
        FakeWatchlistItem("A", "movie", ["imdb://tt1"]),
        FakeWatchlistItem("B", "movie", ["tmdb://2"]),
        FakeWatchlistItem("C", "movie", ["tvdb://3"]),
    ])
    result = await REGISTRY["plex_watchlist"].build(_ctx(_sources(account)))

    assert result.ids == [("imdb", "tt1"), ("tmdb", "2"), ("tvdb", "3")]
    assert {namespace for namespace, _ in result.ids} <= set(GUID_PREFIXES)


# --- items that cannot be used ---------------------------------------------------


async def test_an_item_with_no_usable_guid_is_skipped_with_a_debug_line(caplog):
    """Not an error: an unresolvable entry is the resolver's ordinary
    business, the same judgement ``mdblist_list`` and ``tvdb_list`` make.
    Mutation proof: make the skip a raise and this goes red."""
    account = FakeAccount(_items())
    logger_name = "autoposter.collections.builders.plex_watchlist"
    with caplog.at_level(logging.DEBUG, logger=logger_name):
        result = await REGISTRY["plex_watchlist"].build(_ctx(_sources(account)))

    assert result.ids == [("tmdb", "438631"), ("imdb", "tt2543164")]
    lines = [record.getMessage() for record in caplog.records
             if record.name == logger_name]
    assert any("Unmatched" in line for line in lines), (
        "the skipped item must be named in a debug line, or it vanishes "
        "silently; saw %r" % lines
    )


async def test_a_plex_only_guid_is_not_a_usable_guid():
    """A Discover item always has a ``plex://`` guid. It identifies the item
    on plex.tv, not on this server, so it is not an id the resolver can use."""
    account = FakeAccount([FakeWatchlistItem("Obscure", "movie", ["plex://movie/abc"])])
    with pytest.raises(PlexWatchlistDrift):
        await REGISTRY["plex_watchlist"].build(_ctx(_sources(account)))


async def test_a_watchlist_whose_items_all_lack_guids_is_a_failure_not_an_empty_list():
    """One item without a guid is data; every item without one is the guid
    children having stopped arriving, and an empty membership one layer down
    means "remove every member". Mutation proof: drop the raise and this goes
    red."""
    account = FakeAccount([
        FakeWatchlistItem("A", "movie"),
        FakeWatchlistItem("B", "movie"),
    ])
    with pytest.raises(PlexWatchlistDrift) as caught:
        await REGISTRY["plex_watchlist"].build(_ctx(_sources(account)))

    assert "2" in str(caught.value)


async def test_a_watchlist_with_nothing_of_this_librarys_type_is_empty_not_a_failure():
    """A shows-only watchlist against a Movie library is a correct empty
    answer, not drift -- there were no candidates to fail to translate."""
    account = FakeAccount([FakeWatchlistItem("Severance", "show", ["tvdb://371980"])])
    result = await REGISTRY["plex_watchlist"].build(_ctx(_sources(account)))

    assert result.ids == []


async def test_an_empty_watchlist_is_empty():
    account = FakeAccount([])
    result = await REGISTRY["plex_watchlist"].build(_ctx(_sources(account)))

    assert result.ids == []


# --- the absent token -------------------------------------------------------------


async def test_no_account_token_raises_naming_what_is_missing():
    """``SourceClients.plex_account`` is None when
    ``AUTOPOSTER_PLEX_ACCOUNT_TOKEN`` is unset. Contained by the engine as one
    dead source. Mutation proof: drop the None check and this goes red with a
    TypeError instead."""
    with pytest.raises(PlexAccountNotConfigured) as caught:
        await REGISTRY["plex_watchlist"].build(_ctx(SourceClients()))

    message = str(caught.value)
    assert "account token not configured" in message
    assert "AUTOPOSTER_PLEX_ACCOUNT_TOKEN" in message


async def test_a_library_type_with_no_watchlist_form_is_refused_before_plex_tv():
    """The type guard runs before the factory, so a misdirected definition
    costs no plex.tv round trip -- the factory is lazy precisely because
    ``MyPlexAccount(token=…)`` connects in its constructor."""
    built: list = []
    account = FakeAccount(_items())
    sources = SourceClients(plex_account=_factory(account, built))

    with pytest.raises(LibraryTypeMismatch):
        await REGISTRY["plex_watchlist"].build(_ctx(sources, library_type="Music"))

    assert built == []
    assert account.calls == 0


# --- the token stays out of everything ---------------------------------------------


async def test_the_bundle_does_not_repr_the_account_token():
    """The bundle is a dataclass, so ``repr(sources)`` is one f-string away
    from every log line and every exception context. What it holds is a
    closure over the token, never the token."""
    from types import SimpleNamespace

    import httpx

    config = SimpleNamespace(
        providers=SimpleNamespace(cache_ttl_seconds=3600),
        radarr=SimpleNamespace(enabled=False, base_url=""),
        sonarr=SimpleNamespace(enabled=False, base_url=""),
        manual_assets_root="/manual",
    )
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused", plex_token="server-scoped",
        plex_account_token=TOKEN, tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
    )
    async with httpx.AsyncClient() as http:
        sources = build_source_clients(config, secrets, http)

    assert sources.plex_account is not None
    assert TOKEN not in repr(sources)
    assert TOKEN not in repr(sources.plex_account)


async def test_no_log_line_the_builder_writes_carries_the_token(caplog):
    account = FakeAccount(_items(), token=TOKEN)
    with caplog.at_level(logging.DEBUG):
        await REGISTRY["plex_watchlist"].build(_ctx(_sources(account)))

    assert caplog.records, "the skipped item should have produced at least one line"
    for record in caplog.records:
        assert TOKEN not in record.getMessage()


async def test_the_not_configured_error_names_no_credential():
    with pytest.raises(PlexAccountNotConfigured) as caught:
        await REGISTRY["plex_watchlist"].build(_ctx(SourceClients()))

    assert TOKEN not in str(caught.value)


# --- params and registration ---------------------------------------------------------


async def test_plex_watchlist_refuses_params_it_does_not_understand():
    account = FakeAccount(_items())
    with pytest.raises(ValidationError):
        await REGISTRY["plex_watchlist"].build(_ctx(_sources(account), user="someone"))


def test_plex_watchlist_is_registered_under_its_own_name():
    assert REGISTRY["plex_watchlist"].type_name == "plex_watchlist"
