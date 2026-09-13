"""One Jellyfin read, for the wizard's library tick-list.

``setup_plex`` with four calls has a module; this has one, and it is here for
the same two reasons that one is: the call is made against an address the
OPERATOR supplied, so it carries this wizard's bounds rather than a client's
defaults, and its outbound request must not reach a log record -- httpx emits
one INFO line per request with the whole url on it.

The HEADER is ``jellyfin/client.JellyfinApi``'s and not this module's: the
``Authorization: MediaBrowser Token="..."`` value has exactly one spelling in
this service, and a second copy of it here would be a header whose typo is
indistinguishable from a wrong API key. The REQUEST is this module's, because
``JellyfinApi`` reads a whole body into memory the way every client does --
buffering IS the unboundedness -- and the whole point of this module is that
this one call does not: it streams, and stops at ``JELLYFIN_BODY_LIMIT_BYTES``.

What this module owns besides the bounds is the SHAPE it answers with --
``id``, ``name`` and ``type`` per library and nothing else, built field by
field so a folder entry that grows a field on some future Jellyfin cannot grow
one in a response this wizard serves. ``Locations``, in particular, is a list
of the server's own filesystem paths and stays out.

``GET /Library/VirtualFolders`` -> ``ItemId``/``Name``/``CollectionType``:
docs/reference/2026-09-jellyfin-openapi-12.md.
"""

import asyncio
import json

import httpx

from autoposter.api.setup_checks import SETUP_VERSION, no_httpx_request_log

# setup_plex's value and its argument: the bound is on the WHOLE call rather
# than per operation, because httpx's own timeout is per read and a server that
# emits one byte inside every window holds the handler for hours.
JELLYFIN_TIMEOUT_SECONDS = 10.0
# setup_plex's other bound, and its argument too. The timeout bounds TIME and
# not SIZE: ten seconds of a local network is gigabytes into a pod with a
# memory limit, and this body comes from an address a caller named. A megabyte
# is far above any real virtual-folder listing and far below a number that
# matters to the pod; a body over it fails to parse and is reported as a class
# name, like every other failure here.
JELLYFIN_BODY_LIMIT_BYTES = 1024 * 1024


async def library_list(
    base_url: str, api_key: str, transport: httpx.BaseTransport | None = None
) -> list[dict]:
    """The server's libraries, as ``{id, name, type}`` -- for the tick-list.

    ``transport`` is the test seam and nothing else; production passes ``None``.
    """
    # Imported here and not at module scope for api/setup_checks.py's reason:
    # jellyfin/client.py pulls the index and the writer in behind it, and a
    # setup process that never reaches this route should not pay for them.
    from autoposter.jellyfin.client import JellyfinApi

    # Constructed for its two strings and not to make the request with: the
    # header, and the base address with the trailing slash already stripped --
    # both spellings borrowed, so a change to either moves this call with it.
    api = JellyfinApi(None, base_url, api_key, SETUP_VERSION)
    url = f"{api.base_url}/Library/VirtualFolders"

    async def read() -> bytes:
        with no_httpx_request_log():
            async with httpx.AsyncClient(
                transport=transport,
                timeout=JELLYFIN_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as http:
                async with http.stream("GET", url, headers=api.headers()) as response:
                    response.raise_for_status()
                    head = bytearray()
                    async for chunk in response.aiter_bytes():
                        head += chunk
                        if len(head) >= JELLYFIN_BODY_LIMIT_BYTES:
                            break
        return bytes(head[:JELLYFIN_BODY_LIMIT_BYTES])

    folders = json.loads(await asyncio.wait_for(read(), timeout=JELLYFIN_TIMEOUT_SECONDS))
    return [
        {
            "id": str(folder.get("ItemId", "")),
            "name": folder.get("Name", ""),
            "type": folder.get("CollectionType", ""),
        }
        for folder in folders
    ]
