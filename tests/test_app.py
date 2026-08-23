import asyncio
from pathlib import Path

import httpx
import pytest
import requests
from httpx import ASGITransport, AsyncClient

from autoposter.app import _build_mdblist, _build_providers, _handle_intent, create_app
from autoposter.config.holder import ConfigHolder
from autoposter.config.live import swap_config
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.facts.mdblist import MDBListClient, NullMDBListClient
from autoposter.intake.arr import RenderIntent
from autoposter.notify.dispatch import Notifier, NullNotifier
from autoposter.providers.fanart import FanartClient
from autoposter.providers.tmdb import TMDBClient
from autoposter.queue.jobs import MAX_ATTEMPTS

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def secrets():
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
    )


async def test_unknown_provider_is_warned_about_and_skipped(secrets, caplog):
    # Regression guard for finding 5: a typo'd or stale provider name (e.g. the
    # "Plex" provider this project does not implement) must be dropped with a
    # clear warning, not silently produce an empty provider list.
    config = load_config(EXAMPLE)
    config.providers.order = ["TMDB", "Plex", "Fanart"]

    async with httpx.AsyncClient() as http:
        with caplog.at_level("WARNING"):
            providers = _build_providers(config, secrets, http)

    assert [type(p) for p in providers] == [TMDBClient, FanartClient]
    assert any("Plex" in record.message for record in caplog.records)


async def _get(app, path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_the_api_docs_are_off_by_default(secrets):
    """/docs, /redoc and /openapi.json enumerate every endpoint to anyone who
    can reach the port, and FastAPI serves them outside the router, so they
    cannot carry require_session."""
    config = load_config(EXAMPLE)
    assert config.api_docs_enabled is False
    app = create_app(config, session_factory=None, secrets=secrets)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await _get(app, path)).status_code == 404, path


async def test_the_api_docs_can_be_switched_on_deliberately(secrets):
    config = load_config(EXAMPLE)
    config.api_docs_enabled = True
    app = create_app(config, session_factory=None, secrets=secrets)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await _get(app, path)).status_code == 200, path


@pytest.fixture
def stubbed_background_services(monkeypatch):
    """Everything the lifespan's ``run_background`` branch starts, replaced.

    Entering the real lifespan otherwise starts the queue workers, the
    scheduler, the IMDb refresh loop and a Plex liveness check -- the last of
    which conftest's network guard fails outright. None of that is the wiring
    under test, so each is a stand-in that parks on the stop event and is
    cancelled on the way out. What is left running is the part that matters:
    the client construction and its publication on ``app.state``.
    """

    class _FakeHealth:
        healthy = True

        def __init__(self, **kwargs):
            pass

        async def check_liveness(self) -> bool:
            return True

        async def run(self, stop_event) -> None:
            await stop_event.wait()

    class _FakeLoop:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, stop_event) -> None:
            await stop_event.wait()

    async def _fake_run_workers(count, session_factory, handler, stop_event, is_healthy=None):
        await stop_event.wait()

    monkeypatch.setattr("autoposter.app.PlexHealth", _FakeHealth)
    monkeypatch.setattr("autoposter.app.ImdbAutoRefresh", _FakeLoop)
    monkeypatch.setattr("autoposter.app.Scheduler", _FakeLoop)
    monkeypatch.setattr("autoposter.app.run_workers", _fake_run_workers)


async def test_the_lifespan_publishes_its_http_client_for_request_handlers(
    session_factory, secrets, stubbed_background_services
):
    """``app.state.http = http`` is production-only wiring.

    Only ``main.build()`` passes ``run_background=True``, and nothing else in
    the suite enters a lifespan at all, so deleting that one line leaves every
    test green while every deployed instance answers 503 from the live artwork
    endpoint forever. Exactly the shape of the SPA mount in ``build()`` that
    tests/test_main.py exists to pin.

    Closed-ness after the exit is what proves it is the *shared* client rather
    than a second one made for handlers: the ``finally`` closes only the one
    the providers were built with.
    """
    app = create_app(load_config(EXAMPLE), session_factory, secrets, run_background=True)
    assert app.state.http is None, "create_app alone must not build a client"

    async with app.router.lifespan_context(app):
        http = app.state.http
        assert isinstance(http, httpx.AsyncClient), (
            f"the lifespan did not publish an httpx client on app.state.http ({http!r})"
        )
        assert not http.is_closed

    assert http.is_closed, "app.state.http was not the client the lifespan owns and closes"


