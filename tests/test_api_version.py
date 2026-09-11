"""GET /api/version -- what this container is running, and whether a newer
release exists.

Two halves, and they fail in different directions:

* The **running** version is stamped into the image at build time, and *which*
  stamp is present says what kind of build it is. ``AUTOPOSTER_RELEASE`` is
  set only by the release workflow; ``AUTOPOSTER_VERSION`` (``sha-<commit>``)
  is set by every build. See ``test_version_stamp.py`` for the agreement
  between the workflows and the Dockerfile that puts them there.
* The **latest** release comes from GitHub, refreshed in the background by
  ``ReleasePoller`` and read by the endpoint from its cache.

So there are two kinds of test here: ``ReleasePoller`` tests that exercise the
GitHub request against a mock transport, and endpoint tests that set the
cached answer directly and never make a request at all.

The property these hold to hardest is **the gate**: a build that is not a
release must make no outbound request whatsoever. "Is there a newer release?"
is a question only a released version can answer -- ``sha-4b2a34d`` is not
behind ``v1.2.0`` or ahead of it -- and this operator's own cluster runs
main-built images, so a poller that ignored the gate would have every pod in
it calling GitHub four times a day to compute a marker that would be lit
forever.

The second is that ``update_available`` is a **tri-state**. ``null`` means
"cannot say", which is not the same as "up to date" and must never render as
one.
"""
import asyncio
import logging
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.api.version import RELEASES_URL, ReleasePoller, _version_tuple
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

RUNNING = "v1.2.0"
NEWER = "v1.3.0"
OLDER = "v1.1.0"
SHA_BUILD = "sha-4b2a34d"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest.fixture(autouse=True)
def running_version(monkeypatch):
    """Every test but the ones about stamping runs as a release image would."""
    monkeypatch.setenv("AUTOPOSTER_RELEASE", RUNNING)
    monkeypatch.delenv("AUTOPOSTER_VERSION", raising=False)


@pytest_asyncio.fixture
async def app(session_factory):
    """``create_app`` builds ``app.state.version_poller`` with ``http=None``,
    so a poller is always present for the endpoint to read from even in these
    lifespan-less test apps. It reads the stamp from the environment at
    construction, which the autouse fixture above has already set."""
    return create_app(load_config(EXAMPLE), session_factory, _secrets())


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


