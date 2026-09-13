from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from autoposter import deliveries
from autoposter.db.models import Render, RenderDelivery
from autoposter.render import pipeline
from media_server_doubles import resolved


async def _render(session, server="plex", native="1"):
    row = await pipeline._upsert_media_item(session, resolved(server, native, file_path="/m.mkv"))
    render = await pipeline._get_or_create_render(session, row, "poster", "/a/p.jpg")
    await session.commit()
    return render


async def test_record_and_rollup_uploaded(session):
    render = await _render(session)
    await deliveries.record(session, render.id, "plex", "uploaded")
    assert await deliveries.rollup(session, render.id) == "uploaded"
    row = (await session.execute(select(RenderDelivery))).scalar_one()
    assert row.uploaded_at is not None and row.attempted_at is not None


async def test_rollup_precedence_failed_over_pending_over_uploaded(session):
    render = await _render(session)
    await deliveries.record(session, render.id, "plex", "uploaded")
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=60)
    assert await deliveries.rollup(session, render.id) == "pending"
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="connect: ConnectError")
    assert await deliveries.rollup(session, render.id) == "failed"
    await deliveries.record(session, render.id, "jellyfin", "uploaded")
    assert await deliveries.rollup(session, render.id) == "uploaded"


async def test_rollup_skipped_when_nothing_was_attempted(session):
    render = await _render(session)
    await deliveries.record(session, render.id, "plex", "skipped")
    assert await deliveries.rollup(session, render.id) == "skipped"
    render_row = (await session.execute(select(Render).where(Render.id == render.id))).scalar_one()
    assert render_row.upload_status == "skipped"


def test_failure_detail_never_carries_a_url():
    exc = httpx.ConnectError("https://jf.internal/Items")
    assert deliveries.failure_detail(exc) == "connect: ConnectError"
    resp = httpx.Response(401, request=httpx.Request("GET", "https://jf.internal/x"))
    assert deliveries.failure_detail(httpx.HTTPStatusError("x", request=resp.request, response=resp)) == "status: HTTPStatusError 401"
    assert deliveries.failure_detail(ValueError("https://jf.internal")) == "error: ValueError"


async def test_pending_rows_become_due_and_are_re_delivered(session, config_with_badges, monkeypatch):
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers
    render = await _render(session)
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items["process_item:movie:tmdb1"] = resolved("jellyfin", "j1", file_path="/m.mkv")

    async def fake_compose(session, config, render, item, http, mdblist):  # bytes, no ImageMagick
        return b"badged"

    # Task 19 extracts pipeline.compose_badged_bytes; it does not exist yet, so
    # this stub is added (raising=False) rather than replacing a real attribute.
    monkeypatch.setattr(pipeline, "compose_badged_bytes", fake_compose, raising=False)
    config_with_badges.badges.upload_to_jellyfin = True
    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc)
    )
    assert jf.uploads and jf.uploads[0][0].native_id == "j1"
    assert summary == "pending deliveries: 1 due, 1 uploaded, 0 still pending"
    assert await deliveries.rollup(session, render.id) == "uploaded"


async def test_server_removed_from_config_fails_the_delivery(session, config_with_badges):
    from autoposter.servers.registry import Servers
    render = await _render(session)
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    summary = await deliveries.retry_pending_deliveries(
        session, Servers({}), config_with_badges, now=datetime.now(timezone.utc)
    )
    assert summary == "pending deliveries: 1 due, 0 uploaded, 0 still pending"
    row = (await session.execute(select(RenderDelivery))).scalar_one()
    assert row.status == "failed" and row.detail == "config: server removed"
