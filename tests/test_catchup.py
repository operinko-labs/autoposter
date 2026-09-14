"""The catch-up run (spec §3): one pass over the database, not over the servers."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from autoposter import catchup, deliveries
from autoposter.db.models import (
    ItemFacts, ItemMetadataOverride, MediaItem, MetadataWrite, Render, RenderDelivery, Run,
)
from autoposter.facts.mdblist import NullMDBListClient
from autoposter.facts.models import GatheredFacts
from autoposter.intake.arr import RenderIntent
from autoposter.render import pipeline
from autoposter.scheduler.run_history import close_run, open_run
from autoposter.servers.registry import Servers

from conftest import seed_media_item
from media_server_doubles import JELLYFIN_CAPS, FakeMediaServer, resolved

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def catch_up_config(config_with_badges):
    """The example config with Jellyfin's two switches ON.

    Both are load-bearing: a catch-up arms artwork only when
    `badges.enabled and badges.upload_to_<name>` and metadata only when
    `operations.enabled and operations.write_to_<name>`, and
    `upload_to_jellyfin` defaults to OFF.
    """
    config_with_badges.badges.upload_to_jellyfin = True
    config_with_badges.operations.write_to_jellyfin = True
    return config_with_badges


def _jellyfin(libraries=("Movies",)):
    return FakeMediaServer(
        name="jellyfin", capabilities=JELLYFIN_CAPS, libraries=set(libraries)
    )


async def _item_with_render(
    session, native, *, library="Movies", fingerprint="fp1", facts=True,
    art_kind="poster", status="rendered",
):
    item = await seed_media_item(session, native, library=library, title=native)
    if facts:
        # A catch-up arms a metadata row only for an item this service has
        # something to write for, and `item_facts` is
        # where that something lives.
        session.add(ItemFacts(item_id=item.id))
    render = await pipeline._get_or_create_render(session, item, art_kind, f"/a/{native}.jpg")
    render.status = status
    render.badge_fingerprint = fingerprint
    await session.commit()
    return item, render


async def test_a_catch_up_marks_metadata_pending_and_only_behind_artwork(
    session, catch_up_config
):
    fresh_item, fresh = await _item_with_render(session, "a")
    behind_item, behind = await _item_with_render(session, "b")
    current_item, current = await _item_with_render(session, "c")
    await deliveries.record(session, fresh.id, "jellyfin", "failed", detail="error: X")
    await deliveries.record(session, behind.id, "jellyfin", "uploaded", fingerprint="old")
    await deliveries.record(session, current.id, "jellyfin", "uploaded", fingerprint="fp1")
    await deliveries.record_metadata(session, current_item.id, "jellyfin", "written")
    await session.commit()

    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    art = dict((await session.execute(
        select(RenderDelivery.render_id, RenderDelivery.status)
    )).all())
    assert art == {fresh.id: "pending", behind.id: "pending", current.id: "uploaded"}
    metadata = dict((await session.execute(
        select(MetadataWrite.item_id, MetadataWrite.status)
    )).all())
    assert metadata == {
        fresh_item.id: "pending", behind_item.id: "pending", current_item.id: "pending",
    }
    marked = (await session.execute(
        select(RenderDelivery.previous_status).where(RenderDelivery.render_id == behind.id)
    )).scalar_one()
    assert marked == "uploaded"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.kind == "catch_up" and run.server == "jellyfin" and run.status == "running"
    assert run.name == "catch_up:jellyfin"


async def test_a_marked_row_gets_its_whole_budget_and_a_horizon_of_now(
    session, catch_up_config
):
    """Spec §6: "the budget turns a persistently failing row `failed` and the
    next catch-up re-arms it". The re-arm is `attempts = 0`, the stale failure
    reason gone, and a `next_attempt_at` of now so the very next drain takes
    it -- in BOTH tables."""
    item, render = await _item_with_render(session, "g")
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    await deliveries.record_metadata(
        session, item.id, "jellyfin", "failed", detail="error: Y",
    )
    await session.commit()

    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    art = (await session.execute(select(RenderDelivery))).scalar_one()
    assert (art.attempts, art.detail, art.next_attempt_at) == (0, None, NOW)
    assert (art.status, art.previous_status, art.run_id) == ("pending", "failed", run_id)
    write = (await session.execute(select(MetadataWrite))).scalar_one()
    assert (write.attempts, write.detail, write.next_attempt_at) == (0, None, NOW)
    assert (write.status, write.previous_status, write.run_id) == ("pending", "failed", run_id)


async def test_a_catch_up_rolls_up_the_renders_it_stamped(session, catch_up_config):
    """`renders.upload_status` is what `/api/library`'s filter, the
    dashboard tiles and the action centre read, and nothing else recomputes
    what a set-shaped write to `render_deliveries` touched. A `failed` row a
    catch-up re-arms must leave the render reading `pending`, not `failed` for
    the whole drain."""
    item, render = await _item_with_render(session, "h")
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    render.upload_status = "failed"
    await session.commit()

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    assert (await session.execute(select(Render.upload_status))).scalar_one() == "pending"


async def test_a_catch_up_leaves_items_that_were_never_rendered_to_the_full_pass(
    session, catch_up_config
):
    item = await seed_media_item(session, "d", library="Movies", title="D")
    session.add(ItemFacts(item_id=item.id))
    await session.commit()

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    assert (await session.execute(select(RenderDelivery))).scalars().all() == []
    # Its metadata row IS armed: a metadata write needs no render.
    row = (await session.execute(select(MetadataWrite.item_id, MetadataWrite.status))).one()
    assert row == (item.id, "pending")


async def test_a_catch_up_arms_no_row_for_a_background_or_an_unrendered_render(
    session, catch_up_config
):
    """Both clauses of `rendered`. A background render is never delivered to
    any server (`deliver`'s own first gate), so a `pending` row for one would
    be a due row no pass can settle; a render that has not been composed has
    no bytes to send."""
    await _item_with_render(session, "i1", art_kind="background")
    await _item_with_render(session, "i2", status="failed")
    await session.commit()

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    assert (await session.execute(select(RenderDelivery))).scalars().all() == []


async def test_a_catch_up_arms_no_metadata_row_for_an_item_it_has_nothing_to_write_for(
    session, catch_up_config
):
    """No `item_facts` row and no override means there is
    nothing to write, so the row is left exactly as it is -- not created, and
    not moved off whatever it already says."""
    bare = await seed_media_item(session, "e1", library="Movies", title="E1")
    overridden = await seed_media_item(session, "e2", library="Movies", title="E2")
    session.add(ItemMetadataOverride(item_id=overridden.id, field="title", value="Renamed"))
    settled = await seed_media_item(session, "e3", library="Movies", title="E3")
    session.add(ItemFacts(item_id=settled.id))
    await deliveries.record_metadata(session, settled.id, "jellyfin", "written")
    await session.commit()

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    rows = dict((await session.execute(
        select(MetadataWrite.item_id, MetadataWrite.status)
    )).all())
    # An override is a thing to write, and so is a facts row; the bare item is
    # not armed at all.
    assert rows == {overridden.id: "pending", settled.id: "pending"}
    assert bare.id not in rows


async def test_a_configured_field_verb_makes_every_item_writable(session, catch_up_config):
    """The predicate mirrors `apply_metadata`'s gate term for term,
    and two of its terms are CONFIG-level. A verb IS its field's source and
    fires even when no provider has anything to say, so with one configured an
    item `persist_facts` wrote no row for -- most commonly a season -- is armed
    like any other."""
    bare = await seed_media_item(session, "j", library="Movies", title="J")
    await session.commit()
    catch_up_config.operations.field_verbs = {"studio": "lock"}

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    row = (await session.execute(select(MetadataWrite.item_id, MetadataWrite.status))).one()
    assert row == (bare.id, "pending")


async def test_a_catch_up_leaves_a_skipped_metadata_row_alone(session, catch_up_config):
    """A `skipped` row is an exemption (row 35) or a switched-off
    write, and re-arming it costs a resolve, a label read and a plan per exempt
    item to record the same word again -- while blanking the stored reason the
    item page shows. The full pass is what learns that an exemption was
    lifted."""
    item = await seed_media_item(session, "k", library="Movies", title="K")
    session.add(ItemFacts(item_id=item.id))
    await deliveries.record_metadata(
        session, item.id, "jellyfin", "skipped", detail="label: no-overlay",
    )
    await session.commit()

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    row = (await session.execute(select(MetadataWrite))).scalar_one()
    assert (row.status, row.detail, row.run_id) == ("skipped", "label: no-overlay", None)


async def test_a_second_run_keeps_the_marker_that_a_run_created_the_row(
    session, catch_up_config
):
    """`previous_status` NULL means "a run CREATED this row", and a
    cancel deletes such a row rather than inventing a status for it. A second
    run marking a row the first one created must not write `pending` over that
    NULL."""
    item = await seed_media_item(session, "l", library="Movies", title="L")
    session.add(ItemFacts(item_id=item.id))
    await session.commit()
    servers = Servers({"jellyfin": _jellyfin()})

    first = await catchup.start_catch_up(
        session, servers, catch_up_config, "jellyfin", now=NOW,
    )
    await close_run(session, first, status="ok", detail="drained")
    await session.commit()
    created = (await session.execute(select(MetadataWrite))).scalar_one()
    assert created.previous_status is None and created.run_id == first

    second = await catchup.start_catch_up(
        session, servers, catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    row = (await session.execute(select(MetadataWrite))).scalar_one()
    assert row.run_id == second and row.previous_status is None


async def test_a_second_run_keeps_the_previous_status_the_first_one_recorded(
    session, catch_up_config
):
    """A second catch-up over an ALREADY-armed row does
    not move it off the word it carries, and does not write over the
    `previous_status` the first run recorded. Recording the row's own
    `pending` there would have a later cancel restore a delivered row to
    `pending` -- a due row nothing will ever settle, with what the server
    actually holds lost."""
    item, render = await _item_with_render(session, "l2")
    await deliveries.record(session, render.id, "jellyfin", "uploaded", fingerprint="old")
    await deliveries.record_metadata(session, item.id, "jellyfin", "written")
    await session.commit()
    servers = Servers({"jellyfin": _jellyfin()})

    first = await catchup.start_catch_up(
        session, servers, catch_up_config, "jellyfin", now=NOW,
    )
    await close_run(session, first, status="ok", detail="drained")
    await session.commit()

    second = await catchup.start_catch_up(
        session, servers, catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    art = (await session.execute(select(RenderDelivery))).scalar_one()
    assert (art.status, art.previous_status, art.run_id) == ("pending", "uploaded", second)
    write = (await session.execute(select(MetadataWrite))).scalar_one()
    assert (write.status, write.previous_status, write.run_id) == ("pending", "written", second)


async def test_an_upload_disabled_server_gets_no_artwork_rows(session, catch_up_config):
    """`deliver`'s own rule: "an upload-disabled server must
    never get a `pending` catch-up row, only for the very next retry pass to
    immediately overwrite it with `skipped`". `upload_to_jellyfin` defaults to
    off and spec §3 starts a catch-up automatically after a restart that
    introduced a server."""
    item, render = await _item_with_render(session, "m")
    catch_up_config.badges.upload_to_jellyfin = False

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    assert (await session.execute(select(RenderDelivery))).scalars().all() == []
    # The metadata half is a separate switch and is still on.
    assert (await session.execute(select(MetadataWrite.status))).scalar_one() == "pending"


async def test_a_write_disabled_server_gets_no_metadata_rows(session, catch_up_config):
    item, render = await _item_with_render(session, "n")
    catch_up_config.operations.write_to_jellyfin = False

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    assert (await session.execute(select(MetadataWrite))).scalars().all() == []
    assert (await session.execute(select(RenderDelivery.status))).scalar_one() == "pending"


async def test_a_catch_up_recomputes_presence_first(session, catch_up_config):
    photo_item, photo = await _item_with_render(session, "e", library="Photos")
    await session.commit()

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin(("Movies",))}), catch_up_config,
        "jellyfin", now=NOW,
    )
    await session.commit()

    assert (await session.execute(select(RenderDelivery.status))).scalar_one() == "absent"
    assert (await session.execute(select(MetadataWrite.status))).scalar_one() == "absent"


async def test_a_server_that_lists_nothing_stamps_nothing_absent(session, catch_up_config):
    """`presence.read_presence`'s rule, which a catch-up has to keep: an
    empty-but-successful answer is a Jellyfin still starting up, a key without
    library scope, or an `excluded_libraries` that names every folder -- and
    stamping on it would mark the whole library absent on that server."""
    item, render = await _item_with_render(session, "f")
    await deliveries.record(session, render.id, "jellyfin", "uploaded", fingerprint="fp1")
    await session.commit()

    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin(())}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    assert (await session.execute(select(RenderDelivery.status))).scalar_one() == "uploaded"
    assert (await session.execute(select(MetadataWrite))).scalars().all() == []
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.server == "jellyfin"


async def test_a_catch_up_is_refused_while_one_is_in_flight(session, catch_up_config):
    servers = Servers({"jellyfin": _jellyfin()})
    await catchup.start_catch_up(session, servers, catch_up_config, "jellyfin", now=NOW)
    await session.commit()

    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.start_catch_up(session, servers, catch_up_config, "jellyfin", now=NOW)
    assert str(excinfo.value) == "a catch-up for jellyfin is already in flight"
    # Not transient -- the work is already happening, so an
    # automatic request for it is dropped rather than re-queued.
    assert excinfo.value.transient is False


async def test_two_concurrent_starts_open_exactly_one_run(
    session, session_factory, catch_up_config
):
    """The in-flight SELECT and the `open_run` INSERT are one READ
    COMMITTED transaction and nothing in the schema forbids two open runs per
    server, so a double-clicked button -- or the button racing spec §3's
    automatic post-save start -- used to open a run each, and the second
    re-marked the first's rows out from under it."""
    servers = Servers({"jellyfin": _jellyfin()})

    async def start():
        async with session_factory() as own:
            try:
                run_id = await catchup.start_catch_up(
                    own, servers, catch_up_config, "jellyfin", now=NOW,
                )
            except catchup.CatchUpRefused as exc:
                return exc
            await own.commit()
            return run_id

    outcomes = await asyncio.gather(start(), start())

    opened = [o for o in outcomes if isinstance(o, int)]
    refused = [o for o in outcomes if isinstance(o, catchup.CatchUpRefused)]
    assert len(opened) == 1 and len(refused) == 1
    assert str(refused[0]) == "a catch-up for jellyfin is already in flight"
    assert (await session.execute(select(func.count()).select_from(Run))).scalar_one() == 1


async def test_a_finished_catch_up_does_not_refuse_the_next_one(session, catch_up_config):
    servers = Servers({"jellyfin": _jellyfin()})
    run_id = await catchup.start_catch_up(
        session, servers, catch_up_config, "jellyfin", now=NOW,
    )
    await close_run(session, run_id, status="ok", detail="drained")
    await session.commit()

    again = await catchup.start_catch_up(
        session, servers, catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()
    assert again != run_id


async def test_a_catch_up_is_refused_while_the_server_is_down(session, catch_up_config):
    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.start_catch_up(
            session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin",
            health={"jellyfin": SimpleNamespace(healthy=False)}, now=NOW,
        )
    assert str(excinfo.value) == (
        "jellyfin is not reachable right now; try again once it is back"
    )
    # Transient -- a down server comes back, and spec §3's
    # post-restart trigger fires only once, so its request is re-queued.
    assert excinfo.value.transient is True


async def test_a_catch_up_is_refused_for_a_server_this_deployment_does_not_have(
    session, catch_up_config
):
    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.start_catch_up(session, Servers({}), catch_up_config, "emby", now=NOW)
    assert str(excinfo.value) == "no media server named 'emby' is configured"
    # Not transient -- waiting never makes a server configured.
    assert excinfo.value.transient is False


async def test_a_catch_up_is_refused_while_the_scheduler_is_off(session, catch_up_config):
    """The drain job is registered inside the scheduler's own gate, so with the
    scheduler off a catch-up would mark the whole library and then be drained
    by nothing: the unscoped retry pass takes `run_id IS NULL` rows only and
    the pipeline's re-arm doors skip a live run's rows. The run would never
    close and the in-flight check would refuse every later catch-up for that
    server, automatic ones included."""
    catch_up_config.scheduler.enabled = False
    jf = _jellyfin()

    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.start_catch_up(
            session, Servers({"jellyfin": jf}), catch_up_config, "jellyfin", now=NOW,
        )

    assert str(excinfo.value) == "the scheduler is off; a catch-up needs it to drain"
    # Not transient -- waiting never switches the scheduler on, so an
    # automatic request carrying this reason is dropped rather than re-queued.
    assert excinfo.value.transient is False
    # Refused before anything was opened or marked.
    assert (await session.execute(select(Run))).first() is None
    assert (await session.execute(select(RenderDelivery))).first() is None


async def test_a_catch_up_refusal_never_carries_the_servers_address(
    session, catch_up_config
):
    import httpx

    jf = _jellyfin()

    async def boom():
        raise httpx.ConnectError("https://jellyfin.internal/Library/VirtualFolders")

    jf.library_names = boom
    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.start_catch_up(
            session, Servers({"jellyfin": jf}), catch_up_config, "jellyfin", now=NOW,
        )
    assert str(excinfo.value) == "jellyfin could not list its libraries (ConnectError)"
    assert "jellyfin.internal" not in str(excinfo.value)
    # Transient -- a live network round trip against a server that
    # may still be starting up is the boot trigger's own failure mode.
    assert excinfo.value.transient is True


async def test_the_run_carries_the_cadence_the_button_asked_for(session, catch_up_config):
    catch_up_config.scheduler.pending_deliveries_minutes = 15
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin",
        cadence_seconds=90, now=NOW,
    )
    await session.commit()
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.cadence_seconds == 90


async def test_the_run_defaults_to_the_schedulers_cadence(session, catch_up_config):
    catch_up_config.scheduler.pending_deliveries_minutes = 15
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.cadence_seconds == 900


@pytest.mark.parametrize("asked", [5, 0])
async def test_a_sub_minute_cadence_is_held_to_the_floor(session, catch_up_config, asked):
    """`0` included: an explicit zero is an operator asking for the
    fastest drain there is, and `or` would have read it as "unset" and given
    him the scheduler's quarter of an hour instead."""
    catch_up_config.scheduler.pending_deliveries_minutes = 15
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin",
        cadence_seconds=asked, now=NOW,
    )
    await session.commit()
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.cadence_seconds == catchup.MIN_CADENCE_SECONDS


