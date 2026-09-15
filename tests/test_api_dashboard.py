"""GET /api/status and GET /api/events."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog, Job, ScheduledRun

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
async def app(session_factory):
    """Exposed separately from ``client`` so a test can reach
    ``app.state.scheduler_intervals`` -- the lifespan that normally fills it
    does not run under ASGITransport."""
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )
    return create_app(load_config(EXAMPLE), session_factory, secrets)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def token(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return response.json()["token"]


@pytest_asyncio.fixture
async def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# --- /api/status ---


async def test_status_requires_a_session(client):
    response = await client.get("/api/status")
    assert response.status_code == 401


async def test_status_on_an_empty_database_returns_zeroed_counts(client, auth_headers):
    response = await client.get("/api/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["jobs_by_state"] == {
        "pending": 0, "running": 0, "deferred": 0, "done": 0,
        "done_with_warnings": 0, "failed": 0, "parked": 0, "dismissed": 0,
    }
    assert body["processed_last_24h"] == 0
    assert body["scheduled_jobs"] == []


async def test_status_counts_queued_running_and_parked_jobs_separately(
    client, auth_headers, session
):
    session.add_all([
        Job(kind="render", payload={}, state="pending"),
        Job(kind="render", payload={}, state="pending"),
        Job(kind="render", payload={}, state="running"),
        Job(kind="render", payload={}, state="parked"),
        Job(kind="render", payload={}, state="parked"),
        Job(kind="render", payload={}, state="parked"),
    ])
    await session.commit()

    response = await client.get("/api/status", headers=auth_headers)
    body = response.json()
    assert body["jobs_by_state"]["pending"] == 2
    assert body["jobs_by_state"]["running"] == 1
    assert body["jobs_by_state"]["parked"] == 3
    assert body["jobs_by_state"]["done"] == 0
    assert body["jobs_by_state"]["failed"] == 0


async def test_status_reports_dismissed_jobs_in_the_same_shape(client, auth_headers, session):
    """"dismissed" is a state /api/jobs/{id}/dismiss writes, so leaving it out
    of JOB_STATES made the response grow an extra key only once a dismissed
    row existed -- a shape the UI cannot rely on."""
    session.add(Job(kind="render", payload={}, state="dismissed"))
    await session.commit()

    response = await client.get("/api/status", headers=auth_headers)
    body = response.json()
    assert body["jobs_by_state"]["dismissed"] == 1
    assert set(body["jobs_by_state"]) == {
        "pending", "running", "deferred", "done", "done_with_warnings", "failed",
        "parked", "dismissed",
    }


async def test_status_processed_24h_window_excludes_older_rows(client, auth_headers, session):
    session.add_all([
        Job(kind="render", payload={}, state="done"),
        Job(kind="render", payload={}, state="done"),
    ])
    await session.commit()
    # Push one of the two "done" jobs outside the 24h window.
    await session.execute(
        text(
            "UPDATE jobs SET updated_at = now() - interval '25 hours' "
            "WHERE id = (SELECT id FROM jobs ORDER BY id LIMIT 1)"
        )
    )
    await session.commit()

    response = await client.get("/api/status", headers=auth_headers)
    assert response.json()["processed_last_24h"] == 1


async def test_status_counts_a_done_with_warnings_job_as_processed(
    client, auth_headers, session
):
    """Spec §4's state is FINISHED work: it gets its own count, it is never
    folded in with `failed`, and it must not vanish from `processed_last_24h`
    -- which would have a server being down read as the queue doing less."""
    session.add_all([
        Job(kind="render", payload={}, state="done"),
        Job(kind="render", payload={}, state="done_with_warnings",
            last_error="jellyfin: metadata failed (error: X)"),
    ])
    await session.commit()

    body = (await client.get("/api/status", headers=auth_headers)).json()
    assert body["jobs_by_state"]["done_with_warnings"] == 1
    assert body["jobs_by_state"]["done"] == 1
    assert body["jobs_by_state"]["failed"] == 0
    assert body["processed_last_24h"] == 2


async def test_done_with_warnings_has_its_own_tile(client, auth_headers):
    """The dashboard renders `jobs_by_state` in dict order, so the tile must
    sit beside `failed` -- between `done` and `failed`, per JOB_STATES -- on
    an empty database too, not only once a warned job exists to prove the
    count is separate (already covered by
    test_status_counts_a_done_with_warnings_job_as_processed above)."""
    body = (await client.get("/api/status", headers=auth_headers)).json()
    keys = list(body["jobs_by_state"])
    assert keys.index("done") < keys.index("done_with_warnings") < keys.index("failed")


async def test_status_reports_scheduled_job_last_run_and_status(client, auth_headers, session):
    session.add(
        ScheduledRun(
            name="drift", last_status="ok", last_detail="123 items checked",
        )
    )
    await session.commit()

    response = await client.get("/api/status", headers=auth_headers)
    scheduled = response.json()["scheduled_jobs"]
    assert len(scheduled) == 1
    assert scheduled[0]["name"] == "drift"
    assert scheduled[0]["last_status"] == "ok"
    assert scheduled[0]["last_detail"] == "123 items checked"


async def test_status_reports_the_interval_of_a_registered_scheduler_job(
    app, client, auth_headers, session
):
    """The interval lives only on the in-memory scheduler ``Job`` dataclass,
    never in the database, so ``/api/status`` merges it in by name. The
    frontend computes the next run from this plus ``last_started_at``."""
    session.add(ScheduledRun(name="collections_reconcile"))
    await session.commit()
    # In place, never rebound -- the same discipline app.py's lifespan follows,
    # and for the same reason: the dashboard broadcaster holds this exact dict.
    app.state.scheduler_intervals.update({"collections_reconcile": 86400})

    response = await client.get("/api/status", headers=auth_headers)
    scheduled = response.json()["scheduled_jobs"]
    assert scheduled[0]["interval_seconds"] == 86400


async def test_status_reports_a_null_interval_for_a_job_the_scheduler_never_registered(
    app, client, auth_headers, session
):
    """Registration is config-conditional (app.py:128-136) and the scheduler
    may be disabled entirely, so a ``scheduled_runs`` row left behind by an
    earlier configuration has no interval. That is null, not a guess -- and
    the field is present either way so the UI has one shape to render."""
    session.add(ScheduledRun(name="arr_sync"))
    await session.commit()
    assert app.state.scheduler_intervals == {}, (
        "precondition: create_app publishes an empty mapping unconditionally"
    )

    response = await client.get("/api/status", headers=auth_headers)
    row = response.json()["scheduled_jobs"][0]
    assert "interval_seconds" in row
    assert row["interval_seconds"] is None


async def test_status_reports_worker_count(client, auth_headers):
    response = await client.get("/api/status", headers=auth_headers)
    assert response.json()["workers"] == 5  # config/autoposter.example.yaml


# --- derived scheduled-job status ---
#
# A production incident (2026-08-25): a multi-minute scheduled run logs
# nothing between its claim and its finish, and last_status/last_finished_at
# keep showing the *previous* run's outcome for that whole stretch -- so a
# healthy 15-minute run and a dead scheduler look identical on the dashboard.
# `status` is derived read-time from last_started_at/last_finished_at and
# this process's boot instant (app.state.started_at), never written back.
#
# Every case below drives explicit timestamps against an injected boot
# instant -- no wall-clock sleeps, no datetime.now() deltas.

BOOT = datetime(2026, 1, 1, tzinfo=UTC)


async def _status_of(client, auth_headers, name="drift"):
    response = await client.get("/api/status", headers=auth_headers)
    row = next(j for j in response.json()["scheduled_jobs"] if j["name"] == name)
    return row


async def test_status_is_running_when_started_after_boot_and_unfinished(
    app, client, auth_headers, session
):
    app.state.started_at = BOOT
    session.add(
        ScheduledRun(
            name="drift", last_started_at=BOOT + timedelta(minutes=1),
            last_finished_at=None, last_status="ok",
        )
    )
    await session.commit()

    row = await _status_of(client, auth_headers)
    assert row["status"] == "running"


async def test_status_is_running_when_the_last_finish_predates_the_last_start(
    app, client, auth_headers, session
):
    """last_status/last_finished_at still describe the *previous* run while a
    new one is in flight -- the exact shape a multi-minute run leaves."""
    app.state.started_at = BOOT
    started = BOOT + timedelta(minutes=5)
    session.add(
        ScheduledRun(
            name="drift", last_started_at=started,
            last_finished_at=started - timedelta(hours=1), last_status="ok",
        )
    )
    await session.commit()

    row = await _status_of(client, auth_headers)
    assert row["status"] == "running"


async def test_status_is_running_when_the_start_exactly_equals_boot(
    app, client, auth_headers, session
):
    """The boundary: a start stamped at exactly the boot instant is this
    process's own claim, not a leftover from a process that died -- >=, not >."""
    app.state.started_at = BOOT
    session.add(
        ScheduledRun(name="drift", last_started_at=BOOT, last_finished_at=None)
    )
    await session.commit()

    row = await _status_of(client, auth_headers)
    assert row["status"] == "running"


