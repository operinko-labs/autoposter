import secrets as secrets_module
from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException, Request

from autoposter.db.models import EventLog
from autoposter.intake.arr import RenderIntent, parse_radarr, parse_sonarr
from autoposter.queue.jobs import enqueue

router = APIRouter()


def _authorise(request: Request, token: str | None) -> None:
    expected = request.app.state.secrets.webhook_secret
    if not token or not secrets_module.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-Autoposter-Token")


async def _ingest(request: Request, payload: dict, source: str, parser) -> dict:
    config = request.app.state.config
    session_factory = request.app.state.session_factory
    intents: list[RenderIntent] = parser(payload)

    queued = 0
    async with session_factory() as session:
        session.add(
            EventLog(
                source=source,
                event_type=payload.get("eventType"),
                payload=payload,
                outcome=f"{len(intents)} intents",
            )
        )
        await session.commit()

        for intent in intents:
            job_id = await enqueue(
                session,
                kind="process_item",
                payload=asdict(intent),
                dedupe_key=intent.dedupe_key,
                delay_seconds=config.settle_seconds,
            )
            if job_id is not None:
                queued += 1

    return {"intents": len(intents), "queued": queued}


@router.post("/webhook/radarr")
async def radarr_webhook(
    request: Request, x_autoposter_token: str | None = Header(default=None)
) -> dict:
    _authorise(request, x_autoposter_token)
    return await _ingest(request, await request.json(), "radarr", parse_radarr)


@router.post("/webhook/sonarr")
async def sonarr_webhook(
    request: Request, x_autoposter_token: str | None = Header(default=None)
) -> dict:
    _authorise(request, x_autoposter_token)
    return await _ingest(request, await request.json(), "sonarr", parse_sonarr)


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