# --- the pipeline's re-arm doors, against a row a catch-up owns -------------
#
# Through `process_item` -- the real entry point. A row
# an OPEN run armed is already pending under that run, so the ordinary
# pipeline leaves it alone, `run_id` and `previous_status` included, or the
# run's progress and its cancel would both act on rows it no longer owns. A
# row whose run has FINISHED is an ordinary row again and leaves it.

INTENT = RenderIntent(kind="movie", title="Title", tmdb_id=1, year=2020)


class _FakeTMDBFacts:
    async def movie(self, tmdb_id):
        return GatheredFacts(audience_rating=6.3, sources={"audience_rating": "tmdb"})


async def _fake_render_artifact(session, config, http, item, art_kind, providers, **_kwargs):
    media_item = await pipeline._upsert_media_item(session, item)
    render = await pipeline._get_or_create_render(
        session, media_item, art_kind, f"/tmp/{art_kind}.jpg"
    )
    render.status = "rendered"
    await session.commit()
    return render


async def _compose_badged(session, config, render, media_item, **_kwargs):
    return b"badged"


async def _compose_nothing(session, config, render, media_item, **_kwargs):
    return None


def _dual():
    plex = FakeMediaServer(name="plex", libraries={"Movies"})
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS, libraries={"Movies"})
    plex.items[INTENT.dedupe_key] = resolved("plex", "p1", file_path="/plex/m.mkv")
    jf.items[INTENT.dedupe_key] = resolved("jellyfin", "j1", file_path="/jf/m.mkv")
    return Servers({"plex": plex, "jellyfin": jf}), plex, jf


