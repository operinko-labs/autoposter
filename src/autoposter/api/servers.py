"""The Servers tab's backend (spec §5): list them, probe one, read its
libraries, save or remove one, set or clear its credential, catch up, and retry
what failed.

The listing and the two live reads are here because the tab asks on a RUNNING
deployment the same two questions the wizard asks before there is one -- does
this address answer with this credential, and what libraries does it carry --
and the answers come from ``servers/probe.py``, which both surfaces import.
What is not shared is the auth: these routes sit behind the application's
ordinary session, because a running deployment has to be able to add a server
and the wizard only runs before one is configured.

**The rule this module inherits from the wizard and restates.** A credential
this deployment HOLDS never travels to an address a REQUEST named. A body with
a ``url`` must carry its own ``credential_value``; a body with neither uses the
stored address and the stored credential together. ``api/setup_checks.py``'s
header explains why at length, and the reasoning is unchanged by the caller
being authenticated: a session is a credential for THIS service, not permission
to have it post another service's credential to an arbitrary host.

TWO GENERATIONS OF CONFIG, and which one answers which question. A save
hot-swaps ``app.state.config`` without a restart (``config/live.swap_config``),
so "the address this deployment is configured with" and "the address this
deployment already sends this credential to" stop being the same string the
moment an operator edits the field. The LISTING reads the swapped generation,
because the card must show what is saved. The PROBE reads
``app.state.booted_config`` -- the generation the media-server clients and the
liveness pollers were actually built from -- because that, and only that, is
where the held credential already goes. Without the split, a saved address plus
an empty check body is a two-request way to post this deployment's Plex token
to any host, with no restart and with the token otherwise unreadable through
the API. An operator who has just typed a new address is served by the typed
branch, which is what the typed branch is for, and the restart is what moves
the booted generation.

Both addresses go through ``setup.py``'s guard before anything is sent, the
stored one included: ``PlexConfig.url`` and ``JellyfinConfig.url`` are bare
``str`` fields with no validator, so a document can carry userinfo, a query
string or a non-http scheme that the typed path would refuse -- and the guard
also strips the trailing slash the fixed paths are appended to.

WHAT THE GUARD IS NOT. It is an address-SHAPE guard, not a destination guard:
``api/setup_checks.py``'s bound is that the probe is a compiled-in table of
paths and headers, bounded, non-following and class-name-only in what it
reports -- never that the HOST is somewhere sensible. A session holder can
therefore still learn, one typed address at a time, whether a port inside the
pod's network answers, and unlike the wizard's rate-limited setup token nothing
here meters that. It is written down rather than closed because the caller is
an authenticated administrator of this service and the answer is one boolean
per request; it is not written down as safe.

Everything served here is a name, an address the operator already typed, a
boolean, a timestamp or one of the fixed sentences below. No credential, ever.

NEITHER WRITE OWNS A WRITE PATH. A card's save and a card's removal are edits
to the configuration document, so they go through ``api/routes.py``'s
``_validated_generation`` and ``_persist_and_swap`` -- the two functions a
settings save goes through -- and get that path's validation, pre-write
snapshot, drop cap, stale-revision 409, audit event and restart list by
construction rather than by a second implementation that would drift from it.
The credential routes delegate the same way, to ``api/secrets_api.py``, so the
value rules, the encryption and the rebind of ``app.state.secrets`` stay in the
one module whose subject they are. What is left here is the translation: a
server NAME into a document section, a switch table, and the two refusals that
are about a button rather than about a document.

The catch-up and retry-failed buttons own no logic of their own --
``catchup.py`` does -- and their whole job is turning a ``CatchUpRefused`` into
a 409 whose ``detail`` is the sentence the button shows, so the operator reads
one wording whether the refusal came from the API, a log line or the runs list.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from autoposter.api import setup_checks
from autoposter.api.auth import require_session
from autoposter.api.secrets_api import (
    SecretBody, _FIELD_FOR_NAME, clear_stored_secret, set_secret,
)
from autoposter.api.setup import PUBLIC_URL_NOT_AN_ADDRESS, _require_http_url
from autoposter.catchup import (
    CatchUpRefused, cancel_catch_up, catch_up_progress, retry_failed, start_catch_up,
)
from autoposter.config import secret_store
from autoposter.config.overrides import STORE_FORMAT, load_store
from autoposter.config.schema import secret_sources
from autoposter.db.models import Session as SessionModel
from autoposter.servers import probe

logger = logging.getLogger(__name__)

router = APIRouter()

#: The probe's own refusal for a name this service manages no server by, so a
#: card and the wizard answer one sentence about one typo.
NOT_A_SERVER = probe.NOT_A_SERVER
#: The BOOTED generation's address is the one a probe may reuse the held
#: credential against, so an address that is saved but not yet restarted onto
#: is not an address this deployment has -- and the sentence says so, rather
#: than saying "no stored address" to an operator looking straight at the one
#: they just saved on the card.
NEEDS_AN_ADDRESS = (
    "this deployment has no address it booted with for this server, so the "
    "address to check must be sent with the request"
)
NEEDS_A_CREDENTIAL = (
    "this server has no credential; set one on its card before checking it"
)
TYPED_ADDRESS_NEEDS_A_TYPED_CREDENTIAL = (
    "an address supplied with this request must come with the credential to "
    "use against it; this service does not send a stored credential to an "
    "address a request named"
)


def _transport() -> httpx.BaseTransport | None:
    """The outbound transport. ``None`` in production; the suite patches this.

    A seam rather than a parameter, because the route bodies take a request
    body and nothing else, and threading a transport through three of them
    would put a test concept in the public shape.
    """
    return None


class ProbeBody(BaseModel):
    """What a card sends when it checks a server or reads its libraries.

    Both fields optional and both meaning the same thing when absent: "use what
    this deployment holds". Sending an address without a value is the shape the
    typed-address rule refuses.
    """

    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    credential_value: str | None = None


def _known(name: str) -> None:
    if name not in probe.SERVER_NAMES:
        # The NAME is a caller-chosen string, so the sentence is fixed.
        raise HTTPException(status_code=404, detail=NOT_A_SERVER)


def _block(config, name: str):
    """This server's section of ``config``, or ``None``.

    The generation is the CALLER's choice and the header says why there are
    two of them. Neither is the overrides row: a merged generation is what the
    rest of the service reads, and the row is only half of one.
    ``SERVER_NAMES`` are the section names, which is why the lookup is an
    attribute of the same spelling.
    """
    return getattr(config, name, None)


def _stored_credential(request: Request, name: str) -> str:
    """The credential this deployment holds for ``name``, resolved.

    Read off the running ``Secrets`` rather than the store alone, so a value
    the environment supplies is as usable as a stored one -- which layer it
    came from is the listing's business, not the probe's.
    """
    field = _FIELD_FOR_NAME[probe.SERVER_CREDENTIAL[name]]
    return getattr(request.app.state.secrets, field, "") or ""


def _resolve_target(request: Request, name: str, body: ProbeBody) -> tuple[str, str]:
    """The address and the credential this probe may use, or a refusal.

    The whole of the typed-address rule, in one place, so the check route and
    the libraries route cannot enforce different halves of it.

    The stored address is the BOOTED generation's, for the reason the header
    gives, and it goes through the same guard a typed one does: a document is
    not a validated address, and the refusal names the field rather than the
    value whichever of the two it came from.
    """
    if body.url:
        if not body.credential_value:
            raise HTTPException(
                status_code=400, detail=TYPED_ADDRESS_NEEDS_A_TYPED_CREDENTIAL
            )
        return (
            _require_http_url(body.url, PUBLIC_URL_NOT_AN_ADDRESS),
            body.credential_value,
        )
    block = _block(request.app.state.booted_config, name)
    url = getattr(block, "url", "") or ""
    if not url:
        # An unconfigured server: there is nothing stored to check, and the
        # card's first save plus a restart is what gives it an address.
        raise HTTPException(status_code=400, detail=NEEDS_AN_ADDRESS)
    credential = body.credential_value or _stored_credential(request, name)
    if not credential:
        raise HTTPException(status_code=400, detail=NEEDS_A_CREDENTIAL)
    return _require_http_url(url, PUBLIC_URL_NOT_AN_ADDRESS), credential


def _health(request: Request, name: str) -> dict:
    """The liveness poller's last answer for ``name``, or all nulls.

    A test application and a replica that has not run its lifespan both have no
    poller, and answering nulls is what lets the card render "not checked yet"
    instead of claiming the server is down.

    ``detail`` is ``last_error``, which both pollers keep deliberately
    servable: a status marker or an exception CLASS name, never the exception's
    own text, because an httpx error's ``str()`` embeds the request URL.

    ``checked_at`` is ``last_success``, and only while the poller is HEALTHY --
    which is the one state in which the last success IS the last check. Neither
    poller records when it last LOOKED, so during an outage the honest answer
    is null: a timestamp labelled "checked" that is actually minutes older than
    the check it describes would have the card report the wrong minute for the
    wrong event.
    """
    poller = (getattr(request.app.state, "server_health", None) or {}).get(name)
    if poller is None:
        return {"ok": None, "detail": None, "checked_at": None}
    ok = getattr(poller, "healthy", None)
    last_success = getattr(poller, "last_success", None)
    return {
        "ok": ok,
        "detail": getattr(poller, "last_error", None),
        "checked_at": last_success.isoformat() if ok and last_success else None,
    }


@router.get("/servers")
async def list_servers(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Every server the schema knows, configured or not (spec §5).

    The unconfigured ones are in the list because the tab's "Add a server" row
    is built from exactly this: the servers this deployment has NOT configured.
    A second endpoint for that would be two answers to one question, and
    ``configured`` is what tells the two rows apart.

    ``credential_source`` is a LABEL and never a value -- ``api/secrets_api.py``
    owns that rule and this is the same map it serves, asked with the names of
    the rows that DECRYPT rather than the rows that merely exist, in the same
    expression that discards their values.

    ``restart_pending`` is the one place the card sees BOTH generations at
    once: the saved address differs from the one this process booted with, so
    the clients, the liveness poller and the scheduler's server factory are
    still pointed at the old one and the check button still probes it. A save
    swaps the generation without a restart, which is why this is a fact worth
    serving rather than an impossibility -- and the save's own
    ``restart_required`` says the same thing once, at the moment of the save,
    while this says it for as long as it stays true and to every admin who
    opens the tab.
    """
    async with request.app.state.session_factory() as session:
        readable = sorted(await secret_store.load_stored_secrets(session))
    sources = secret_sources(readable)
    servers = []
    for name in probe.SERVER_NAMES:
        # The SWAPPED generation: the card shows what is saved, which is the
        # one question a save answers immediately.
        block = _block(request.app.state.config, name)
        url = getattr(block, "url", "") or ""
        booted = _block(request.app.state.booted_config, name)
        servers.append(
            {
                "name": name,
                "configured": bool(url),
                "url": url or None,
                "excluded_libraries": list(getattr(block, "excluded_libraries", []) or []),
                "credential_source": sources[probe.SERVER_CREDENTIAL[name]],
                "restart_pending": url != (getattr(booted, "url", "") or ""),
                "health": _health(request, name),
            }
        )
    return {"servers": servers}


