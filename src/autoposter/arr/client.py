"""A thin client for one Radarr or Sonarr instance.

Both services expose nearly identical read-mostly APIs: list what they
already have, resolve a quality profile by name, list root folders, and add
one item. Authentication is the ``X-Api-Key`` request header on every call
-- never a query string, never logged.

A non-2xx response always raises rather than being swallowed into an empty
collection: an empty listing would make every Plex item look missing, which
under ``add_existing`` is a request to register the entire library. A 200-OK
*empty* listing is just as dangerous and cannot be caught here -- see
``sync.sync_section``, which refuses to act on one.
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

    async def listing(self) -> list[dict]:
        """Everything the service currently holds, in one GET.

        Both views the sync needs -- the set of external ids and the map of
        registered paths -- are derived from this single response by
        ``ids_in``/``paths_in``, rather than each fetching the same endpoint
        again: one round trip instead of two, and the two views cannot
        disagree with each other about what the service holds.
        """
        url = f"{self._base_url}/api/v3/{self._kind.resource}"
        response = await self._http.get(url, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def ids_in(self, entries: list[dict]) -> set[str]:
        """Ids in a listing, as strings, for comparing against Plex guids.

        A set, because the sync's question is membership -- "does the service
        hold this?" -- and has no order. ``ordered_ids_in`` is the same
        entries for the caller whose question does.
        """
        return set(self.ordered_ids_in(entries))

    def ordered_ids_in(self, entries: list[dict]) -> list[str]:
        """The same ids, in the order the service listed them.

        The collection builders read this one: a collection built from a
        listing *is* an order, and a set has none. Sharing the skip rule with
        ``ids_in`` rather than restating it is the point -- two spellings
        would eventually disagree about which entries count, and the
        disagreement would show up as an item the sync thinks is registered
        and a collection that leaves it out.

        Entries whose id field is absent or zero are skipped: an unmatched
        item in the service has no external id and must not collide with
        anything.
        """
        ids = []
        for entry in entries:
            value = entry.get(self._kind.id_field)
            if value:
                ids.append(str(value))
        return ids

    def tag_ids_in(self, entry: dict) -> set[int]:
        """The tag ids on one listing entry.

        Ids, not labels: a listing entry carries only the numbers, and what
        they mean is the separate vocabulary ``tags`` fetches. Absent and
        empty are the same answer -- an entry with no tags.
        """
        return {int(value) for value in entry.get("tags") or []}

    def paths_in(self, entries: list[dict]) -> dict[str, dict]:
        """Every path in a listing, normalised (no trailing slash, case
        preserved), mapped to that entry's title and external id.

        Comparing by id alone misses the case where the service holds the
        right folder under the *wrong* id -- a different item entirely. The
        sync uses this to catch that before ever adding, by checking a
        mapped Plex path against what the service already has on disk.
        """
        paths: dict[str, dict] = {}
        for entry in entries:
            path = entry.get("path")
            if not path:
                continue
            normalized = path.replace("\\", "/").rstrip("/")
            paths[normalized] = {"title": entry.get("title"), self._kind.id_field: entry.get(self._kind.id_field)}
        return paths

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

    async def tags(self) -> dict[str, int]:
        """The instance's tag vocabulary, label to id.

        One flat vocabulary per service -- ``/api/v3/tag`` takes no
        ``movie``/``series`` resource -- and the only way to turn the labels
        an operator writes in a config into the ids a listing entry carries.

        Raises on a non-2xx like every other call here, and for a sharper
        reason than most: an unreadable vocabulary would make every configured
        tag name look unknown, and "unknown tag" is a refusal an operator
        would go and act on by editing a config that was never wrong.
        """
        url = f"{self._base_url}/api/v3/tag"
        response = await self._http.get(url, headers=self._headers())
        response.raise_for_status()
        labels = {}
        for entry in response.json():
            label, tag_id = entry.get("label"), entry.get("id")
            if label and tag_id is not None:
                labels[label] = tag_id
        return labels

    async def root_folders(self) -> list[str]:
        """The roots this instance actually manages, e.g. ``/mnt/media/Movies``.

        ``sync_section`` checks the configured path mapping against these
        before comparing anything: a service that manages a completely
        different tree is not the instance this sync was configured for.
        """
        url = f"{self._base_url}/api/v3/rootfolder"
        response = await self._http.get(url, headers=self._headers())
        response.raise_for_status()
        return [entry["path"] for entry in response.json()]

    async def add(self, payload: dict) -> dict:
        url = f"{self._base_url}/api/v3/{self._kind.resource}"
        response = await self._http.post(url, json=payload, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def entry_ids_by_external_id(self, entries: list[dict]) -> dict[str, int]:
        """``{external id: the service's own id}`` for a listing.

        The join the tag write needs: a Plex item is matched by its tmdb/tvdb
        guid, and the editor endpoint takes the service's INTERNAL ids. Derived
        from the listing already fetched rather than from a second request, and
        skipping the same entries ``ordered_ids_in`` skips -- an entry with no
        external id cannot be matched to a Plex item at all, so tagging it
        would be tagging something nobody named.
        """
        mapping: dict[str, int] = {}
        for entry in entries:
            value, entry_id = entry.get(self._kind.id_field), entry.get("id")
            if value and entry_id is not None:
                mapping.setdefault(str(value), int(entry_id))
        return mapping

    async def create_tag(self, label: str) -> int:
        """Add a label to the instance's tag vocabulary and return its id.

        ``POST /api/v3/tag`` with a ``TagResource`` body (banked:
        ``docs/reference/2026-09-arr-openapi.md``, ``paths → "/api/v3/tag" →
        post``). One flat vocabulary per service, as ``tags()`` above says.

        Raises on a non-2xx like everything else here, and for a sharp reason:
        a creation that failed but was treated as fine would leave the caller
        writing an id that means nothing, or another tag entirely.
        """
        url = f"{self._base_url}/api/v3/tag"
        response = await self._http.post(url, json={"label": label}, headers=self._headers())
        response.raise_for_status()
        return int(response.json()["id"])

    async def apply_tags(self, entry_ids: list[int], tag_ids: list[int]) -> None:
        """Add tags to entries the service already holds. The client's first PUT.

        ``PUT /api/v3/movie/editor`` (Radarr) / ``PUT /api/v3/series/editor``
        (Sonarr) with ``{"<resource>Ids": [...], "tags": [...], "applyTags":
        "add"}`` -- banked verbatim in
        ``docs/reference/2026-09-arr-openapi.md``.

        The editor endpoint rather than ``PUT /api/v3/{movie,series}/{id}``,
        deliberately. That one takes the FULL resource (49 properties on
        Radarr's ``MovieResource``, ``additionalProperties: false``), so a
        write built from anything less than a fresh, complete GET can blank a
        field on the operator's own instance -- and it costs one request per
        item where this costs one per pass.

        ``applyTags`` is pinned to ``"add"``. The enum also has ``"remove"``
        and ``"replace"``; this service tags items it manages and does not own
        the instance's tag vocabulary, so it must never take a tag off
        anything. **Note that ``DELETE`` on this same URL takes this same body
        and deletes the items** -- the method is part of the contract, which is
        why the tests pin it.

        Nothing to tag is not a request: an empty list on either side would be
        a body the service is free to interpret however it likes.
        """
        if not entry_ids or not tag_ids:
            return
        url = f"{self._base_url}/api/v3/{self._kind.resource}/editor"
        payload = {
            f"{self._kind.resource}Ids": list(entry_ids),
            "tags": list(tag_ids),
            "applyTags": "add",
        }
        response = await self._http.put(url, json=payload, headers=self._headers())
        response.raise_for_status()
