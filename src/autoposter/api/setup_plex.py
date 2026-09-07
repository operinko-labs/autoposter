"""plex.tv's PIN sign-in, the owned-server listing, and one library read.

Four calls, and one of them is why this module exists rather than a couple of
inline requests: the ``X-Plex-Client-Identifier`` header must be the SAME
string on the mint, on the auth link the operator opens, and on every poll --
plex.tv 404s a poll whose identifier differs. The implementation probe measured
that on 2026-09-07: a poll of a freshly minted PIN under a second, random
identifier answered 404. The identifier is minted ONCE -- a random string,
staged in ``SetupState``, never persisted, and REUSED by every later mint --
because it is what plex.tv shows the operator as the DEVICE on their account:
one per sign-in attempt would leave a row of dead Autoposters there for every
abandoned try. What must not be inherited from an abandoned attempt is its PIN,
and that is ``plex_pin_id``'s job, not the identifier's: the pin id is what
binds a poll to a PIN and it is overwritten on every mint.

Row 213, and the documented exception (facts C6). The PIN CODE and the
``app.plex.tv`` auth URL ARE served -- they are minted by plex.tv, are public
by design, and there is no flow without showing them. That is a fourth
category beside "given to us", "held" and "minted by us", and it is written
here rather than left for a reader to discover as an apparent violation. The
``authToken`` is in none of those categories: the poll route answers a
BOOLEAN, and the value goes straight into ``SetupState.staged``.

``?strong=true`` on the mint, and it is a security choice rather than a
default: the probe measured a strong code at 25 characters against the four a
weak one carries. A four-character code is enumerable by anyone holding the
client identifier, and this deployment has no use for the one thing a weak code
buys -- typing it at plex.tv/link -- because the operator is already in the
browser the auth link opens in.

Nothing here logs a line of its own, and it does not leave the one line httpx
logs to a setting made elsewhere. httpx emits one INFO line per request with
the FULL url, and the POLL url carries the PIN id -- so every call below runs
inside ``setup_checks.no_httpx_request_log``, the local filter that module
wrote for exactly this shape. ``boot.main``'s process-wide clamp (boot.py:151)
stays the second line of defence, not the first: C6 is a property of this
module and should not depend on another file's level.
"""

import asyncio
import json

import httpx

from autoposter.api.setup_checks import no_httpx_request_log

PLEX_TV = "https://plex.tv"
AUTH_APP = "https://app.plex.tv/auth#?"
# What the operator sees named on plex.tv's authorised-devices page.
PRODUCT = "Autoposter"
PLEX_TIMEOUT_SECONDS = 10.0
# The timeout bounds TIME and not SIZE, and unlike the check probes -- which
# abandon the body unread -- these four calls have to READ theirs. So the read
# is bounded instead of unbounded: ten seconds of a local network is gigabytes
# into a pod with a memory limit, and the picked server's address is
# operator-supplied. A megabyte is far above any real answer here (plex.tv's
# resources list and a server's section list are both small objects) and far
# below a number that matters to the pod; a body over it fails to parse and is
# reported as a class name, like every other failure.
PLEX_BODY_LIMIT_BYTES = 1024 * 1024


async def _request_json(
    method: str,
    url: str,
    headers: dict[str, str],
    transport: httpx.BaseTransport | None,
):
    """Every outbound call in this module, and every bound on it, in one place.

    No redirect is followed (a redirect off plex.tv or off the picked server is
    not a place this token should go), the WHOLE call is bounded by
    ``asyncio.wait_for`` at ``PLEX_TIMEOUT_SECONDS``, at most
    ``PLEX_BODY_LIMIT_BYTES`` of the answer is read, and httpx's own request log
    is filtered for the length of it.

    ``asyncio.wait_for`` and not httpx's ``timeout=`` alone, which is
    ``setup_checks.run_check``'s idiom for the reason that module gives: httpx's
    timeout is PER OPERATION, so a server that emits one byte inside every
    window gets a fresh ten seconds for each and holds this handler until the
    size cap is reached -- hours rather than seconds. The cap is not a bound on
    time any more than the timeout is a bound on size; both are needed, and the
    reachable case is a library read against the address the operator supplied.
    """

    async def read() -> bytes:
        with no_httpx_request_log():
            async with httpx.AsyncClient(
                transport=transport, timeout=PLEX_TIMEOUT_SECONDS, follow_redirects=False
            ) as client:
                async with client.stream(method, url, headers=headers) as response:
                    response.raise_for_status()
                    head = bytearray()
                    async for chunk in response.aiter_bytes():
                        head += chunk
                        if len(head) >= PLEX_BODY_LIMIT_BYTES:
                            break
        return bytes(head[:PLEX_BODY_LIMIT_BYTES])

    return json.loads(await asyncio.wait_for(read(), timeout=PLEX_TIMEOUT_SECONDS))