async def test_handle_intent_tags_plex_connection_errors_with_resolve_max_attempts(monkeypatch):
    # Finding 1: a Plex outage surfaces as a requests connection/timeout error
    # (see _LazyPlexServer._connect in main.py), not ItemNotFound, so
    # _handle_intent must classify it the same way — otherwise run_once falls
    # back to the generic MAX_ATTEMPTS cap and the job parks after ~450s.
    config = load_config(EXAMPLE)
    assert config.plex.resolve_max_attempts != MAX_ATTEMPTS  # sanity: distinct budgets

    async def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Plex unreachable")

    monkeypatch.setattr("autoposter.app.process_item", boom)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)
    with pytest.raises(requests.exceptions.ConnectionError) as exc_info:
        await _handle_intent(
            None, intent, config_holder=ConfigHolder(config), http=None, plex=None, providers=[],
        )

    assert exc_info.value.max_attempts == config.plex.resolve_max_attempts


async def test_missing_mdblist_key_warns_and_returns_a_stand_in(secrets, caplog):
    # Finding 1: an unconfigured MDBList key must degrade only content
    # ratings, and the operator must be told why via a startup warning
    # naming the environment variable, not left to discover it by omission.
    assert secrets.mdblist_apikey == ""

    async with httpx.AsyncClient() as http:
        with caplog.at_level("WARNING"):
            mdblist = _build_mdblist(secrets, http)

    assert isinstance(mdblist, NullMDBListClient)
    assert await mdblist.content_rating(tmdb_id=1, is_movie=True) is None
    assert any(
        "AUTOPOSTER_MDBLIST_APIKEY" in record.message for record in caplog.records
    )


async def test_configured_mdblist_key_builds_the_real_client_without_warning(secrets, caplog):
    secrets.mdblist_apikey = "KEY"

    async with httpx.AsyncClient() as http:
        with caplog.at_level("WARNING"):
            mdblist = _build_mdblist(secrets, http)

    assert isinstance(mdblist, MDBListClient)
    assert not any(
        "AUTOPOSTER_MDBLIST_APIKEY" in record.message for record in caplog.records
    )


async def test_handle_intent_passes_the_artwork_probe_through_to_process_item():
    """The badge stage's provenance read is only useful if it is actually
    wired: app.py builds the partial, _handle_intent has to carry it."""
    seen = {}

    async def capture(*args, **kwargs):
        seen.update(kwargs)

    probe = object()
    config = load_config(EXAMPLE)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("autoposter.app.process_item", capture)
        await _handle_intent(
            None, RenderIntent(kind="movie", title="Dune", tmdb_id=1),
            config_holder=ConfigHolder(config), http=None, plex=None, providers=[], artwork_probe=probe,
        )

    assert seen["artwork_probe"] is probe


async def test_the_wired_artwork_probe_reads_provenance_for_one_plex_item(monkeypatch):
    """The shape app.py builds -- functools.partial(artwork_provenance, http,
    base_url=..., headers=...) -- must be callable with just the plexapi object."""
    import functools

    from autoposter.plex.artwork import artwork_provenance
    from autoposter.plex.exif import format_provenance

    calls = []

    async def fake_probe_exif(http, url, headers):
        calls.append((url, headers))
        return {0x010E: format_provenance("fp-xyz")}

    monkeypatch.setattr("autoposter.plex.artwork.probe_exif", fake_probe_exif)
    probe = functools.partial(
        artwork_provenance, None,
        base_url="http://plex.local/", headers={"X-Plex-Token": "tok"},
    )

    result = await probe(type("Item", (), {"thumb": "/library/metadata/1/thumb/1"})())

    assert result == "fp-xyz"
    assert calls == [
        ("http://plex.local/library/metadata/1/thumb/1", {"X-Plex-Token": "tok"})
    ]


async def test_the_lifespan_wires_a_notifier_from_config(
    session_factory, secrets, stubbed_background_services
):
    """``app.state.notifier = build_notifier(...)`` is production-only wiring,
    the same shape as ``app.state.http`` above: nothing else in the suite
    enters a lifespan, so deleting that line would leave every test green
    while no deployed instance ever sent a notification. Before the lifespan
    (and in every no-lifespan test app) the state carries a ``NullNotifier``,
    never ``None``, so callers send unconditionally."""
    config = load_config(EXAMPLE)
    config.notifications.enabled = True
    config.notifications.url = "http://hooks.internal/notify"
    app = create_app(config, session_factory, secrets, run_background=True)
    assert isinstance(app.state.notifier, NullNotifier), (
        "create_app alone must install a NullNotifier stand-in, never None"
    )

    async with app.router.lifespan_context(app):
        notifier = app.state.notifier
        assert isinstance(notifier, Notifier), (
            f"the lifespan did not build the real notifier ({notifier!r})"
        )
        assert notifier._http is app.state.http, (
            "the notifier must borrow the lifespan's shared http client"
        )