async def test_status_is_interrupted_when_the_start_predates_boot_and_unfinished(
    app, client, auth_headers, session
):
    """A start before this process existed cannot be this process's claim --
    the process that made it is gone, so the run died with it. A proof, not a
    guess about how long is too long."""
    app.state.started_at = BOOT
    session.add(
        ScheduledRun(
            name="drift", last_started_at=BOOT - timedelta(seconds=1),
            last_finished_at=None, last_status="ok",
        )
    )
    await session.commit()

    row = await _status_of(client, auth_headers)
    assert row["status"] == "interrupted"


async def test_status_reports_the_recorded_status_once_finished(
    app, client, auth_headers, session
):
    app.state.started_at = BOOT
    started = BOOT + timedelta(minutes=1)
    session.add(
        ScheduledRun(
            name="drift", last_started_at=started,
            last_finished_at=started + timedelta(minutes=2), last_status="failed",
        )
    )
    await session.commit()

    row = await _status_of(client, auth_headers)
    assert row["status"] == "failed"


async def test_status_is_null_for_a_job_that_has_never_run(
    app, client, auth_headers, session
):
    app.state.started_at = BOOT
    session.add(ScheduledRun(name="drift"))
    await session.commit()

    row = await _status_of(client, auth_headers)
    assert row["status"] is None
    assert row["last_status"] is None


