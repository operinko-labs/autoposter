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

One thing this module does that v1 did not: it makes outbound requests, to
addresses a caller partly supplies. ``api/setup_checks.py`` holds that surface
and its bound -- an allowlisted system key, a fixed path and method per system,
a scheme/userinfo guard on the four typed addresses, and a five-second ceiling.
No private-IP denylist, because every correct target on every shipped
deployment IS a private address. What remains, said plainly rather than papered
over: a holder of the setup token can learn whether an arbitrary host answers
on an arbitrary port, as a boolean.

One documented exception to "nothing it was given is served" (facts C6): the
Plex PIN code and the app.plex.tv auth URL. Both are minted by plex.tv, are
public by design, and the sign-in is impossible without showing them -- a
fourth category beside given, held and self-minted. The account token that
flow produces is in none of those categories: the poll route answers a boolean
and the value goes straight into `staged`.
"""

import asyncio
import logging
import os
import secrets as secrets_module
import sys
import uuid
from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

# Module scope, and safe in this direction: ``boot`` imports the setup
# surface lazily, inside ``main``. Anything given to ``boot``'s module scope
# is paid for by every setup process, and the double load under
# ``python -m autoposter.boot`` (``__main__`` plus ``autoposter.boot``) would
# run it twice.
from autoposter import boot
from autoposter.api.auth import LoginRateLimiter, hash_password, verify_password
from autoposter.api.errors import validation_error_without_input
from autoposter.api import setup_arr
from autoposter.api import setup_checks
from autoposter.api import setup_plex
from autoposter.api.spa import mount_spa, spa_dist
from autoposter.config.loader import (
    build_config,
    config_document_path,
    read_config_document,
)
from autoposter.config.schema import (
    _SECRET_ENV,
    _SOFT_SECRET_ENV,
    missing_hard_secret_names,
    resolve_secret_values,
)
from autoposter.config.state import (
    MAXIMUM_SECRET_LENGTH,
    example_config_path,
    is_storable,
    merge_secrets_file,
    state_config_path,
    state_dir,
    write_state_file,
)
from autoposter.db.base import database_answers

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
# `PlexConfig.url` is a bare `str`, so an empty or hostless one VALIDATES and
# the config step would write a document that loads perfectly and describes a
# deployment that cannot reach Plex -- with the wizard gone by then. Checked
# here, on the one field an operator types into this step, as a requirement and
# never as the value that failed it.
#
# Two clauses beyond the scheme, because v2 routes this field through the
# shared guard below and the guard enforces both: an operator who pasted
# `http://user:pass@plex:32400` has to read a sentence that describes why it
# was refused, which a sentence naming only the scheme does not.
PLEX_URL_NOT_AN_ADDRESS = (
    "the Plex server URL must be an http:// or https:// address with a host, "
    "with no username or password in it, and with no query string or fragment"
)
# The shared address guard's refusal, and the one Task 2's check endpoint
# reuses. Three requirements in one sentence because they are one decision: an
# address this service will connect to -- or hand to an *arr as a callback --
# on a caller's say-so must be a web address, must not smuggle a credential
# into a string that ends up in an *arr's database, its UI and its log lines,
# and must not carry a query string or fragment that a path appended to it
# would land after (facts C2: the registration URL never carries one).
PUBLIC_URL_NOT_AN_ADDRESS = (
    "this must be an http:// or https:// address with a host, with no "
    "username or password in it, and with no query string or fragment"
)
# Row 121 residue (b). The example document is baked into the image; an image
# missing it cannot compose a config document at all, and the operator needs to
# know that rather than reading a bare 500.
EXAMPLE_CONFIG_UNREADABLE = "the shipped example configuration document could not be read"
# The other side of _config_source/_config_ready: a deployment that already
# resolves a document -- a mounted ConfigMap, compose's bind-mounted example --
# is not offered this step at all (Amendment 6), and this is what enforces
# that as a server rule rather than a client courtesy that a direct POST can
# route around. Refusing here also protects the write ordering finish depends
# on: writing beside a document that already resolves would produce a second
# file the next boot never reads, with the operator's plex.url landing in it.
CONFIG_ALREADY_PROVIDED = (
    "this deployment already has a configuration document; the setup wizard "
    "does not offer to replace it"
)
# The Plex sign-in's own refusals. Steps, never values -- the same vocabulary
# every other refusal here uses.
NO_PLEX_SIGN_IN_IN_PROGRESS = "no Plex sign-in is in progress; start one from the Plex panel first"
NO_PLEX_ACCOUNT_TOKEN = (
    "this deployment has no Plex account token yet; sign in from the Plex panel first"
)
WEBHOOK_SECRET_IS_GENERATED = (
    "this credential is generated by the wizard and cannot be supplied: "
    "AUTOPOSTER_WEBHOOK_SECRET"
)
# The submitted NAMES are caller-chosen strings, so the refusal cannot name
# them: an operator who pastes a token into a name field would get it back in a
# response body that reaches a reverse-proxy log, a HAR export and the
# frontend's retained error text -- verbatim the incident api/errors.py exists
# to prevent. The page renders the accepted names from the presence map every
# call answers with, so it needs no echo to show which they are.
NOT_A_CREDENTIAL_THIS_SERVICE_READS = (
    "one of the submitted names is not a credential this service reads; the "
    "provider list this step answers with is the whole of what it accepts"
)
# A credential this deployment could never be given back: one the state file's
# reader would split into two entries, one carrying a NUL byte -- which makes
# boot's `os.environ[name] = value` raise at a boot where every hard secret
# resolves, so no wizard is served for that shape and the pod exits non-zero
# forever -- or one too long for the exec that follows. Refused at the step
# that ACCEPTS it. render_secrets_file refuses exactly the same values, but
# that raise lands inside the finish step -- after the config document has been
# written, as a 500 that names no field, with the wizard about to be gone. The
# NAME is safe to serve here and only here: it has already passed the
# _PROVIDER_ENV allowlist, so it is one of this module's own strings. The
# sentence names the RULES and never the value that broke one.
VALUE_IS_NOT_STORABLE = (
    "this credential must be a single line of at most "
    f"{MAXIMUM_SECRET_LENGTH} characters with no NUL byte, and this one is not:"
)
# What a write into the state directory answers when the directory will not
# take it: a missing PVC, a mount owned by another uid, a full volume. 503
# rather than 500 -- the deployment is not broken, its volume is -- and rather
# than 400, because the caller supplied nothing wrong. The DIRECTORY and the
# exception's CLASS only: boot.py's rule at the same decision, since an errno
# string carries a file name and merge_secrets_file's own message carries a
# variable name.
STATE_DIR_NOT_WRITABLE = "the state directory could not be written"

# The check endpoint's vocabulary. A system key is caller text -- exactly like
# a provider NAME at step 3 -- so the refusal names the surface and never the
# key it was given.
NOT_A_SYSTEM_THIS_WIZARD_CHECKS = (
    "one of the submitted names is not a system this wizard checks; the list "
    "this step answers with is the whole of what it accepts"
)
CHECK_NEEDS_AN_ADDRESS = "this system needs its own address before it can be checked"
CHECK_TAKES_NO_ADDRESS = "this system's address is built in and cannot be supplied"
# The three outcomes, spelled once (facts C4). `system` is always one of
# setup_checks.CHECK_SYSTEMS' own labels -- never the caller's key -- and the
# third carries an exception class name, the database_answers precedent.
CHECK_ANSWERED = "{system} answered."
CHECK_REFUSED = "{system} refused the credential."
CHECK_UNREACHABLE = "{system} could not be reached ({failure})."

# The registration's own vocabulary (facts C2/C2a/C3). Two step names for the
# values the registration needs and does not have, one refusal for a name it
# does not register, and two outcome sentences -- the refusal one is the check
# endpoint's own, reused rather than reworded, because it is the same fact about
# the same credential.
NOT_A_SERVICE_THIS_WIZARD_REGISTERS = (
    "one of the submitted names is not a service this wizard registers a "
    "webhook with; Radarr and Sonarr are the whole of what it accepts"
)
NO_CHECKED_ADDRESS = (
    "this service has no checked address yet; run Check connection on its panel first"
)
NO_DEPLOYMENT_URL = (
    "this deployment's own URL has not been given yet; go back to the address step first"
)
# `action` is "created" or "updated" -- facts C2a asks for the distinction on
# the finish page -- and `failure` is a status marker or an exception class
# name, never the *arr's own text. The second sentence on acceptance is the
# `forceSave` fact: the *arr's own connection test never ran (setup_arr's
# docstring), so the operator must not be left thinking one already proved the
# hook works.
REGISTRATION_ACCEPTED = (
    "{system} accepted the webhook registration ({action}). No test was sent; "
    "{system} will exercise the hook on its first real event."
)
REGISTRATION_REFUSED = "{system} would not accept the webhook registration ({failure})."
# A second call for the same service while the first is still mid-flight
# (review I1): the flag below refuses it outright rather than letting it list
# and create a second time, which is the duplicate facts C2a exists to
# prevent.
REGISTRATION_IN_PROGRESS = "{system}'s webhook registration is already in progress."

# Which wizard step an unmet requirement belongs to, and the whole of what the
# finish step is allowed to say about it. Fixed strings: the check that
# produces them runs over credentials, and "which step" is the most this
# surface may report.
STEP_DATABASE = "setup is not complete: the database step has not been finished"
STEP_PROVIDERS = "setup is not complete: the provider keys step has not been finished"
STEP_CONFIG = "setup is not complete: the configuration step has not been finished"
STEP_DATABASE_UNREACHABLE = (
    "setup is not complete: the database this deployment was given did not answer"
)
# The post-write gate. Reachable only if what was just persisted does not
# satisfy `boot.is_configured` -- an operator editing the state directory
# underneath the wizard, or a config document that vanished between the write
# and the check -- and the answer is to stay in setup mode rather than exec a
# boot that would exit non-zero into a restart loop with no wizard on the port.
STEP_NOT_CONFIRMED = (
    "setup is not complete: the persisted state did not pass the boot check"
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

# The one provider credential this deployment CHOOSES rather than is given.
# Sonarr and Radarr sign their webhooks with a secret the receiver picks, so
# somebody has to pick it, and an operator typing one produces a weaker secret
# and puts a credential on the wire that came out of a form. It is generated by
# the provider step instead, staged like the pasted ones, and served ONCE --
# by ``GET /api/setup/webhook-secret`` and by nothing else, on the last pane,
# as the string the operator pastes into Sonarr and Radarr. Submitting one is
# REFUSED rather than accepted, which is what makes "the only value this
# application ever serves is one it minted itself" a property of the code
# rather than a claim about it.
_GENERATED_SECRET = "AUTOPOSTER_WEBHOOK_SECRET"


class SetupState:
    """What the setup application holds in memory, and nothing more.

    The token is minted by a successful master-password step and dies with the
    process. That is the whole of "exit setup mode atomically": the last step
    execs a fresh boot, so the token, these routes and this application object
    cease to exist at the same instant the credentials become complete. There
    is nothing to invalidate and no window in which a stale token outlives the
    mode it belonged to.

    The database URL, the provider keys and the config document are STAGED
    here and written at the finish step, never as they are collected (facts
    Amendment 3). Persisting them step by step has one reachable failure that
    this ordering removes entirely: a wizard abandoned -- or a pod evicted --
    between the last hard secret landing and the config document landing
    leaves a deployment whose credentials all resolve and whose document does
    not, which ``boot`` treats as a configuration error and a non-zero exit.
    That deployment cannot be fixed by the wizard, because no wizard is served
    for that shape. Staged, the same interruption leaves the hard secrets
    absent and the next boot is the wizard again, from step 1.

    The cost is the one this row can afford: a page reloaded mid-wizard
    restarts from the master password, which it must re-prove anyway to get a
    token. Step 1's hash is a SOFT secret and is persisted immediately, so
    re-proving it is possible at all.
    """

    def __init__(self) -> None:
        self.token: str | None = None
        # Secret NAME -> value, for the names the finish step will write. The
        # plaintext credentials of a deployment being set up live here and
        # nowhere else until that step.
        self.staged: dict[str, str] = {}
        # The validated config document the finish step will write, or None
        # while the config step has not been completed.
        self.config_document: dict | None = None
        # This deployment's own externally reachable address (wizard v2 step
        # 2). Staged like a credential and for the same reason -- the finish
        # step is the only writer -- but it is NOT a credential: it lands in
        # the config document, not the secrets file. On a deployment whose
        # document already resolves it is used for the *arr registration and
        # persisted nowhere, which the finish page says by name (facts C1).
        self.public_url: str | None = None
        # System key -> the base address the operator typed for it, for the
        # three systems whose address a caller supplies (Task 2 writes it).
        # Same lifecycle as public_url: staged, stamped onto the document, or
        # used-and-not-persisted when a document already resolves.
        self.base_urls: dict[str, str] = {}
        # This deployment's Plex client identifier, and the PIN currently
        # outstanding. The identifier must be the SAME string on the mint, the
        # auth link and every poll -- plex.tv 404s a poll whose identifier
        # differs, which the implementation probe measured -- and it names
        # THIS DEPLOYMENT as a device on the operator's plex.tv account, so it
        # is minted once and reused: a fresh one per attempt would leave a
        # dead device entry on their account for every sign-in they restarted.
        # A random string and never a hostname; staged, never persisted,
        # because nothing after the wizard needs it.
        self.plex_client_identifier: str | None = None
        self.plex_pin_id: int | None = None
        # Whether the generated webhook secret has already been served. The
        # value itself lives in ``staged`` like every other credential -- the
        # finish step writes it and the *arr registration reads it there -- and
        # this is the whole of "served once": the route below answers the value
        # while this is False and ``null`` forever after.
        self.webhook_secret_served = False
        # The *arr services with a webhook registration in flight right now
        # (review I1). Checked and added with no `await` between the two, so
        # this is atomic under asyncio's cooperative scheduling: a second POST
        # for a service already in this set is refused outright instead of
        # listing and creating a second time, which is the duplicate facts
        # C2a exists to prevent. Removed in a `finally`, so a raised or timed
        # out registration frees the service too.
        self.registering: set[str] = set()
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


def _persist(write, *args) -> None:
    """One write into the state directory, or a fixed 503.

    Every write this application makes goes through here. The reachable
    failure is the deployment shape this row exists for: a pod whose PVC is
    missing or misowned reaches setup mode, passes /healthz, is routed by the
    Ingress -- and its very first POST is an UNAUTHENTICATED route whose write
    raises PermissionError out of ``directory.mkdir``. Unhandled, that is a
    bare 500 with FastAPI's generic body, from which an operator cannot tell an
    unwritable volume from a broken build.
    """
    try:
        write(*args)
    except OSError as exc:
        # The step name only -- C9 -- and the sentence carries the directory
        # an operator sets, never a file name and never the errno text.
        logger.error("first-start setup: the state directory refused a write")
        raise HTTPException(
            status_code=503,
            detail=f"{STATE_DIR_NOT_WRITABLE}: {state_dir()} ({type(exc).__name__})",
        ) from None


def _persisted_admin_hash() -> str:
    return resolve_secret_values().get("AUTOPOSTER_ADMIN_PASSWORD_HASH", "")


def _require_http_url(value: str, refusal: str) -> str:
    """An operator-typed address, or a fixed refusal that never names it.

    Lifted out of the config step's own check (which was the first place this
    was needed) because v2 has four more: the deployment's own URL, and the
    three systems whose base address a caller supplies to the check and
    registration endpoints. One spelling, so the four cannot disagree.

    Schemes are case-insensitive (RFC 3986) and browsers normalise them, so a
    pasted HTTP://plex.lan refused by a sentence asking for http:// reads as
    the wizard being wrong.

    Userinfo is refused rather than stripped: an operator who pasted
    `http://user:pass@sonarr` meant the credential to be used, and silently
    dropping it would produce a check that fails for a reason the sentence does
    not give. The returned value has its trailing slash removed so that the
    fixed paths this module appends never double one.

    A PATH is allowed and a query string or fragment is not, because every
    caller of this guard appends a fixed path to what it returns. A deployment
    legitimately lives at a path prefix behind a reverse proxy, so
    `https://media.example.test/autoposter` has to pass; but
    `https://autoposter.example.test/?x=1` would compose the *arr callback as
    `https://autoposter.example.test/?x=1/webhook/sonarr` -- a string written
    into that *arr's database, its UI and its logs, and shown to the operator
    on the finish page. Refused here, once, rather than in each of the four
    callers (facts C2: the registration URL never carries one).
    """
    cleaned = value.strip()
    scheme, separator, rest = cleaned.partition("://")
    if scheme.lower() not in {"http", "https"} or not separator or not rest:
        raise HTTPException(status_code=400, detail=refusal)
    if "?" in rest or "#" in rest:
        raise HTTPException(status_code=400, detail=refusal)
    authority = rest.partition("/")[0]
    if "@" in authority or authority == "":
        raise HTTPException(status_code=400, detail=refusal)
    return cleaned.rstrip("/")


def _presence_map(resolved: dict[str, str]) -> dict[str, str | None]:
    """Which provider credentials this deployment holds -- never which values.

    ``***REDACTED***`` for a name that is set and ``null`` for one that is not:
    exactly what GET /api/config serves for the same fields, so the page has
    one idiom to render and this module has none of its own to get wrong.
    """
    return {name: (REDACTED if resolved.get(name) else None) for name in _PROVIDER_ENV}


def _effective(request: Request) -> dict[str, str]:
    """What this deployment WOULD hold if the wizard finished now.

    The persisted names (environment first, state file second) with the staged
    ones on top, in that order, because a name typed into the wizard is the
    operator correcting what the deployment already had. Every reporting and
    completeness check in this module asks this rather than
    ``resolve_secret_values`` alone, so "the page says the step is done" and
    "the finish step agrees" read the same map.
    """
    resolved = resolve_secret_values()
    resolved.update(request.app.state.setup.staged)
    return resolved


def _database_source(request: Request) -> str:
    """WHICH side answered the database step: the deployment, or this wizard.

    ``_effective`` merges the persisted names with the staged ones and then
    cannot tell them apart, which is right for "is this step met" and wrong for
    "may the operator go back and change it". Both questions are asked of
    /progress, so the second gets its own word.

    ``resolved`` and deliberately not ``environment``: ``resolve_secret_values``
    reads the environment AND the state file, and setup mode is entered when
    ANY hard secret is missing -- so a deployment that already persisted a
    database URL is a real shape and lands here too. The word means "not the
    wizard's own", which is the whole of what the page needs: a value the boot
    resolver already answers with is one this wizard cannot improve on, and
    asking for it again implies it can.

    ``staged`` is the case that was invisible: the step's OWN submit made
    ``database`` true, the page dropped the step from the order, and its
    ``Stored`` pill and its empty-means-keep became unreachable.
    """
    if resolve_secret_values().get("AUTOPOSTER_DATABASE_URL"):
        return "resolved"
    if request.app.state.setup.staged.get("AUTOPOSTER_DATABASE_URL"):
        return "staged"
    return "missing"


def _config_source(request: Request) -> str | None:
    """Where the document the NEXT BOOT will read comes from, or None.

    ``config_document_path`` and never ``state_config_path().is_file()``:
    ``boot`` prefers a PRESENT ``AUTOPOSTER_CONFIG`` and falls back to the
    state document, and every shipped shape sets that variable -- the image
    bakes ``/config/autoposter.yaml``, the HelmRelease sets it beside a
    ConfigMap mount, and docker-compose.yml points it at the example. Setup
    mode is reachable on all three (a blanked or failed ExternalSecret; a .env
    missing one hard name), so a wizard that asked only about the state path
    would demand step 4 on a deployment that already HAS a document, write the
    operator's Plex URL to a file the next boot never opens, and answer with
    that file's name. One resolver for the wizard and for the boot, so the two
    cannot look in different places.

    Two sources because the resolver has two, plus the wizard's own. A document
    this wizard is merely HOLDING answers ``staged`` -- v1 answered ``state``
    for it, because ``state`` is where the finish step will put it, and that
    conflated the one document the wizard may still replace with the one it may
    not. The page reads this word to decide whether to offer the step, so under
    the old word a well-formed but WRONG Plex URL could not be corrected for
    the life of the process: this endpoint validates the document, it does not
    reach the server the URL names.

    ``configured`` and ``state`` keep v1's meaning exactly -- a document the
    boot resolver answers with, which this wizard cannot replace and which the
    finish step will not write over. ``staged`` and ``null`` are the two the
    step stays offered for. The source is a WORD and never the path: /progress
    is a presence surface.
    """
    path = config_document_path()
    if path is not None:
        return "state" if path == state_config_path() else "configured"
    return "staged" if request.app.state.setup.config_document is not None else None


def _config_ready(request: Request) -> bool:
    """Whether the config step is met: a document the next boot will read, or
    one staged for the finish step to write."""
    return _config_source(request) is not None


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
            _persist(merge_secrets_file, {"AUTOPOSTER_ADMIN_PASSWORD_HASH": hashed})
        state.token = secrets_module.token_urlsafe(32)
        # The step name and nothing else -- C9.
        logger.info("first-start setup: the master password step completed")
        return {"token": state.token}


@router.get("/progress", dependencies=[RequireSetupToken])
async def setup_progress(request: Request) -> dict:
    """What the wizard has been told, behind the token.

    ``required`` is the list of hard NAMES still unresolved, which is how the
    page knows the provider step is finished: several provider credentials are
    legitimately optional forever, so "every field filled" is not the test and
    the server is the only side that knows which are which.

    Over the EFFECTIVE map -- persisted plus staged -- so it reports the wizard
    the operator is in the middle of, not the empty state directory the finish
    step has not written yet. The password boolean is the exception and is read
    from the persisted map, because step 1's hash is persisted immediately and
    a staged copy of it does not exist.

    ``config_source`` is how the page knows whether to OFFER step 4: a
    deployment that already has a document -- a mounted ConfigMap, compose's
    bind-mounted example -- reports ``"configured"`` and the step is skipped,
    because the wizard cannot edit that file and writing beside it would
    discard what the operator typed. A word, never the path.

    ``database_source`` and ``config_source`` are the same idiom and answer the
    same question for two steps: ``resolved`` (``configured``/``state`` for the
    document) is a value the BOOT RESOLVER holds, ``staged`` is one this wizard
    holds for this session, and ``missing``/``null`` is neither. The boolean
    beside each says whether the step is MET; the word says whether the
    operator may still change the answer, and a step is hidden only for the
    first. Without it a step's own submit deleted the step, and neither its
    ``Stored`` pill nor its empty-means-keep was reachable from the page.

    ``checked_systems`` is ``public_url``'s idiom applied to the other half of
    the *arr registration's precondition: WHICH systems have a successfully
    checked address, never the address itself. The finish page reads it beside
    ``providers`` to tell a service this deployment does not run at all --
    "Not configured" -- from one that is configured but whose registration was
    simply never pressed, or was pressed and failed -- "Not attempted".

    Presence and names only, on every line: ``***REDACTED***``/``null`` per
    provider, booleans per step, and NAMES in ``required``. The generated
    webhook secret is reported here exactly like the pasted ones -- as
    presence. The response that generated it is the only place its value ever
    appears.
    """
    resolved = _effective(request)
    source = _config_source(request)
    return {
        "password": bool(resolve_secret_values().get("AUTOPOSTER_ADMIN_PASSWORD_HASH")),
        "database": bool(resolved.get("AUTOPOSTER_DATABASE_URL")),
        # WHICH side answered it, which the boolean above cannot say: the page
        # hides a step only for a value the boot resolver already holds, and
        # keeps one the wizard staged reachable so it can be corrected.
        "database_source": _database_source(request),
        "providers": _presence_map(resolved),
        "required": missing_hard_secret_names(resolved),
        "config": source is not None,
        "config_source": source,
        # v2 step 2. Presence, like every other line here: the address is not
        # a credential, but /progress is a presence surface and stays one.
        "public_url": request.app.state.setup.public_url is not None,
        # NAMES only -- which systems a successful check staged an address
        # for, never the address itself.
        "checked_systems": sorted(request.app.state.setup.base_urls),
    }


class DatabaseRequest(BaseModel):
    url: str


@router.post("/database", dependencies=[RequireSetupToken])
async def set_database_url(body: DatabaseRequest, request: Request) -> dict:
    """Step 2, validated by USING the URL rather than by parsing it.

    ``make_engine`` is lazy, so a well-formed URL pointing at nothing
    constructs perfectly and fails on the first real query hours later -- by
    which time the wizard is gone and the deployment crashloops. The connection
    is made here, and the value is kept only if it answered.

    The refusal is the exception's CLASS NAME and nothing else: a connection
    error's own text carries the DSN, password included, and this string is a
    response body.

    Staged rather than written (facts Amendment 3): the database URL is a hard
    secret, and no hard secret reaches the state file before the finish step.

    An empty submit KEEPS what is already held (facts C7), the provider step's
    rule at the step that shares its problem: this value is never sent back to
    the page, so a pane stepped back into renders an empty field beside a
    ``Stored`` pill, and the only way forward would otherwise be to re-type a
    URL the server already has. Empty with nothing held falls through to the
    checks below, because then there is nothing to keep.
    """
    if not body.url.strip() and _effective(request).get("AUTOPOSTER_DATABASE_URL"):
        # No probe: an empty field is not a new value to try.
        return {"ok": True}
    if not is_storable(body.url):
        # Before the probe, for the reason step 3 gives at the same check: a
        # URL the state file's reader would split into two entries -- or one
        # the process environment could not carry -- is refused by
        # render_secrets_file at the finish step, which is too late.
        raise HTTPException(
            status_code=400,
            detail=f"{VALUE_IS_NOT_STORABLE} AUTOPOSTER_DATABASE_URL",
        )
    answered, failure = await database_answers(body.url)
    if not answered:
        raise HTTPException(
            status_code=400, detail=f"the database did not answer ({failure})"
        )
    async with request.app.state.setup.lock:
        request.app.state.setup.staged["AUTOPOSTER_DATABASE_URL"] = body.url
    logger.info("first-start setup: the database step completed")
    return {"ok": True}


class PublicUrlRequest(BaseModel):
    url: str


@router.post("/public-url", dependencies=[RequireSetupToken])
async def set_public_url(body: PublicUrlRequest, request: Request) -> dict:
    """Step 2: the address other services will reach this deployment at.

    Asked before the database and the systems because it is what the *arr
    registration builds its callback from, and because an operator who does not
    know it yet should find that out at the top of the wizard rather than at
    the bottom.

    Not persisted here. On a fresh deployment it lands in the config document
    the finish step writes; on one whose document already resolves it is used
    for the registration and written nowhere, because
    ``POST /api/setup/config`` refuses to write beside a resolving document
    (Amendment 6) and reversing that would produce a second file the next boot
    never opens. The finish page names that omission and the key it would have
    been (facts C1).

    An empty submit keeps the staged address, for the reason the database step
    above gives at the same decision (facts C7). Empty with nothing staged is
    refused, because then there is nothing to keep.
    """
    if not body.url.strip() and request.app.state.setup.public_url is not None:
        return {"ok": True}
    url = _require_http_url(body.url, PUBLIC_URL_NOT_AN_ADDRESS)
    if not is_storable(url):
        raise HTTPException(status_code=400, detail=PUBLIC_URL_NOT_AN_ADDRESS)
    async with request.app.state.setup.lock:
        request.app.state.setup.public_url = url
    logger.info("first-start setup: the deployment address step completed")
    return {"ok": True}


class CheckRequest(BaseModel):
    system: str
    base_url: str | None = None
    #: The credential typed into the form beside the button, when one was. Used
    #: for THIS probe and staged nowhere -- staging stays with Save. Absent or
    #: empty means "keep", the rule every one-field pane in this wizard has, and
    #: the probe then runs against the credential the deployment holds.
    credential_value: str | None = None


@router.post("/check", dependencies=[RequireSetupToken])
async def check_connection(body: CheckRequest, request: Request) -> dict:
    """"Does this credential work" for one system, as one of three sentences.

    ONE route rather than ten. Ten would be ten places to get the redaction
    rule wrong and ten entries to argue into the route sweep; one is a table
    (api/setup_checks.py) whose whole bound is readable at once.

    The address a successful check proved is STAGED, because this is the only
    moment the wizard knows it works: the *arr registration reads it from
    there, and ``_apply_staged_urls`` stamps it onto the config document a
    fresh deployment writes. A failed check stages nothing -- an address that
    did not answer is not a fact about this deployment.

    The CREDENTIAL is read the same way the address is: the value typed beside
    the button when there is one, and the one the deployment holds when the
    field is empty. Sending only the address meant the two inputs in one form
    behaved oppositely -- the address live, the credential whatever was last
    SAVED -- so a freshly pasted key was answered "refused" about a key that is
    correct. An inline value authenticates this probe and is staged nowhere:
    what the deployment WILL hold is Save's answer, not a question's.

    Never the provider's own body: an *arr's 400 echoes the fields it was sent,
    and a provider's error text can carry a key out of a query string.
    """
    check = setup_checks.CHECK_SYSTEMS.get(body.system)
    if check is None:
        raise HTTPException(status_code=400, detail=NOT_A_SYSTEM_THIS_WIZARD_CHECKS)

    base_url: str | None = None
    if check.host is None:
        if not body.base_url:
            raise HTTPException(status_code=400, detail=CHECK_NEEDS_AN_ADDRESS)
        base_url = _require_http_url(body.base_url, PUBLIC_URL_NOT_AN_ADDRESS)
        # `set_public_url`'s pair, for the same reason: a successful check
        # STAGES this value and the finish step writes it into the config
        # document, so an address that cannot survive that round trip is
        # refused where it is typed rather than at the write.
        if not is_storable(base_url):
            raise HTTPException(status_code=400, detail=PUBLIC_URL_NOT_AN_ADDRESS)
    elif body.base_url:
        raise HTTPException(status_code=400, detail=CHECK_TAKES_NO_ADDRESS)

    # `_effective` returns a fresh mapping per call, so the inline value goes
    # into this probe's copy and reaches nothing that outlives it.
    credentials = _effective(request)
    if body.credential_value and check.credential is not None:
        credentials[check.credential] = body.credential_value

    outcome = await setup_checks.run_check(body.system, base_url, credentials)

    if outcome.ok:
        if base_url is not None:
            async with request.app.state.setup.lock:
                request.app.state.setup.base_urls[body.system] = base_url
        return {"ok": True, "detail": CHECK_ANSWERED.format(system=check.label)}
    if outcome.refused:
        return {"ok": False, "detail": CHECK_REFUSED.format(system=check.label)}
    return {
        "ok": False,
        "detail": CHECK_UNREACHABLE.format(system=check.label, failure=outcome.failure),
    }


@router.post("/plex/pin", dependencies=[RequireSetupToken])
async def mint_plex_pin(request: Request) -> dict:
    """Start a Plex sign-in, and serve the operator what the flow needs.

    The PIN CODE and the ``app.plex.tv`` link ARE served, and that is the one
    documented exception to this module's "the only value it serves is one it
    minted itself" rule (facts C6): both are minted by plex.tv, are public by
    design, and the flow is impossible without showing them. The ``authToken``
    is not in that category -- the poll below answers a boolean.

    The client identifier is minted once per process and REUSED, because it
    names this deployment as a device on the operator's account rather than
    naming an attempt. What must not be inherited from an abandoned attempt is
    its PIN, and the pin id below is overwritten on every mint: the poll asks
    about the newest PIN whichever identifier carried it.
    """
    state = request.app.state.setup
    identifier = state.plex_client_identifier or str(uuid.uuid4())
    try:
        minted = await setup_plex.mint_pin(identifier)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=CHECK_UNREACHABLE.format(system="the Plex account", failure=type(exc).__name__),
        ) from None

    async with state.lock:
        state.plex_client_identifier = identifier
        state.plex_pin_id = minted["id"]

    expires_at = datetime.fromisoformat(minted["expires_at"].replace("Z", "+00:00"))
    remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    logger.info("first-start setup: a Plex sign-in was started")
    return {
        "code": minted["code"],
        "auth_url": setup_plex.auth_url(identifier, minted["code"]),
        # Seconds rather than the timestamp: the client polls against a
        # deadline it computes once, and a clock skew between the browser and
        # plex.tv would otherwise stop the poll early or never.
        "expires_in": max(remaining, 0),
    }


@router.get("/plex/pin", dependencies=[RequireSetupToken])
async def poll_plex_pin(request: Request) -> dict:
    """Has the operator approved yet -- as a BOOLEAN.

    The account token never reaches this response. On success it goes straight
    into ``staged``, under BOTH ``AUTOPOSTER_PLEX_TOKEN`` and
    ``AUTOPOSTER_PLEX_ACCOUNT_TOKEN`` and with no exchange: probe 1 established
    that the OWNED server's ``accessToken`` equals the account token, which is
    the ruling's own precondition. Servers shared to the account are out of
    scope, which is why the listing below filters them out server-side.

    No background poll on the server. A task outliving its request in an
    application with no lifespan is a task nothing shuts down; the client polls
    at two seconds and stops at the PIN's expiry.
    """
    state = request.app.state.setup
    if state.plex_pin_id is None or state.plex_client_identifier is None:
        raise HTTPException(status_code=400, detail=NO_PLEX_SIGN_IN_IN_PROGRESS)

    try:
        account_token = await setup_plex.poll_pin(state.plex_pin_id, state.plex_client_identifier)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=CHECK_UNREACHABLE.format(system="the Plex account", failure=type(exc).__name__),
        ) from None

    if account_token is None:
        return {"authorised": False}
    # The same guard `/providers` applies to a pasted credential, applied to
    # one this wizard was handed instead: a value the state file's reader would
    # split in two, or one too long for the exec that follows, must be refused
    # at the step that accepts it rather than at the finish write.
    if not is_storable(account_token):
        raise HTTPException(
            status_code=400, detail=f"{VALUE_IS_NOT_STORABLE} AUTOPOSTER_PLEX_ACCOUNT_TOKEN"
        )
    async with state.lock:
        state.staged["AUTOPOSTER_PLEX_TOKEN"] = account_token
        state.staged["AUTOPOSTER_PLEX_ACCOUNT_TOKEN"] = account_token
    logger.info("first-start setup: the Plex sign-in was approved")
    return {"authorised": True}


@router.get("/plex/servers", dependencies=[RequireSetupToken])
async def list_plex_servers(request: Request) -> dict:
    """The account's OWN servers, with no token in any entry.

    ``owned == True`` is applied in ``setup_plex`` rather than here or on the
    page, because it is a scope decision: a shared server would need its own
    per-resource token, which is the exchange path the ruling excludes.
    """
    account_token = _effective(request).get("AUTOPOSTER_PLEX_ACCOUNT_TOKEN", "")
    if not account_token:
        raise HTTPException(status_code=400, detail=NO_PLEX_ACCOUNT_TOKEN)
    state = request.app.state.setup
    identifier = state.plex_client_identifier or str(uuid.uuid4())
    try:
        servers = await setup_plex.owned_servers(account_token, identifier)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=CHECK_UNREACHABLE.format(system="the Plex account", failure=type(exc).__name__),
        ) from None
    async with state.lock:
        state.plex_client_identifier = identifier
    return {"servers": servers}


class PlexLibrariesRequest(BaseModel):
    base_url: str
    #: The Plex token typed beside the address, when one was -- ``CheckRequest``'s
    #: field and its semantics exactly: used for THIS read and staged nowhere,
    #: absent or empty meaning "keep", so an untouched field reads with the token
    #: the deployment already holds.
    credential_value: str | None = None


@router.post("/plex/libraries", dependencies=[RequireSetupToken])
async def list_plex_libraries(body: PlexLibrariesRequest, request: Request) -> dict:
    """The chosen server's libraries, for the tick-list -- from EITHER arrival.

    The pick-list is one way in and a typed address is the other, and both are
    needed: the configuration document has one writer, and behind a completed
    plex.tv sign-in alone it is unreachable for a deployment whose server is
    linked to no plex.tv account, whose pod cannot reach plex.tv, or whose
    picked connection the pod cannot route to -- and a wizard that cannot stage
    a document cannot be finished. So the address is read from the body and the
    token the way ``/check`` reads its two inputs, and both arrivals end at the
    same tick-list and the same ``POST /config``.

    The address goes through the same guard the check endpoint and the URL step
    use -- it is operator-supplied whether it was typed or picked, since a
    pick-list is a request body like any other.
    """
    base_url = _require_http_url(body.base_url, PUBLIC_URL_NOT_AN_ADDRESS)
    # `_effective` returns a fresh mapping per call, so nothing here outlives
    # this read: staging a credential stays with Save.
    token = body.credential_value or _effective(request).get("AUTOPOSTER_PLEX_TOKEN", "")
    if not token:
        raise HTTPException(status_code=400, detail=NO_PLEX_ACCOUNT_TOKEN)
    state = request.app.state.setup
    identifier = state.plex_client_identifier or str(uuid.uuid4())
    try:
        libraries = await setup_plex.library_sections(base_url, token, identifier)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=CHECK_UNREACHABLE.format(system="Plex", failure=type(exc).__name__),
        ) from None
    async with state.lock:
        state.plex_client_identifier = identifier
    return {"libraries": libraries}


class ArrWebhookRequest(BaseModel):
    #: ``radarr`` or ``sonarr``. Validated against ``setup_arr.NAMES`` and never
    #: rendered back: the refusal names the surface, not the string it was given.
    service: str


@router.post("/arr/webhook", dependencies=[RequireSetupToken])
async def register_arr_webhook(body: ArrWebhookRequest, request: Request) -> dict:
    """Point one *arr's Webhook connection at this deployment, idempotently.

    Everything it needs is already staged, each by the one step that knows it:
    the address by a SUCCESSFUL check (the single moment the wizard knows that
    address works), the API key by that system's accordion, the callback base by
    the URL step, and the secret by the provider step that minted it. A missing
    one is named as a STEP rather than reported as a value, which is why the
    three refusals below are step names.

    Never a 500 and never a blocker (facts C3). A registration that did not work
    is answered with ``200`` and ``ok: false`` carrying one of two fixed
    sentences, because "the *arr would not take it" is a RESULT this step
    reports rather than an error in the request that asked for it -- and because
    the finish page has to be able to list it as skipped. The operator can paste
    the secret by hand, which is what they do today; a registration that gated
    the exit would turn a third-party outage into an unfinishable wizard.

    The *arr's own response body reaches nothing here: ``setup_arr`` answers
    with a marker or an exception class name, and a 400 from an *arr echoes the
    fields it was sent -- one of which is the secret.

    A second call for the same service while the first is still mid-flight is
    refused with ``REGISTRATION_IN_PROGRESS`` and never reaches ``setup_arr``
    at all: two overlapping calls would both list an empty registration and
    both create, which is the duplicate facts C2a exists to prevent (review
    I1). A sequential second call is unaffected -- it lists the first's own
    entry and updates it.
    """
    if body.service not in setup_arr.NAMES:
        raise HTTPException(status_code=400, detail=NOT_A_SERVICE_THIS_WIZARD_REGISTERS)

    state = request.app.state.setup
    label = setup_checks.CHECK_SYSTEMS[body.service].label
    base_url = state.base_urls.get(body.service)
    if not base_url:
        raise HTTPException(status_code=400, detail=NO_CHECKED_ADDRESS)
    if not state.public_url:
        raise HTTPException(status_code=400, detail=NO_DEPLOYMENT_URL)

    resolved = _effective(request)
    api_key = resolved.get(f"AUTOPOSTER_{body.service.upper()}_APIKEY", "")
    secret = resolved.get(_GENERATED_SECRET, "")
    if not api_key or not secret:
        raise HTTPException(status_code=400, detail=STEP_PROVIDERS)

    # The in-flight guard (review I1): a second POST for this service while
    # the first is still mid-flight must not also list-and-create, which is
    # the duplicate facts C2a exists to prevent. No `await` sits between the
    # membership test and the add, so this is atomic under asyncio's
    # cooperative scheduling -- whichever call reaches here first wins, and
    # the other is refused without ever touching the *arr.
    if body.service in state.registering:
        return {
            "ok": False,
            "action": None,
            "detail": REGISTRATION_IN_PROGRESS.format(system=label),
        }
    state.registering.add(body.service)
    try:
        action, failure = await setup_arr.register(
            body.service, base_url, api_key, state.public_url, secret
        )
    finally:
        state.registering.discard(body.service)
    # The step only -- C9/C10. Not the service's address, not which service, not
    # the outcome: the wizard's log lines name the STEP and nothing that would
    # let a reader of them reconstruct the deployment.
    logger.info("first-start setup: a webhook registration was attempted")

    if action is not None:
        return {
            "ok": True,
            "action": action,
            "detail": REGISTRATION_ACCEPTED.format(system=label, action=action),
        }
    if failure == "refused":
        return {"ok": False, "action": None, "detail": CHECK_REFUSED.format(system=label)}
    return {
        "ok": False,
        "action": None,
        "detail": REGISTRATION_REFUSED.format(system=label, failure=failure),
    }


class ProvidersRequest(BaseModel):
    """Keyed by environment variable NAME -- the same vocabulary the presence
    map the page renders is keyed by, so there is no mapping table that can
    fall out of step with ``_SECRET_ENV``."""

    values: dict[str, str]


@router.post("/providers", dependencies=[RequireSetupToken])
async def set_provider_keys(body: ProvidersRequest, request: Request) -> dict:
    """Step 3. Every name optional; an omitted or empty one keeps what is
    already held, because for a credential "cleared" and "not retyped" are
    indistinguishable and the safe reading is the second.

    Answers with the presence map rather than an acknowledgement, so the page
    renders what the deployment now holds without a second round trip and
    without ever being sent a pasted value back.

    This step MINTS the generated webhook secret and does not serve it. The
    two are separate because the pane that shows it is the last one, several
    steps later: a save that answered with the value would make a reload in
    between lose it for good -- the next save has nothing left to mint -- and
    the finish step would persist a secret the operator never saw. It is
    staged here and served by ``GET /webhook-secret`` below, once.
    """
    if set(body.values) - set(_PROVIDER_ENV):
        # A fixed sentence: the submitted keys are caller-chosen strings.
        raise HTTPException(
            status_code=400, detail=NOT_A_CREDENTIAL_THIS_SERVICE_READS
        )
    if _GENERATED_SECRET in body.values:
        raise HTTPException(status_code=400, detail=WEBHOOK_SECRET_IS_GENERATED)
    for name in sorted(body.values):
        if not is_storable(body.values[name]):
            # The name has passed the allowlist above, so it is one of this
            # module's own strings; the value is never served.
            raise HTTPException(
                status_code=400, detail=f"{VALUE_IS_NOT_STORABLE} {name}"
            )
    supplied = {name: value for name, value in body.values.items() if value}
    state = request.app.state.setup
    async with state.lock:
        state.staged.update(supplied)
        resolved = _effective(request)
        if not resolved.get(_GENERATED_SECRET):
            generated = secrets_module.token_urlsafe(32)
            state.staged[_GENERATED_SECRET] = generated
            resolved[_GENERATED_SECRET] = generated
    logger.info("first-start setup: the provider keys step completed")
    return {"providers": _presence_map(resolved)}


@router.get("/providers", dependencies=[RequireSetupToken])
async def get_provider_keys(request: Request) -> dict:
    """The presence map on its own, in the POST's shape so the page has one
    body to render."""
    return {"providers": _presence_map(_effective(request))}


@router.get("/webhook-secret", dependencies=[RequireSetupToken])
async def get_webhook_secret(request: Request) -> dict:
    """The generated webhook secret, once, for the pane that shows it.

    The only value this application ever serves, and the only route that
    serves it. Once, in the v1 sense the Amendment ratified: the first call
    answers the string, every later one answers ``null``, and ``/progress``
    reports it as presence thereafter. The page holds what it was given, so an
    operator stepping back from the last pane and forward again re-renders the
    value already served rather than asking for a second serve.

    Not logged, here or anywhere: a fixed step name is the most this module
    says about any credential (facts C10), and this one is a credential the
    deployment is about to sign its webhooks with.
    """
    state = request.app.state.setup
    async with state.lock:
        if state.webhook_secret_served:
            return {"webhook_secret": None}
        generated = state.staged.get(_GENERATED_SECRET)
        if generated is None:
            # Nothing minted yet: the provider step has not been saved. Asking
            # does not mint one -- this route serves, it never generates.
            return {"webhook_secret": None}
        state.webhook_secret_served = True
        return {"webhook_secret": generated}


class ConfigRequest(BaseModel):
    plex_url: str
    # The tick-list's COMPLEMENT: the schema's field is `excluded_libraries`,
    # and the example's own two entries belong to one deployment. Absent means
    # "unchanged" -- the provider step's "empty means keep" rule, for the same
    # reason: an operator who never reached the tick-list has asked for nothing.
    # An EMPTY LIST is a different answer and is applied: it is what an
    # operator who ticked every library said.
    excluded_libraries: list[str] | None = None


@router.post("/config", dependencies=[RequireSetupToken])
async def stage_config_document(body: ConfigRequest, request: Request) -> dict:
    """Step 4: the config document a fresh deployment does not have.

    Parsed from the shipped example, given the operator's ``plex.url``,
    VALIDATED, and only then staged -- a document that does not load would
    leave the next boot crashing inside ``load_config`` with the wizard already
    gone, which is the one failure this whole row exists to prevent.

    ``plex.url`` is checked here as well as validated, because
    ``PlexConfig.url`` is a bare ``str``: an empty or hostless one passes the
    model and produces a deployment that loads its config and cannot reach
    Plex. The requirement is reported, never the value that failed it.

    Re-serialised rather than patched as text, so what lands is exactly what
    validated. The example's comments do not survive that, which is
    acceptable: the Settings editor is where this document is edited from here
    on.

    Written at the finish step and not here (facts Amendment 3), in front of
    the secrets file -- see ``finish``. ``path`` is where it will land, which
    is what the page shows the operator.

    Row 98's plex.tv owner check is deliberately not in scope -- this step
    records a URL, it does not authenticate against it.

    Refused outright when a document already resolves (Amendment 6): the
    progress surface stops offering this step at that point, and this is what
    makes that a server rule rather than a client courtesy a direct POST could
    route around. A document this wizard merely STAGED is a different thing and
    is simply replaced -- nothing here reaches the Plex server the URL names,
    so a well-formed wrong address is accepted and has to stay correctable.
    """
    if config_document_path() is not None:
        raise HTTPException(status_code=400, detail=CONFIG_ALREADY_PROVIDED)

    # Facts C7, the rule /public-url and /database already hold: a staged
    # document is never served back, so a step navigated into again shows an
    # empty field, and an empty submit there means "keep what you have".
    # Empty with nothing staged still falls through to the refusal below.
    if not body.plex_url and request.app.state.setup.config_document is not None:
        return {"path": str(state_config_path())}

    body_plex_url = _require_http_url(body.plex_url, PLEX_URL_NOT_AN_ADDRESS)

    try:
        document = read_config_document(example_config_path())
    except Exception as exc:
        # Row 121 residue (b). The class name only, boot.py's rule at the same
        # decision: an OSError's own text carries a file name.
        logger.error("first-start setup: the example configuration document could not be read")
        raise HTTPException(
            status_code=503,
            detail=f"{EXAMPLE_CONFIG_UNREADABLE} ({type(exc).__name__})",
        ) from None

    _apply_staged_urls(document, request.app.state.setup)
    # AFTER the staged addresses and not before them: the Plex accordion and
    # this field render on the same step, so an operator who checked
    # `http://plex.lan:32400`, realised that is the NAT address and typed the
    # in-cluster one here is one operator correcting one value. The typed
    # document wins, and the staged entry is dropped below so that finish --
    # which applies the staged map a second time -- cannot put it back.
    document.setdefault("plex", {})["url"] = body_plex_url
    if body.excluded_libraries is not None:
        document["plex"]["excluded_libraries"] = body.excluded_libraries
    try:
        build_config(document)
    except Exception as exc:
        # Class name only, the intake/routes.py idiom: a validation error's own
        # text quotes the values it rejected.
        raise HTTPException(
            status_code=400,
            detail=f"the configuration document was rejected ({type(exc).__name__})",
        ) from None

    async with request.app.state.setup.lock:
        request.app.state.setup.config_document = document
        # Only once the document validated, so a refusal changes nothing. The
        # three *arr entries stay: each has exactly one writer, and it is the
        # check. `plex` is the one address two panes can write.
        request.app.state.setup.base_urls.pop("plex", None)
    logger.info("first-start setup: the configuration step completed")
    return {"path": str(state_config_path())}


def _apply_staged_urls(document: dict, state: SetupState) -> dict:
    """Stamp the addresses the wizard staged onto the document about to land.

    Called twice on purpose: once at the config step, so what `build_config`
    validates is what will be written, and once immediately before the write at
    finish, so an address staged AFTER the config step is not lost. Back
    navigation makes both orders reachable -- the operator can complete the
    Plex accordion, step back to the URL pane, and correct it -- so "the last
    staged value wins" has to be a property of the write rather than of the
    order the panes happened to be visited in.

    Mutates and returns the mapping it was given; the callers own the copy.
    """
    if state.public_url is not None:
        document["public_url"] = state.public_url
    for system, base_url in state.base_urls.items():
        # `plex` is the one whose config key is `url` rather than `base_url` --
        # PlexConfig predates the three *arr-shaped sections -- and the one an
        # explicit config submit DROPS from this map, because it is the one
        # address two panes on the same step can write. `plex_account`
        # and the six built-in hosts never reach here: only the four typed
        # systems are ever staged, and the account has no address of its own.
        if system == "plex":
            document.setdefault("plex", {})["url"] = base_url
        elif system in ("radarr", "sonarr", "tracearr"):
            document.setdefault(system, {})["base_url"] = base_url
    return document


def _unmet_step(resolved: dict[str, str], config_ready: bool) -> str | None:
    if not resolved.get("AUTOPOSTER_DATABASE_URL"):
        return STEP_DATABASE
    if missing_hard_secret_names(resolved):
        return STEP_PROVIDERS
    if not config_ready:
        return STEP_CONFIG
    return None


@router.post("/finish", dependencies=[RequireSetupToken])
async def finish(request: Request) -> JSONResponse:
    """Step 5: write what the wizard staged, re-run the boot check in process,
    then hand the process over.

    The write order is the config document FIRST and the secrets file second,
    each atomically (facts Amendment 3). Both orders have a window; only this
    one has a survivable window. Secrets-first crashing halfway leaves a
    deployment whose credentials all resolve and whose document does not, which
    ``boot`` reports as a configuration error and exits non-zero -- forever,
    because no wizard is served for that shape. Document-first crashing halfway
    leaves a document and no credentials, which is the wizard again.

    Then ``boot.is_configured`` over what was actually persisted, rather than
    over what this process believes it wrote: the same function, the same
    resolver and the same order the next boot will use, so "the wizard says it
    is done" and "the next boot agrees" cannot disagree. A failed check names
    the unmet step and execs nothing, leaving the deployment in setup mode with
    the wizard still on the port.

    ``os.execv`` rather than a flag: nothing in a running setup application can
    become the real one -- no migration has run, no engine exists,
    ``create_app`` was never called -- and an exec replaces this process image
    with a fresh boot that re-derives the mode from credentials that now
    resolve. The setup token, these routes and this application object die with
    it, which is what makes the exit atomic rather than a flag somebody has to
    remember to flip.

    Scheduled on the response's background task list so the body is written
    before the process is replaced. The page then polls ``GET
    /api/setup/state`` until the port answers again with ``{"setup": false}``.
    """
    state = request.app.state.setup
    effective = _effective(request)
    unmet = _unmet_step(effective, _config_ready(request))
    if unmet is not None:
        # Nothing is written: an incomplete wizard leaves the state directory
        # exactly as it found it.
        raise HTTPException(status_code=400, detail=unmet)
    answered, _failure = await database_answers(effective["AUTOPOSTER_DATABASE_URL"])
    if not answered:
        raise HTTPException(status_code=400, detail=STEP_DATABASE_UNREACHABLE)

    async with state.lock:
        if state.config_document is not None:
            _persist(
                write_state_file,
                state_config_path(),
                yaml.safe_dump(
                    _apply_staged_urls(dict(state.config_document), state),
                    sort_keys=False,
                    allow_unicode=True,
                ),
            )
        # An OSError here, after the document landed, is the same 503: the
        # document stays and the hard secrets stay ABSENT, which is the next
        # boot back in setup mode -- the survivable half of Amendment 3's
        # ordering, reported rather than served as a 500.
        _persist(merge_secrets_file, state.staged)

    persisted = resolve_secret_values()
    if not boot.is_configured(persisted):
        raise HTTPException(
            status_code=400,
            detail=_unmet_step(persisted, config_document_path() is not None)
            or STEP_NOT_CONFIRMED,
        )
    logger.info("first-start setup: complete; restarting into the application")
    return JSONResponse(content={"restarting": True}, background=BackgroundTask(_exec_boot))


def _exec_boot() -> None:
    os.execv(sys.executable, [sys.executable, "-m", "autoposter.boot"])


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
