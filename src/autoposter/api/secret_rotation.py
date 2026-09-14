"""Rotating the Radarr/Sonarr webhook secret from the Settings page.

Its own module rather than 160 more lines in `api/routes.py`: it is the only
handler in this API that imports `setup_arr`, it owns a vocabulary nothing
else uses, and `routes.py` is already 2 400 lines. `api/item_overrides.py`
and eleven others are the same argument.

WHAT IT MAY IMPORT. `setup_arr` imports only stdlib, httpx and
`setup_checks`, which imports only stdlib and httpx.
Neither is part of the setup SURFACE `boot.main` keeps out of a configured
boot -- `api/setup.py` is -- so importing them here at module scope is safe
and importing `api.setup` would not be. The four lines of the unwritable-
state-directory guard below are therefore duplicated from `api/setup.py`'s
`_persist` rather than imported, which is the same call `setup_arr` makes for
`_guarded_base_url`: lifting the wrapper into `config/state.py` would put
`fastapi.HTTPException` into a module that is deliberately framework-free and
is imported by `config/schema.py`.

ORDER OF OPERATIONS, and it is load-bearing: write the layer that ANSWERS
this name -- the stored row, or the state file when the file is what answers
-> rebind `app.state.secrets` -> re-register both *arrs -> report. The write
is the only step whose failure changes nothing (`write_state_file` is atomic
and unlinks its partial; the row is one committed statement), so it goes
first. Registration goes last because a
failed one is already a non-raising, fixed-sentence RESULT in this codebase,
not an error -- and it is reported, never rolled back.

NO DUAL-ACCEPT AND NO GRACE WINDOW. `forceSave=true` on the UPDATE path makes
the *arr skip its own save-time connection test, so there is no moment during
the write at which it calls back with the old secret and needs a 200 --
register-first would buy nothing mechanically. A window in which the old
value still authenticates is a security control whose END NOTHING CAN
OBSERVE: nothing tells this application when an *arr adopted the new header.
`intake/routes.py` keeps exactly one `compare_digest`.

THE REBIND'S BLAST RADIUS reaches `app.state.secrets` readers and nothing
else. Every consumer handed the `Secrets` object at construction -- the
provider clients, the notifier, the Plex server factories, the arr-sync job
-- keeps the OLD one. That is harmless for `webhook_secret`, whose only
reader in `src/` is `intake/routes.py`, per request; it would be a bug for
any other name, so the next person who reaches for this route as a template
needs this sentence rather than folklore.

THE ADDRESS BOUND THE WIZARD CARRIES DOES NOT CARRY OVER, deliberately. The
wizard binds a resolved secret to a reconstructed address because there the
address was staged by a REQUEST, and a check proves a host answered, never
who owns it. Here the addresses come from this deployment's own validated
`Config`, so the bound is satisfied by construction and the request body
carries no address field whatsoever. Keeping the guard would be cargo cult.

NO `is_storable` CHECK. `secrets.token_urlsafe(32)` is 43 URL-safe
characters: it cannot split across lines, cannot hold a NUL and cannot exceed
4096 bytes. A branch for it would be error handling for an impossible
scenario, and a dead branch is something the next reader has to reason about.

ONCE-ONLY IS SIMPLER HERE THAN IN THE WIZARD, and should stay that way. The
wizard needed a served-once flag and a GET because it minted at one step and
displayed at another. The POST response below is the only serve, so there is
no GET, no flag, and nothing replayable. A reload before the operator copies
loses the value, and the honest remedy is to rotate again -- which is
idempotent by design and one click.
"""

import logging
import secrets as secrets_module

from fastapi import APIRouter, Depends, HTTPException, Request

from autoposter.api import setup_arr
from autoposter.api.auth import require_session
from autoposter.api.setup_checks import CHECK_SYSTEMS
from autoposter.config import secret_store
from autoposter.config.schema import Secrets, secret_sources
from autoposter.config.state import merge_secrets_file, state_dir
from autoposter.db.models import EventLog
from autoposter.db.models import Session as SessionModel

logger = logging.getLogger(__name__)

router = APIRouter()

WEBHOOK_SECRET_ENV = "AUTOPOSTER_WEBHOOK_SECRET"

# The two services this application receives webhooks from, in the order the
# panel lists them. `setup_arr.NAMES` is the server's own allowlist and this
# is a fixed loop over it -- there is no request field to pick a service, so
# there is nothing here to validate against it.
ARR_SERVICES = ("radarr", "sonarr")

