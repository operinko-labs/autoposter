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
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from autoposter.api.auth import require_session
from autoposter.collections.playlists import library_scope, reconcile_playlists
from autoposter.config.overrides import load_overrides_document
from autoposter.db.models import EventLog, ManagedPlaylist
from autoposter.db.models import Session as SessionModel

logger = logging.getLogger(__name__)

router = APIRouter()

# Action strings are built by the pass, but not all of them are safe to echo: a
# provider step reports the source it could not fetch, and provider URLs carry
# credentials often enough that none of them reaches a response. The same rule
# ``api/collections_builders.py`` applies, and the same one the engine applies
# to a builder's exception one layer in.
_URL = re.compile(r"https?://\S+")


def _redact(action: str) -> str:
    return _URL.sub("<url>", action)


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
    return {
        "libraries": library_scope(config),
        "definitions": [
            {
                "title": definition.title,
                "builder": definition.builder,
                "params": definition.params,
                "libraries": definition.libraries,
                "sync_mode": definition.sync_mode,
                "builder_level": definition.builder_level,
                "provenance": provenance,
            }
            for definition in config.playlists.definitions
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
                "actions": [_redact(action) for action in result.actions],
            }
            for result in run.playlists
        ],
        "actions": [_redact(action) for action in run.actions],
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
