"""What a config generation swap reaches, and what it does not.

Two things live here, and they are two halves of one honest answer to "did my
edit take effect?".

``swap_config`` is the whole of the swap as far as a running application is
concerned: the holder is pointed at the new generation, ``app.state.config``
is rebound so the per-request readers that hold no holder see it too, and the
published scheduler cadences are refreshed. Consumers that were handed the
*holder* -- the worker handler, the scheduler job bodies and their intervals,
the dashboard broadcaster -- need nothing from this function; they deref on
their next read.

``FROZEN_SECTIONS`` is the rest of the truth. Some settings are read exactly
once, at startup, to build an object the process then keeps: the size of the
worker pool, the provider clients, the notifier, the Plex client, whether
FastAPI mounted /docs. A swap cannot reach those, and the editor must say so
rather than let an operator believe an edit landed. The map is data, not
behaviour, so the API layer can render it without importing any of the
machinery it describes.

This module deliberately imports nothing from ``autoposter.app``: the API
routers are included *by* that module, so an endpoint importing the swap from
there would close an import cycle.
"""

# Dotted config paths (a prefix matches itself and everything beneath it)
# mapped to why a swap does not reach them. Every reason names the object that
# was built once, because "restart required" without the why is a shrug.
FROZEN_SECTIONS: dict[str, str] = {
    "workers": (
        "the worker pool is sized once when the process starts, so the new "
        "count applies at the next restart"
    ),
    "providers": (
        "the artwork provider clients and their cache are built once at "
        "startup, in the configured order"
    ),
    "notifications": (
        "the notifier is built once at startup, so a changed target or "
        "retry policy applies at the next restart"
    ),
    "plex": (
        "the Plex client, the liveness probe and the scheduler's server "
        "factory are all built once at startup from these values"
    ),
    "jellyfin": (
        "the Jellyfin client, the liveness probe and the scheduler's server "
        "factory are all built once at startup from these values"
    ),
    "operations.imdb_refresh_enabled": (
        "the IMDb auto-refresh loop is started once at startup and cannot be "
        "switched on or off underneath itself"
    ),
    "operations.imdb_refresh_hours": (
        "the IMDb auto-refresh loop captures its cadence when it starts"
    ),
    "operations.imdb_miss_refresh_minutes": (
        "the miss-triggered IMDb refresh is installed process-wide at startup "
        "(gather_facts carries no client of its own)"
    ),
    "operations.tmdb_backoff_seconds": (
        "the TMDb rate budget captures its window length when the facts client "
        "is built at startup (facts/tmdb_budget.py)"
    ),
    # The one entry here that a restart does not fix either. FastAPI builds
    # the docs routes into the application object, which exists before the
    # lifespan has read a single override -- so this value can only ever come
    # from the mounted file. Said plainly rather than as "restart to apply",
    # which would be a lie an operator would only discover by restarting.
    "api_docs_enabled": (
        "FastAPI decides whether /docs, /redoc and /openapi.json exist when "
        "the application object is constructed, which happens before the "
        "overrides are read -- so a restart will not apply this one either; "
        "set it in the mounted autoposter.yaml"
    ),
    "scheduler.enabled": (
        "the scheduler's job set is registered once at startup; the cadences "
        "of the jobs it did register are live"
    ),
    "scheduler.poll_seconds": (
        "the scheduler captures its poll interval when it starts"
    ),
    "collections.enabled": (
        "whether the collections job is registered is decided once at "
        "startup; the rest of this section is live"
    ),
    "playlists.enabled": (
        "whether the collections job -- which carries the playlists pass -- is "
        "registered is decided once at startup; the rest of this section is live"
    ),
    "arr_sync.enabled": (
        "whether the Radarr/Sonarr job is registered is decided once at "
        "startup; the rest of this section, cadence included, is live"
    ),
}

# Paths that a broader frozen prefix would otherwise swallow, but which are
# genuinely read per use. ``plex.resolve_max_attempts`` is read off the config
# in ``_handle_intent`` every time a job fails against Plex, not captured in
# any client -- see app.py.
LIVE_EXCEPTIONS: frozenset[str] = frozenset({"plex.resolve_max_attempts"})

# The one entry in FROZEN_SECTIONS whose own reason says a restart does not
# help either -- see the comment on api_docs_enabled above. The editor reports
# these separately from "restart required" (routes.py's _inert_changes), so it
# never promises an operator a restart will apply something only editing the
# mounted file can.
INERT_SECTIONS: frozenset[str] = frozenset({"api_docs_enabled"})


def _covers(prefix: str, path: str) -> bool:
    return path == prefix or path.startswith(prefix + ".")


def is_inert(path: str) -> bool:
    """Whether nothing short of editing the mounted config file reaches
    ``path`` -- not a swap, and not a restart either."""
    return any(_covers(prefix, path) for prefix in INERT_SECTIONS)


def frozen_reason(path: str) -> str | None:
    """Why a swap does not reach ``path``, or ``None`` if it does.

    Longest prefix wins, and ``LIVE_EXCEPTIONS`` wins over all of them, so a
    section can be frozen as a whole while one field inside it stays live.
    """
    if any(_covers(live, path) for live in LIVE_EXCEPTIONS):
        return None
    for prefix in sorted(FROZEN_SECTIONS, key=len, reverse=True):
        if _covers(prefix, path):
            return FROZEN_SECTIONS[prefix]
    return None


def swap_config(app, new_config) -> None:
    """Point a running application at a new config generation.

    ``app.state.config`` is rebound rather than mutated: the request handlers
    that read it (intake, the artwork endpoints, /api/status, ...) read the
    attribute per request, so a rebind is exactly as live as the holder is,
    and the object itself stays the immutable thing every reader assumes.

    ``app.state.scheduler_intervals`` is refreshed *in place* -- updated, and
    stale names deleted -- never rebound. The dashboard broadcaster was handed
    that dict by ``create_app`` and holds the object, not its contents (see
    app.py), so rebinding here would leave the live stream reporting the boot
    cadences forever while /api/status reported the new ones.

    The job *set* is not rebuilt: a swap that newly enables the collections or
    arr_sync job does not register it, which is why ``scheduler.enabled`` and
    those two ``enabled`` flags are in ``FROZEN_SECTIONS``.
    """
    app.state.config_holder.swap(new_config)
    app.state.config = new_config

    live = {job.name: job.current_interval() for job in app.state.scheduler_jobs}
    published = app.state.scheduler_intervals
    published.update(live)
    for stale in set(published) - set(live):
        del published[stale]
