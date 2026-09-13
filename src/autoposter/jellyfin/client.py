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

import base64
import functools
import logging

import httpx

from autoposter.jellyfin.index import LibraryIndex
from autoposter.jellyfin.writer import apply_facts as _jf_apply_facts
from autoposter.servers.base import (
    CAP_ARTWORK_PROVENANCE, CAP_FIELD_LOCKS, CAP_LOCK_ARTWORK, CAP_LOGO_UPLOAD,
    CAP_RESET_TO_AGENT_DEFAULT, ItemNotFound, ResolvedItem, SectionItem, ServerItemRef,
    UnsupportedOnServer,
)

logger = logging.getLogger(__name__)

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
        # The user every single-item read is made as; see `reading_user_id`.
        self._user_id: str | None = None

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

    async def users(self) -> list[dict]:                      # capture → "/Users" → get
        return (await self._get("/Users")).json() or []

    async def reading_user_id(self) -> str:
        """The user id every single-item read carries.

        `GET /Items/{itemId}` is served by Jellyfin's user-library controller,
        which looks the user up even when the query names none; an API key has
        no user, so the lookup gets an empty id and the server answers 400
        ("Guid can't be empty") -- verified live against 12.0.0, 2026-09-13,
        and recorded in the capture. The list route (`GET /Items?ids=`) needs
        no user but returns the trimmed DTO, and the writer posts the DTO back
        whole, so a trimmed read would erase what it did not carry. Hence one
        user, resolved once per client from `/Users`: an administrator when
        there is one (administrators see every library), else the first user
        the server lists.
        """
        if self._user_id is None:
            users = await self.users()
            admins = [u for u in users if (u.get("Policy") or {}).get("IsAdministrator")]
            chosen = (admins or users or [{}])[0].get("Id")
            if not chosen:
                raise RuntimeError("jellyfin: the server lists no user to read items as")
            self._user_id = str(chosen)
        return self._user_id

    async def item(self, item_id: str) -> dict:               # capture → "/Items/{itemId}" → get
        user_id = await self.reading_user_id()
        return (await self._get(f"/Items/{item_id}", userId=user_id)).json()

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
        # documented as image/* raw bytes, not multipart and not JSON. V3
        # (design doc §11), resolved live 2026-09-13 against Jellyfin 12.0.0:
        # raw bytes get a 500 -- the wire body must be base64-encoded, same as
        # the "known behaviour of earlier versions" the design doc flagged as
        # open. Content-Type stays the real image mime; only the body changes.
        response = await self._http.post(
            f"{self.base_url}/Items/{item_id}/Images/{image_type}", content=base64.b64encode(data),
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


def _guard(method):
    """Wraps a ``JellyfinClient`` method whose second parameter is a
    ``ServerItemRef``: a 404 means Jellyfin no longer has the item, mapped
    to ``ItemNotFound`` (queue/worker.py's except ladder already knows what
    to do with that); anything else is logged by class name only -- never a
    URL -- and re-raised unchanged."""

    @functools.wraps(method)
    async def wrapper(self, ref, *args, **kwargs):
        try:
            return await method(self, ref, *args, **kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ItemNotFound(f"Jellyfin no longer has item {ref.native_id}") from exc
            logger.warning("jellyfin: %s failed (%s)", method.__name__, type(exc).__name__)
            raise

    return wrapper


class JellyfinClient:
    """Jellyfin as a ``MediaServer`` (servers/base.py): composes
    ``LibraryIndex`` (resolution/listing) and ``jellyfin.writer.apply_facts``
    (metadata writes) over the ``JellyfinApi`` transport above. Every
    endpoint an operation here reaches for is cited on the ``JellyfinApi``
    method it calls, not restated."""

    name = "jellyfin"
    capabilities = JELLYFIN_CAPABILITIES

    def __init__(
        self, api: JellyfinApi, excluded_libraries: list[str], library_map: dict[str, str],
        replace_thumb_with_backdrop: bool,
    ):
        self._api = api
        self.base_url = api.base_url
        self._thumb = replace_thumb_with_backdrop
        self._index = LibraryIndex(api, excluded=set(excluded_libraries), library_map=dict(library_map))

    # --- resolution: LibraryIndex does the work ---
    async def resolve(self, intent) -> ResolvedItem:
        return await self._index.resolve(intent)

    async def fetch_ref(self, native_id: str) -> ServerItemRef | None:
        try:
            dto = await self._api.item(native_id)  # capture → "/Items/{itemId}" → get
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            logger.warning("jellyfin: fetch_ref failed (%s)", type(exc).__name__)
            raise
        await self._index.rebuild()  # no-op once already built; needed so library_of/kind_of see the folders
        return ServerItemRef("jellyfin", dto["Id"], self._index.library_of(dto), self._index.kind_of(dto))

    async def exists_many(self, intents) -> list[bool]:
        return [await self._index.exists(intent) for intent in intents]

    async def library_names(self) -> set[str]:
        await self._index.rebuild()  # no-op once built; the folder list is what we need
        return self._index.library_names()

    async def _key_matches(self, dto: dict, intent) -> bool:
        """Whether a live-fetched ``dto`` is still the item ``intent``'s
        stored native id names -- plex/client.py:~396's type+coordinate
        check, with the ``Type`` interpretation delegated to the index's own
        ``kind_of``/``_pid`` rather than re-read here (the ruling's "falls
        back to the index").

        Also requires the item's own library to still be one of the index's
        non-excluded, right-typed folders -- plex/client.py:411-419's own
        check (there, an item whose ``librarySectionTitle`` is not among the
        caller's already-filtered ``sections`` is refused the same way). A
        stale key that now names a real item moved into an excluded library
        (or one Jellyfin no longer places under any known root) must not be
        accepted just because its type and coordinates still match.
        """
        from autoposter.jellyfin.index import _pid

        kind = self._index.kind_of(dto)
        if kind != intent.kind:
            return False
        if kind == "season":
            matches_coords = dto.get("IndexNumber") == intent.season_number
        elif kind == "episode":
            matches_coords = (
                dto.get("ParentIndexNumber") == intent.season_number
                and dto.get("IndexNumber") == intent.episode_number
            )
        else:
            wanted = [
                (ns, v) for ns, v in
                (("tmdb", intent.tmdb_id), ("tvdb", intent.tvdb_id), ("imdb", intent.imdb_id)) if v
            ]
            matches_coords = not wanted or any(str(_pid(dto, ns)) == str(v) for ns, v in wanted)
        if not matches_coords:
            return False
        await self._index.rebuild()  # no-op once already built and fresh
        return self._index._folder_of(dto) is not None

    async def keys_resolve(self, intents) -> list[bool]:
        out = []
        for intent in intents:
            native_id = intent.native_id_on("jellyfin")
            if not native_id:
                out.append(False)
                continue
            try:
                dto = await self._api.item(native_id)  # capture → "/Items/{itemId}" → get
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    out.append(False)
                    continue
                logger.warning("jellyfin: keys_resolve failed (%s)", type(exc).__name__)
                raise
            out.append(await self._key_matches(dto, intent))
        return out

    def invalidate(self) -> None:
        """Forces the next resolve/exists/list_items to rebuild the library
        index from scratch (spec §4.4 step 6). The full pass calls this at
        its start; wiring that call site is the pipeline's job, not this one's."""
        self._index.invalidate()

    async def list_items(self, kind: str) -> list[SectionItem]:
        return await self._index.list_items(kind)

    @_guard
    async def item_labels(self, ref: ServerItemRef) -> list[str]:
        dto = await self._api.item(ref.native_id)  # capture → "/Items/{itemId}" → get
        return list(dto.get("Tags") or [])

    # --- artwork ---
    @_guard
    async def upload_artwork(self, ref: ServerItemRef, data: bytes, art_kind: str, lock: bool) -> None:
        if lock:
            raise UnsupportedOnServer(self.name, CAP_LOCK_ARTWORK)  # no CAP_LOCK_ARTWORK on this server
        content_type = _content_type(data)
        # capture → "/Items/{itemId}/Images/{imageType}" → post
        await self._api.set_image(ref.native_id, IMAGE_SLOT[art_kind], data, content_type)
        if art_kind == "background" and self._thumb:
            await self._api.set_image(ref.native_id, "Thumb", data, content_type)

    @_guard
    async def upload_logo(self, ref: ServerItemRef, data: bytes, suffix: str = ".png") -> str | None:
        await self._api.set_image(ref.native_id, "Logo", data, _content_type(data))
        return None  # no CAP_LOGO_UPLOAD_KEY: Jellyfin hands back no per-upload key

    @_guard
    async def clear_logo(self, ref: ServerItemRef) -> None:
        await self._api.delete_image(ref.native_id, "Logo")  # capture → ".../Images/{imageType}" → delete

    @_guard
    async def has_clearlogo(self, ref: ServerItemRef) -> bool:
        infos = await self._api.image_infos(ref.native_id)  # capture → "/Items/{itemId}/Images" → get
        return any(info.get("ImageType") == "Logo" for info in infos)

    @_guard
    async def fetch_artwork(self, ref: ServerItemRef, art_kind: str) -> tuple[bytes, str] | None:
        data = await self._api.image(ref.native_id, IMAGE_SLOT[art_kind])  # capture → ".../Images/{imageType}" → get
        if data is None:
            return None
        return data, _content_type(data)

    @_guard
    async def artwork_provenance(self, ref: ServerItemRef, art_kind: str) -> str | None:
        # plex/exif is server-agnostic despite the package name: its
        # header/tail readers already work directly on a byte string, so the
        # bytes `fetch_artwork`'s own `image()` call already has in hand are
        # enough -- no second HTTP round trip the way Plex's URL-based
        # probe_exif needs.
        #
        # Unlike plex/artwork.py's own artwork_provenance -- which swallows
        # every failure and always answers None, so a Plex hiccup costs one
        # redundant upload rather than an exception -- this one, wrapped by
        # `_guard`, RAISES on a non-404 transport error (logged by class name)
        # rather than answering None. Callers already wrap this call the same
        # way they wrap any other server operation, so the divergence is
        # deliberate rather than a gap to close here.
        from autoposter.plex.exif import PROVENANCE_TAG, parse_provenance, read_exif_from_header, read_exif_from_tail

        data = await self._api.image(ref.native_id, IMAGE_SLOT[art_kind])
        if data is None:
            return None
        if data.startswith(b"\xff\xd8"):
            exif = read_exif_from_header(data)
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            exif = read_exif_from_tail(data)
        else:
            return None
        return parse_provenance(exif.get(PROVENANCE_TAG))

    async def reset_artwork_to_agent_default(self, ref: ServerItemRef, art_kind: str) -> bool:
        raise UnsupportedOnServer(self.name, CAP_RESET_TO_AGENT_DEFAULT)  # no CAP_RESET_TO_AGENT_DEFAULT

    # --- metadata ---
    @_guard
    async def apply_facts(
        self, ref: ServerItemRef, facts, operations=None,
        parental_categories=None, overrides=None,
    ) -> dict:
        return await _jf_apply_facts(self._api, ref, facts, operations, parental_categories, overrides)

    async def check_liveness(self) -> bool:
        try:
            return bool(await self._api.system_info())  # capture → "/System/Info" → get
        except Exception as exc:
            logger.warning("jellyfin liveness check failed (%s)", type(exc).__name__)
            return False
