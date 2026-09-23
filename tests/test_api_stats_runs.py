"""``GET /api/stats/runs`` through the real app (roadmap row 53).

Through ``create_app`` + ``ASGITransport``, never through the handler alone
(memory: gated features need one test through the real entry point) -- what is
worth pinning is the WIRED surface: that the route is mounted, that it wears
row 51's ``api_key_or_session`` and is named in ``ALLOWLIST`` (both halves, or
the dependency's own path check refuses it), that it refuses an unkeyed
caller, and that nothing row 213 forbids reaches the body.

The counts themselves are pinned in ``tests/test_run_history.py``; this file
seeds the smallest rows that make the shape legible.
"""

from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api import auth as auth_module
from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Job, Run

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
# Row 51's one fixed fake, reused rather than invented again.
FAKE_KEY = "test-api-key-0123456789abcdef"
KEYED = {"X-API-Key": FAKE_KEY}
REFUSED = {"detail": "not authenticated"}
# A string shaped exactly like the disclosures row 213 exists to stop: a host
# filesystem path inside a job's last_error, which is what PlexPathMismatch
# deliberately carries.
LEAKY_ERROR = "PlexPathMismatch: /mnt/user/media/Movies is not /data/Movies"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
        api_key=FAKE_KEY,
    )


@pytest_asyncio.fixture
async def app(session_factory):
    return create_app(load_config(EXAMPLE), session_factory, _secrets())


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _seed(session):
    """One attributed full pass, one unattributed scheduled run, and a job
    carrying a path in its last_error that must never be served."""
    session.add(
        Run(
            kind="full_pass", name="full_pass", status="ok",
            finished_at=None, detail="drained: 5 processed, 1 failed, 0 deferred",
            rendered_poster=2, rendered_season_poster=0,
            rendered_background=1, rendered_title_card=0,
            processed=5, failed=1, deferred=0,
        )
    )
    session.add(Run(kind="scheduled", name="plex_prune", status="ok", detail="nothing to do"))
    session.add(Job(kind="process_item", payload={}, state="parked", last_error=LEAKY_ERROR))
    await session.commit()
    # finished_at cannot be a server default, so stamp it after the insert.
    from sqlalchemy import text
    await session.execute(
        text("UPDATE runs SET finished_at = started_at + interval '90 seconds'")
    )
    await session.commit()


async def test_the_endpoint_answers_the_documented_shape(client, session_headers, session):
    await _seed(session)

    response = await client.get("/api/stats/runs", headers=session_headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"runs", "generated_at"}
    assert isinstance(body["generated_at"], str)

    by_name = {run["name"]: run for run in body["runs"]}
    assert set(by_name) == {"full_pass", "plex_prune"}

    full = by_name["full_pass"]
    assert set(full) == {
        "id", "kind", "name", "started_at", "finished_at", "status",
        "duration_seconds", "rendered", "processed", "failed", "deferred",
        "server", "detail",
    }
    assert full["kind"] == "full_pass"
    assert full["status"] == "ok"
    assert full["duration_seconds"] == 90.0
    # Always all four art-kind keys, zero-filled -- api/stats.py's rule.
    assert full["rendered"] == {
        "poster": 2, "season_poster": 0, "background": 1, "title_card": 0
    }
    assert (full["processed"], full["failed"], full["deferred"]) == (5, 1, 0)
    # A full pass carries no server; its detail is the same narrowed sentence
    # its closer wrote.
    assert full["server"] is None
    assert full["detail"] == "drained: 5 processed, 1 failed, 0 deferred"


async def test_an_unattributed_run_serves_nulls_rather_than_zeroes(client, session_headers, session):
    """NULL means "this window was not attributed", which is every scheduled
    run by design. Zero would read as "this pass did nothing", which is a
    different claim and a false one."""
    await _seed(session)

    body = (await client.get("/api/stats/runs", headers=session_headers)).json()
    scheduled = next(run for run in body["runs"] if run["name"] == "plex_prune")

    assert scheduled["rendered"] is None
    assert scheduled["processed"] is None
    assert scheduled["failed"] is None
    assert scheduled["deferred"] is None


async def test_the_newest_runs_come_first_and_limit_bounds_the_page(client, session_headers, session):
    for index in range(4):
        session.add(Run(kind="scheduled", name=f"job-{index}", status="ok"))
        await session.commit()

    body = (await client.get("/api/stats/runs?limit=2", headers=session_headers)).json()

    assert len(body["runs"]) == 2
    ids = [run["id"] for run in body["runs"]]
    assert ids == sorted(ids, reverse=True), "the newest run must be first"


