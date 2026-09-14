from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from autoposter import deliveries
from autoposter.db.models import ItemFacts, MediaItem, MetadataWrite, Render, RenderDelivery
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
    # `http`/`mdblist`, `force` and the identity
    # server's `server`/`ref`.
    async def fake_compose(session, config, render, item, **kwargs):  # bytes, no ImageMagick
        return b"badged"

    # pipeline.compose_badged_bytes does not exist yet, so
    # this stub is added (raising=False) rather than replacing a real attribute.
    monkeypatch.setattr(pipeline, "compose_badged_bytes", fake_compose, raising=False)
    config_with_badges.badges.upload_to_jellyfin = True
    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc)
    )
    assert jf.uploads and jf.uploads[0][0].native_id == "j1"
    assert summary == (
        "pending deliveries: 1 due, 1 done, 0 still pending, 0 failed, 0 skipped; "
        "jellyfin: 1 due, 1 uploaded, 0 written, 0 pending, 0 failed, 0 skipped"
    )
    assert await deliveries.rollup(session, render.id) == "uploaded"


async def test_server_removed_from_config_fails_the_delivery(session, config_with_badges):
    from autoposter.servers.registry import Servers
    render = await _render(session)
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    summary = await deliveries.retry_pending_deliveries(
        session, Servers({}), config_with_badges, now=datetime.now(timezone.utc)
    )
    assert summary == (
        "pending deliveries: 1 due, 0 done, 0 still pending, 1 failed, 0 skipped; "
        "jellyfin: 1 due, 0 uploaded, 0 written, 0 pending, 1 failed, 0 skipped"
    )
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
    assert summary == (
        "pending deliveries: 1 due, 0 done, 1 still pending, 0 failed, 0 skipped; "
        "jellyfin: 1 due, 0 uploaded, 0 written, 1 pending, 0 failed, 0 skipped"
    )
    # Column-only select -- see the note in test_server_removed_from_config_
    # fails_the_delivery on why a full-entity select would read stale.
    row = (await session.execute(select(RenderDelivery.status, RenderDelivery.detail))).one()
    assert row.status == "pending" and row.detail == "connect: ConnectError"
    assert await deliveries.rollup(session, render.id) == "pending"


async def test_a_transport_error_at_resolve_spends_budget_on_both_tables(
    session, config_with_badges
):
    """Review I2: the same event -- the server is unreachable at resolve time
    -- spent an attempt on `render_deliveries` and none on `metadata_writes`,
    so spec §2's "nothing retries forever" did not hold for metadata in the
    one production failure mode it was written for. Only `ItemNotFound`, a
    real resolution miss, is a wait."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    item = await _item_with_facts(session, native="j-i2")
    render = await pipeline._get_or_create_render(session, item, "poster", "/a/p.jpg")
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await deliveries.record_metadata(session, item.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(
        name="jellyfin", capabilities=JELLYFIN_CAPS,
        raise_on_resolve=httpx.ConnectError("jellyfin is down"),
    )
    config_with_badges.badges.upload_to_jellyfin = True
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True

    await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges,
        now=datetime.now(timezone.utc) + timedelta(hours=12),
    )

    artwork = (await session.execute(
        select(RenderDelivery.status, RenderDelivery.attempts, RenderDelivery.detail)
    )).one()
    metadata = (await session.execute(
        select(MetadataWrite.status, MetadataWrite.attempts, MetadataWrite.detail)
    )).one()
    # Both seeded rows were themselves counted `pending`s, so the second
    # attempt is this pass's -- on BOTH tables.
    assert artwork.status == "pending" and artwork.attempts == 2
    assert metadata.status == "pending" and metadata.attempts == 2
    assert artwork.detail == metadata.detail == "connect: ConnectError"


async def test_an_unreachable_server_exhausts_a_metadata_rows_budget(
    session, config_with_badges
):
    """The other half of I2: counting is only worth anything if the row can
    then reach `failed`, which is what makes an unreachable server visible in
    the run history rather than an endless `N pending, 0 failed`."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    config_with_badges.scheduler.delivery_attempts = 2
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True
    item = await _item_with_facts(session, native="j-i2b")
    await deliveries.record_metadata(
        session, item.id, "jellyfin", "pending", retry_in=0, count_attempt=False,
    )
    await session.commit()
    jf = FakeMediaServer(
        name="jellyfin", capabilities=JELLYFIN_CAPS,
        raise_on_resolve=httpx.ConnectError("jellyfin is down"),
    )
    servers = Servers({"jellyfin": jf})

    await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges,
        now=datetime.now(timezone.utc) + timedelta(hours=12),
    )
    second = await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges,
        now=datetime.now(timezone.utc) + timedelta(hours=24),
    )

    row = (await session.execute(
        select(MetadataWrite.status, MetadataWrite.detail, MetadataWrite.next_attempt_at)
    )).one()
    assert row.status == "failed" and row.detail == "connect: ConnectError"
    assert row.next_attempt_at is None
    assert "jellyfin: 1 due, 0 uploaded, 0 written, 0 pending, 1 failed, 0 skipped" in second


async def test_identity_resolution_wait_leaves_the_budget_alone(session, config_with_badges):
    """The identity server (Plex) failing to resolve is a wait for the
    DELIVERY server's row too -- the branch's own comment says it is "exactly
    like the delivery server's own miss above" (the `ItemNotFound` branch,
    which IS `count_attempt=False`) -- so it must not spend this row's
    retry budget either."""
    from autoposter.db.models import MediaItemServerRef
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    render = await _render(session)
    session.add(MediaItemServerRef(item_id=render.item_id, server="plex", native_id="p1", library="Movies"))
    seeded_attempts = await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await session.commit()

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items["process_item:movie:tmdb1"] = resolved("jellyfin", "j1", file_path="/m.mkv")
    plex = FakeMediaServer(name="plex", raise_on_resolve=httpx.ConnectError("plex is down"))

    config_with_badges.badges.upload_to_jellyfin = True
    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf, "plex": plex}), config_with_badges, now=datetime.now(timezone.utc)
    )

    assert summary == (
        "pending deliveries: 1 due, 0 done, 1 still pending, 0 failed, 0 skipped; "
        "jellyfin: 1 due, 0 uploaded, 0 written, 1 pending, 0 failed, 0 skipped"
    )
    row = (await session.execute(select(RenderDelivery.status, RenderDelivery.attempts))).one()
    assert row.status == "pending"
    assert row.attempts == seeded_attempts


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
    assert summary == (
        "pending deliveries: 1 due, 0 done, 0 still pending, 1 failed, 0 skipped; "
        "jellyfin: 1 due, 0 uploaded, 0 written, 0 pending, 1 failed, 0 skipped"
    )
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
    assert summary == (
        "pending deliveries: 2 due, 1 done, 0 still pending, 0 failed, 1 skipped; "
        "jellyfin: 2 due, 1 uploaded, 0 written, 0 pending, 0 failed, 1 skipped"
    )


