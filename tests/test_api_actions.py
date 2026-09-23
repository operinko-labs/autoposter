"""GET /api/jobs/parked, retry/dismiss, POST /api/items/{id}/reprocess and
GET /api/config."""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Job, Render
from autoposter.queue.jobs import fail

from conftest import seed_media_item

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

# Recognisable secret values used by the redaction test -- chosen to be
# unlikely to appear anywhere else in the response by accident. Every one of
# them is distinct: seeding "x" everywhere left only tmdb_token genuinely
# asserted on, since "x" appears in any response by chance.
SECRET_TMDB_TOKEN = "sekrit-tmdb-token-should-never-leak-9f31c2"
SECRET_PLEX_TOKEN = "sekrit-plex-token-should-never-leak-4b7ade"
SECRET_WEBHOOK_SECRET = "sekrit-webhook-secret-should-never-leak-c05e18"
# The hash the app actually holds. bcrypt is salted, so hashing PASSWORD
# again in the assertion would produce a different string that could never
# appear in a response whether the endpoint leaked or not.
ADMIN_PASSWORD_HASH = hash_password(PASSWORD)


@pytest_asyncio.fixture
async def client(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token=SECRET_PLEX_TOKEN, tmdb_token=SECRET_TMDB_TOKEN, tvdb_apikey="x",
        fanart_apikey="x", webhook_secret=SECRET_WEBHOOK_SECRET,
        admin_password_hash=ADMIN_PASSWORD_HASH,
    )
    app = create_app(load_config(EXAMPLE), session_factory, secrets)
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


def _parked_job(**overrides):
    fields = {
        "kind": "process_item",
        "payload": {"kind": "movie", "title": "A"},
        "state": "parked",
        "attempts": 5,
        "last_error": "Plex never scanned the file",
    }
    fields.update(overrides)
    return Job(**fields)


# --- GET /api/jobs/parked ---


async def test_parked_jobs_requires_a_session(client):
    response = await client.get("/api/jobs/parked")
    assert response.status_code == 401


async def test_parked_jobs_lists_them_with_their_reasons(client, auth_headers, session):
    session.add(_parked_job(last_error="Plex never scanned the file"))
    await session.commit()

    response = await client.get("/api/jobs/parked", headers=auth_headers)
    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["reason"] == "Plex never scanned the file"
    assert jobs[0]["attempts"] == 5


async def test_parked_jobs_pagination_reaches_past_the_first_page(client, auth_headers, session):
    """Without an offset, anything past the first ``limit`` parked jobs is
    unreachable -- and a backlog is exactly when this endpoint matters."""
    session.add_all([_parked_job(last_error=f"reason {i}") for i in range(3)])
    await session.commit()

    first_page = await client.get(
        "/api/jobs/parked", headers=auth_headers, params={"limit": 2, "offset": 0}
    )
    second_page = await client.get(
        "/api/jobs/parked", headers=auth_headers, params={"limit": 2, "offset": 2}
    )

    first_ids = [job["id"] for job in first_page.json()["jobs"]]
    second_ids = [job["id"] for job in second_page.json()["jobs"]]
    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert not set(first_ids) & set(second_ids)


async def test_parked_jobs_excludes_other_states(client, auth_headers, session):
    session.add_all([
        Job(kind="process_item", payload={}, state="pending"),
        Job(kind="process_item", payload={}, state="done"),
        _parked_job(),
    ])
    await session.commit()

    response = await client.get("/api/jobs/parked", headers=auth_headers)
    assert len(response.json()["jobs"]) == 1


async def test_parked_jobs_lifts_the_naming_fields_from_a_real_render_intent_payload(
    client, auth_headers, session
):
    """The report this fix answers: an operator on Failures saw a job id and a
    bare reason class ("SourceRefused") with no way to tell which show,
    episode or movie it was about. `api/jobs.py:134-138`'s precedent -- the
    Jobs page lifts four payload fields out by name rather than echoing the
    payload wholesale -- applies here too."""
    from dataclasses import asdict

    from autoposter.intake.arr import RenderIntent

    intent = RenderIntent(
        kind="episode", title="Andor", tmdb_id=64677, season_number=2, episode_number=5,
    )
    session.add(_parked_job(payload=asdict(intent)))
    await session.commit()

    response = await client.get("/api/jobs/parked", headers=auth_headers)
    job = response.json()["jobs"][0]
    assert job["title"] == "Andor"
    assert job["item_kind"] == "episode"
    assert job["season_number"] == 2
    assert job["episode_number"] == 5
    # Never echoed wholesale: the payload can carry provider ids and, for
    # other kinds, source URLs.
    assert "payload" not in job


