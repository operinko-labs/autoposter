"""Notification payload builders: the two shapes a configured webhook receives.

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
"""

from datetime import datetime, timezone

# NotifyJSON.json_version in Apprise's custom_json.py.
APPRISE_JSON_VERSION = "1.0"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    raise ValueError(f"unknown notification mode {mode!r}")
