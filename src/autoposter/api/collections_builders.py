"""What a collections pass would do, without doing any of it.

One endpoint: ``POST /api/collections/preview``. It runs the real engine over
the real definitions with ``dry_run`` forced on, and answers with the counts
and the action strings each definition produced. Nothing here writes to Plex or
to the database -- the session is never committed -- so there is no apply flag,
no worker fence and no concurrency question to answer: two operators previewing
at once is two reads.

Its own module rather than another handler in ``routes.py``, for the reason the
6d/6e/7b routers each have one: the substance of it is a Plex-touching
operation with rules of its own (the redaction below, the 503 when this replica
has no Plex connection), and those do not belong scattered through the JSON
handlers.

**Dry run is not a parameter.** ``run_library`` takes ``dry_run`` from
``collections.apply_to_plex`` when it is not told, so an operator with writes
switched on would otherwise get a preview that reconciled the library. It is
passed explicitly here, and that is the only reason this endpoint is safe to
expose at all.
"""
import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from autoposter.api.auth import require_session
from autoposter.collections.engine import run_library
from autoposter.collections.service import LIBRARY_TYPES, library_definitions
from autoposter.db.models import Session as SessionModel

logger = logging.getLogger(__name__)

router = APIRouter()

# Action strings are built here, but not all of them: a poster step reports the
# source it could not fetch, and that source is a URL. Provider URLs carry
# credentials often enough that none of them is echoed into a response -- the
# same rule the engine applies to a builder's exception, one layer out.
_URL = re.compile(r"https?://\S+")


def _redact(action: str) -> str:
    return _URL.sub("<url>", action)


class PreviewRequest(BaseModel):
    """Which definitions to preview. Both filters are optional; omitting both
    previews every definition in every configured library."""

    library: str | None = None
    title: str | None = None


@router.post("/collections/preview")
async def preview_collections(
    body: PreviewRequest,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Run the collections engine as a dry run and report what it would do.

    Per definition: the collection's title and library, how many members would
    be added and removed, whether it would be deleted by the sweep, how many of
    the source's ids this library does not own, and whether the source failed
    or nothing was applied -- plus that definition's own action strings.
    """
    factory = request.app.state.plex_server_factory
    if factory is None:
        raise HTTPException(
            status_code=503, detail="this instance is not connected to Plex"
        )
    config = request.app.state.config_holder.current
    http = request.app.state.http

    # Connecting is a blocking plexapi call, so it goes off the loop -- the
    # scheduled job does the same with the same factory.
    server = await asyncio.to_thread(factory)

    definitions_out: list[dict] = []
    actions: list[str] = []
    async with request.app.state.session_factory() as session:
        for name in config.collections.libraries:
            if body.library is not None and name != body.library:
                continue
            section = server.library.section(name)
            library_type = LIBRARY_TYPES.get(section.type)
            if library_type is None:
                continue

            definitions = library_definitions(config, library_type)
            if body.title is not None:
                definitions = [d for d in definitions if d.title == body.title]
            # The sweep runs only for an unfiltered library, because it reports
            # what *no definition* builds: against a filtered subset every
            # definition left out would look unaccounted for, and the preview
            # would show deletions a real pass would never make.
            run = await run_library(
                session, section, name, library_type, definitions, config,
                http=http, dry_run=True, sweep=body.title is None, preview=True,
            )

            for result in run.definitions:
                definitions_out.append({
                    "title": result.title,
                    "library": result.library,
                    "adding": result.adding,
                    "removing": result.removing,
                    "deleting": result.deleting,
                    "unresolved": result.unresolved,
                    "failed": result.failed,
                    "skipped": result.skipped,
                    "actions": [_redact(action) for action in result.actions],
                })
            actions += [_redact(action) for action in run.actions]
        # Never committed. A dry run writes nothing, but the rollback on the
        # way out of this block is what makes that structural rather than a
        # promise every branch below the engine has to keep.
        await session.rollback()

    return {"definitions": definitions_out, "actions": actions}
