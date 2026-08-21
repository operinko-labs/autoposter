"""One-shot collection reconciliation: ``python -m autoposter.collections``.

Scheduling belongs to a later phase. This exists so the reconciliation can be
run and inspected now, which matters because the first real run against a
library is the one worth reading carefully before anything is written.

There is no ``session_scope`` or ``build_server`` helper in this codebase.
This mirrors the shape ``main.py`` and ``facts/imdb.py``'s own one-shot entry
point (``_run_cli``) actually use: ``load_config`` takes a path (read from
``AUTOPOSTER_CONFIG``, same as ``main.py``), the session comes from
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

from autoposter.collections.service import reconcile_libraries, summary_has_failure
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.base import make_engine, make_session_factory

CONFIG_PATH = Path(os.environ.get("AUTOPOSTER_CONFIG", "/config/autoposter.yaml"))

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config(CONFIG_PATH)
    if not config.collections.enabled:
        logger.info("collections are disabled in config")
        return

    secrets = Secrets.from_env()
    server = PlexServer(config.plex.url, secrets.plex_token)

    engine = make_engine(secrets.database_url)
    session_factory = make_session_factory(engine)
    try:
        async with session_factory() as session, httpx.AsyncClient() as http:
            summary = await reconcile_libraries(session, server, config, http)
            logger.info(summary)
    finally:
        await engine.dispose()

    # reconcile_libraries contains a failing library rather than raising, so
    # without this the process exits 0 after a library failed and a cron
    # wrapper watching the exit code never sees it.
    if summary_has_failure(summary):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
