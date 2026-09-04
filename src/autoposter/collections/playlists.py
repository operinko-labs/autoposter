"""Reconcile playlists against a source-ordered item list spanning libraries.

A playlist is a list collection with three things taken away and one added.

**Taken away.** It belongs to no library -- Plex fills a ``Playlist``'s
``librarySectionID`` only for radio playlists -- so it cannot live inside
``service.reconcile_libraries``' per-library loop, whose whole point is a
per-library commit boundary there is no library to draw here. It has no display
mode, no sort title, no hub and no member labels, so most of
``CollectionDefinition`` has nothing to mean on it (``PlaylistDefinition``
refuses those keys by name). And -- the one that decides this module's design
-- **it has no Plex labels at all**: ``plexapi.playlist.Playlist`` is not a
``LabelMixin`` (pinned in ``tests/test_plexapi_playlist_contract.py``), so
every ownership, adoption and protection predicate in
``collections/reconcile.py`` is uncompilable here. They are label-typed end to
end.

**Added.** Its members come from several libraries at once.

## Ownership

**A playlist is ours if and only if a ``managed_playlists`` row's
``plex_rating_key`` names a playlist currently on the server.** Not the title.
``engine._sweep``'s docstring already refuses title matching for collections --
"the row alone can name a collection somebody else recreated under that title"
-- and for a playlist there is no second half (the label) to pair the row with,
so the row carries Plex's own identity for the object instead. Two consequences,
both deliberate:

- a playlist somebody else created under one of our titles is **never** ours.
  It is reported and left alone, and no second playlist is created beside it --
  Plex permits duplicate titles, so "create ours anyway" was available and is
  refused;
- one of ours that an operator **renamed** in Plex is still ours, because a
  rename does not move the rating key. The rename is reported, not reverted:
  the title an operator typed into Plex is not this pass's to overwrite, and
  98a ships no adoption ceremony that would justify it.

There is deliberately **no adoption and no protect-labels analogue**. A
Kometa-made playlist carries no marker to adopt from, and a playlist this
service holds no row for is simply never touched -- the same outcome protection
buys, by a different route.

## What is reused rather than rewritten

``member_diff``, ``_enforce_order`` and ``_members_hash`` are imported from
``collections/lists.py`` verbatim. All three are label-free already, and the
contract file pins the two facts that license it: ``Playlist.moveItem`` has the
same signature as ``Collection.moveItem``, and ``Playlist.items()`` is the same
kind of cached snapshot that only ``reload()`` invalidates. Copying them would
be two implementations of one behaviour.

Two costs are disclosed rather than discovered. ``Playlist.removeItems`` issues
**one DELETE per item**, unlike the collection form's single request -- which is
another reason the pass diffs rather than recreating, and why Kometa's
delete-and-recreate shape was not ported. And each library in scope costs one
``build_owned_index`` walk (measured at 1,954 movies in 2.8 s against
production), paid at most once per library per pass and only when there is a
definition to build.

## What this module does NOT do, named rather than left to be discovered

- **``filters:``** is refused on a playlist definition by name
  (``_REFUSED_PLAYLIST_FIELDS``). The post-builder filter stage lives inside
  ``engine._run_one`` with its tier-2 prefetch, and a second copy of an
  oracle-proven stage was judged worse than the gap. Roadmap row 98's ledger
  records it.
- **``BuilderResult.summary`` is ignored.** A builder may derive its own
  summary -- ``builders/base.py`` calls it "only used when the definition does
  not set one" -- and ``engine._run_one`` resolves it through ``_summary_for``.
  This pass reads ``result.ids`` and ``result.level`` and nothing else, so a
  chart or award builder feeding a playlist contributes no summary and a
  playlist's summary is whatever its definition writes, or none. That is the
  second declared gap, and it is in the ledger beside the first.
- **Cross-library twins both enter.** Deduplication is by Plex item
  (``resolve._identity`` is ``str(item.ratingKey)``), so a film held in both a
  4K and an HD section can appear twice if the source names a guid each section
  owns separately. Intended -- a Plex item is a Plex item, and Kometa behaves
  the same way. See ``resolve.resolve_external_across``' docstring.
"""
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    PlexSectionAccess,
    SourceClients,
)
from autoposter.collections.engine import _due
from autoposter.collections.lists import _enforce_order, _members_hash, member_diff
from autoposter.collections.playlist_presets import (
    playlist_definitions,
    preset_conflicts,
    presets_needing_mdblist,
)
from autoposter.collections.resolve import build_owned_index, resolve_external_across
from autoposter.collections.service import LIBRARY_TYPES
from autoposter.config.schema import PlaylistDefinition
from autoposter.db.models import EventLog, ManagedPlaylist
from autoposter.providers.cache import ProviderCache

