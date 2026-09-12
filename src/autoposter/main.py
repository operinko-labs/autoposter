import logging

import httpx
import uvicorn
from fastapi import FastAPI

from autoposter.api.spa import mount_spa, spa_dist
from autoposter.app import create_app
from autoposter.config.loader import DEFAULT_CONFIG_PATH, load_config
from autoposter.config.schema import Config, Secrets
from autoposter.db.base import make_engine, make_session_factory
from autoposter.servers.registry import Servers, build_servers

# The file this process boots from, published as app.state.config_path so the
# lifespan knows which document to merge the database overrides over and the
# config editor knows which document to revert to. Kept under this name
# because the suite monkeypatches main.CONFIG_PATH to point at the example
# config.
CONFIG_PATH = DEFAULT_CONFIG_PATH

logger = logging.getLogger(__name__)


def build() -> FastAPI:
    """The application, constructed from the config *file* alone.

    Nothing here awaits or blocks on the database, and that is the whole
    contract: ``uvicorn autoposter.main:build --factory`` -- the dev-compose
    hot-reload command, and the only shape ``--reload`` accepts -- calls this
    from inside the server's already-running event loop. A synchronous bridge
    to the async overrides read (``asyncio.run``) therefore cannot live here;
    it raises ``RuntimeError: asyncio.run() cannot be called from a running
    event loop`` and takes every hot-reload boot down with it.

    The database overrides are merged in by the lifespan instead, at its very
    first statement, before any consumer is built from the config -- see
    app.py. Production (``python -m autoposter.main``, below) and the factory
    path go through exactly the same sequence, so there is one boot path to
    reason about rather than two.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs one INFO line per request carrying the FULL url, and two of
    # this process's URLs embed secrets: the notifications webhook may carry
    # a token in its path (Uptime-Kuma style -- the host-only guarantee in
    # config/schema.py's NotificationsConfig), and fanart.tv's key travels
    # as an api_key query parameter (providers/fanart.py). Neither may reach
    # the pod logs, so httpx speaks only at WARNING and above.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    secrets = Secrets.from_env()
    config = load_config(CONFIG_PATH)

    engine = make_engine(secrets.database_url)
    session_factory = make_session_factory(engine)

    def servers_factory(effective: Config, http: httpx.AsyncClient) -> Servers:
        """The servers this deployment configures, built by the lifespan once
        it holds the effective config rather than here.

        ``config.plex``/``config.jellyfin`` feed three objects built once at
        startup: the servers themselves, the liveness probe(s) and the
        scheduler's server factory. The other two are built inside the
        lifespan, after the overrides land. Building this one here would
        leave the client that runs every job pointed at the un-overridden URL
        while the probe that decides whether jobs run at all watched the
        overridden one -- a split no operator could be expected to diagnose.
        Passing the recipe instead keeps the wiring in this module and its
        timing in the lifespan's.

        ``http`` is what lets a built ``PlexClient`` actually read artwork
        (``fetch_artwork``/``artwork_provenance`` go over HTTP, never through
        ``plexapi``) -- the lifespan hands its own ``http`` in here so this is
        the one real client construction in the process with it wired, unlike
        the prune/merge jobs' own clients (app.py), which never read artwork
        and so never needed it.
        """
        return build_servers(effective, secrets, http)

    # create_app publishes app.state.config_holder from this config -- the
    # file generation the process starts on, which the lifespan then swaps for
    # the effective one. Building the holder there rather than here is what
    # gives every test's application one too.
    app = create_app(
        config, session_factory, secrets, run_background=True,
        servers_factory=servers_factory, engine=engine,
    )
    # Which document the config came from. create_app defaults this to
    # DEFAULT_CONFIG_PATH; rebinding it to the path this call actually read is
    # what keeps the lifespan's overrides merge over the same file, including
    # when the suite repoints CONFIG_PATH.
    app.state.config_path = CONFIG_PATH
    # Last, and here rather than in create_app(): the SPA's catch-all matches
    # whatever no router claimed, so anything mounted afterwards is
    # unreachable. Keeping it out of the factory also keeps it out of the test
    # suite, which would otherwise pick up a stale frontend/dist from the
    # developer's checkout and quietly serve it during every test.
    mount_spa(app, spa_dist())
    return app


def main() -> None:
    # Bounded, rather than uvicorn's default of waiting forever: the
    # dashboard's /api/dashboard/stream connection never ends on its own (its
    # generator's `finally` -- the only thing that unsubscribes it -- runs on
    # disconnect, not on a timer), so uvicorn's graceful shutdown would sit at
    # "Waiting for connections to close" for the whole pod lifetime with any
    # dashboard tab left open. That leaves the lifespan's `finally`
    # (task cancellation, http.aclose(), engine.dispose()) unreached until
    # kubelet's SIGKILL at the 30s grace period, which is exactly the
    # dropped-connection symptom this bounds: at 10s uvicorn forces the
    # remaining connections closed and the lifespan's shutdown runs instead.
    uvicorn.run(build(), host="0.0.0.0", port=8080, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