async def _artwork_row_after_a_full_pass(session, config, monkeypatch, *, finish_the_run):
    monkeypatch.setattr(pipeline, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline, "compose_badged_bytes", _compose_badged)
    servers, _, _ = _dual()
    renders = await pipeline.process_item(session, config, None, servers, [], INTENT)
    poster = next(r for r in renders if r.art_kind == "poster")
    # The render has moved on since that upload, so the catch-up below finds
    # this row behind and arms it.
    poster.badge_fingerprint = "fp2"
    await session.commit()

    run_id = await catchup.start_catch_up(session, servers, config, "jellyfin", now=NOW)
    if finish_the_run:
        await close_run(session, run_id, status="ok", detail="drained")
    await session.commit()

    # Nothing new composed this pass: `deliver`'s first re-arm door.
    monkeypatch.setattr(pipeline, "compose_badged_bytes", _compose_nothing)
    await pipeline.process_item(session, config, None, servers, [], INTENT)

    return run_id, (await session.execute(
        select(RenderDelivery.status, RenderDelivery.run_id, RenderDelivery.previous_status)
        .where(RenderDelivery.render_id == poster.id, RenderDelivery.server == "jellyfin")
    )).one()


async def test_a_full_pass_leaves_an_open_runs_artwork_row_in_its_run(
    session, catch_up_config, monkeypatch
):
    run_id, row = await _artwork_row_after_a_full_pass(
        session, catch_up_config, monkeypatch, finish_the_run=False,
    )
    assert row.status == "pending"
    assert row.run_id == run_id and row.previous_status == "uploaded"


