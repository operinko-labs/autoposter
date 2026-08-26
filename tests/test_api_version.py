"""GET /api/version -- what this pod is running, and whether Harbor has newer.

Two independent facts meet here, and the tests keep them independent:

* The **running** version is what the image was built as. It arrives as
  ``AUTOPOSTER_VERSION``, written by the Dockerfile from the ``GIT_SHA``
  build-arg the CI workflow passes, so it is the tag the registry actually
  holds this image under. A build without the arg -- every local
  ``docker build`` -- reports ``dev``, which is the honest answer rather than
  a fabricated sha.
* The **latest** version is Harbor's, read from the repository's most
  recently pushed artifact. Nothing else in this application knows what is
  deployable; the git history does not, because a commit whose image failed
  to build was never pushed.

The registry, project and repository are derived from ``AUTOPOSTER_IMAGE_REF``
at boot (``config/image_ref.py``, ``app.py``'s ``create_app``), not typed as
config -- see ``test_image_ref.py`` for the parser itself. The rule these
tests hold to hardest: **the Harbor URL is derived, internal, and must never
leave the process.** It is not in the response, and a failed check logs the
exception's class name and nothing else -- an httpx error's own message
carries the full URL, so ``exc_info`` or ``%s`` on the exception would put an
internal hostname into a log an operator pastes into a bug report.
"""
import logging
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api import version as version_module
from autoposter.api.auth import hash_password
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
def clean_cache(monkeypatch):
    """The Harbor answer is cached in a module global, so one test's answer
    would otherwise be the next test's -- and the cache test would pass on a
    leftover entry rather than on its own first call."""
    monkeypatch.setattr(version_module, "_cache", {})


@pytest.fixture(autouse=True)
def running_version(monkeypatch):
    """Every test but the ``dev`` one runs as a built image would."""
    monkeypatch.setenv("AUTOPOSTER_VERSION", RUNNING)