async def test_a_failed_delivery_keeps_the_renders_uploaded_at(session):
    """`record`'s conflict `set_` carried `uploaded_at=None` for every
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
    """The rule that one row's failure never aborts the pass held only for
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

    assert summary == (
        "pending deliveries: 2 due, 1 done, 1 still pending, 0 failed, 0 skipped; "
        "jellyfin: 2 due, 1 uploaded, 0 written, 1 pending, 0 failed, 0 skipped"
    )
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
    """A pending row outlives the state that created it. Badges turned
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

    assert summary == (
        "pending deliveries: 1 due, 0 done, 0 still pending, 0 failed, 1 skipped; "
        "jellyfin: 1 due, 0 uploaded, 0 written, 0 pending, 0 failed, 1 skipped"
    )
    assert jf.uploads == [], "there was nothing to upload"
    # Column-only select -- see the note in test_server_removed_from_config_
    # fails_the_delivery on why a full-entity select would read stale.
    row = (await session.execute(select(RenderDelivery.status, RenderDelivery.detail))).one()
    assert row.status == "skipped" and row.detail is None
    assert await deliveries.rollup(session, render.id) == "skipped"


async def test_a_migration_backfilled_row_is_never_due(session, config_with_badges):
    """The Phase-2 migration backfills a `plex` row per
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

    assert summary == "pending deliveries: 0 due, 0 done, 0 still pending, 0 failed, 0 skipped"
    row = (
        await session.execute(
            select(RenderDelivery.status, RenderDelivery.attempted_at)
        )
    ).one()
    assert row.status == "pending" and row.attempted_at is None


async def test_a_rolled_back_row_keeps_every_other_rows_work(
    session, session_factory, config_with_badges, monkeypatch,
):
    """This scenario probes three due rows with the
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

    assert summary == (
        "pending deliveries: 3 due, 2 done, 1 still pending, 0 failed, 0 skipped; "
        "jellyfin: 3 due, 2 uploaded, 0 written, 1 pending, 0 failed, 0 skipped"
    )
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


async def test_a_path_mismatch_on_the_identity_server_fails_rather_than_waiting_forever(
    session, config_with_badges,
):
    """`PathMismatch` subclasses `ItemNotFound`, so the identity
    re-resolve's broad handler recorded it as a `pending` on the 6h horizon
    -- retried forever against a mount mismatch no retry can fix (spec §6.2).
    The delivery server's own resolve has always recorded `failed` for it;
    the identity's now does too."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    render = await _render(session)
    # Ten minutes in the past rather than `retry_in=0`: this machine's
    # container clock steps backwards a few seconds at a time.
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=-600)
    await session.commit()

    plex = FakeMediaServer(name="plex")
    plex.path_mismatch.add("process_item:movie:tmdb1")
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items["process_item:movie:tmdb1"] = resolved("jellyfin", "j1", file_path="/m.mkv")
    config_with_badges.badges.upload_to_jellyfin = True

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"plex": plex, "jellyfin": jf}), config_with_badges,
        now=datetime.now(timezone.utc),
    )

    assert summary == (
        "pending deliveries: 1 due, 0 done, 0 still pending, 1 failed, 0 skipped; "
        "jellyfin: 1 due, 0 uploaded, 0 written, 0 pending, 1 failed, 0 skipped"
    )
    assert jf.uploads == [], "nothing may be delivered when the identity cannot be sampled"
    # Column-only select -- see the note in test_server_removed_from_config_
    # fails_the_delivery on why a full-entity select would read stale.
    row = (await session.execute(select(RenderDelivery.status, RenderDelivery.detail))).one()
    assert row.status == "failed" and row.detail == "error: PathMismatch"


async def test_metadata_write_is_unique_per_item_and_server(session):
    from conftest import seed_media_item
    item = await seed_media_item(session, "rk-mw", title="A")
    session.add(MetadataWrite(item_id=item.id, server="jellyfin", status="pending"))
    await session.commit()
    session.add(MetadataWrite(item_id=item.id, server="jellyfin", status="pending"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_render_delivery_carries_attempts_and_the_delivered_fingerprint(session):
    render = await _render(session)
    session.add(RenderDelivery(render_id=render.id, server="plex", status="uploaded", fingerprint="abc"))
    await session.commit()
    row = (await session.execute(select(RenderDelivery))).scalar_one()
    assert row.attempts == 0 and row.fingerprint == "abc"


async def test_attempts_climb_on_pending_and_reset_on_success(session):
    render = await _render(session)
    assert await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=60) == 1
    assert await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=60) == 2
    assert await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X") == 3
    assert await deliveries.record(session, render.id, "jellyfin", "uploaded") == 0


async def test_an_uncounted_pending_leaves_the_budget_alone(session):
    """A resolution miss is a wait, not an attempt at the write (spec §2)."""
    render = await _render(session)
    assert await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=60) == 1
    assert await deliveries.record(
        session, render.id, "jellyfin", "pending", retry_in=60, count_attempt=False
    ) == 1


async def test_an_outcome_inside_a_run_keeps_its_scope(session):
    """The other half of review I4: a row keeps `run_id`/`previous_status`
    across every retry inside the run that armed it, terminal outcome
    included -- Phase C's progress counts by run AND status, and its cancel
    restores `previous_status`. Only `leave_run` clears them."""
    from conftest import seed_media_item
    from autoposter.scheduler.run_history import open_run

    item = await seed_media_item(session, "rk-run", title="A")
    run_id = await open_run(session, kind="catch_up", name="catch_up:jellyfin")
    await deliveries.record_metadata(session, item.id, "jellyfin", "pending", retry_in=0)
    await session.execute(
        update(MetadataWrite).values(run_id=run_id, previous_status="written")
    )

    await deliveries.record_metadata(session, item.id, "jellyfin", "written")
    row = (await session.execute(
        select(MetadataWrite.status, MetadataWrite.run_id, MetadataWrite.previous_status)
    )).one()
    assert row.status == "written"
    assert row.run_id == run_id and row.previous_status == "written"

    await deliveries.record_metadata(
        session, item.id, "jellyfin", "pending", retry_in=0, leave_run=True,
    )
    row = (await session.execute(
        select(MetadataWrite.run_id, MetadataWrite.previous_status)
    )).one()
    assert row.run_id is None and row.previous_status is None


