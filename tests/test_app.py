import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import requests
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from autoposter.app import _build_mdblist, _build_providers, _handle_intent, create_app
from autoposter.config.holder import ConfigHolder
from autoposter.config.live import swap_config
from autoposter.config.loader import load_config
from autoposter.config.overrides import OVERRIDES_ROW_ID
from autoposter.config.schema import STATE_FILE_NAMES_ENV, Secrets
from autoposter.db.models import ConfigOverride
from autoposter.facts.mdblist import MDBListClient, NullMDBListClient
from autoposter.intake.arr import RenderIntent
from autoposter.notify.dispatch import Notifier, NullNotifier
from autoposter.providers.fanart import FanartClient
from autoposter.providers.tmdb import TMDBClient
from autoposter.queue.jobs import MAX_ATTEMPTS
from autoposter.render.pipeline import SourceRefused
from autoposter.scheduler.run_history import UNRECORDED

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

    async def _fake_run_workers(
        count, session_factory, handlers, stop_event, is_healthy=None, pause=None
    ):
        await stop_event.wait()

    monkeypatch.setattr("autoposter.app.PlexHealth", _FakeHealth)
    monkeypatch.setattr("autoposter.app.ImdbAutoRefresh", _FakeLoop)
    monkeypatch.setattr("autoposter.app.Scheduler", _FakeLoop)
    monkeypatch.setattr("autoposter.app.run_workers", _fake_run_workers)


def _background_app(config, session_factory, secrets, **kwargs):
    """A ``run_background=True`` application that knows it came from EXAMPLE.

    The lifespan's first statement re-reads ``app.state.config_path`` to merge
    the database overrides over it, so an app built from the example config
    has to say which file that was: ``create_app`` defaults the attribute to
    the deployed ``/config/autoposter.yaml``, which exists in a pod and
    nowhere else. ``main.build()`` rebinds it for exactly the same reason.
    """
    app = create_app(config, session_factory, secrets, run_background=True, **kwargs)
    app.state.config_path = EXAMPLE
    return app


async def _store_override(session, document: dict) -> None:
    session.add(ConfigOverride(id=OVERRIDES_ROW_ID, document=document))
    await session.commit()


async def test_the_lifespan_boots_on_the_effective_config_not_the_file_alone(
    session, session_factory, secrets, stubbed_background_services, monkeypatch
):
    """``main.build()`` loads the *file* config; the database overrides are
    merged in here, at the top of the lifespan.

    They have to be. ``uvicorn --factory`` calls ``build()`` from inside its
    own event loop, so ``build()`` cannot await the overrides read and cannot
    bridge it either (see tests/test_main.py). The lifespan is the first place
    that is both on the loop and holding the session factory, so that is where
    the effective generation is loaded and swapped in.

    Two assertions, and the second is the load-bearing one. ``workers`` is
    read exactly once, by the lifespan itself, on the line that starts the
    pool -- so it reports not merely *that* the swap happened but that it
    happened before the consumers were built from it. A swap performed even
    one line too late leaves the first assertion green and this one showing
    the file's value.
    """
    file_config = load_config(EXAMPLE)
    overridden = file_config.workers + 7
    await _store_override(session, {"workers": overridden})

    started = []

    async def capture_workers(count, factory, handlers, stop_event, is_healthy=None, pause=None):
        started.append(count)
        await stop_event.wait()

    monkeypatch.setattr("autoposter.app.run_workers", capture_workers)

    app = _background_app(file_config, session_factory, secrets)
    assert app.state.config.workers == file_config.workers, (
        "precondition: create_app starts on the file generation"
    )

    async with app.router.lifespan_context(app):
        # run_workers is started as a task, so let it reach its first await.
        await asyncio.sleep(0)
        assert app.state.config.workers == overridden, (
            "the lifespan never applied the stored overrides; every deployment "
            "would boot on the file config alone and the Settings editor would "
            "not survive a restart"
        )
        assert started == [overridden], (
            "the worker pool was sized from the file config, so the overrides "
            f"swap happens after its consumers read it ({started!r})"
        )


