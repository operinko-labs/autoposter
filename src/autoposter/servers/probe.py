"""Does this server answer, and what does it carry? (spec section 5.)

The wizard owned both questions while it was the only thing that could ask
them. The Servers tab asks the same two on a running deployment, so the code
moves here and both applications import it: one probe, one library shape, one
set of three sentences. A second copy would be two definitions of "refused",
and a card that said "connected" about a server the wizard would have failed.

The BOUNDS are ``api/setup_checks.py``'s, unchanged, because they are the ones
that were argued for: the probe is a TABLE (a compiled-in path, method, header
and credential per system, never a caller-named URL), the whole call is capped
by ``asyncio.wait_for``, the body is streamed and capped, and the only thing
reported about a failure is an exception CLASS NAME -- never the server's own
text and never a URL.

What this module does NOT own is the rule about WHOSE credential may travel to
a caller-named address. That is a property of each calling surface -- the
wizard's typed-address rule, and the Servers tab's restatement of it -- and it
is enforced there, before the address and the value reach this module at all.
"""

from dataclasses import dataclass

import httpx

from autoposter.api import setup_checks
from autoposter.config.schema import _SERVER_SECRET_ENV

#: The media servers this service manages, Plex first.
SERVER_NAMES: tuple[str, ...] = ("plex", "jellyfin")

#: server name -> the environment NAME its credential is known by. The one
#: copy: ``api/setup.py`` imports this rather than keeping its own. Keyed by
#: the name the config document, the check table and /progress all use for the
#: server; ``config/schema._SERVER_SECRET_ENV`` keys the same two by the MODEL
#: FIELD instead, which is the one spelling no caller here wants -- so the
#: values are read from it rather than spelled a third time.
SERVER_CREDENTIAL: dict[str, str] = {
    "plex": _SERVER_SECRET_ENV["plex_token"],
    "jellyfin": _SERVER_SECRET_ENV["jellyfin_api_key"],
}

NOT_A_SERVER = "this service manages no media server by that name"

# The three sentences, moved from `api/setup.py` so both callers render one
# vocabulary. `{system}` is the check table's own label, never a caller's key.
ANSWERED = "{system} answered."
REFUSED = "{system} refused the credential."
UNREACHABLE = "{system} could not be reached ({failure})."


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    #: The server answered and rejected the credential (401/403).
    refused: bool
    #: An exception class name or a status marker, never a message.
    failure: str | None
    #: The server's own version when it volunteered one, else None. Read only
    #: on a probe that answered ok, from the fixed path each server states it
    #: on (``setup_checks.read_version``), and never allowed to change the
    #: three fields above it.
    version: str | None
    #: One of the three sentences above, rendered.
    detail: str


@dataclass(frozen=True)
class Library:
    """One library, in the ONE shape the tick-list renders.

    Plex answers ``key``/``title``/``type`` and Jellyfin ``ItemId``/``Name``/
    ``CollectionType``; both land here. ``kind`` rather than ``type``, because
    ``type`` is a builtin and this dataclass is read far more often than it is
    written. The server's own filesystem paths are never in it.
    """

    id: str
    name: str
    kind: str


def _known(name: str) -> None:
    if name not in SERVER_NAMES:
        raise ValueError(NOT_A_SERVER)


async def check_server(
    name: str,
    base_url: str,
    credential: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> ProbeResult:
    """One media server's probe, as a result the UI can render directly.

    ``setup_checks.run_check`` does the work and never raises for a network
    reason; this wraps its two booleans in the sentence each one means.

    The version is asked for SECOND and only of a server that answered, so the
    three fields the card renders a pill from are decided by the credentialed
    probe alone: a version this service could not read is a null field beside
    "connected", never a connection reported as broken.
    """
    _known(name)
    check = setup_checks.CHECK_SYSTEMS[name]
    credentials = {check.credential or "": credential}
    outcome = await setup_checks.run_check(
        name, base_url, credentials, transport=transport
    )
    if outcome.ok:
        return ProbeResult(
            ok=True,
            refused=False,
            failure=None,
            version=await setup_checks.read_version(
                name, base_url, credentials, transport=transport
            ),
            detail=ANSWERED.format(system=check.label),
        )
    if outcome.refused:
        return ProbeResult(
            ok=False,
            refused=True,
            failure=None,
            version=None,
            detail=REFUSED.format(system=check.label),
        )
    return ProbeResult(
        ok=False,
        refused=False,
        failure=outcome.failure,
        version=None,
        detail=UNREACHABLE.format(system=check.label, failure=outcome.failure),
    )


async def list_libraries(
    name: str,
    base_url: str,
    credential: str,
    *,
    client_identifier: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[Library]:
    """The server's own library list, live.

    ``client_identifier`` is Plex's deployment-stable string and is ignored by
    Jellyfin; a caller that has none passes ``None`` and one is not invented
    here, because plex.tv answers 400 without it and the caller is the only
    thing that knows which identifier this deployment uses.
    """
    _known(name)
    if name == "plex":
        from autoposter.api import setup_plex

        rows = await setup_plex.library_sections(
            base_url, credential, client_identifier or "", transport
        )
        return [
            Library(id=str(row["key"]), name=row["title"], kind=row["type"]) for row in rows
        ]

    from autoposter.api import setup_jellyfin

    rows = await setup_jellyfin.library_list(base_url, credential, transport)
    return [Library(id=str(row["id"]), name=row["name"], kind=row["type"]) for row in rows]