async def test_a_re_arm_that_is_also_an_attempt_lands_at_one(session):
    """Review M9: `reset_attempts` used to be silently ignored whenever
    `count_attempt` was true, so a caller whose event is both a fresh start
    and a real attempt had no way to say so. Reset, then count -- 1."""
    render = await _render(session)
    assert await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=60) == 1
    assert await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=60) == 2
    assert await deliveries.record(
        session, render.id, "jellyfin", "failed", detail="error: X",
    ) == 3
    assert await deliveries.record(
        session, render.id, "jellyfin", "pending", retry_in=60, reset_attempts=True,
    ) == 1
    # And without the counting half it is still a plain reset to zero.
    assert await deliveries.record(
        session, render.id, "jellyfin", "pending", retry_in=60,
        count_attempt=False, reset_attempts=True,
    ) == 0


async def test_the_delivered_fingerprint_is_kept_and_never_erased(session):
    render = await _render(session)
    await deliveries.record(session, render.id, "plex", "uploaded", fingerprint="fp1")
    await deliveries.record(session, render.id, "plex", "pending", retry_in=60)
    row = (await session.execute(
        select(RenderDelivery.fingerprint, RenderDelivery.status)
    )).one()
    assert row.fingerprint == "fp1" and row.status == "pending"


async def test_record_metadata_writes_one_row_per_item_and_server(session):
    from conftest import seed_media_item
    item = await seed_media_item(session, "rk-rm", title="A")
    assert await deliveries.record_metadata(session, item.id, "jellyfin", "written") == 0
    assert await deliveries.record_metadata(
        session, item.id, "jellyfin", "pending", detail="connect: ConnectError", retry_in=60
    ) == 1
    row = (await session.execute(select(
        MetadataWrite.status, MetadataWrite.detail, MetadataWrite.attempts,
        MetadataWrite.written_at, MetadataWrite.next_attempt_at,
    ))).one()
    assert row.status == "pending" and row.detail == "connect: ConnectError"
    assert row.attempts == 1
    # The written_at half of the `uploaded_at` rule: a later failure must not
    # erase the fact that this server DID hold the metadata once.
    assert row.written_at is not None and row.next_attempt_at is not None


async def test_record_metadata_skipped_keeps_its_reason(session):
    from conftest import seed_media_item
    item = await seed_media_item(session, "rk-sk", title="A")
    await deliveries.record_metadata(
        session, item.id, "jellyfin", "skipped",
        detail="config: operations.write_to_jellyfin is off",
    )
    row = (await session.execute(select(MetadataWrite.status, MetadataWrite.detail))).one()
    assert row.status == "skipped"
    assert row.detail == "config: operations.write_to_jellyfin is off"


async def test_an_absent_row_is_never_written_over_by_either_writer(session):
    """Review C1/I4: the `absent` rule is a clause on the upsert, not a
    convention each caller remembers. Both writers, both tables."""
    from conftest import seed_media_item
    item = await seed_media_item(session, "rk-abs", title="A")
    render = await pipeline._get_or_create_render(session, item, "poster", "/a/p.jpg")
    await session.commit()
    await deliveries.record(session, render.id, "jellyfin", "absent", detail="library: x")
    await deliveries.record_metadata(session, item.id, "jellyfin", "absent", detail="library: x")

    # Zero, not a climbing counter: nothing was attempted against a server
    # that does not carry the library.
    assert await deliveries.record(
        session, render.id, "jellyfin", "pending", retry_in=60
    ) == 0
    assert await deliveries.record(session, render.id, "jellyfin", "uploaded", fingerprint="fp") == 0
    assert await deliveries.record_metadata(session, item.id, "jellyfin", "written") == 0

    delivery = (await session.execute(select(RenderDelivery))).scalar_one()
    assert delivery.status == "absent" and delivery.next_attempt_at is None
    assert delivery.fingerprint is None and delivery.uploaded_at is None
    metadata = (await session.execute(select(MetadataWrite))).scalar_one()
    assert metadata.status == "absent" and metadata.written_at is None


async def test_an_unknown_status_is_refused_rather_than_stored(session):
    """Review minor 4: the vocabulary is `deliveries.STATUSES` plus each
    table's own terminal word, and `status` is a plain String(24) -- so a
    typo would otherwise store and read as neither."""
    from conftest import seed_media_item
    item = await seed_media_item(session, "rk-vocab", title="A")
    render = await pipeline._get_or_create_render(session, item, "poster", "/a/p.jpg")
    await session.commit()

    assert deliveries.STATUSES == ("pending", "failed", "skipped", "absent")
    with pytest.raises(ValueError):
        await deliveries.record_metadata(session, item.id, "jellyfin", "write")
    with pytest.raises(ValueError):
        # Each table admits only its OWN terminal word.
        await deliveries.record_metadata(session, item.id, "jellyfin", "uploaded")
    with pytest.raises(ValueError):
        await deliveries.record(session, render.id, "jellyfin", "written")


async def test_a_terminal_call_without_a_fingerprint_keeps_the_stored_one(session):
    """Review minor 1: `record(..., "uploaded")` with no `fingerprint=` is a
    caller that does not know which bytes the server holds, not one asserting
    it holds none."""
    render = await _render(session)
    await deliveries.record(session, render.id, "plex", "uploaded", fingerprint="fp1")
    await deliveries.record(session, render.id, "plex", "uploaded")
    row = (await session.execute(select(RenderDelivery))).scalar_one()
    assert row.fingerprint == "fp1"


