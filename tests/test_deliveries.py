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
    # Column-only, not a full-entity select: retry_pending_deliveries' own
    # `due` query already loaded this row's RenderDelivery instance into the
    # session's identity map, and a plain `select(RenderDelivery)` here would
    # hand back that same (now stale) cached object rather than the row
    # `record`'s raw upsert just wrote -- the same hazard
    # `render/pipeline.py`'s `_upsert_media_item` documents at its own
    # `populate_existing=True` read.
    row = (await session.execute(select(RenderDelivery.status, RenderDelivery.detail))).one()
    assert row.status == "failed" and row.detail == "config: server removed"


async def test_transport_error_during_resolve_stays_pending(session, config_with_badges, monkeypatch):
    """A resolution miss (spec §6.2's transport-error row) means "not there
    yet" -- unlike a compose/upload failure below, it must stay `pending`."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers
    render = await _render(session)
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)

    async def raise_connect_error(intent):
        raise httpx.ConnectError("jellyfin is down")

    monkeypatch.setattr(jf, "resolve", raise_connect_error)
    config_with_badges.badges.upload_to_jellyfin = True
    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc)
    )
    assert summary == "pending deliveries: 1 due, 0 uploaded, 1 still pending"
    # Column-only select -- see the note in test_server_removed_from_config_
    # fails_the_delivery on why a full-entity select would read stale.
    row = (await session.execute(select(RenderDelivery.status, RenderDelivery.detail))).one()
    assert row.status == "pending" and row.detail == "connect: ConnectError"
    assert await deliveries.rollup(session, render.id) == "pending"


async def test_upload_exception_records_failed_not_pending(session, config_with_badges, monkeypatch):
    """The item DID resolve; the composite/upload itself failed. That mirrors
    apply_badges' own upload except clause (render/pipeline.py), which
    records `failed`, not another `pending` -- a resolution miss and an
    upload failure against a found item are different problems."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers
    render = await _render(session)
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items["process_item:movie:tmdb1"] = resolved("jellyfin", "j1", file_path="/m.mkv")
    jf.raise_on_upload = httpx.ConnectError("upload failed")

    async def fake_compose(session, config, render, item, http, mdblist):
        return b"badged"

    monkeypatch.setattr(pipeline, "compose_badged_bytes", fake_compose, raising=False)
    config_with_badges.badges.upload_to_jellyfin = True
    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc)
    )
    assert summary == "pending deliveries: 1 due, 0 uploaded, 0 still pending"
    # Column-only select -- see the note in test_server_removed_from_config_
    # fails_the_delivery on why a full-entity select would read stale.
    row = (await session.execute(select(RenderDelivery.status, RenderDelivery.detail))).one()
    assert row.status == "failed" and row.detail == "connect: ConnectError"
    assert await deliveries.rollup(session, render.id) == "failed"


async def test_library_override_gates_the_retry_per_row(session, monkeypatch):
    """apply_badges resolves config_for_library BEFORE reading badges.* (spec
    §5.1 step 4, render/pipeline.py:1735); the retry pass has to gate each due
    row the same way, or a library override that turns Jellyfin delivery off
    would be honoured on the first attempt and ignored on every retry."""
    from pathlib import Path

    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS

    from autoposter.config.loader import build_config, read_config_document
    from autoposter.servers.registry import Servers

    example_config = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
    document = read_config_document(example_config)
    document["libraries"] = {"Movies": {"badges": {"upload_to_jellyfin": False}}}
    config = build_config(document)
    config.badges.enabled = True
    config.badges.upload_to_jellyfin = True

    gated_row = await pipeline._upsert_media_item(
        session, resolved("plex", "g1", library="Movies", tmdb_id=1, file_path="/g.mkv")
    )
    gated_render = await pipeline._get_or_create_render(session, gated_row, "poster", "/a/g.jpg")
    open_row = await pipeline._upsert_media_item(
        session, resolved("plex", "o1", library="TV Shows", tmdb_id=2, file_path="/o.mkv")
    )
    open_render = await pipeline._get_or_create_render(session, open_row, "poster", "/a/o.jpg")
    await session.commit()

    await deliveries.record(session, gated_render.id, "jellyfin", "pending", retry_in=0)
    await deliveries.record(session, open_render.id, "jellyfin", "pending", retry_in=0)
    await session.commit()

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items["process_item:movie:tmdb1"] = resolved("jellyfin", "j1", library="Movies")
    jf.items["process_item:movie:tmdb2"] = resolved("jellyfin", "j2", library="TV Shows")

    async def fake_compose(session, config, render, item, http, mdblist):
        return b"badged"

    monkeypatch.setattr(pipeline, "compose_badged_bytes", fake_compose, raising=False)

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config, now=datetime.now(timezone.utc)
    )

    assert await deliveries.rollup(session, gated_render.id) == "skipped"
    assert await deliveries.rollup(session, open_render.id) == "uploaded"
    assert len(jf.uploads) == 1 and jf.uploads[0][0].native_id == "j2"
    assert summary == "pending deliveries: 2 due, 1 uploaded, 0 still pending"
