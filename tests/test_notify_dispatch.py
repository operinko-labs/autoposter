"""The notification dispatcher: one POST per event, retries, and containment.

Everything runs against ``httpx.MockTransport`` (conftest's
``no_outbound_network`` fails the test if a real request escapes it). Two
things a MockTransport cannot fake are handled the way ``test_api_artwork.py``
does:

- Elapsed time: the per-request timeout is asserted via
  ``request.extensions["timeout"]`` against a client whose own default
  differs, so the assertion cannot pass by the request merely inheriting the
  client's configuration.
- Sleeping: ``dispatch._sleep`` is module-level (the ``payload._utcnow``
  precedent) so tests record the backoff schedule instead of living through
  it.

The hook URL embeds a token in its path on purpose (Uptime-Kuma style): the
containment tests assert the token never reaches the log or the events row.
"""

import json
import logging

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from autoposter.config.schema import NotificationsConfig
from autoposter.db.models import EventLog
from autoposter.notify import dispatch
from autoposter.notify.dispatch import build_notifier

HOOK_HOST = "hooks.example.test"
HOOK_TOKEN = "tok-SECRET123"
HOOK_URL = f"http://{HOOK_HOST}/notify/{HOOK_TOKEN}"
CLIENT_DEFAULT_TIMEOUT = 30.0
CONFIGURED_TIMEOUT = 7


def _config(**overrides) -> NotificationsConfig:
    values = dict(
        enabled=True,
        url=HOOK_URL,
        mode="apprise-json",
        timeout_seconds=CONFIGURED_TIMEOUT,
        retry_count=3,
    )
    values.update(overrides)
    return NotificationsConfig(**values)


def _refuse_db():
    raise AssertionError("this test must not touch the events log")


@pytest_asyncio.fixture
async def make_client():
    """An httpx client on a MockTransport, closed on teardown.

    The default timeout deliberately differs from the configured
    ``timeout_seconds`` -- see the timeout test.
    """
    clients = []

    def make(handler):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=CLIENT_DEFAULT_TIMEOUT
        )
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


@pytest.fixture
def sleeps(monkeypatch):
    """Record backoff sleeps instead of living through them."""
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(dispatch, "_sleep", fake_sleep)
    return recorded


async def test_the_catcher_receives_the_exact_documented_payload(make_client, caplog):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    notifier = build_notifier(_config(), make_client(handler), _refuse_db)

    with caplog.at_level(logging.DEBUG):
        ok = await notifier.send(
            "scheduled_run_completed", "collections: 3 updated", {"status": "ok"}
        )

    assert ok is True
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url) == HOOK_URL
    assert seen[0].headers["content-type"] == "application/json"
    assert json.loads(seen[0].content) == {
        "version": "1.0",
        "title": "autoposter: scheduled_run_completed",
        "message": "collections: 3 updated",
        "attachments": [],
        "type": "success",
    }
    # Success is debug-only: nothing at warning or above.
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


async def test_a_500_then_200_succeeds_via_retry(make_client, sleeps):
    statuses = iter([500, 200])
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(next(statuses))

    notifier = build_notifier(_config(), make_client(handler), _refuse_db)

    ok = await notifier.send("scheduled_run_completed", "drift: swept", {"status": "ok"})

    assert ok is True
    assert len(seen) == 2
    assert sleeps == [0.5]


async def test_a_4xx_is_a_misconfiguration_and_is_not_retried(
    make_client, session_factory, sleeps
):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(404)

    notifier = build_notifier(_config(), make_client(handler), session_factory)

    ok = await notifier.send("scheduled_run_completed", "cleanup: done", {"status": "ok"})

    assert ok is False
    assert len(seen) == 1
    assert sleeps == []