async def test_the_retry_records_the_fingerprint_it_actually_delivered(
    session, config_with_badges, monkeypatch
):
    """Review I1/T3: `compose_badged_bytes(force=True)` deliberately does not
    write `render.badge_fingerprint`, so the render's stored fingerprint is by
    construction not the one these bytes were composed under -- the two differ
    routinely, most sharply when the item has no Plex ref and the forced
    compose drops the resolution/format overlays. Recording the stored one
    would make a server holding visibly different artwork read as up to date
    to Phase C's catch-up, which is the failure mode it exists to find."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers
    render = await _render(session)
    render.badge_fingerprint = "fp-from-the-last-full-pass"
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items["process_item:movie:tmdb1"] = resolved("jellyfin", "j1", file_path="/m.mkv")

    async def fake_compose(session, config, render, item, *, out=None, **kwargs):
        # What the real one does on the forced path: hand back the fingerprint
        # these bytes were composed under and leave the column alone.
        assert kwargs["force"] is True
        if out is not None:
            out["fingerprint"] = "fp-composed-just-now"
        return b"badged"

    monkeypatch.setattr(pipeline, "compose_badged_bytes", fake_compose, raising=False)
    config_with_badges.badges.upload_to_jellyfin = True

    await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc)
    )

    row = (await session.execute(
        select(RenderDelivery.status, RenderDelivery.fingerprint)
    )).one()
    assert row.status == "uploaded"
    assert row.fingerprint == "fp-composed-just-now"
    assert row.fingerprint != "fp-from-the-last-full-pass"


async def _item_with_facts(session, native="j9"):
    """The item AND the ``item_facts`` row the name promises (review M8).

    Seeding none is why C1 was invisible to every metadata test in this file:
    the retry took the `or GatheredFacts()` fallback and never constructed the
    object the production path always has. ``persist_facts`` writes this row
    in the same ``apply_metadata`` call that records the ``pending``, so every
    due metadata row has one -- the hit rate is 100%.
    """
    from conftest import seed_media_item
    item = await seed_media_item(session, native, server="jellyfin", title="A", library="Movies")
    session.add(ItemFacts(
        item_id=item.id, critic_rating=8.5, genres=["Drama"], tmdb_origin_country=[],
        sources={"critic_rating": "tmdb"},
    ))
    await session.flush()
    return item


async def test_a_due_metadata_row_is_written_and_recorded(session, config_with_badges):
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    item = await _item_with_facts(session)
    await deliveries.record_metadata(session, item.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.resolve_any = resolved("jellyfin", "j9", file_path="/m.mkv")
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc),
    )

    assert jf.facts_written and jf.facts_written[0][0].native_id == "j9"
    row = (await session.execute(select(MetadataWrite.status, MetadataWrite.attempts))).one()
    assert row.status == "written" and row.attempts == 0
    assert summary.startswith("pending deliveries: 1 due, 1 done, 0 still pending")
    assert "jellyfin: 1 due, 0 uploaded, 1 written, 0 pending, 0 failed, 0 skipped" in summary


async def test_the_stored_facts_row_reaches_the_real_writer_and_a_write_lands(
    session, config_with_badges
):
    """Review C1: the retry handed `plan_edits` the raw `ItemFacts` ORM row,
    which has none of the four `GatheredFacts` fields with no column
    (`user_rating`, `original_title`, `added_at`, `sort_title`). `plan_edits`
    dereferences `facts.user_rating` for every kind of item -- it is in all
    four `WRITABLE_BY_KIND` sets -- so every due metadata row raised
    `AttributeError`, wrote nothing and stayed due forever.

    Driven through the REAL writer (`plex.writer.apply_facts`/`plan_edits`,
    which `jellyfin/writer.py` imports unmodified) over a fake plexapi item,
    not the recording double: the double records its arguments without
    touching a field, which is the other half of why this was invisible.
    """
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from test_plex_writer import FakeItem
    from autoposter.plex import writer as plex_writer
    from autoposter.servers.registry import Servers

    item = await _item_with_facts(session, native="j-c1")
    await deliveries.record_metadata(session, item.id, "jellyfin", "pending", retry_in=0)
    await session.commit()

    # The transport, faked; the writer, real.
    plex_item = FakeItem(kind="movie")
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.resolve_any = resolved("jellyfin", "j-c1", file_path="/m.mkv")

    async def real_apply_facts(ref, facts, operations=None, parental_categories=None, overrides=None):
        return await plex_writer.apply_facts(
            plex_item, facts, operations, parental_categories, overrides
        )

    jf.apply_facts = real_apply_facts
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc),
    )

    # A write LANDED -- the seeded critic rating, planned against what the
    # item holds and sent in one batched edit.
    assert plex_item.saved is True
    assert plex_item.edits["rating.value"] == 8.5
    row = (await session.execute(select(MetadataWrite.status, MetadataWrite.attempts))).one()
    assert row.status == "written" and row.attempts == 0
    assert "jellyfin: 1 due, 0 uploaded, 1 written, 0 pending, 0 failed, 0 skipped" in summary


def test_the_unpersisted_facts_fields_come_back_at_their_defaults():
    """The other half of C1: a `GatheredFacts` field with no `item_facts`
    column must reach the writer at its default rather than as a missing
    attribute -- which is what the next such field would otherwise be."""
    from autoposter.facts.models import GatheredFacts

    row = ItemFacts(item_id=1, critic_rating=8.5, genres=["Drama"], sources={})
    facts = deliveries.facts_from_row(row)

    assert isinstance(facts, GatheredFacts)
    assert facts.critic_rating == 8.5 and facts.genres == ["Drama"]
    for unpersisted in ("user_rating", "original_title", "added_at", "sort_title"):
        assert not hasattr(ItemFacts, unpersisted), f"{unpersisted} gained a column"
        assert getattr(facts, unpersisted) is None
    assert deliveries.facts_from_row(None) == GatheredFacts()


async def test_a_metadata_resolution_miss_stays_pending_without_spending_budget(
    session, config_with_badges
):
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    item = await _item_with_facts(session, native="j10")
    seeded_attempts = await deliveries.record_metadata(
        session, item.id, "jellyfin", "pending", retry_in=0
    )
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)  # resolves nothing
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True

    await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc),
    )

    row = (await session.execute(select(MetadataWrite.status, MetadataWrite.attempts))).one()
    # The seeded value, unmoved: a server that has not scanned the item yet is
    # a wait, not an attempt at the write (the same reading
    # `test_an_uncounted_pending_leaves_the_budget_alone` pins for artwork).
    assert row.status == "pending" and row.attempts == seeded_attempts


async def test_the_server_filter_leaves_every_other_servers_rows_alone(
    session, config_with_badges
):
    from autoposter.servers.registry import Servers

    render = await _render(session)
    await deliveries.record(session, render.id, "plex", "pending", retry_in=0)
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    # Both tables, because a catch-up marks both (review M1).
    await deliveries.record_metadata(session, render.item_id, "plex", "pending", retry_in=0)
    await deliveries.record_metadata(session, render.item_id, "jellyfin", "pending", retry_in=0)
    await session.commit()

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({}), config_with_badges, now=datetime.now(timezone.utc),
        server="jellyfin",
    )

    assert summary.startswith("pending deliveries: 2 due")
    rows = dict((await session.execute(
        select(RenderDelivery.server, RenderDelivery.status)
    )).all())
    assert rows == {"plex": "pending", "jellyfin": "failed"}
    writes = dict((await session.execute(
        select(MetadataWrite.server, MetadataWrite.status)
    )).all())
    assert writes == {"plex": "pending", "jellyfin": "failed"}


async def test_the_run_id_filter_takes_only_that_runs_rows(session, config_with_badges):
    from autoposter.scheduler.run_history import open_run
    from autoposter.servers.registry import Servers

    render = await _render(session)
    # Its own identity, not `_render(native="2")`: `resolved()` defaults
    # `tmdb_id=1`, so a second render under the same tmdb id is the same
    # `media_items` row and the same render -- the shape every other
    # two-render test in this file builds explicitly.
    other_item = await pipeline._upsert_media_item(
        session, resolved("plex", "2", tmdb_id=2, file_path="/m2.mkv")
    )
    other = await pipeline._get_or_create_render(session, other_item, "poster", "/a/p2.jpg")
    run_id = await open_run(session, kind="catch_up", name="catch_up:jellyfin")
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await deliveries.record(session, other.id, "jellyfin", "pending", retry_in=0)
    # Both tables, because a catch-up marks both (review M1).
    await deliveries.record_metadata(session, render.item_id, "jellyfin", "pending", retry_in=0)
    await deliveries.record_metadata(session, other_item.id, "jellyfin", "pending", retry_in=0)
    await session.execute(
        update(RenderDelivery)
        .where(RenderDelivery.render_id == render.id)
        .values(run_id=run_id, previous_status="uploaded")
    )
    await session.execute(
        update(MetadataWrite)
        .where(MetadataWrite.item_id == render.item_id)
        .values(run_id=run_id, previous_status="written")
    )
    await session.commit()

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({}), config_with_badges, now=datetime.now(timezone.utc),
        run_id=run_id,
    )

    assert summary.startswith("pending deliveries: 2 due")
    statuses = dict((await session.execute(
        select(RenderDelivery.render_id, RenderDelivery.status)
    )).all())
    assert statuses == {render.id: "failed", other.id: "pending"}
    writes = dict((await session.execute(
        select(MetadataWrite.item_id, MetadataWrite.status)
    )).all())
    assert writes == {render.item_id: "failed", other_item.id: "pending"}


async def test_the_scheduled_pass_never_drains_a_catch_ups_rows(session, config_with_badges):
    """Review I5: the scheduled pass passed neither filter and therefore took
    every due row. A catch-up stamps its backlog `next_attempt_at = now` while
    ordinary rows sit six hours out, so `ORDER BY next_attempt_at LIMIT 500`
    handed the catch-up's thousands of rows the whole budget of every
    scheduled pass until they drained -- while ordinary due rows waited, and
    with the catch-up's own run-scoped progress advanced from outside it."""
    from autoposter.scheduler.run_history import open_run
    from autoposter.servers.registry import Servers

    ordinary = await _item_with_facts(session, native="j-i5-plain")
    scoped = await _item_with_facts(session, native="j-i5-run")
    run_id = await open_run(session, kind="catch_up", name="catch_up:jellyfin")
    await deliveries.record_metadata(session, ordinary.id, "jellyfin", "pending", retry_in=0)
    await deliveries.record_metadata(session, scoped.id, "jellyfin", "pending", retry_in=0)
    await session.execute(
        update(MetadataWrite)
        .where(MetadataWrite.item_id == scoped.id)
        .values(run_id=run_id, previous_status="written")
    )
    await session.commit()

    # `Servers({})`, so every row this pass DOES take goes `failed` and says
    # which one it was.
    unscoped_summary = await deliveries.retry_pending_deliveries(
        session, Servers({}), config_with_badges, now=datetime.now(timezone.utc),
    )

    assert unscoped_summary.startswith("pending deliveries: 1 due")
    statuses = dict((await session.execute(
        select(MetadataWrite.item_id, MetadataWrite.status)
    )).all())
    assert statuses == {ordinary.id: "failed", scoped.id: "pending"}

    # And the catch-up's own pass takes only its own row.
    scoped_summary = await deliveries.retry_pending_deliveries(
        session, Servers({}), config_with_badges, now=datetime.now(timezone.utc),
        run_id=run_id,
    )

    assert scoped_summary.startswith("pending deliveries: 1 due")
    statuses = dict((await session.execute(
        select(MetadataWrite.item_id, MetadataWrite.status)
    )).all())
    assert statuses == {ordinary.id: "failed", scoped.id: "failed"}


