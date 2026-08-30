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

from autoposter.arr.client import RADARR, SONARR, ArrClient
from autoposter.collections.builders import SourceClients
from autoposter.collections.builders.credits_family import (
    FAMILY_LABEL_PREFIX as _CREDITS_FAMILY_PREFIX,
)
from autoposter.collections.builders.dynamic import (
    FAMILY_LABEL_PREFIX as _DYNAMIC_FAMILY_PREFIX,
)
from autoposter.collections.builders.facts_family import (
    FAMILY_LABEL_PREFIX as _FACTS_FAMILY_PREFIX,
)
from autoposter.collections.engine import definition_titles, run_library
from autoposter.collections.reconcile import (
    adoptable_labels,
    load_labels,
    protected_label,
)
from autoposter.collections.sources import default_definitions
from autoposter.config.schema import Config, Secrets
from autoposter.facts.mdblist import MDBListClient
from autoposter.providers.cache import ProviderCache
from autoposter.providers.tmdb_lists import TmdbListClient
from autoposter.providers.tracearr import TracearrClient
from autoposter.providers.tvdb import TVDBClient

logger = logging.getLogger(__name__)

LIBRARY_TYPES = {"movie": "Movie", "show": "Show"}

# How a failed library is written into the summary.
FAILURE_MARKER = ": failed ("

# Every family-label prefix this service writes, case-folded. A collection
# carrying one of these was labelled by a family of OURS, whatever the offline
# enumeration below can and cannot name -- see ``_family_labelled``. Folded
# because Plex canonicalises label case (``reconcile._folded_labels``): the
# source prefix is ``autoposter-dynamic: `` and the server stores
# ``Autoposter-dynamic: ``.
FAMILY_LABEL_PREFIXES = tuple(sorted(
    prefix.casefold() for prefix in (
        _DYNAMIC_FAMILY_PREFIX, _FACTS_FAMILY_PREFIX, _CREDITS_FAMILY_PREFIX,
    )
))


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