@router.post("/servers/{name}/check")
async def check(
    name: str,
    body: ProbeBody,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Does this address answer with this credential? One of three sentences."""
    _known(name)
    url, credential = _resolve_target(request, name, body)
    result = await probe.check_server(name, url, credential, transport=_transport())
    # The SERVER name only -- api/setup_checks.py's rule: no address, no value,
    # and never the server's own text.
    logger.info("a media server connection check was run (%s)", name)
    return {
        "ok": result.ok,
        "refused": result.refused,
        "failure": result.failure,
        "detail": result.detail,
    }


@router.post("/servers/{name}/libraries")
async def libraries(
    name: str,
    body: ProbeBody,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """The server's library list, read live, for the card's tick-list.

    ``client_identifier`` is left to the probe's default: it is plex.tv's
    requirement, and this call is to the operator's own server, which serves
    ``/library/sections`` without one. There is no deployment-stable identifier
    outside the wizard's own in-memory state to pass instead, and inventing a
    fresh one per request would be a new device on the operator's account each
    time the tab is opened.
    """
    _known(name)
    label = setup_checks.CHECK_SYSTEMS[name].label
    url, credential = _resolve_target(request, name, body)
    try:
        found = await probe.list_libraries(
            name, url, credential, transport=_transport()
        )
    except httpx.HTTPStatusError as exc:
        # A status the read refused to work with, classified the way the check
        # route classifies the same status, so "Check connection" and "Reload
        # libraries" cannot tell an operator two different things about one
        # server: 401/403 is a credential the server rejected, and any other
        # status is the number and never the service's text.
        status = exc.response.status_code
        raise HTTPException(
            status_code=502,
            detail=(
                probe.REFUSED.format(system=label)
                if status in setup_checks.REFUSING_STATUSES
                else probe.UNREACHABLE.format(system=label, failure=f"HTTPStatus{status}")
            ),
        ) from None
    except Exception as exc:
        # The probe answers a library list or raises; the check route's own
        # vocabulary is what says so, with the exception CLASS and never its
        # text or the address it names.
        raise HTTPException(
            status_code=502,
            detail=probe.UNREACHABLE.format(
                system=label, failure=type(exc).__name__
            ),
        ) from None
    return {
        "libraries": [
            {"id": row.id, "name": row.name, "kind": row.kind} for row in found
        ]
    }


LAST_SERVER = (
    "this is the only configured media server; configure another one before "
    "removing it"
)
NOT_THIS_SERVERS_SWITCH = "that setting does not belong to this server"
#: A store that still holds a DELTA over the mounted file. A save is fine on
#: one -- the delta arm merges, which is exactly what a settings save does --
#: but a removal is not: dropping a key from a delta stops OVERRIDING the
#: file's block, it does not remove it, so the card would report a removal that
#: changed nothing. The next start converts the store (``config/overrides.py``'s
#: ``migrate_delta_to_document``), and then the removal means what it says.
DELTA_STORE_CANNOT_REMOVE = (
    "this deployment still stores its settings as changes to the mounted "
    "configuration file, and removing a server from those would leave the "
    "file's own block in force; restart this deployment, which converts the "
    "stored settings into a whole document, then remove the server"
)

#: The switches a card may set, per server (spec §5). Compiled in, so a body
#: can never reach a config path this table does not name.
SERVER_SWITCHES: dict[str, tuple[str, ...]] = {
    "plex": ("badges.upload_to_plex", "operations.write_to_plex"),
    "jellyfin": (
        "badges.upload_to_jellyfin",
        "operations.write_to_jellyfin",
        "jellyfin.replace_thumb_with_backdrop",
    ),
}


class ServerBody(BaseModel):
    """One card's save: address, exclusions and that server's switches.

    A form, not a patch: a card saves as a unit because a server IS a unit --
    address, credential and exclusions belong together (spec §5). The revision
    and the confirm flag are the settings page's, unchanged, because this write
    goes through the settings page's own write path.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    excluded_libraries: list[str] = []
    switches: dict[str, bool] = {}
    expected_revision: str | None = None
    confirm: bool = False


class ServerRemovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str | None = None
    confirm: bool = False


def _config_write():
    """The settings page's own two write functions, imported on use.

    ``api/routes.py`` collects every sub-router in this package, this one
    included, so a module-scope import here would close a cycle that neither
    file can carry -- the same shape, and the same answer,
    ``config/overrides.py`` uses for ``capture_snapshot``. Deferring it is what
    lets a server write go through the settings page's write path rather than
    around it, which is the whole point of these two routes.
    """
    from autoposter.api.routes import _persist_and_swap, _validated_generation

    return _validated_generation, _persist_and_swap


def _stored_block(document: dict, name: str) -> dict:
    """This server's section of a stored DOCUMENT, or ``{}``.

    Not ``_block``: that reads a built ``Config`` and answers a model. What a
    save edits is the document, where an absent section is an absent key and
    the keys it does carry are the operator's own -- ``library_map`` and
    ``liveness_interval_seconds`` among them, which no card sends and which a
    save must therefore carry through rather than default away.
    """
    block = document.get(name)
    return dict(block) if isinstance(block, dict) else {}


def _with_path(document: dict, path: str, value: object) -> dict:
    """``document`` with one dotted path set, copied rather than mutated.

    Copied because the document in hand was read from the store and is about to
    be handed to a validator that may refuse it: nothing may be left changed on
    the way to a 422.
    """
    head, _, rest = path.partition(".")
    if not rest:
        return {**document, head: value}
    child = document.get(head)
    return {
        **document,
        head: _with_path(child if isinstance(child, dict) else {}, rest, value),
    }


async def _stored(request: Request) -> tuple[dict, bool]:
    """The stored document and whether it is a whole one.

    The document a server write edits is the STORE's, never the running
    generation's dump: the running generation is the mounted file plus the
    store on a delta-era deployment, and writing its dump back would promote
    the file's every value into overrides in passing.
    """
    async with request.app.state.session_factory() as session:
        document, meta = await load_store(session)
    return document, meta.get("format") == STORE_FORMAT


def _configured(document: dict, config) -> list[str]:
    """The servers this deployment has an address for.

    Both sources, because either one alone is wrong on one of the two store
    shapes: the document alone misses a server the mounted file configures,
    and the running config alone misses nothing but is the merged generation,
    which is the right answer here precisely because the refusal below is about
    what this deployment HAS rather than about what its store says.
    """
    return [
        name
        for name in probe.SERVER_NAMES
        if _stored_block(document, name).get("url")
        or (getattr(_block(config, name), "url", "") or "")
    ]


@router.put("/servers/{name}")
async def save_server(
    name: str,
    body: ServerBody,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Write this server's block into the stored document (spec §5).

    Through ``_validated_generation`` and ``_persist_and_swap`` -- the settings
    page's own two functions -- and not around them. That is what gives this
    route the pre-write snapshot, the drop cap, the stale-revision 409, the
    audit event and the restart-list update without a second implementation of
    any of them; and it is what makes ``PUT /api/servers/jellyfin`` on a
    Plex-only deployment add ``jellyfin`` to the restart list, because the
    section is frozen and ``_restart_required`` already knows it.

    The candidate is built from the STORED document, whatever shape it is in,
    and handed over unchanged in that respect: a whole document is validated
    whole, and a delta-era one is merged over the mounted file exactly as a
    settings save of the same deployment would be. Neither arm is this route's
    to choose -- ``_validated_generation`` reads the row's format and chooses,
    which is the only way this save and the next boot can agree.
    """
    _known(name)
    unknown = sorted(set(body.switches) - set(SERVER_SWITCHES[name]))
    if unknown:
        # The path, not the value: a body naming another server's switch is a
        # page bug or a hand-written request, and either way the answer is
        # which key was refused.
        raise HTTPException(
            status_code=422,
            detail=[
                {"path": path, "message": NOT_THIS_SERVERS_SWITCH} for path in unknown
            ],
        )
    url = _require_http_url(body.url, PUBLIC_URL_NOT_AN_ADDRESS)

    document, _whole = await _stored(request)
    block = {
        **_stored_block(document, name),
        "url": url,
        "excluded_libraries": list(body.excluded_libraries),
    }
    # The block first and the switches over it, because one of the switches
    # LIVES in the block (`jellyfin.replace_thumb_with_backdrop`) and the card
    # that sent it must win over the section it was carried through in.
    candidate = {**document, name: block}
    for path, value in body.switches.items():
        candidate = _with_path(candidate, path, value)

    validated_generation, persist_and_swap = _config_write()
    validated, after, whole = await validated_generation(request, candidate)
    result = await persist_and_swap(
        request,
        validated,
        after,
        whole_document=whole,
        expected_revision=body.expected_revision,
        confirm=body.confirm,
    )
    # The server NAME and the action -- never the address, which is the one
    # thing an operator just typed and the one thing this module's header
    # promises no log line carries.
    logger.info("a media server's configuration was saved (%s)", name)
    return result


@router.delete("/servers/{name}")
async def remove_server(
    name: str,
    request: Request,
    body: ServerRemovalBody | None = None,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Drop this server's block and clear its stored credential (spec §5).

    Refused when it would leave no server configured -- and refused HERE rather
    than by ``build_config``'s own ``_at_least_one_media_server``, because that
    validator's message is about a document and this refusal is about a button.
    The document validator still stands behind it; this is the sentence an
    operator reads.

    The block goes, and with it everything that lived in it -- for Jellyfin
    that is ``replace_thumb_with_backdrop`` and ``library_map``, which are
    settings ABOUT a server this deployment no longer has. The two switches
    that live elsewhere are turned off by name, because nothing else would:
    ``badges.upload_to_<name>`` and ``operations.write_to_<name>`` would
    otherwise stay true and describe uploads to a server that is gone.

    The credential is cleared AFTER the document write commits. The other order
    leaves a window where the block still names a server whose credential is
    gone, which ``missing_server_setup`` reads as "not configured" -- a boot in
    that window would serve the wizard.
    """
    _known(name)
    body = body or ServerRemovalBody()
    document, whole_store = await _stored(request)
    if not whole_store:
        raise HTTPException(status_code=409, detail=DELTA_STORE_CANNOT_REMOVE)
    if _configured(document, request.app.state.config) == [name]:
        raise HTTPException(status_code=409, detail=LAST_SERVER)

    candidate = {key: value for key, value in document.items() if key != name}
    for path in (f"badges.upload_to_{name}", f"operations.write_to_{name}"):
        candidate = _with_path(candidate, path, False)

    validated_generation, persist_and_swap = _config_write()
    validated, after, whole = await validated_generation(request, candidate)
    result = await persist_and_swap(
        request,
        validated,
        after,
        whole_document=whole,
        expected_revision=body.expected_revision,
        confirm=body.confirm,
    )
    await clear_stored_secret(probe.SERVER_CREDENTIAL[name], request, None)
    logger.info("a media server was removed from the configuration (%s)", name)
    return result


@router.put("/servers/{name}/credential")
async def set_server_credential(
    name: str,
    body: SecretBody,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Store this server's credential. Delegated, never re-implemented.

    ``api/secrets_api.py`` owns the value rules, the key-file failure, the
    encryption and the rebind of ``app.state.secrets``; this route owns the
    translation from a server NAME to the environment name that credential is
    known by. Re-implementing any of it here would be a second place for a
    value to leak from, on a route whose whole subject is a credential.

    The delegate's own refusals come back untouched -- an unstorable value is
    its 422, in its words -- because two sentences about one rule is how a page
    ends up telling an operator something the store does not believe.
    """
    _known(name)
    answer = await set_secret(probe.SERVER_CREDENTIAL[name], body, request, None)
    return {"name": name, "credential_source": answer["source"]}


@router.delete("/servers/{name}/credential")
async def clear_server_credential(
    name: str, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Clear the stored credential; the next source down takes over.

    ``credential_source`` is the delegate's ``source``, which is the layer that
    now supplies the value rather than a fixed word: clearing a stored token on
    a deployment whose environment also sets one answers ``environment``, and
    the card must say so instead of claiming the server has no credential.
    """
    _known(name)
    answer = await clear_stored_secret(probe.SERVER_CREDENTIAL[name], request, None)
    return {"name": name, "credential_source": answer["source"]}


class CatchUpBody(BaseModel):
    """How often this run's backlog is drained.

    ``None`` -- and an omitted body -- means the scheduler's own
    pending-deliveries cadence. The floor is applied in ``start_catch_up``,
    not here: one place decides what a cadence may be.
    """

    cadence_seconds: int | None = None


@router.post("/servers/{name}/catch-up")
async def start(
    name: str, request: Request, body: CatchUpBody | None = None,
    _: SessionModel = Depends(require_session),
) -> dict:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            run_id = await start_catch_up(
                session, request.app.state.servers, request.app.state.config, name,
                health=getattr(request.app.state, "server_health", {}),
                cadence_seconds=(body.cadence_seconds if body is not None else None),
            )
        except CatchUpRefused as exc:
            # 409, not 400: nothing about the request is malformed -- the
            # deployment is in a state that has no room for this run yet.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await session.commit()
        progress = await catch_up_progress(session, name)
    return {
        "run_id": run_id, "server": name, "cadence_seconds": progress["cadence_seconds"],
    }


@router.get("/servers/{name}/catch-up")
async def progress(
    name: str, request: Request, _: SessionModel = Depends(require_session),
) -> dict:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        found = await catch_up_progress(session, name)
    # An explicit envelope rather than a bare null, so the page can tell "no
    # run yet" from a transport failure without reading the status code.
    return found if found is not None else {"run": None}


@router.delete("/servers/{name}/catch-up")
async def cancel(
    name: str, request: Request, _: SessionModel = Depends(require_session),
) -> dict:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            outcome = await cancel_catch_up(session, name)
        except CatchUpRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await session.commit()
    return outcome


@router.post("/servers/{name}/retry-failed")
async def retry(
    name: str, request: Request, _: SessionModel = Depends(require_session),
) -> dict:
    """Re-arm this server's failed rows without a full catch-up (spec §5).

    Never refused: re-arming nothing is a legitimate answer, and the counts
    say so.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        outcome = await retry_failed(session, name)
        await session.commit()
    return outcome
