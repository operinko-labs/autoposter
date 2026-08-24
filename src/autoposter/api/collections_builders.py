"""The collections engine's operator surface: preview, and the lifecycle ops.

``POST /api/collections/preview`` runs the real engine over the real
definitions with ``dry_run`` forced on, and answers with the counts and the
action strings each definition produced. Nothing in it writes to Plex or to the
database -- the session is never committed -- so there is no apply flag, no
worker fence and no concurrency question to answer: two operators previewing at
once is two reads.

The three ``/collections/ops/*`` endpoints (roadmap row 28) are the opposite
kind of thing, and the difference is deliberate: they **write**, and dry run
does not apply to them. ``collections.apply_to_plex`` gates the *scheduled*
pass, which runs unattended; these run because an operator pressed a button
naming one library and one collection, and a button that did nothing because of
a setting somewhere else would be worse than no button. What they do keep is
every ownership guard the reconcilers use -- the ownership label, a
``managed_collections`` row, a protected label winning over both -- plus an
``events_log`` row each, because a write nobody can find afterwards is not an
operator action, it is a mystery.

Its own module rather than another handler in ``routes.py``, for the reason the
6d/6e/7b routers each have one: the substance of it is a Plex-touching
operation with rules of its own (the redaction below, the 503 when this replica
has no Plex connection), and those do not belong scattered through the JSON
handlers.

**Dry run is not a parameter.** ``run_library`` takes ``dry_run`` from
``collections.apply_to_plex`` when it is not told, so an operator with writes
switched on would otherwise get a preview that reconciled the library. It is
passed explicitly here, and that is the only reason that endpoint is safe to
expose at all.
"""
import asyncio
import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from autoposter.api.auth import require_session
from autoposter.collections.engine import run_library
from autoposter.collections.reconcile import (
    LIBTYPES,
    COLLECTION_MODES,
    create_blank_collection,
    has_label,
    load_labels,
    protected_label,
)
from autoposter.collections.service import LIBRARY_TYPES, library_definitions
from autoposter.db.models import EventLog, ManagedCollection
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


