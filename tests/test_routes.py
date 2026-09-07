import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog, Job

FIXTURES = Path(__file__).parent / "fixtures" / "webhooks"
EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
TOKEN = "test-secret"

# The one string the sender and the Events feed both get. Written out here
# rather than imported from the route module: importing it would make a
# rename of the sentence pass silently, and the sentence is the contract.
REFUSED = "body does not match the webhook schema for this service"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def secrets():
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret=TOKEN,
    )


@pytest_asyncio.fixture
async def client(session_factory, secrets):
    app = create_app(load_config(EXAMPLE), session_factory, secrets)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz_is_open(client):
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_missing_token_is_rejected(client):
    response = await client.post("/webhook/radarr", json=load("radarr_download.json"))
    assert response.status_code == 401


async def test_wrong_token_is_rejected(client):
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": "nope"},
    )
    assert response.status_code == 401


async def test_radarr_download_enqueues_one_job(client, session):
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["queued"] == 1
    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].kind == "process_item"
    assert jobs[0].payload["tmdb_id"] == 693134


async def test_sonarr_episode_enqueues_three_jobs(client, session):
    response = await client.post(
        "/webhook/sonarr",
        json=load("sonarr_download_single.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.json()["queued"] == 3
    jobs = (await session.execute(select(Job))).scalars().all()
    assert {j.payload["kind"] for j in jobs} == {"show", "season", "episode"}


async def test_repeated_delivery_is_coalesced(client, session):
    payload = load("sonarr_download_single.json")
    headers = {"X-Autoposter-Token": TOKEN}
    first = await client.post("/webhook/sonarr", json=payload, headers=headers)
    second = await client.post("/webhook/sonarr", json=payload, headers=headers)
    assert first.json()["queued"] == 3
    assert second.json()["queued"] == 0
    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 3


async def test_test_payload_is_acknowledged_without_queueing(client, session):
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_test.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["queued"] == 0
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_every_delivery_is_logged(client, session):
    await client.post(
        "/webhook/radarr",
        json=load("radarr_test.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].source == "radarr"
    assert events[0].event_type == "Test"


async def test_jobs_are_delayed_by_the_settle_window(client, session):
    await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": TOKEN},
    )
    from datetime import timedelta

    job = (await session.execute(select(Job))).scalar_one()
    # Compare against the database clock, never this process's clock: the two
    # can drift (they measurably do on this machine), and run_after is
    # computed by Postgres via func.now() + settle_seconds.
    db_now = (await session.execute(select(func.now()))).scalar_one()
    assert job.run_after > db_now + timedelta(seconds=20)


async def test_non_ascii_token_is_rejected_not_500(client):
    # httpx requires header values to be ASCII-safe str or raw bytes; send the
    # UTF-8 bytes directly to get a genuinely non-ASCII header on the wire.
    response = await client.post(
        "/webhook/radarr",
        json=load("radarr_download.json"),
        headers={"X-Autoposter-Token": "tökén-é".encode("utf-8")},
    )
    assert response.status_code == 401


async def test_invalid_json_body_returns_400_and_is_logged(client, session):
    response = await client.post(
        "/webhook/radarr",
        content=b"{not valid json",
        headers={
            "X-Autoposter-Token": TOKEN,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].source == "radarr"
    assert "_raw" in events[0].payload


async def test_non_object_json_body_returns_400_and_is_logged(client, session):
    response = await client.post(
        "/webhook/radarr",
        content=b"[1, 2, 3]",
        headers={
            "X-Autoposter-Token": TOKEN,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].source == "radarr"
    assert "_raw" in events[0].payload


async def test_an_unparseable_body_is_reported_by_class_name_only(client, session):
    """The 400 detail goes back to the sender and the same string is
    EventLog.outcome, served by /api/events; a decode error's own text
    quotes positions and bytes of the payload. Class name only on both --
    the capped raw body on the row stays the evidence."""
    response = await client.post(
        "/webhook/radarr",
        content=b'{"eventType": "Download", "movie": {"folderPath": "/mnt/media/Movies/Dune"',
        headers={"X-Autoposter-Token": TOKEN, "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unparseable payload (JSONDecodeError)"
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].outcome == "unparseable payload (JSONDecodeError)"
    assert "/mnt/media/Movies/Dune" in events[0].payload["_raw"]


async def test_a_malformed_movie_is_the_senders_junk_not_our_bug(client, session):
    """Was a 500 -- `parser error (AttributeError)` -- until the intake gate
    existed: `movie` as a string reached `parse_radarr`, which called `.get` on
    it. A body whose shape the sender chose is a 400, and the structured
    payload is still kept on the row as evidence (never a "_raw" text blob).
    `payload` is not selected by `events_snapshot`, so none of it is served."""
    response = await client.post(
        "/webhook/radarr",
        json={"eventType": "Download", "movie": "not-an-object"},
        headers={"X-Autoposter-Token": TOKEN},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == REFUSED
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].source == "radarr"
    assert events[0].outcome == REFUSED
    assert "eventType" in events[0].payload
    assert "_raw" not in events[0].payload


async def test_a_parser_bug_is_still_a_500_recorded_by_class_name_only(
    monkeypatch, session_factory, secrets, session
):
    """The gate now refuses the sender's junk, so the only way left into the
    parser-error branch is a bug in the parser -- which must still surface as a
    500 with the structured payload kept and the served outcome carrying the
    class name only. Monkeypatched rather than provoked by a body, because no
    body can reach `parse_radarr` malformed any more; without this the branch
    would become untested dead code."""

    def boom(payload):
        raise RuntimeError("boom")

    monkeypatch.setattr("autoposter.intake.routes.parse_radarr", boom)
    app = create_app(load_config(EXAMPLE), session_factory, secrets)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as broken_client:
        response = await broken_client.post(
            "/webhook/radarr",
            json=load("radarr_download.json"),
            headers={"X-Autoposter-Token": TOKEN},
        )

    assert response.status_code == 500
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    assert events[0].source == "radarr"
    assert events[0].outcome == "parser error (RuntimeError)"
    assert "boom" not in events[0].outcome
    assert "eventType" in events[0].payload
    assert "_raw" not in events[0].payload


@pytest.mark.parametrize(
    "fixture", ["radarr_download.json", "radarr_rename.json", "radarr_movie_added.json"]
)
async def test_every_radarr_fixture_passes_the_gate(client, fixture):
    """The gate must not refuse a real delivery. Each fixture is a body one of
    the three provisioned Radarr triggers actually sends."""
    response = await client.post(
        "/webhook/radarr", json=load(fixture), headers={"X-Autoposter-Token": TOKEN}
    )
    assert response.status_code == 200
    assert response.json()["queued"] >= 1


@pytest.mark.parametrize(
    "fixture",
    [
        "sonarr_download_single.json",
        "sonarr_import_complete_seasonpack.json",
        "sonarr_rename.json",
        "sonarr_series_add.json",
    ],
)
async def test_every_sonarr_fixture_passes_the_gate(client, fixture):
    response = await client.post(
        "/webhook/sonarr", json=load(fixture), headers={"X-Autoposter-Token": TOKEN}
    )
    assert response.status_code == 200
    assert response.json()["queued"] >= 1


async def test_the_sonarr_test_payload_is_acknowledged_without_queueing(client, session):
    """Test goes through the unaccepted-event branch and is never shape-checked.
    Its body is the least stable of all -- Sonarr's sends `year: 0`, Radarr's
    carries a `remoteMovie` block no other event has -- and it is the one
    delivery an operator watches live while wiring the service up."""
    response = await client.post(
        "/webhook/sonarr", json=load("sonarr_test.json"), headers={"X-Autoposter-Token": TOKEN}
    )
    assert response.status_code == 200
    assert response.json()["queued"] == 0
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [(e.event_type, e.outcome) for e in events] == [("Test", "0 intents")]


async def test_a_body_with_no_event_type_is_refused(client, session):
    """No envelope at all: the row keeps a null event_type, because the refusal
    happens before there is anything to record."""
    response = await client.post(
        "/webhook/radarr", json={"hello": "world"}, headers={"X-Autoposter-Token": TOKEN}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == REFUSED
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [(e.source, e.event_type, e.outcome) for e in events] == [("radarr", None, REFUSED)]
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_an_accepted_event_with_no_movie_is_refused(client, session):
    """The envelope validated, so this row DOES carry the event type: the feed
    says which delivery was refused. Only an envelope failure leaves it null."""
    response = await client.post(
        "/webhook/radarr",
        json={"eventType": "Download"},
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == REFUSED
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [(e.event_type, e.outcome) for e in events] == [("Download", REFUSED)]


async def test_a_string_where_an_id_belongs_is_refused_at_both_levels(client, session):
    """One rule, two levels. Pydantic's lax mode would coerce "693134" to an
    int; a quoted id is precisely what a scanner produces and what neither
    *arr ever sends, so both models type their numbers as StrictInt."""
    headers = {"X-Autoposter-Token": TOKEN}

    movie = load("radarr_download.json")
    movie["movie"]["tmdbId"] = "693134"
    first = await client.post("/webhook/radarr", json=movie, headers=headers)

    show = load("sonarr_download_single.json")
    show["episodes"][0]["seasonNumber"] = "2"
    second = await client.post("/webhook/sonarr", json=show, headers=headers)

    assert first.status_code == 400
    assert second.status_code == 400
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_a_health_event_is_acknowledged_not_refused(client, session):
    """A Health, Grab or ApplicationUpdate body carries no `movie`/`series` key
    at all. Refusing one would put a warning line in the operator's own Radarr
    log on every health event -- noise manufactured in someone else's service
    for a delivery this one simply does not want."""
    response = await client.post(
        "/webhook/radarr",
        json={"eventType": "Health", "level": "warning", "message": "no download client"},
        headers={"X-Autoposter-Token": TOKEN},
    )
    assert response.status_code == 200
    assert response.json() == {"intents": 0, "queued": 0}
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [(e.event_type, e.outcome) for e in events] == [("Health", "0 intents")]


async def test_an_over_long_event_type_is_refused_and_never_a_500(client, session):
    """`events_log.event_type` is String(64) and is served verbatim by
    /api/events. Before the envelope existed, a 200-character eventType reached
    that column unchecked and the delivery died on the insert."""
    payload = load("radarr_download.json")
    payload["eventType"] = "D" * 200
    response = await client.post(
        "/webhook/radarr", json=payload, headers={"X-Autoposter-Token": TOKEN}
    )
    assert response.status_code == 400
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [(e.event_type, e.outcome) for e in events] == [(None, REFUSED)]


async def test_a_non_string_event_type_is_refused(client, session):
    payload = load("radarr_download.json")
    payload["eventType"] = {"a": 1}
    response = await client.post(
        "/webhook/radarr", json=payload, headers={"X-Autoposter-Token": TOKEN}
    )
    assert response.status_code == 400
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [(e.event_type, e.outcome) for e in events] == [(None, REFUSED)]


async def test_a_nul_in_event_type_is_refused_and_never_a_500(client, session):
    """A NUL passes the length and type check alone -- `VARCHAR` and `JSONB`
    both refuse it outright, so before the control-character check existed
    this reached the insert and died as a 500. Same shape as the over-long
    and non-string cases: an envelope failure, so the row keeps a null
    event_type."""
    payload = load("radarr_download.json")
    payload["eventType"] = "Test\x00"
    response = await client.post(
        "/webhook/radarr", json=payload, headers={"X-Autoposter-Token": TOKEN}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == REFUSED
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [(e.event_type, e.outcome) for e in events] == [(None, REFUSED)]


async def test_a_nul_in_a_title_is_refused(client, session):
    """The same hole one level down: a fully valid, accepted Radarr body with
    a NUL inside `movie.title` passed the gate and died on the `payload`
    JSONB insert. The envelope itself validates (its own eventType has no
    NUL), so this is a stage-two refusal and the row keeps the event type."""
    payload = load("radarr_download.json")
    payload["movie"]["title"] = "Dune\x00: Part Two"
    response = await client.post(
        "/webhook/radarr", json=payload, headers={"X-Autoposter-Token": TOKEN}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == REFUSED
    events = (await session.execute(select(EventLog))).scalars().all()
    assert [(e.event_type, e.outcome) for e in events] == [("Download", REFUSED)]


async def test_an_explicit_null_episodes_is_tolerated(client):
    """The other optional fields all tolerate an explicit `null`; `episodes`
    now does too. `parse_sonarr` already reads `payload.get("episodes") or
    []`, so a body that sends `"episodes": null` on a Rename must still
    enqueue the show rather than 400."""
    payload = load("sonarr_rename.json")
    payload["episodes"] = None
    response = await client.post(
        "/webhook/sonarr", json=payload, headers={"X-Autoposter-Token": TOKEN}
    )
    assert response.status_code == 200
    assert response.json()["queued"] >= 1


async def test_a_tokenless_junk_post_writes_no_event_row(client, session):
    """The order pin. `_authorise` runs before `_ingest` is entered, so a caller
    with no secret reaches neither the body read, nor the JSON decode, nor the
    models, nor the events_log write: it cannot make this service allocate a
    row or a parse. Moving the gate ahead of the secret check would undo that."""
    response = await client.post("/webhook/radarr", json={"hello": "world"})
    assert response.status_code == 401
    assert (await session.execute(select(EventLog))).scalars().all() == []


async def test_a_refusal_logs_field_names_and_no_part_of_the_body(client, caplog):
    """DEBUG, not WARNING: a refusal is the expected outcome of a port scan and
    must not fill the pod log. The line carries the exception class name and our
    own field names -- `loc` can only name a field declared in intake/arr.py,
    because every model there is extra="ignore" and an unknown key is dropped
    rather than reported. Never `msg`, never `input`: those quote the body."""
    body = {"eventType": "Download", "movie": {"title": "Dune", "tmdbId": "not-an-int"}}
    with caplog.at_level("DEBUG", logger="autoposter.intake.routes"):
        response = await client.post(
            "/webhook/radarr", json=body, headers={"X-Autoposter-Token": TOKEN}
        )

    assert response.status_code == 400
    records = [r for r in caplog.records if r.name == "autoposter.intake.routes"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "ValidationError" in message
    assert "tmdbId" in message
    assert "not-an-int" not in message
    assert "Dune" not in message
