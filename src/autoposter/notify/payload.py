"""Notification payload builders: the four shapes a configured webhook receives.

Apprise ``json://`` shape, verified 2026-08-22 against the plugin source --
``NotifyJSON.send`` in
https://github.com/caronc/apprise/blob/master/apprise/plugins/custom_json.py:

    payload = {
        JSONPayloadField.VERSION: self.json_version,        # "version": "1.0"
        JSONPayloadField.TITLE: title,                      # "title"
        JSONPayloadField.MESSAGE: body,                     # "message"
        JSONPayloadField.ATTACHMENTS: attachments,          # "attachments"
        JSONPayloadField.MESSAGETYPE: notify_type.value,    # "type"
    }

Findings against the plan:

- The field set (``version``, ``title``, ``message``, ``type``,
  ``attachments``) matches the plan, with one correction: ``attachments`` is
  ALWAYS present -- an empty list when there are none -- not only "when
  present". We always send ``[]``; this service never attaches files.
- ``type`` takes ``NotifyType`` values (``apprise/common.py`` in the same
  repository): ``info`` | ``success`` | ``warning`` | ``failure``. We emit
  only ``success`` and ``failure``: a payload reports a finished run, and the
  consumer pattern this supports is a gate on exactly ``type === "success"``
  (the user's n8n flow carried one). Derivation: ``detail["status"] ==
  "failed"`` means failure, anything else -- including no status at all --
  is success; ``scheduler/core.py`` writes exactly ``"ok"`` or ``"failed"``,
  and events without failure semantics (``full_pass_enqueued``) carry none.

The native ``autoposter-v1`` shape is this service's own versioned contract:
``schema`` names the contract, ``detail`` carries the full event dict that
apprise-json mode has no field for. ``at`` is the payload build time from the
service clock, ISO 8601 UTC with an explicit ``+00:00`` offset (the project's
DB-clock discipline governs database writes; a payload is not one, and the
timestamp must be readable by consumers that never see the database).

The mode gate is ``NotificationsConfig.mode``'s ``Literal`` in
``config/schema.py`` -- an unknown mode fails config validation at load time.
The ``ValueError`` below is defence in depth for callers that bypass config,
never a fallback to some default shape.

The ``discord`` shape is a webhook embed. Discord's limits are stated from
my own knowledge and cross-checked against Posterizarr's own embed
(``modules/functions/Notifications.ps1``, which agrees on every field name
and on both colour constants): at most 10 embeds, ``title`` 256 characters,
``description`` 4096, at most 25 ``fields`` of ``name`` 256 / ``value``
1024, and 6000 characters across the whole embed. Two of those collide with
what this service actually sends -- ``scheduler/core.py`` truncates a run
detail to 2000 characters, over a field value's 1024 -- so the builder
enforces every limit rather than hoping; an over-length embed is a 400, and
``dispatch.py`` correctly does not retry a 4xx. ``fields`` is a GENERIC
flattening of ``detail``, so every event the taxonomy has or gains is
covered with no per-event table. Success is 204 with an empty body, which
``httpx.Response.is_success`` already accepts.

The ``apprise-api`` shape is the Apprise API SERVER's request body -- the
opposite direction from ``apprise-json``, which impersonates Apprise as a
sender. Read 2026-09-07 from the project's own sources:

    https://github.com/caronc/apprise-api
        -- the README's "API Details" section, POST /notify/{KEY}
    https://raw.githubusercontent.com/caronc/apprise-api/master/apprise_api/api/forms.py
        -- NotifyForm's field definitions

Accepted fields: ``body`` (the only required one), ``title``, ``type``,
``tag``, ``format``, ``attachment``; the stateless POST /notify route adds
``urls``. ``type`` takes the same NotifyType vocabulary apprise-json cites
above -- ``info`` | ``success`` | ``warning`` | ``failure``, defaulting to
``info`` -- and ``format`` takes ``text`` | ``markdown`` | ``html``,
defaulting to ``text``. We send ``title``, ``body`` and ``type`` only:
``tag`` and ``format`` both have server-side defaults that are already right
(``text``, for plain-text summaries), and a config field for an unconfigured
target would be speculative. ``body`` vs apprise-json's ``message`` is the
whole difference between the two, and it is load-bearing: an Apprise API
server answers a body-less payload with a 400, which dispatch.py then
correctly refuses to retry.
"""

from datetime import datetime, timezone

# NotifyJSON.json_version in Apprise's custom_json.py.
APPRISE_JSON_VERSION = "1.0"

# Discord's documented embed limits. Enforced in the builder because an
# over-length embed is a 400, and dispatch.py correctly refuses to retry a
# 4xx -- so a missed limit loses the notification silently.
DISCORD_TITLE_LIMIT = 256
DISCORD_DESCRIPTION_LIMIT = 4096
DISCORD_FIELD_LIMIT = 25
DISCORD_FIELD_NAME_LIMIT = 256
DISCORD_FIELD_VALUE_LIMIT = 1024
DISCORD_EMBED_TOTAL_LIMIT = 6000