# The variable NAME is what an operator must act on, and naming a
# variable is what `missing_hard_secret_names` and `Secrets.load` already
# establish as both safe and required to say. No value, no path, no host.
#
# A POLICY now, and the sentence says so, because the mechanism it used to
# describe is gone: the state file outranks the environment, so a rotation
# written here would WIN at the next boot rather than be undone by it. That is
# precisely why the refusal stays. The deployment's own manifest is what sets
# that variable, and a page that could quietly overrule it from a file the
# manifest does not mention would stop the manifest being the truth about this
# deployment without anyone having edited it.
ENV_CONFIGURED_REFUSAL = (
    "this deployment's webhook secret comes from AUTOPOSTER_WEBHOOK_SECRET in "
    "its environment, which is where its deployment sets it -- rotating it "
    "here would write a value that quietly overrules that one. Set a new "
    "value where AUTOPOSTER_WEBHOOK_SECRET is set and roll the deployment."
)
ROTATION_IN_PROGRESS = "a webhook secret rotation is already in progress."
ROTATION_RATE_LIMITED = "too many rotation attempts"
STATE_DIR_NOT_WRITABLE = "the state directory could not be written"
#: The store's half of the sentence above, for the arm that writes a row
#: instead of the file. The CLASS NAME travels with it and nothing else: a
#: database error's own text carries the DSN, and a key-file one names the
#: file, neither of which belongs in a response body.
STORE_REFUSED_THE_WRITE = "the new webhook secret could not be stored"

# The per-service sentences. `{system}` is `CHECK_SYSTEMS[svc].label` and the
# only thing that varies: no address, no *arr response text, no key.
NOT_CONFIGURED = (
    "{system} is not configured on this deployment -- no base_url, or no API "
    "key -- so nothing was re-registered there."
)
NO_PUBLIC_URL = (
    "public_url is empty in this deployment's configuration, so the callback "
    "address {system} would be given cannot be built. Set public_url in the "
    "General panel above and rotate again, or paste the value into "
    "{system}'s Webhook connection by hand."
)
REGISTRATION_ACCEPTED = "{system} accepted the new webhook secret ({action})."
CREDENTIAL_REFUSED = "{system} refused the credential."
REGISTRATION_REFUSED = "{system} would not accept the webhook registration ({failure})."


