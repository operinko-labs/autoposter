"""GET /api/jobs and POST /api/jobs/{id}/cancel -- the jobs overview page."""
import json
from dataclasses import asdict
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Job
from autoposter.intake.arr import RenderIntent
from autoposter.queue.jobs import MAX_ATTEMPTS, enqueue

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

# The message ItemNotFound raises when Plex has not indexed the file yet
# (plex/client.py). The overview classifies on this prefix.
WAITING_ERROR = "no Plex item for movie 'Dune' (tmdb=1, tvdb=None)"


@pytest_asyncio.fixture
async def app(session_factory):
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
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _make_job(
    session,
    *,
    kind: str = "process_item",
    payload: dict | None = None,
    state: str = "pending",
    attempts: int = 0,
    last_error: str | None = None,
    delay_seconds: int = 0,
    dedupe_key: str | None = None,
) -> int:
    """One queue row in whatever state the test needs.

    Enqueued through the real ``enqueue`` so ``run_after`` is stamped by the
    database clock exactly as production does, then nudged into the state
    under test.
    """
    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=1)
    job_id = await enqueue(
        session,
        kind,
        asdict(intent) if payload is None else payload,
        dedupe_key=dedupe_key,
        delay_seconds=delay_seconds,
    )
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.state = state
    job.attempts = attempts
    job.last_error = last_error
    await session.commit()
    return job_id


# --- GET /api/jobs ---


async def test_jobs_requires_a_session(client):
    assert (await client.get("/api/jobs")).status_code == 401


async def test_lists_pending_and_running_jobs_ordered_by_run_after(
    client, auth_headers, session
):
    later = await _make_job(session, delay_seconds=600, dedupe_key="later")
    sooner = await _make_job(session, delay_seconds=5, dedupe_key="sooner")
    running = await _make_job(session, state="running", dedupe_key="running")

    response = await client.get("/api/jobs", headers=auth_headers)
    assert response.status_code == 200
    ids = [job["id"] for job in response.json()["jobs"]]
    assert ids == [running, sooner, later]


async def test_finished_jobs_are_not_listed(client, auth_headers, session):
    for index, state in enumerate(("done", "parked", "dismissed", "failed")):
        await _make_job(session, state=state, dedupe_key=f"k{index}")
    pending = await _make_job(session, dedupe_key="pending")

    response = await client.get("/api/jobs", headers=auth_headers)
    assert [job["id"] for job in response.json()["jobs"]] == [pending]


async def test_the_state_filter_narrows_the_list(client, auth_headers, session):
    pending = await _make_job(session, dedupe_key="pending")
    running = await _make_job(session, state="running", dedupe_key="running")

    only_pending = await client.get("/api/jobs?state=pending", headers=auth_headers)
    only_running = await client.get("/api/jobs?state=running", headers=auth_headers)

    assert [job["id"] for job in only_pending.json()["jobs"]] == [pending]
    assert [job["id"] for job in only_running.json()["jobs"]] == [running]


async def test_an_unknown_state_filter_is_rejected(client, auth_headers, session):
    response = await client.get("/api/jobs?state=parked", headers=auth_headers)
    assert response.status_code == 422


async def test_the_payload_identifies_the_item_without_being_echoed(
    client, auth_headers, session
):
    """The page names the item, so title/kind/season/episode are lifted out of
    the payload -- but the payload itself carries provider ids and, for other
    job kinds, filter internals that have no business in a UI response."""
    await _make_job(
        session,
        payload={
            "kind": "episode",
            "title": "Andor",
            "season_number": 2,
            "episode_number": 5,
            "tmdb_id": 999,
            "source_url": "https://provider.example/secret/path.jpg",
        },
    )

    body = (await client.get("/api/jobs", headers=auth_headers)).json()
    job = body["jobs"][0]

    assert job["title"] == "Andor"
    assert job["item_kind"] == "episode"
    assert job["season_number"] == 2
    assert job["episode_number"] == 5
    assert "payload" not in job
    # Nothing else smuggles it back either -- source URLs in particular are
    # provider credentials in query strings often enough to be worth a sweep.
    assert "provider.example" not in json.dumps(body)
    assert "999" not in json.dumps(body)


async def test_a_payload_without_item_fields_reports_them_as_null(
    client, auth_headers, session
):
    """A mode job's payload is its filters, not a RenderIntent."""
    await _make_job(session, kind="artwork_mode", payload={"filters": {"library": "Films"}})

    job = (await client.get("/api/jobs", headers=auth_headers)).json()["jobs"][0]
    assert job["kind"] == "artwork_mode"
    assert job["title"] is None
    assert job["item_kind"] is None
    assert job["season_number"] is None
    assert job["episode_number"] is None