async def test_metadata_operations_turned_off_records_a_skip_and_writes_nothing(
    session, config_with_badges
):
    """Review I2: `apply_metadata` returns before it writes a row at all when
    `operations.enabled` is off, so this pass was the one path that could
    still write to a server the operator had switched off entirely."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    item = await _item_with_facts(session, native="j12")
    await deliveries.record_metadata(session, item.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.resolve_any = resolved("jellyfin", "j12", file_path="/m.mkv")
    config_with_badges.operations.enabled = False
    config_with_badges.operations.write_to_jellyfin = True

    await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc),
    )

    assert jf.facts_written == [], "operations are off; nothing may be written anywhere"
    row = (await session.execute(select(MetadataWrite.status, MetadataWrite.detail))).one()
    assert row.status == "skipped" and row.detail == "config: operations.enabled is off"


async def test_the_budget_turns_a_persistently_failing_delivery_failed(
    session, config_with_badges
):
    """spec §2: nothing retries forever. The row is retried at the pass's
    cadence up to `scheduler.delivery_attempts` and is then `failed`, with
    the failure's class name, until a full pass or a catch-up re-arms it."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    config_with_badges.scheduler.delivery_attempts = 2
    config_with_badges.badges.upload_to_jellyfin = True
    render = await _render(session)
    # Uncounted: the seeding must not be one of the two attempts under test.
    await deliveries.record(
        session, render.id, "jellyfin", "pending", retry_in=0, count_attempt=False,
    )
    await session.commit()
    jf = FakeMediaServer(
        name="jellyfin", capabilities=JELLYFIN_CAPS,
        raise_on_resolve=httpx.ConnectError("jellyfin is down"),
    )
    servers = Servers({"jellyfin": jf})

    # Each pass reads a `now` beyond the horizon the previous one stamped,
    # rather than sleeping: this machine's container clock steps backwards.
    first = await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges,
        now=datetime.now(timezone.utc) + timedelta(hours=12),
    )
    row = (await session.execute(
        select(RenderDelivery.status, RenderDelivery.attempts, RenderDelivery.detail)
    )).one()
    assert row.status == "pending" and row.attempts == 1
    assert "jellyfin: 1 due, 0 uploaded, 0 written, 1 pending, 0 failed, 0 skipped" in first

    second = await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges,
        now=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    row = (await session.execute(
        select(RenderDelivery.status, RenderDelivery.detail, RenderDelivery.next_attempt_at)
    )).one()
    assert row.status == "failed" and row.detail == "connect: ConnectError"
    assert row.next_attempt_at is None, "a failed row is not due again on its own"
    assert "jellyfin: 1 due, 0 uploaded, 0 written, 0 pending, 1 failed, 0 skipped" in second


