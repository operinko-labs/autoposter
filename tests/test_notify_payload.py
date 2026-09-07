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
from autoposter.notify.payload import (
    DISCORD_FIELD_NAME_LIMIT,
    DISCORD_FIELD_VALUE_LIMIT,
    build_payload,
)

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


# --- discord mode ------------------------------------------------------------
#
# Discord's own limits are enforced in the builder, not hoped for: an
# over-length embed field is a 400, and dispatch.py refuses to retry a 4xx, so
# the notification would simply be lost with one warning. Each limit gets a
# test because each one is a silent data-loss bug when it is missed.

_FIXED_NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(payload_module, "_utcnow", lambda: _FIXED_NOW)
    return _FIXED_NOW


def test_discord_success_shape_exactly(frozen_clock):
    built = build_payload(
        "discord",
        event="scheduled_run_completed",
        summary="scheduled run collections finished: ok",
        detail={"job": "collections", "status": "ok", "detail": "nothing to do"},
    )
    assert built == {
        "embeds": [
            {
                "title": "autoposter: scheduled_run_completed",
                "description": "scheduled run collections finished: ok",
                "color": 0x57F287,
                "timestamp": frozen_clock.isoformat(),
                "fields": [
                    {"name": "job", "value": "collections", "inline": True},
                    {"name": "status", "value": "ok", "inline": True},
                    {"name": "detail", "value": "nothing to do", "inline": True},
                ],
            }
        ],
        "allowed_mentions": {"parse": []},
    }


def test_discord_failed_status_is_red(frozen_clock):
    built = build_payload(
        "discord",
        event="scheduled_run_failed",
        summary="scheduled run collections failed: PlexApiError",
        detail={"job": "collections", "status": "failed", "detail": "PlexApiError"},
    )
    assert built["embeds"][0]["color"] == 0xFF0000


def test_discord_flattens_the_detail_dict_generically(frozen_clock):
    """No per-event shapes: collection_changed's four keys become four fields
    with no special-casing in payload.py."""
    built = build_payload(
        "discord",
        event="collection_changed",
        summary="Movies: 'Top Rated' changed: +3 -1",
        detail={"library": "Movies", "collection": "Top Rated", "added": 3, "removed": 1},
    )
    assert built["embeds"][0]["fields"] == [
        {"name": "library", "value": "Movies", "inline": True},
        {"name": "collection", "value": "Top Rated", "inline": True},
        {"name": "added", "value": "3", "inline": True},
        {"name": "removed", "value": "1", "inline": True},
    ]


def test_discord_sends_exactly_one_embed(frozen_clock):
    """Discord accepts at most 10; we send one, always."""
    built = build_payload("discord", event="e", summary="s", detail={})
    assert len(built["embeds"]) == 1


def test_discord_title_is_clipped_at_256(frozen_clock):
    built = build_payload("discord", event="x" * 400, summary="s", detail={})
    title = built["embeds"][0]["title"]
    assert len(title) == 256
    assert title.endswith("...[truncated]")


def test_discord_description_is_clipped_at_4096(frozen_clock):
    built = build_payload("discord", event="e", summary="y" * 5000, detail={})
    description = built["embeds"][0]["description"]
    assert len(description) == 4096
    assert description.endswith("...[truncated]")


def test_discord_field_value_is_clipped_at_1024(frozen_clock):
    """scheduler/core.py:304 truncates a failure detail to 2000 chars -- the
    real payload, not a synthetic one. An embed field value tops out at 1024,
    so this is the collision that would otherwise produce a 400."""
    built = build_payload(
        "discord",
        event="scheduled_run_completed",
        summary="scheduled run collections finished: failed",
        detail={"job": "collections", "status": "failed", "detail": "z" * 2000},
    )
    value = built["embeds"][0]["fields"][2]["value"]
    assert len(value) == 1024
    assert value.endswith("...[truncated]")


def test_discord_field_name_is_clipped_at_256(frozen_clock):
    built = build_payload("discord", event="e", summary="s", detail={"k" * 400: "v"})
    name = built["embeds"][0]["fields"][0]["name"]
    assert len(name) == 256
    assert name.endswith("...[truncated]")


def test_discord_caps_fields_at_25_and_marks_the_omission(frozen_clock):
    """detail is an open dict by contract, so the cap is not optional even
    though today's largest event carries four keys."""
    built = build_payload(
        "discord",
        event="e",
        summary="s",
        detail={f"k{n:02d}": "v" for n in range(40)},
    )
    fields = built["embeds"][0]["fields"]
    assert len(fields) == 25
    assert fields[-1] == {
        "name": "...[truncated]",
        "value": "16 more field(s) omitted",
        "inline": False,
    }


