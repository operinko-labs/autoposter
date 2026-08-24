"""One-shot collection reconciliation: ``python -m autoposter.collections``.

Scheduling belongs to a later phase. This exists so the reconciliation can be
run and inspected now, which matters because the first real run against a
library is the one worth reading carefully before anything is written.

There is no ``session_scope`` or ``build_server`` helper in this codebase.
This mirrors the shape ``main.py`` and ``facts/imdb.py``'s own one-shot entry
point (``_run_cli``) actually use: the config is loaded from a path (read from
``AUTOPOSTER_CONFIG``, same as ``main.py``) with the database overrides merged
over it by ``load_effective_config``, the session comes from
``make_engine``/``make_session_factory`` used directly, and the Plex
connection is a plain ``plexapi.server.PlexServer`` -- there is no need for
``main.py``'s lazy-connect wrapper here, since this process does nothing
before it needs Plex anyway.
"""
import asyncio
import logging
import os
from pathlib import Path

import httpx
from plexapi.server import PlexServer

from autoposter.collections.service import build_source_clients, reconcile_libraries
from autoposter.config.overrides import load_effective_config
from autoposter.config.schema import Secrets
from autoposter.db.base import make_engine, make_session_factory
from autoposter.facts.tmdb_facts import TMDBFactsClient
from autoposter.providers.cache import ProviderCache

CONFIG_PATH = Path(os.environ.get("AUTOPOSTER_CONFIG", "/config/autoposter.yaml"))

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    secrets = Secrets.from_env()

    # The database comes first now: the config the operator is actually
    # running is the YAML with the UI's overrides merged over it, and reading
    # those needs a session. Nothing here needed the config to build the
    # engine -- the database URL is a secret, not a config setting -- so this
    # is the same work in a different order.
    engine = make_engine(secrets.database_url)
    session_factory = make_session_factory(engine)
    try:
        async with session_factory() as session:
            config = await load_effective_config(CONFIG_PATH, session)
            if not config.collections.enabled:
                logger.info("collections are disabled in config")
                return

            server = PlexServer(config.plex.url, secrets.plex_token)
            async with httpx.AsyncClient() as http:
                # The same cache-fronted client the API process uses, built
                # here because this process has no lifespan to build it: a
                # ``tmdb_summary:`` definition run from the CLI must borrow its
                # summary through the provider cache, not around it.
                cache = (
                    ProviderCache(session_factory)
                    if config.providers.cache_ttl_seconds > 0 else None
                )
                summaries = TMDBFactsClient(
                    secrets.tmdb_token, http, cache=cache,
                    cache_ttl_seconds=config.providers.cache_ttl_seconds,
                )
                # The builders' own clients, built here for the same reason as
                # the cache above: this process has no lifespan to build them,
                # and a definition backed by MDBList or Radarr must work from
                # the CLI exactly as it does from the scheduled pass.
                sources = build_source_clients(config, secrets, http, cache)
                result = await reconcile_libraries(
                    session, server, config, http, summaries=summaries,
                    sources=sources, cache=cache,
                )
            logger.info(result.summary)
    finally:
        await engine.dispose()

    # reconcile_libraries contains a failing library rather than raising, so
    # without this the process exits 0 after a library failed and a cron
    # wrapper watching the exit code never sees it.
    if result.failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
