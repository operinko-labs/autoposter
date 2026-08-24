"""Cross-library orchestration shared by the one-shot CLI and the scheduler.

``reconcile_libraries`` is the reconciliation sequence both
``python -m autoposter.collections`` and the scheduled collections job run:
for each configured library, run every collection definition -- the shipped
Common Sense buckets, IMDb charts and Oscars collections, plus whatever the
operator has configured -- through the builder engine.
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

from autoposter.collections.engine import definition_titles, run_definitions
from autoposter.collections.reconcile import load_labels, protected_label
from autoposter.collections.sources import default_definitions
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


def _library_definitions(config: Config, library_type: str) -> list:
    """Everything a pass over this library reconciles: defaults, then config.

    The operator's definitions are appended rather than merged, so an empty
    ``definitions:`` list is exactly what shipped.
    """
    return [*default_definitions(config, library_type), *config.collections.definitions]


def _managed_titles(collections, library_type: str, config: Config) -> set[str]:
    """Every title this service manages for one library.

    Enumerated from the same definitions the pass runs, so a collection cannot
    be built by one and called abandoned by the other. ``collections`` is only
    read to recover dynamically-named titles (the Oscars years), so the caller
    may pass the subset it is about to test rather than the whole library: a
    title absent from that subset cannot be reported.

    A definition targeting *another* library still contributes its title here.
    That is the safe direction: the cost is a same-named collection in this
    library going unreported, where the alternative is inviting the operator to
    delete something a sibling library manages.
    """
    return definition_titles(
        _library_definitions(config, library_type), collections, library_type, config
    )


def unmanaged_prior_collections(section, library_type: str, config: Config) -> list[str]:
    """Titles carrying a prior tool's label that this service does not manage.

    Reports titles only -- there is no argument for a service deciding what to
    do with a collection it does not manage, so nothing here modifies, claims
    or deletes anything.

    The prior-tool label is matched by the *server*, not here: ``labels`` is a
    ``cached_data_property`` that ``section.collections()`` never populates, so
    reading it per collection would mean a ``reload()`` GET for each of the
    library's 305 collections on every pass -- and this report is gated by
    neither ``adopt`` nor ``apply_to_plex``, so a scheduled no-op pass would
    pay all 276 of them. ``LibrarySection.collections(label=...)`` forwards the
    keyword to ``search``, which validates it into a server-side filter
    argument (pinned in ``tests/test_plexapi_collection_contract.py``), so one
    filtered request per ``adopt_from`` entry returns exactly the candidates.

    An unlabelled collection carries no prior-tool label, so the operator's
    hand-made collections and Plex's own franchise collections are never
    returned by that filter in the first place.
    """
    candidates: dict[str, object] = {}
    for label in config.collections.adopt_from:
        for collection in section.collections(label=label):
            candidates.setdefault(collection.title, collection)

    managed = _managed_titles(candidates.values(), library_type, config)
    protect_labels = config.collections.protect_labels

    leftovers = []
    for title, collection in candidates.items():
        if title in managed:
            continue
        if protect_labels:
            # Maintainerr's collections also carry the prior tool's label.
            # Reporting one as "left behind" invites the operator to act on
            # the collection this service works hardest never to touch.
            load_labels(collection)
            if protected_label(collection, protect_labels) is not None:
                continue
        leftovers.append(title)
    return sorted(leftovers)


async def reconcile_libraries(
    session: AsyncSession,
    server,
    config: Config,
    http: httpx.AsyncClient,
    run_index: int = 0,
) -> str:
    """Reconcile every configured library, committing after each one.

    Returns a summary such as ``"Movies: 3 action(s); TV Shows: 0
    action(s)"``. A library that fails is logged and recorded as ``"<name>:
    failed (<error>)"`` in the summary rather than aborting the remaining
    libraries.

    ``run_index`` is which pass this is, for definitions gated to every Nth --
    the scheduler derives it (``scheduler/jobs.py``). A hand-run pass leaves it
    at 0, which runs everything: someone who ran the CLI meant to.
    """
    summaries: list[str] = []
    for name in config.collections.libraries:
        try:
            section = server.library.section(name)
            library_type = LIBRARY_TYPES.get(section.type)
            if library_type is None:
                logger.info("skipping %r: unsupported library type %r", name, section.type)
                continue

            actions = await run_definitions(
                session, section, name, library_type,
                _library_definitions(config, library_type),
                config, http=http, run_index=run_index,
            )

            logger.info("%s: %d action(s)", name, len(actions))
            for action in actions:
                logger.info("   %s", action)

            await session.commit()

            # Below the commit, and with its own handler: the reconcile's Plex
            # writes have already landed, so a read failure in this purely
            # diagnostic scan must not reach the handler below and roll back
            # the library's ManagedCollection rows. Losing them would make the
            # next pass rewrite the whole library -- exactly what this
            # function's per-library commit boundary exists to prevent.
            leftovers: list[str] = []
            try:
                leftovers = unmanaged_prior_collections(section, library_type, config)
            except Exception:
                logger.exception("failed scanning %r for prior-tool leftovers", name)

            if leftovers:
                logger.info(
                    "%s: %d prior-tool collection(s) left behind: %s",
                    name, len(leftovers), ", ".join(leftovers),
                )

            summary = "%s: %d action(s)" % (name, len(actions))
            if leftovers:
                summary += "; %d left behind (%s)" % (len(leftovers), ", ".join(leftovers))
            summaries.append(summary)
        except Exception as error:
            await session.rollback()
            logger.exception("failed reconciling %r", name)
            summaries.append("%s%s%s)" % (name, FAILURE_MARKER, error))

    return "; ".join(summaries)
