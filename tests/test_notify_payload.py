"""The notification payload contract (Phase 5a, Task 1).

Both payload shapes are asserted as whole dicts, not key-by-key -- an extra
or missing field is a contract change and must fail here. The mode gate lives
in the config schema (a ``Literal``), so an unknown mode is a validation
error at load time, never a runtime fallback to some default shape.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoposter.config.loader import load_config
from autoposter.notify import payload as payload_module
from autoposter.notify.payload import build_payload

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def _variant(tmp_path, old, new):
    text = EXAMPLE.read_text(encoding="utf-8")
    assert old in text, f"{old!r} is no longer in the example config"
    path = tmp_path / "variant.yaml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


# --- apprise-json mode -------------------------------------------------------


def test_apprise_json_success_shape_exactly():
    built = build_payload(
        "apprise-json",
        event="scheduled_run_completed",
        summary="collections finished: ok",
        detail={"job": "collections", "status": "ok"},
    )
    assert built == {
        "version": "1.0",
        "title": "autoposter: scheduled_run_completed",
        "message": "collections finished: ok",
        "attachments": [],
        "type": "success",
    }


def test_apprise_json_failed_status_shape_exactly():
    built = build_payload(
        "apprise-json",
        event="scheduled_run_completed",
        summary="collections finished: failed",
        detail={"job": "collections", "status": "failed", "error": "boom"},
    )
    assert built == {
        "version": "1.0",
        "title": "autoposter: scheduled_run_completed",
        "message": "collections finished: failed",
        "attachments": [],
        "type": "failure",
    }


def test_apprise_json_without_a_status_is_success():
    """Events with no failure semantics (e.g. full_pass_enqueued) report
    success -- the orphaned n8n gate on ``body.type === "success"`` must pass
    for them if reconnected."""
    built = build_payload(
        "apprise-json",
        event="full_pass_enqueued",
        summary="full pass: 12 queued",
        detail={"total": 12, "queued": 12, "skipped": 0},
    )
    assert built["type"] == "success"


# --- autoposter-v1 mode ------------------------------------------------------


def test_autoposter_v1_shape_exactly(monkeypatch):
    frozen = datetime(2026, 8, 22, 12, 34, 56, tzinfo=timezone.utc)
    monkeypatch.setattr(payload_module, "_utcnow", lambda: frozen)
    built = build_payload(
        "autoposter-v1",
        event="scheduled_run_completed",
        summary="collections finished: ok",
        detail={"job": "collections", "status": "ok"},
    )
    assert built == {
        "schema": "autoposter/v1",
        "event": "scheduled_run_completed",
        "at": "2026-08-22T12:34:56+00:00",
        "summary": "collections finished: ok",
        "detail": {"job": "collections", "status": "ok"},
    }


def test_autoposter_v1_at_is_utc_iso8601():
    built = build_payload("autoposter-v1", event="e", summary="s", detail={})
    parsed = datetime.fromisoformat(built["at"])
    assert parsed.utcoffset() == timedelta(0)


# --- the mode gate -----------------------------------------------------------


def test_build_payload_refuses_an_unknown_mode():
    """Defence in depth for callers that bypass config -- never a silent
    wrong-shaped payload."""
    with pytest.raises(ValueError, match="carrier-pigeon"):
        build_payload("carrier-pigeon", event="e", summary="s", detail={})


def test_an_unknown_mode_is_rejected_at_config_load(tmp_path):
    bad = _variant(tmp_path, "mode: apprise-json", "mode: carrier-pigeon")
    with pytest.raises(ValueError, match="apprise-json"):
        load_config(bad)


# --- the config block --------------------------------------------------------


def test_example_config_notifications_defaults():
    n = load_config(EXAMPLE).notifications
    assert n.enabled is False
    assert n.url == ""
    assert n.mode == "apprise-json"
    assert n.timeout_seconds == 10
    assert n.retry_count == 3


def test_toggling_notifications_does_not_change_the_render_version(tmp_path):
    """The notifications block must never invalidate stored render
    fingerprints -- same cutover concern as ``adopt.apply`` in
    ``test_config.py``."""
    changed = load_config(
        _variant(tmp_path, "enabled: false # POST run-completion", "enabled: true # POST run-completion")
    )
    assert changed.notifications.enabled is True
    assert changed.version == load_config(EXAMPLE).version
