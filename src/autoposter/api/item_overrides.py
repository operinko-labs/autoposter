"""Per-item metadata overrides (roadmap row 99): the operator's own surface.

Three routes and no fourth. There is no bulk PUT and no importer, and that is
a locked scope decision rather than a backlog item: the roadmap's own head
says "Kometa/Posterizarr YAML is never parsed and no importer is planned", and
row 99's cell says "not YAML compatibility". If a bulk shape in OUR vocabulary
is ever wanted, the existing ``GET /api/config/overrides/export`` /
``POST /api/config/overrides/import`` envelope (discriminator
``autoposter_overrides``) is where it goes, not a second file format here.

**Its own module, for the reason every other sub-router here has one.** These
three handlers are the only place in the API where an operator's free text
becomes a value this service writes into Plex, and the two rules that makes
necessary are the substance of the module:

1. **Nothing here ever seeds a row from a provider or from Plex.** The PUT
   body is the only source of a row's value. A "fill this in with what Plex
   currently says" convenience is the freezing hazard
   (``frontend/src/api/overrides.ts:13-21``) exactly -- it would store today's
   values as overrides and freeze them against every future provider change,
   and reverting a field would stop meaning anything.
2. **No refusal this module raises echoes what the operator typed.** A value
   may be a summary with a URL in it or a pasted token, so **every 422 here is
   the bare exception class name** -- ``queue/worker.py::_served_reason``'s
   rule for ``job.last_error``, and roadmap row 99's C3/C10 verbatim
   ("class-name-only detail"). The unwritable-field branch raises
   ``OverrideValueError`` into the same ``except`` the parse uses, so there is
   ONE exit and one form; the message, which does name the field and the
   shape, reaches the pod log instead -- the trusted sink under row 207 -- and
   the panel labels its own error because it already knows which field it
   asked about. The field name from the URL path therefore never reaches a
   response at all. The DELETE's Plex-failure 503 is class-name-only for the
   same reason (row 213's ruling), not a redacted message: ``redact_urls``
   only matches a URL scheme, and the commonest Plex failure -- a connection
   error -- has none, so it would serve the internal host and port verbatim.

   **The residual, stated rather than papered over:** FastAPI validates the
   body before this handler runs, and its own 422 echoes the submitted input.
   The PUT therefore takes the body as a plain ``dict`` and type-checks
   ``value`` itself, so ``{"value": 123}`` -- the realistic wrong shape -- is
   refused here like any other bad value. A body that is not a JSON object at
   all (``"a string"``, ``[1,2]``) is still refused by FastAPI's own echoing
   422; no panel sends that shape and it carries no field value, but the claim
   above is true of this handler and not of the framework in front of it.

**The gate is a 409 on the writes and a flag on the read.** Off means the
panel is READ-ONLY, not absent: an operator whose overrides silently stopped
applying, with no panel to explain it, would have nothing to go on. So the
listing keeps answering, carrying ``enabled: false``, and the panel renders a
banner naming the key.
"""
import asyncio
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select

from autoposter.api.auth import require_session
from autoposter.db.models import ItemMetadataOverride, MediaItem
from autoposter.db.models import Session as SessionModel
from autoposter.plex.item_overrides import (
    OverrideValueError,
    canonical_value,
    parse_override,
    writable_fields,
)
from autoposter.plex.writer import _locked_in_plex, _PLEX_FIELD_NAMES, exemption_reason

logger = logging.getLogger(__name__)

router = APIRouter()

_GATE = "operations.item_overrides_enabled"


def _enabled(request: Request) -> bool:
    """The LIVE gate. ``config_holder.current`` rather than ``state.config``
    because ``operations`` is not frozen -- a saved override reaches this
    reader immediately, and the panel says "live" for that reason."""
    return bool(
        request.app.state.config_holder.current.operations.item_overrides_enabled
    )


def _require_enabled(request: Request) -> None:
    if not _enabled(request):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{_GATE} is off; existing overrides are left in place and "
                "ignored, and nothing can be changed until it is on"
            ),
        )