def _family_labelled(collection) -> bool:
    """Does ``collection`` carry one of OUR families' labels?

    Pure reader -- ``load_labels`` must have been called first, the rule every
    reader in ``reconcile.py`` follows.

    This exists because ``engine.definition_titles`` cannot answer for a
    family. Its smart branch (``engine.py:1183-1184``) asks the builder for a
    ``titles()`` and falls through to the definition's own title when there is
    none, and ``DynamicBuilder`` has none: a family's titles are the LIBRARY's,
    knowable only at run time, which is roadmap row 135's documented boundary
    and stays one -- ``generated_titles`` needs the pass's ``run_cache`` and
    this report is a pure, offline call with no pass around it.

    Measured 2026-08-30: the pass that had just created, labelled and prefixed
    19 genre collections then reported all 19 among "55 prior-tool
    collection(s) left behind". The delete SWEEP was never wrong -- it reads
    ``generated_titles`` through ``engine._family_state`` -- so only the report
    lied, and this is the report-side answer: the family label is on the
    collection, so the report can read what it cannot enumerate.

    Prefix, not the exact labels the configured definitions would produce, and
    deliberately the wider set. This report says "a prior tool left this
    behind". A collection carrying one of our family labels was never a prior
    tool's, whether or not the family that made it is still configured -- an
    orphan from a family since removed from the config is a different report to
    write, and inviting the operator to treat it as Kometa's is simply false.
    """
    return any(
        tag.tag.casefold().startswith(FAMILY_LABEL_PREFIXES)
        for tag in (getattr(collection, "labels", None) or [])
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

    That server-side filter being case-insensitive is exactly why
    ``adoptable_labels`` is applied to ``adopt_from`` here: an entry that is a
    case-variant of our own ownership label asks the server for every
    collection this service owns and gets all of them.

    The labels ARE read, once, for each candidate that would otherwise be
    reported -- the family check needs them and a family label cannot be asked
    for server-side, because it is matched by prefix. That is one ``reload()``
    per REPORTED title, not per collection in the library, which is the cost
    the paragraph above refuses.
    """
    candidates: dict[str, object] = {}
    for label in adoptable_labels(
        config.collections.adopt_from, config.collections.ownership_label
    ):
        for collection in section.collections(label=label):
            candidates.setdefault(collection.title, collection)

    managed = _managed_titles(candidates.values(), library_type, config)
    protect_labels = config.collections.protect_labels

    leftovers = []
    for title, collection in candidates.items():
        if title in managed:
            continue
        load_labels(collection)
        if _family_labelled(collection):
            # One of ours, built by a family whose titles no offline
            # enumeration can name. Not a prior tool's leftover.
            continue
        if protected_label(collection, protect_labels) is not None:
            # Maintainerr's collections also carry the prior tool's label.
            # Reporting one as "left behind" invites the operator to act on
            # the collection this service works hardest never to touch.
            continue
        leftovers.append(title)
    return sorted(leftovers)


def build_source_clients(
    config: Config,
    secrets: Secrets,
    http: httpx.AsyncClient,
    cache: ProviderCache | None = None,
) -> SourceClients:
    """The pass's clients, built where the config and the secrets both are.

    Called once per pass rather than once per process, unlike the artwork
    providers: ``radarr``/``sonarr`` are live-editable config (they are not in
    ``config/live.py``'s ``FROZEN_SECTIONS``), so a bundle built at startup
    would tell an operator who just enabled Radarr that Radarr is not
    configured until the next restart. Building it costs no network -- every
    client here defers its first request, and ``plex_account`` is a factory
    precisely because ``MyPlexAccount`` does not.

    Absent means None: an unconfigured service is not a stand-in that answers
    nothing, it is nothing, and the builder that needed it raises. See
    ``SourceClients``.
    """
    ttl = config.providers.cache_ttl_seconds
    return SourceClients(
        # TMDb's token is a hard secret (``Secrets.tmdb_token``) so it is
        # normally present -- but a blank one is still checked here rather
        # than trusted, because a client holding "" would turn every TMDb
        # definition into a 401 the operator has to read out of a log instead
        # of "TMDb is not configured".
        tmdb=(
            TmdbListClient(
                secrets.tmdb_token, http, cache=cache, cache_ttl_seconds=ttl
            )
            if secrets.tmdb_token else None
        ),
        # MDBList: None rather than the ``NullMDBListClient`` app.py falls
        # back to. That stand-in exists so one metadata field can go missing
        # quietly; a list builder handed one would fail on a missing attribute
        # instead of reporting that MDBList is not configured.
        mdblist=(
            MDBListClient(
                secrets.mdblist_apikey, http, cache=cache, cache_ttl_seconds=ttl
            )
            if secrets.mdblist_apikey else None
        ),
        # TVDb: same blank-key guard as tmdb/mdblist above, for the same
        # reason -- a client holding "" would 401 instead of reporting "not
        # configured" the way ``tvdb_list``'s own refusal already documents.
        tvdb=(
            TVDBClient(secrets.tvdb_apikey, http, cache=cache, cache_ttl_seconds=ttl)
            if secrets.tvdb_apikey else None
        ),
        radarr=_arr_client(config.radarr, secrets.radarr_apikey, RADARR, http),
        sonarr=_arr_client(config.sonarr, secrets.sonarr_apikey, SONARR, http),
        tracearr=_tracearr_client(config.tracearr, secrets.tracearr_apikey, http),
        plex_account=_plex_account_factory(secrets.plex_account_token),
        # Config, not a client -- ``text_file`` resolves its ``path`` under
        # this root and contains it there. Taken from the config here for the
        # same reason every client is: this is the layer that has one.
        manual_assets_root=config.manual_assets_root,
    )


def _arr_client(service, api_key: str, kind, http: httpx.AsyncClient) -> ArrClient | None:
    """One Radarr/Sonarr client, or None if this deployment has no such service.

    Both halves matter: ``enabled`` is the operator's switch, and a blank
    ``base_url`` or api key is a half-configured service whose every request
    would fail with a confusing error rather than "not configured".
    """
    if not (service.enabled and service.base_url and api_key):
        return None
    return ArrClient(http, service.base_url, api_key, kind)


def _tracearr_client(
    service, api_key: str, http: httpx.AsyncClient
) -> TracearrClient | None:
    """One Tracearr client, or None if this deployment has no such service.

    The ``_arr_client`` triple, for the same reasons: ``enabled`` is the
    operator's switch, and a blank ``base_url`` or api key is a half-configured
    service whose every request would fail with a confusing error rather than
    with "not configured".
    """
    if not (service.enabled and service.base_url and api_key):
        return None
    return TracearrClient(http, service.base_url, api_key)


def _plex_account_factory(token: str):
    """A callable that connects to plex.tv, or None when there is no token.

    Lazy on purpose. ``MyPlexAccount(token=...)`` performs the request in its
    constructor, so building it here would put a plex.tv round trip -- and a
    plex.tv outage -- in the path of every pass, including passes with no
    account-scoped definition in them at all.
    """
    if not token:
        return None

    def account():
        from plexapi.myplex import MyPlexAccount

        return MyPlexAccount(token=token)

    return account


async def reconcile_libraries(
    session: AsyncSession,
    server,
    config: Config,
    http: httpx.AsyncClient,
    run_index: int = 0,
    summaries=None,
    sources: SourceClients | None = None,
    cache: ProviderCache | None = None,
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

    ``summaries`` is the TMDB facts client a ``tmdb_summary:`` definition
    borrows its summary through. Optional everywhere: without it such a
    definition reports that it could not, and every other definition is
    unaffected.

    ``sources`` and ``cache`` are threaded the same way and for the same
    reason -- see ``build_source_clients``, which is what the callers with
    secrets in hand build the bundle with. Both are optional here so a caller
    that has neither still reconciles everything that needs neither.
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
                summaries=summaries, sources=sources, cache=cache,
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
            # Class-name-only (rows 136/188): a plexapi or provider failure's
            # message commonly carries the URL it failed on -- the operator's
            # base URL, in some shapes a token -- and this string flows through
            # ReconcileResult.detail -> CollectionsPassFailed ->
            # scheduled_runs.last_detail (served by /api/snapshots) and into
            # notifications. The full message and traceback are in the
            # logger.exception line above, where the host-only rule applies --
            # the same treatment the preview endpoint's _library_failure and
            # the engine's builder-exception rule already give this exact
            # failure shape.
            result.libraries.append(
                LibraryOutcome(library=name, error=type(error).__name__)
            )

    return result
