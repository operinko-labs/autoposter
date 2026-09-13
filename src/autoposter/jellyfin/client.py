"""Jellyfin 12.x transport. Every endpoint is cited to
docs/reference/2026-09-jellyfin-openapi-12.md. No third-party SDK, and
nothing copied from any other Jellyfin client (spec §0).

JellyfinApi is the transport only -- one method per captured endpoint, raw
dicts and bytes in and out, no interpretation of what the fields mean. The
protocol implementation (JellyfinClient) that turns this into a MediaServer
is a separate task; this module knows nothing about ResolvedItem, LibraryIndex,
or apply_facts.
"""
from __future__ import annotations

import httpx

from autoposter.servers.base import CAP_ARTWORK_PROVENANCE, CAP_FIELD_LOCKS, CAP_LOGO_UPLOAD

JELLYFIN_CAPABILITIES = frozenset({CAP_LOGO_UPLOAD, CAP_FIELD_LOCKS, CAP_ARTWORK_PROVENANCE})

# art_kind -> ImageType (capture → "/Items/{itemId}/Images/{imageType}" → post: ImageType enum).
IMAGE_SLOT = {"poster": "Primary", "season_poster": "Primary", "title_card": "Primary", "background": "Backdrop"}
CONTENT_TYPES = {b"\x89PNG": "image/png", b"RIFF": "image/webp", b"\xff\xd8": "image/jpeg"}


def _content_type(data: bytes) -> str:
    for magic, ct in CONTENT_TYPES.items():
        if data.startswith(magic):
            return ct
    return "image/jpeg"


class JellyfinApi:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str, version: str):
        self._http = http
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._version = version

    def headers(self) -> dict[str, str]:
        # capture → securitySchemes.CustomAuthentication: an apiKey named
        # "Authorization". The VALUE format is V1 (design doc; verified live
        # 2026-09-13 against Jellyfin 12.0.0 -- MediaBrowser Token="..." works
        # with or without Client/Device/DeviceId/Version; X-Emby-Token is
        # refused, 401).
        return {
            "Authorization": (
                f'MediaBrowser Token="{self._api_key}", Client="autoposter", '
                f'Device="autoposter", DeviceId="autoposter", Version="{self._version}"'
            ),
            "Accept": "application/json",
        }

    async def _get(self, path: str, **params) -> httpx.Response:
        response = await self._http.get(f"{self.base_url}{path}", params=params or None, headers=self.headers())
        response.raise_for_status()
        return response

    async def system_info(self) -> dict:                      # capture → "/System/Info" → get
        return (await self._get("/System/Info")).json()

    async def virtual_folders(self) -> list[dict]:            # capture → "/Library/VirtualFolders" → get
        return (await self._get("/Library/VirtualFolders")).json()

    async def items(self, **query) -> list[dict]:             # capture → "/Items" → get
        # BaseItemDtoQueryResult.Items is optional on the wire; absent means none.
        return (await self._get("/Items", **query)).json().get("Items") or []

    async def item(self, item_id: str) -> dict:               # capture → "/Items/{itemId}" → get
        return (await self._get(f"/Items/{item_id}")).json()

    async def seasons(self, series_id: str) -> list[dict]:    # capture → "/Shows/{seriesId}/Seasons" → get
        return (await self._get(f"/Shows/{series_id}/Seasons", fields="ProviderIds,Path")).json().get("Items") or []

    async def episodes(self, series_id: str) -> list[dict]:   # capture → "/Shows/{seriesId}/Episodes" → get
        return (await self._get(f"/Shows/{series_id}/Episodes", fields="ProviderIds,Path")).json().get("Items") or []

    async def update_item(self, item_id: str, dto: dict) -> None:
        # capture → "/Items/{itemId}" → post (UpdateItem): full-body replace, 204 on success.
        response = await self._http.post(f"{self.base_url}/Items/{item_id}", json=dto, headers=self.headers())
        response.raise_for_status()

    async def set_image(self, item_id: str, image_type: str, data: bytes, content_type: str) -> None:
        # capture → "/Items/{itemId}/Images/{imageType}" → post: requestBody is
        # image/* raw bytes, not multipart and not JSON. (V3 in the design doc.)
        response = await self._http.post(
            f"{self.base_url}/Items/{item_id}/Images/{image_type}", content=data,
            headers={**self.headers(), "Content-Type": content_type},
        )
        response.raise_for_status()

    async def image(self, item_id: str, image_type: str) -> bytes | None:
        # capture → "/Items/{itemId}/Images/{imageType}" → get: no `security`
        # block on this operation (unlike every other route captured) -- a
        # public image read. 404 means no image of that type; anything else raises.
        response = await self._http.get(f"{self.base_url}/Items/{item_id}/Images/{image_type}", headers=self.headers())
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    async def delete_image(self, item_id: str, image_type: str) -> None:
        # capture → "/Items/{itemId}/Images/{imageType}" → delete: 204 on
        # success; 404 (nothing to delete) is tolerated, anything else raises.
        response = await self._http.delete(f"{self.base_url}/Items/{item_id}/Images/{image_type}", headers=self.headers())
        if response.status_code != 404:
            response.raise_for_status()

    async def image_infos(self, item_id: str) -> list[dict]:  # capture → "/Items/{itemId}/Images" → get
        return (await self._get(f"/Items/{item_id}/Images")).json()
