"""The per-server buttons (spec §5): catch up, and retry what failed.

These are the routes the Servers tab's cards call. They own no logic of their
own -- ``catchup.py`` does -- and their whole job is turning a
``CatchUpRefused`` into a 409 whose ``detail`` is the sentence the button
shows, so the operator reads one wording whether the refusal came from the
API, a log line or the runs list.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from autoposter.api.auth import require_session
from autoposter.catchup import (
    CatchUpRefused, cancel_catch_up, catch_up_progress, retry_failed, start_catch_up,
)
from autoposter.db.models import Session as SessionModel

router = APIRouter()


class CatchUpBody(BaseModel):
    """How often this run's backlog is drained.

    ``None`` -- and an omitted body -- means the scheduler's own
    pending-deliveries cadence. The floor is applied in ``start_catch_up``,
    not here: one place decides what a cadence may be.
    """

    cadence_seconds: int | None = None


@router.post("/servers/{name}/catch-up")
async def start(
    name: str, request: Request, body: CatchUpBody | None = None,
    _: SessionModel = Depends(require_session),
) -> dict:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            run_id = await start_catch_up(
                session, request.app.state.servers, request.app.state.config, name,
                health=getattr(request.app.state, "server_health", {}),
                cadence_seconds=(body.cadence_seconds if body is not None else None),
            )
        except CatchUpRefused as exc:
            # 409, not 400: nothing about the request is malformed -- the
            # deployment is in a state that has no room for this run yet.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await session.commit()
        progress = await catch_up_progress(session, name)
    return {
        "run_id": run_id, "server": name, "cadence_seconds": progress["cadence_seconds"],
    }


@router.get("/servers/{name}/catch-up")
async def progress(
    name: str, request: Request, _: SessionModel = Depends(require_session),
) -> dict:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        found = await catch_up_progress(session, name)
    # An explicit envelope rather than a bare null, so the page can tell "no
    # run yet" from a transport failure without reading the status code.
    return found if found is not None else {"run": None}


@router.delete("/servers/{name}/catch-up")
async def cancel(
    name: str, request: Request, _: SessionModel = Depends(require_session),
) -> dict:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            outcome = await cancel_catch_up(session, name)
        except CatchUpRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await session.commit()
    return outcome


@router.post("/servers/{name}/retry-failed")
async def retry(
    name: str, request: Request, _: SessionModel = Depends(require_session),
) -> dict:
    """Re-arm this server's failed rows without a full catch-up (spec §5).

    Never refused: re-arming nothing is a legitimate answer, and the counts
    say so.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        outcome = await retry_failed(session, name)
        await session.commit()
    return outcome