async def test_exhausted_retries_fail_quietly_log_the_host_and_write_the_events_row(
    make_client, session_factory, session, sleeps, caplog
):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    notifier = build_notifier(_config(), make_client(handler), session_factory)

    with caplog.at_level(logging.DEBUG):
        # Reaching the assertions at all is the never-raises guard: an
        # exhausted send that propagates fails this test on the exception.
        ok = await notifier.send(
            "scheduled_run_completed", "arr sync: boom", {"status": "failed"}
        )

    assert ok is False
    assert sleeps == [0.5, 1.0]

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert HOOK_HOST in warnings[0].getMessage()
    # The URL may embed a token in its path; only the host may be logged.
    assert HOOK_URL not in caplog.text
    assert HOOK_TOKEN not in caplog.text

    rows = (await session.execute(select(EventLog))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "notifier"
    assert row.event_type == "scheduled_run_completed"
    assert "failed" in row.outcome
    stored = json.dumps(row.payload)
    assert HOOK_TOKEN not in stored


async def test_a_bug_before_the_transport_is_contained_by_the_outer_wrapper(
    make_client, caplog
):
    """Drives the OUTER never-raises wrapper in ``send``: ``detail=None``
    makes ``build_payload`` raise before ``_send``'s transport handling can
    contain anything, so only ``send``'s own try/except stands between the
    bug and the caller."""

    def throw(request):
        raise AssertionError("a payload that cannot build must never be POSTed")

    notifier = build_notifier(_config(), make_client(throw), _refuse_db)

    with caplog.at_level(logging.DEBUG):
        ok = await notifier.send("scheduled_run_completed", "s", None)

    assert ok is False
    # Our bug outranks webhook noise: exactly one record, at ERROR.
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR


async def test_a_db_failure_while_recording_still_yields_exactly_one_warning(
    make_client, sleeps, caplog
):
    """If the events-log write fails too (DB down), the failed send still
    logs exactly one WARNING; the recording failure is a secondary info
    line, not a second warning."""

    def handler(request):
        raise httpx.ConnectError("connection refused")

    def broken_db():
        raise RuntimeError("database is down")

    notifier = build_notifier(_config(), make_client(handler), broken_db)

    with caplog.at_level(logging.DEBUG):
        ok = await notifier.send("scheduled_run_completed", "s", {"status": "failed"})

    assert ok is False
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert HOOK_HOST in warnings[0].getMessage()


async def test_a_disabled_config_yields_a_noop_that_never_sends(make_client):
    def throw(request):
        raise AssertionError("a disabled notifier must never make a request")

    notifier = build_notifier(_config(enabled=False), make_client(throw), _refuse_db)

    # True as vacuous success: nothing was owed, so nothing failed. False is
    # reserved for "a notification was owed and could not be delivered" --
    # a caller branching on the result must never treat an intentionally
    # disabled config as a failure worth warning about on every send.
    assert await notifier.send("scheduled_run_completed", "s", {}) is True


async def test_enabled_without_a_url_is_a_noop_with_one_build_time_warning(
    make_client, caplog
):
    """The default ``url`` is empty, so enabled-without-url is a real
    misconfiguration; it is named once at build (the ``_build_mdblist``
    precedent), never once per send."""

    def throw(request):
        raise AssertionError("a notifier without a URL must never make a request")

    with caplog.at_level(logging.WARNING):
        notifier = build_notifier(_config(url=""), make_client(throw), _refuse_db)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "url" in warnings[0].getMessage()

    assert await notifier.send("scheduled_run_completed", "s", {}) is True


async def test_the_request_carries_the_configured_timeout(make_client):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    client = make_client(handler)
    # Checked against the client's own default too, so this cannot pass by
    # the request merely inheriting whatever the client was built with.
    assert client.timeout.read != CONFIGURED_TIMEOUT

    notifier = build_notifier(_config(), client, _refuse_db)
    await notifier.send("scheduled_run_completed", "s", {"status": "ok"})

    assert seen[0].extensions["timeout"] == {
        "connect": CONFIGURED_TIMEOUT,
        "read": CONFIGURED_TIMEOUT,
        "write": CONFIGURED_TIMEOUT,
        "pool": CONFIGURED_TIMEOUT,
    }
