"""GET /api/version -- what this pod is running, and whether Harbor has newer.

Two independent facts meet here, and the tests keep them independent:

* The **running** version is what the image was built as. It arrives as
  ``AUTOPOSTER_VERSION``, written by the Dockerfile from the ``GIT_SHA``
  build-arg the CI workflow passes, so it is the tag the registry actually
  holds this image under. A build without the arg -- every local
  ``docker build`` -- reports ``dev``, which is the honest answer rather than
  a fabricated sha.
* The **latest** version is Harbor's, refreshed in the background by
  ``VersionPoller`` (``api/version.py``) and read by the endpoint from its
  cache -- never fetched at request time. This file therefore splits into two
  kinds of test: ``VersionPoller`` tests that exercise the Harbor request
  itself (what it asks for, how a token is or isn't attached, how failures are
  handled and logged), and endpoint tests that exercise only what
  ``GET /api/version`` does with whatever the poller already has cached.

The registry, project and repository are derived from ``AUTOPOSTER_IMAGE_REF``
at boot (``config/image_ref.py``, ``app.py``'s ``create_app``), not typed as
config -- see ``test_image_ref.py`` for the parser itself. The rule these
tests hold to hardest: **the Harbor URL is derived, internal, and must never
leave the process.** It is not in the response, and a failed poll logs the
exception's class name and nothing else -- an httpx error's own message
carries the full URL, so ``exc_info`` or ``%s`` on the exception would put an
internal hostname into a log an operator pastes into a bug report.

The autoposter Harbor project is public (operator decision, 2026-08-26), so
``AUTOPOSTER_HARBOR_TOKEN`` is optional: an unset token means an anonymous
request, not a disabled check.
"""
import asyncio
import logging
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api import version as version_module
from autoposter.api.auth import hash_password
from autoposter.api.version import VersionPoller
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

REGISTRY = "harbor.example"
HARBOR_URL = f"https://{REGISTRY}"
HARBOR_TOKEN = "cm9ib3QkYXV0b3Bvc3RlcjpzM2NyZXQ="
PROJECT = "operinko-labs"
REPOSITORY = "autoposter"
TARGET = (REGISTRY, PROJECT, REPOSITORY)
IMAGE_REF = f"{REGISTRY}/{PROJECT}/{REPOSITORY}:sha-4b2a34d"

RUNNING = "sha-4b2a34d"
NEWER = "sha-9f10c2e"


def _secrets(harbor_token: str = HARBOR_TOKEN) -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        harbor_token=harbor_token,
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest.fixture(autouse=True)
def running_version(monkeypatch):
    """Every test but the ``dev`` one runs as a built image would."""
    monkeypatch.setenv("AUTOPOSTER_VERSION", RUNNING)


def _app(
    session_factory, secrets: Secrets, monkeypatch, image_ref: str | None = IMAGE_REF
):
    """An app whose ``version_check_target`` is derived from ``image_ref``.

    ``create_app`` reads ``AUTOPOSTER_IMAGE_REF`` once, at construction, onto
    ``app.state`` -- there is no config field to set after the fact, so the
    environment has to carry the right value *before* this call, via the
    caller's own ``monkeypatch``. ``create_app`` also builds
    ``app.state.version_poller`` with ``http=None`` at this point (the real
    http client only exists once the background lifespan runs), so a poller
    is always present for the endpoint to read from, even in these
    lifespan-less test apps.
    """
    if image_ref is None:
        monkeypatch.delenv("AUTOPOSTER_IMAGE_REF", raising=False)
    else:
        monkeypatch.setenv("AUTOPOSTER_IMAGE_REF", image_ref)
    config = load_config(EXAMPLE)
    return create_app(config, session_factory, secrets)