async def _load_item(session, item_id: int) -> MediaItem:
    item = (
        await session.execute(select(MediaItem).where(MediaItem.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    return item


@router.get("/items/{item_id}/metadata-overrides")
async def list_metadata_overrides(
    item_id: int, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """This item's overrides, its kind's writable field names, and the gate.

    ``writable`` is a list of NAMES and nothing else. Serving each one
    pre-filled with what Plex or the facts row currently holds would put
    today's values one Save away from being frozen as overrides -- the
    freezing hazard with an extra click rather than without one.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = await _load_item(session, item_id)
        rows = (
            await session.execute(
                select(ItemMetadataOverride)
                .where(ItemMetadataOverride.item_id == item_id)
                .order_by(ItemMetadataOverride.field)
            )
        ).scalars().all()
        overrides = [
            {
                "field": row.field,
                "value": row.value,
                "updated_at": row.updated_at.isoformat(),
            }
            for row in rows
        ]
        kind = item.kind
    return {
        "enabled": _enabled(request),
        "kind": kind,
        "writable": writable_fields(kind),
        "overrides": overrides,
    }


@router.put("/items/{item_id}/metadata-overrides/{field}")
async def put_metadata_override(
    item_id: int,
    field: str,
    request: Request,
    # A plain ``dict`` rather than ``value: str = Body(..., embed=True)``, and
    # the difference is the served-string law rather than a style choice:
    # FastAPI validates a declared body type BEFORE this function runs, and
    # its own 422 echoes the submitted input. Taking the object and checking
    # ``value``'s type below keeps the realistic wrong shape -- a JSON number
    # -- inside this handler's own class-name-only refusal. (A body that is
    # not an object at all is still FastAPI's; see the module docstring.)
    payload: dict = Body(...),
    _: SessionModel = Depends(require_session),
) -> dict:
    """Declare this field's value for this item, and re-run the item.

    An upsert, because ``UNIQUE(item_id, field)`` makes "the override for this
    field" a single answerable question. The value is parsed HERE, while the
    operator can still see what they typed, and stored in the canonical form
    the writer's diff is made in -- see ``plex/item_overrides.py`` for why
    those have to be the same string.

    The re-enqueue is the standard reprocess. C10 says "the metadata-only pass
    where one exists"; this application has exactly ONE job kind
    (``app.py``'s ``handlers = {"process_item": process_item_handler}``), so
    that branch does not apply and the ordinary reprocess is what runs -- the
    same one the Re-run button and ``clear-override`` use, deduplicated on the
    intent's own key so pressing Save twice queues one job.
    """
    _require_enabled(request)
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = await _load_item(session, item_id)
        # ONE exit for every refusal about the field or the value, so all of
        # them serve the same class-name-only detail (C3/C10). The unwritable
        # case raises rather than returning its own HTTPException precisely so
        # that the served FORM cannot drift between the two branches.
        try:
            if field not in writable_fields(item.kind):
                raise OverrideValueError(
                    f"{field!r} is not a field an item of kind {item.kind!r} carries"
                )
            value = payload.get("value")
            if not isinstance(value, str):
                raise OverrideValueError(f"{field} must be sent as a JSON string")
            parsed = parse_override(field, value)
        except OverrideValueError as exc:
            # Row 213: the CLASS NAME, never the message and never the value.
            # The message -- which DOES name the field and the shape -- reaches
            # the pod log, the trusted sink under row 207. The panel labels its
            # own error because it already knows which field it asked about.
            logger.info("item %s: refused an override for %s: %s", item_id, field, exc)
            raise HTTPException(status_code=422, detail=type(exc).__name__) from None
        stored = canonical_value(field, parsed)

        row = (
            await session.execute(
                select(ItemMetadataOverride)
                .where(ItemMetadataOverride.item_id == item_id)
                .where(ItemMetadataOverride.field == field)
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(ItemMetadataOverride(
                item_id=item_id, field=field, value=stored,
            ))
        else:
            row.value = stored
        await session.commit()

        job_id = await _reprocess(request, session, item)
    return {
        "status": "saved", "field": field, "value": stored,
        "queued": job_id is not None,
    }


@router.delete("/items/{item_id}/metadata-overrides/{field}")
async def delete_metadata_override(
    item_id: int,
    field: str,
    request: Request,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Clear this override, unlock the field in Plex, and re-run the item.

    Roadmap row 99's C9, option (b), and both halves are disclosed rather than
    only the tidy one. Everything this service writes is LOCKED, so a row that
    merely went away would leave a locked field nothing will ever refill. So
    ONE write sets ``{field}.locked = 0`` -- no value write -- and then:

    * a field with a provider source (ratings, content rating, studio, release
      date, genres, original title) is rewritten AND re-locked by the ordinary
      facts path on the next pass;
    * a field with NO source (``title``, ``sort_title``, ``summary``,
      ``tagline``) keeps the operator's last value in Plex until Plex itself
      refreshes it.

    There is deliberately no "write the provider value back immediately": that
    would need a source for every field -- which four of them do not have --
    and would be a second write path into Plex.

    **Unlock FIRST, then delete the row.** ``clear_manual_override``'s
    ordering law verbatim: if the first write fails, nothing else happens. A
    deleted row and a still-locked field would be this endpoint claiming to
    have cleared something it did not.

    **Row 35's exemption gates this write too.** ``exemption_reason`` is the
    single gate on every Plex metadata write this service makes
    (``render/pipeline.py``'s two ``apply_facts`` call sites); an exempt item
    gets the same treatment here -- the row is still deleted, but the Plex
    write is skipped rather than sent, and the response says so
    (``plex: "skipped (exempt)"``) instead of claiming an unlock that did not
    happen.

    This is the opposite call from the ``plex is None`` branch above, on
    purpose: that branch keeps the row because nothing else would ever say
    the field should be unlocked, but here the operator's own action (the
    override) is the thing being withdrawn, and the row answers "is this
    field overridden", not "is this field locked" -- keeping it around
    would say something that is no longer true. The residual: a field this
    service wrote and locked *before* the item became exempt stays locked
    forever once its override is cleared this way, since the pipeline skips
    an exempt item's writes entirely and nothing else in this service ever
    unlocks a field. Bounded -- the field is still editable by hand in
    Plex, it just stops taking agent updates -- and disclosed to the
    operator by the response the panel renders (N-1), not silently.
    """
    _require_enabled(request)
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        item = await _load_item(session, item_id)
        row = (
            await session.execute(
                select(ItemMetadataOverride)
                .where(ItemMetadataOverride.item_id == item_id)
                .where(ItemMetadataOverride.field == field)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="no override for this field")
        if field not in _PLEX_FIELD_NAMES:
            # Reachable from a stale row (a kind change or a future narrowing
            # of the map), not from the panel: the PUT that could have made
            # this row goes through ``writable_fields``, which is already
            # intersected with this map. ``load_overrides`` skips the same
            # drift with a warning rather than raising; this matches that
            # posture instead of a 500 on an unguarded subscript below.
            raise HTTPException(status_code=404, detail="no override for this field")

        plex = request.app.state.plex
        if plex is None:
            # A replica running without the background services. Deleting the
            # row anyway would leave the field locked in Plex with nothing
            # left anywhere to say it should be unlocked.
            raise HTTPException(
                status_code=503, detail="this instance is not connected to Plex"
            )
        _attribute, plex_field = _PLEX_FIELD_NAMES[field]
        exempt = None
        try:
            plex_item = await plex.fetch_item(item.rating_key)
            exempt = exemption_reason(
                request.app.state.config_holder.current.operations,
                item.rating_key, item.imdb_id,
                getattr(plex_item, "labels", None),
            )
            if exempt is None:
                await asyncio.to_thread(_unlock, plex_item, plex_field)
        except Exception as exc:
            # Row 213's ruling extends to this 503 too: the class name only,
            # never ``str(exc)``. A plexapi connection failure carries the
            # internal Plex host and port, and ``redact_urls`` -- scheme-
            # anchored -- does not touch that shape, so serving the message
            # would leak it to whoever called this endpoint.
            logger.warning(
                "could not unlock %s on %s: %s", field, item.rating_key, exc,
            )
            raise HTTPException(
                status_code=503, detail=type(exc).__name__
            ) from None

        if exempt is not None:
            logger.info(
                "plex: skipped clearing %s on %s: %s",
                field, item.rating_key, exempt,
            )

        await session.delete(row)
        await session.commit()

        job_id = await _reprocess(request, session, item)
    result = {"status": "cleared", "field": field, "queued": job_id is not None}
    if exempt is None:
        result["unlocked"] = True
    else:
        result["plex"] = "skipped (exempt)"
    return result


def _unlock(plex_item, plex_field: str) -> None:
    """One batched write setting the field's lock bit to 0, and no value.

    Batched for the shape rather than the saving: it is the same
    ``batchEdits()``/``saveEdits()`` block ``plex/writer.apply_facts`` uses, so
    an operator reading a Plex access log sees one request of a familiar
    shape rather than a second, different way this service edits an item.

    Skipped when Plex already reports the field unlocked, matching
    ``plex/writer.py``'s own ``unlock`` verb (``_locked_in_plex(...) is not
    False``) rather than writing unconditionally.
    """
    if _locked_in_plex(plex_item, plex_field) is False:
        return
    plex_item.batchEdits()
    plex_item.edit(**{f"{plex_field}.locked": 0})
    plex_item.saveEdits()


async def _reprocess(request, session, item: MediaItem) -> int | None:
    """Queue the item's ordinary reprocess, via ``api/routes``' own helper.

    Imported inside the function rather than at module scope: ``api/routes``
    imports THIS module to register the router, so a top-level import would
    be circular. One indexed insert on a button press.
    """
    from autoposter.api.routes import _enqueue_reprocess

    return await _enqueue_reprocess(session, item)