async def test_a_full_pass_takes_a_finished_runs_artwork_row_back(
    session, catch_up_config, monkeypatch
):
    _, row = await _artwork_row_after_a_full_pass(
        session, catch_up_config, monkeypatch, finish_the_run=True,
    )
    assert row.status == "pending"
    assert row.run_id is None and row.previous_status is None


async def _metadata_row_after_a_full_pass(
    session, config, monkeypatch, *, finish_the_run, armed_status="failed",
):
    monkeypatch.setattr(pipeline, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline, "compose_badged_bytes", _compose_badged)
    servers, _, jf = _dual()
    await pipeline.process_item(
        session, config, None, servers, [], INTENT,
        tmdb_facts=_FakeTMDBFacts(), mdblist=NullMDBListClient(),
    )
    item_id = (await session.execute(select(MediaItem.id))).scalar_one()

    # `failed`: the shape a catch-up leaves behind once the retry pass has
    # spent the row's budget. `pending`: the shape while the drain still owes
    # it, which is the one that carries a horizon.
    run_id = await open_run(session, kind=catchup.CATCH_UP_KIND, name="catch_up:jellyfin")
    await deliveries.record_metadata(
        session, item_id, "jellyfin", armed_status,
        detail="error: X", retry_in=0 if armed_status == "pending" else None,
    )
    await session.execute(
        update(MetadataWrite)
        .where(MetadataWrite.item_id == item_id, MetadataWrite.server == "jellyfin")
        .values(
            run_id=run_id, previous_status="written",
            **({"next_attempt_at": NOW} if armed_status == "pending" else {}),
        )
    )
    if finish_the_run:
        await close_run(session, run_id, status="ok", detail="drained")
    await session.commit()

    async def boom(ref, facts, operations=None, parental_categories=None, overrides=None):
        raise RuntimeError("jellyfin refused the write")

    jf.apply_facts = boom
    await pipeline.process_item(
        session, config, None, servers, [], INTENT,
        tmdb_facts=_FakeTMDBFacts(), mdblist=NullMDBListClient(),
    )

    return run_id, (await session.execute(
        select(MetadataWrite).where(
            MetadataWrite.item_id == item_id, MetadataWrite.server == "jellyfin",
        )
    )).scalar_one()


async def test_a_full_pass_leaves_an_open_runs_metadata_row_in_its_run(
    session, catch_up_config, monkeypatch
):
    run_id, row = await _metadata_row_after_a_full_pass(
        session, catch_up_config, monkeypatch, finish_the_run=False,
    )
    assert row.status == "pending"
    assert row.run_id == run_id and row.previous_status == "written"
    # Not re-armed either: the budget keeps climbing inside the run that owns
    # the row, rather than going back to zero from outside it.
    assert row.attempts == 2
    # The row had no horizon to keep (a `failed` row carries none), so it takes
    # the new one -- a `pending` row with no `next_attempt_at` is never due.
    assert row.next_attempt_at is not None


async def test_a_full_pass_re_arms_a_finished_runs_metadata_row(
    session, catch_up_config, monkeypatch
):
    _, row = await _metadata_row_after_a_full_pass(
        session, catch_up_config, monkeypatch, finish_the_run=True,
    )
    assert row.status == "pending"
    assert row.run_id is None and row.previous_status is None
    # Reset, then counted: the whole budget back, minus this attempt.
    assert row.attempts == 1


async def test_a_full_pass_leaves_an_open_runs_horizon_where_the_run_put_it(
    session, catch_up_config, monkeypatch
):
    """The run's own drain owns the timing of the rows it armed. A
    full pass whose write fails would otherwise push the row six hours out and
    stall a drain the run is still counting on."""
    _, row = await _metadata_row_after_a_full_pass(
        session, catch_up_config, monkeypatch, finish_the_run=False, armed_status="pending",
    )
    assert row.status == "pending" and row.next_attempt_at == NOW


async def test_a_full_pass_pushes_a_finished_runs_horizon_out(
    session, catch_up_config, monkeypatch
):
    _, row = await _metadata_row_after_a_full_pass(
        session, catch_up_config, monkeypatch, finish_the_run=True, armed_status="pending",
    )
    assert row.status == "pending"
    # Off the run's horizon and onto this pass's own, six hours from the
    # attempt it just made. Asserted against `attempted_at` rather than `NOW`:
    # the row's timestamps come from the database's clock, not the test's.
    assert row.next_attempt_at != NOW
    assert row.next_attempt_at == row.attempted_at + timedelta(
        seconds=deliveries.RETRY_SECONDS
    )


# --- progress, cancel, retry-failed (spec §3, §5) --------------------------


async def test_progress_counts_this_runs_rows_only(session, catch_up_config):
    item_a, render_a = await _item_with_render(session, "p1")
    item_b, render_b = await _item_with_render(session, "p2")
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()
    # One of each settles; one metadata row fails.
    await deliveries.record(session, render_a.id, "jellyfin", "uploaded", fingerprint="fp1")
    await deliveries.record_metadata(session, item_a.id, "jellyfin", "written")
    await deliveries.record_metadata(session, item_b.id, "jellyfin", "failed", detail="error: X")
    await session.commit()

    progress = await catchup.catch_up_progress(session, "jellyfin")

    assert progress["run_id"] == run_id and progress["server"] == "jellyfin"
    assert progress["status"] == "running"
    assert (progress["due"], progress["done"], progress["failed"]) == (1, 2, 1)
    assert progress["total"] == 4


async def test_progress_is_none_for_a_server_that_never_had_one(session):
    assert await catchup.catch_up_progress(session, "jellyfin") is None


async def test_cancelling_restores_prior_statuses_and_removes_the_rows_it_created(
    session, catch_up_config
):
    kept_item, kept = await _item_with_render(session, "c1")
    fresh_item, fresh = await _item_with_render(session, "c2")
    await deliveries.record(session, kept.id, "jellyfin", "failed", detail="error: X")
    await session.commit()

    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    outcome = await catchup.cancel_catch_up(session, "jellyfin", now=NOW)
    await session.commit()

    art = dict((await session.execute(
        select(RenderDelivery.render_id, RenderDelivery.status)
    )).all())
    assert art == {kept.id: "failed"}          # restored; fresh's row removed
    assert (await session.execute(select(MetadataWrite))).scalars().all() == []
    assert outcome["run_id"] == run_id
    assert outcome["restored"] == 1 and outcome["removed"] == 3
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.status == "cancelled" and run.finished_at is not None


async def test_cancelling_never_undoes_what_was_already_delivered(
    session, catch_up_config
):
    item, render = await _item_with_render(session, "c3")
    await deliveries.record(session, render.id, "jellyfin", "uploaded", fingerprint="old")
    await session.commit()
    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    await catchup.cancel_catch_up(session, "jellyfin", now=NOW)
    await session.commit()

    row = (await session.execute(select(
        RenderDelivery.status, RenderDelivery.uploaded_at, RenderDelivery.fingerprint
    ))).one()
    assert row.status == "uploaded" and row.uploaded_at is not None
    assert row.fingerprint == "old"


