import json
import secrets as secrets_module
from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException, Request

from autoposter.db.models import EventLog
from autoposter.intake.arr import RenderIntent, parse_radarr, parse_sonarr
from autoposter.queue.jobs import enqueue

router = APIRouter()

# Cap on the raw body text stored on an unparseable delivery, so a huge
# malformed payload cannot bloat the events_log row.
_MAX_RAW_BODY_CHARS = 2000


def _authorise(request: Request, token: str | None) -> None:
    expected = request.app.state.secrets.webhook_secret
    # compare_digest raises TypeError on non-ASCII str input; comparing bytes
    # instead is constant-time and never raises regardless of content.
    if not token or not secrets_module.compare_digest(
        token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid or missing X-Autoposter-Token")


async def _ingest(request: Request, source: str, parser) -> dict:
    config = request.app.state.config
    session_factory = request.app.state.session_factory

    raw_body = await request.body()

    error: str | None = None
    event_type: str | None = None
    payload_for_log: dict = {}
    intents: list[RenderIntent] = []

    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError("payload is not a JSON object")
        payload_for_log = payload
        event_type = payload.get("eventType")
        intents = parser(payload)
    except Exception as exc:
        error = f"unparseable payload: {exc}"
        text = raw_body.decode("utf-8", errors="replace")
        if len(text) > _MAX_RAW_BODY_CHARS:
            text = text[:_MAX_RAW_BODY_CHARS] + "...(truncated)"
        payload_for_log = {"_raw": text}

    queued = 0
    async with session_factory() as session:
        session.add(
            EventLog(
                source=source,
                event_type=event_type,
                payload=payload_for_log,
                outcome=error if error is not None else f"{len(intents)} intents",
            )
        )
        await session.commit()

        if error is None:
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

    if error is not None:
        raise HTTPException(status_code=400, detail=error)

    return {"intents": len(intents), "queued": queued}


@router.post("/webhook/radarr")
async def radarr_webhook(
    request: Request, x_autoposter_token: str | None = Header(default=None)
) -> dict:
    _authorise(request, x_autoposter_token)
    return await _ingest(request, "radarr", parse_radarr)


@router.post("/webhook/sonarr")
async def sonarr_webhook(
    request: Request, x_autoposter_token: str | None = Header(default=None)
) -> dict:
    _authorise(request, x_autoposter_token)
    return await _ingest(request, "sonarr", parse_sonarr)


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
