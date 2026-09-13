"""Ten "does this credential work" probes, and the bound on all of them.

This is the wizard's only outbound surface, and the one place where an
authenticated caller names a target this service then connects to. That is an
SSRF shape, and it is bounded by being a TABLE rather than a fetcher:

* the caller names a SYSTEM KEY from an allowlist, never a URL on its own;
* the PATH, the METHOD, the HEADER NAMES and which credential authenticates are
  compiled in, per system;
* five of the ten have a compiled-in HOST as well; the five that do not (Plex,
  Jellyfin, Radarr, Sonarr, Tracearr) take a base address that must pass
  ``api/setup._require_http_url`` -- http/https, a host, no userinfo;
* the whole probe is bounded by ``asyncio.wait_for`` at five seconds, the value
  and the idiom ``db/base.database_answers`` chose for the same reason;
* the RESPONSE is streamed and its body abandoned unread, except for the one
  subject that has to look inside it, which reads ``CHECK_BODY_LIMIT_BYTES``
  and no more. Five seconds of a local network is gigabytes, and the pod's
  memory limit is the first thing that would give.

**What is deliberately NOT here: a private-IP denylist.** Every correct answer
on every shipped deployment IS a private address -- ``http://sonarr``,
``http://plex:32400`` -- so a denylist would refuse exactly the addresses that
work and nothing else. The honest statement of what remains is therefore: a
caller who already holds the setup token (minted only by the master password,
rate-limited) can learn whether an arbitrary host answers on an arbitrary port,
as a boolean, five seconds at a time. That is a boolean port scan of the
network this pod sits in, it is not closed by anything in this module, and it
is written here rather than papered over.

**What is NOT in that residual, because a rule above this module closes it.**
The probe carries a credential, and for the five systems whose address the
caller also supplies that credential must have come from the caller: the value
typed into the same request, or one this WIZARD staged. It is never one the
BOOT RESOLVER answered with -- the environment or the state file -- because
setup mode is entered when any ONE hard secret fails to resolve, so a pod in it
still holds every OTHER credential its deployment was given. Without the rule,
a single request naming an attacker's host and omitting the credential sent the
live ``AUTOPOSTER_PLEX_TOKEN``, or an *arr API key, to that host.
``api/setup.check_connection`` and ``api/setup.list_plex_libraries`` enforce it
and answer ``CHECK_NEEDS_A_TYPED_CREDENTIAL`` otherwise -- a fixed sentence
with no value and no host in it, and no request made at all. So the residual
above really is a boolean. The bound is on WHICH CREDENTIAL may go to a
caller-named host and not on which host: the denylist is still not here, and
still cannot be.

Row 213 holds throughout: the outcome is two booleans plus, on a failure, an
exception CLASS NAME. The third party's own response body is never read into
anything returned or logged -- an *arr's 400 echoes the fields it was sent, and
a provider's error text can carry the key out of a query string. The url does
not reach a log record either: httpx logs one INFO line per request with the
whole of it, and two of these ten carry their credential in the query string,
so the probe runs with that logger filtered here as well as clamped at boot.
"""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass

import httpx


logger = logging.getLogger(__name__)

# The whole probe, not a per-library connect option: only asyncpg bounds a
# connect implicitly and nothing bounds a response, so a host that accepts and
# then stops answering is precisely the case this covers. db/base.py's value.
CHECK_TIMEOUT_SECONDS = 5.0

# The most of a response body this module will ever hold. Nine of the ten
# subjects are answered by the status and one header and read no body at all;
# MDBList's spent-budget shape is a shallow object, so its head is enough. The
# timeout alone is not a bound on SIZE -- five seconds of a private network is
# gigabytes into a pod with a memory limit -- and this is the one item on the
# bound list that would otherwise be absent rather than argued.
CHECK_BODY_LIMIT_BYTES = 64 * 1024

# What Jellyfin records as this client's version on the device it lists for the
# API key. The wizard has no running application to ask, and the field is
# cosmetic -- the token half of the header is what authenticates -- so it says
# which half of this service is calling rather than a number it cannot know.
SETUP_VERSION = "setup"


class _DropEveryRecord(logging.Filter):
    """Attached to the ``httpx`` logger for the length of one probe."""

    def filter(self, record: logging.LogRecord) -> bool:
        return False


@contextlib.contextmanager
def no_httpx_request_log():
    """httpx logs one INFO line per request carrying the FULL url.

    ``boot.main`` already clamps that logger to WARNING for the whole process,
    and this is the same clamp held locally, because this is the module that
    puts a credential IN a url: Fanart's and MDBList's keys ride the query
    string (``providers/fanart.py``, ``providers/mdblist.py``), and the five
    typed addresses are operator URLs. A property row 213 depends on is not
    left to a setting made in another file.

    A FILTER rather than a level, because two operators can check two systems
    at once: filters compose, so an overlapping probe removing its own still
    leaves the other's in place, where restoring a saved level would re-enable
    the log line under the second probe.
    """
    httpx_logger = logging.getLogger("httpx")
    silence = _DropEveryRecord()
    httpx_logger.addFilter(silence)
    try:
        yield
    finally:
        httpx_logger.removeFilter(silence)


