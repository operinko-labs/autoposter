"""The Restart button.

A restart is how a frozen change takes effect now, so the route has to be
reachable -- and it has to refuse the three shapes where restarting would
destroy work or be a lie.
"""
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api import setup as setup_api
from autoposter.api import system
from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Run
from autoposter.main import listen_address

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

    The wizard's finish and this button both replace the process image with a
    fresh boot; patching the exec where the WIZARD performs it and calling it
    from here is what pins that they are the same line.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(setup_api.os, "execv", lambda path, argv: calls.append(argv))

    system._exec_boot()

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


async def test_a_single_worker_is_not_several(client, auth_headers, monkeypatch, no_exec):
    """The variable is set on every uvicorn deployment that thought about it,
    and a `1` is the ordinary single-process pod this button is for."""
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    assert (
        await client.post("/api/system/restart", headers=auth_headers)
    ).status_code == 200


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
