"""Restarting this process, deliberately.

A frozen setting is one a generation swap cannot reach (``config/live.py``),
and until now the editor's only honest answer was a note saying "restart to
apply" -- which an operator could act on only outside the application. This
route is that action, and it is the SAME mechanism the first-start wizard
already ends with: ``api/setup.py``'s ``_exec_boot``, an ``os.execv`` of
``python -m autoposter.boot``, imported here rather than copied so that the
two restarts cannot drift into two behaviours. The PID is kept, no supervisor
is involved, and the same sequence runs under systemd, a terminal or a
container.

Three refusals, and each of them is a thing a restart would destroy or a
promise it could not keep:

* a run that lives in this process is in flight -- the work would be
  interrupted, and the response names the run so the page can offer "restart
  when it finishes". A catch-up is the exception and is named as one below;
* an artwork or metadata mode holds the process-wide mode lock -- that one is
  mid-write to a media server;
* this process is one worker of several on one port -- execing itself would
  leave the deployment half old and half new, with no way to reach the others.
"""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from autoposter.api.auth import require_session
from autoposter.catchup import CATCH_UP_KIND
from autoposter.db.models import Run
from autoposter.db.models import Session as SessionModel

logger = logging.getLogger(__name__)

router = APIRouter()

#: The run kinds this guard deliberately IGNORES. A catch-up lives for hours,
#: everything it knows is a row in the database, and its drain resumes on the
#: other side of a boot -- so a restart during one interrupts nothing and
#: refusing would mean an operator with a day-long backlog could never apply a
#: setting. Every other kind -- a full pass, a scheduled job -- holds its work
#: in this process's memory and would lose it.
IGNORED_RUN_KINDS = (CATCH_UP_KIND,)

RUN_IN_FLIGHT = (
    "a {kind} is running ({name}); restarting now would interrupt it. Wait for "
    "it to finish, or cancel it, and restart then."
)
MODE_IN_FLIGHT = (
    "an artwork or metadata mode is running on this instance and is writing to "
    "a media server; restarting now would interrupt it"
)
MULTIPLE_WORKERS = (
    "this process is one of several workers sharing a port, so restarting it "
    "would leave the others running the old configuration; restart the "
    "deployment instead"
)

#: How a multi-worker deployment announces itself. Both spellings, because
#: uvicorn reads the first and the image's own entrypoint could set either.
WORKER_COUNT_ENV: tuple[str, ...] = ("WEB_CONCURRENCY", "UVICORN_WORKERS")


def _is_one_of_several_workers() -> bool:
    for name in WORKER_COUNT_ENV:
        raw = os.environ.get(name) or ""
        try:
            if int(raw) > 1:
                return True
        except ValueError:
            continue
    return False


def _exec_boot() -> None:
    """The wizard's own exec, called rather than copied.

    Imported inside the call and not at module scope: this module is part of
    every configured application, and ``api/setup.py`` is the wizard surface a
    configured deployment otherwise never loads -- the same reason
    ``boot.main`` imports ``build_setup_app`` where it uses it. One line of
    ``os.execv`` exists in this service, and it is that one.
    """
    from autoposter.api.setup import _exec_boot as exec_boot

    exec_boot()


@router.post("/system/restart")
async def restart(
    request: Request, _: SessionModel = Depends(require_session)
) -> JSONResponse:
    """Re-execute boot, so a saved frozen change takes effect.

    Boot re-reads the store, so what comes back is the configuration as the
    Settings page saved it.

    Scheduled on the response's background task list so the body is written
    before the process is replaced -- ``api/setup.py::finish``'s shape, and the
    reason the page can poll ``/healthz`` until the port answers again.
    """
    if _is_one_of_several_workers():
        raise HTTPException(status_code=409, detail=MULTIPLE_WORKERS)
    if request.app.state.mode_lock.locked():
        raise HTTPException(status_code=409, detail=MODE_IN_FLIGHT)
    async with request.app.state.session_factory() as session:
        run = await session.scalar(
            select(Run)
            .where(Run.finished_at.is_(None), Run.kind.not_in(IGNORED_RUN_KINDS))
            .order_by(Run.started_at.desc())
            .limit(1)
        )
    if run is not None:
        raise HTTPException(
            status_code=409, detail=RUN_IN_FLIGHT.format(kind=run.kind, name=run.name)
        )
    logger.warning("a restart was requested from the Settings page")
    return JSONResponse(
        content={"restarting": True}, background=BackgroundTask(_exec_boot)
    )
