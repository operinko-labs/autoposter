"""The /api router: login, logout and everything behind require_session."""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from autoposter.api.auth import (
    create_session,
    hash_password,
    require_session,
    revoke,
    session_for_token,
    verify_password,
)
from autoposter.db.models import EventLog, Job, ScheduledRun
from autoposter.db.models import Session as SessionModel

logger = logging.getLogger(__name__)

# jobs.state values (see db/models.py's Job docstring); always reported even
# when zero, so an empty database returns zeroed counts rather than an
# incomplete dict.
JOB_STATES = ("pending", "running", "done", "failed", "parked")

DEFAULT_EVENTS_LIMIT = 50
MAX_EVENTS_LIMIT = 200

router = APIRouter(prefix="/api")

# How long an issued session stays valid before the operator has to log in
# again.
SESSION_TTL_HOURS = 24

# Verified on every login attempt where no admin password is configured, so
# that response timing cannot reveal whether AUTOPOSTER_ADMIN_PASSWORD_HASH
# is set -- see verify_password's docstring in api/auth.py.
_DUMMY_HASH = hash_password("no admin password is configured")


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request) -> dict:
    secrets = request.app.state.secrets
    admin_hash = secrets.admin_password_hash
    if not admin_hash:
        logger.warning(
            "AUTOPOSTER_ADMIN_PASSWORD_HASH is not set; this deployment has no "
            "admin password configured, so every login attempt will fail"
        )
    # Always run the bcrypt check, even with no admin password configured --
    # short-circuiting here would let response timing reveal whether the
    # deployment is configured.
    valid = verify_password(body.password, admin_hash or _DUMMY_HASH)
    if not admin_hash or not valid:
        raise HTTPException(status_code=401, detail="invalid credentials")

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        token = await create_session(session, ttl_hours=SESSION_TTL_HOURS)
        row = await session_for_token(session, token)
    return {"token": token, "expires_at": row.expires_at}


@router.get("/me")
async def me(current: SessionModel = Depends(require_session)) -> dict:
    return {"authenticated": True, "expires_at": current.expires_at}


@router.post("/logout")
async def logout(
    request: Request,
    authorization: str | None = Header(default=None),
    _: SessionModel = Depends(require_session),
) -> dict:
    token = authorization.removeprefix("Bearer ")
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await revoke(session, token)
    return {"ok": True}


@router.get("/status")
async def status(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # GROUP BY in SQL rather than fetching every job row and counting in
        # Python -- the jobs table is the hot one at this library's size.
        state_counts = (
            await session.execute(select(Job.state, func.count()).group_by(Job.state))
        ).all()
        jobs_by_state = dict.fromkeys(JOB_STATES, 0)
        for state, count in state_counts:
            jobs_by_state[state] = count

        processed_last_24h = (
            await session.execute(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.state == "done",
                    Job.updated_at >= func.now() - timedelta(hours=24),
                )
            )
        ).scalar_one()

        scheduled_rows = (
            (await session.execute(select(ScheduledRun).order_by(ScheduledRun.name)))
            .scalars()
            .all()
        )

    return {
        "jobs_by_state": jobs_by_state,
        "workers": request.app.state.config.workers,
        "processed_last_24h": processed_last_24h,
        "scheduled_jobs": [
            {
                "name": row.name,
                "last_started_at": row.last_started_at,
                "last_finished_at": row.last_finished_at,
                "last_status": row.last_status,
                "last_detail": row.last_detail,
            }
            for row in scheduled_rows
        ],
    }


@router.get("/events")
async def events(
    request: Request,
    limit: int = DEFAULT_EVENTS_LIMIT,
    _: SessionModel = Depends(require_session),
) -> dict:
    capped_limit = min(max(limit, 1), MAX_EVENTS_LIMIT)
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # payload is never selected -- it holds whole webhook bodies and can
        # carry tokens from the sending service.
        rows = (
            await session.execute(
                select(
                    EventLog.source, EventLog.event_type, EventLog.outcome, EventLog.received_at
                )
                .order_by(EventLog.received_at.desc())
                .limit(capped_limit)
            )
        ).all()
    return {
        "events": [
            {
                "source": row.source,
                "event_type": row.event_type,
                "outcome": row.outcome,
                "received_at": row.received_at,
            }
            for row in rows
        ]
    }
