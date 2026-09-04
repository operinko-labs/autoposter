"""The playlist endpoints: the definitions listing, the preview, and the delete.

Its own module for the reason ``api/collections_builders.py`` is one -- it
drives an engine directly -- and for a second one: the ownership predicate here
is not the collections one and must not be reachable from a handler that
believes it is. Collections are owned by a Plex label plus a row; a playlist is
owned by a ``managed_playlists`` row whose ``plex_rating_key`` names a playlist
currently on the server, and nothing else.

The ``managed_playlists`` LISTING (``GET /api/playlists``) lives in
``api/routes.py`` beside ``GET /api/collections``, which it mirrors exactly:
both are plain column listings over a table, and that module is where those
live.
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from autoposter.api.auth import require_session
from autoposter.collections.playlists import library_scope, reconcile_playlists
# The MODULE, not its names. ``BY_TITLE`` is a table a test may swap
# (``tests/test_playlists.py``'s ``a_preset`` monkeypatches the module
# attribute), and a ``from … import BY_TITLE`` here would bind the original
# dict at import time and quietly ignore the patch. The functions read the
# module globals at call time and would have been safe either way; one import
# for all three keeps the rule visible instead of split -- and it keeps
# ``playlist_definitions`` (the composition) unambiguous next to
# ``playlist_definitions`` (this module's own handler, same name).
from autoposter.collections import playlist_presets
from autoposter.config.overrides import load_overrides_document
from autoposter.db.models import EventLog, ManagedPlaylist
from autoposter.db.models import Session as SessionModel
from autoposter.redact import redact_urls

logger = logging.getLogger(__name__)

router = APIRouter()


def _enabled(request: Request):
    """The config, once this instance is actually running playlists.

    ``playlists.enabled`` is what an operator switches off to stop this service
    touching playlists at all. The write endpoints would otherwise keep
    working, which is exactly the guarantee that switch is supposed to give.
    The two read-only listings are deliberately NOT behind this, for the
    collections catalog's reason: reading config changes nothing, and an
    operator about to switch the section back on would otherwise be shown an
    empty page instead of the thing they came to configure.
    """
    config = request.app.state.config_holder.current
    if not config.playlists.enabled:
        raise HTTPException(
            status_code=503,
            detail="playlists are disabled in the config for this instance",
        )
    return config


async def _connect(request: Request):
    """The Plex server, or the 503 a replica without one answers with."""
    factory = request.app.state.plex_server_factory
    if factory is None:
        raise HTTPException(
            status_code=503, detail="this instance is not connected to Plex"
        )
    # Connecting is a blocking plexapi call, so it goes off the loop -- the
    # scheduled job does the same with the same factory.
    return await asyncio.to_thread(factory)


@router.get("/playlists/definitions")
async def playlist_definitions(
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """The operator-configured playlist definitions, without running anything.

    The collections analogue's listing, field for field where the fields exist:
    ``playlists.definitions`` off the RUNNING config, no Plex read and no
    engine run.

    ``provenance`` is per entry in shape and uniform in value by construction --
    the overrides layer replaces a list WHOLESALE, so when the stored document
    carries ``playlists.definitions`` every effective entry came through it
    ("override"), and when it does not, every entry is the mounted file's
    ("file"). That distinction is what will make 98b's panel writes safe:
    "file" rows must never be copied into the stored document, because copying
    them would freeze today's file values against every future edit of the YAML.

    ``libraries`` is the section's own scope -- what a create form offers as
    scope checkboxes, in the order a definition would search them.

    ``provenance`` has a third value the collections listing has no analogue
    for: ``"preset"``, for a row ``playlists.presets`` expands into. A preset
    row is stored in no document at all, so it is neither editable nor
    removable through the overrides layer -- it is switched off by removing its
    key, which the settings page's ``string[]`` editor already does.
    ``preset_key`` names that key and is null on every other row.

    ``preset_conflicts`` is A6's report: a switched-on preset whose title an
    operator definition already builds is DROPPED from the expansion (one
    playlist title is one playlist, so two definitions on it would flap the
    members hash forever) and named here instead, so the panel can say which
    key stopped building and why.

    Not behind ``_enabled``, for the catalog's reason.
    """
    config = request.app.state.config_holder.current
    async with request.app.state.session_factory() as session:
        try:
            stored = await load_overrides_document(session)
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail="config overrides row is corrupt (not a JSON object); fix or delete it",
            ) from exc
    section = stored.get("playlists")
    overridden = isinstance(section, dict) and "definitions" in section
    provenance = "override" if overridden else "file"

    def _row(definition, source: str, preset_key: str | None) -> dict:
        """One definition as the panel reads it.

        ``summary``, ``limit`` and ``schedule`` are here and were not in 98a
        (L-7): the collections listing omits the same three, which made it
        parity at the time and makes it a blocker now -- the edit form cannot
        show a field the listing does not carry, and it must never rebuild an
        entry from this projection anyway (that is the freezing hazard
        ``api/overrides.ts`` opens with). These are for DISPLAY; the write is
        always seeded from the stored overrides document.

        Nothing here can carry a URL or a token: every value is a title, a
        builder key, a library name, a count, a literal from a closed set, or
        the definition's own ``params`` -- which the collections listing has
        served verbatim since row 137, no registered builder's params model
        carrying a URL, key or token field.
        """
        return {
            "title": definition.title,
            "builder": definition.builder,
            "params": definition.params,
            "libraries": definition.libraries,
            "summary": definition.summary,
            "limit": definition.limit,
            # The WHOLE model, defaults included: a definition whose YAML says
            # ``schedule: {every_n_runs: 3}`` is served as
            # ``{"every_n_runs": 3, "months": null}``.
            # There is no exclude-anything precedent to copy
            # -- the collections listing serves no ``schedule`` at all, and the
            # one place this codebase puts a nested config model on a response
            # is ``GET /api/config``, which does a plain
            # ``config.model_dump(mode="json")`` (``api/routes.py``). Same call
            # here, so the served shape is the schema's shape and the frontend
            # type does not have to guess which keys survive.
            "schedule": (
                None if definition.schedule is None
                else definition.schedule.model_dump(mode="json")
            ),
            "sync_mode": definition.sync_mode,
            "builder_level": definition.builder_level,
            "provenance": source,
            "preset_key": preset_key,
        }

    # THE SAME COMPOSITION THE PASS AND THE SWEEP RUN, not a third one. This
    # handler could re-expand the presets and append
    # ``config.playlists.definitions`` itself; it would agree with
    # ``playlist_definitions`` today and be free to drift tomorrow, and the
    # panel's ``overrideOrdinal`` maps a listing row back to its stored entry
    # by counting the "override" rows before it -- which is only correct while
    # the two orders are the same list. So the list is composed once, here as
    # everywhere, and this handler's only extra job is per-row provenance.
    #
    # ``playlist_definitions`` appends the operator's definitions to the
    # presets (its docstring, and ``test_presets_come_first_and_operator_
    # definitions_are_appended``), so the last ``len(config.playlists.
    # definitions)`` rows are theirs and everything before them expanded from
    # ``playlists.presets``. Counting the boundary is what keeps the split
    # honest without re-deriving either half.
    composed = playlist_presets.playlist_definitions(config)
    boundary = len(composed) - len(config.playlists.definitions)
    return {
        "libraries": library_scope(config),
        "definitions": [
            # Every row before the boundary came out of ``preset_definitions``,
            # so its title is a key of the table by construction -- this
            # subscript cannot raise, and reaching the table THROUGH the module
            # is what keeps a swapped table (a test's, and one day a wider one)
            # reaching this half too.
            _row(definition, "preset", playlist_presets.BY_TITLE[definition.title].key)
            if index < boundary
            else _row(definition, provenance, None)
            for index, definition in enumerate(composed)
        ],
        "preset_conflicts": [
            {"key": key, "title": shadowed}
            for key, shadowed in playlist_presets.preset_conflicts(config)
        ],
    }


class PreviewRequest(BaseModel):
    """Which definition to preview. Omitting it previews every one."""

    title: str | None = None


@router.post("/playlists/preview")
async def preview_playlists(
    body: PreviewRequest,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Run the playlists pass as a dry run and report what it would do.

    The real entry point, with ``dry_run`` FORCED on rather than inherited from
    ``playlists.apply_to_plex``: a preview that wrote because the setting
    happened to be on would be the one thing an operator pressing preview must
    never discover.

    **What carries that guarantee is the dry-run branches, not a trailing
    rollback, and this docstring says so rather than claiming otherwise.**
    ``reconcile_playlists`` commits per definition -- that is its whole commit
    boundary, and it does so whether or not ``dry_run`` is set -- so a
    ``session.rollback()`` after it returns would undo nothing that had already
    landed. The reason nothing lands is one level in: ``_reconcile_one``'s
    ``if dry_run:`` branch returns before every row write, and
    ``_sweep_playlists``' returns a "would delete" line without
    ``session.delete(row)``. Those two branches are the guarantee, they are
    pinned by ``test_the_gate_off_writes_nothing_and_says_what_it_would_do``
    and ``test_the_sweep_reports_an_orphan_while_delete_unconfigured_is_off``,
    and the assertion here is the endpoint-level one:
    ``test_the_preview_is_a_dry_run_even_with_apply_to_plex_on`` reads the
    ``managed_playlists`` table back through a SEPARATE session and finds it
    empty.

    (The collections precedent does not have this shape at all: its preview
    drives ``run_library``, which contains no commits -- ``reconcile_libraries``
    owns that boundary and the preview never calls it. Saying "the rollback
    makes it structural" here would have been borrowing a sentence from a
    function with a different design.)
    """
    config = _enabled(request)
    server = await _connect(request)
    http = request.app.state.http
    cache = getattr(request.app.state, "provider_cache", None)
    secrets = getattr(request.app.state, "secrets", None)
    from autoposter.collections.service import build_source_clients

    async with request.app.state.session_factory() as session:
        try:
            sources = (
                build_source_clients(config, secrets, http, cache)
                if secrets is not None else None
            )
            run = await reconcile_playlists(
                session, server, config, http,
                dry_run=True, sources=sources, cache=cache,
                sweep=body.title is None, title=body.title,
            )
        except Exception as error:
            # The exception's CLASS only -- whatever a Plex or provider failure
            # carries (a URL with a token in it, most of the time) stays in the
            # log, the same rule the pass applies one layer in.
            logger.exception("could not preview the playlists pass")
            await session.rollback()
            raise HTTPException(
                status_code=502,
                detail="could not preview playlists (%s)" % type(error).__name__,
            ) from None
        # Housekeeping, not the guarantee: the pass has already committed per
        # definition (nothing, on a dry run -- see the docstring), and this
        # discards whatever identity map is left before the session closes.
        await session.rollback()

    return {
        "playlists": [
            {
                "title": result.title,
                "libraries": result.libraries,
                "adding": result.adding,
                "removing": result.removing,
                "deleting": result.deleting,
                "unresolved": result.unresolved,
                "failed": result.failed,
                "skipped": result.skipped,
                "actions": [redact_urls(action) for action in result.actions],
            }
            for result in run.playlists
        ],
        "actions": [redact_urls(action) for action in run.actions],
    }


