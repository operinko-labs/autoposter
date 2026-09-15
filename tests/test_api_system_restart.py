"""The Restart button.

A restart is how a frozen change takes effect now, so the route has to be
reachable -- and it has to refuse the three shapes where restarting would
destroy work or be a lie.
"""
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api import setup as setup_api
from autoposter.api import system
from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.holder import ConfigHolder
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Run, ScheduledRun
from autoposter.main import listen_address
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.jobs import make_catch_up_drain_job
from autoposter.servers.registry import Servers

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
async def app(session_factory):
    return create_app(
        load_config(EXAMPLE),
        session_factory,
        Secrets(
            database_url="postgresql+asyncpg://unused",
            plex_token="x", tmdb_token="x", tvdb_apikey="x",
            fanart_apikey="x", webhook_secret="x",
            admin_password_hash=hash_password(PASSWORD),
        ),
    )


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
def no_exec(monkeypatch):
    """The route really does replace the process. Count the call instead."""
    calls: list[int] = []
    monkeypatch.setattr(system, "_exec_boot", lambda: calls.append(1))
    return calls


async def test_a_restart_answers_and_then_execs(client, auth_headers, no_exec):
    response = await client.post("/api/system/restart", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"restarting": True}
    assert no_exec == [1], "the exec runs after the body is written"


def test_the_exec_is_the_wizard_s_own(monkeypatch):
    """One mechanism, not two that look alike.

    Patched at the wizard's FUNCTION rather than at ``os.execv``: ``os`` is one
    module object shared by every importer, so patching the exec itself would
    be satisfied just as well by a second copy of the line written here -- the
    one thing this test exists to forbid.
    """
    calls: list[str] = []
    monkeypatch.setattr(setup_api, "_exec_boot", lambda: calls.append("wizard"))

    system._exec_boot()

    assert calls == ["wizard"]


def test_the_wizard_s_exec_replaces_this_process_with_a_boot(monkeypatch):
    """And what that one function does, pinned where the argv actually lives:
    a fresh ``python -m autoposter.boot``, which re-reads the store."""
    calls: list[list[str]] = []
    monkeypatch.setattr(setup_api.os, "execv", lambda path, argv: calls.append(argv))

    setup_api._exec_boot()

    assert calls == [[sys.executable, "-m", "autoposter.boot"]]


async def test_a_restart_is_refused_while_a_full_pass_is_in_flight(
    client, auth_headers, session_factory, no_exec
):
    async with session_factory() as session:
        session.add(Run(kind="full_pass", name="full_pass", status="running"))
        await session.commit()
    response = await client.post("/api/system/restart", headers=auth_headers)
    assert response.status_code == 409
    assert "full_pass" in response.json()["detail"]
    assert no_exec == []


async def test_a_restart_is_refused_while_a_scheduled_run_is_in_flight(
    client, auth_headers, session_factory, no_exec
):
    """A scheduled pass holds its work in this process, exactly as a full pass
    does; the response names it so the page can say which one to wait for."""
    async with session_factory() as session:
        session.add(Run(kind="scheduled", name="collections", status="running"))
        await session.commit()
    response = await client.post("/api/system/restart", headers=auth_headers)
    assert response.status_code == 409
    assert "collections" in response.json()["detail"]
    assert no_exec == []


async def test_an_orphaned_run_row_does_not_wedge_the_button(
    client, auth_headers, session_factory, no_exec
):
    """A row nothing will ever close must not refuse forever.

    Only the next pass of the same named job closes a stale ``scheduled`` row,
    so a pod killed mid-pass leaves one ``running`` on a deployment that may
    never run that job again -- and the first thing an operator would try,
    turning the scheduler off and restarting, is exactly what it refuses.
    Past the horizon a run could plausibly still be alive, it is treated as
    the wreckage it is.
    """
    async with session_factory() as session:
        session.add(Run(
            kind="scheduled", name="collections", status="running",
            started_at=datetime.now(UTC) - timedelta(
                seconds=system.ORPHAN_RUN_SECONDS + 3600
            ),
        ))
        await session.commit()
    response = await client.post("/api/system/restart", headers=auth_headers)
    assert response.status_code == 200
    assert no_exec == [1]


async def test_a_run_inside_the_horizon_still_refuses(
    client, auth_headers, session_factory, no_exec
):
    """The other side of the same bound: an hour old is a pass at work."""
    async with session_factory() as session:
        session.add(Run(
            kind="full_pass", name="full_pass", status="running",
            started_at=datetime.now(UTC) - timedelta(hours=1),
        ))
        await session.commit()
    response = await client.post("/api/system/restart", headers=auth_headers)
    assert response.status_code == 409
    assert no_exec == []


async def test_a_catch_up_in_flight_does_not_refuse_a_restart(
    client, auth_headers, session_factory, no_exec
):
    """The one kind a restart does not interrupt.

    A catch-up runs for hours, everything it knows is a row, and its drain
    picks up again on the other side of the boot -- so refusing here would
    mean a deployment with a backlog could never apply a setting.
    """
    async with session_factory() as session:
        session.add(Run(kind="catch_up", name="jellyfin", status="running"))
        await session.commit()
    response = await client.post("/api/system/restart", headers=auth_headers)
    assert response.status_code == 200
    assert no_exec == [1]