async def test_the_budget_turns_a_persistently_failing_metadata_write_failed(
    session, config_with_badges
):
    """The metadata half of the same rule (spec §2): a write error is
    `pending` again until the budget runs out and then `failed`, with its
    class name."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    config_with_badges.scheduler.delivery_attempts = 2
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True
    item = await _item_with_facts(session, native="j13")
    await deliveries.record_metadata(
        session, item.id, "jellyfin", "pending", retry_in=0, count_attempt=False,
    )
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.resolve_any = resolved("jellyfin", "j13", file_path="/m.mkv")

    async def boom(ref, facts, operations=None, parental_categories=None, overrides=None):
        raise httpx.ConnectError("jellyfin is down")

    jf.apply_facts = boom
    servers = Servers({"jellyfin": jf})

    first = await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges,
        now=datetime.now(timezone.utc) + timedelta(hours=12),
    )
    row = (await session.execute(
        select(MetadataWrite.status, MetadataWrite.attempts, MetadataWrite.detail)
    )).one()
    assert row.status == "pending" and row.attempts == 1
    assert row.detail == "connect: ConnectError"
    assert "jellyfin: 1 due, 0 uploaded, 0 written, 1 pending, 0 failed, 0 skipped" in first

    second = await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges,
        now=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    row = (await session.execute(
        select(MetadataWrite.status, MetadataWrite.detail, MetadataWrite.next_attempt_at)
    )).one()
    assert row.status == "failed" and row.detail == "connect: ConnectError"
    assert row.next_attempt_at is None
    assert "jellyfin: 1 due, 0 uploaded, 0 written, 0 pending, 1 failed, 0 skipped" in second


async def test_a_re_armed_row_gets_its_whole_budget_again(session, config_with_badges):
    """Review I1: `failed` is itself a counted attempt, so an exhausted row
    stayed permanently above the budget -- `deliver`'s re-arm left the
    counter where it was, and the next failure exhausted the row again on its
    FIRST attempt. Spec §2 promises a row re-armed by a full pass (or, in
    Phase C, by a catch-up) the whole budget, not one retry."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    config_with_badges.scheduler.delivery_attempts = 2
    config_with_badges.badges.upload_to_jellyfin = True
    render = await _render(session)
    render.status = "rendered"
    media_item = (await session.execute(
        select(MediaItem).where(MediaItem.id == render.item_id)
    )).scalar_one()
    # The row runs its budget out: two counted attempts and the `failed` the
    # pass stamps on top of the second.
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=0)
    await deliveries.record(
        session, render.id, "jellyfin", "failed", detail="connect: ConnectError",
    )
    await session.commit()

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf_item = resolved("jellyfin", "j1", file_path="/m.mkv")
    # What a full pass does for a render whose fingerprint has not moved:
    # `data is None`, so `deliver` re-arms the failed row and composes
    # nothing.
    await pipeline.deliver(
        session, config_with_badges, render, media_item,
        Servers({"jellyfin": jf}), {"jellyfin": jf_item.ref}, None,
    )

    row = (await session.execute(
        select(RenderDelivery.status, RenderDelivery.attempts)
    )).one()
    assert row.status == "pending" and row.attempts == 0, "a re-arm starts the row over"

    # And the next failure is the FIRST attempt of the new budget, not one
    # past the end of the old one.
    jf.raise_on_resolve = httpx.ConnectError("jellyfin is down")
    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges,
        now=datetime.now(timezone.utc) + timedelta(hours=12),
    )

    row = (await session.execute(
        select(RenderDelivery.status, RenderDelivery.attempts)
    )).one()
    assert row.status == "pending" and row.attempts == 1
    assert "jellyfin: 1 due, 0 uploaded, 0 written, 1 pending, 0 failed, 0 skipped" in summary