def test_discord_embed_total_never_exceeds_6000(frozen_clock):
    """The worst case this service can actually produce: a maximal summary and
    twenty detail keys each holding a maximal truncated detail string."""
    built = build_payload(
        "discord",
        event="e" * 300,
        summary="s" * 5000,
        detail={f"k{n:02d}": "v" * 2000 for n in range(20)},
    )
    embed = built["embeds"][0]
    total = (
        len(embed["title"])
        + len(embed["description"])
        + sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
    )
    assert total <= 6000


def test_discord_suppresses_every_mention(frozen_clock):
    """A Plex collection titled @everyone must not ping a server. Titles and
    library names flow into summary and detail straight from Plex."""
    built = build_payload(
        "discord",
        event="collection_changed",
        summary="Movies: '@everyone' changed: +1 -0",
        detail={"library": "Movies", "collection": "@everyone", "added": 1, "removed": 0},
    )
    assert built["allowed_mentions"] == {"parse": []}


def test_discord_empty_detail_value_renders_as_a_placeholder_not_empty(frozen_clock):
    """scheduler/core.py:251 -- ``detail = await job.run(session) or ""`` --
    puts an empty string in the detail dict on the success path of any job
    whose run() returns nothing. An empty embed field value is a Discord 400
    that dispatch.py will not retry, so this must never reach the wire."""
    built = build_payload(
        "discord",
        event="scheduled_run_completed",
        summary="scheduled run collections finished: ok",
        detail={"job": "collections", "status": "ok", "detail": ""},
    )
    for field in built["embeds"][0]["fields"]:
        assert field["name"] != ""
        assert field["value"] != ""
    detail_field = built["embeds"][0]["fields"][2]
    assert detail_field == {"name": "detail", "value": "(empty)", "inline": True}


def test_discord_empty_rating_key_renders_as_a_placeholder(frozen_clock):
    """collections/engine.py:1605 -- ``"rating_key": str(getattr(collection,
    "ratingKey", "") or "")`` -- is empty on every collection_deleted where
    the Plex object carries no ratingKey."""
    built = build_payload(
        "discord",
        event="collection_deleted",
        summary="deleted collection 'Old Stuff' in Movies",
        detail={
            "library": "Movies",
            "collection": "Old Stuff",
            "rating_key": "",
            "reason": "stale",
        },
    )
    for field in built["embeds"][0]["fields"]:
        assert field["value"] != ""
    rating_key_field = built["embeds"][0]["fields"][2]
    assert rating_key_field == {"name": "rating_key", "value": "(empty)", "inline": True}


def test_discord_none_detail_value_renders_as_a_placeholder_not_the_string_none(frozen_clock):
    built = build_payload(
        "discord",
        event="e",
        summary="s",
        detail={"job": "collections", "status": "ok", "detail": None},
    )
    detail_field = built["embeds"][0]["fields"][2]
    assert detail_field == {"name": "detail", "value": "(empty)", "inline": True}


def test_discord_empty_field_placeholder_is_inside_every_limit(frozen_clock):
    """The placeholder itself must never be the thing that blows a limit."""
    built = build_payload("discord", event="e", summary="s", detail={"": ""})
    field = built["embeds"][0]["fields"][0]
    assert field["name"] == "(empty)"
    assert field["value"] == "(empty)"
    assert len(field["name"]) <= DISCORD_FIELD_NAME_LIMIT
    assert len(field["value"]) <= DISCORD_FIELD_VALUE_LIMIT
    assert field["name"] != ""
    assert field["value"] != ""


def test_mode_discord_loads_from_config(tmp_path):
    loaded = load_config(_variant(tmp_path, "mode: apprise-json", "mode: discord"))
    assert loaded.notifications.mode == "discord"


# --- apprise-api mode --------------------------------------------------------
#
# The Apprise API SERVER, not the json:// sender apprise-json impersonates:
# opposite directions of the same integration. The difference that matters is
# `body` vs `message`, and it is load-bearing -- an Apprise API server 400s on
# a body-less payload, which dispatch.py then correctly refuses to retry.


def test_apprise_api_success_shape_exactly():
    built = build_payload(
        "apprise-api",
        event="full_pass_enqueued",
        summary="full pass enqueued: 12 queued, 3 skipped, 15 total",
        detail={"total": 15, "queued": 12, "skipped": 3},
    )
    assert built == {
        "title": "autoposter: full_pass_enqueued",
        "body": "full pass enqueued: 12 queued, 3 skipped, 15 total",
        "type": "success",
    }


def test_apprise_api_failed_status_shape_exactly():
    built = build_payload(
        "apprise-api",
        event="scheduled_run_failed",
        summary="scheduled run collections failed: PlexApiError",
        detail={"job": "collections", "status": "failed", "detail": "PlexApiError"},
    )
    assert built == {
        "title": "autoposter: scheduled_run_failed",
        "body": "scheduled run collections failed: PlexApiError",
        "type": "failure",
    }


