import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
import uvicorn
from fastapi import FastAPI

from autoposter.api.spa import mount_spa, spa_dist
from autoposter.app import create_app
from autoposter.boot import stored_config_document
from autoposter.config.loader import DEFAULT_CONFIG_PATH, load_config
from autoposter.config.overrides import _validated
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


def _stored_document(database_url: str) -> dict | None:
    """``boot.stored_config_document``, off this thread.

    That function owns a private event loop, which is correct for the frame it
    was written for -- ``boot.main`` is synchronous and runs before uvicorn --
    and raises outright from inside a running one. ``build()`` may well have
    one: ``uvicorn autoposter.main:build --factory``, the dev-compose reload
    command, calls it from the server's own loop. Called there directly, the
    read would land in that function's catch-all and answer ``None``, which is
    indistinguishable from a store that holds nothing -- so a deployment whose
    configuration is entirely in the database would be told it has none
    anywhere. A worker thread has no loop of its own, so the read runs exactly
    as it does at boot; blocking this thread for it is what reading the file
    already did.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(stored_config_document, database_url).result()


def _boot_config(database_url: str) -> Config:
    """The configuration this application OBJECT is built from.

    The FILE when there is one, byte for byte the load this module has always
    done, and the store's document only when there is not. Every deployment
    that mounts a document is therefore unaffected, including in
    ``api_docs_enabled`` -- the one setting ``create_app`` takes from this
    generation and the one the schema documents as file-only.

    The second arm is what makes the file removable. Until it existed, a
    deployment whose document and secrets both lived in the database still
    died here on ``FileNotFoundError``: ``boot`` would decide it was
    configured, exec this module, and this line would raise before the
    lifespan -- which loads the store -- ever ran.

    This is a BOOT-TIME config and not the effective one either way: the
    lifespan replaces it with ``load_effective_config``'s before a request is
    served (app.py). It exists because the ``FastAPI`` object has to.

    The stored document is validated through the overrides layer's own
    ``_validated`` rather than through ``build_config`` alone, so that the one
    document gets one verdict: every other reader of the store -- the
    lifespan's ``load_effective_config``, the config write path -- refuses a
    stored ``secrets`` key there, and a boot-time ``Config`` that accepted one
    the lifespan is about to refuse would be two answers to one question.

    Neither a file nor a store is unreachable through ``boot``, which serves
    the wizard for that shape rather than exec'ing this module. It is raised
    rather than invented so that a direct caller is told, and it says the
    store ANSWERED NONE rather than that this deployment is unconfigured: from
    here those are the same fact, and the read's own log line -- one of the
    pair ``stored_config_document`` always writes -- is what says whether the
    store was unreadable or simply holds nothing. Reporting an outage as
    "never configured" would send an operator to reconfigure a deployment that
    is already configured.
    """
    if CONFIG_PATH.is_file():
        return load_config(CONFIG_PATH)
    document = _stored_document(database_url)
    if document is None:
        raise ValueError(
            f"no configuration document: nothing at {CONFIG_PATH}, and the "
            "configuration store answered none"
        )
    return _validated(document)


def build() -> FastAPI:
    """The application, constructed from one config *document* alone -- the
    mounted file's, or the store's when there is no file (``_boot_config``).

    Nothing here awaits or blocks on the database, and that is the whole
    contract: ``uvicorn autoposter.main:build --factory`` -- the dev-compose
    hot-reload command, and the only shape ``--reload`` accepts -- calls this
    from inside the server's already-running event loop. A synchronous bridge
    to the async overrides read (``asyncio.run``) therefore cannot live here;
    it raises ``RuntimeError: asyncio.run() cannot be called from a running
    event loop`` and takes every hot-reload boot down with it. The one read
    ``_boot_config`` may make -- the store's document, on a deployment with no
    mounted file -- goes through a worker thread for exactly that reason, and
    is not made at all by a deployment that has a file.

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
    config = _boot_config(secrets.database_url)

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