async def test_a_failed_commit_costs_only_its_own_row(session, config_with_badges, monkeypatch):
    """Review I3: the pass held ONE transaction over as many as 1,000 rows and
    committed once at the end, so a single failure there discarded every
    outcome row while the uploads and writes had already landed on real
    servers -- and the summary still claimed them. Each row now commits its
    own outcome, and a row whose commit fails is counted `still pending`,
    which is what it is in the database."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    items = [await _item_with_facts(session, native=f"j-i3-{n}") for n in range(3)]
    for item in items:
        await deliveries.record_metadata(session, item.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.resolve_any = resolved("jellyfin", "j-i3", file_path="/m.mkv")
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True

    real_commit = session.commit
    commits = {"n": 0}

    async def flaky_commit():
        commits["n"] += 1
        if commits["n"] == 2:
            raise RuntimeError("the connection went away")
        return await real_commit()

    monkeypatch.setattr(session, "commit", flaky_commit)

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc),
    )

    # All three writes reached the server; only the second row's OUTCOME was
    # lost, and the sentence says two rather than claiming three.
    assert len(jf.facts_written) == 3
    assert summary.startswith("pending deliveries: 3 due, 2 done, 1 still pending")
    assert "jellyfin: 3 due, 0 uploaded, 2 written, 1 pending, 0 failed, 0 skipped" in summary

    # Durable: what survives a rollback of whatever is still open is what the
    # database actually holds.
    monkeypatch.setattr(session, "commit", real_commit)
    await session.rollback()
    statuses = sorted(
        status for (status,) in
        (await session.execute(select(MetadataWrite.status))).all()
    )
    assert statuses == ["pending", "written", "written"]


def test_the_budget_is_a_scheduler_setting_defaulting_to_eight():
    from autoposter.config.schema import SchedulerConfig

    assert SchedulerConfig().delivery_attempts == 8


async def test_an_exhausted_row_is_not_retried_again(session, config_with_badges):
    """A `failed` row is never `due` (spec §2) -- the retry pass selects on
    `status == "pending"` alone, so an exhausted row does not come back
    around on its own; only a full pass or a catch-up re-arms it."""
    from autoposter.servers.registry import Servers

    config_with_badges.scheduler.delivery_attempts = 1
    render = await _render(session)
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    await session.commit()

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({}), config_with_badges, now=datetime.now(timezone.utc),
    )

    assert summary == "pending deliveries: 0 due, 0 done, 0 still pending, 0 failed, 0 skipped"


async def test_a_database_error_on_one_metadata_row_does_not_abort_the_pass(
    session, session_factory, config_with_badges, monkeypatch
):
    """Review test gap 2: both savepoint-isolation tests explode inside
    `record`, i.e. the artwork half only. The metadata loop has its own
    handler and its own `_tally(server, "pending")`, and neither was
    exercised."""
    from sqlalchemy import text

    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    items = [await _item_with_facts(session, native=f"j-gap2-{n}") for n in range(3)]
    # Ordered by next_attempt_at, so the exploding row is squarely first.
    for offset, item in zip((-900, -600, -300), items, strict=True):
        await deliveries.record_metadata(session, item.id, "jellyfin", "pending", retry_in=offset)
    await session.commit()
    ids = [item.id for item in items]

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.resolve_any = resolved("jellyfin", "j-gap2", file_path="/m.mkv")
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True

    real_record_metadata = deliveries.record_metadata

    async def exploding_record_metadata(db, item_id, server, status, **kwargs):
        if item_id == ids[0] and status == "written":
            # A genuinely aborted transaction, not a bare Python raise: that
            # is the failure class the isolation has to survive.
            await db.execute(text("SELECT 1 / 0"))
        return await real_record_metadata(db, item_id, server, status, **kwargs)

    monkeypatch.setattr(deliveries, "record_metadata", exploding_record_metadata)

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc),
    )

    assert summary == (
        "pending deliveries: 3 due, 2 done, 1 still pending, 0 failed, 0 skipped; "
        "jellyfin: 3 due, 0 uploaded, 2 written, 1 pending, 0 failed, 0 skipped"
    )
    assert len(jf.facts_written) == 3, "every row must still be attempted"
    # A separate session: the point is that rows 2 and 3 were COMMITTED, not
    # merely written and then erased by a neighbour's rollback.
    async with session_factory() as fresh:
        committed = dict((await fresh.execute(
            select(MetadataWrite.item_id, MetadataWrite.status)
        )).all())
    assert committed[ids[0]] == "pending", "the exploding row keeps its seeded state"
    assert committed[ids[1]] == committed[ids[2]] == "written"


async def test_the_retrys_write_toggle_being_off_skips_the_row_through_the_pass(
    session, config_with_badges
):
    """Review test gap 4: `operations.write_to_<server>` off is asserted by
    calling `record_metadata` directly; only `operations.enabled` had a
    pass-level test. The retry is the one path that could still write to a
    server whose toggle the operator has turned off."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS
    from autoposter.servers.registry import Servers

    item = await _item_with_facts(session, native="j-gap4")
    await deliveries.record_metadata(session, item.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.resolve_any = resolved("jellyfin", "j-gap4", file_path="/m.mkv")
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = False

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf}), config_with_badges, now=datetime.now(timezone.utc),
    )

    assert jf.facts_written == [], "the toggle is off; nothing may be written"
    assert jf.resolve_calls == 0, "the toggle is checked before the row costs a round trip"
    row = (await session.execute(select(MetadataWrite.status, MetadataWrite.detail))).one()
    assert row.status == "skipped"
    assert row.detail == "config: operations.write_to_jellyfin is off"
    assert summary == (
        "pending deliveries: 1 due, 0 done, 0 still pending, 0 failed, 1 skipped; "
        "jellyfin: 1 due, 0 uploaded, 0 written, 0 pending, 0 failed, 1 skipped"
    )


