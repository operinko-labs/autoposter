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

    Both are load-bearing since review I3: a catch-up arms artwork only when
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
        # something to write for (controller ruling 1), and `item_facts` is
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
    """Review I1: `renders.upload_status` is what `/api/library`'s filter, the
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
    """Controller ruling 1: no `item_facts` row and no override means there is
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
    """Review I2: the predicate mirrors `apply_metadata`'s gate term for term,
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
    """Review M4: a `skipped` row is an exemption (row 35) or a switched-off
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
    """Review M2: `previous_status` NULL means "a run CREATED this row", and a
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
    """Controller ruling 2: a second catch-up over an ALREADY-armed row does
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
    """Review I3, and `deliver`'s own rule: "an upload-disabled server must
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


async def test_two_concurrent_starts_open_exactly_one_run(
    session, session_factory, catch_up_config
):
    """Review I4: the in-flight SELECT and the `open_run` INSERT are one READ
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


async def test_a_catch_up_is_refused_for_a_server_this_deployment_does_not_have(
    session, catch_up_config
):
    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.start_catch_up(session, Servers({}), catch_up_config, "emby", now=NOW)
    assert str(excinfo.value) == "no media server named 'emby' is configured"


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
    """`0` included (review M6): an explicit zero is an operator asking for the
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
# Controller ruling 2, through `process_item` -- the real entry point. A row
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
    """Ruling 5: the run's own drain owns the timing of the rows it armed. A
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
    """Controller ruling 1: EVERY `failed` row for that server is re-armed,
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
    """Review I1's finding, for this writer: nothing else recomputes what a
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
    """Review I1: `skipped` is the retry pass's own word for a per-library
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
    """Review I2: `start_catch_up` rolled this render up to `pending` when it
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
    """Review M2: every other writer to these two tables stamps
    `attempted_at`, the "we last looked at this server" timestamp."""
    item, render = await _item_with_render(session, "r4")
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    await session.commit()

    await catchup.retry_failed(session, "jellyfin", now=NOW)
    await session.commit()

    assert (await session.execute(select(RenderDelivery.attempted_at))).scalar_one() == NOW
