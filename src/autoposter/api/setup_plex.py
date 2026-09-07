"""plex.tv's PIN sign-in, the owned-server listing, and one library read.

Four calls, and one of them is why this module exists rather than a couple of
inline requests: the ``X-Plex-Client-Identifier`` header must be the SAME
string on the mint, on the auth link the operator opens, and on every poll --
plex.tv 404s a poll whose identifier differs. The implementation probe measured
that on 2026-09-07: a poll of a freshly minted PIN under a second, random
identifier answered 404. The identifier is minted per wizard session, staged in
``SetupState`` and never persisted: it identifies this browser's sign-in
attempt, not this deployment.

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

Nothing here logs. ``boot.main`` already clamps ``httpx`` to WARNING
(boot.py:151) because httpx emits one INFO line per request carrying the full
URL -- and the poll URL carries the PIN id.
"""

import httpx

PLEX_TV = "https://plex.tv"
AUTH_APP = "https://app.plex.tv/auth#?"
# What the operator sees named on plex.tv's authorised-devices page.
PRODUCT = "Autoposter"
PLEX_TIMEOUT_SECONDS = 10.0


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
    async with httpx.AsyncClient(
        transport=transport, timeout=PLEX_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        response = await client.post(
            f"{PLEX_TV}/api/v2/pins?strong=true", headers=_headers(client_identifier)
        )
        response.raise_for_status()
        body = response.json()
    return {"id": body["id"], "code": body["code"], "expires_at": body["expiresAt"]}


async def poll_pin(
    pin_id: int, client_identifier: str, transport: httpx.BaseTransport | None = None
) -> str | None:
    """The account token once the operator has approved, else ``None``.

    The same ``X-Plex-Client-Identifier`` as the mint, or plex.tv answers 404.
    """
    async with httpx.AsyncClient(
        transport=transport, timeout=PLEX_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        response = await client.get(
            f"{PLEX_TV}/api/v2/pins/{pin_id}", headers=_headers(client_identifier)
        )
        response.raise_for_status()
        return response.json().get("authToken") or None


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
    async with httpx.AsyncClient(
        transport=transport, timeout=PLEX_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        response = await client.get(
            f"{PLEX_TV}/api/v2/resources?includeHttps=1&includeRelay=0",
            headers={"X-Plex-Token": account_token, "Accept": "application/json"},
        )
        response.raise_for_status()
        resources = response.json()

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
    async with httpx.AsyncClient(
        transport=transport, timeout=PLEX_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        response = await client.get(
            f"{base_url}/library/sections",
            headers={"X-Plex-Token": token, "Accept": "application/json"},
        )
        response.raise_for_status()
        container = response.json().get("MediaContainer") or {}

    return [
        {
            "key": str(directory.get("key", "")),
            "title": directory.get("title", ""),
            "type": directory.get("type", ""),
        }
        for directory in container.get("Directory") or []
    ]
