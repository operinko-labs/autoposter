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
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.engine import definition_titles, run_library
from autoposter.collections.reconcile import load_labels, protected_label
from autoposter.collections.sources import default_definitions
from autoposter.config.schema import Config

logger = logging.getLogger(__name__)

LIBRARY_TYPES = {"movie": "Movie", "show": "Show"}

# How a failed library is written into the summary.
FAILURE_MARKER = ": failed ("


class CollectionsPassFailed(RuntimeError):
    """Raised by the *callers* of ``reconcile_libraries`` when a pass failed.

    Never raised by the reconcile itself: failures are contained per library so
    one bad library cannot stop the rest, and the contained outcome is carried
    in ``ReconcileResult``. The scheduled job re-raises this once the pass has
    finished and its per-library commits have landed, because ``last_status``
    is derived from whether the job body raised -- and until it did, a pass
    where every source was dead was recorded as ``ok`` (roadmap row 115).
    """


@dataclass
class LibraryOutcome:
    """One library's result. ``ok`` is the honest answer, not the action count.

    A library is not ok if the pass over it raised (``error``) *or* if any
    definition's source failed (``failed_definitions``). The second is the row
    115 fix: those failures were always contained -- the collection is left
    exactly as it was -- but containing a failure is not the same as it not
    having happened.
    """

    library: str
    actions: list[str] = field(default_factory=list)
    failed_definitions: list[str] = field(default_factory=list)
    leftovers: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.failed_definitions

    @property
    def summary(self) -> str:
        if self.error is not None:
            return "%s%s%s)" % (self.library, FAILURE_MARKER, self.error)
        summary = "%s: %d action(s)" % (self.library, len(self.actions))
        if self.failed_definitions:
            summary += "; %d definition(s) failed (%s)" % (
                len(self.failed_definitions), ", ".join(self.failed_definitions)
            )
        if self.leftovers:
            summary += "; %d left behind (%s)" % (
                len(self.leftovers), ", ".join(self.leftovers)
            )
        return summary


@dataclass
class ReconcileResult:
    """Every library's outcome, and the one-line summary of the whole pass."""

    libraries: list[LibraryOutcome] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(not outcome.ok for outcome in self.libraries)

    @property
    def summary(self) -> str:
        """``"Movies: 3 action(s); TV Shows: 0 action(s)"`` -- what the CLI
        prints and the job records."""
        return "; ".join(outcome.summary for outcome in self.libraries)

    @property
    def detail(self) -> str:
        """The summary with the failures named first.

        Failures first because this is what a scheduled run's ``last_detail``
        holds and what a notification carries, and both are truncated (2000
        characters) -- the part that must survive is what broke.
        """
        broken = [
            "%s%s" % (
                outcome.library,
                "" if not outcome.failed_definitions
                else " (%s)" % ", ".join(outcome.failed_definitions),
            )
            for outcome in self.libraries if not outcome.ok
        ]
        if not broken:
            return self.summary
        return "failed: %s; %s" % ("; ".join(broken), self.summary)


def library_definitions(config: Config, library_type: str) -> list:
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
        library_definitions(config, library_type), collections, library_type, config
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
) -> ReconcileResult:
    """Reconcile every configured library, committing after each one.

    Returns the per-library outcomes (``ReconcileResult``), whose ``summary``
    is the line the CLI prints and the job records: ``"Movies: 3 action(s); TV
    Shows: 0 action(s)"``. A library that fails is logged and recorded as
    ``"<name>: failed (<error>)"`` rather than aborting the remaining
    libraries -- and, since row 115, so is a library where a definition's
    source failed. Nothing here raises on a failure; the caller decides what a
    failed pass means (see ``CollectionsPassFailed``).

    ``run_index`` is which pass this is, for definitions gated to every Nth --
    the scheduler derives it (``scheduler/jobs.py``). A hand-run pass leaves it
    at 0, which runs everything: someone who ran the CLI meant to.
    """
    result = ReconcileResult()
    for name in config.collections.libraries:
        try:
            section = server.library.section(name)
            library_type = LIBRARY_TYPES.get(section.type)
            if library_type is None:
                logger.info("skipping %r: unsupported library type %r", name, section.type)
                continue

            run = await run_library(
                session, section, name, library_type,
                library_definitions(config, library_type),
                config, http=http, run_index=run_index, sweep=True,
            )
            actions = run.actions

            logger.info("%s: %d action(s)", name, len(actions))
            for action in actions:
                logger.info("   %s", action)
            if run.failures:
                logger.warning(
                    "%s: %d definition(s) failed: %s",
                    name, len(run.failures), ", ".join(run.failures),
                )

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

            result.libraries.append(LibraryOutcome(
                library=name, actions=actions,
                failed_definitions=run.failures, leftovers=leftovers,
            ))
        except Exception as error:
            await session.rollback()
            logger.exception("failed reconciling %r", name)
            result.libraries.append(LibraryOutcome(library=name, error=str(error)))

    return result