logger = logging.getLogger(__name__)

# What the sweep reports under when it has nothing to report ABOUT -- the
# refusal past the cap belongs to the pass, not to any one playlist, and a real
# title here would read as that playlist being the problem. The collections
# engine's ``SWEEP_TITLE`` for the same reason.
SWEEP_TITLE = "(playlist delete sweep)"


class PlaylistsPassFailed(RuntimeError):
    """Raised by the CALLERS of ``reconcile_playlists`` when a pass failed.

    Never raised by the reconcile itself: failures are contained per definition
    so one bad source cannot stop the rest, and the contained outcome is carried
    in ``PlaylistRun``. The scheduled job re-raises this once the pass has
    finished and its per-definition commits have landed, because ``last_status``
    is derived from whether the job body raised -- the same shape, and the same
    argument, as ``CollectionsPassFailed``.
    """

    # Read by scheduler/core.py's failure branch: this message is BUILT for the
    # served surfaces and is already class-name-only, so the scheduler serves it
    # verbatim instead of narrowing it to this class's name.
    served_detail = True


@dataclass
class PlaylistResult:
    """What one pass did, or would do, to one playlist.

    ``adding``/``removing``/``deleting`` are PREVIEW counts and stay zero on a
    real pass; ``added``/``removed`` are what the pass actually applied. The
    split is ``DefinitionResult``'s, and for its reason: learning the preview
    counts costs a membership read the scheduled pass has no use for.

    ``skipped`` covers every reason nothing was applied -- outside its schedule,
    a failure, an empty resolve, a refusal -- and ``failed`` says which of those
    it was.
    """

    title: str
    libraries: list[str] = field(default_factory=list)
    adding: int = 0
    removing: int = 0
    deleting: int = 0
    unresolved: int = 0
    added: int = 0
    removed: int = 0
    failed: bool = False
    skipped: bool = False
    actions: list[str] = field(default_factory=list)


@dataclass
class PlaylistRun:
    """One playlists pass: every action string, and how each definition fared."""

    actions: list[str] = field(default_factory=list)
    playlists: list[PlaylistResult] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        """The titles whose builder failed or whose scope was refused. A pass
        with any of these is not a success, whatever the action count says --
        roadmap row 115's rule, one subsystem along."""
        return [result.title for result in self.playlists if result.failed]

    @property
    def failed(self) -> bool:
        return bool(self.failures)

    @property
    def summary(self) -> str:
        summary = "playlists: %d action(s)" % len(self.actions)
        if self.failures:
            summary += "; %d playlist(s) failed (%s)" % (
                len(self.failures), ", ".join(self.failures)
            )
        return summary

    @property
    def detail(self) -> str:
        """The summary with the failures named first -- what a scheduled run's
        ``last_detail`` holds and what a notification carries, both truncated,
        so the part that must survive is what broke."""
        if not self.failures:
            return self.summary
        return "failed: %s; %s" % (", ".join(self.failures), self.summary)


def library_scope(config) -> list[str]:
    """The libraries a playlist may resolve members from, in order.

    ``playlists.libraries`` when set, and ``collections.libraries`` otherwise --
    the second is the default rather than a second copy of the list, so the two
    cannot drift. Kometa's own default is the same idea ("every library this run
    processed", modules/builder.py:710-718).
    """
    scope = config.playlists.libraries
    return list(config.collections.libraries if scope is None else scope)