@pytest_asyncio.fixture
async def app(session_factory, monkeypatch):
    return _app(session_factory, _secrets(), monkeypatch)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def get(client, headers):
    response = await client.get("/api/version", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _mock_transport(*, tags=(NEWER,), status=200, error: Exception | None = None):
    """A fake Harbor transport, recording every request it is asked to make.

    A request to anything but the artifacts endpoint fails the test outright
    -- this handler must not quietly stand in for a call the code under test
    had no business making. Returns ``(AsyncClient, seen)``.
    """
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if error is not None:
            raise error
        expected = f"/api/v2.0/projects/{PROJECT}/repositories/{REPOSITORY}/artifacts"
        if request.url.path != expected:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        if status != 200:
            return httpx.Response(status, json={"errors": []})
        return httpx.Response(200, json=[{"tags": [{"name": name} for name in tags]}])

    return AsyncClient(transport=httpx.MockTransport(handler)), seen


# --- auth ----------------------------------------------------------------


async def test_requires_a_session(client):
    assert (await client.get("/api/version")).status_code == 401


# --- the running version ---------------------------------------------------


async def test_reports_the_version_the_image_was_built_as(client, auth_headers):
    assert (await get(client, auth_headers))["version"] == RUNNING


async def test_reports_dev_when_the_image_names_no_version(
    client, auth_headers, monkeypatch
):
    """A local `docker build` passes no GIT_SHA, so the ENV expands to an empty
    string. Reporting `dev` says so; reporting `sha-` would look like a real
    tag that simply never matches anything in the registry."""
    monkeypatch.delenv("AUTOPOSTER_VERSION")

    assert (await get(client, auth_headers))["version"] == "dev"


async def test_an_empty_version_is_dev_too(client, auth_headers, monkeypatch):
    monkeypatch.setenv("AUTOPOSTER_VERSION", "")

    assert (await get(client, auth_headers))["version"] == "dev"


async def test_the_bare_prefix_a_local_build_produces_is_dev(
    client, auth_headers, monkeypatch
):
    """This is what an unstamped build actually reports, and the case a
    `getenv` default would miss: `ENV AUTOPOSTER_VERSION=sha-${GIT_SHA}` with
    no `--build-arg` does not leave the variable unset, it sets it to `sha-`.
    Reporting that verbatim would put a tag naming no commit in the sidebar."""
    monkeypatch.setenv("AUTOPOSTER_VERSION", "sha-")

    assert (await get(client, auth_headers))["version"] == "dev"


# --- the endpoint reads the poller's cache, never Harbor --------------------


async def test_before_the_first_poll_latest_and_update_available_are_null(
    client, auth_headers
):
    body = await get(client, auth_headers)

    assert body["latest"] is None
    assert body["update_available"] is None


async def test_a_cached_newer_tag_is_reported_as_an_update(client, auth_headers, app):
    app.state.version_poller.latest = NEWER

    body = await get(client, auth_headers)

    assert body["version"] == RUNNING
    assert body["latest"] == NEWER
    assert body["update_available"] is True


async def test_the_running_tag_cached_as_latest_is_not_an_update(
    client, auth_headers, app
):
    app.state.version_poller.latest = RUNNING

    body = await get(client, auth_headers)

    assert body["update_available"] is False


async def test_the_endpoint_never_calls_harbor_itself(client, auth_headers, app):
    """The background poll replaced the request-path fetch -- GET /api/version
    must only ever read app.state.version_poller.latest.

    Asserted as a discriminating pair, because the negative half alone is not
    evidence: a `_http` assignment that had gone inert -- wrong attribute, a
    poller the endpoint does not read -- would satisfy "zero Harbor requests"
    just as well as the property being true. So the same client, on the same
    poller, must record *zero* requests across a run of endpoint calls and
    *exactly one* from a single poll. Only a live assignment passes both."""
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"tags": [{"name": NEWER}]}])

    async with AsyncClient(transport=httpx.MockTransport(handler)) as http:
        app.state.version_poller._http = http

        for _ in range(3):
            await get(client, auth_headers)
        assert seen == [], "the endpoint must never call Harbor itself"

        await app.state.version_poller._poll()

    assert len(seen) == 1, "the poller's own http client was not the one wired in"
    assert app.state.version_poller.latest == NEWER


async def test_a_dev_build_against_a_cached_registry_tag_is_an_update(
    client, auth_headers, app, monkeypatch
):
    """Honest rather than clever: a developer running an unpushed local build
    IS behind whatever main last published, and saying so is more useful than
    suppressing the marker because the running version has no sha. This is the
    only test behind deploy/README.md's claim that a `dev` pod with a reachable
    registry always shows the marker, so the tag is polled for real rather than
    assigned."""
    monkeypatch.delenv("AUTOPOSTER_VERSION")
    http, _ = _mock_transport(tags=(NEWER,))
    async with http:
        app.state.version_poller._http = http
        await app.state.version_poller._poll()

    body = await get(client, auth_headers)

    assert body["version"] == "dev"
    assert body["latest"] == NEWER
    assert body["update_available"] is True


async def test_the_robot_token_is_never_in_the_response(client, auth_headers, app):
    app.state.version_poller.latest = NEWER

    response = await client.get("/api/version", headers=auth_headers)

    assert HARBOR_TOKEN not in response.text


async def test_the_harbor_url_is_never_in_the_response(client, auth_headers, app):
    """Derived from AUTOPOSTER_IMAGE_REF, not something a browser session is
    entitled to: the response says whether an update exists, never where
    that was learned."""
    app.state.version_poller.latest = NEWER

    response = await client.get("/api/version", headers=auth_headers)

    assert "harbor.example" not in response.text