def _app(
    session_factory, secrets: Secrets, monkeypatch, image_ref: str | None = IMAGE_REF
):
    """An app whose ``version_check_target`` is derived from ``image_ref``.

    ``create_app`` reads ``AUTOPOSTER_IMAGE_REF`` once, at construction, onto
    ``app.state`` -- there is no longer a config field to set after the fact,
    so the environment has to carry the right value *before* this call, via
    the caller's own ``monkeypatch``.
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


@pytest_asyncio.fixture
async def wire():
    """Give an app the ``app.state.http`` its lifespan would, backed by a mock
    transport that records every request it is asked to make.

    The returned list is the record: the cache tests count it, and the
    "not attempted" tests assert it stayed empty. A request to anything but
    Harbor's artifacts endpoint fails the test rather than being answered --
    this handler must not quietly stand in for a call the endpoint had no
    business making.
    """
    clients = []

    def install(app, *, tags=(NEWER,), status=200, error: Exception | None = None):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if error is not None:
                raise error
            expected = (
                f"/api/v2.0/projects/{PROJECT}/repositories/{REPOSITORY}/artifacts"
            )
            if request.url.path != expected:
                raise AssertionError(f"unexpected request {request.method} {request.url}")
            if status != 200:
                return httpx.Response(status, json={"errors": []})
            return httpx.Response(
                200, json=[{"tags": [{"name": name} for name in tags]}]
            )

        http = AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(http)
        app.state.http = http
        return seen

    yield install
    for http in clients:
        await http.aclose()


async def get(client, headers):
    response = await client.get("/api/version", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- auth --------------------------------------------------------------------


async def test_requires_a_session(client):
    assert (await client.get("/api/version")).status_code == 401


# --- the running version -----------------------------------------------------


async def test_reports_the_version_the_image_was_built_as(client, auth_headers, app, wire):
    wire(app)

    assert (await get(client, auth_headers))["version"] == RUNNING


async def test_reports_dev_when_the_image_names_no_version(
    client, auth_headers, app, wire, monkeypatch
):
    """A local `docker build` passes no GIT_SHA, so the ENV expands to an empty
    string. Reporting `dev` says so; reporting `sha-` would look like a real
    tag that simply never matches anything in the registry."""
    monkeypatch.delenv("AUTOPOSTER_VERSION")
    wire(app)

    assert (await get(client, auth_headers))["version"] == "dev"


async def test_an_empty_version_is_dev_too(client, auth_headers, app, wire, monkeypatch):
    monkeypatch.setenv("AUTOPOSTER_VERSION", "")
    wire(app)

    assert (await get(client, auth_headers))["version"] == "dev"


async def test_the_bare_prefix_a_local_build_produces_is_dev(
    client, auth_headers, app, wire, monkeypatch
):
    """This is what an unstamped build actually reports, and the case a
    `getenv` default would miss: `ENV AUTOPOSTER_VERSION=sha-${GIT_SHA}` with
    no `--build-arg` does not leave the variable unset, it sets it to `sha-`.
    Reporting that verbatim would put a tag naming no commit in the sidebar."""
    monkeypatch.setenv("AUTOPOSTER_VERSION", "sha-")
    wire(app)

    assert (await get(client, auth_headers))["version"] == "dev"


# --- the check being switched off --------------------------------------------


async def test_without_an_image_ref_nothing_is_checked(session_factory, monkeypatch, wire):
    """The default state of a deployment that has not been given
    AUTOPOSTER_IMAGE_REF: it reports what it is running and says nothing
    about newer."""
    app = _app(session_factory, _secrets(), monkeypatch, image_ref=None)
    seen = wire(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": PASSWORD})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        body = await get(client, headers)

    assert body["version"] == RUNNING
    assert body["latest"] is None
    assert body["update_available"] is None
    assert seen == []


async def test_without_a_robot_token_nothing_is_checked(session_factory, monkeypatch, wire):
    """Harbor's artifact listing is not anonymous for a private project, and a
    tokenless request would 401 on every poll. Not attempting it is the answer,
    not an error to report."""
    app = _app(session_factory, _secrets(harbor_token=""), monkeypatch)
    seen = wire(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": PASSWORD})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        body = await get(client, headers)

    assert body["latest"] is None
    assert body["update_available"] is None
    assert seen == []


# --- the Harbor answer -------------------------------------------------------


async def test_a_newer_tag_in_the_registry_is_an_update(client, auth_headers, app, wire):
    wire(app, tags=(NEWER,))

    body = await get(client, auth_headers)

    assert body["version"] == RUNNING
    assert body["latest"] == NEWER
    assert body["update_available"] is True


async def test_the_running_tag_being_the_newest_is_not_an_update(
    client, auth_headers, app, wire
):
    wire(app, tags=(RUNNING,))

    body = await get(client, auth_headers)

    assert body["latest"] == RUNNING
    assert body["update_available"] is False


async def test_a_dev_build_against_a_real_registry_tag_is_an_update(
    client, auth_headers, app, wire, monkeypatch
):
    """Honest rather than clever: a developer running an unpushed local build
    IS behind whatever main last published, and saying so is more useful than
    suppressing the marker because the running version has no sha."""
    monkeypatch.delenv("AUTOPOSTER_VERSION")
    wire(app, tags=(NEWER,))

    body = await get(client, auth_headers)

    assert body["version"] == "dev"
    assert body["update_available"] is True


async def test_the_latest_tag_is_the_sha_one_not_the_floating_one(
    client, auth_headers, app, wire
):
    """Every push tags the artifact twice, `sha-<git>` and `latest`. Comparing
    against `latest` would never match a running version and so would report an
    update forever."""
    wire(app, tags=("latest", NEWER))

    assert (await get(client, auth_headers))["latest"] == NEWER


async def test_an_artifact_with_no_sha_tag_reports_nothing(client, auth_headers, app, wire):
    wire(app, tags=("latest",))

    body = await get(client, auth_headers)

    assert body["latest"] is None
    assert body["update_available"] is None


# --- how the registry is asked -----------------------------------------------


async def test_harbor_is_asked_for_the_single_newest_artifact_with_its_tags(
    client, auth_headers, app, wire
):
    """The repository holds one artifact per commit ever pushed. Without the
    sort and the page cap this reads the whole history to answer a one-line
    question, and without `with_tag` the artifacts come back tagless -- the
    answer would be null however many pages were fetched."""
    seen = wire(app)

    await get(client, auth_headers)

    assert len(seen) == 1
    params = seen[0].url.params
    assert params["page_size"] == "1"
    assert params["sort"] == "-push_time"
    assert params["with_tag"] == "true"


async def test_the_request_carries_the_robot_token_as_basic_auth(
    client, auth_headers, app, wire
):
    seen = wire(app)

    await get(client, auth_headers)

    assert seen[0].headers["Authorization"] == f"Basic {HARBOR_TOKEN}"


async def test_the_robot_token_is_never_in_the_response(client, auth_headers, app, wire):
    wire(app)

    response = await client.get("/api/version", headers=auth_headers)

    assert HARBOR_TOKEN not in response.text


async def test_the_harbor_url_is_never_in_the_response(client, auth_headers, app, wire):
    """Derived from AUTOPOSTER_IMAGE_REF, not something a browser session is
    entitled to: the response says whether an update exists, never where
    that was learned."""
    wire(app)

    response = await client.get("/api/version", headers=auth_headers)

    assert "harbor.example" not in response.text


# --- failure -----------------------------------------------------------------


async def test_a_harbor_failure_reports_nulls(client, auth_headers, app, wire):
    """A registry that cannot be reached must not fail the sidebar. The
    version the pod is running is still known, and is still the useful half."""
    wire(app, error=httpx.ConnectError("connection refused"))

    body = await get(client, auth_headers)

    assert body["version"] == RUNNING
    assert body["latest"] is None
    assert body["update_available"] is None


async def test_a_harbor_error_status_reports_nulls(client, auth_headers, app, wire):
    """A 401 from a rotated robot account is as much a failed check as a
    refused connection, and is handled the same way rather than surfacing as a
    500 out of the sidebar."""
    wire(app, status=401)

    body = await get(client, auth_headers)

    assert body["latest"] is None
    assert body["update_available"] is None


async def test_a_failure_logs_the_exception_class_and_not_the_url(
    client, auth_headers, app, wire, caplog
):
    """The guard this module exists to keep. httpx's own exception message
    embeds the full request URL, so logging the exception -- with `%s`, with
    `exc_info=True`, with `repr()` -- publishes the operator's internal Harbor
    hostname into any log an operator pastes anywhere.
    """
    wire(app, error=httpx.ConnectError("connection refused to https://harbor.example"))

    with caplog.at_level(logging.WARNING):
        await get(client, auth_headers)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "ConnectError" in warnings[0].getMessage()
    # caplog.text is the formatted line plus any traceback the record carries,
    # so this catches `exc_info=True` as well as an interpolated exception.
    assert "harbor.example" not in caplog.text
    assert HARBOR_URL not in caplog.text
    assert "connection refused" not in caplog.text


async def test_a_401_logs_the_status_code_and_not_just_the_class(
    client, auth_headers, app, wire, caplog
):
    """A rotated robot token (401) and an outage both land in `except
    Exception` today, and both log identically -- an operator cannot tell a
    credential problem from Harbor being down. For an HTTPStatusError the
    status code, an int with no URL in it, should be in the log too."""
    wire(app, status=401)

    with caplog.at_level(logging.WARNING):
        await get(client, auth_headers)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "HTTPStatusError" in warnings[0].getMessage()
    assert "401" in warnings[0].getMessage()
    assert "harbor.example" not in caplog.text
    assert HARBOR_URL not in caplog.text


async def test_a_failed_check_is_cached_so_an_outage_is_not_hammered(
    client, auth_headers, app, wire
):
    """Otherwise every sidebar mount in every open tab retries a dead registry
    for as long as the outage lasts."""
    seen = wire(app, error=httpx.ConnectError("connection refused"))

    await get(client, auth_headers)
    await get(client, auth_headers)

    assert len(seen) == 1


# --- the cache ---------------------------------------------------------------


async def test_harbor_is_asked_once_per_window_not_once_per_request(
    client, auth_headers, app, wire
):
    """The sidebar mounts on every full page load, and there may be several
    tabs open. Without this the registry sees a request per mount."""
    seen = wire(app)

    first = await get(client, auth_headers)
    second = await get(client, auth_headers)
    third = await get(client, auth_headers)

    assert len(seen) == 1
    assert first == second == third


async def test_the_window_expiring_asks_harbor_again(
    client, auth_headers, app, wire, monkeypatch
):
    """A cache that never expires is not a cache -- an update pushed after the
    first request would never be reported for the life of the pod."""
    seen = wire(app)

    await get(client, auth_headers)
    assert len(seen) == 1

    # Age the entry past the window rather than waiting out fifteen real
    # minutes: the cache stores the monotonic clock reading it was filled at.
    key = (HARBOR_URL, PROJECT, REPOSITORY)
    stamped, latest = version_module._cache[key]
    monkeypatch.setattr(
        version_module,
        "_cache",
        {key: (stamped - version_module.CACHE_TTL_SECONDS - 1, latest)},
    )

    await get(client, auth_headers)

    assert len(seen) == 2