def _libraries_for(config, definition: PlaylistDefinition) -> list[str]:
    return list(definition.libraries) if definition.libraries else library_scope(config)


async def reconcile_playlists(
    session: AsyncSession,
    server,
    config,
    http: httpx.AsyncClient | None = None,
    *,
    dry_run: bool | None = None,
    sources: SourceClients | None = None,
    cache: ProviderCache | None = None,
    run_index: int = 0,
    now: datetime | None = None,
    sweep: bool = True,
    title: str | None = None,
) -> PlaylistRun:
    """Reconcile every configured playlist, committing after each one.

    The sibling of ``service.reconcile_libraries`` rather than a step inside it:
    a playlist belongs to no library, so it cannot be committed under one, and
    the per-library rollback that function relies on would roll back half a
    playlist. Both are called by the same job and the same CLI, in that order.

    **The commit boundary is one definition.** A playlist is the independent
    unit here exactly as a library is there, and for the same reason: a failure
    partway through must not roll back a playlist that already succeeded and
    already wrote to Plex, which would force a full rewrite next pass instead of
    the cheap no-op the hash gate exists to give.

    ``dry_run`` defaults to ``not config.playlists.apply_to_plex``. It is a
    parameter so the preview endpoint can force it on regardless of the setting.

    ``title`` narrows the pass to one definition; the sweep is then skipped,
    because against a subset every definition left out looks unaccounted for.

    ``run_index`` and ``now`` drive schedule gating and are injected rather than
    read here, so a gate is testable without waiting a month.

    Nothing here raises: a failure is contained to its definition and reported.
    The caller decides what a failed pass means -- see ``PlaylistsPassFailed``.

    ``config.playlists.enabled`` is deliberately NOT checked here. The callers
    own that switch, exactly as ``engine.run_library`` leaves
    ``collections.enabled`` to its own callers.
    """
    if dry_run is None:
        dry_run = not config.playlists.apply_to_plex
    now = now or datetime.now(UTC)
    run = PlaylistRun()

    definitions = [
        definition for definition in playlist_definitions(config)
        if title is None or definition.title == title
    ]
    if not definitions and not (sweep and title is None):
        return run

    # A6's report: a switched-on preset an operator definition displaced. Only
    # on a whole pass -- against a single-title run every other definition is
    # out of play, so a conflict line about one of them would be noise.
    if title is None:
        for key, shadowed in preset_conflicts(config):
            run.actions.append(
                "the %r preset is not built: an operator definition already "
                "builds %r" % (key, shadowed)
            )
        # And the other reason a switched-on preset produces nothing: two of
        # the nine sit on ``mdblist_list``, which raises before any request
        # when no MDBList API key is configured. Contained one layer down and
        # reported there as "source returned no items" -- true of the builder,
        # useless to the operator, and the same wrong blame the empty-scope
        # guard below fixes. ``sources.mdblist`` rather than a config read,
        # because the bundle is what the builder itself checks.
        if sources is None or sources.mdblist is None:
            for key in presets_needing_mdblist(config):
                run.actions.append(
                    "the %r preset is skipped: no mdblist key is configured "
                    "for this deployment, so its list cannot be fetched" % key
                )

    # One section object and one owned index per (library, level), built lazily
    # and shared by every definition in the pass. Lazily because a pass with no
    # definition scoped to a library must not pay for that library's walk, and
    # shared because ``build_owned_index`` is the only expensive call here.
    sections: dict[str, object] = {}
    indexes: dict[tuple[str, str], dict] = {}

    def section_for(name: str):
        if name not in sections:
            sections[name] = server.library.section(name)
        return sections[name]

    def index_for(name: str, level: str) -> dict:
        if (name, level) not in indexes:
            indexes[(name, level)] = build_owned_index(section_for(name), level)
        return indexes[(name, level)]

    # The server's playlists, once. Ownership is answered from this map and
    # never by asking the server about a title -- ``server.playlist(title)`` is
    # the Kometa shape this design refuses.
    live = server.playlists()
    by_key = {str(playlist.ratingKey): playlist for playlist in live}

    for definition in definitions:
        try:
            result = await _reconcile_one(
                session, server, definition, config, by_key,
                section_for, index_for, http, sources, cache,
                run_index, now, dry_run,
            )
            await session.commit()
        except Exception as error:
            await session.rollback()
            logger.exception("failed reconciling the playlist %r", definition.title)
            # Class-name-only: a plexapi or provider failure's message commonly
            # carries the URL it failed on -- the operator's base URL, in some
            # shapes a token -- and this string flows through PlaylistRun.detail
            # into scheduled_runs.last_detail, /api/snapshots and notifications.
            # The full message and traceback are in the logger.exception above.
            result = PlaylistResult(
                title=definition.title,
                libraries=_libraries_for(config, definition),
                failed=True, skipped=True,
                actions=["%r: failed (%s)" % (definition.title, type(error).__name__)],
            )
        run.playlists.append(result)
        run.actions += result.actions

    if sweep and title is None:
        try:
            swept = await _sweep_playlists(session, config, by_key, dry_run)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("the playlist delete sweep failed")
            swept = [PlaylistResult(
                title=SWEEP_TITLE, failed=True, skipped=True,
                actions=["the playlist delete sweep failed; see logs for detail"],
            )]
        run.playlists += swept
        for result in swept:
            run.actions += result.actions

    logger.info("playlists: %d action(s)", len(run.actions))
    for action in run.actions:
        logger.info("   %s", action)
    return run


