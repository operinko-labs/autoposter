"""Which media servers this deployment has (spec §3.5), and the gate (spec §9)."""
from __future__ import annotations

from collections.abc import Iterator, Mapping

from fastapi import HTTPException, Request

from autoposter.servers.base import MediaServer

PLEX_REQUIRED = "This needs Plex, and no Plex server is configured."


class Servers(Mapping[str, MediaServer]):
    """The media servers this deployment has, by name (spec §3.5).

    A read-only view built once, by ``build_servers``, from the effective
    config -- there is no add/remove after construction. Iterating, indexing
    (``servers["plex"]``) and membership (``"plex" in servers``) all come from
    ``Mapping``; ``.plex``/``.jellyfin`` are the two named conveniences most
    callers actually want. Per-server HEALTH is deliberately not carried here:
    it lives on ``app.state.server_health`` instead, built and owned by the
    lifespan (app.py), because it has its own lifecycle (async `.run()` tasks
    to cancel on shutdown) that this plain, immutable registry has no business
    holding.
    """

    def __init__(self, by_name: dict[str, MediaServer]):
        self._by_name = dict(by_name)

    def __getitem__(self, name: str) -> MediaServer:
        return self._by_name[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._by_name)

    def __len__(self) -> int:
        return len(self._by_name)

    @property
    def names(self) -> list[str]:
        return list(self._by_name)

    @property
    def plex(self):
        return self._by_name.get("plex")

    @property
    def jellyfin(self):
        return self._by_name.get("jellyfin")


def build_servers(config, secrets, http) -> Servers:
    """Constructs every server this deployment's config configures.

    Plex via ``_LazyPlexServer`` (moved to ``plex/client.py`` so this module
    does not have to import ``main.py`` -- the circular import that shape
    would otherwise create). Jellyfin via ``JellyfinApi``/``JellyfinClient``
    (jellyfin/client.py) -- ``http`` may be ``None`` in a test app that
    builds a registry without ever making a call; ``JellyfinApi`` only
    touches it on use.
    """
    from autoposter.api.version import _running_version
    from autoposter.jellyfin.client import JellyfinApi, JellyfinClient
    from autoposter.plex.client import PlexClient, _LazyPlexServer

    by_name: dict[str, MediaServer] = {}
    if config.plex is not None:
        by_name["plex"] = PlexClient(
            server=_LazyPlexServer(config.plex.url, secrets.plex_token),
            excluded_libraries=config.plex.excluded_libraries,
            http=http, base_url=config.plex.url, token=secrets.plex_token,
        )
    if config.jellyfin is not None:
        api = JellyfinApi(http, config.jellyfin.url, secrets.jellyfin_api_key, version=_running_version())
        by_name["jellyfin"] = JellyfinClient(
            api, config.jellyfin.excluded_libraries, config.jellyfin.library_map,
            config.jellyfin.replace_thumb_with_backdrop,
        )
    return Servers(by_name)


def plex_configured(app) -> bool:
    """Whether this application is configured to talk to Plex at all.

    Checked three ways, broadest source of truth last:

    * ``app.state.servers`` -- the registry, populated by the lifespan from
      ``build_servers`` once it holds the effective config. The source of
      truth for a fully booted production process.
    * ``app.state.plex`` -- the alias ``app.py`` keeps for one release
      (many existing tests and a few routes still set or read it directly,
      never touching the registry at all -- see ``app.py``'s own note).
    * ``app.state.config_holder`` -- the boot config itself. A process whose
      registry and alias are both empty is not necessarily one with no Plex
      configured: it may simply not have run the lifespan yet (a replica, or
      a test that calls ``create_app`` directly), and such a process must
      still answer this predicate honestly rather than reading as "no Plex
      at all" only because nothing has connected yet -- a distinction a
      caller downstream (an endpoint's own 503 for "not connected") still
      needs to draw.
    """
    servers = getattr(app.state, "servers", None)
    if servers is not None and "plex" in servers:
        return True
    if getattr(app.state, "plex", None) is not None:
        return True
    holder = getattr(app.state, "config_holder", None)
    return holder is not None and holder.current.plex is not None


def require_plex(request: Request) -> None:
    if not plex_configured(request.app):
        raise HTTPException(status_code=409, detail=PLEX_REQUIRED)