async def get(client, headers):
    response = await client.get("/api/version", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _mock_transport(*, tag=NEWER, status=200, error: Exception | None = None, body=None):
    """A fake GitHub, recording every request it is asked to make.

    A request to anything but the releases endpoint fails the test outright --
    this handler must not quietly stand in for a call the code under test had
    no business making. Returns ``(AsyncClient, seen)``.
    """
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if error is not None:
            raise error
        if str(request.url) != RELEASES_URL:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        if status != 200:
            return httpx.Response(status, json={"message": "no"})
        return httpx.Response(200, json=body if body is not None else {"tag_name": tag})

    return AsyncClient(transport=httpx.MockTransport(handler)), seen


# --- auth --------------------------------------------------------------------


async def test_requires_a_session(client):
    assert (await client.get("/api/version")).status_code == 401


# --- the running version -----------------------------------------------------


async def test_reports_the_release_the_image_was_published_as(client, auth_headers):
    assert (await get(client, auth_headers))["version"] == RUNNING


async def test_the_release_stamp_wins_over_the_commit_stamp(
    client, auth_headers, monkeypatch
):
    """A release image carries both. The release is the one comparable to a
    release tag, so it is the one reported."""
    monkeypatch.setenv("AUTOPOSTER_VERSION", SHA_BUILD)

    assert (await get(client, auth_headers))["version"] == RUNNING


async def test_without_a_release_stamp_the_commit_stamp_is_reported(
    client, auth_headers, monkeypatch
):
    """Every build of main. Unchanged from what such a pod reported before the
    update check ever moved to GitHub."""
    monkeypatch.delenv("AUTOPOSTER_RELEASE")
    monkeypatch.setenv("AUTOPOSTER_VERSION", SHA_BUILD)

    assert (await get(client, auth_headers))["version"] == SHA_BUILD


async def test_an_empty_release_stamp_falls_through_rather_than_blanking(
    client, auth_headers, monkeypatch
):
    """`ENV AUTOPOSTER_RELEASE=${RELEASE_VERSION}` with no --build-arg sets the
    variable to the empty string rather than leaving it unset, which is what
    every non-release build produces. It must not shadow the commit stamp."""
    monkeypatch.setenv("AUTOPOSTER_RELEASE", "")
    monkeypatch.setenv("AUTOPOSTER_VERSION", SHA_BUILD)

    assert (await get(client, auth_headers))["version"] == SHA_BUILD


async def test_reports_dev_when_the_image_names_no_version_at_all(
    client, auth_headers, monkeypatch
):
    monkeypatch.delenv("AUTOPOSTER_RELEASE")

    assert (await get(client, auth_headers))["version"] == "dev"


async def test_the_bare_prefix_a_local_build_produces_is_dev(
    client, auth_headers, monkeypatch
):
    """What an unstamped build actually reports, and the case a `getenv`
    default would miss: `ENV AUTOPOSTER_VERSION=sha-${GIT_SHA}` with no
    `--build-arg` sets the variable to `sha-`. Reporting that verbatim would
    put a tag naming no commit in the sidebar."""
    monkeypatch.delenv("AUTOPOSTER_RELEASE")
    monkeypatch.setenv("AUTOPOSTER_VERSION", "sha-")

    assert (await get(client, auth_headers))["version"] == "dev"


# --- the endpoint reads the poller's cache, never GitHub ---------------------


async def test_before_the_first_poll_latest_and_update_available_are_null(
    client, auth_headers
):
    body = await get(client, auth_headers)

    assert body["latest"] is None
    assert body["update_available"] is None


async def test_a_newer_release_is_an_update(client, auth_headers, app):
    app.state.version_poller.latest = NEWER

    body = await get(client, auth_headers)

    assert body["latest"] == NEWER
    assert body["update_available"] is True


async def test_the_same_release_is_not_an_update(client, auth_headers, app):
    app.state.version_poller.latest = RUNNING

    assert (await get(client, auth_headers))["update_available"] is False


async def test_a_release_older_than_this_build_is_not_an_update(
    client, auth_headers, app
):
    """Strictly newer, not merely different. An image built from a tag ahead of
    the newest published release -- which is what the tagging commit's own
    build is, briefly -- must not be told to downgrade."""
    app.state.version_poller.latest = OLDER

    assert (await get(client, auth_headers))["update_available"] is False


async def test_double_digit_minors_order_numerically_not_lexically(
    client, auth_headers, app, monkeypatch
):
    """`"v0.9.0" > "v0.10.0"` as strings, so a string comparison would hide
    every update for as long as the minor stayed in double digits."""
    monkeypatch.setenv("AUTOPOSTER_RELEASE", "v0.9.0")
    app.state.version_poller.latest = "v0.10.0"

    assert (await get(client, auth_headers))["update_available"] is True


async def test_a_sha_build_can_never_be_compared(client, auth_headers, app, monkeypatch):
    """The gate, at the endpoint. Even with an answer cached, a build of main
    is neither ahead of a release nor behind one."""
    monkeypatch.delenv("AUTOPOSTER_RELEASE")
    monkeypatch.setenv("AUTOPOSTER_VERSION", SHA_BUILD)
    app.state.version_poller.latest = NEWER

    body = await get(client, auth_headers)

    assert body["version"] == SHA_BUILD
    assert body["latest"] == NEWER
    assert body["update_available"] is None, (
        "a commit build was compared against a release tag"
    )


async def test_an_unparseable_latest_tag_is_not_an_update(client, auth_headers, app):
    """A hand-made tag, or a release named something else entirely. Unknown,
    not "up to date"."""
    app.state.version_poller.latest = "nightly"

    assert (await get(client, auth_headers))["update_available"] is None


async def test_the_endpoint_never_calls_github_itself(client, auth_headers, app):
    """The whole reason the poll is a background task. A request-path call
    would mean one GitHub round trip per sidebar mount, against an anonymous
    rate limit of sixty an hour.

    The poller is given a live transport so a request would succeed if one were
    made -- asserting on a poller the endpoint does not touch would satisfy
    "zero requests" for the wrong reason.
    """
    http, seen = _mock_transport()
    async with http:
        app.state.version_poller._http = http

        await get(client, auth_headers)
        assert seen == [], "the endpoint must never call GitHub itself"

        # And the same poller, asked directly, does make one -- so the
        # assertion above is about the endpoint, not about a dead transport.
        await app.state.version_poller._poll()

    assert len(seen) == 1
    assert app.state.version_poller.latest == NEWER


# --- the gate ----------------------------------------------------------------


def test_a_release_build_with_an_http_client_is_enabled():
    assert ReleasePoller(http=object(), running_version=RUNNING).enabled is True


@pytest.mark.parametrize("stamp", [SHA_BUILD, "dev", "", "sha-", "nightly", "1.2.0"])
def test_only_a_v_prefixed_semver_build_is_enabled(stamp):
    """`1.2.0` without the `v` is in this list deliberately: the release
    workflow refuses to publish such a tag, so an image reporting one was not
    built by it and has no business comparing itself to releases."""
    assert ReleasePoller(http=object(), running_version=stamp).enabled is False


def test_without_an_http_client_nothing_is_enabled():
    """Every app not built with ``run_background=True`` -- which is every test
    app, and the placeholder ``create_app`` installs."""
    assert ReleasePoller(http=None, running_version=RUNNING).enabled is False


async def test_run_makes_no_request_at_all_for_a_commit_build():
    """The gate where it costs something: not merely "reports null", but
    "never opened a connection"."""
    http, seen = _mock_transport()
    async with http:
        poller = ReleasePoller(http=http, running_version=SHA_BUILD)
        await poller.run(asyncio.Event())

    assert seen == []
    assert poller.latest is None


# --- ReleasePoller: how GitHub is asked --------------------------------------


async def test_the_poll_asks_the_latest_release_endpoint():
    """``/releases/latest`` and not ``/releases``: GitHub excludes drafts and
    pre-releases from it, so a release published for testing cannot light the
    marker for everyone."""
    http, seen = _mock_transport()
    async with http:
        poller = ReleasePoller(http=http, running_version=RUNNING)
        await poller._poll()

    assert len(seen) == 1
    assert str(seen[0].url) == RELEASES_URL
    assert str(seen[0].url).endswith("/releases/latest")
    assert poller.latest == NEWER


async def test_the_poll_pins_the_api_version_and_media_type():
    """Otherwise the response shape is whatever the API's current default
    happens to be, which is not a thing to discover in production."""
    http, seen = _mock_transport()
    async with http:
        poller = ReleasePoller(http=http, running_version=RUNNING)
        await poller._poll()

    assert seen[0].headers["Accept"] == "application/vnd.github+json"
    assert seen[0].headers["X-GitHub-Api-Version"] == "2022-11-28"


async def test_the_poll_sends_no_credential():
    """The repository is public and the call is anonymous. An Authorization
    header here would mean a token had been wired back in."""
    http, seen = _mock_transport()
    async with http:
        poller = ReleasePoller(http=http, running_version=RUNNING)
        await poller._poll()

    assert "authorization" not in {k.lower() for k in seen[0].headers}


async def test_a_release_with_no_tag_name_is_no_answer():
    http, _ = _mock_transport(body={"name": "untagged somehow"})
    async with http:
        poller = ReleasePoller(http=http, running_version=RUNNING)
        await poller._poll()

    assert poller.latest is None


async def test_surrounding_whitespace_in_a_tag_is_not_part_of_the_version():
    http, _ = _mock_transport(body={"tag_name": "  v1.3.0\n"})
    async with http:
        poller = ReleasePoller(http=http, running_version=RUNNING)
        await poller._poll()

    assert poller.latest == NEWER


# --- ReleasePoller: failure --------------------------------------------------


async def test_a_refusal_is_logged_by_category_and_never_raised(caplog):
    """A repository with no published release answers 404, and a rate limit
    answers 403. Both are ordinary, neither is a 5xx for the caller."""
    http, _ = _mock_transport(status=404)
    async with http:
        poller = ReleasePoller(http=http, running_version=RUNNING)
        with caplog.at_level(logging.WARNING):
            await poller._poll()

    assert poller.latest is None
    assert "the update check failed" in caplog.text
    assert "status:" in caplog.text
    assert "404" in caplog.text, (
        "the status code is what separates a rate limit from a repository with "
        "no release yet, and it names nothing about where it was fetched from"
    )


async def test_an_unreachable_github_is_a_connect_failure(caplog):
    """A deployment with no outbound internet lands here on every poll. That
    is a permanent no-answer rather than an outage, and worth telling apart."""
    http, _ = _mock_transport(error=httpx.ConnectError("nope"))
    async with http:
        poller = ReleasePoller(http=http, running_version=RUNNING)
        with caplog.at_level(logging.WARNING):
            await poller._poll()

    assert poller.latest is None
    assert "connect:" in caplog.text


async def test_an_unreadable_answer_is_a_parse_failure(caplog):
    http, _ = _mock_transport(body=["not", "an", "object"])
    async with http:
        poller = ReleasePoller(http=http, running_version=RUNNING)
        with caplog.at_level(logging.WARNING):
            await poller._poll()

    assert poller.latest is None
    assert "parse:" in caplog.text


async def test_a_failed_poll_leaves_the_previous_answer_standing(caplog):
    """An outage must not erase a still-true answer -- the sidebar would blank
    a marker that is still correct."""
    http, _ = _mock_transport()
    async with http:
        poller = ReleasePoller(http=http, running_version=RUNNING)
        await poller._poll()
        assert poller.latest == NEWER

    broken, _ = _mock_transport(error=httpx.ConnectError("nope"))
    async with broken:
        poller._http = broken
        with caplog.at_level(logging.WARNING):
            await poller._poll()

    assert poller.latest == NEWER


# --- ReleasePoller: the background loop --------------------------------------


async def test_run_polls_before_it_waits(monkeypatch):
    """Polling first is what gives the sidebar an answer within seconds of
    boot rather than up to POLL_INTERVAL_SECONDS later."""
    stop = asyncio.Event()
    polls = 0
    real_poll = ReleasePoller._poll

    async def stopping_poll(self):
        nonlocal polls
        polls += 1
        await real_poll(self)
        stop.set()

    monkeypatch.setattr(ReleasePoller, "_poll", stopping_poll)
    http, _ = _mock_transport()
    async with http:
        poller = ReleasePoller(
            http=http, running_version=RUNNING, interval_seconds=9999
        )
        await asyncio.wait_for(poller.run(stop), timeout=5)

    assert polls == 1, "run() waited out an interval before its first poll"
    assert poller.latest == NEWER


async def test_run_returns_promptly_when_the_stop_event_is_set(monkeypatch):
    """Shutdown must not wait out the interval -- the lifespan cancels this
    task, and a six-hour sleep would hold the pod's termination open."""
    stop = asyncio.Event()
    real_poll = ReleasePoller._poll

    async def stopping_poll(self):
        await real_poll(self)
        stop.set()

    monkeypatch.setattr(ReleasePoller, "_poll", stopping_poll)
    http, _ = _mock_transport()
    async with http:
        poller = ReleasePoller(
            http=http, running_version=RUNNING, interval_seconds=0
        )
        await asyncio.wait_for(poller.run(stop), timeout=5)


# --- the version parser ------------------------------------------------------


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v1.2.3", (1, 2, 3)),
        ("v0.1.0", (0, 1, 0)),
        ("v10.20.30", (10, 20, 30)),
        ("  v1.2.3  ", (1, 2, 3)),
        ("1.2.3", None),
        ("v1.2", None),
        ("v1.2.3-rc1", None),
        ("sha-4b2a34d", None),
        ("dev", None),
        ("", None),
    ],
)
def test_the_version_parser_accepts_exactly_what_the_release_workflow_publishes(
    tag, expected
):
    """The release workflow refuses to publish anything but vMAJOR.MINOR.PATCH,
    so that is the whole set of versions that can exist. A pre-release suffix
    is rejected here for the same reason it is rejected there."""
    assert _version_tuple(tag) == expected