async def _reconcile_one(
    session: AsyncSession,
    server,
    definition: PlaylistDefinition,
    config,
    by_key: dict,
    section_for,
    index_for,
    http,
    sources,
    cache,
    run_index: int,
    now: datetime,
    dry_run: bool,
) -> PlaylistResult:
    """One playlist: gate, refuse, build, resolve, diff, apply."""
    libraries = _libraries_for(config, definition)
    outcome = PlaylistResult(title=definition.title, libraries=list(libraries))

    if not _due(definition, run_index, now):
        outcome.skipped = True
        return outcome

    # L-6: an empty EFFECTIVE scope. ``_libraries_must_not_be_blank`` refuses
    # the explicitly empty list on a definition; this is the inherited one --
    # ``collections.libraries`` has no minimum length, so a definition that
    # omits ``libraries:`` can inherit nothing at all. Contained and named
    # here, rather than reaching ``libraries[0]`` and reporting
    # "failed (IndexError)", which is true and useless.
    if not libraries:
        outcome.failed = True
        outcome.skipped = True
        outcome.actions.append(
            "%r has no library to resolve its members from: the playlists "
            "section's scope is empty. Name libraries on this playlist, set "
            "playlists.libraries, or configure collections.libraries"
            % definition.title
        )
        return outcome

    # A7's real refusal point, and it is here rather than at config load
    # because the config holds library NAMES and nothing that says a name is a
    # Music or Photo section. Checked BEFORE the builder runs and before any
    # Plex write: a refusal at the first ``addItems`` would be a BadRequest
    # mid-pass with some of the playlist already built.
    scoped_types: dict[str, str] = {}
    for name in libraries:
        section = section_for(name)
        library_type = LIBRARY_TYPES.get(getattr(section, "type", None))
        if library_type is None:
            outcome.failed = True
            outcome.skipped = True
            outcome.actions.append(
                "%r is scoped to %r, which is a %r library: plexapi refuses a "
                "playlist whose members are not all one media type -- \"Can not "
                "mix media types when building a playlist\" -- and only movie and "
                "show libraries share one. Narrow this playlist's 'libraries' to "
                "movie and show libraries"
                % (definition.title, name, getattr(section, "type", None))
            )
            return outcome
        # Kept, because the level refusal below needs it and re-deriving it
        # there would be a second reading of the same section objects.
        scoped_types[name] = library_type

    builder = REGISTRY[definition.builder]
    # The bundle, with the FIRST scoped library's accessor bound onto it. Kometa
    # does the same thing for the same reason -- ``self.libraries[0]`` becomes
    # ``self.library`` and configures the playlist (modules/builder.py) -- and
    # there is no better answer: a builder whose source IS the library needs one
    # library, and a playlist names several.
    primary = libraries[0]
    primary_section = section_for(primary)
    bound_sources = replace(
        sources or SourceClients(),
        plex=PlexSectionAccess(
            primary_section, lambda level="item": index_for(primary, level)
        ),
    )
    ctx = BuilderContext(
        library=primary,
        library_type=scoped_types[primary],
        http=http,
        config=definition.params,
        cache=cache,
        run_cache={},
        sources=bound_sources,
        session=session,
        definition=definition,
    )

    try:
        result = await builder.build(ctx)
    except Exception:
        # The containment invariant, verbatim from ``engine._run_one``: the
        # class name goes in the log with the traceback, and nothing derived
        # from the exception reaches an action string or Plex, because a
        # provider error commonly carries the URL it failed on and those carry
        # credentials.
        logger.exception(
            "source failed for the playlist %r (%s)",
            definition.title, definition.builder,
        )
        outcome.failed = True
        result = BuilderResult(ids=[])

    # A10, through ``builder_level``'s existing semantics rather than a silent
    # expansion. Kometa flattens a Show or Season into its episodes without
    # being asked; this service already has an explicit way to say "the members
    # are episodes", so the definition says it and the ids resolve against an
    # index of that level. A builder that self-declares a level and a definition
    # that declares a different one is two answers to one question.
    declared = definition.builder_level
    if declared != "item" and result.level != "item" and declared != result.level:
        outcome.failed = True
        outcome.skipped = True
        outcome.actions.append(
            "%r: builder_level is %r but %r builds %s-level members; nothing was "
            "applied. Remove builder_level, or point this playlist at a builder "
            "that produces %s ids"
            % (definition.title, declared, definition.builder, result.level, declared)
        )
        return outcome
    level = declared if declared != "item" else result.level

    # ``engine._run_one``'s SECOND post-level guard, ported with the first
    # (engine.py, immediately below the block above). A season or an episode
    # exists only in a Show library, and ``index_for(name, level)`` is
    # ``build_owned_index(section, level)`` is ``section.search(libtype=level)``
    # -- which a Movie library answers with nothing at all. Without this the
    # empty index reaches the empty-result law and the pass reports "source
    # returned no items", a SOURCE diagnosis for a SCOPE mistake, and the
    # operator goes looking at the list.
    #
    # Widened by exactly the difference between the two callers: the engine
    # runs per library and sees one ``ctx.library_type``; a playlist names
    # several, so every one in scope is checked and the offenders are named.
    if level != "item":
        not_shows = [name for name in libraries if scoped_types[name] != "Show"]
        if not_shows:
            outcome.failed = True
            outcome.skipped = True
            outcome.actions.append(
                "%r: %s-level members exist only in a Show library, and this "
                "playlist is scoped to %s, where the search would match nothing "
                "at all. Narrow this playlist's 'libraries' so it only names "
                "Show libraries"
                % (definition.title, level, ", ".join(
                    "%r (a %s library)" % (name, scoped_types[name])
                    for name in not_shows
                ))
            )
            return outcome

    resolved = resolve_external_across(
        [index_for(name, level) for name in libraries], result.ids
    )
    outcome.unresolved = resolved.unresolved
    if resolved.unresolved:
        logger.info(
            "playlist %r: %d id(s) no scoped library owns",
            definition.title, resolved.unresolved,
        )
    items = resolved.items
    if definition.limit is not None:
        items = items[: definition.limit]

    # The empty-result law, and it is the FIRST thing that happens after
    # resolution for the reason ``lists.reconcile_list_collection`` states: a
    # failed chart fetch returning nothing would, taken literally, empty a live
    # playlist. Empty means "make no changes", never "remove everything".
    if not items:
        outcome.skipped = True
        outcome.actions.append(
            "%r: source returned no items; leaving the playlist untouched"
            % definition.title
        )
        return outcome

    row = (
        await session.execute(
            select(ManagedPlaylist).where(ManagedPlaylist.title == definition.title)
        )
    ).scalar_one_or_none()
    playlist = by_key.get(row.plex_rating_key) if row is not None else None

    if playlist is None:
        # Not ours -- either no row at all, or a row whose rating key names
        # nothing on the server any more. Before creating, check whether the
        # title is already taken by a playlist we do not own: Plex permits
        # duplicate titles, so "create ours anyway" is available and is refused.
        stranger = next(
            (p for p in by_key.values() if p.title == definition.title), None
        )
        if stranger is not None:
            outcome.skipped = True
            outcome.actions.append(
                "%r already exists on this server and is not ours -- no "
                "managed_playlists row names it. Leaving it untouched; rename it, "
                "or rename this definition" % definition.title
            )
            return outcome
    elif playlist.title != definition.title:
        # Ours, renamed in Plex. Still ours -- the rating key did not move --
        # and the rename is reported rather than reverted.
        outcome.actions.append(
            "%r is titled %r in Plex; still ours by rating key, and the title is "
            "left as the operator set it" % (definition.title, playlist.title)
        )

    wanted = _members_hash(items, definition.summary, definition.sync_mode, None)
    if playlist is not None and row is not None and row.definition_hash == wanted:
        # Unchanged. The pass still CONFIRMED the membership, so the row records
        # the observation with a zero delta -- the collections side stamps the
        # same way, and a NULL here would read as "never reconciled".
        if not dry_run:
            row.member_count = len(items)
            row.last_added = 0
            row.last_removed = 0
            row.last_reconciled_at = func.now()
            await session.flush()
        return outcome

    added = removed = 0
    if dry_run:
        if playlist is None:
            outcome.adding = len(items)
            outcome.actions.append(
                "would create %r with %d item(s) from %s"
                % (definition.title, len(items), ", ".join(libraries))
            )
        else:
            adding, removing = member_diff(playlist, items, definition.sync_mode)
            outcome.adding, outcome.removing = len(adding), len(removing)
            outcome.actions.append(
                "would update %r: +%d -%d"
                % (definition.title, len(adding), len(removing))
            )
        return outcome

    if playlist is None:
        # ``createPlaylist`` raises BadRequest on an empty list, which the
        # empty-result law above has already made unreachable.
        playlist = server.createPlaylist(title=definition.title, items=items)
        by_key[str(playlist.ratingKey)] = playlist
        if definition.summary:
            playlist.editSummary(definition.summary)
        added = len(items)
        outcome.actions.append(
            "created %r with %d item(s)" % (definition.title, len(items))
        )
    else:
        if definition.summary:
            if getattr(playlist, "summary", None) != definition.summary:
                playlist.editSummary(definition.summary)
                outcome.actions.append("updated the summary of %r" % definition.title)
        elif _clear_playlist_summary(playlist):
            # Roadmap row 187's semantics, on the one metadata field a playlist
            # takes. The summary is part of the members hash, so a DELETED
            # ``summary:`` reaches this branch -- and without the clear the new
            # hash would be stored while the old summary stayed on the playlist
            # forever, every later pass short-circuiting on that hash.
            #
            # Unconditional in a way the collection twin cannot be. There,
            # ``summary_asserted`` exists because an absent effective summary
            # may be a ``tmdb_summary:`` pull that failed, and clearing on that
            # fallback would wipe what the last healthy pass wrote. A playlist
            # definition has no such fallback -- ``tmdb_summary`` is refused by
            # name -- so an absent ``summary`` is always an assertion that there
            # is none. The helper does the narrowing that is left.
            outcome.actions.append("cleared the summary of %r" % definition.title)
        adding, removing = member_diff(playlist, items, definition.sync_mode)
        if adding:
            playlist.addItems(adding)
        if removing:
            # Reloaded first: ``removeItems`` turns each item into a
            # playlist-scoped id through ``_getPlaylistItemID``, which walks the
            # CACHED ``items()`` -- a snapshot that the ``addItems`` above left
            # in place. Kometa reloads at the same point for the same reason.
            playlist.reload()
            playlist.removeItems(removing)
        # Order is enforced only under sync. ``_enforce_order`` arranges the
        # playlist to be exactly ``items``, which under append would drag this
        # definition's picks to the front and push whatever else the playlist
        # holds behind them -- a write against members that mode exists not to
        # touch.
        moves = 0 if definition.sync_mode == "append" else _enforce_order(playlist, items)
        added, removed = len(adding), len(removing)
        if adding or removing or moves:
            outcome.actions.append(
                "updated %r: +%d -%d, %d move(s)"
                % (definition.title, added, removed, moves)
            )

    outcome.added, outcome.removed = added, removed
    if row is None:
        row = ManagedPlaylist(
            title=definition.title,
            plex_rating_key=str(playlist.ratingKey),
            definition_hash=wanted,
            libraries=list(libraries),
        )
        session.add(row)
    else:
        row.plex_rating_key = str(playlist.ratingKey)
        row.definition_hash = wanted
        row.libraries = list(libraries)
    row.member_count = len(items)
    row.last_added = added
    row.last_removed = removed
    row.last_reconciled_at = func.now()
    await session.flush()
    return outcome


