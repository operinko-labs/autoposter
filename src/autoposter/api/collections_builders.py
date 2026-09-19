"""The collections engine's operator surface: the catalog, preview, and the ops.

``GET /api/collections/catalog`` is the odd one out and says so in its own
docstring: it touches neither Plex nor the database, because it answers "what
could this service build" -- a question about a table, not about a server. It
lives here rather than in ``routes.py`` because it is part of this surface: the
picker it feeds sits beside the preview an operator runs next.

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
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from autoposter.api.auth import require_session
from autoposter.collections.catalog import catalog_listing
from autoposter.collections.engine import run_library
from autoposter.collections.groups import SEPARATOR_STYLES, group_listing
from autoposter.collections.reconcile import (
    LIBTYPES,
    COLLECTION_MODES,
    create_blank_collection,
    has_label,
    load_labels,
    protected_label,
)
from autoposter.collections.service import (
    LIBRARY_TYPES,
    build_source_clients,
    library_definitions,
)
from autoposter.collections.source_urls import SourceUrlRefused, parse_source
from autoposter.config.overrides import load_overrides_document
from autoposter.db.models import EventLog, ManagedCollection
from autoposter.db.models import Session as SessionModel
from autoposter.redact import redact_urls
from autoposter.servers.registry import require_plex

logger = logging.getLogger(__name__)

router = APIRouter()


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
        "filtered": 0,
        "failed": True, "skipped": True,
        "actions": [
            "%s: could not be previewed (%s)" % (library, type(error).__name__)
        ],
    }


@router.get("/collections/catalog")
async def collections_catalog(
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """The preset catalog: every collection this service can build, by category.
    Also carries ``groups``: every collection group as ``{key, title, section,
    position}`` in the running config's effective order, which is the Groups
    panel's enumeration source — served so the frontend never holds a copy of
    the group keys. ``separator_styles``/``separator_style`` are the same panel's
    style select, on the same terms: the 22 names and the running value.

    The one endpoint in this module that touches neither Plex nor the database.
    It is a dump of a pure table (``collections/catalog.py``) plus which keys
    the live config has switched on, and that is deliberate: choosing which
    collections to build is not a question about a server, so the picker has to
    work on a replica with no Plex connection -- where every other handler here
    answers 503.

    It is also not behind ``_enabled``. That guard exists because the endpoints
    below it read and write a Plex library, and switching ``collections.enabled``
    off is how an operator stops this service touching collections at all.
    Reading the catalog changes nothing; an operator about to switch the section
    back on would otherwise be shown an empty page instead of the thing they
    came to configure.
    """
    config = request.app.state.config_holder.current
    return {
        "categories": catalog_listing(config),
        # The group-order panel's enumeration:
        # served rather than transcribed, so the eleven keys exist in exactly
        # one language. Effective order, not canonical -- the panel shows
        # the tab as the running config orders it.
        "groups": group_listing(config),
        # The style select's enumeration and its current value -- served, so
        # the frontend holds no style name of its own (the group rows' rule).
        "separator_styles": list(SEPARATOR_STYLES),
        "separator_style": config.collections.separator_style,
    }


@router.get("/collections/definitions")
async def collections_definitions(
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """The operator-configured collection definitions, without running anything.

    Row 137's listing: ``collections.definitions`` off the RUNNING config --
    the same list ``service.library_definitions`` appends after the built-in
    defaults on every pass -- with no Plex read and no engine run. The
    Definitions panel's preview stays the only dry run; this is the plain
    listing it never had.

    ``provenance`` is per entry in shape and uniform in value by construction:
    the overrides layer replaces a list WHOLESALE (``merge_overrides``), so
    when the stored overrides document carries ``collections.definitions``
    every effective entry came through it ("override"), and when it does not,
    every entry is the mounted file's ("file"). The distinction is what makes
    the Custom collections panel's writes safe: "override" rows are the
    panel's to rewrite, and "file" rows must never be copied into the stored
    document -- copying them would freeze today's file values against every
    future edit of the YAML (the delta rule ``frontend/src/api/overrides.ts``
    opens with).

    ``libraries`` is ``collections.libraries`` -- the names the create form
    offers as scope checkboxes. There is no library-type enum anywhere in a
    definition; the config speaks names, so the form does too.

    Not behind ``_enabled``, for the catalog's reason: reading config changes
    nothing, and the panel must render on a replica with no Plex connection.
    """
    config = request.app.state.config_holder.current
    async with request.app.state.session_factory() as session:
        try:
            stored = await load_overrides_document(session)
        except ValueError as exc:
            # A hand-edited config_overrides row whose document is not a JSON
            # object. The same answer ``GET /api/config`` gives the same row
            # (``routes.py``): an operator needs to know what to fix, not an
            # opaque 500 from an AttributeError three frames down.
            raise HTTPException(
                status_code=500,
                detail="config overrides row is corrupt (not a JSON object); fix or delete it",
            ) from exc
    section = stored.get("collections")
    overridden = isinstance(section, dict) and "definitions" in section
    provenance = "override" if overridden else "file"
    return {
        "libraries": list(config.collections.libraries),
        "definitions": [
            {
                "title": definition.title,
                "builder": definition.builder,
                "params": definition.params,
                "libraries": definition.libraries,
                "sort": definition.sort,
                "sync_mode": definition.sync_mode,
                "provenance": provenance,
            }
            for definition in config.collections.definitions
        ],
    }


class ParseSourceRequest(BaseModel):
    """One pasted source URL (or bare id) to resolve to a builder."""

    url: str


@router.post("/collections/parse-source")
async def parse_collection_source(
    body: ParseSourceRequest,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Resolve a pasted list URL to ``(builder, params)`` -- shape-only.

    A thin shell over ``collections/source_urls.py``, which is where the
    shapes live (beside the params models they feed; see that module's
    docstring for why the parsing is server-side at all). Touches nothing:
    no Plex, no database, no outbound request -- a list's existence is the
    first pass's business, not this endpoint's. Not behind ``_enabled``, for
    the catalog's reason: parsing changes nothing, and the form must work on
    a replica with no Plex connection.

    The refusal is a 422 whose ``detail`` is one operator-facing sentence:
    the params model's own error string, or the supported-shapes list.
    """
    try:
        parsed = parse_source(body.url)
    except SourceUrlRefused as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {
        "builder": parsed.builder,
        "params": parsed.params,
        "display_note": parsed.display_note,
    }


