"""The first-start setup wizard: a SECOND application, never a flag.

``create_app`` cannot serve this. It takes a ``Config`` and a session factory
positionally, publishes ``app.state.secrets`` at construction, and its
background lifespan's first statement opens a database session -- and a
deployment in setup mode has none of those three things, because they are what
the operator is being asked for.

Making it a second FastAPI object buys the row's own security constraint
literally: "the wizard exists only while unconfigured" is not a guard that can
be bypassed, because when the deployment is configured these route objects are
never constructed. It also leaves ``tests/test_api_login.py``'s structural 401
sweep unexempted -- a setup router on the normal application would have to be
excused from it, and an exemption list on a structural sweep is how the sweep
stops being structural.

Roadmap row 213 applies to every line here, and applies hardest: this
application's inputs are the most credential-dense strings the service will
ever hold. Nothing it was given is served or logged. Refusals are a fixed
sentence or an exception's class name -- never ``str(exc)``, which on a
connection error carries the DSN. What it reports about what it holds is a
presence map, ``***REDACTED***`` per set name, the same idiom
``GET /api/config`` uses at api/routes.py:1609.
"""

import asyncio
import logging
import secrets as secrets_module

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.responses import JSONResponse

from autoposter.api.auth import LoginRateLimiter, hash_password, verify_password
from autoposter.api.errors import validation_error_without_input
from autoposter.api.spa import mount_spa, spa_dist
from autoposter.config.schema import (
    _SECRET_ENV,
    _SOFT_SECRET_ENV,
    missing_hard_secret_names,
    resolve_secret_values,
)
from autoposter.config.state import merge_secrets_file, state_config_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup")

# The same string GET /api/config serves for every secret field
# (api/routes.py's _REDACTED). Repeated rather than imported: importing
# api/routes.py here would pull the whole application router into the setup
# process, which is the one thing this module exists not to do.
# tests/test_api_setup.py pins the two equal.
REDACTED = "***REDACTED***"

# Every refusal this application makes, spelled once. A fixed sentence cannot
# accidentally interpolate a value, which is the failure mode row 213 exists
# for; keeping them as constants is also what lets the tests assert on the
# sentence rather than on a substring.
NOT_AUTHENTICATED = "not authenticated"
INVALID_CREDENTIALS = "invalid credentials"
TOO_MANY_ATTEMPTS = "too many login attempts"
NOT_CONFIGURED = (
    "this deployment has not been set up yet; open / and complete the first-start wizard"
)
MINIMUM_PASSWORD_LENGTH = 12
PASSWORD_TOO_SHORT = (
    f"the master password must be at least {MINIMUM_PASSWORD_LENGTH} characters"
)
# bcrypt's own limit, and since bcrypt 4.0 ``hashpw`` RAISES on a longer
# password rather than truncating it (pyproject pins bcrypt>=4.2). Refused
# here, BEFORE the hash call, so an operator who pastes a long diceware
# passphrase into the first field of the first-start wizard gets this sentence
# instead of a bare 500 with no hash, no token and no explanation. A count of
# bytes, never the value that failed it.
MAXIMUM_PASSWORD_BYTES = 72
PASSWORD_TOO_LONG = (
    f"the master password must be at most {MAXIMUM_PASSWORD_BYTES} bytes as UTF-8"
)

SETUP_TOKEN_HEADER = "X-Setup-Token"

# Secret names the provider step must NOT collect, and which _presence_map
# therefore never reports:
#
# * AUTOPOSTER_DATABASE_URL has its own step, because it is the one credential
#   that is validated by being used;
# * AUTOPOSTER_ADMIN_PASSWORD_HASH is step 1's OUTPUT, not an operator's paste.
#   Offering it as a provider field would let a token-holder replace the master
#   password's hash with a caller-chosen string -- a malformed one locks the
#   deployment's admin out permanently, because verify_password returns False
#   on a malformed hash and the wizard is gone after the exec. It is already
#   reported anyway, as /progress's `password` boolean;
# * AUTOPOSTER_API_KEY is row 51's operator choice -- a key this service mints
#   for its own callers, not a third party's credential -- so it is configured
#   after setup, not during it.
_NOT_A_PROVIDER = (
    "AUTOPOSTER_DATABASE_URL",
    "AUTOPOSTER_ADMIN_PASSWORD_HASH",
    "AUTOPOSTER_API_KEY",
)

# The credentials the provider step collects: every secret name that is not on
# the list above. Derived from _SECRET_ENV/_SOFT_SECRET_ENV rather than
# restated, so a PROVIDER secret added to the model is collected here without a
# second edit -- while a non-provider one has to be named above to stay out.
_PROVIDER_ENV = tuple(
    name
    for name in (*_SECRET_ENV.values(), *_SOFT_SECRET_ENV.values())
    if name not in _NOT_A_PROVIDER
)