async def test_a_source_refused_park_serves_its_full_reason_on_failures(
    client, auth_headers, session
):
    """Through the real entry points: the worker's own `run_once` parks the
    job exactly as production does, and the Failures page's own endpoint is
    what serves the reason. `SourceRefused` now carries `served_detail = True`
    (roadmap row 213's contract), so the operator sees the stage-bearing
    sentence instead of the bare class name."""
    from dataclasses import asdict

    from autoposter.intake.arr import RenderIntent
    from autoposter.queue.worker import run_once
    from autoposter.render.pipeline import SourceRefused
    from queue_support import enqueue_due

    async def handler(session_, job):
        exc = SourceRefused(
            "the clearlogo did not decode after download (DecompressionBombError)"
        )
        exc.max_attempts = 1
        raise exc

    intent = RenderIntent(kind="movie", title="Sinners", tmdb_id=1234)
    await enqueue_due(session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key)
    await run_once(session, "worker-1", {"process_item": handler})

    response = await client.get("/api/jobs/parked", headers=auth_headers)
    job = response.json()["jobs"][0]
    assert job["reason"] == (
        "SourceRefused: the clearlogo did not decode after download "
        "(DecompressionBombError)"
    )


async def test_a_runtime_error_park_keeps_the_bare_class_name_on_failures(
    client, auth_headers, session, session_factory
):
    """The contrast the test above is only meaningful against: an untagged
    exception class stays class-name-only, per the class-name-only default
    the row-213 marker is an exception to, not a replacement for."""
    from dataclasses import asdict

    from autoposter.intake.arr import RenderIntent
    from autoposter.queue.jobs import MAX_ATTEMPTS
    from autoposter.queue.worker import run_once
    from queue_support import enqueue_due, make_due

    async def handler(session_, job):
        raise RuntimeError("provider exploded")

    intent = RenderIntent(kind="movie", title="Dune", tmdb_id=5678)
    job_id = await enqueue_due(
        session, "process_item", asdict(intent), dedupe_key=intent.dedupe_key
    )
    for _ in range(MAX_ATTEMPTS):
        # A fresh session per simulated attempt, matching run_worker's own
        # "async with session_factory() as session" per claim (worker.py):
        # reusing one session let a stale, never-expired identity-map ``Job``
        # mask claim()'s raw-SQL state/attempts writes between iterations.
        async with session_factory() as attempt_session:
            await make_due(attempt_session, job_id)
            # The fresh session alone is not enough here: this ``job`` local
            # stays alive across the call below (unlike run_worker's, which
            # is claim()'s own and dies with the loop iteration), keeping the
            # identity map's cached instance alive too, so claim()'s raw-SQL
            # writes would still be masked without this explicit expire.
            attempt_session.expire_all()
            await run_once(attempt_session, "worker-1", {"process_item": handler})

    response = await client.get("/api/jobs/parked", headers=auth_headers)
    job = response.json()["jobs"][0]
    assert job["reason"] == "RuntimeError"


# --- POST /api/jobs/{job_id}/retry ---


async def test_retry_requires_a_session(client, session):
    session.add(_parked_job())
    await session.commit()
    response = await client.post("/api/jobs/1/retry")
    assert response.status_code == 401