async def test_cancelling_releases_every_row_the_run_still_owns(session, catch_up_config):
    """A cancelled run owns nothing afterwards. A row that SETTLED inside it
    keeps its outcome -- nothing uploaded is undone -- but loses the two scope
    columns with the rest, or a later progress query would go on counting for
    a run that is over."""
    item, render = await _item_with_render(session, "c4")
    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()
    await deliveries.record(session, render.id, "jellyfin", "uploaded", fingerprint="fp1")
    await session.commit()

    await catchup.cancel_catch_up(session, "jellyfin", now=NOW)
    await session.commit()

    art = (await session.execute(select(RenderDelivery))).scalar_one()
    assert art.status == "uploaded"
    assert art.run_id is None and art.previous_status is None


async def test_cancelling_with_nothing_in_flight_is_refused(session):
    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.cancel_catch_up(session, "jellyfin", now=NOW)
    assert str(excinfo.value) == "no catch-up for jellyfin is in flight"


async def test_retry_failed_re_arms_both_tables_for_one_server(session):
    item, render = await _item_with_render(session, "r1")
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    await deliveries.record(session, render.id, "plex", "failed", detail="error: X")
    await deliveries.record_metadata(session, item.id, "jellyfin", "failed", detail="error: X")
    await session.commit()

    outcome = await catchup.retry_failed(session, "jellyfin", now=NOW)
    await session.commit()

    assert outcome == {"server": "jellyfin", "artwork": 1, "metadata": 1}
    statuses = dict((await session.execute(
        select(RenderDelivery.server, RenderDelivery.status)
    )).all())
    assert statuses == {"jellyfin": "pending", "plex": "failed"}
    metadata = (await session.execute(select(
        MetadataWrite.status, MetadataWrite.attempts, MetadataWrite.next_attempt_at
    ))).one()
    assert metadata.status == "pending" and metadata.attempts == 0
    assert metadata.next_attempt_at == NOW


async def test_retry_failed_takes_a_failed_row_out_of_the_run_that_armed_it(
    session, catch_up_config
):
    """EVERY `failed` row for that server is re-armed,
    inside a run or outside one, and as an ORDINARY row -- the unscoped retry
    pass takes `run_id IS NULL` rows only, so a row left in its run would be
    re-armed here and then drained by nothing until that run's own cadence
    came round."""
    item, render = await _item_with_render(session, "r2")
    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()
    # The run's drain spends both rows' budgets and gives up on them.
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    await deliveries.record_metadata(session, item.id, "jellyfin", "failed", detail="error: Y")
    await session.commit()

    outcome = await catchup.retry_failed(session, "jellyfin", now=NOW)
    await session.commit()

    assert outcome == {"server": "jellyfin", "artwork": 1, "metadata": 1}
    art = (await session.execute(select(RenderDelivery))).scalar_one()
    assert (art.status, art.run_id, art.previous_status) == ("pending", None, None)
    assert (art.attempts, art.detail, art.next_attempt_at) == (0, None, NOW)
    write = (await session.execute(select(MetadataWrite))).scalar_one()
    assert (write.status, write.run_id, write.previous_status) == ("pending", None, None)


async def test_retry_failed_rolls_up_the_renders_it_re_armed(session):
    """For this writer too: nothing else recomputes what a
    set-shaped write to `render_deliveries` touched, so a `failed` row this
    re-arms would leave the render reading `failed` -- flagged in the action
    centre and served stale on the item page -- until the drain reached it."""
    item, render = await _item_with_render(session, "r3")
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    render.upload_status = "failed"
    await session.commit()

    await catchup.retry_failed(session, "jellyfin", now=NOW)
    await session.commit()

    assert (await session.execute(select(Render.upload_status))).scalar_one() == "pending"


async def test_progress_counts_a_skipped_row_inside_the_run(session, catch_up_config):
    """`skipped` is the retry pass's own word for a per-library
    toggle switched off mid-drain or an exemption found during the write, and
    none of those calls pass `leave_run` -- so the row keeps its `run_id` and
    must be counted. Counted nowhere, it left a `total` smaller than the
    backlog the run marked, with no bucket accounting for the difference."""
    item, render = await _item_with_render(session, "p3")
    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()
    await deliveries.record_metadata(
        session, item.id, "jellyfin", "skipped", detail="label: no-overlay",
    )
    await session.commit()

    progress = await catchup.catch_up_progress(session, "jellyfin")

    # The artwork row is still due; the metadata row has settled.
    assert (progress["due"], progress["done"], progress["failed"]) == (1, 1, 0)
    assert progress["total"] == 2


async def test_progress_after_a_cancel_reports_the_tallies_it_had(
    session, catch_up_config
):
    """Concern 3, ruled a fix: a cancel releases every row the run owned, so
    counting them afterwards answers zero for a run that marked four. The
    tallies are taken before the release and stamped on the run row, and that
    is what a finished run serves."""
    item_a, render_a = await _item_with_render(session, "p4")
    item_b, render_b = await _item_with_render(session, "p5")
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()
    await deliveries.record(session, render_a.id, "jellyfin", "uploaded", fingerprint="fp1")
    await deliveries.record_metadata(session, item_b.id, "jellyfin", "failed", detail="error: X")
    await session.commit()

    await catchup.cancel_catch_up(session, "jellyfin", now=NOW)
    await session.commit()

    # Nothing carries the run id any more...
    assert (await session.execute(select(func.count()).select_from(RenderDelivery).where(
        RenderDelivery.run_id == run_id
    ))).scalar_one() == 0
    # ...and the progress line still reports what the run had done.
    progress = await catchup.catch_up_progress(session, "jellyfin")
    assert progress["run_id"] == run_id and progress["status"] == "cancelled"
    assert (progress["due"], progress["done"], progress["failed"]) == (2, 1, 1)
    assert progress["total"] == 4


async def test_cancelling_rolls_up_the_renders_it_restores(session, catch_up_config):
    """`start_catch_up` rolled this render up to `pending` when it
    armed the row, and the restore puts the row back to `failed` without
    anything recomputing `renders.upload_status` -- the column
    `/api/library`'s filter, the dashboard tiles and the action centre read."""
    item, render = await _item_with_render(session, "c5")
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    render.upload_status = "failed"
    await session.commit()

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()
    assert (await session.execute(select(Render.upload_status))).scalar_one() == "pending"

    await catchup.cancel_catch_up(session, "jellyfin", now=NOW)
    await session.commit()

    assert (await session.execute(select(Render.upload_status))).scalar_one() == "failed"


async def test_retry_failed_stamps_when_it_last_looked_at_the_server(session):
    """Every other writer to these two tables stamps
    `attempted_at`, the "we last looked at this server" timestamp."""
    item, render = await _item_with_render(session, "r4")
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    await session.commit()

    await catchup.retry_failed(session, "jellyfin", now=NOW)
    await session.commit()

    assert (await session.execute(select(RenderDelivery.attempted_at))).scalar_one() == NOW