async def test_the_lifespan_reads_the_boot_instant_from_the_database_clock(
    session_factory, secrets, stubbed_background_services, monkeypatch, caplog
):
    """SCHED-UX review, Important 1: /api/status derives a scheduled job's
    status by comparing ``app.state.started_at`` against ``last_started_at``,
    a column the scheduler stamps with Postgres's own ``now()``
    (scheduler/core.py's ``claim_due``). ``create_app`` can only set a
    Python-clock placeholder -- it is synchronous and cannot await a database
    read -- so if nothing corrected it, a deployment where the app host's and
    the database host's clocks disagree would mislabel a healthy first run
    after every restart as "interrupted" for however long the skew lasts. The
    lifespan has to replace the placeholder with a `SELECT now()` reading.

    Two checks, not one. A local test database shares the runner's own clock,
    so bracketing ``app.state.started_at`` between two of the database's own
    `now()` reads alone would pass even for a ``datetime.now(UTC)``
    regression -- there is no skew here to catch it with. So `datetime.now`
    is first replaced with a canary decades away from anything the database
    could report; a regression back to the Python clock reads the canary
    straight through, which the second assertion below the lifespan catches
    directly. The bracket then proves the value is not merely "not the
    canary" but a plausible database-clock reading. Both checks are ordering
    comparisons against an injected constant or a freshly read database
    value -- no `datetime.now()` and no `timedelta` in either assertion, per
    the suite's injected-time discipline (row 119; see
    tests/test_api_dashboard.py).

    A third, unrelated check rides along here since this is the one place
    already pinning the DB-clock boot source: the lifespan also logs the
    signed delta between the database clock and the process clock at boot,
    for permanent visibility into real skew. Assert only that the line
    exists and carries a signed millisecond value -- never its magnitude,
    which is timestamp-flake territory (row 119).
    """
    canary = datetime(1999, 1, 1, tzinfo=UTC)

    class _CanaryDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return canary

    monkeypatch.setattr("autoposter.app.datetime", _CanaryDatetime)

    app = _background_app(load_config(EXAMPLE), session_factory, secrets)

    async with session_factory() as before_session:
        before = (await before_session.execute(select(func.now()))).scalar_one()

    with caplog.at_level("INFO"):
        async with app.router.lifespan_context(app):
            boot = app.state.started_at
            assert boot != canary, (
                "app.state.started_at is the canary datetime.now(UTC) would have "
                "produced -- the lifespan is reading the Python clock, not the "
                "database's"
            )
            assert app.state.dashboard_broadcaster._started_at == boot, (
                "the dashboard stream's broadcaster still holds the Python-clock "
                "placeholder create_app built it with, so the live stream would "
                "keep deriving every run's status against the wrong clock even "
                "though /api/status was fixed"
            )

    async with session_factory() as after_session:
        after = (await after_session.execute(select(func.now()))).scalar_one()

    assert before <= boot <= after, (
        f"app.state.started_at ({boot!r}) did not come from the database "
        f"clock: expected it between two SELECT now() reads bracketing the "
        f"lifespan ({before!r}, {after!r})"
    )

    # The lifespan's boot-time delta log (app.py, right after started_at is
    # read) is this deployment's only permanent record of real clock skew --
    # it is logged once, at boot, not re-measured. Assert the line exists and
    # carries a signed millisecond value; never its magnitude, which is
    # timestamp-flake territory (row 119).
    boot_lines = [
        r.message
        for r in caplog.records
        if r.message.startswith("boot: database clock is ")
    ]
    assert boot_lines, "no boot-time database-vs-process clock delta was logged"
    suffix = boot_lines[0].removeprefix("boot: database clock is ")
    assert suffix.endswith("ms relative to the process clock"), (
        f"boot-time clock delta log line has an unexpected shape: {boot_lines[0]!r}"
    )
    value = suffix.removesuffix("ms relative to the process clock")
    assert value[:1] in "+-" and value[1:].isdigit(), (
        f"expected a signed millisecond value in the boot-time clock delta log, "
        f"got {value!r}"
    )


