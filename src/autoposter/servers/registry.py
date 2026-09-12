"""Which media servers this deployment has (spec §3.5), and the gate (spec §9)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Iterator

from fastapi import HTTPException, Request

from autoposter.servers.base import MediaServer

PLEX_REQUIRED = "This needs Plex, and no Plex server is configured."


class Servers(Mapping[str, MediaServer]):
    def __init__(self, by_name: dict[str, MediaServer], health: dict | None = None):
        self._by_name = dict(by_name)
        self.health = dict(health or {})

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
    would otherwise create). Jellyfin is stubbed until Task 14 lands the
    client: a ``jellyfin:`` block is accepted by Task 10's config already, so
    this raises rather than silently building nothing, which is what lets
    Phase 3 (this task) merge ahead of Phase 4.
    """
    from autoposter.plex.client import PlexClient, _LazyPlexServer

    by_name: dict[str, MediaServer] = {}
    if config.plex is not None:
        by_name["plex"] = PlexClient(
            server=_LazyPlexServer(config.plex.url, secrets.plex_token),
            excluded_libraries=config.plex.excluded_libraries,
            http=http, base_url=config.plex.url, token=secrets.plex_token,
        )
    if config.jellyfin is not None:
        raise NotImplementedError("the Jellyfin client lands in Task 14")
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