class DeleteRequest(BaseModel):
    """Delete the playlist ``title`` names. ``confirm`` must be true.

    A confirmation flag rather than a second endpoint, because the thing being
    confirmed is THIS title: a mis-clicked button that deleted the wrong
    playlist would be just as irreversible as one that deleted without asking.
    """

    title: str
    confirm: bool = False


@router.post("/playlists/ops/delete")
async def delete_playlist(
    body: DeleteRequest,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Delete one named playlist, through the ownership predicate.

    The guard the sweep applies, applied here for its reasons: a
    ``managed_playlists`` row must exist for the title AND its
    ``plex_rating_key`` must name a playlist currently on the server. Title
    match alone is never ownership -- the row alone can name a playlist somebody
    else recreated under that title, which is precisely the case this refuses.

    The one guard NOT shared is ``delete_unconfigured``: that setting authorises
    the unattended sweep to decide for itself, and this endpoint is an operator
    deciding. ``confirm: true`` stands in its place.

    **Two outcomes, both 200, told apart by ``retired_orphan``.** If the row's
    rating key names a playlist on the server, that object is deleted and the
    row goes with it (``retired_orphan: false``). If it names nothing, the
    object is already gone and only the row is RETIRED (``retired_orphan:
    true``) -- not a 409, because refusing here would leave that row
    unreachable forever: the sweep deliberately skips it (a candidate must name
    a live playlist, so an operator is never invited to authorise a deletion
    that cannot happen), and ``GET /api/playlists`` would go on listing a
    playlist that does not exist with no route to clean it up. Retiring is not
    a Plex write and cannot become one, so it is exactly what "delete this
    playlist" means in that state.

    No ``EventLog`` for the retire, deliberately: the audit row on the delete
    below records an irreversible action taken against Plex, and a retire is
    bookkeeping about a row of ours describing an object somebody else already
    removed. The whole observable effect is the listing, which stops lying.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=422, detail="deleting %r needs confirm: true" % body.title
        )

    _enabled(request)
    server = await _connect(request)

    async with request.app.state.session_factory() as session:
        row = (
            await session.execute(
                select(ManagedPlaylist).where(ManagedPlaylist.title == body.title)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=409,
                detail="%r has no managed_playlists row, so it is not ours to delete"
                % body.title,
            )

        try:
            live = await asyncio.to_thread(server.playlists)
        except Exception as error:
            logger.exception("could not list playlists")
            raise HTTPException(
                status_code=502,
                detail="could not list playlists on Plex (%s)" % type(error).__name__,
            ) from None
        playlist = next(
            (p for p in live if str(p.ratingKey) == row.plex_rating_key), None
        )
        if playlist is None:
            # Already gone from Plex. Retire the row rather than refuse: see
            # the docstring for why a 409 here would strand it.
            await session.delete(row)
            await session.commit()
            return {
                "actions": [
                    "the playlist %r named (rating key %s) is not on this "
                    "server, so nothing was deleted; retired the "
                    "managed_playlists row that named it"
                    % (body.title, row.plex_rating_key)
                ],
                "retired_orphan": True,
            }

        await asyncio.to_thread(playlist.delete)
        await session.delete(row)
        action = "deleted the playlist %r" % body.title
        session.add(EventLog(
            source="playlists",
            event_type="playlist_deleted",
            # Identity only, never a URL -- the same rule the ops endpoints on
            # the collections side follow.
            payload={"title": body.title, "rating_key": row.plex_rating_key},
            outcome=action,
        ))
        await session.commit()

    return {"actions": [action], "retired_orphan": False}