def test_apprise_api_omits_tag_and_format():
    """Both are optional with server-side defaults (`all` routing and `text`),
    and our summaries are plain text. Shipping config for an unconfigured
    target would be speculative."""
    built = build_payload("apprise-api", event="e", summary="s", detail={})
    assert set(built) == {"title", "body", "type"}


def test_mode_apprise_api_loads_from_config(tmp_path):
    loaded = load_config(_variant(tmp_path, "mode: apprise-json", "mode: apprise-api"))
    assert loaded.notifications.mode == "apprise-api"


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
    ``test_config.py``. ``mode`` as well as ``enabled``: row 20 added two
    values to that Literal, and ``config/loader.py``'s render-version input is
    an explicit six-key dict that does not include ``notifications``."""
    baseline = load_config(EXAMPLE).version
    changed = load_config(
        _variant(tmp_path, "enabled: false # POST run-completion", "enabled: true # POST run-completion")
    )
    assert changed.notifications.enabled is True
    assert changed.version == baseline

    remoded = load_config(_variant(tmp_path, "mode: apprise-json", "mode: discord"))
    assert remoded.notifications.mode == "discord"
    assert remoded.version == baseline


# --- row 19's new events, pinned per mode -------------------------------------
#
# ``build_payload`` is generic over ``event``/``detail`` -- these two events
# get no special-casing in payload.py. The point of these tests is not the
# builder (already covered above); it is nailing down, for the two events row
# 19 actually introduced, what a consumer configured each way really
# receives: ``apprise-json`` (the default) drops ``detail`` entirely, so the
# collection/library/added/removed and rating_key/reason facts never reach an
# apprise-json consumer -- only ``autoposter-v1`` carries them. Shipped
# behaviour, not a new requirement; these pin it so it cannot regress or drift
# unnoticed.

_CHANGED_DETAIL = {
    "library": "Movies",
    "collection": "Hand Picked",
    "added": 3,
    "removed": 1,
}

_DELETED_DETAIL = {
    "library": "Movies",
    "collection": "Hand Picked",
    "rating_key": "12345",
    "reason": "unconfigured",
}


def test_apprise_json_collection_changed_has_no_detail():
    built = build_payload(
        "apprise-json",
        event="collection_changed",
        summary="Movies: 'Hand Picked' changed: +3 -1",
        detail=_CHANGED_DETAIL,
    )
    assert built == {
        "version": "1.0",
        "title": "autoposter: collection_changed",
        "message": "Movies: 'Hand Picked' changed: +3 -1",
        "attachments": [],
        "type": "success",
    }
    assert "detail" not in built


def test_autoposter_v1_collection_changed_carries_full_detail(monkeypatch):
    frozen = datetime(2026, 8, 22, 12, 34, 56, tzinfo=timezone.utc)
    monkeypatch.setattr(payload_module, "_utcnow", lambda: frozen)
    built = build_payload(
        "autoposter-v1",
        event="collection_changed",
        summary="Movies: 'Hand Picked' changed: +3 -1",
        detail=_CHANGED_DETAIL,
    )
    assert built == {
        "schema": "autoposter/v1",
        "event": "collection_changed",
        "at": "2026-08-22T12:34:56+00:00",
        "summary": "Movies: 'Hand Picked' changed: +3 -1",
        "detail": _CHANGED_DETAIL,
    }


def test_apprise_json_collection_deleted_has_no_detail():
    built = build_payload(
        "apprise-json",
        event="collection_deleted",
        summary="deleted collection 'Hand Picked' in Movies",
        detail=_DELETED_DETAIL,
    )
    assert built == {
        "version": "1.0",
        "title": "autoposter: collection_deleted",
        "message": "deleted collection 'Hand Picked' in Movies",
        "attachments": [],
        "type": "success",
    }
    assert "detail" not in built


def test_autoposter_v1_collection_deleted_carries_full_detail(monkeypatch):
    frozen = datetime(2026, 8, 22, 12, 34, 56, tzinfo=timezone.utc)
    monkeypatch.setattr(payload_module, "_utcnow", lambda: frozen)
    built = build_payload(
        "autoposter-v1",
        event="collection_deleted",
        summary="deleted collection 'Hand Picked' in Movies",
        detail=_DELETED_DETAIL,
    )
    assert built == {
        "schema": "autoposter/v1",
        "event": "collection_deleted",
        "at": "2026-08-22T12:34:56+00:00",
        "summary": "deleted collection 'Hand Picked' in Movies",
        "detail": _DELETED_DETAIL,
    }