# Discord's own brand red and green, the two Posterizarr used. Keyed off the
# SAME severity derivation apprise-json uses below -- one derivation, four
# modes. No amber tier: it keyed off fallback/truncated counters this service
# does not have, and a third severity in one mode only is exactly the drift
# the single derivation exists to prevent.
DISCORD_COLOR_FAILURE = 0xFF0000
DISCORD_COLOR_SUCCESS = 0x57F287

# Visible, not silent: a consumer reading a clipped value must be able to see
# that it was clipped. ASCII, like the rest of this module.
TRUNCATION_MARKER = "...[truncated]"

# Headroom held back from the 6000-character embed budget for the omission
# marker field, so appending it can never push the embed over the limit.
_OMISSION_RESERVE = 64

# Discord rejects an embed field whose name or value is empty with a 400,
# and dispatch.py does not retry a 4xx -- so a detail dict carrying "", None,
# or a whitespace-only string for some key or value (scheduler/core.py's
# success-path ``detail = ... or ""``; collections/engine.py's ``rating_key``
# when the Plex object carries none) would otherwise drop the notification
# silently. Every flattened key and value renders as this instead of nothing.
EMPTY_FIELD_PLACEHOLDER = "(empty)"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clipped(text: str, limit: int) -> str:
    """``text`` at or under ``limit`` characters, marked when it was cut."""
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _field_text(value: object) -> str:
    """``value`` stringified for an embed field -- never empty or blank.

    ``None`` and a blank/whitespace-only string both stringify to something
    Discord's ``BASE_TYPE_REQUIRED`` (or, for ``None``, the literal and
    unreadable ``"None"``) would otherwise let through; both render as the
    placeholder instead.
    """
    if value is None:
        return EMPTY_FIELD_PLACEHOLDER
    text = str(value)
    return text if text.strip() else EMPTY_FIELD_PLACEHOLDER


def _discord_fields(detail: dict, budget: int) -> list[dict]:
    """The ``detail`` dict flattened into embed fields, generically.

    No per-event shapes: every key becomes one field, so all five events --
    and every future one -- are covered with no table to maintain. Stops at
    the field cap or the character budget, whichever comes first, and says so
    with a final marker field rather than dropping data silently.
    """
    fields: list[dict] = []
    omitted = 0
    items = list(detail.items())
    for index, (key, value) in enumerate(items):
        name = _clipped(_field_text(key), DISCORD_FIELD_NAME_LIMIT)
        text = _clipped(_field_text(value), DISCORD_FIELD_VALUE_LIMIT)
        if len(fields) >= DISCORD_FIELD_LIMIT - 1 or len(name) + len(text) > budget:
            omitted = len(items) - index
            break
        budget -= len(name) + len(text)
        fields.append({"name": name, "value": text, "inline": True})
    if omitted:
        fields.append(
            {
                "name": TRUNCATION_MARKER,
                "value": f"{omitted} more field(s) omitted",
                "inline": False,
            }
        )
    return fields


def build_payload(mode: str, event: str, summary: str, detail: dict) -> dict:
    """Build the POST body for one notification in the configured ``mode``."""
    if mode == "apprise-json":
        return {
            "version": APPRISE_JSON_VERSION,
            "title": f"autoposter: {event}",
            "message": summary,
            "attachments": [],
            "type": "failure" if detail.get("status") == "failed" else "success",
        }
    if mode == "autoposter-v1":
        return {
            "schema": "autoposter/v1",
            "event": event,
            "at": _utcnow().isoformat(),
            "summary": summary,
            "detail": detail,
        }
    if mode == "discord":
        title = _clipped(f"autoposter: {event}", DISCORD_TITLE_LIMIT)
        description = _clipped(summary, DISCORD_DESCRIPTION_LIMIT)
        # title + description is at most 256 + 4096 = 4352, so a positive
        # field budget always remains and dropping fields always converges.
        budget = DISCORD_EMBED_TOTAL_LIMIT - len(title) - len(description) - _OMISSION_RESERVE
        return {
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": (
                        DISCORD_COLOR_FAILURE
                        if detail.get("status") == "failed"
                        else DISCORD_COLOR_SUCCESS
                    ),
                    "timestamp": _utcnow().isoformat(),
                    "fields": _discord_fields(detail, budget),
                }
            ],
            # A Plex collection titled @everyone must not ping a server.
            # Titles and library names reach summary and detail straight from
            # Plex, so this is categorical rather than a sanitiser.
            "allowed_mentions": {"parse": []},
        }
    if mode == "apprise-api":
        return {
            "title": f"autoposter: {event}",
            "body": summary,
            "type": "failure" if detail.get("status") == "failed" else "success",
        }
    raise ValueError(f"unknown notification mode {mode!r}")
