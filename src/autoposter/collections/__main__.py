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

import httpx
from plexapi.server import PlexServer

from autoposter.collections.playlists import reconcile_playlists
from autoposter.collections.service import build_source_clients, reconcile_libraries
from autoposter.config.loader import DEFAULT_CONFIG_PATH
from autoposter.config.overrides import load_effective_config
from autoposter.config.schema import Secrets
from autoposter.db.base import make_engine, make_session_factory
from autoposter.facts.tmdb_facts import TMDBFactsClient
from autoposter.providers.cache import ProviderCache
from autoposter.servers.registry import PLEX_REQUIRED

CONFIG_PATH = DEFAULT_CONFIG_PATH

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx logs one INFO line per request carrying the FULL url, and this
    # process builds the same SourceClients bundle main.py does -- Tracearr's
    # base_url included -- so the same reasoning that silences httpx there
    # (main.py:75-81) binds here too.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    secrets = Secrets.from_env()

    # The database comes first now: the config the operator is actually
    # running is the YAML with the UI's overrides merged over it, and reading
    # those needs a session. Nothing here needed the config to build the
    # engine -- the database URL is a secret, not a config setting -- so this
    # is the same work in a different order.
    engine = make_engine(secrets.database_url)
    session_factory = make_session_factory(engine)
    failed = False
    try:
        async with session_factory() as session:
            config = await load_effective_config(CONFIG_PATH, session)
            if not config.collections.enabled and not config.playlists.enabled:
                logger.info("collections and playlists are disabled in config")
                return

            # config.plex is optional now (a Jellyfin-only deployment). This
            # CLI hard-depends on a real plexapi.PlexServer -- there is no
            # lazy-connect wrapper here (see the module docstring) -- so a
            # missing `plex:` block must be refused loudly before the
            # unguarded `config.plex.url` read below, not crash on it.
            if config.plex is None:
                logger.error(PLEX_REQUIRED)
                raise SystemExit(2)

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
                if config.collections.enabled:
                    result = await reconcile_libraries(
                        session, server, config, http, summaries=summaries,
                        sources=sources, cache=cache,
                    )
                    logger.info(result.summary)
                    failed = result.failed
                else:
                    logger.info("collections are disabled in config")
                    failed = False
                # The sibling pass, run here for the reason the scheduled job
                # runs it: a playlist belongs to no library, so it cannot live
                # inside reconcile_libraries' per-library loop, and both callers
                # of that loop have to call this one too or the CLI would
                # silently reconcile half the config.
                if config.playlists.enabled:
                    playlists = await reconcile_playlists(
                        session, server, config, http, sources=sources, cache=cache,
                    )
                    logger.info(playlists.summary)
                    failed = failed or playlists.failed
    finally:
        await engine.dispose()

    # Neither pass raises on a contained failure, so without this the process
    # exits 0 after a library or a playlist failed and a cron wrapper watching
    # the exit code never sees it.
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