async def test_counted_serves_the_runs_with_counts_however_deep_they_sit(
    client, session_headers, session
):
    """The dashboard's counts chart asks for the runs that carry counts, and
    has to get them however many scheduled runs came after -- in production
    ``pending_deliveries`` records one every fifteen minutes, which buried the
    only completed full pass 1,337 rows deep and left the chart saying no full
    pass had ever completed. The unfiltered page is unchanged."""
    await _seed(session)
    for _ in range(5):
        session.add(Run(kind="scheduled", name="pending_deliveries", status="ok"))
        await session.commit()

    counted = (
        await client.get("/api/stats/runs?limit=2&counted=true", headers=session_headers)
    ).json()
    plain = (await client.get("/api/stats/runs?limit=2", headers=session_headers)).json()

    assert [run["name"] for run in counted["runs"]] == ["full_pass"]
    assert [run["name"] for run in plain["runs"]] == ["pending_deliveries"] * 2


async def test_an_absurd_limit_is_clamped_rather_than_refused(client, session_headers, session):
    """A widget's misconfiguration must not be able to ask for the whole
    table, and must not be answered with a 422 an operator then has to debug
    from a dashboard tile that just says `error`."""
    await _seed(session)

    for limit in ("0", "-5", "100000"):
        response = await client.get(f"/api/stats/runs?limit={limit}", headers=session_headers)
        assert response.status_code == 200, limit
        assert 1 <= len(response.json()["runs"]) <= 500


async def test_a_key_reads_the_run_stats_without_a_session(client, session):
    """Row 51's key on row 53's route. Both halves must agree: the handler
    takes ``api_key_or_session`` AND the path is in ``ALLOWLIST``, or the
    dependency refuses its own route."""
    await _seed(session)
    assert "/api/stats/runs" in auth_module.ALLOWLIST

    response = await client.get("/api/stats/runs", headers=KEYED)

    assert response.status_code == 200
    assert len(response.json()["runs"]) == 2


async def test_the_endpoint_refuses_a_request_with_no_credential(client):
    response = await client.get("/api/stats/runs")

    assert response.status_code == 401
    assert response.json() == REFUSED


async def test_no_served_string_carries_a_path_or_an_error(client, session_headers, session):
    """Row 213 on the new surface. A per-run "top errors" breakdown built by
    grouping ``jobs.last_error`` would re-serve whatever those strings hold --
    and PlexPathMismatch deliberately carries the operator's host filesystem
    paths. This endpoint serves counts, class names, timestamps and the
    already-narrowed ``detail`` copy, and this asserts the difference."""
    await _seed(session)

    text_body = (await client.get("/api/stats/runs", headers=session_headers)).text

    assert LEAKY_ERROR not in text_body
    assert "/mnt/user" not in text_body
    assert "PlexPathMismatch" not in text_body


async def test_the_runs_list_shows_a_catch_up_by_server_with_its_sentence(
    client, session_headers, session
):
    """Spec §5: the runs list groups a catch-up by ``server`` and shows the
    counts sentence its close stamped in ``detail`` -- both added by this
    task beside ``processed``/``failed``/``deferred``, which a catch-up
    already served.

    ``processed``/``failed``/``deferred`` are stamped here the way
    ``catchup.finish_catch_up`` stamps them, so ``rendered`` exercises the
    ``CATCH_UP_KIND`` guard in ``api/stats.py::_rendered`` rather than only
    its pre-existing ``processed is None`` branch -- a catch-up's own tallies
    must never be reported as "nothing was composited".
    """
    from sqlalchemy import update

    from autoposter.db.models import Run
    from autoposter.scheduler.run_history import close_run, open_run

    run_id = await open_run(session, kind="catch_up", name="catch_up:jellyfin")
    await session.execute(
        update(Run).where(Run.id == run_id).values(
            server="jellyfin", processed=41, failed=1, deferred=0,
        )
    )
    await close_run(session, run_id, status="ok", detail="catch-up: 40 done, 1 failed")
    await session.commit()

    rows = (await client.get("/api/stats/runs", headers=session_headers)).json()["runs"]

    row = next(r for r in rows if r["id"] == run_id)
    assert row["kind"] == "catch_up" and row["server"] == "jellyfin"
    assert row["detail"] == "catch-up: 40 done, 1 failed"
    assert (row["processed"], row["failed"], row["deferred"]) == (41, 1, 0)
    # The false claim this guard exists to stop: a catch-up never renders
    # anything, so this must be None despite carrying a non-null `processed`.
    assert row["rendered"] is None


def test_the_readme_documents_the_runs_widget_with_a_header_and_never_a_query_string():
    """Row 52's documentation test, extended to row 53's recipe. The
    query-string form is the ONE thing not copied from Posterizarr, whose docs
    put ``?api_key=`` in every example -- a key there lands in an ingress
    access log and a browser history."""
    readme = (Path(__file__).parent.parent / "deploy" / "README.md").read_text(
        encoding="utf-8"
    )

    assert "#### Homepage `customapi` recipe: runs" in readme
    assert "/api/stats/runs" in readme
    assert 'X-API-Key: "{{HOMEPAGE_VAR_AUTOPOSTER_API_KEY}}"' in readme
    # The two things an operator cannot infer from the numbers themselves.
    assert "window attribution" in readme
    assert "500 rows per" in readme
    for line in readme.splitlines():
        if line.strip().startswith("url:"):
            assert "?" not in line, f"a customapi recipe put a credential in the URL: {line}"