class TracearrDidNotAnswer(Exception):
    """A 200 with no ``x-ratelimit-limit`` header: Tracearr's SPA answered, not
    its API (providers/tracearr.py:175), so the key was never tested."""


@dataclass(frozen=True)
class Check:
    """One system's probe, entirely compiled in except ``host`` for five.

    ``label`` is the word every sentence about this system uses: one of this
    module's own strings, never the caller's key, which is what keeps a
    caller-chosen string out of the response body.
    """

    label: str
    #: ``None`` for the five systems whose base address the operator supplies.
    host: str | None
    #: Fixed. A caller can never name a path.
    path: str
    #: The environment NAME whose value authenticates, or ``None``.
    credential: str | None
    #: How that value is carried: one of the six spellings ``_probe`` knows.
    auth: str
    method: str = "GET"
    #: TVDB's login is the one probe with a body, and the body is the key.
    json_credential_key: str | None = None
    #: MDBList answers 200 with this key set when the daily budget is spent.
    error_key: str | None = None
    #: Tracearr's proof that the API and not the SPA answered.
    require_header: str | None = None


CHECK_SYSTEMS: dict[str, Check] = {
    # /library/sections rather than /identity: identity is unauthenticated
    # (plex/health.py:58) and proves reachability only, while sections proves
    # the URL AND the token -- and is the same read the library tick-list
    # needs, so the wizard makes it once.
    "plex": Check(
        label="Plex",
        host=None,
        path="/library/sections",
        credential="AUTOPOSTER_PLEX_TOKEN",
        auth="x-plex-token",
    ),
    "plex_account": Check(
        label="the Plex account",
        host="https://plex.tv",
        path="/api/v2/resources?includeHttps=1&includeRelay=0",
        credential="AUTOPOSTER_PLEX_ACCOUNT_TOKEN",
        auth="x-plex-token",
    ),
    # The second media server. Its credential is the one this module does not
    # spell itself: the value format is `MediaBrowser Token="..."`, written in
    # jellyfin/client.py, so the probe borrows that builder rather than keeping
    # a second copy of a header a typo makes indistinguishable from a wrong key
    # (X-Emby-Token is refused with a 401 on 12.0). /System/Info and not the
    # unauthenticated /System/Info/Public, for the reason Plex reads
    # /library/sections: this probe has to prove the API KEY and not just
    # reachability (docs/reference/2026-09-jellyfin-openapi-12.md).
    "jellyfin": Check(
        label="Jellyfin",
        host=None,
        path="/System/Info",
        credential="AUTOPOSTER_JELLYFIN_APIKEY",
        auth="mediabrowser",
    ),
    # providers/tmdb.py:131 -- the configured token is a v4 read access token,
    # carried as a bearer. /3/configuration is the cheapest authenticated read.
    "tmdb": Check(
        label="TMDb",
        host="https://api.themoviedb.org",
        path="/3/configuration",
        credential="AUTOPOSTER_TMDB_TOKEN",
        auth="bearer",
    ),
    # A POST, and TVDB's only way to validate a key (providers/tvdb.py's
    # _login). It creates nothing: the response is a session token this module
    # reads for nothing and discards.
    "tvdb": Check(
        label="TVDB",
        host="https://api4.thetvdb.com",
        path="/v4/login",
        credential="AUTOPOSTER_TVDB_APIKEY",
        auth="json",
        method="POST",
        json_credential_key="apikey",
    ),
    # providers/fanart.py:112 -- the key rides the query string. 550 is a
    # long-standing TMDb id, used here as a known-present subject.
    "fanart": Check(
        label="Fanart",
        host="https://webservice.fanart.tv",
        path="/v3.2/movies/550",
        credential="AUTOPOSTER_FANART_APIKEY",
        auth="query-api_key",
    ),
    "mdblist": Check(
        label="MDBList",
        host="https://api.mdblist.com",
        path="/tmdb/movie/550/",
        credential="AUTOPOSTER_MDBLIST_APIKEY",
        auth="query-apikey",
        error_key="error",
    ),
    "radarr": Check(
        label="Radarr",
        host=None,
        path="/api/v3/system/status",
        credential="AUTOPOSTER_RADARR_APIKEY",
        auth="x-api-key",
    ),
    "sonarr": Check(
        label="Sonarr",
        host=None,
        path="/api/v3/system/status",
        credential="AUTOPOSTER_SONARR_APIKEY",
        auth="x-api-key",
    ),
    "tracearr": Check(
        label="Tracearr",
        host=None,
        path="/api/v2/public/history?limit=1",
        credential="AUTOPOSTER_TRACEARR_APIKEY",
        auth="bearer",
        require_header="x-ratelimit-limit",
    ),
}