async def test_the_re_arm_doors_read_the_open_runs_once_per_item(
    session, catch_up_config, monkeypatch
):
    """The open-run check used to cost one primary-key
    SELECT per re-arm door hit, and nothing ever clears `run_id` off a
    terminal outcome -- so every row a catch-up has ever touched paid that
    query on every later pass. The set of open catch-ups is read ONCE per
    `process_item` and handed down to the doors exactly as `absent_servers`
    is."""
    reads = 0
    real = pipeline.open_catch_up_runs

    async def counting(session):
        nonlocal reads
        reads += 1
        return await real(session)

    monkeypatch.setattr(pipeline, "open_catch_up_runs", counting)

    run_id, row = await _artwork_row_after_a_full_pass(
        session, catch_up_config, monkeypatch, finish_the_run=False,
    )

    # Two `process_item` calls, each over several renders: one read each, and
    # not one from a door.
    assert reads == 2
    # And the door still did its job off the set it was handed.
    assert row.status == "pending" and row.run_id == run_id


# --- the drain (spec §3) ---------------------------------------------------


async def test_the_drain_takes_one_batch_and_closes_the_run_when_it_empties(
    session, catch_up_config, monkeypatch
):
    from autoposter.render import pipeline as pipeline_module

    catch_up_config.operations.enabled = True
    item, render = await _item_with_render(session, "d1")
    jf = _jellyfin()
    jf.resolve_any = resolved("jellyfin", "d1", file_path="/m.mkv")

    async def fake_compose(session, config, render, media_item, **kwargs):
        return b"badged"

    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", fake_compose)

    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": jf}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    summary = await catchup.drain_catch_ups(
        session, Servers({"jellyfin": jf}), catch_up_config, now=NOW,
    )

    assert summary == "catch-up: jellyfin finished, 2 done, 0 failed"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.status == "ok" and run.finished_at is not None
    assert run.detail == "catch-up: 2 done, 0 failed"
    assert run.last_drained_at == NOW


async def test_a_finished_runs_tallies_outlive_the_rows_it_released(
    session, catch_up_config, monkeypatch
):
    """The finish stamps the tallies BEFORE it releases, and releases every
    row it owned -- a run that is over must own nothing, or a later progress
    query goes on counting for it. The stored tallies are what the progress
    line serves once the rows are gone."""
    from autoposter.render import pipeline as pipeline_module

    catch_up_config.operations.enabled = True
    item, render = await _item_with_render(session, "d5")
    jf = _jellyfin()
    jf.resolve_any = resolved("jellyfin", "d5", file_path="/m.mkv")

    async def fake_compose(session, config, render, media_item, **kwargs):
        return b"badged"

    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", fake_compose)

    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": jf}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    await catchup.drain_catch_ups(
        session, Servers({"jellyfin": jf}), catch_up_config, now=NOW,
    )

    owned = (await session.execute(
        select(func.count()).select_from(RenderDelivery).where(RenderDelivery.run_id == run_id)
    )).scalar_one()
    assert owned == 0
    art = (await session.execute(select(RenderDelivery))).scalar_one()
    assert art.status == "uploaded" and art.previous_status is None
    progress = await catchup.catch_up_progress(session, "jellyfin")
    assert (progress["due"], progress["done"], progress["failed"]) == (0, 2, 0)
    assert progress["total"] == 2


async def test_the_drain_respects_the_per_run_cadence(session, catch_up_config):
    await _item_with_render(session, "d2")
    jf = _jellyfin()
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": jf}), catch_up_config, "jellyfin",
        cadence_seconds=600, now=NOW,
    )
    await session.execute(
        update(Run).where(Run.id == run_id).values(last_drained_at=NOW - timedelta(seconds=30))
    )
    await session.commit()

    summary = await catchup.drain_catch_ups(
        session, Servers({"jellyfin": jf}), catch_up_config, now=NOW,
    )

    assert summary == "catch-up: jellyfin waiting 600s between batches"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.finished_at is None


async def test_the_drain_reports_a_run_still_working(session, catch_up_config):
    """A server that cannot resolve leaves its rows pending, and the run stays open."""
    catch_up_config.operations.enabled = True
    await _item_with_render(session, "d3")
    jf = _jellyfin()  # resolves nothing
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": jf}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    summary = await catchup.drain_catch_ups(
        session, Servers({"jellyfin": jf}), catch_up_config, now=NOW,
    )

    assert summary == "catch-up: jellyfin 2 still due, 0 done, 0 failed"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.finished_at is None
    assert run.last_drained_at == NOW


async def test_the_drain_says_so_when_nothing_is_in_flight(session, catch_up_config):
    assert await catchup.drain_catch_ups(
        session, Servers({}), catch_up_config, now=NOW,
    ) == "catch-up: nothing in flight"


async def test_the_drain_takes_only_the_rows_of_the_run_it_is_draining(
    session, catch_up_config, monkeypatch
):
    """The batch is scoped by `run_id` (spec §3): an ORDINARY due row -- one
    no catch-up armed -- is left to the unscoped pending-deliveries pass, so
    it neither spends this run's batch nor advances its progress."""
    from autoposter.render import pipeline as pipeline_module

    catch_up_config.operations.enabled = False
    jf = _jellyfin()
    jf.resolve_any = resolved("jellyfin", "d4", file_path="/m.mkv")

    async def fake_compose(session, config, render, media_item, **kwargs):
        return b"badged"

    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", fake_compose)

    await _item_with_render(session, "d4")
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": jf}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()
    # Armed AFTER the catch-up marked its backlog, so it carries no run id.
    _, ordinary = await _item_with_render(session, "d4b")
    await deliveries.record(session, ordinary.id, "jellyfin", "pending", retry_in=0)
    await session.commit()
    columns = (
        RenderDelivery.status, RenderDelivery.attempts, RenderDelivery.next_attempt_at,
    )
    before = (await session.execute(
        select(*columns).where(RenderDelivery.render_id == ordinary.id)
    )).one()

    summary = await catchup.drain_catch_ups(
        session, Servers({"jellyfin": jf}), catch_up_config, now=NOW,
    )

    assert summary == "catch-up: jellyfin finished, 1 done, 0 failed"
    # Untouched, field for field -- and still due, for the unscoped pass.
    left = (await session.execute(
        select(*columns).where(RenderDelivery.render_id == ordinary.id)
    )).one()
    assert left == before and left.status == "pending"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.status == "ok"


