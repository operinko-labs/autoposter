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

    # `**kwargs` for what the retry pass passes and these stubs do not model:
    # `http`/`mdblist`, `force` (fix round 3 round 2, R1) and the identity
    # server's `server`/`ref` (round 3, N2).
    async def fake_compose(session, config, render, item, **kwargs):  # bytes, no ImageMagick
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

    async def fake_compose(session, config, render, item, **kwargs):
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

    async def fake_compose(session, config, render, item, **kwargs):
        return b"badged"

    monkeypatch.setattr(pipeline, "compose_badged_bytes", fake_compose, raising=False)

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config, now=datetime.now(timezone.utc)
    )

    assert await deliveries.rollup(session, gated_render.id) == "skipped"
    assert await deliveries.rollup(session, open_render.id) == "uploaded"
    assert len(jf.uploads) == 1 and jf.uploads[0][0].native_id == "j2"
    assert summary == "pending deliveries: 2 due, 1 uploaded, 0 still pending"


# Fix round 3 (Phase 5 branch review).


async def test_a_failed_delivery_keeps_the_renders_uploaded_at(session):
    """I6: `record`'s conflict `set_` carried `uploaded_at=None` for every
    non-`uploaded` status, and `rollup` then wrote that NULL onto the render.
    A single failed Plex upload erased the "last delivered" timestamp the
    item page's Uploaded column shows -- the column an operator reads to tell
    "never delivered" from "delivered, then broke"."""
    render = await _render(session)
    await deliveries.record(session, render.id, "plex", "uploaded")
    assert await deliveries.rollup(session, render.id) == "uploaded"
    delivered_at = (
        await session.execute(select(Render.uploaded_at).where(Render.id == render.id))
    ).scalar_one()
    assert delivered_at is not None

    await deliveries.record(session, render.id, "plex", "failed", detail="connect: ConnectError")
    assert await deliveries.rollup(session, render.id) == "failed"

    row = (
        await session.execute(
            select(RenderDelivery.uploaded_at).where(RenderDelivery.render_id == render.id)
        )
    ).scalar_one()
    assert row == delivered_at, "the row's own last success must survive a failure"
    after = (
        await session.execute(select(Render.uploaded_at).where(Render.id == render.id))
    ).scalar_one()
    assert after == delivered_at, "and so must the render's roll-up of it"


async def test_a_database_error_on_one_row_does_not_abort_the_pass(
    session, session_factory, config_with_badges, monkeypatch,
):
    """I2: "one row's failure never aborts the pass" held only for
    pure-Python exceptions. A statement error -- `record`'s upsert, a `rollup`
    UPDATE, a dropped connection -- leaves the transaction aborted, so every
    remaining due row raised `PendingRollbackError` at its first execute and
    the closing commit took the whole pass, and every prior row's work, with
    it. The next due row must still be attempted AND committed."""
    from sqlalchemy import text

    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    first = await _render(session)
    second_item = await pipeline._upsert_media_item(
        session, resolved("plex", "2", tmdb_id=2, file_path="/m2.mkv")
    )
    second = await pipeline._get_or_create_render(session, second_item, "poster", "/a/p2.jpg")
    await session.commit()
    # The due query orders by next_attempt_at, so the exploding row is put
    # squarely FIRST rather than left to a tie-break.
    await deliveries.record(session, first.id, "jellyfin", "pending", retry_in=-600)
    await deliveries.record(session, second.id, "jellyfin", "pending", retry_in=0)
    await session.commit()

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items["process_item:movie:tmdb1"] = resolved("jellyfin", "j1", file_path="/m.mkv")
    jf.items["process_item:movie:tmdb2"] = resolved("jellyfin", "j2", file_path="/m2.mkv")

    async def fake_compose(session, config, render, item, **kwargs):
        return b"badged"

    monkeypatch.setattr(pipeline, "compose_badged_bytes", fake_compose, raising=False)

    real_record = deliveries.record
    # Plain ints, read now: the pass rolls back mid-flight, which expires
    # every ORM object this session holds -- including these two -- and a
    # `first.id` inside the stub below would then be a lazy load outside an
    # await rather than a value.
    first_id, second_id = first.id, second.id

    async def exploding_record(db, render_id, server, status, **kwargs):
        if render_id == first_id:
            # A genuinely aborted transaction, not a bare Python raise: that
            # is the failure class the isolation did not survive.
            await db.execute(text("SELECT 1 / 0"))
        await real_record(db, render_id, server, status, **kwargs)

    monkeypatch.setattr(deliveries, "record", exploding_record)
    config_with_badges.badges.upload_to_jellyfin = True

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc)
    )

    assert summary == "pending deliveries: 2 due, 1 uploaded, 1 still pending"
    assert [u[0].native_id for u in jf.uploads] == ["j1", "j2"], "the second row was never attempted"
    # A separate session, because the point of the assertion is that the
    # second row's work was COMMITTED and not lost with the first row's.
    async with session_factory() as fresh:
        rows = dict(
            (await fresh.execute(
                select(RenderDelivery.render_id, RenderDelivery.status)
            )).all()
        )
    assert rows[second_id] == "uploaded"
    assert rows[first_id] == "pending", "the exploding row keeps its seeded state"