class SetupState:
    """What the setup application holds in memory, and nothing more.

    The token is minted by a successful master-password step and dies with the
    process. That is the whole of "exit setup mode atomically": the last step
    execs a fresh boot, so the token, these routes and this application object
    cease to exist at the same instant the credentials become complete. There
    is nothing to invalidate and no window in which a stale token outlives the
    mode it belonged to.

    Nothing else is held here. Every value the wizard collects goes to the
    state file immediately and is read back from it, so there is one source of
    truth for what this deployment has been told -- and a page that reloads
    mid-wizard picks up exactly where it left off.
    """

    def __init__(self) -> None:
        self.token: str | None = None
        # One persisting step at a time: merge_secrets_file is a
        # read-modify-write over a single file, and two concurrent steps would
        # otherwise drop one of the two writes. The app.state.mode_lock
        # precedent, for the same reason.
        self.lock = asyncio.Lock()


async def require_setup_token(
    request: Request,
    x_setup_token: str | None = Header(default=None, alias=SETUP_TOKEN_HEADER),
) -> None:
    """401s on an absent, malformed or wrong token, indistinguishably.

    ``require_session``'s contract, with an in-memory token instead of a
    database row -- because in setup mode there is no database, which is
    precisely what the operator is being asked to supply.
    """
    minted = request.app.state.setup.token
    if minted is None or x_setup_token is None:
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED)
    # Bytes, not str, and for the reason api/auth.py:_key_matches gives at the
    # same call: Starlette decodes header values as latin-1, so one byte >= 0x80
    # in X-Setup-Token arrives as a non-ASCII str and compare_digest raises
    # TypeError there -- an unhandled 500 from an unauthenticated caller, on
    # every route this dependency guards, and a malformed token distinguishable
    # from a wrong one, which is exactly what the docstring above promises it is
    # not.
    if not secrets_module.compare_digest(
        x_setup_token.encode("utf-8"), minted.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED)


RequireSetupToken = Depends(require_setup_token)


def _persisted_admin_hash() -> str:
    return resolve_secret_values().get("AUTOPOSTER_ADMIN_PASSWORD_HASH", "")


def _presence_map(resolved: dict[str, str]) -> dict[str, str | None]:
    """Which provider credentials this deployment holds -- never which values.

    ``***REDACTED***`` for a name that is set and ``null`` for one that is not:
    exactly what GET /api/config serves for the same fields, so the page has
    one idiom to render and this module has none of its own to get wrong.
    """
    return {name: (REDACTED if resolved.get(name) else None) for name in _PROVIDER_ENV}


@router.get("/state")
async def setup_state() -> dict:
    """The one open route, and the one the SPA probes on load.

    Two booleans and no enumeration of what is missing: an unauthenticated
    caller learns that this deployment is unconfigured -- which the wizard it
    is about to be served says anyway -- and that a master password has or has
    not been chosen, which is what decides whether the first pane asks the
    operator to set one or to prove one. The per-step detail is behind the
    token.
    """
    return {"setup": True, "password_set": bool(_persisted_admin_hash())}


class PasswordRequest(BaseModel):
    password: str


@router.post("/password")
async def set_master_password(body: PasswordRequest, request: Request) -> dict:
    """Step 1, and the only route here reachable without the setup token.

    Rate limited before the bcrypt call, for the reason api/routes.py's login
    handler gives in the same order: this endpoint needs no credential to
    reach and bcrypt at cost 12 is ~250 ms of CPU per attempt, which is enough
    to starve the process by asking.

    Two modes, one route. With nothing persisted the submitted password
    BECOMES the master password. With a hash already persisted -- a reloaded
    page, a second browser, a wizard resumed after a restart -- the submitted
    password is verified against it. Whoever reaches an unconfigured
    deployment first becomes its admin; that is intrinsic to any first-start
    wizard, and the mitigation is the row's own constraint that the wizard
    exists only while unconfigured.

    Unlike the login handler there is no timing oracle to defend: on the first
    call there is nothing to compare against, and on every later one there is,
    unconditionally.

    A successful call MINTS A NEW TOKEN and abandons the previous one, so a
    second tab -- or the same operator re-proving the password after a reload --
    401s the older tab mid-wizard with the sentence that means "wrong token".
    Deliberate: one live token is what makes "the token dies with the process"
    a complete account of its lifetime.
    """
    client = request.client.host if request.client else "unknown"
    # The limiter counts SUCCESSES too (LoginRateLimiter's existing contract),
    # so ten password posts in sixty seconds lock step 1 for the rest of the
    # window -- which a wizard that re-proves the password on every reload can
    # spend without an attacker.
    if not request.app.state.setup_rate_limiter.allow(client):
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS)

    state = request.app.state.setup
    async with state.lock:
        existing = _persisted_admin_hash()
        if existing:
            valid = await asyncio.to_thread(verify_password, body.password, existing)
            if not valid:
                raise HTTPException(status_code=401, detail=INVALID_CREDENTIALS)
        else:
            if len(body.password) < MINIMUM_PASSWORD_LENGTH:
                # The requirement, never the value that failed it.
                raise HTTPException(status_code=400, detail=PASSWORD_TOO_SHORT)
            if len(body.password.encode("utf-8")) > MAXIMUM_PASSWORD_BYTES:
                # Before the hash call: bcrypt raises ValueError past 72 bytes.
                raise HTTPException(status_code=400, detail=PASSWORD_TOO_LONG)
            hashed = await asyncio.to_thread(hash_password, body.password)
            merge_secrets_file({"AUTOPOSTER_ADMIN_PASSWORD_HASH": hashed})
        state.token = secrets_module.token_urlsafe(32)
        # The step name and nothing else -- C9.
        logger.info("first-start setup: the master password step completed")
        return {"token": state.token}