def _clear_playlist_summary(playlist) -> bool:
    """Clear a summary this service wrote: empty value, lock released.

    ``reconcile._clear_collection_summary``'s counterpart, and simpler in the
    one way that matters: that function hand-rolls a PUT because
    ``Collection.editSummary`` routes through the SECTION and 404s for a
    collection summary. ``Playlist`` overrides ``_edit`` with a PUT straight at
    its own key (pinned in ``tests/test_plexapi_playlist_contract.py``), and
    ``editField`` already builds exactly ``{"summary.value": "",
    "summary.locked": 0}`` from ``("", locked=False)`` -- so the whole clear is
    one library call and this phase writes no hand-rolled request at all.

    **The lock is the marker**, exactly as it is there: every summary this pass
    sets goes through ``editSummary``, which locks by default, so an UNLOCKED
    summary was never ours and is left alone. The disclosed consequence is the
    collection twin's, unchanged -- Plex's own UI locks fields it edits, so a
    hand-edit on a playlist WE manage, whose definition carries no summary, is
    cleared by the next pass that reaches the write path. A managed playlist's
    desired state is its definition; that is the same stance sync-mode
    membership already takes.

    Returns whether a write was issued, so the caller can report it. ``fields``
    is a ``cached_data_property``, read defensively (missing or empty is NOT
    locked) the same way ``reconcile.has_label`` reads ``labels``.
    """
    locked = any(
        entry.name == "summary" and entry.locked
        for entry in (getattr(playlist, "fields", None) or [])
    )
    if not locked:
        return False
    playlist.editSummary("", locked=False)
    return True