async def test_without_an_image_ref_the_poller_is_never_configured(
    session_factory, monkeypatch
):
    """The default state of a deployment that has not been given
    AUTOPOSTER_IMAGE_REF: it reports what it is running and says nothing
    about newer."""
    app = _app(session_factory, _secrets(), monkeypatch, image_ref=None)
    assert app.state.version_check_target is None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": PASSWORD})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        body = await get(client, headers)

    assert body["version"] == RUNNING
    assert body["latest"] is None
    assert body["update_available"] is None


# --- VersionPoller: the optional token --------------------------------------


async def test_a_token_request_carries_it_as_basic_auth():
    http, seen = _mock_transport()
    async with http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        await poller._poll()

    assert seen[0].headers["Authorization"] == f"Basic {HARBOR_TOKEN}"


async def test_an_anonymous_request_carries_no_authorization_header():
    """The autoposter Harbor project is public -- an unset token means an
    anonymous request, not a disabled check (operator decision, 2026-08-26)."""
    http, seen = _mock_transport()
    async with http:
        poller = VersionPoller(http=http, target=TARGET, token="")
        await poller._poll()

    assert "Authorization" not in seen[0].headers


async def test_anonymous_and_token_requests_parse_the_tag_list_identically():
    for token in ("", HARBOR_TOKEN):
        http, _ = _mock_transport(tags=("latest", NEWER))
        async with http:
            poller = VersionPoller(http=http, target=TARGET, token=token)
            await poller._poll()

        assert poller.latest == NEWER


# --- VersionPoller: how Harbor is asked -------------------------------------


async def test_harbor_is_asked_for_the_single_newest_artifact_with_its_tags():
    """The repository holds one artifact per commit ever pushed. Without the
    sort and the page cap this reads the whole history to answer a one-line
    question, and without `with_tag` the artifacts come back tagless -- the
    answer would be null however many pages were fetched."""
    http, seen = _mock_transport()
    async with http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        await poller._poll()

    assert len(seen) == 1
    params = seen[0].url.params
    assert params["page_size"] == "1"
    assert params["sort"] == "-push_time"
    assert params["with_tag"] == "true"


async def test_the_latest_tag_is_the_sha_one_not_the_floating_one():
    """Every push tags the artifact twice, `sha-<git>` and `latest`. Comparing
    against `latest` would never match a running version and so would report
    an update forever."""
    http, _ = _mock_transport(tags=("latest", NEWER))
    async with http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        await poller._poll()

    assert poller.latest == NEWER


async def test_an_artifact_with_no_sha_tag_is_no_answer():
    http, _ = _mock_transport(tags=("latest",))
    async with http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        await poller._poll()

    assert poller.latest is None


# --- VersionPoller: failure --------------------------------------------------


async def test_a_failed_poll_leaves_latest_none_before_any_success():
    http, _ = _mock_transport(error=httpx.ConnectError("connection refused"))
    async with http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        await poller._poll()

    assert poller.latest is None


async def test_a_failed_poll_leaves_the_previous_answer_standing():
    """An outage must not erase a still-true answer -- the pod's sidebar keeps
    showing what the last successful poll found."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=[{"tags": [{"name": NEWER}]}])
        raise httpx.ConnectError("boom", request=request)

    async with AsyncClient(transport=httpx.MockTransport(handler)) as http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        await poller._poll()
        assert poller.latest == NEWER

        await poller._poll()

    assert poller.latest == NEWER


async def test_an_error_status_also_leaves_the_previous_answer_standing():
    """A 401 from a rotated robot account is as much a failed poll as a refused
    connection, and must be contained the same way: the sidebar keeps showing
    what the last successful poll found rather than blanking."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=[{"tags": [{"name": NEWER}]}])
        return httpx.Response(401, json={"errors": []})

    async with AsyncClient(transport=httpx.MockTransport(handler)) as http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        await poller._poll()
        assert poller.latest == NEWER

        await poller._poll()

    assert poller.latest == NEWER


async def test_a_connect_failure_is_logged_under_the_connect_category(caplog):
    """The category is what makes an https-only assumption debuggable: the
    Harbor URL is built with a hardcoded scheme (api/version.py `_poll`), so a
    registry that speaks plain HTTP fails here on every poll forever, and a
    bare class name would never say which half of the exchange failed."""
    http, _ = _mock_transport(error=httpx.ConnectError("connection refused"))
    async with http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        with caplog.at_level(logging.WARNING):
            await poller._poll()

    assert "connect: ConnectError" in caplog.records[0].getMessage()


async def test_an_unreadable_answer_is_logged_under_the_parse_category(caplog):
    """Harbor answered, and the answer was not something this module could
    read -- a different problem from an unreachable registry and from a
    refusal, so it is named differently."""
    def handler(request):
        return httpx.Response(200, text="<html>not the registry api</html>")

    async with AsyncClient(transport=httpx.MockTransport(handler)) as http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        with caplog.at_level(logging.WARNING):
            await poller._poll()

    assert poller.latest is None
    assert "parse: " in caplog.records[0].getMessage()