async def test_retry_makes_a_parked_job_claimable_again(client, auth_headers, session):
    session.add(_parked_job(attempts=5))
    await session.commit()
    job_id = (await session.execute(select(Job))).scalars().one().id

    response = await client.post(f"/api/jobs/{job_id}/retry", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["state"] == "pending"

    row = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.state == "pending"
    assert row.attempts == 0


async def test_retrying_a_job_whose_item_is_already_queued_is_409_not_500(
    client, auth_headers, session
):
    """uq_jobs_pending_dedupe allows one pending job per dedupe key. An item
    parks after repeated failures, a webhook then queues a fresh pending job
    for the same item, and Retry collides with the index. Every production
    job carries a dedupe_key -- the other tests here leave it None, which is
    why this went unnoticed."""
    dedupe_key = "movie:tmdb:603"
    session.add_all([
        _parked_job(dedupe_key=dedupe_key),
        Job(kind="process_item", payload={}, state="pending", dedupe_key=dedupe_key),
    ])
    await session.commit()
    parked_id = (
        await session.execute(select(Job.id).where(Job.state == "parked"))
    ).scalar_one()

    response = await client.post(f"/api/jobs/{parked_id}/retry", headers=auth_headers)
    assert response.status_code == 409
    assert "already queued" in response.json()["detail"]

    session.expire_all()  # read the row back from the database, not the map
    row = (await session.execute(select(Job).where(Job.id == parked_id))).scalar_one()
    assert row.state == "parked"


async def test_retrying_a_job_whose_item_is_deferred_is_also_409(
    client, auth_headers, session
):
    """uq_jobs_pending_dedupe now also covers ``deferred`` rows, so retrying a
    parked job collides the same way when the item's other job is off waiting
    on Plex rather than pending outright."""
    dedupe_key = "movie:tmdb:604"
    session.add_all([
        _parked_job(dedupe_key=dedupe_key),
        Job(kind="process_item", payload={}, state="deferred", dedupe_key=dedupe_key),
    ])
    await session.commit()
    parked_id = (
        await session.execute(select(Job.id).where(Job.state == "parked"))
    ).scalar_one()

    response = await client.post(f"/api/jobs/{parked_id}/retry", headers=auth_headers)
    assert response.status_code == 409
    assert "already queued" in response.json()["detail"]

    session.expire_all()  # read the row back from the database, not the map
    row = (await session.execute(select(Job).where(Job.id == parked_id))).scalar_one()
    assert row.state == "parked"


async def test_retrying_an_unknown_job_is_404(client, auth_headers):
    response = await client.post("/api/jobs/999999/retry", headers=auth_headers)
    assert response.status_code == 404


async def test_retry_then_repark_reuses_the_same_row(client, auth_headers, session):
    """retry_job flips the existing parked row back to pending rather than
    inserting a new one. Confirmed here, not just read from the source: retry
    it, then fail it back to parked (fail()'s own park-sibling sweep would
    dismiss a second row for this dedupe_key if one existed), and check
    exactly one row for the key remains -- the same id, carrying the fresh
    reason."""
    dedupe_key = "movie:tmdb:900"
    session.add(_parked_job(dedupe_key=dedupe_key, attempts=5))
    await session.commit()
    job_id = (await session.execute(select(Job.id))).scalar_one()

    response = await client.post(f"/api/jobs/{job_id}/retry", headers=auth_headers)
    assert response.status_code == 200

    session.expire_all()
    # max_attempts=0: retry() reset attempts to 0, and this asserts the park
    # arm fires without depending on a separate claim() call to bump it.
    state = await fail(session, job_id, "boom again", max_attempts=0)
    assert state == "parked"

    rows = (
        await session.execute(select(Job).where(Job.dedupe_key == dedupe_key))
    ).scalars().all()
    assert len(rows) == 1, "retry-then-repark must not duplicate the row"
    assert rows[0].id == job_id
    assert rows[0].last_error == "boom again"


async def test_retrying_a_job_that_is_not_parked_is_404_not_500(client, auth_headers, session):
    session.add(Job(kind="process_item", payload={}, state="pending"))
    await session.commit()
    job_id = (await session.execute(select(Job))).scalars().one().id

    response = await client.post(f"/api/jobs/{job_id}/retry", headers=auth_headers)
    assert response.status_code == 404


async def test_retrying_an_already_dismissed_job_is_404(client, auth_headers, session):
    session.add(_parked_job())
    await session.commit()
    job_id = (await session.execute(select(Job))).scalars().one().id
    await client.post(f"/api/jobs/{job_id}/dismiss", headers=auth_headers)

    response = await client.post(f"/api/jobs/{job_id}/retry", headers=auth_headers)
    assert response.status_code == 404


# --- POST /api/jobs/{job_id}/dismiss ---


async def test_dismiss_requires_a_session(client, session):
    session.add(_parked_job())
    await session.commit()
    response = await client.post("/api/jobs/1/dismiss")
    assert response.status_code == 401


async def test_dismiss_marks_without_deleting_the_row(client, auth_headers, session):
    session.add(_parked_job(last_error="disk full"))
    await session.commit()
    job_id = (await session.execute(select(Job))).scalars().one().id

    response = await client.post(f"/api/jobs/{job_id}/dismiss", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["state"] == "dismissed"

    row = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    assert row is not None
    assert row.state == "dismissed"
    assert row.last_error == "disk full"


async def test_dismissing_an_unknown_job_is_404(client, auth_headers):
    response = await client.post("/api/jobs/999999/dismiss", headers=auth_headers)
    assert response.status_code == 404


async def test_dismissing_an_already_dismissed_job_is_404_not_500(client, auth_headers, session):
    session.add(_parked_job())
    await session.commit()
    job_id = (await session.execute(select(Job))).scalars().one().id
    first = await client.post(f"/api/jobs/{job_id}/dismiss", headers=auth_headers)
    assert first.status_code == 200

    second = await client.post(f"/api/jobs/{job_id}/dismiss", headers=auth_headers)
    assert second.status_code == 404

    row = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    assert row.state == "dismissed"


# --- POST /api/items/{item_id}/reprocess ---


async def test_reprocess_requires_a_session(client, session):
    await seed_media_item(session, "rk1", library="Movies", kind="movie", title="A")
    response = await client.post("/api/items/1/reprocess")
    assert response.status_code == 401


async def test_reprocess_enqueues_a_job(client, auth_headers, session):
    item = await seed_media_item(session, "rk1", library="Movies", kind="movie", title="A")
    item_id = item.id

    response = await client.post(f"/api/items/{item_id}/reprocess", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["queued"] is True
    assert body["job_id"] is not None

    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].kind == "process_item"
    assert jobs[0].state == "pending"


async def test_reprocess_carries_the_items_plex_rating_key(client, auth_headers, session):
    """Same reason as the full pass: the row already knows exactly which Plex
    item this is, so the job resolves by rating key instead of by external ids
    that -- for an adopted season or episode -- are the item's own rather than
    the series', and so resolve to nothing or to the wrong thing. The dedupe
    key is asserted alongside it because it must not have moved."""
    item = await seed_media_item(
        session, "77632", library="TV", kind="episode", title="The Pirate Solution",
        tmdb_id=64677, tvdb_id=1123661, season_number=3, episode_number=4,
    )
    item_id = item.id

    await client.post(f"/api/items/{item_id}/reprocess", headers=auth_headers)

    job = (await session.execute(select(Job))).scalars().one()
    assert job.payload["refs"]["plex"] == "77632"
    assert job.dedupe_key == "process_item:episode:tmdb64677:s03e04"


async def test_reprocessing_twice_still_queues_once(client, auth_headers, session):
    item = await seed_media_item(session, "rk1", library="Movies", kind="movie", title="A")
    item_id = item.id

    first = await client.post(f"/api/items/{item_id}/reprocess", headers=auth_headers)
    second = await client.post(f"/api/items/{item_id}/reprocess", headers=auth_headers)

    assert first.json()["queued"] is True
    assert second.json()["queued"] is False
    assert second.json()["job_id"] is None

    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1


async def test_reprocessing_an_unknown_item_is_404(client, auth_headers):
    response = await client.post("/api/items/999999/reprocess", headers=auth_headers)
    assert response.status_code == 404


# --- the `note` field on POST /items/{item_id}/reprocess ---
#
# `note` used to carry a warning when the row being re-run had an identity
# twin under another Plex rating key -- possible only because a row was keyed
# on Plex's own id. Rows are now keyed on identity (spec §4.2), so a twin for
# one identity cannot exist and `note` is always null. It stays in the
# response rather than disappearing, so a client need not special-case its
# absence.


async def test_reprocess_note_is_always_null(client, auth_headers, session):
    item = await seed_media_item(
        session, "rk-solo", library="Movies", kind="movie", title="A", tmdb_id=555,
    )
    item_id = item.id

    body = (await client.post(
        f"/api/items/{item_id}/reprocess", headers=auth_headers
    )).json()

    assert body["queued"] is True
    assert "note" in body
    assert body["note"] is None


# --- reprocess re-verifies the source (perf workstream B1) ---


async def test_reprocess_clears_the_fingerprint_of_every_render_of_the_item(
    client, auth_headers, session
):
    """render_artifact now trusts a stored fingerprint enough to skip
    downloading an unmoved provider image, so an operator's reprocess clears
    it: that is the explicit "re-check the bytes" the spec keeps. Every art
    kind of the item, because the job renders every one; nothing of another
    item; and not badge_fingerprint, which the spec leaves alone."""
    item = await seed_media_item(
        session, "rk-fp", library="Movies", kind="movie", title="A", tmdb_id=4101,
    )
    other = await seed_media_item(
        session, "rk-other", library="Movies", kind="movie", title="B", tmdb_id=4102,
    )
    renders = [
        Render(item_id=item.id, art_kind="poster", status="rendered",
               asset_path="/assets/p.jpg", fingerprint="f" * 64, badge_fingerprint="b" * 64),
        Render(item_id=item.id, art_kind="background", status="rendered",
               asset_path="/assets/b.jpg", fingerprint="f" * 64),
        Render(item_id=other.id, art_kind="poster", status="rendered",
               asset_path="/assets/o.jpg", fingerprint="k" * 64),
    ]
    session.add_all(renders)
    await session.commit()

    response = await client.post(f"/api/items/{item.id}/reprocess", headers=auth_headers)
    assert response.status_code == 200

    for render in renders:
        await session.refresh(render)
    poster, background, untouched = renders
    assert poster.fingerprint is None
    assert background.fingerprint is None
    assert poster.badge_fingerprint == "b" * 64
    assert untouched.fingerprint == "k" * 64


async def test_a_deduped_reprocess_still_clears_the_fingerprint(client, auth_headers, session):
    """The second press queues nothing (the first job is still pending) but
    the operator still asked for a re-check, and the pending job reads the row
    when it runs."""
    item = await seed_media_item(
        session, "rk-dd", library="Movies", kind="movie", title="A", tmdb_id=4103,
    )
    render = Render(item_id=item.id, art_kind="poster", status="rendered",
                    asset_path="/assets/p.jpg", fingerprint="f" * 64)
    session.add(render)
    await session.commit()

    first = await client.post(f"/api/items/{item.id}/reprocess", headers=auth_headers)
    assert first.json()["queued"] is True
    render.fingerprint = "g" * 64
    await session.commit()

    second = await client.post(f"/api/items/{item.id}/reprocess", headers=auth_headers)
    assert second.json()["queued"] is False
    await session.refresh(render)
    assert render.fingerprint is None


# --- GET /api/config ---


async def test_config_requires_a_session(client):
    response = await client.get("/api/config")
    assert response.status_code == 401


async def test_config_returns_the_configuration_shape(client, auth_headers):
    response = await client.get("/api/config", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    # From config/autoposter.example.yaml -- proves this is the real config,
    # not an empty stub.
    assert body["workers"] == 5
    assert "plex" in body
    assert "artwork" in body


async def test_config_never_leaks_a_secret_value(client, auth_headers):
    """Seeds recognisable secrets and asserts none appears anywhere in the
    serialised response body, rather than checking specific fields -- a
    field-by-field assertion would silently miss a key added later."""
    response = await client.get("/api/config", headers=auth_headers)
    assert response.status_code == 200
    assert SECRET_TMDB_TOKEN not in response.text
    assert SECRET_PLEX_TOKEN not in response.text
    assert SECRET_WEBHOOK_SECRET not in response.text
    # The admin hash the app is actually holding, not a fresh hash of the
    # same password: bcrypt is salted, so a fresh one could never appear in
    # any response and would assert nothing.
    assert ADMIN_PASSWORD_HASH not in response.text


NOTIFY_URL_TOKEN = "sekrit-webhook-path-token-should-never-leak-7d20aa"
NOTIFY_URL = f"http://hooks.example.test/notify/{NOTIFY_URL_TOKEN}"


async def test_config_reduces_the_notification_url_to_its_host(session_factory):
    """``notifications.url`` lives in Config, not Secrets, so the wholesale
    Secrets redaction never touches it -- but it may embed a token in its
    path (Uptime-Kuma style). The same stance as the events_log rows
    (``Notifier._record_failure``): payloads that reach the operator over
    HTTP carry the host only. The full URL stays in the operator's own
    config file."""
    config = load_config(EXAMPLE)
    config.notifications.url = NOTIFY_URL
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=ADMIN_PASSWORD_HASH,
    )
    app = create_app(config, session_factory, secrets)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login = await c.post("/api/login", json={"password": PASSWORD})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        response = await c.get("/api/config", headers=headers)

    assert response.status_code == 200
    assert NOTIFY_URL_TOKEN not in response.text
    assert response.json()["notifications"]["url"] == "hooks.example.test"