# --- /api/events ---


async def test_events_requires_a_session(client):
    response = await client.get("/api/events")
    assert response.status_code == 401


async def test_events_come_back_newest_first(client, auth_headers, session):
    session.add_all([
        EventLog(source="radarr", event_type="Download", payload={}, outcome="queued"),
        EventLog(source="sonarr", event_type="Download", payload={}, outcome="skipped"),
    ])
    await session.commit()
    await session.execute(
        text(
            "UPDATE events_log SET received_at = now() - interval '1 hour' "
            "WHERE source = 'radarr'"
        )
    )
    await session.commit()

    response = await client.get("/api/events", headers=auth_headers)
    events = response.json()["events"]
    assert [e["source"] for e in events] == ["sonarr", "radarr"]


async def test_events_limit_defaults_to_50(client, auth_headers, session):
    session.add_all(
        [EventLog(source="tautulli", event_type="x", payload={}) for _ in range(60)]
    )
    await session.commit()

    response = await client.get("/api/events", headers=auth_headers)
    assert len(response.json()["events"]) == 50


async def test_events_limit_is_respected(client, auth_headers, session):
    session.add_all(
        [EventLog(source="tautulli", event_type="x", payload={}) for _ in range(10)]
    )
    await session.commit()

    response = await client.get("/api/events", headers=auth_headers, params={"limit": 3})
    assert len(response.json()["events"]) == 3


async def test_events_limit_is_capped_above_its_maximum(client, auth_headers, session):
    session.add_all(
        [EventLog(source="tautulli", event_type="x", payload={}) for _ in range(250)]
    )
    await session.commit()

    response = await client.get("/api/events", headers=auth_headers, params={"limit": 10000})
    assert len(response.json()["events"]) == 200


async def test_events_never_include_the_payload(client, auth_headers, session):
    session.add(
        EventLog(
            source="manual", event_type="test", outcome="queued",
            payload={"secret_token": "should-never-leak"},
        )
    )
    await session.commit()

    response = await client.get("/api/events", headers=auth_headers)
    body = response.text
    assert "should-never-leak" not in body
    assert "payload" not in response.json()["events"][0]