async def test_the_lifespan_disposes_the_engine_after_closing_the_http_client(
    session_factory, secrets, stubbed_background_services
):
    """Shutdown gap: nothing in the served application ever called
    ``engine.dispose()``, so the pod's 5 pooled connections were never
    gracefully closed -- the process exited and Postgres saw a socket close
    rather than a terminate ("unexpected EOF on client connection with an
    open transaction", at every deploy).

    Ordering is the load-bearing half. Dispose has to come after the
    ``gather`` -- so every cancelled background task has had its cancellation
    delivered and its session returned to the pool -- and after
    ``http.aclose()``. The fake records whether the shared client was already
    closed when it ran, which is exactly that ordering expressed as a value.
    """
    disposals = []

    class _FakeEngine:
        async def dispose(self):
            disposals.append(app.state.http.is_closed)

    app = _background_app(
        load_config(EXAMPLE), session_factory, secrets, engine=_FakeEngine()
    )

    async with app.router.lifespan_context(app):
        pass

    assert disposals == [True], (
        "the engine was disposed zero times, more than once, or before the "
        f"shared http client was closed ({disposals!r})"
    )


async def test_a_dispose_failure_does_not_fail_the_lifespan(
    session_factory, secrets, stubbed_background_services, caplog
):
    """``dispose()`` must not be able to fail the shutdown it is part of --
    an exception out of it would propagate out of the lifespan generator
    (uvicorn reports ``Application shutdown failed`` and exits non-zero) and
    skip the ``removeHandler`` call right after it, leaving the root logger
    pointed at a teardown-stage buffer.
    """

    class _RaisingEngine:
        async def dispose(self):
            raise RuntimeError("pool already gone")

    app = _background_app(
        load_config(EXAMPLE), session_factory, secrets, engine=_RaisingEngine()
    )

    with caplog.at_level("WARNING"):
        async with app.router.lifespan_context(app):
            pass

    assert app.state.log_buffer not in logging.getLogger().handlers, (
        "removeHandler was skipped because dispose() raised past it"
    )
    assert any(
        "engine dispose failed" in record.message and "RuntimeError" in record.message
        for record in caplog.records
    ), "the dispose failure was swallowed silently instead of logged"


async def test_the_lifespan_builds_the_plex_client_from_the_effective_config(
    session, session_factory, secrets, stubbed_background_services
):
    """``app.state.plex`` is built from the config the *lifespan* holds.

    It used to be built in ``main.build()``, which was fine while ``build()``
    itself loaded the effective config. It no longer does, and ``config.plex``
    feeds three things built at startup -- this client, the liveness probe and
    the scheduler's server factory. The other two are built in the lifespan,
    after the swap; leaving this one in ``build()`` would point the client that
    runs every job at the un-overridden URL while the probe that decides
    whether jobs run at all watched the overridden one.
    """
    overridden_url = "http://plex.overridden.internal:32400"
    file_config = load_config(EXAMPLE)
    assert file_config.plex.url != overridden_url
    await _store_override(session, {"plex": {"url": overridden_url}})

    seen = []

    def plex_factory(config):
        seen.append(config)
        return "the-plex-client"

    app = _background_app(
        file_config, session_factory, secrets, plex_factory=plex_factory
    )
    assert app.state.plex is None, "create_app alone must not build a Plex client"

    async with app.router.lifespan_context(app):
        assert app.state.plex == "the-plex-client", (
            "the lifespan did not build the Plex client from the factory "
            f"build() handed it ({app.state.plex!r})"
        )
        assert [c.plex.url for c in seen] == [overridden_url], (
            "the Plex client was built from the file config, so it and the "
            "liveness probe disagree about which server this deployment talks to"
        )


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
    app = _background_app(load_config(EXAMPLE), session_factory, secrets)
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