async def test_a_restart_is_refused_while_an_apply_holds_the_mode_lock(
    client, auth_headers, app, no_exec
):
    async with app.state.mode_lock:
        response = await client.post("/api/system/restart", headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"] == system.MODE_IN_FLIGHT
    assert no_exec == []


async def test_a_restart_takes_the_mode_lock_rather_than_peeking_at_it(
    client, auth_headers, app, no_exec
):
    """The guard has to still be true when the exec runs.

    A check that only looks leaves a window -- everything after it in the
    handler, and the background task the response carries -- in which a mode
    can acquire the lock and be replaced mid-write to a media server. Taking
    the lock closes the window: a mode arriving after this point is refused by
    the trigger's own guard, exactly as it would be by a second mode.
    """
    assert not app.state.mode_lock.locked(), "precondition: nothing holds it"

    response = await client.post("/api/system/restart", headers=auth_headers)

    assert response.status_code == 200
    assert no_exec == [1]
    assert app.state.mode_lock.locked(), (
        "the restart only peeked at the lock; a mode could start inside the "
        "window between the guard and the exec"
    )


async def test_two_restarts_at_once_produce_one_exec(
    client, auth_headers, app, no_exec
):
    """The same window, seen from the other side: the loser is refused rather
    than both execing, because the winner holds the lock from the moment it
    stops awaiting."""
    first, second = await asyncio.gather(
        client.post("/api/system/restart", headers=auth_headers),
        client.post("/api/system/restart", headers=auth_headers),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert no_exec == [1]


async def test_a_restart_is_refused_on_a_multi_worker_process(
    client, auth_headers, monkeypatch, no_exec
):
    """One worker of several on one port cannot restart the others, and
    execing itself would leave the deployment half old and half new."""
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    response = await client.post("/api/system/restart", headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"] == system.MULTIPLE_WORKERS
    assert no_exec == []


async def test_the_other_worker_variable_is_read_too(
    client, auth_headers, monkeypatch, no_exec
):
    """Both spellings, because uvicorn reads one and an image's entrypoint may
    set the other; a typo in either string would ship a guard that never
    fires."""
    monkeypatch.setenv("UVICORN_WORKERS", "4")
    response = await client.post("/api/system/restart", headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["detail"] == system.MULTIPLE_WORKERS
    assert no_exec == []


async def test_a_single_worker_is_not_several(client, auth_headers, monkeypatch, no_exec):
    """The variable is set on every uvicorn deployment that thought about it,
    and a `1` is the ordinary single-process pod this button is for."""
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    assert (
        await client.post("/api/system/restart", headers=auth_headers)
    ).status_code == 200
    assert no_exec == [1], "answered 200 and scheduled nothing"


async def test_the_catch_up_drain_s_own_ticks_do_not_refuse_a_restart(
    client, auth_headers, session_factory, no_exec
):
    """The exemption has to survive the machinery around the catch-up.

    The drain looks for due deliveries on its own cadence, and while it is
    registered as an ordinary scheduled job its ticks record no run history --
    otherwise the very deployment the exemption exists for would be refused
    once a minute, naming a job the operator never pressed.

    The real job through the real scheduler, stopped before the question is
    asked so the answer cannot be a race.
    """
    config = SimpleNamespace(
        scheduler=SimpleNamespace(
            catch_up_poll_seconds=60, pending_deliveries_minutes=15, delivery_attempts=8,
        )
    )
    job = make_catch_up_drain_job(
        ConfigHolder(config), lambda: Servers({}), lambda: [], lambda: {},
        http=None, mdblist=None,
    )
    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [job], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    row = None
    try:
        async with asyncio.timeout(60):
            while row is None or row.last_finished_at is None:
                await asyncio.sleep(0.01)
                async with session_factory() as check:
                    row = (await check.execute(select(ScheduledRun))).scalar_one_or_none()
    finally:
        stop.set()
        await task
    assert row is not None, "precondition: the drain never ticked"

    response = await client.post("/api/system/restart", headers=auth_headers)

    assert response.status_code == 200, response.json()
    assert no_exec == [1]


async def test_the_route_needs_a_session(client, no_exec):
    assert (await client.post("/api/system/restart")).status_code == 401
    assert no_exec == []


def test_the_listen_address_travels_through_the_environment(monkeypatch):
    monkeypatch.delenv("AUTOPOSTER_HOST", raising=False)
    monkeypatch.delenv("AUTOPOSTER_PORT", raising=False)
    assert listen_address() == ("0.0.0.0", 8080)
    monkeypatch.setenv("AUTOPOSTER_HOST", "127.0.0.1")
    monkeypatch.setenv("AUTOPOSTER_PORT", "9090")
    assert listen_address() == ("127.0.0.1", 9090)
    monkeypatch.setenv("AUTOPOSTER_PORT", "not-a-port")
    assert listen_address() == ("127.0.0.1", 8080), "a bad port falls back, loudly"
    monkeypatch.setenv("AUTOPOSTER_PORT", "99999")
    assert listen_address() == ("127.0.0.1", 8080), (
        "a number no socket can bind is a typo too -- it would parse here and "
        "then kill the boot at bind, with no UI left to fix it"
    )