async def test_nothing_left_to_compose_records_skipped_not_a_failed_upload(
    session, config_with_badges, monkeypatch,
):
    """I3: a pending row outlives the state that created it. Badges turned
    off for the library, or a render that has since gone `failed`, makes
    `compose_badged_bytes` answer `None` -- which was passed straight into
    `upload_artwork`, producing a sticky `failed` row with a misleading
    detail. `skipped` is the honest outcome."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    render = await _render(session)
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items["process_item:movie:tmdb1"] = resolved("jellyfin", "j1", file_path="/m.mkv")

    async def compose_nothing(session, config, render, item, **kwargs):
        return None

    monkeypatch.setattr(pipeline, "compose_badged_bytes", compose_nothing, raising=False)
    config_with_badges.badges.upload_to_jellyfin = True

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc)
    )

    assert summary == "pending deliveries: 1 due, 0 uploaded, 0 still pending"
    assert jf.uploads == [], "there was nothing to upload"
    # Column-only select -- see the note in test_server_removed_from_config_
    # fails_the_delivery on why a full-entity select would read stale.
    row = (await session.execute(select(RenderDelivery.status, RenderDelivery.detail))).one()
    assert row.status == "skipped" and row.detail is None
    assert await deliveries.rollup(session, render.id) == "skipped"


async def test_a_migration_backfilled_row_is_never_due(session, config_with_badges):
    """I5's other half: the Phase-2 migration backfills a `plex` row per
    render with `next_attempt_at` NULL, and NULL never satisfies the due
    query. Load-bearing and, until now, untested -- every one of those rows
    is `pending` on a production database the moment the migration lands."""
    from sqlalchemy import insert

    from autoposter.servers.registry import Servers

    render = await _render(session)
    await session.execute(
        insert(RenderDelivery).values(render_id=render.id, server="plex", status="pending")
    )
    await session.commit()

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({}), config_with_badges, now=datetime.now(timezone.utc)
    )

    assert summary == "pending deliveries: 0 due, 0 uploaded, 0 still pending"
    row = (
        await session.execute(
            select(RenderDelivery.status, RenderDelivery.attempted_at)
        )
    ).one()
    assert row.status == "pending" and row.attempted_at is None


# Fix round 3, round 3 (re-review findings).


async def test_a_rolled_back_row_keeps_every_other_rows_work(
    session, session_factory, config_with_badges, monkeypatch,
):
    """N1, the re-reviewer's own probe shape: THREE due rows with the
    database error on the FIRST.

    The previous fix rolled the whole session back in the handler. Nothing
    commits until the end of the pass, so that threw away every earlier row's
    `record`/`rollup` too -- and it expired every object the `due` query
    returned, so the row after next died reading its own id and rolled the
    session back again. Measured: two uploads on the server, nothing
    committed, `1 uploaded` reported. A SAVEPOINT per row reaches only the
    row that failed."""
    from sqlalchemy import text

    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    renders = []
    for index in (1, 2, 3):
        row = await pipeline._upsert_media_item(
            session, resolved("plex", str(index), tmdb_id=index, file_path=f"/m{index}.mkv")
        )
        renders.append(await pipeline._get_or_create_render(session, row, "poster", f"/a/p{index}.jpg"))
    await session.commit()
    # Ordered by next_attempt_at, so the exploding row is squarely first.
    for offset, render in zip((-900, -600, -300), renders, strict=True):
        await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=offset)
    await session.commit()
    ids = [render.id for render in renders]

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    for index in (1, 2, 3):
        jf.items[f"process_item:movie:tmdb{index}"] = resolved(
            "jellyfin", f"j{index}", file_path=f"/m{index}.mkv"
        )

    async def fake_compose(session, config, render, item, **kwargs):
        return b"badged"

    monkeypatch.setattr(pipeline, "compose_badged_bytes", fake_compose, raising=False)

    real_record = deliveries.record

    async def exploding_record(db, render_id, server, status, **kwargs):
        if render_id == ids[0]:
            await db.execute(text("SELECT 1 / 0"))
        await real_record(db, render_id, server, status, **kwargs)

    monkeypatch.setattr(deliveries, "record", exploding_record)
    config_with_badges.badges.upload_to_jellyfin = True

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc)
    )

    assert summary == "pending deliveries: 3 due, 2 uploaded, 1 still pending"
    assert [u[0].native_id for u in jf.uploads] == ["j1", "j2", "j3"], (
        "every row must still be attempted"
    )
    # A separate session: the point is that rows 2 and 3 were COMMITTED, not
    # merely written and then erased by a neighbour's rollback.
    async with session_factory() as fresh:
        committed = dict(
            (await fresh.execute(
                select(RenderDelivery.render_id, RenderDelivery.status)
            )).all()
        )
    assert committed == {ids[0]: "pending", ids[1]: "uploaded", ids[2]: "uploaded"}
