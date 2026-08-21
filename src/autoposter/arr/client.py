"""A thin client for one Radarr or Sonarr instance.

Both services expose nearly identical read-mostly APIs: list what they
already have, resolve a quality profile by name, list root folders, and add
one item. Authentication is the ``X-Api-Key`` request header on every call
-- never a query string, never logged.

A non-2xx response always raises rather than being swallowed into an empty
collection: an empty ``existing_ids`` would make every Plex item look
missing, which under ``add_existing`` is a request to register the entire
library.
"""
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ArrKind:
    name: str
    resource: str
    id_field: str


RADARR = ArrKind("radarr", "movie", "tmdbId")
SONARR = ArrKind("sonarr", "series", "tvdbId")


class ArrClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str, api_key: str, kind: ArrKind):
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._kind = kind

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self._api_key}

    async def existing_ids(self) -> set[str]:
        """Ids the service already has, as strings, for comparing against Plex guids.

        Entries whose id field is absent or zero are skipped -- an unmatched
        item in the service has no external id and must not collide with
        anything.
        """
        url = f"{self._base_url}/api/v3/{self._kind.resource}"
        response = await self._http.get(url, headers=self._headers())
        response.raise_for_status()
        ids = set()
        for entry in response.json():
            value = entry.get(self._kind.id_field)
            if value:
                ids.add(str(value))
        return ids

    async def quality_profile_id(self, name: str) -> int | None:
        """Resolve a quality profile by exact name. None if it is absent.

        Never falls back to another profile -- a missing profile must be
        reported, not silently replaced.
        """
        url = f"{self._base_url}/api/v3/qualityprofile"
        response = await self._http.get(url, headers=self._headers())
        response.raise_for_status()
        for profile in response.json():
            if profile.get("name") == name:
                return profile.get("id")
        return None

    async def root_folders(self) -> list[str]:
        url = f"{self._base_url}/api/v3/rootfolder"
        response = await self._http.get(url, headers=self._headers())
        response.raise_for_status()
        return [entry["path"] for entry in response.json()]

    async def add(self, payload: dict) -> dict:
        url = f"{self._base_url}/api/v3/{self._kind.resource}"
        response = await self._http.post(url, json=payload, headers=self._headers())
        response.raise_for_status()
        return response.json()