async def test_a_failure_logs_the_exception_class_and_not_the_url(caplog):
    """The guard this module exists to keep. httpx's own exception message
    embeds the full request URL, so logging the exception -- with `%s`, with
    `exc_info=True`, with `repr()` -- publishes the operator's internal Harbor
    hostname into any log an operator pastes anywhere."""
    http, _ = _mock_transport(
        error=httpx.ConnectError("connection refused to https://harbor.example")
    )
    async with http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        with caplog.at_level(logging.WARNING):
            await poller._poll()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "ConnectError" in warnings[0].getMessage()
    # caplog.text is the formatted line plus any traceback the record carries,
    # so this catches `exc_info=True` as well as an interpolated exception.
    assert "harbor.example" not in caplog.text
    assert HARBOR_URL not in caplog.text
    assert "connection refused" not in caplog.text


async def test_a_401_logs_the_status_code_and_not_just_the_class(caplog):
    """A rotated robot token (401) and an outage both catch as generic
    failures, and both log identically -- an operator cannot tell a credential
    problem from Harbor being down. For an HTTPStatusError the status code, an
    int with no URL in it, should be in the log too."""
    http, _ = _mock_transport(status=401)
    async with http:
        poller = VersionPoller(http=http, target=TARGET, token=HARBOR_TOKEN)
        with caplog.at_level(logging.WARNING):
            await poller._poll()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "status: HTTPStatusError 401" in warnings[0].getMessage()
    assert "harbor.example" not in caplog.text
    assert HARBOR_URL not in caplog.text


# --- VersionPoller: the background loop -------------------------------------


async def test_poll_interval_is_six_hours_and_hardcoded():
    """Cadence is a deployment fact, not an operator knob -- the same
    philosophy that moved the Harbor coordinates out of the config schema."""
    assert version_module.POLL_INTERVAL_SECONDS == 6 * 3600


async def test_run_polls_on_the_way_in_not_after_the_first_interval(monkeypatch):
    """The sidebar should have an answer within seconds of boot, not up to
    POLL_INTERVAL_SECONDS later -- so the loop polls before it ever waits.
    interval_seconds is deliberately huge: if the loop waited out the
    interval before its first poll, this test would time out rather than see
    a request."""
    http, seen = _mock_transport()
    stop_event = asyncio.Event()
    real_poll = VersionPoller._poll

    async def stopping_poll(self):
        await real_poll(self)
        stop_event.set()

    monkeypatch.setattr(VersionPoller, "_poll", stopping_poll)

    async with http:
        poller = VersionPoller(
            http=http, target=TARGET, token=HARBOR_TOKEN, interval_seconds=9999
        )
        await asyncio.wait_for(poller.run(stop_event), timeout=5)

    assert len(seen) == 1
    assert poller.latest == NEWER


async def test_run_survives_a_failed_poll_and_retries_next_interval(monkeypatch, caplog):
    """A network failure must be logged, not kill the loop: run() must still
    make a second attempt -- here, effectively immediately, since
    interval_seconds=0 keeps the test from waiting for real."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=[{"tags": [{"name": NEWER}]}])

    stop_event = asyncio.Event()
    real_poll = VersionPoller._poll

    async def stopping_poll(self):
        await real_poll(self)
        if calls["n"] >= 2:
            stop_event.set()

    monkeypatch.setattr(VersionPoller, "_poll", stopping_poll)

    async with AsyncClient(transport=httpx.MockTransport(handler)) as http:
        poller = VersionPoller(
            http=http, target=TARGET, token=HARBOR_TOKEN, interval_seconds=0
        )
        with caplog.at_level("WARNING"):
            await asyncio.wait_for(poller.run(stop_event), timeout=5)

    assert calls["n"] == 2
    assert poller.latest == NEWER
    assert any("the update check failed" in r.message for r in caplog.records)


async def test_run_does_nothing_without_an_image_ref():
    http, seen = _mock_transport()
    async with http:
        poller = VersionPoller(http=http, target=None, token=HARBOR_TOKEN)
        await asyncio.wait_for(poller.run(asyncio.Event()), timeout=5)

    assert seen == []
    assert poller.latest is None


async def test_run_does_nothing_without_an_http_client():
    """The create_app placeholder: no client exists until the background
    lifespan runs, so the loop must not try to use one that isn't there."""
    poller = VersionPoller(http=None, target=TARGET, token=HARBOR_TOKEN)

    await asyncio.wait_for(poller.run(asyncio.Event()), timeout=5)

    assert poller.latest is None