def _headers(client_identifier: str) -> dict[str, str]:
    return {
        "X-Plex-Client-Identifier": client_identifier,
        "X-Plex-Product": PRODUCT,
        "Accept": "application/json",
    }


def auth_url(client_identifier: str, code: str) -> str:
    """The link the operator opens in a new tab.

    Opened by the BROWSER and never fetched by the server: this is a
    human-facing sign-in page, and a server that fetched it would be doing
    something neither Plex nor the operator asked for.
    """
    return f"{AUTH_APP}clientID={client_identifier}&code={code}&context[device][product]={PRODUCT}"


async def mint_pin(client_identifier: str, transport: httpx.BaseTransport | None = None) -> dict:
    """Ask plex.tv for a strong PIN. Answers id, code and expiry -- no token.

    ``expires_at`` is plex.tv's own answer and is what the client polls
    against, so nothing here hard-codes a duration. The measured body also
    carries ``expiresIn``; ``expiresAt`` is read instead because it is an
    instant rather than a budget, and the seconds the route serves are computed
    from it at the moment the route answers rather than at the moment plex.tv
    did.
    """
    body = await _request_json(
        "POST", f"{PLEX_TV}/api/v2/pins?strong=true", _headers(client_identifier), transport
    )
    return {"id": body["id"], "code": body["code"], "expires_at": body["expiresAt"]}


async def poll_pin(
    pin_id: int, client_identifier: str, transport: httpx.BaseTransport | None = None
) -> str | None:
    """The account token once the operator has approved, else ``None``.

    The same ``X-Plex-Client-Identifier`` as the mint, or plex.tv answers 404.
    """
    body = await _request_json(
        "GET", f"{PLEX_TV}/api/v2/pins/{pin_id}", _headers(client_identifier), transport
    )
    return body.get("authToken") or None


async def owned_servers(
    account_token: str, transport: httpx.BaseTransport | None = None
) -> list[dict]:
    """The account's OWN servers, with no token in any entry.

    ``owned == True`` is applied here rather than in the route or the page,
    because it is a scope decision and not a default: a server shared TO this
    account carries its own per-resource ``accessToken`` (probe 1 measured four
    such, all different), and using one would be the exchange path the ruling
    excludes.

    Connections are ordered ``local`` first. Every connection probe 1 saw was
    https with exactly one ``local: true`` entry on 32400, which is the address
    a pod inside the same network should be given first.
    """
    resources = await _request_json(
        "GET",
        f"{PLEX_TV}/api/v2/resources?includeHttps=1&includeRelay=0",
        {"X-Plex-Token": account_token, "Accept": "application/json"},
        transport,
    )

    servers = []
    for entry in resources:
        if not entry.get("owned") or "server" not in (entry.get("provides") or ""):
            continue
        connections = sorted(
            (
                {
                    "uri": connection.get("uri", ""),
                    "local": bool(connection.get("local")),
                    "protocol": connection.get("protocol", ""),
                    "port": connection.get("port", 0),
                }
                for connection in entry.get("connections") or []
            ),
            key=lambda connection: not connection["local"],
        )
        # Built field by field rather than by deleting `accessToken` from a
        # copy: a listing that omits the token by construction cannot grow one
        # when plex.tv adds a field.
        servers.append(
            {
                "client_identifier": entry.get("clientIdentifier", ""),
                "name": entry.get("name", ""),
                "product": entry.get("product", ""),
                "platform": entry.get("platform", ""),
                "connections": connections,
            }
        )
    return servers


async def library_sections(
    base_url: str, token: str, transport: httpx.BaseTransport | None = None
) -> list[dict]:
    """``key``, ``title`` and ``type`` per library, and nothing else.

    The same read ``setup_checks``' Plex probe makes, which is why that probe
    uses ``/library/sections`` rather than the unauthenticated ``/identity``:
    one call proves the address AND the token AND produces the tick-list.
    """
    body = await _request_json(
        "GET",
        f"{base_url}/library/sections",
        {"X-Plex-Token": token, "Accept": "application/json"},
        transport,
    )
    container = body.get("MediaContainer") or {}

    return [
        {
            "key": str(directory.get("key", "")),
            "title": directory.get("title", ""),
            "type": directory.get("type", ""),
        }
        for directory in container.get("Directory") or []
    ]