def _sweep_name(row, playlist) -> str:
    """How a swept playlist is named in a report and in its audit row.

    ``row.title`` is our key; ``playlist.title`` is what an operator sees in
    Plex. They differ exactly when one of ours was RENAMED there -- which this
    pass reports and never reverts, and ``title`` is the table's unique key so
    the row cannot follow. By the time the definition is removed and the sweep
    reaches it, a report naming only the row would name a title that is not on
    the server, for the one irreversible action this phase performs. So both
    are named, and only when they differ.
    """
    if playlist.title == row.title:
        return repr(row.title)
    return "%r (titled %r in Plex)" % (row.title, playlist.title)


async def _sweep_playlists(
    session: AsyncSession, config, by_key: dict, dry_run: bool
) -> list[PlaylistResult]:
    """Playlists this service owns that no definition builds any more.

    The only place a playlist is deleted, and every guard is here rather than
    spread over the callers -- ``engine._sweep``'s shape, with the label half of
    its predicate replaced by the rating key:

    - a candidate must have a ``managed_playlists`` row **and** that row's
      rating key must name a playlist currently on the server. A row whose
      object is already gone is not a candidate: there is nothing to delete, and
      reporting it as deletable would invite an operator to authorise a deletion
      that cannot happen. Such a row is re-pointed if a definition is ever aimed
      back at its title.
    - ``playlists.delete_unconfigured`` is off by default, and **off means
      reported**.
    - past ``max_deletes`` the sweep refuses **entirely**, with the numbers.
      Deleting "the first five" of a hundred would be the same accident spread
      over twenty passes -- the ``cleanup.max_orphans`` precedent, and the
      collections sweep's own rule.

    There is no protected-label check because there is no label, and none is
    needed: a playlist with no row of ours is never a candidate in the first
    place, which is the same outcome by a different route.
    """
    # Through the same composition the pass runs, never
    # ``config.playlists.definitions`` alone: a preset's playlist would
    # otherwise be created by one half of this module and called an orphan by
    # the other, on the very next pass.
    managed = {definition.title for definition in playlist_definitions(config)}
    rows = (await session.execute(select(ManagedPlaylist))).scalars().all()
    candidates = [
        (row, by_key[row.plex_rating_key])
        for row in rows
        if row.title not in managed and row.plex_rating_key in by_key
    ]
    results: list[PlaylistResult] = []

    if not config.playlists.delete_unconfigured:
        for row, playlist in candidates:
            results.append(_swept(
                row.title,
                "%s: no playlist definition builds it any more; set "
                "playlists.delete_unconfigured to delete it"
                % _sweep_name(row, playlist),
            ))
        return results

    cap = config.playlists.max_deletes
    if len(candidates) > cap:
        results.append(_swept(SWEEP_TITLE, (
            "refusing to delete %d unconfigured playlist(s): more than the "
            "max_deletes cap of %d; nothing was deleted and everything else was "
            "reconciled" % (len(candidates), cap)
        )))
        return results

    for row, playlist in candidates:
        named = _sweep_name(row, playlist)
        if dry_run:
            results.append(_swept(
                row.title,
                "would delete %s: no playlist definition builds it any more" % named,
                deleting=1,
            ))
            continue
        try:
            playlist.delete()
        except Exception:
            # A later candidate's delete is not this candidate's problem, and it
            # must not cost the audit trail of one that already happened --
            # which is why that audit is flushed below before the next
            # candidate's delete() gets a chance to raise.
            #
            # ``failed=True``, and that is load-bearing: without it
            # ``PlaylistRun.failures`` stays empty, the job raises nothing, and
            # ``scheduled_runs.last_status`` records ``ok`` for a pass in which
            # an IRREVERSIBLE operation errored. ``service.reconcile_libraries``'
            # per-library handler is the precedent -- it appends an outcome
            # carrying an error, which is what makes ``ReconcileResult.failed``
            # true. The message is not echoed (class name and traceback go to
            # the logger): a plexapi failure's message carries the base URL and
            # in some shapes a token.
            logger.exception("could not delete the playlist %r", row.title)
            results.append(_swept(
                row.title,
                "failed to delete %s: see logs for detail" % named,
                failed=True,
            ))
            continue
        await session.delete(row)
        session.add(EventLog(
            source="playlists",
            event_type="playlist_deleted",
            # Identity only. The row's stats go with it and the playlist's
            # members were never ours to record. Both titles, because they can
            # differ -- see ``_sweep_name`` -- and an audit row naming a title
            # that was not on the server is not an audit row.
            payload={
                "title": row.title,
                "plex_title": playlist.title,
                "rating_key": row.plex_rating_key,
            },
            outcome="deleted; no playlist definition builds it any more",
        ))
        await session.flush()
        results.append(_swept(
            row.title,
            "deleted %s: no playlist definition builds it any more" % named,
            deleting=1,
        ))
    return results


def _swept(
    title: str, action: str, deleting: int = 0, failed: bool = False
) -> PlaylistResult:
    """One sweep outcome, in the same shape a definition reports -- so a
    would-be-deleted playlist renders as another row in the preview rather than
    a second kind of thing.

    ``title`` is always the ROW's title, because that is this result's identity
    and what ``PlaylistRun.failures`` names. Which title(s) the operator READS
    is ``_sweep_name``'s answer, and it is already baked into ``action``.
    """
    return PlaylistResult(
        title=title, deleting=deleting, skipped=True, failed=failed, actions=[action]
    )