async def test_a_dual_server_pass_reports_one_sorted_clause_per_server(
    session, config_with_badges
):
    """Review test gap 5: every other test runs one server, so the sorted
    multi-clause join -- the exact string spec §2 promises the dashboard --
    was never asserted with more than one clause."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, PLEX_CAPS
    from autoposter.servers.registry import Servers

    item = await _item_with_facts(session, native="j-gap5")
    await deliveries.record_metadata(session, item.id, "jellyfin", "pending", retry_in=0)
    await deliveries.record_metadata(session, item.id, "plex", "pending", retry_in=0)
    await session.commit()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.resolve_any = resolved("jellyfin", "j-gap5", file_path="/m.mkv")
    plex = FakeMediaServer(name="plex", capabilities=PLEX_CAPS)
    plex.resolve_any = resolved("plex", "p-gap5", file_path="/m.mkv")

    async def boom(ref, facts, operations=None, parental_categories=None, overrides=None):
        raise httpx.ConnectError("plex is down")

    plex.apply_facts = boom
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True
    config_with_badges.operations.write_to_plex = True

    summary = await deliveries.retry_pending_deliveries(
        session, Servers({"jellyfin": jf, "plex": plex}), config_with_badges,
        now=datetime.now(timezone.utc),
    )

    # Sorted, so the run history's detail reads the same way twice for the
    # same work: jellyfin before plex whatever order the rows came back in.
    assert summary == (
        "pending deliveries: 2 due, 1 done, 1 still pending, 0 failed, 0 skipped; "
        "jellyfin: 1 due, 0 uploaded, 1 written, 0 pending, 0 failed, 0 skipped; "
        "plex: 1 due, 0 uploaded, 0 written, 1 pending, 0 failed, 0 skipped"
    )


async def test_both_filters_select_rows_that_then_do_real_work(session, config_with_badges):
    """Review test gap 8: both filter tests pass `Servers({})`, so every
    selected row takes the `config: server removed` branch -- the filters are
    proven to select, not to select rows that then get written."""
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, PLEX_CAPS
    from autoposter.scheduler.run_history import open_run
    from autoposter.servers.registry import Servers

    mine = await _item_with_facts(session, native="j-gap8-mine")
    theirs = await _item_with_facts(session, native="j-gap8-theirs")
    run_id = await open_run(session, kind="catch_up", name="catch_up:jellyfin")
    await deliveries.record_metadata(session, mine.id, "jellyfin", "pending", retry_in=0)
    await deliveries.record_metadata(session, theirs.id, "plex", "pending", retry_in=0)
    await session.execute(
        update(MetadataWrite)
        .where(MetadataWrite.item_id == mine.id)
        .values(run_id=run_id, previous_status="written")
    )
    await session.commit()

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.resolve_any = resolved("jellyfin", "j-gap8", file_path="/m.mkv")
    plex = FakeMediaServer(name="plex", capabilities=PLEX_CAPS)
    plex.resolve_any = resolved("plex", "p-gap8", file_path="/m.mkv")
    servers = Servers({"jellyfin": jf, "plex": plex})
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True
    config_with_badges.operations.write_to_plex = True

    # The run filter, against a registry that can actually answer.
    scoped = await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges, now=datetime.now(timezone.utc), run_id=run_id,
    )
    assert scoped == (
        "pending deliveries: 1 due, 1 done, 0 still pending, 0 failed, 0 skipped; "
        "jellyfin: 1 due, 0 uploaded, 1 written, 0 pending, 0 failed, 0 skipped"
    )
    assert [ref.native_id for ref, _ in jf.facts_written] == ["j-gap8"]
    assert plex.facts_written == [], "the other server's row is not this run's"

    # And the server filter, likewise -- a row that is written, not one that
    # takes the `server removed` branch.
    by_server = await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges, now=datetime.now(timezone.utc), server="plex",
    )
    assert by_server == (
        "pending deliveries: 1 due, 1 done, 0 still pending, 0 failed, 0 skipped; "
        "plex: 1 due, 0 uploaded, 1 written, 0 pending, 0 failed, 0 skipped"
    )
    statuses = dict((await session.execute(
        select(MetadataWrite.server, MetadataWrite.status)
    )).all())
    assert statuses == {"jellyfin": "written", "plex": "written"}


async def test_outcome_warnings_names_each_unsettled_server_and_kind(session):
    """Spec §4's sentence: one clause per unsettled (server, kind), artwork
    before metadata, with the stored detail in brackets."""
    render = await _render(session, native="w1")
    await deliveries.record(
        session, render.id, "jellyfin", "pending", detail="connect: ConnectError", retry_in=60,
    )
    await deliveries.record_metadata(
        session, render.item_id, "jellyfin", "failed", detail="status: HTTPStatusError 400",
    )
    await deliveries.record(session, render.id, "plex", "uploaded", fingerprint="fp1")
    await session.commit()

    sentence = await deliveries.outcome_warnings(
        session, render.item_id, ["jellyfin", "plex"],
    )

    assert sentence == (
        "jellyfin: artwork pending (connect: ConnectError); "
        "jellyfin: metadata failed (status: HTTPStatusError 400)"
    )


async def test_outcome_warnings_is_none_when_everything_settled(session):
    render = await _render(session, native="w2")
    await deliveries.record(session, render.id, "jellyfin", "uploaded", fingerprint="fp1")
    await deliveries.record_metadata(session, render.item_id, "jellyfin", "absent")
    await session.commit()

    assert await deliveries.outcome_warnings(session, render.item_id, ["jellyfin"]) is None


async def test_outcome_warnings_ignores_servers_this_pass_did_not_touch(session):
    render = await _render(session, native="w3")
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    await session.commit()

    assert await deliveries.outcome_warnings(session, render.item_id, ["plex"]) is None


async def test_outcome_warnings_names_a_resolution_miss_that_left_no_row(session):
    """A missed server leaves a row only when the badge stage ran AND its
    upload toggle is on, and never leaves a metadata row at all -- so the
    misses have to be told, or the case this state exists for reads as
    nothing at all."""
    render = await _render(session, native="w4")
    await deliveries.record(session, render.id, "plex", "uploaded", fingerprint="fp1")
    await session.commit()

    sentence = await deliveries.outcome_warnings(
        session, render.item_id, ["plex", "jellyfin"], misses=["jellyfin"],
    )

    assert sentence == "jellyfin: not found"


async def test_outcome_warnings_prefers_a_servers_row_over_its_miss(session):
    """A row says more than "not found" does, and a server is never honestly
    both -- so the row wins and the miss adds no second clause."""
    render = await _render(session, native="w5")
    await deliveries.record(
        session, render.id, "jellyfin", "pending", detail="connect: ConnectError", retry_in=60,
    )
    await session.commit()

    sentence = await deliveries.outcome_warnings(
        session, render.item_id, ["jellyfin"], misses=["jellyfin"],
    )

    assert sentence == "jellyfin: artwork pending (connect: ConnectError)"


async def test_outcome_warnings_orders_a_miss_among_the_other_servers(session):
    render = await _render(session, native="w6")
    await deliveries.record_metadata(
        session, render.item_id, "plex", "failed", detail="error: X",
    )
    await session.commit()

    sentence = await deliveries.outcome_warnings(
        session, render.item_id, ["plex", "jellyfin"], misses=["jellyfin"],
    )

    assert sentence == "jellyfin: not found; plex: metadata failed (error: X)"


async def test_outcome_warnings_ignores_a_miss_on_a_server_it_was_not_given(session):
    render = await _render(session, native="w7")
    await session.commit()

    assert await deliveries.outcome_warnings(
        session, render.item_id, ["plex"], misses=["jellyfin"],
    ) is None
