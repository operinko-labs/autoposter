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

from plexapi.server import PlexServer
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.reconcile import reconcile_content_ratings
from autoposter.config.loader import load_config
from autoposter.config.schema import Config, Secrets
from autoposter.db.base import make_engine, make_session_factory

CONFIG_PATH = Path(os.environ.get("AUTOPOSTER_CONFIG", "/config/autoposter.yaml"))
LIBRARY_TYPES = {"movie": "Movie", "show": "Show"}

logger = logging.getLogger(__name__)


async def _reconcile_libraries(session: AsyncSession, server, config: Config) -> None:
    """Reconcile every configured library, committing after each one.

    Committing per library -- rather than once at the end -- means a failure
    on a later library cannot discard an earlier library's rows after its
    Plex writes have already landed, which would force a full rewrite next
    run instead of the cheap no-op the hash gate is meant to give.
    """
    for name in config.collections.libraries:
        section = server.library.section(name)
        library_type = LIBRARY_TYPES.get(section.type)
        if library_type is None:
            logger.info("skipping %r: unsupported library type %r", name, section.type)
            continue

        actions = await reconcile_content_ratings(
            session, section, name, library_type,
            config.collections.ownership_label,
            dry_run=not config.collections.apply_to_plex,
        )
        logger.info("%s: %d action(s)", name, len(actions))
        for action in actions:
            logger.info("   %s", action)
        await session.commit()


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
        async with session_factory() as session:
            await _reconcile_libraries(session, server, config)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
