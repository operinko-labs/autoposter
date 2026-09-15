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

* this process is one worker of several on one port -- execing itself would
  leave the deployment half old and half new, with no way to reach the others.
  Opt-in, and honest about it: the two environment variables below are how a
  deployment declares its worker count, and ``uvicorn --workers 4`` typed on a
  command line sets neither, so that shape passes the guard;
* a run that lives in this process is in flight -- the work would be
  interrupted, and the response names the run so the page can offer "restart
  when it finishes". A catch-up is the exception and is named as one below;
* an artwork or metadata mode holds the process-wide mode lock -- that one is
  mid-write to a media server. Asked LAST, and by taking the lock rather than
  looking at it: every guard before it awaits, and a mode that acquired the
  lock inside one of those windows would be execed mid-write.
"""
import logging
import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from starlette.background import BackgroundTask

from autoposter.api.auth import require_session
from autoposter.catchup import CATCH_UP_KIND
from autoposter.db.models import Run
from autoposter.db.models import Session as SessionModel
from autoposter.scheduler.run_history import FULL_PASS_CEILING_SECONDS

logger = logging.getLogger(__name__)

router = APIRouter()

#: The run kinds this guard deliberately IGNORES. A catch-up lives for hours,
#: everything it knows is a row in the database, and its drain resumes on the
#: other side of a boot -- so a restart during one interrupts nothing and
#: refusing would mean an operator with a day-long backlog could never apply a
#: setting. Every other kind -- a full pass, a scheduled job -- holds its work
#: in this process's memory and would lose it.
IGNORED_RUN_KINDS = (CATCH_UP_KIND,)

#: How old an open row may be before this guard stops believing it. Nothing
#: reconciles an orphaned ``scheduled`` row except the next pass of that same
#: job (``scheduler/run_history.open_run``), so a SIGKILL mid-pass leaves one
#: ``running`` forever -- and without a ceiling here that row would wedge this
#: button permanently on a deployment where the job does not run again, with
#: the first remedy an operator reaches for (turn the scheduler off and
#: restart) being the very thing refused. The full pass's own timeout horizon,
#: borrowed rather than restated, so one number decides how long a run may
#: plausibly still be alive.
ORPHAN_RUN_SECONDS = FULL_PASS_CEILING_SECONDS

RUN_IN_FLIGHT = (
    "a {kind} run is in progress ({name}); restarting now would interrupt it"
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

    The guards run cheapest-first with one exception that decides the whole
    order: the mode lock is asked LAST, because it is the only one that must
    still be true at the moment of the exec and the only one that can be made
    to stay true.
    """
    if _is_one_of_several_workers():
        raise HTTPException(status_code=409, detail=MULTIPLE_WORKERS)
    async with request.app.state.session_factory() as session:
        run = await session.scalar(
            select(Run)
            # Newest first: with the ceiling above, every row this query can
            # still see is one that might genuinely be alive, and of those the
            # newest is the one an operator just started and is asking about.
            .where(
                Run.finished_at.is_(None),
                Run.kind.not_in(IGNORED_RUN_KINDS),
                Run.started_at >= func.now() - timedelta(seconds=ORPHAN_RUN_SECONDS),
            )
            .order_by(Run.started_at.desc())
            .limit(1)
        )
    if run is not None:
        raise HTTPException(
            status_code=409, detail=RUN_IN_FLIGHT.format(kind=run.kind, name=run.name)
        )
    lock = request.app.state.mode_lock
    # Safe without a lock of its own, and the reason this guard is last:
    # nothing is awaited between the check and the acquire, so no mode can take
    # the lock in between -- `api/routes.py`'s own trigger relies on the same
    # fact. Taken and never released, because the next statement but one
    # replaces this process image; a mode arriving after this line meets a held
    # lock and is refused, which is the outcome the guard exists for.
    if lock.locked():
        raise HTTPException(status_code=409, detail=MODE_IN_FLIGHT)
    await lock.acquire()
    logger.warning("a restart was requested from the Settings page")
    return JSONResponse(
        content={"restarting": True}, background=BackgroundTask(_exec_boot)
    )