async def test_a_run_that_moves_nothing_twice_stops_and_frees_the_server(
    session, catch_up_config
):
    """A resolution miss is a wait, not a failure -- it spends no
    attempt budget -- so a row for a file the server will never scan is `due`
    for ever, and the in-flight check would refuse every later catch-up for
    that server, automatic ones included. A run whose batch moves nothing
    twice running closes, and its rows go back to being ordinary pending
    waits for the unscoped retry pass."""
    catch_up_config.operations.enabled = True
    await _item_with_render(session, "s1")
    jf = _jellyfin()  # resolves nothing, for ever
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": jf}), catch_up_config, "jellyfin",
        cadence_seconds=60, now=NOW,
    )
    await session.commit()

    first = await catchup.drain_catch_ups(
        session, Servers({"jellyfin": jf}), catch_up_config, now=NOW,
    )
    assert first == "catch-up: jellyfin 2 still due, 0 done, 0 failed"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.finished_at is None
    # Counted, not acted on: one idle batch is an outage, not a verdict.
    assert run.idle_drains == 1

    later = NOW + timedelta(seconds=60)
    second = await catchup.drain_catch_ups(
        session, Servers({"jellyfin": jf}), catch_up_config, now=later,
    )

    assert second == "catch-up: jellyfin stopped with 2 still due, 0 done, 0 failed"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.status == "ok" and run.finished_at is not None
    assert run.detail == "catch-up: stopped with 2 still due, 0 done, 0 failed"
    # Released: ordinary pending rows again, which the unscoped pass drains.
    art = (await session.execute(select(RenderDelivery))).scalar_one()
    assert (art.status, art.run_id, art.previous_status) == ("pending", None, None)
    # And the server is free, which is the whole point of closing it.
    again = await catchup.start_catch_up(
        session, Servers({"jellyfin": jf}), catch_up_config, "jellyfin", now=later,
    )
    await session.commit()
    assert again != run_id


async def test_a_row_on_a_future_horizon_keeps_its_run_open(session, catch_up_config):
    """The finish condition counts every `pending` row the run owns, with no
    horizon predicate, while the BATCH takes only rows due now. The asymmetry
    is deliberate: a row waiting on a horizon is still owed, so the run stays
    open and comes back for it at its next cadence."""
    catch_up_config.operations.enabled = False
    item, render = await _item_with_render(session, "s2")
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.execute(
        update(RenderDelivery)
        .where(RenderDelivery.run_id == run_id)
        .values(next_attempt_at=NOW + timedelta(hours=6))
    )
    await session.commit()

    summary = await catchup.drain_catch_ups(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, now=NOW,
    )

    # The batch took nothing -- the row is not due yet -- and the run is open.
    assert summary == "catch-up: jellyfin 1 still due, 0 done, 0 failed"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.finished_at is None
    row = (await session.execute(select(RenderDelivery))).scalar_one()
    assert row.next_attempt_at == NOW + timedelta(hours=6) and row.attempts == 0


async def test_two_open_runs_are_drained_independently_and_reported_in_order(
    session, catch_up_config
):
    """One clause per run, joined in `runs.id` order, each on its OWN cadence:
    a run mid-cadence waits while another drains in the same pass."""
    catch_up_config.operations.enabled = False
    await _item_with_render(session, "s3")
    jf, plex = _jellyfin(), FakeMediaServer(name="plex", libraries={"Movies"})
    servers = Servers({"jellyfin": jf, "plex": plex})

    waiting = await catchup.start_catch_up(
        session, servers, catch_up_config, "jellyfin", cadence_seconds=600, now=NOW,
    )
    await session.execute(
        update(Run).where(Run.id == waiting)
        .values(last_drained_at=NOW - timedelta(seconds=30))
    )
    draining = await catchup.start_catch_up(
        session, servers, catch_up_config, "plex", now=NOW,
    )
    await session.commit()

    summary = await catchup.drain_catch_ups(session, servers, catch_up_config, now=NOW)

    assert summary == (
        "catch-up: jellyfin waiting 600s between batches; "
        "plex 1 still due, 0 done, 0 failed"
    )
    rows = dict((await session.execute(select(Run.id, Run.last_drained_at))).all())
    assert rows[waiting] == NOW - timedelta(seconds=30)   # untouched
    assert rows[draining] == NOW


async def test_the_drain_commits_its_own_bookkeeping(session, session_factory, catch_up_config):
    """Read back through a SECOND session: every other test here reads through
    the one that ran the pass, so a missing commit would go unnoticed -- and
    the run's close is what the next poll's in-flight check reads."""
    catch_up_config.operations.enabled = False
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    await catchup.drain_catch_ups(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, now=NOW,
    )

    async with session_factory() as other:
        run = (await other.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.finished_at is not None and run.last_drained_at == NOW
    assert run.detail == "catch-up: 0 done, 0 failed"


async def test_one_runs_failure_does_not_skip_the_runs_behind_it(
    session, catch_up_config, monkeypatch
):
    """The runs are walked in a stable `runs.id` order, so a raise
    outside a row -- the retry pass contains the per-row ones itself -- used to
    abort the whole pass and leave the same run first in line on the next
    poll, with the runs behind it never getting a batch. Contained per run,
    and the clause carries a class NAME, never the exception's message."""
    catch_up_config.operations.enabled = False
    await _item_with_render(session, "s5")
    servers = Servers({
        "jellyfin": _jellyfin(), "plex": FakeMediaServer(name="plex", libraries={"Movies"}),
    })
    await catchup.start_catch_up(session, servers, catch_up_config, "jellyfin", now=NOW)
    await catchup.start_catch_up(session, servers, catch_up_config, "plex", now=NOW)
    await session.commit()

    real = catchup.retry_pending_deliveries

    async def explode(session, servers, config, **kwargs):
        if kwargs.get("server") == "jellyfin":
            raise RuntimeError("https://jellyfin.internal/Items")
        return await real(session, servers, config, **kwargs)

    monkeypatch.setattr(catchup, "retry_pending_deliveries", explode)

    summary = await catchup.drain_catch_ups(session, servers, catch_up_config, now=NOW)

    assert summary == (
        "catch-up: jellyfin failed (RuntimeError); plex 1 still due, 0 done, 0 failed"
    )
    assert "jellyfin.internal" not in summary


async def _run_with_staggered_horizons(session, config, jf, monkeypatch, horizons):
    """A catch-up whose rows come due at different times, so each drain can be
    made to move exactly one row -- or none.

    ``horizons`` is one offset from ``NOW`` per row; the first is left due
    immediately. Uploads are made to succeed, so a row the batch takes moves.
    """
    from autoposter.render import pipeline as pipeline_module

    async def fake_compose(session, config, render, media_item, **kwargs):
        return b"badged"

    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", fake_compose)
    config.operations.enabled = False
    jf.resolve_any = resolved("jellyfin", "n1", file_path="/m.mkv")

    renders = []
    for index, _ in enumerate(horizons):
        _, render = await _item_with_render(session, f"n{index}")
        renders.append(render)
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": jf}), config, "jellyfin",
        cadence_seconds=60, now=NOW,
    )
    for render, offset in zip(renders, horizons):
        if offset is None:
            continue
        await session.execute(
            update(RenderDelivery)
            .where(RenderDelivery.render_id == render.id)
            .values(next_attempt_at=NOW + offset)
        )
    await session.commit()
    return run_id


