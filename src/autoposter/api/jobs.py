"""The jobs overview: what the queue is about to do, and how to stop it.

The Failures page already answers "what gave up". This answers the question an
operator has *while* a pass is running -- what is queued, what is in flight,
what is stuck waiting on Plex, and how long until the next attempt -- and lets
them cancel a job without going to the database.

Two things shape the response:

* **The payload is never echoed.** A job's payload is a ``RenderIntent`` for
  the item kinds and a filter set for the modes; it carries provider ids and,
  for other kinds, source URLs. The page needs to *name* the item, so the four
  fields that do that are lifted out by name and everything else stays in the
  database.
* **The attempt budget is not one number.** A job failing because Plex has not
  indexed the file yet is retried against ``config.plex.resolve_max_attempts``
  (worker.py hands that budget to ``fail()`` via the exception), while every
  other failure gets the queue's ``MAX_ATTEMPTS``. Showing 3/5 for a job that
  actually has ten tries would read as nearly dead when it is fine, so the
  budget shown follows the same fork the worker takes.

Cancelling forks on the state the row is *actually* in, not on what the page
last saw:

* ``pending`` -- nothing has started, so the job is dismissed outright.
* ``running`` -- a worker holds it. It is not interrupted: a render that has
  happened cannot be un-rendered, and killing a handler mid-upload would leave
  Plex holding half a change. ``cancel_requested`` is set instead and the
  worker honours it when the attempt ends (queue/jobs.py's ``fail()``).
* anything terminal -- a conflict. There is nothing left to cancel, and parked
  jobs have the Failures page.

That fork is also the claim race. The page lists a job as pending; a worker
claims it before the operator clicks. Dismissing it there would strand a job
the pool is actively working -- the row would read as cancelled while the
handler kept going, and completing it would resurrect it. The row is locked and
re-read here, so the running branch is taken instead.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from autoposter.api.auth import require_session
from autoposter.db.models import Job
from autoposter.db.models import Session as SessionModel
from autoposter.queue.jobs import MAX_ATTEMPTS

router = APIRouter()

# The states this page is about. Terminal ones are excluded on purpose: done
# and dismissed jobs are history, and parked ones belong to Failures, which can
# retry them.
LIVE_STATES = ("running", "pending")

# The prefix of the ItemNotFound message raised when Plex has not indexed the
# file yet (plex/client.py's resolve()). Classifying on the stored error string
# is a heuristic -- it is all a finished attempt leaves behind -- and it is
# deliberately the narrow prefix rather than anything looser: over-claiming
# "waiting for Plex" would also over-report the attempt budget.
WAITING_FOR_PLEX_PREFIX = "no Plex item"

# A full pass enqueues one job per library item -- fifteen thousand of them on
# the library this was built for -- and the page polls every few seconds. The
# list is capped and ``total`` reports what the cap hid, rather than serialising
# the whole queue on a timer.
LIST_LIMIT = 200


def _text(payload: dict, key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _number(payload: dict, key: str) -> int | None:
    value = payload.get(key)
    # bool is an int subclass, and a `true` in a payload field is not a season.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@router.get("/jobs")
async def list_jobs(
    request: Request,
    state: Literal["pending", "running"] | None = None,
    _: SessionModel = Depends(require_session),
) -> dict:
    """Pending and running jobs, soonest first.

    ``state`` narrows to one of the two; omitted, both are returned. Any other
    value is a 422 from validation rather than an empty list, so a typo in the
    query string cannot read as "the queue is empty".
    """
    states = LIVE_STATES if state is None else (state,)
    # config is read per request off the live holder, not captured at startup:
    # resolve_max_attempts is one of the values config/live.py documents as
    # read per use, so a swap must be visible in the very next response.
    config = request.app.state.config_holder.current
    plex_max_attempts = config.plex.resolve_max_attempts

    # Computed by the database clock, matching the one that stamped run_after
    # (queue/jobs.py enqueues with func.now() for exactly this reason). Doing
    # the subtraction in this process would fold in whatever drift there is
    # between the app container's clock and PostgreSQL's.
    run_in = func.extract("epoch", Job.run_after - func.now()).label("run_in_seconds")

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(Job).where(Job.state.in_(states))
            )
        ).scalar_one()
        rows = (
            await session.execute(
                select(Job, run_in)
                .where(Job.state.in_(states))
                # id breaks ties: a batch enqueue stamps one run_after across
                # thousands of rows, and without a total order a capped read
                # returns them in whatever order the scan produced.
                .order_by(Job.run_after, Job.id)
                .limit(LIST_LIMIT)
            )
        ).all()

    jobs = []
    for job, run_in_seconds in rows:
        payload = job.payload if isinstance(job.payload, dict) else {}
        waiting = (job.last_error or "").startswith(WAITING_FOR_PLEX_PREFIX)
        jobs.append(
            {
                "id": job.id,
                "kind": job.kind,
                "state": job.state,
                "attempts": job.attempts,
                "max_attempts": plex_max_attempts if waiting else MAX_ATTEMPTS,
                "waiting_for_plex": waiting,
                # The four payload fields that name the item, and nothing else.
                "title": _text(payload, "title"),
                "item_kind": _text(payload, "kind"),
                "season_number": _number(payload, "season_number"),
                "episode_number": _number(payload, "episode_number"),
                # Negative for a job that is already due (and for every running
                # job, whose run_after is in the past). The page renders that as
                # "now" rather than a countdown, which is the truth.
                "run_in_seconds": round(run_in_seconds),
                "last_error": job.last_error,
                "created_at": job.created_at,
                "cancel_requested": job.cancel_requested,
            }
        )
    return {"jobs": jobs, "total": total}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int, request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """Stop a job, by the means its current state allows.

    ``with_for_update`` is what makes this safe against the worker pool rather
    than merely usually right: the claim in queue/jobs.py is a
    ``SELECT ... FOR UPDATE SKIP LOCKED``, so while this transaction holds the
    row a worker skips it instead of claiming it underneath the check. Read
    without the lock, a job could be claimed between the read and the flip and
    end up dismissed while a handler was already running it.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        job = (
            await session.execute(select(Job).where(Job.id == job_id).with_for_update())
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        if job.state == "pending":
            # Dismissed, not deleted: what was queued and why is worth keeping,
            # and this is the same disposal the Failures page uses.
            job.state = "dismissed"
            await session.commit()
            return {"id": job_id, "state": "dismissed", "cancelled": True}

        if job.state == "running":
            job.cancel_requested = True
            await session.commit()
            return {
                "id": job_id,
                "state": "running",
                "cancel_requested": True,
                "detail": (
                    "the job will finish its current attempt and will not be retried"
                ),
            }

        raise HTTPException(status_code=409, detail=f"job is already {job.state}")