async def test_without_the_lifespan_the_notifier_is_a_null_stand_in(secrets):
    app = create_app(load_config(EXAMPLE), session_factory=None, secrets=secrets)
    assert isinstance(app.state.notifier, NullNotifier)


async def test_the_lifespan_hands_the_notifier_to_the_scheduler(
    session_factory, secrets, stubbed_background_services, monkeypatch
):
    """The scheduler's run-completed hook only fires if the lifespan actually
    passes the notifier it built -- pinned here because the stubbed Scheduler
    otherwise swallows its arguments and the wiring could silently drop."""
    created = []

    class _RecordingScheduler:
        def __init__(self, *args, **kwargs):
            created.append(kwargs)

        async def run(self, stop_event):
            await stop_event.wait()

    monkeypatch.setattr("autoposter.app.Scheduler", _RecordingScheduler)
    config = load_config(EXAMPLE)
    config.notifications.enabled = True
    config.notifications.url = "http://hooks.internal/notify"
    app = create_app(config, session_factory, secrets, run_background=True)

    async with app.router.lifespan_context(app):
        wired = app.state.notifier

    assert len(created) == 1
    assert isinstance(wired, Notifier)
    assert created[0].get("notifier") is wired, (
        "the lifespan built a notifier but did not hand it to the scheduler"
    )


async def test_the_lifespan_fills_the_dict_the_broadcaster_holds_rather_than_rebinding_it(
    session_factory, secrets, stubbed_background_services
):
    """``app.state.scheduler_intervals`` is handed to the dashboard
    broadcaster by ``create_app`` as the object, not a copy, so the lifespan
    has to fill it in place. Rebinding it (``= {...}``) would leave
    ``/api/status`` reporting the real cadences while the live stream reported
    null intervals forever -- and no test that builds its own dict can tell
    the difference, so the identity the broadcaster holds is captured here
    before the lifespan runs and asserted through it.
    """
    app = create_app(load_config(EXAMPLE), session_factory, secrets, run_background=True)
    held = app.state.dashboard_broadcaster._scheduler_intervals
    assert held is app.state.scheduler_intervals and held == {}, (
        "precondition: create_app publishes one empty mapping, shared"
    )

    async with app.router.lifespan_context(app):
        assert app.state.scheduler_intervals, (
            "precondition: the example config registers scheduler jobs"
        )
        assert held == app.state.scheduler_intervals, (
            "the lifespan rebound app.state.scheduler_intervals; the broadcaster "
            f"still holds the mapping it was given ({held!r}) and the stream "
            "would report null intervals forever"
        )
        assert held is app.state.scheduler_intervals


async def test_a_config_swap_reaches_the_next_job_the_lifespan_s_handler_processes(
    session_factory, secrets, stubbed_background_services, monkeypatch
):
    """The worker handler is a partial that lives as long as the process.

    Driven through the partial the *lifespan* builds, not through a
    ``_handle_intent`` call this test constructs: what is under test is the
    wiring in app.py -- ``config_holder=`` rather than ``config=`` -- and a
    test that built its own partial would pass with the closured instance
    restored. ``process_item`` already takes a config per call, so the only
    thing between a swap and the next item is that one keyword.
    """
    captured = {}

    async def capture_workers(count, factory, handler, stop_event, is_healthy=None):
        captured["handler"] = handler
        await stop_event.wait()

    seen = []

    async def capture_process_item(session, config, *args, **kwargs):
        seen.append(config)

    monkeypatch.setattr("autoposter.app.run_workers", capture_workers)
    monkeypatch.setattr("autoposter.app.process_item", capture_process_item)

    config = load_config(EXAMPLE)
    app = create_app(config, session_factory, secrets, run_background=True)
    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)

    async with app.router.lifespan_context(app):
        # run_workers is started as a task, so let it reach its first await.
        await asyncio.sleep(0)
        handler = captured["handler"]
        await handler(None, intent)

        swapped = config.model_copy(update={"workers": config.workers + 7})
        swap_config(app, swapped)
        await handler(None, intent)

    assert seen[0] is config, "the first job did not see the boot generation"
    assert seen[1] is swapped, (
        "the second job still saw the boot generation: the handler partial "
        "closures a Config instance rather than the holder, so nothing a "
        "worker does can ever pick up a swap"
    )