async def test_a_run_that_moves_rows_again_after_an_idle_batch_stays_open(
    session, catch_up_config, monkeypatch
):
    """The case that tells the two predicates apart: the rule
    is TWO CONSECUTIVE idle batches, not "this batch left the counts where the
    last one did". A run that moves rows, then moves nothing, then moves rows
    again is working -- and a brief outage mid-drain, which turns every row of
    a batch into a resolution miss, is exactly that shape."""
    jf = _jellyfin()
    run_id = await _run_with_staggered_horizons(
        session, catch_up_config, jf, monkeypatch,
        [None, timedelta(hours=1), timedelta(hours=3)],
    )
    servers = Servers({"jellyfin": jf})

    first = await catchup.drain_catch_ups(session, servers, catch_up_config, now=NOW)
    assert first == "catch-up: jellyfin 2 still due, 1 done, 0 failed"

    # Nothing is due yet: an idle batch, counted but not acted on.
    idle = await catchup.drain_catch_ups(
        session, servers, catch_up_config, now=NOW + timedelta(seconds=60),
    )
    assert idle == "catch-up: jellyfin 2 still due, 1 done, 0 failed"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.finished_at is None and run.idle_drains == 1

    # The second row comes due and moves: the counter goes back to zero.
    third = await catchup.drain_catch_ups(
        session, servers, catch_up_config, now=NOW + timedelta(hours=2),
    )
    assert third == "catch-up: jellyfin 1 still due, 2 done, 0 failed"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.finished_at is None and run.idle_drains == 0


async def test_two_idle_batches_after_a_productive_one_stop_the_run(
    session, catch_up_config, monkeypatch
):
    """The other half of the rule: the counter is CONSECUTIVE, so a run that
    worked and then went quiet twice running is stopped all the same."""
    jf = _jellyfin()
    run_id = await _run_with_staggered_horizons(
        session, catch_up_config, jf, monkeypatch, [None, timedelta(hours=3)],
    )
    servers = Servers({"jellyfin": jf})

    assert await catchup.drain_catch_ups(session, servers, catch_up_config, now=NOW) == (
        "catch-up: jellyfin 1 still due, 1 done, 0 failed"
    )
    await catchup.drain_catch_ups(
        session, servers, catch_up_config, now=NOW + timedelta(seconds=60),
    )
    stopped = await catchup.drain_catch_ups(
        session, servers, catch_up_config, now=NOW + timedelta(seconds=120),
    )

    assert stopped == "catch-up: jellyfin stopped with 1 still due, 1 done, 0 failed"
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.status == "ok" and run.finished_at is not None
    assert run.idle_drains == 2


async def test_a_failing_run_does_not_roll_back_an_earlier_runs_finish(
    session, session_factory, catch_up_config, monkeypatch
):
    """The sentence this pass returns is stored on the scheduled
    run, so a clause claiming a finish has to be durable before a later run
    can roll the transaction back."""
    catch_up_config.operations.enabled = False
    servers = Servers({
        "jellyfin": _jellyfin(), "plex": FakeMediaServer(name="plex", libraries={"Movies"}),
    })
    # jellyfin's run marks nothing (no renders at all), so its first batch
    # finishes it; plex's raises.
    finished = await catchup.start_catch_up(
        session, servers, catch_up_config, "jellyfin", now=NOW,
    )
    await catchup.start_catch_up(session, servers, catch_up_config, "plex", now=NOW)
    await session.commit()

    real = catchup.retry_pending_deliveries

    async def explode(session, servers, config, **kwargs):
        if kwargs.get("server") == "plex":
            raise RuntimeError("boom")
        return await real(session, servers, config, **kwargs)

    monkeypatch.setattr(catchup, "retry_pending_deliveries", explode)

    summary = await catchup.drain_catch_ups(session, servers, catch_up_config, now=NOW)

    assert summary == (
        "catch-up: jellyfin finished, 0 done, 0 failed; plex failed (RuntimeError)"
    )
    # What the sentence claims, read back through a second session.
    async with session_factory() as other:
        run = (await other.execute(select(Run).where(Run.id == finished))).scalar_one()
    assert run.finished_at is not None and run.status == "ok"


async def test_a_cancel_mid_drain_survives_the_drains_own_finish(
    session, session_factory, catch_up_config, monkeypatch
):
    """The operator's DELETE commits while a batch is in flight, so by the time
    the drain counts again nothing carries the run id and it reads the run as
    one that marked nothing. Its finish must find the run already closed and
    leave it -- status, detail and the three counts -- exactly as the cancel
    wrote them."""
    catch_up_config.operations.enabled = False
    await _item_with_render(session, "x1")
    jf = _jellyfin()
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": jf}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    async def cancel_instead_of_draining(inner, servers, config, **kwargs):
        # The route's own session, committed: READ COMMITTED, so every
        # statement the drain makes after this sees the cancelled run.
        async with session_factory() as other:
            await catchup.cancel_catch_up(other, "jellyfin", now=NOW)
            await other.commit()

    monkeypatch.setattr(catchup, "retry_pending_deliveries", cancel_instead_of_draining)

    await catchup.drain_catch_ups(
        session, Servers({"jellyfin": jf}), catch_up_config, now=NOW,
    )

    async with session_factory() as other:
        run = (await other.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.status == "cancelled"
    assert run.detail == (
        "cancelled: 1 marked, 0 done, 0 failed; 0 restored, 1 removed"
    )
    # The cancel's tallies, not the drain's zeros.
    assert (run.processed, run.deferred, run.failed) == (1, 1, 0)


async def test_a_batch_that_spends_budget_is_not_an_idle_one(
    session, session_factory, catch_up_config
):
    """Every row attempted and failed-but-not-exhausted leaves the run's status
    histogram byte-identical -- each row is still `pending` -- so counting
    statuses alone reads a real attempt as an idle batch, and two of them
    close a run on a server that is merely refusing writes. The budget the
    batch spent is the other half of "this batch moved something"."""
    catch_up_config.operations.enabled = False
    await _item_with_render(session, "x2")
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()
    refusing = _jellyfin()
    refusing.raise_on_resolve = RuntimeError("https://jellyfin.internal/Items")

    summary = await catchup.drain_catch_ups(
        session, Servers({"jellyfin": refusing}), catch_up_config, now=NOW,
    )

    assert summary == "catch-up: jellyfin 1 still due, 0 done, 0 failed"
    async with session_factory() as other:
        run = (await other.execute(select(Run).where(Run.id == run_id))).scalar_one()
        row = (await other.execute(select(RenderDelivery))).scalar_one()
    # The row is where it was, and one attempt poorer: not idle.
    assert (row.status, row.attempts) == ("pending", 1)
    assert run.idle_drains == 0 and run.finished_at is None


async def test_a_finish_whose_commit_fails_claims_only_the_failure(
    session, session_factory, catch_up_config, monkeypatch
):
    """The per-run clause is held back until the commit returns. A commit that
    raises is rolled back -- the finish is gone -- so the stored sentence must
    carry the failure alone and not a finish it has just discarded."""
    catch_up_config.operations.enabled = False
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, "jellyfin", now=NOW,
    )
    await session.commit()

    async def drained_nothing(inner, servers, config, **kwargs):
        """No rows, and no commits of its own: the next commit is the drain's."""

    monkeypatch.setattr(catchup, "retry_pending_deliveries", drained_nothing)
    real_commit, calls = session.commit, []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        await real_commit()

    monkeypatch.setattr(session, "commit", flaky)

    summary = await catchup.drain_catch_ups(
        session, Servers({"jellyfin": _jellyfin()}), catch_up_config, now=NOW,
    )

    assert summary == "catch-up: jellyfin failed (RuntimeError)"
    # And the finish the rollback discarded is not there either.
    async with session_factory() as other:
        run = (await other.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.finished_at is None and run.status == "running"