@router.get("/progress", dependencies=[RequireSetupToken])
async def setup_progress() -> dict:
    """What the wizard has been told, behind the token.

    ``required`` is the list of hard NAMES still unresolved, which is how the
    page knows the provider step is finished: several provider credentials are
    legitimately optional forever, so "every field filled" is not the test and
    the server is the only side that knows which are which.

    Read back from the state file on every call rather than from anything held
    in memory, so a page reloaded mid-wizard resumes from what was actually
    persisted. The steps this file gains later (the database URL, the provider
    keys, the config document) report through the same two lines.
    """
    resolved = resolve_secret_values()
    return {
        "password": bool(resolved.get("AUTOPOSTER_ADMIN_PASSWORD_HASH")),
        "database": bool(resolved.get("AUTOPOSTER_DATABASE_URL")),
        "providers": _presence_map(resolved),
        "required": missing_hard_secret_names(resolved),
        "config": state_config_path().is_file(),
    }


def build_setup_app() -> FastAPI:
    """The application an unconfigured deployment serves.

    No engine, no config, no scheduler, no worker pool, no log buffer, and no
    lifespan -- there is nothing to start. Only the setup router, the probe,
    one 503 for the rest of ``/api``, and the SPA. Nothing else: the probe is
    the ONE path outside ``/api/setup/*`` and the SPA's own files, and
    tests/test_api_setup.py pins that set exactly rather than by containment,
    so a second route added here has to be argued for in that test.

    No ``openapi_url``: an unauthenticated surface does not publish an
    enumeration of itself, and the real application already makes that a
    config decision (``api_docs_enabled``) rather than a default.
    """
    app = FastAPI(title="autoposter setup", openapi_url=None, docs_url=None, redoc_url=None)
    app.state.setup = SetupState()
    # Per process, the LoginRateLimiter contract. This process is replaced by
    # the exec at the end of the wizard, so the counter's lifetime is exactly
    # the wizard's.
    app.state.setup_rate_limiter = LoginRateLimiter()
    # The same 422 handler the real application installs. Here it matters more,
    # not less: every body this application takes is a credential, and
    # pydantic's `missing` arm echoes the whole body.
    app.add_exception_handler(RequestValidationError, validation_error_without_input)
    app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        """The shape intake/routes.py serves, with this mode's own status.

        Without it a deployment that reaches setup mode cannot be reached at
        all under the chart: kubernetes/apps/media/autoposter/app/helmrelease.yaml
        points liveness AND readiness at ``httpGet /healthz`` on 8080
        (initialDelaySeconds 30, periodSeconds 10, failureThreshold 5), and
        ``healthz`` is a RESERVED_PREFIXES entry, so the SPA fallback answers
        this server's own 404 rather than the shell. Readiness would never pass
        -- no Service endpoint, so the wizard is unreachable except by
        port-forward -- and liveness would kill the container at roughly t+80 s
        back into the same state, with a signal that says nothing about why.
        The reachable shape is a hard secret that resolves EMPTY: a blanked or
        failed ExternalSecret, which every reader in this row treats as absent.

        ``"setup"`` rather than ``"ok"``: the probe passing must not read as
        the application being up, because it is not -- this process is a
        credential form. Whoever is looking at the response can tell the two
        apart, and the probe cannot.
        """
        return {"status": "setup"}

    @app.api_route(
        "/api/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def not_configured(full_path: str) -> JSONResponse:
        """Everything the real application serves, while it is not running.

        Registered AFTER the setup router, so those paths keep their own
        handlers, and BEFORE mount_spa, whose catch-all would otherwise turn an
        API 404 into an HTML page and break every client's error handling.
        """
        return JSONResponse(status_code=503, content={"detail": NOT_CONFIGURED})

    # Last, for the reason main.build() gives: the SPA's catch-all matches
    # whatever no router claimed, so anything mounted afterwards is
    # unreachable.
    mount_spa(app, spa_dist())
    return app
