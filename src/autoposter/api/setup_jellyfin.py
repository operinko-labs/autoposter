"""One Jellyfin read, for the wizard's library tick-list.

``setup_plex`` with four calls has a module; this has one, and it is here for
the same two reasons that one is: the call is made against an address the
OPERATOR supplied, so it carries this wizard's bounds rather than a client's
defaults, and its outbound request must not reach a log record -- httpx emits
one INFO line per request with the whole url on it.

The request itself is ``jellyfin/client.JellyfinApi``'s and not this module's:
the ``Authorization: MediaBrowser Token="..."`` header has exactly one spelling
in this service, and a second copy of it here would be a header whose typo is
indistinguishable from a wrong API key. What this module owns is the SHAPE it
answers with -- ``id``, ``name`` and ``type`` per library and nothing else,
built field by field so a folder entry that grows a field on some future
Jellyfin cannot grow one in a response this wizard serves. ``Locations``, in
particular, is a list of the server's own filesystem paths and stays out.

``GET /Library/VirtualFolders`` -> ``ItemId``/``Name``/``CollectionType``:
docs/reference/2026-09-jellyfin-openapi-12.md.
"""

import asyncio

import httpx

from autoposter.api.setup_checks import SETUP_VERSION, no_httpx_request_log

# setup_plex's value and its argument: the bound is on the WHOLE call rather
# than per operation, because httpx's own timeout is per read and a server that
# emits one byte inside every window holds the handler for hours.
JELLYFIN_TIMEOUT_SECONDS = 10.0


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

    async def read() -> list[dict]:
        with no_httpx_request_log():
            async with httpx.AsyncClient(
                transport=transport,
                timeout=JELLYFIN_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as http:
                return await JellyfinApi(http, base_url, api_key, SETUP_VERSION).virtual_folders()

    folders = await asyncio.wait_for(read(), timeout=JELLYFIN_TIMEOUT_SECONDS)
    return [
        {
            "id": str(folder.get("ItemId", "")),
            "name": folder.get("Name", ""),
            "type": folder.get("CollectionType", ""),
        }
        for folder in folders
    ]
