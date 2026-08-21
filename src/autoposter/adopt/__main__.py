"""One-time library adoption: ``python -m autoposter.adopt``.

Prints the ``AdoptionReport`` for each configured library plus a total.
``adopt.apply: false`` (the default) is a dry run: the walk computes and
reports everything it would adopt but writes no rows, so an operator can
read the report -- in particular ``missing_assets`` -- before cutover. See
``deploy/README.md`` for the full cutover procedure.

Follows ``collections/__main__.py``'s shape for building the config, engine,
session factory and Plex server; connecting to Plex is a blocking network
call, so it goes through ``asyncio.to_thread`` here, same as everywhere else
in this module.
"""
import asyncio
import logging
import os
from pathlib import Path

from plexapi.server import PlexServer

from autoposter.adopt.walk import adopt_library
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.base import make_engine, make_session_factory

CONFIG_PATH = Path(os.environ.get("AUTOPOSTER_CONFIG", "/config/autoposter.yaml"))

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config(CONFIG_PATH)
    dry_run = not config.adopt.apply

    secrets = Secrets.from_env()
    server = await asyncio.to_thread(PlexServer, config.plex.url, secrets.plex_token)

    engine = make_engine(secrets.database_url)
    session_factory = make_session_factory(engine)
    try:
        async with session_factory() as session:
            total_items = total_renders = total_missing = total_skipped = 0
            for name in config.adopt.libraries:
                section = await asyncio.to_thread(server.library.section, name)
                report = await adopt_library(session, config, section, dry_run=dry_run)
                logger.info(
                    "%s: %d item(s), %d render(s), %d missing asset(s), %d skipped, by kind %s",
                    name, report.items, report.renders, report.missing_assets,
                    report.skipped, report.by_kind,
                )
                total_items += report.items
                total_renders += report.renders
                total_missing += report.missing_assets
                total_skipped += report.skipped

            logger.info(
                "total: %d item(s), %d render(s), %d missing asset(s), %d skipped%s",
                total_items, total_renders, total_missing, total_skipped,
                " (dry run -- nothing written)" if dry_run else "",
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