def _enabled(request: Request):
    """The config, once this instance is actually running collections.

    ``collections.enabled`` is what an operator switches off to stop this
    service touching collections at all. Every endpoint here would otherwise
    keep working -- the preview would still connect to Plex and read the
    library, and the ops below would still write to it -- which is exactly the
    guarantee that switch is supposed to give.
    """
    config = request.app.state.config_holder.current
    if not config.collections.enabled:
        raise HTTPException(
            status_code=503,
            detail="collections are disabled in the config for this instance",
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


LIBRARY_ENTRY_TITLE = "(library)"


def _library_failure(library: str, error: Exception) -> dict:
    """One preview row standing for a library that could not be read.

    The exception's *class* only. Whatever a Plex or provider failure carries
    -- a URL with a token in it, most of the time -- stays in the log, the same
    rule the engine applies to a builder's exception one layer in.
    """
    return {
        "title": LIBRARY_ENTRY_TITLE,
        "library": library,
        "adding": 0, "removing": 0, "deleting": 0, "unresolved": 0,
        "failed": True, "skipped": True,
        "actions": [
            "%s: could not be previewed (%s)" % (library, type(error).__name__)
        ],
    }


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

    A library that cannot be previewed -- a section name that no longer names a
    section, a Plex read that failed -- becomes one failed entry for that
    library rather than a 500 for the whole preview. The preview's whole job is
    to report what a pass would do, and a pass contains a failing library the
    same way (``reconcile_libraries``); answering nothing at all about the four
    libraries that are fine, because the fifth was renamed, would be the
    preview being less honest than the thing it previews.
    """
    config = _enabled(request)
    server = await _connect(request)
    http = request.app.state.http
    summaries = getattr(request.app.state, "tmdb_facts", None)

    definitions_out: list[dict] = []
    actions: list[str] = []
    async with request.app.state.session_factory() as session:
        for name in config.collections.libraries:
            if body.library is not None and name != body.library:
                continue
            try:
                section = server.library.section(name)
                library_type = LIBRARY_TYPES.get(section.type)
                if library_type is None:
                    continue

                definitions = library_definitions(config, library_type)
                if body.title is not None:
                    definitions = [d for d in definitions if d.title == body.title]
                # The sweep runs only for an unfiltered library, because it
                # reports what *no definition* builds: against a filtered
                # subset every definition left out would look unaccounted for,
                # and the preview would show deletions a real pass would never
                # make.
                run = await run_library(
                    session, section, name, library_type, definitions, config,
                    http=http, dry_run=True, sweep=body.title is None, preview=True,
                    summaries=summaries,
                )
            except Exception as error:
                logger.exception("could not preview %r", name)
                entry = _library_failure(name, error)
                definitions_out.append(entry)
                actions += entry["actions"]
                continue

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


# --- roadmap row 28: the lifecycle operations ------------------------------
#
# Three explicit operator actions, each naming exactly what it will touch.
# They write whatever ``apply_to_plex`` says (see the module docstring), so
# every one of them is behind the ownership guards instead, and every one of
# them leaves an ``events_log`` row.


class BlankRequest(BaseModel):
    """Create an empty collection at ``title`` in ``library``."""

    library: str
    title: str


class DeleteRequest(BaseModel):
    """Delete ``title`` in ``library``. ``confirm`` must be true.

    A confirmation flag rather than a second endpoint, because the thing being
    confirmed is *this* library and *this* title: a mis-clicked button that
    deleted the wrong collection would be just as irreversible as one that
    deleted without asking.
    """

    library: str
    title: str
    confirm: bool = False


class MassModeRequest(BaseModel):
    """Set the display mode on every collection this service owns in ``library``."""

    library: str
    mode: Literal["default", "hide", "hideItems", "showItems"]


async def _managed_section(request: Request, library: str):
    """The Plex section for a library this service is configured to manage.

    A library absent from ``collections.libraries`` is refused rather than
    operated on: these endpoints exist to run the collections service's own
    operations by hand, and the set of libraries it runs against is a config
    decision, not a request parameter.
    """
    config = _enabled(request)
    if library not in config.collections.libraries:
        raise HTTPException(
            status_code=404,
            detail="%r is not one of this instance's collections libraries" % library,
        )
    server = await _connect(request)
    try:
        section = await asyncio.to_thread(server.library.section, library)
    except Exception as error:
        logger.exception("could not open the %r section", library)
        raise HTTPException(
            status_code=502,
            detail="could not open %r on Plex (%s)" % (library, type(error).__name__),
        ) from None
    library_type = LIBRARY_TYPES.get(section.type)
    if library_type is None:
        raise HTTPException(
            status_code=422, detail="%r is not a movie or show library" % library
        )
    return config, section, library_type


def _audit(session, event_type: str, library: str, title: str, outcome: str) -> None:
    """One ``events_log`` row per operation. Identity only, never a URL.

    Written for every op, because these are the only writes this service makes
    that no scheduled pass would have made, and the action strings they return
    are not stored anywhere.
    """
    session.add(EventLog(
        source="collections",
        event_type=event_type,
        payload={"library": library, "title": title},
        outcome=outcome,
    ))


async def _managed_row(session, library: str, title: str) -> ManagedCollection | None:
    return (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.library == library, ManagedCollection.title == title
            )
        )
    ).scalar_one_or_none()


@router.post("/collections/ops/blank")
async def blank_collection(
    body: BlankRequest,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Create an empty collection -- Kometa's ``blank_collection``.

    Empty collections cannot be made through plexapi's normal API (see
    ``create_blank_collection``), which is why this is an endpoint rather than
    something an operator can do in Plex. They are used as section dividers,
    the way the Common Sense family's own separator is.

    Refuses a title that already exists, whoever owns it: creating a second
    collection under a name Plex already has is not what the operator asked
    for, and claiming the existing one would be an adoption -- which has its
    own rules, its own config flag, and is not this button.
    """
    config, section, library_type = await _managed_section(request, body.library)
    label = config.collections.ownership_label

    existing = await asyncio.to_thread(section.collections)
    if any(collection.title == body.title for collection in existing):
        raise HTTPException(
            status_code=409,
            detail="a collection called %r already exists in %r"
            % (body.title, body.library),
        )

    collection = await asyncio.to_thread(
        create_blank_collection, section, LIBTYPES[library_type], body.title
    )
    await asyncio.to_thread(collection.addLabel, label)

    action = "created the empty collection %r in %r" % (body.title, body.library)
    async with request.app.state.session_factory() as session:
        if await _managed_row(session, body.library, body.title) is None:
            session.add(ManagedCollection(
                # "operator", not "separator": this row is not the Common
                # Sense family's divider, and reusing that kind would leave
                # it with no marker of its own -- ``engine._sweep`` skips
                # "operator" rows specifically because no definition
                # enumerates this title, so nothing else would keep the next
                # ``delete_unconfigured`` pass from deleting what an operator
                # just made.
                library=body.library, title=body.title, kind="operator",
                plex_rating_key=str(getattr(collection, "ratingKey", "") or ""),
                # No definition builds it, so there is no desired state to
                # hash. The empty string is not a hash any pass can produce,
                # which is what keeps a later reconcile from short-circuiting
                # on it if a definition is ever pointed at this title.
                definition_hash="",
            ))
        _audit(session, "collection_blanked", body.library, body.title, action)
        await session.commit()

    return {"actions": [action]}


@router.post("/collections/ops/delete")
async def delete_collection(
    body: DeleteRequest,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Delete one named collection -- Kometa's ``delete_collections_named``.

    Every guard the delete sweep applies, applied here for the same reasons
    (``engine._sweep``): a protected label wins first, then the collection must
    carry the ownership label *and* have a ``managed_collections`` row. Neither
    alone is a collection that is ours to delete -- the row alone can name a
    collection somebody else recreated under that title, and the label alone is
    one an operator labelled by hand.

    The one guard that is *not* shared is ``delete_unconfigured``: that setting
    authorises the unattended sweep to decide for itself, and this endpoint is
    an operator deciding. ``confirm: true`` is what stands in its place.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=422,
            detail="deleting %r needs confirm: true" % body.title,
        )

    config, section, _type = await _managed_section(request, body.library)
    label = config.collections.ownership_label

    existing = await asyncio.to_thread(section.collections)
    collection = next((c for c in existing if c.title == body.title), None)
    if collection is None:
        raise HTTPException(
            status_code=404,
            detail="there is no collection called %r in %r" % (body.title, body.library),
        )

    await asyncio.to_thread(load_labels, collection)
    protecting = protected_label(collection, config.collections.protect_labels or [])
    if protecting is not None:
        raise HTTPException(
            status_code=409,
            detail="%r carries the protected label %r" % (body.title, protecting),
        )
    if not has_label(collection, label):
        raise HTTPException(
            status_code=409,
            detail="%r does not carry the %r label, so it is not ours to delete"
            % (body.title, label),
        )

    async with request.app.state.session_factory() as session:
        row = await _managed_row(session, body.library, body.title)
        if row is None:
            raise HTTPException(
                status_code=409,
                detail="%r has no managed_collections row, so it is not ours to delete"
                % body.title,
            )

        await asyncio.to_thread(collection.delete)
        await session.delete(row)
        action = "deleted %r from %r" % (body.title, body.library)
        _audit(session, "collection_deleted", body.library, body.title, action)
        await session.commit()

    return {"actions": [action]}


@router.post("/collections/ops/mass-mode")
async def mass_collection_mode(
    body: MassModeRequest,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Set the display mode on every collection this service owns in a library.

    Kometa's ``mass_collection_mode``, narrowed to ours. Kometa's own version
    sets the mode on *every* collection in the library; this one asks Plex for
    the collections carrying the ownership label (a server-side filter -- see
    ``service.unmanaged_prior_collections``) and then keeps only those with a
    ``managed_collections`` row, so the operator's own collections and Plex's
    franchise collections are never touched. A protected label still wins.

    Each skip is reported rather than silently dropped: "37 collections
    changed" is not an answer to "did it do the one I was thinking of".
    """
    config, section, _type = await _managed_section(request, body.library)
    label = config.collections.ownership_label
    wanted = COLLECTION_MODES[body.mode]

    candidates = await asyncio.to_thread(section.collections, label=label)

    async with request.app.state.session_factory() as session:
        rows = {
            row.title for row in (
                await session.execute(
                    select(ManagedCollection).where(
                        ManagedCollection.library == body.library
                    )
                )
            ).scalars()
        }

        actions: list[str] = []
        changed = 0
        for collection in candidates:
            if collection.title not in rows:
                actions.append(
                    "skipped %r: it carries the %r label but has no managed row"
                    % (collection.title, label)
                )
                continue
            await asyncio.to_thread(load_labels, collection)
            protecting = protected_label(
                collection, config.collections.protect_labels or []
            )
            if protecting is not None:
                actions.append(
                    "skipped %r: it carries the protected label %r"
                    % (collection.title, protecting)
                )
                continue
            if getattr(collection, "collectionMode", None) == wanted:
                continue
            await asyncio.to_thread(collection.modeUpdate, mode=body.mode)
            changed += 1

        summary = "set the display mode of %d collection(s) in %r to %r" % (
            changed, body.library, body.mode
        )
        actions.insert(0, summary)
        _audit(
            session, "collection_mode_set", body.library,
            # The op belongs to the library, not to any one collection, so the
            # payload's title slot names the mode and the row reads on its own.
            body.mode, summary,
        )
        await session.commit()

    return {"actions": actions}
