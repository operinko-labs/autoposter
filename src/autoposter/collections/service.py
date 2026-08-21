"""Cross-library orchestration shared by the one-shot CLI and the scheduler.

``reconcile_libraries`` is the reconciliation sequence both
``python -m autoposter.collections`` and the scheduled collections job run:
for each configured library, bring the Common Sense rating buckets in line
and, if enabled, do the same for the IMDb chart / Oscars list collections.
Two copies of this sequence would drift apart, so both callers use this one.

Committing per library -- rather than once at the end -- means a failure
partway through cannot roll back a library that already succeeded and
already wrote to Plex, which would force a full rewrite next run instead of
the cheap no-op the hash gate is meant to give. Containing a failure to the
library it happened on, rather than letting it end the pass, means one bad
library cannot stop the rest of the configured libraries from being tried.
"""
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.reconcile import reconcile_content_ratings
from autoposter.collections.sources import build_all
from autoposter.config.schema import Config

logger = logging.getLogger(__name__)

LIBRARY_TYPES = {"movie": "Movie", "show": "Show"}

# How a failed library is written into the summary. Kept as a constant because
# ``summary_has_failure`` reads it back out: the one-shot CLI must exit
# non-zero when any library failed, and a cron wrapper watching the exit code
# is the only thing that will ever notice.
FAILURE_MARKER = ": failed ("


def summary_has_failure(summary: str) -> bool:
    """True when a ``reconcile_libraries`` summary reports any failed library.

    Failures are contained per library rather than raised -- one bad library
    must not stop the rest -- so the return value is the only place the
    outcome survives. Callers that need an exit code ask here.
    """
    return FAILURE_MARKER in summary


async def reconcile_libraries(
    session: AsyncSession, server, config: Config, http: httpx.AsyncClient
) -> str:
    """Reconcile every configured library, committing after each one.

    Returns a summary such as ``"Movies: 3 action(s); TV Shows: 0
    action(s)"``. A library that fails is logged and recorded as ``"<name>:
    failed (<error>)"`` in the summary rather than aborting the remaining
    libraries.
    """
    summaries: list[str] = []
    for name in config.collections.libraries:
        try:
            section = server.library.section(name)
            library_type = LIBRARY_TYPES.get(section.type)
            if library_type is None:
                logger.info("skipping %r: unsupported library type %r", name, section.type)
                continue

            actions = await reconcile_content_ratings(
                session, section, name, library_type,
                config.collections.ownership_label,
                dry_run=not config.collections.apply_to_plex,
                adopt=config.collections.adopt,
                adopt_from=config.collections.adopt_from,
                adopt_removes_prior_label=config.collections.adopt_removes_prior_label,
                separators=config.collections.separators,
                protect_labels=config.collections.protect_labels,
            )

            if config.collections.charts or config.collections.awards:
                actions += await build_all(
                    http, session, section, name, library_type,
                    config.collections.ownership_label, config,
                )

            logger.info("%s: %d action(s)", name, len(actions))
            for action in actions:
                logger.info("   %s", action)
            await session.commit()
            summaries.append("%s: %d action(s)" % (name, len(actions)))
        except Exception as error:
            await session.rollback()
            logger.exception("failed reconciling %r", name)
            summaries.append("%s%s%s)" % (name, FAILURE_MARKER, error))

    return "; ".join(summaries)