async def test_handle_intent_tags_source_refused_with_max_attempts_one(monkeypatch):
    # Roadmap: the unscorable-floor investigation's fix 3. A SourceRefused is
    # a validation refusal (render/pipeline.py's own docstring) -- retrying
    # re-downloads the exact same corrupt bytes, so the generic 5-attempt
    # budget just burns four attempts for nothing. _handle_intent must tag it
    # with max_attempts=1, the same threading the ConnectionError test above
    # pins for resolve_max_attempts, so run_once parks it after one attempt.
    config = load_config(EXAMPLE)

    async def boom(*args, **kwargs):
        raise SourceRefused("the poster source did not decode after download (OSError)")

    monkeypatch.setattr("autoposter.app.process_item", boom)

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)
    with pytest.raises(SourceRefused) as exc_info:
        await _handle_intent(
            None, intent, config_holder=ConfigHolder(config), http=None, plex=None, providers=[],
        )

    assert exc_info.value.max_attempts == 1


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


async def test_handle_intent_passes_plex_generated_base_through_to_process_item():
    """The plex-preview rung (roadmap row 241) is only useful if it is
    actually wired: app.py builds the partial, _handle_intent has to carry
    it through to process_item exactly like artwork_probe does."""
    seen = {}

    async def capture(*args, **kwargs):
        seen.update(kwargs)

    plex_generated_base = object()
    config = load_config(EXAMPLE)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("autoposter.app.process_item", capture)
        await _handle_intent(
            None, RenderIntent(kind="movie", title="Dune", tmdb_id=1),
            config_holder=ConfigHolder(config), http=None, plex=None, providers=[],
            plex_generated_base=plex_generated_base,
        )

    assert seen["plex_generated_base"] is plex_generated_base


async def test_the_wired_plex_generated_base_fetches_the_generated_frame_for_one_item(
    tmp_path,
):
    """The shape app.py builds -- functools.partial(fetch_plex_generated_base,
    http, plex, base_url=..., headers=...) -- must be callable with just the
    rating key and a destination path, exactly as render_artifact calls it."""
    import functools

    from conftest import decodable_png

    from autoposter.render.pipeline import fetch_plex_generated_base

    class FakeEntry:
        def __init__(self, rating_key, key):
            self.ratingKey = rating_key
            self.key = key

    class FakePlexItem:
        def posters(self):
            return [
                FakeEntry("upload://abc", "/library/metadata/1/file?url=upload..."),
                FakeEntry(
                    "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
                    "/library/metadata/1/file?url=media%3A%2F%2F5%2Fx.bundle...",
                ),
            ]

    class FakePlex:
        async def fetch_item(self, rating_key):
            assert rating_key == "153736"
            return FakePlexItem()

    async def handler(request):
        assert request.headers["X-Plex-Token"] == "tok"
        return httpx.Response(200, content=decodable_png())

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plex_generated_base = functools.partial(
        fetch_plex_generated_base, http, FakePlex(),
        base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
    )

    destination = tmp_path / "base.jpg"
    sha = await plex_generated_base("153736", destination, stage="the title_card source")

    assert sha is not None
    assert destination.exists()
    await http.aclose()


