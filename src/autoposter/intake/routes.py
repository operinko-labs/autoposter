import json
import logging
import secrets as secrets_module
from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from autoposter.db.models import EventLog
from autoposter.intake.arr import (
    RADARR_EVENTS,
    SONARR_EVENTS,
    ArrEnvelope,
    RadarrPayload,
    RenderIntent,
    SonarrPayload,
    parse_radarr,
    parse_sonarr,
)
from autoposter.queue.jobs import enqueue

logger = logging.getLogger(__name__)

router = APIRouter()

# Cap on the raw body text stored on an unparseable delivery, so a huge
# malformed payload cannot bloat the events_log row.
_MAX_RAW_BODY_CHARS = 2000

# Fixed, and fixed on purpose: this string is the 400 body returned to
# whatever posted to the port AND it is EventLog.outcome, which /api/events
# and the dashboard stream serve. Row 213's 2026-09-05 amendment lets the
# /api request-body 422 serve pydantic's type/loc/msg, because that caller is
# the operator; this caller is a stranger, so it gets a sentence and nothing
# else. The field names go to the pod log instead, at DEBUG.
NOT_AN_ARR_DELIVERY = "body does not match the webhook schema for this service"


def _without_nul(value: object) -> object:
    """Strip a literal NUL (U+0000) from every string in a JSON-shaped value.

    Postgres refuses NUL outright in both ``VARCHAR`` and ``JSONB`` columns.
    ``ArrEnvelope``/``RadarrPayload``/``SonarrPayload`` now refuse a NUL in
    ``eventType`` or a title before it is work, but the row committed below
    always keeps the *raw* body as evidence -- refused or not -- so a NUL
    anywhere else in that body would still crash this insert. Applied only to
    the stored copy: validation and parsing still see the untouched payload.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {key: _without_nul(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_nul(item) for item in value]
    return value


def _log_refusal(source: str, exc: ValidationError) -> None:
    """Say which fields failed, and nothing about what was in them.

    ``loc`` holds this module's own declared field names and list indices and
    nothing else, because every model in ``intake/arr.py`` is
    ``extra="ignore"``: an unknown key is dropped rather than reported, so no
    part of ``loc`` can be a string the sender chose. ``msg``, ``input``,
    ``ctx`` and ``url`` do quote the body -- excluded from ``errors()``
    itself, so the no-leak property holds even if a future edit here forgets
    to filter them back out.

    DEBUG rather than WARNING: a refusal is the expected outcome of a port
    scan, and a scanner must not be able to fill the pod log.
    """
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    fields = sorted({part for error in errors for part in error["loc"] if isinstance(part, str)})
    logger.debug("%s webhook body refused (%s): %s", source, type(exc).__name__, ", ".join(fields))


def _authorise(request: Request, token: str | None) -> None:
    expected = request.app.state.secrets.webhook_secret
    # compare_digest raises TypeError on non-ASCII str input; comparing bytes
    # instead is constant-time and never raises regardless of content.
    if not token or not secrets_module.compare_digest(
        token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid or missing X-Autoposter-Token")


async def _ingest(
    request: Request,
    source: str,
    parser,
    accepted_events: set[str],
    model: type[ArrEnvelope],
) -> dict:
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
    except Exception as exc:
        # Class name only: this string is the 400 body sent back to the
        # sender AND EventLog.outcome, served by /api/events. A decode
        # error's own text quotes positions and bytes of the payload; the
        # capped raw body kept on the row below is the evidence.
        error = f"unparseable payload ({type(exc).__name__})"
        text = raw_body.decode("utf-8", errors="replace")
        if len(text) > _MAX_RAW_BODY_CHARS:
            text = text[:_MAX_RAW_BODY_CHARS] + "...(truncated)"
        payload_for_log = {"_raw": text}
    else:
        payload_for_log = _without_nul(payload)
        accepted = False
        try:
            # Stage one, for every body: is this a plausible delivery at all?
            event_type = ArrEnvelope.model_validate(payload).eventType
            if event_type.lower() in accepted_events:
                # Stage two, only for an event we do work for. An unaccepted
                # one -- Test, Grab, Health, an Arr we do not know -- falls
                # past both branches to a 200 with zero intents and one
                # events_log row, which is today's behaviour and deliberate:
                # those bodies carry no movie/series key at all, and a 400
                # would be a warning line in the operator's own Arr log on
                # every delivery.
                model.model_validate(payload)
                accepted = True
        except ValidationError as exc:
            _log_refusal(source, exc)
            error = NOT_AN_ARR_DELIVERY

        if accepted:
            try:
                intents = parser(payload)
            except Exception as exc:
                # A bug in our parser, not a malformed request from the
                # sender: the gate above has already refused every shape the
                # sender could have chosen. Preserve the structured payload as
                # evidence and commit it before propagating, then let the
                # exception surface as a 500. The outcome is served by
                # /api/events, so it carries the class name only; the
                # traceback reaches the pod log with the 500.
                async with session_factory() as session:
                    session.add(
                        EventLog(
                            source=source,
                            event_type=event_type,
                            payload=payload_for_log,
                            outcome=f"parser error ({type(exc).__name__})",
                        )
                    )
                    await session.commit()
                raise

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
    return await _ingest(request, "radarr", parse_radarr, RADARR_EVENTS, RadarrPayload)


@router.post("/webhook/sonarr")
async def sonarr_webhook(
    request: Request, x_autoposter_token: str | None = Header(default=None)
) -> dict:
    _authorise(request, x_autoposter_token)
    return await _ingest(request, "sonarr", parse_sonarr, SONARR_EVENTS, SonarrPayload)


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