async def test_a_job_waiting_for_plex_is_flagged_and_gets_the_plex_budget(
    app, client, auth_headers, session
):
    """The Plex wait has its own, much larger attempt budget
    (config.plex.resolve_max_attempts), so showing it against the generic
    queue cap would tell the operator a job is nearly dead when it is not."""
    await _make_job(session, attempts=3, last_error=WAITING_ERROR)

    job = (await client.get("/api/jobs", headers=auth_headers)).json()["jobs"][0]

    assert job["waiting_for_plex"] is True
    assert job["attempts"] == 3
    assert job["max_attempts"] == app.state.config_holder.current.plex.resolve_max_attempts


async def test_any_other_failure_gets_the_queue_attempt_cap(
    app, client, auth_headers, session
):
    await _make_job(session, attempts=3, last_error="RuntimeError: provider exploded")

    job = (await client.get("/api/jobs", headers=auth_headers)).json()["jobs"][0]

    assert job["waiting_for_plex"] is False
    assert job["max_attempts"] == MAX_ATTEMPTS
    # The two budgets must actually differ, or the assertion above passes for
    # a handler that never switches.
    assert MAX_ATTEMPTS != app.state.config_holder.current.plex.resolve_max_attempts


async def test_the_plex_budget_is_read_from_the_live_config(app, client, auth_headers, session):
    """Config is swapped in place at runtime (config/live.py), and
    resolve_max_attempts is one of the values documented as read per use. A
    handler holding the startup config would keep reporting the old budget."""
    await _make_job(session, attempts=1, last_error=WAITING_ERROR)

    swapped = load_config(EXAMPLE)
    swapped.plex.resolve_max_attempts = 17
    app.state.config_holder.swap(swapped)

    job = (await client.get("/api/jobs", headers=auth_headers)).json()["jobs"][0]
    assert job["max_attempts"] == 17


async def test_run_in_seconds_counts_down_to_the_next_attempt(client, auth_headers, session):
    await _make_job(session, delay_seconds=600)

    job = (await client.get("/api/jobs", headers=auth_headers)).json()["jobs"][0]
    assert 570 <= job["run_in_seconds"] <= 600


async def test_a_row_carries_its_state_error_and_creation_time(client, auth_headers, session):
    await _make_job(session, attempts=2, last_error="RuntimeError: boom")

    job = (await client.get("/api/jobs", headers=auth_headers)).json()["jobs"][0]
    assert job["state"] == "pending"
    assert job["last_error"] == "RuntimeError: boom"
    assert job["created_at"] is not None


async def test_a_pending_cancel_on_a_running_job_is_exposed(client, auth_headers, session):
    """The flag is client-only until it's in the list response: without it, an
    operator who reloads mid-attempt loses the fact that a cancel is already
    pending on this row and has no way to tell it apart from any other running
    job."""
    job_id = await _make_job(session, state="running")
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    job.cancel_requested = True
    await session.commit()

    listed = (await client.get("/api/jobs", headers=auth_headers)).json()["jobs"][0]
    assert listed["cancel_requested"] is True


async def test_an_ordinary_job_reports_no_pending_cancel(client, auth_headers, session):
    await _make_job(session)

    job = (await client.get("/api/jobs", headers=auth_headers)).json()["jobs"][0]
    assert job["cancel_requested"] is False


# --- POST /api/jobs/{id}/cancel ---


async def test_cancelling_a_pending_job_dismisses_it(client, auth_headers, session):
    job_id = await _make_job(session)

    response = await client.post(f"/api/jobs/{job_id}/cancel", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "dismissed"


async def test_cancelling_a_job_claimed_since_the_list_does_not_clobber_it(
    client, auth_headers, session
):
    """The claim race. The page lists a job as pending; a worker claims it
    before the operator clicks. Flipping it to ``dismissed`` there would strand
    a job the pool is actively running -- the row would read as cancelled while
    the handler kept going, and completing it would resurrect it. The running
    branch is taken instead: the cancellation is *requested*, and the worker
    honours it when the attempt ends.
    """
    job_id = await _make_job(session)
    # The claim, exactly as queue/jobs.py's claim() leaves the row.
    await session.execute(
        text(
            "UPDATE jobs SET state='running', claimed_by='worker-1', "
            "claimed_at=now(), attempts=attempts+1 WHERE id=:id"
        ),
        {"id": job_id},
    )
    await session.commit()

    response = await client.post(f"/api/jobs/{job_id}/cancel", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["cancel_requested"] is True
    assert "cancelled" not in body

    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == "running", "a running job must not be dismissed under the worker"
    assert job.cancel_requested is True


@pytest.mark.parametrize("state", ["done", "parked", "dismissed"])
async def test_cancelling_a_finished_job_is_a_conflict(client, auth_headers, session, state):
    job_id = await _make_job(session, state=state)

    response = await client.post(f"/api/jobs/{job_id}/cancel", headers=auth_headers)

    assert response.status_code == 409
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    await session.refresh(job)
    assert job.state == state


async def test_cancelling_an_unknown_job_is_a_404(client, auth_headers):
    assert (await client.post("/api/jobs/999/cancel", headers=auth_headers)).status_code == 404


async def test_cancel_requires_a_session(client, session):
    job_id = await _make_job(session)
    assert (await client.post(f"/api/jobs/{job_id}/cancel")).status_code == 401