async def test_the_lifespan_wires_a_notifier_from_config(
    session, session_factory, secrets, stubbed_background_services
):
    """``app.state.notifier = build_notifier(...)`` is production-only wiring,
    the same shape as ``app.state.http`` above: nothing else in the suite
    enters a lifespan, so deleting that line would leave every test green
    while no deployed instance ever sent a notification. Before the lifespan
    (and in every no-lifespan test app) the state carries a ``NullNotifier``,
    never ``None``, so callers send unconditionally."""
    # Expressed as a stored override rather than mutated onto the Config
    # object handed to create_app: under run_background the lifespan boots on
    # the effective config it loads itself, so an in-memory mutation of the
    # argument never reaches the notifier. This is how a deployment turns
    # notifications on -- ConfigMap or Settings editor, both merged here.
    await _store_override(
        session,
        {"notifications": {"enabled": True, "url": "http://hooks.internal/notify"}},
    )
    app = _background_app(load_config(EXAMPLE), session_factory, secrets)
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
    session, session_factory, secrets, stubbed_background_services, monkeypatch
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
    # Expressed as a stored override rather than mutated onto the Config
    # object handed to create_app: under run_background the lifespan boots on
    # the effective config it loads itself, so an in-memory mutation of the
    # argument never reaches the notifier. This is how a deployment turns
    # notifications on -- ConfigMap or Settings editor, both merged here.
    await _store_override(
        session,
        {"notifications": {"enabled": True, "url": "http://hooks.internal/notify"}},
    )
    app = _background_app(load_config(EXAMPLE), session_factory, secrets)

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
    the difference, so this asserts the identity itself: that the lifespan
    fills the published mapping in place, and that the broadcaster the
    lifespan leaves behind holds that very mapping.
    """
    app = _background_app(load_config(EXAMPLE), session_factory, secrets)
    held = app.state.dashboard_broadcaster._scheduler_intervals
    assert held is app.state.scheduler_intervals and held == {}, (
        "precondition: create_app publishes one empty mapping, shared"
    )

    async with app.router.lifespan_context(app):
        assert app.state.scheduler_intervals, (
            "precondition: the example config registers scheduler jobs"
        )
        assert "plex_prune" in app.state.scheduler_intervals, (
            "the boot registration in app.py did not append the prune job"
        )
        assert "plex_merge" in app.state.scheduler_intervals, (
            "the boot registration in app.py did not append the twin-merge job"
        )
        assert held is app.state.scheduler_intervals, (
            "the lifespan rebound app.state.scheduler_intervals rather than "
            f"filling it in place; it still holds {held!r}"
        )
        # The lifespan discards the broadcaster create_app built and puts a
        # new one in its place, so `held` -- captured from the discarded one
        # -- says nothing about what the app actually serves from. The live
        # broadcaster is the one that has to hold the mapping itself: hand it
        # a copy here and the stream reports null intervals forever.
        assert (
            app.state.dashboard_broadcaster._scheduler_intervals
            is app.state.scheduler_intervals
        ), (
            "the broadcaster the lifespan serves from holds a copy of "
            "app.state.scheduler_intervals, not the mapping itself; the "
            "stream would report null intervals forever"
        )


async def test_stale_job_reclaim_is_registered_even_with_the_scheduler_disabled(
    session, session_factory, secrets, stubbed_background_services
):
    """``scheduler.enabled`` is the master switch for the five *optional*
    maintenance passes (app.py's comment above ``scheduler_jobs = [...]``),
    not for queue correctness -- ``stale_job_reclaim`` is registered ahead of
    the ``if config.scheduler.enabled:`` gate specifically so disabling those
    passes cannot also disable the sweep that closes the incident this branch
    fixes. Nothing pinned that until now: every other assertion over
    ``scheduler_jobs``/``scheduler_intervals`` in this file and
    ``test_api_dashboard.py``/``test_config_live.py`` runs with the scheduler
    on, so a change that moved the registration line inside the ``if`` would
    pass the whole suite while silently reintroducing the incident on every
    scheduler-off deployment.

    Turned off through a stored override rather than a mutation on the
    ``Config`` object handed to ``create_app``: under ``run_background`` the
    lifespan boots on the effective config it loads itself (this file plus
    the database overrides), so an in-memory mutation of the argument never
    reaches it -- see ``create_app``'s docstring.
    """
    config = load_config(EXAMPLE)
    assert config.scheduler.enabled is True, "precondition: EXAMPLE ships the master switch on"
    await _store_override(session, {"scheduler": {"enabled": False}})

    app = _background_app(config, session_factory, secrets)

    async with app.router.lifespan_context(app):
        assert app.state.config.scheduler.enabled is False, (
            "precondition: the override did not reach the effective config"
        )
        assert "stale_job_reclaim" in app.state.scheduler_intervals
        assert any(job.name == "stale_job_reclaim" for job in app.state.scheduler_jobs)
        # The five optional passes really are off -- otherwise this would not
        # be exercising the branch it claims to.
        assert "plex_prune" not in app.state.scheduler_intervals
        assert "plex_merge" not in app.state.scheduler_intervals
        # Every job registered ahead of the scheduler.enabled gate must be
        # one run_history knows to never record -- otherwise a future job
        # added above the gate grows `runs` forever with no trimmer, which is
        # exactly the incident this branch fixed for stale_job_reclaim.
        assert {job.name for job in app.state.scheduler_jobs} <= UNRECORDED


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

    async def capture_workers(count, factory, handlers, stop_event, is_healthy=None, pause=None):
        captured["handlers"] = handlers
        await stop_event.wait()

    seen = []

    async def capture_process_item(session, config, *args, **kwargs):
        seen.append(config)

    monkeypatch.setattr("autoposter.app.run_workers", capture_workers)
    monkeypatch.setattr("autoposter.app.process_item", capture_process_item)

    app = _background_app(load_config(EXAMPLE), session_factory, secrets)
    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)

    async with app.router.lifespan_context(app):
        # run_workers is started as a task, so let it reach its first await.
        await asyncio.sleep(0)
        # The generation the lifespan itself booted on. Read off the app
        # rather than reusing the Config create_app was handed: the lifespan
        # re-reads the file and merges the (here empty) overrides over it, so
        # the boot generation is an equal-but-distinct object.
        booted = app.state.config
        # The process_item entry of the dispatch map the lifespan built. It
        # decodes the RenderIntent off the job's payload, so drive it with a
        # stand-in job carrying that payload rather than the intent directly.
        process_item_handler = captured["handlers"]["process_item"]
        job = SimpleNamespace(payload=asdict(intent))
        await process_item_handler(None, job)

        swapped = booted.model_copy(update={"workers": booted.workers + 7})
        swap_config(app, swapped)
        await process_item_handler(None, job)

    assert seen[0] is booted, "the first job did not see the boot generation"
    assert seen[1] is swapped, (
        "the second job still saw the boot generation: the handler partial "
        "closures a Config instance rather than the holder, so nothing a "
        "worker does can ever pick up a swap"
    )


async def test_the_collections_job_is_registered_for_playlists_alone(
    session, session_factory, secrets, stubbed_background_services
):
    """Roadmap row 98a. The playlists pass rides the ``collections_reconcile``
    job, so ``playlists.enabled: true`` with ``collections.enabled: false`` has
    to still REGISTER that job -- otherwise the setting reads as configured, the
    job body's own two-switch handling never runs, and the pass silently never
    happens.

    Driven through the real lifespan, not through ``make_collections_job``. The
    registration is a line in ``app.py`` that no monkeypatch of
    ``scheduler.jobs`` can reach, and the standing lesson on this branch is two
    same-branch defects where the helper tests passed and the wired path
    differed. The job is only BUILT here, never run: ``server_factory`` is a
    ``functools.partial`` that nothing calls, and the background services the
    fixture stubs are what would otherwise touch the network.

    The switches are expressed as a STORED OVERRIDE, not as a mutation on the
    ``Config`` handed to ``create_app``: under ``run_background`` the lifespan's
    first statement re-reads ``app.state.config_path`` and merges the database
    overrides over it, so an in-memory mutation of the argument never reaches
    the job-set registration -- the same reason
    ``test_the_lifespan_hands_the_notifier_to_the_scheduler`` and
    ``test_stale_job_reclaim_is_registered_even_with_the_scheduler_disabled``
    do it this way. It is load-bearing here rather than merely idiomatic:
    ``EXAMPLE`` ships ``collections.enabled: true``, so a mutated ``Config``
    would leave the lifespan booting with collections ON and this test would go
    GREEN against the unfixed ``app.py`` -- pinning nothing, which is exactly
    the failure mode it exists to close.
    """
    config = load_config(EXAMPLE)
    assert config.collections.enabled is True, (
        "precondition: EXAMPLE ships collections on -- turning it off in the "
        "override below is what makes this test discriminating"
    )
    await _store_override(
        session,
        {"collections": {"enabled": False}, "playlists": {"enabled": True}},
    )

    app = _background_app(config, session_factory, secrets)

    async with app.router.lifespan_context(app):
        assert app.state.config.collections.enabled is False, (
            "precondition: the override did not reach the effective config"
        )
        assert app.state.config.playlists.enabled is True, (
            "precondition: the override did not reach the effective config"
        )
        names = {job.name for job in app.state.scheduler_jobs}

    assert "collections_reconcile" in names, (
        "playlists.enabled is on and the job that carries the playlists pass "
        "was not registered, so the pass can never run in this deployment"
    )
    assert "playlists_reconcile" not in names, (
        "the playlists pass rides the collections job; a second scheduled job "
        "would need SCHEDULED_JOB_NAMES and the agreement guard in "
        "tests/test_api_scheduled_runs.py to move with it"
    )


async def test_the_collections_job_is_not_registered_when_both_are_off(
    session, session_factory, secrets, stubbed_background_services
):
    """The other direction, so the assertion above is not vacuous: with both
    switches off the job is absent, which is what makes registering it on
    either one a decision rather than an accident.

    A stored override for the same reason as above -- and here ``EXAMPLE``
    ships BOTH switches on (``collections.enabled: true`` at :139,
    ``playlists.enabled: true`` in the block Step 10 adds), so a mutated
    ``Config`` would leave the job registered and this test could never pass.
    """
    config = load_config(EXAMPLE)
    await _store_override(
        session,
        {"collections": {"enabled": False}, "playlists": {"enabled": False}},
    )

    app = _background_app(config, session_factory, secrets)

    async with app.router.lifespan_context(app):
        assert app.state.config.collections.enabled is False, (
            "precondition: the override did not reach the effective config"
        )
        assert app.state.config.playlists.enabled is False, (
            "precondition: the override did not reach the effective config"
        )
        names = {job.name for job in app.state.scheduler_jobs}
        # The scheduler's optional passes really are on, so this is the two
        # switches deciding the job's absence and not the master switch.
        assert "plex_prune" in names

    assert "collections_reconcile" not in names


async def test_the_asset_stats_job_is_registered_by_the_lifespan(
    session_factory, secrets, stubbed_background_services
):
    """Roadmap row 52. The backfill only ever runs if the lifespan registers
    it, and the sweep is the ONLY thing that fills in the ~16k render rows
    written before ``size_bytes`` existed -- a factory nothing appends is a
    feature that is configured, documented, and silently never runs."""
    app = _background_app(load_config(EXAMPLE), session_factory, secrets)

    async with app.router.lifespan_context(app):
        names = {job.name for job in app.state.scheduler_jobs}
        assert "asset_stats" in names
        assert app.state.scheduler_intervals["asset_stats"] == 7 * 24 * 3600


def test_an_app_that_never_booted_says_no_secret_came_from_the_state_file(secrets):
    """Fail closed. Every test application, and any operator running
    `python -m autoposter.main` directly, has no marker -- and the refusal is
    the answer they get, which is the right default and the one a test has to
    opt OUT of rather than into."""
    app = create_app(load_config(EXAMPLE), session_factory=None, secrets=secrets)

    assert app.state.secret_from_state_file.get("AUTOPOSTER_WEBHOOK_SECRET", False) is False


def test_the_boot_marker_becomes_a_per_name_boolean(secrets, monkeypatch):
    monkeypatch.setenv(STATE_FILE_NAMES_ENV, "AUTOPOSTER_WEBHOOK_SECRET,AUTOPOSTER_RADARR_APIKEY")

    app = create_app(load_config(EXAMPLE), session_factory=None, secrets=secrets)

    assert app.state.secret_from_state_file.get("AUTOPOSTER_WEBHOOK_SECRET", False) is True
    assert app.state.secret_from_state_file.get("AUTOPOSTER_RADARR_APIKEY", False) is True
    assert app.state.secret_from_state_file.get("AUTOPOSTER_PLEX_TOKEN", False) is False


def test_an_empty_marker_is_not_a_name(secrets, monkeypatch):
    """`"".split(",")` is `[""]`, which would otherwise put an empty-string key
    in the map -- harmless here and a trap for the next reader."""
    monkeypatch.setenv(STATE_FILE_NAMES_ENV, "")

    app = create_app(load_config(EXAMPLE), session_factory=None, secrets=secrets)

    assert app.state.secret_from_state_file == {}