@router.post("/config/webhook-secret/rotate")
async def rotate_webhook_secret(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Mint a new webhook secret, persist it, make it live, and re-register.

    No request body: there is no field a caller may legitimately supply. The
    secret is minted here and the addresses come from `Config`, so a body-less
    route cannot be asked to send a secret anywhere -- the same property the
    wizard makes structural by refusing a submitted webhook secret outright.

    The response is the ONE serve of the new value.
    """
    app = request.app
    # Before anything else, and per client, the way `POST /api/login` is. This
    # route is session-gated, so reaching it already costs a credential; the
    # limiter is the second bound, on an action that writes a file and makes
    # two 10-second outbound calls.
    client = request.client.host if request.client else "unknown"
    if not app.state.rotation_rate_limiter.allow(client):
        raise HTTPException(status_code=429, detail=ROTATION_RATE_LIMITED)

    # Decide rotatability BEFORE touching the filesystem. On an env-configured
    # deployment AUTOPOSTER_STATE_DIR is typically unset and `state_dir()` is
    # /state, which does not exist -- so the order here is what keeps the
    # refusal, and not a 503 about a directory, the answer that shape gets.
    #
    # The question this guard asks is which source WINS for this name, and
    # `secret_sources` is the one function that answers it -- in the same
    # order `resolve_secret_values` resolves in, so the guard and the resolver
    # cannot drift apart.
    #
    # The names of the rows that DECRYPT -- `load_stored_secrets`' keys, in
    # the same expression `api/secrets_api.py`'s listing uses and taken in the
    # form that drops its values. `stored` has to mean one thing across this
    # service: on a lost volume the rows are all still there and none of them
    # answers, the resolver falls through to the layer beneath, and a guard
    # that read the row names alone would label this name `stored`, allow the
    # rotation, and write a file that outranks the manifest -- the one write
    # this refusal exists to stop. It costs one decrypt per row on an action
    # an operator takes by hand.
    #
    # `secret_sources` is asked with a live session rather than left to read
    # `boot`'s marker, because a secret cleared from the Settings page since
    # boot must stop being labelled `stored` the moment it is cleared, not at
    # the next restart.
    async with app.state.session_factory() as session:
        stored = sorted(await secret_store.load_stored_secrets(session))
    source = secret_sources(stored)[WEBHOOK_SECRET_ENV]
    if source == "environment":
        raise HTTPException(status_code=400, detail=ENV_CONFIGURED_REFUSAL)

    lock = app.state.secret_rotation_lock
    # No `await` between the test and the acquire, so this is atomic under
    # asyncio's cooperative scheduling -- the wizard's `state.registering`
    # idiom. Refused rather than queued: a second rotation during the first is
    # never what the operator meant, and holding the lock across two 10-second
    # *arr calls is the correct cost of that.
    if lock.locked():
        raise HTTPException(status_code=409, detail=ROTATION_IN_PROGRESS)
    async with lock:
        return await _rotate(request, source)


async def _rotate(request: Request, source: str) -> dict:
    """``source`` is the layer that ANSWERS this name, and it decides where
    the new value is written.

    The rotation has to land where the next boot will look, and that is the
    winning layer rather than the file: with a stored row above it, a value
    written to ``secrets.env`` is one the next start ignores, so both *arrs
    would go on signing with a value this service had forgotten -- served
    once, recoverable nowhere -- and every inbound webhook would fail
    verification silently. `environment` never reaches here; it is refused
    above, because there is no layer this process may write that the manifest
    would not outrank.
    """
    app = request.app
    minted = secrets_module.token_urlsafe(32)

    if source == "stored":
        try:
            async with app.state.session_factory() as session:
                await secret_store.store_secret(session, WEBHOOK_SECRET_ENV, minted)
                await session.commit()
        except Exception as exc:
            # The CLASS NAME, in the log and in the sentence, and nothing
            # else: the database's own text carries the DSN and the key
            # file's names the file. Nothing has changed yet at this point --
            # this is still the first step -- so the refusal leaves a
            # deployment whose webhook secret is exactly what it was.
            logger.error(
                "webhook secret rotation: the store refused the write (%s)",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail=f"{STORE_REFUSED_THE_WRITE} ({type(exc).__name__})",
            ) from None
    else:
        try:
            merge_secrets_file({WEBHOOK_SECRET_ENV: minted})
        except OSError as exc:
            # Duplicated from `api/setup.py`'s `_persist` with its reasoning:
            # the step name only in the log, and the sentence carries the
            # directory an operator sets, never a file name and never the
            # errno text.
            logger.error("webhook secret rotation: the state directory refused a write")
            raise HTTPException(
                status_code=503,
                detail=f"{STATE_DIR_NOT_WRITABLE}: {state_dir()} ({type(exc).__name__})",
            ) from None

    # Rebound, never mutated -- `config/live.py`'s `swap_config` is the
    # precedent and the argument. Reconstructed through the constructor rather
    # than `model_copy` so the object bound here went through the same
    # validation `Secrets.load` uses, which is what `_authorise` is entitled to
    # assume; it costs one dict round trip on a once-a-year action.
    held = app.state.secrets
    app.state.secrets = Secrets(**{**held.model_dump(), "webhook_secret": minted})

    registrations = await _reregister(app, minted)

    # The step and nothing else -- no service, no address, no outcome, and
    # never the value.
    logger.info("a webhook secret rotation was completed")

    async with app.state.session_factory() as session:
        row = EventLog(
            source="config",
            event_type="webhook_secret_rotated",
            # The ACTION word or null, per service: the same vocabulary the
            # response carries and the panel renders, which is what makes an
            # events row and a screenshot comparable. No value, no address, no
            # public_url, no key.
            payload={service: result["action"] for service, result in registrations.items()},
            outcome="rotated",
        )
        session.add(row)
        await session.commit()
        # The row's own timestamp, so the panel's "rotated at" and the
        # Dashboard's events list cannot disagree.
        await session.refresh(row)
        rotated_at = row.received_at

    return {
        "webhook_secret": minted,
        "rotated_at": rotated_at,
        "registrations": registrations,
    }


async def _reregister(app, secret: str) -> dict[str, dict]:
    """Re-register both *arrs with ``secret``, and report per service.

    The predicate is a non-empty `base_url` AND a non-empty API key, and NEVER
    `radarr.enabled`: that flag gates `arr_sync`, the job that registers
    unknown Plex items INTO Radarr, and has nothing to do with the inbound
    webhook. A deployment can receive Radarr webhooks all day with
    `enabled: false`, and reading the flag here would silently skip the
    re-registration on a correctly wired deployment and leave it 401ing.
    """
    config, secrets = app.state.config, app.state.secrets
    public_url = config.public_url
    results: dict[str, dict] = {}

    for service in ARR_SERVICES:
        label = CHECK_SYSTEMS[service].label
        base_url = getattr(config, service).base_url
        api_key = getattr(secrets, f"{service}_apikey")

        if not base_url or not api_key:
            results[service] = _result(None, NOT_CONFIGURED.format(system=label))
            continue
        if not public_url:
            results[service] = _result(None, NO_PUBLIC_URL.format(system=label))
            continue

        # `register` never raises and answers a marker or a class name, never
        # the *arr's own text -- a 400 from Sonarr echoes the submitted fields,
        # secret included. httpx's URL-bearing INFO line is filtered for the
        # length of the call, inherited from `register` itself. This call site
        # does not trust that contract, though: the state file is already
        # written and `app.state.secrets` already rebound by the time this
        # runs, so a genuine raise here must still land as this service's
        # fixed refusal -- never a 500 that undoes none of that and reports
        # neither the new value nor an audit row.
        try:
            action, failure = await setup_arr.register(
                service, base_url, api_key, public_url, secret
            )
        except Exception as exc:
            action, failure = None, type(exc).__name__

        if action is not None:
            results[service] = {
                "ok": True,
                "action": action,
                "detail": REGISTRATION_ACCEPTED.format(system=label, action=action),
            }
        elif failure == "refused":
            results[service] = _result(None, CREDENTIAL_REFUSED.format(system=label))
        else:
            results[service] = _result(
                None, REGISTRATION_REFUSED.format(system=label, failure=failure)
            )

    return results


def _result(action: str | None, detail: str) -> dict:
    return {"ok": action is not None, "action": action, "detail": detail}