@dataclass(frozen=True)
class CheckOutcome:
    ok: bool
    #: True when the service answered and rejected the credential (401/403, or
    #: MDBList's 200-with-an-error-body). False for everything else.
    refused: bool
    #: An exception class name or a status marker, never a message.
    failure: str | None


async def _capped_body(response: httpx.Response) -> bytes:
    """The head of a streamed body, with the rest abandoned rather than read."""
    head = bytearray()
    async for chunk in response.aiter_bytes():
        head += chunk
        if len(head) >= CHECK_BODY_LIMIT_BYTES:
            break
    return bytes(head[:CHECK_BODY_LIMIT_BYTES])


async def _probe(client: httpx.AsyncClient, check: Check, url: str, value: str) -> CheckOutcome:
    """One request, and the reading of its answer. Never its body's text."""
    headers: dict[str, str] = {"accept": "application/json"}
    params: dict[str, str] = {}
    json_body: dict[str, str] | None = None

    if check.auth == "x-plex-token":
        headers["X-Plex-Token"] = value
    elif check.auth == "x-api-key":
        headers["X-Api-Key"] = value
    elif check.auth == "bearer":
        headers["Authorization"] = f"Bearer {value}"
    elif check.auth == "mediabrowser":
        # Imported here and not at module scope: jellyfin/client.py pulls the
        # index and the writer in behind it, and a setup process that never
        # checks Jellyfin should not pay for them. The Authorization value
        # alone -- the builder's `Accept` is the one this probe already set.
        from autoposter.jellyfin.client import JellyfinApi

        headers["Authorization"] = JellyfinApi(None, "", value, SETUP_VERSION).headers()[
            "Authorization"
        ]
    elif check.auth == "query-api_key":
        params["api_key"] = value
    elif check.auth == "query-apikey":
        params["apikey"] = value
    elif check.auth == "json":
        json_body = {check.json_credential_key or "apikey": value}

    # `params or None` and not `params`: httpx turns a FALSY params into
    # `query=None` and then `copy_with(query=None)`, which drops the query the
    # url string already carried rather than leaving it alone -- and three of
    # these ten compile a query into their path, two of them (`limit=1`,
    # `page_size=1`) precisely to keep the probe cheap.
    #
    # `stream` and not `request`: the latter buffers the whole body before this
    # sees the status, and the body is a third party's.
    async with client.stream(
        check.method, url, headers=headers, params=params or None, json=json_body
    ) as response:
        if response.status_code in (401, 403):
            return CheckOutcome(ok=False, refused=True, failure=None)
        if not response.is_success:
            # A status is a number, not the service's text.
            return CheckOutcome(
                ok=False, refused=False, failure=f"HTTPStatus{response.status_code}"
            )
        if check.require_header is not None and check.require_header not in response.headers:
            raise TracearrDidNotAnswer()
        if check.error_key is not None:
            # The head only. A body too large to be MDBList's shallow
            # spent-budget object is not that object, and the JSONDecodeError a
            # truncated head raises is reported as a class name like any other
            # failure -- which is the honest answer, and a bounded one.
            payload = json.loads(await _capped_body(response))
            if isinstance(payload, dict) and payload.get(check.error_key):
                # MDBList's spent-budget shape. "Refused" is the honest
                # reading: the question this endpoint answers is whether the
                # credential can do work now, and a spent key cannot.
                return CheckOutcome(ok=False, refused=True, failure=None)
    return CheckOutcome(ok=True, refused=False, failure=None)


async def run_check(
    system: str,
    base_url: str | None,
    credentials: dict[str, str],
    transport: httpx.BaseTransport | None = None,
) -> CheckOutcome:
    """Probe one system. Never raises for a network reason.

    A short-lived client per call rather than a lifespan: the setup application
    starts nothing, which is half its security argument, and a check is
    operator-paced -- a few dozen per wizard at most.

    ``transport`` is the test seam and nothing else; production passes ``None``.
    """
    check = CHECK_SYSTEMS[system]
    value = credentials.get(check.credential or "", "")

    try:
        host = check.host if check.host is not None else base_url
        url = f"{host}{check.path}"

        async def attempt() -> CheckOutcome:
            async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
                with no_httpx_request_log():
                    return await _probe(client, check, url, value)

        return await asyncio.wait_for(attempt(), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:
        # The CLASS NAME and nothing else, on every arm. httpx embeds the full
        # URL in its own messages, and a URL here can carry a query-string key.
        # The log line names the SYSTEM only -- api/setup.py's step-name rule.
        logger.info("first-start setup: a connection check did not succeed (%s)", system)
        return CheckOutcome(ok=False, refused=False, failure=type(exc).__name__)