class PreviewRequest(BaseModel):
    """Which definitions to preview. Both filters are optional; omitting both
    previews every definition in every configured library."""

    library: str | None = None
    title: str | None = None


@router.post("/collections/preview", dependencies=[Depends(require_plex)])
async def preview_collections(
    body: PreviewRequest,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Run the collections engine as a dry run and report what it would do.

    Per definition: the collection's title and library, how many members would
    be added and removed, whether it would be deleted by the sweep, how many of
    the source's ids this library does not own, how many resolved items its
    ``filters:`` excluded, and whether the source failed or nothing was applied
    -- plus that definition's own action strings.

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
    cache = getattr(request.app.state, "provider_cache", None)
    # Built per request, as the scheduled pass builds it per run: a preview
    # whose source clients were fixed at startup would answer for a
    # configuration the next real pass is not going to run.
    secrets = getattr(request.app.state, "secrets", None)

    definitions_out: list[dict] = []
    actions: list[str] = []
    async with request.app.state.session_factory() as session:
        for name in config.collections.libraries:
            if body.library is not None and name != body.library:
                continue
            try:
                # Built inside the per-library try, not once above the loop:
                # a construction failure here is a library-level failure like
                # any other, and must become that library's failure entry
                # rather than a 500 for the whole preview.
                sources = (
                    build_source_clients(config, secrets, http, cache)
                    if secrets is not None else None
                )
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
                    summaries=summaries, sources=sources, cache=cache,
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
                    "filtered": result.filtered,
                    "failed": result.failed,
                    "skipped": result.skipped,
                    "actions": [redact_urls(action) for action in result.actions],
                })
            actions += [redact_urls(action) for action in run.actions]
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


@router.post("/collections/ops/blank", dependencies=[Depends(require_plex)])
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
        row = await _managed_row(session, body.library, body.title)
        if row is None:
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
        else:
            # A row can outlive the Plex collection it describes: rows are
            # never reaped when the object vanishes straight out of Plex, so
            # a definition-built collection hand-deleted there leaves a
            # "manual"/"smart" row behind with a hash no definition will ever
            # match again. If an operator then blanks the same title, the
            # title check above already proved Plex has nothing by that name
            # -- but this row still does, and unless it is corrected it keeps
            # describing the collection that is gone rather than the blank
            # that now exists. A stale kind + stale hash is exactly what
            # ``engine._sweep`` reads as "no definition builds this any
            # more", so the very next ``delete_unconfigured`` pass would
            # delete the blank an operator just made -- the same class of bug
            # the "operator" kind exists to prevent, arriving through the
            # row surviving rather than through the row never being written.
            row.kind = "operator"
            row.definition_hash = ""
            row.plex_rating_key = str(getattr(collection, "ratingKey", "") or "")
            # The rest of the row described the OLD Plex object; none of it
            # is true of the new blank one, and "operator" rows are never
            # stamped by a reconcile pass anyway (only list.py's are).
            row.poster_sha256 = None
            row.member_count = None
            row.last_added = None
            row.last_removed = None
            row.last_reconciled_at = None
        _audit(session, "collection_blanked", body.library, body.title, action)
        await session.commit()

    return {"actions": [action]}


@router.post("/collections/ops/delete", dependencies=[Depends(require_plex)])
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


@router.post("/collections/ops/mass-mode", dependencies=[Depends(require_plex)])
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
