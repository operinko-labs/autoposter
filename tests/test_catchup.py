"""The catch-up run (spec §3): one pass over the database, not over the servers."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from autoposter import catchup, deliveries
from autoposter.db.models import (
    ItemFacts, ItemMetadataOverride, MediaItem, MetadataWrite, RenderDelivery, Run,
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


def _jellyfin(libraries=("Movies",)):
    return FakeMediaServer(
        name="jellyfin", capabilities=JELLYFIN_CAPS, libraries=set(libraries)
    )


async def _item_with_render(session, native, *, library="Movies", fingerprint="fp1", facts=True):
    item = await seed_media_item(session, native, library=library, title=native)
    if facts:
        # A catch-up arms a metadata row only for an item this service has
        # something to write for (controller ruling 1), and `item_facts` is
        # where that something lives.
        session.add(ItemFacts(item_id=item.id))
    render = await pipeline._get_or_create_render(session, item, "poster", f"/a/{native}.jpg")
    render.status = "rendered"
    render.badge_fingerprint = fingerprint
    await session.commit()
    return item, render


async def test_a_catch_up_marks_metadata_pending_and_only_behind_artwork(
    session, config_with_badges
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
        session, Servers({"jellyfin": _jellyfin()}), config_with_badges, "jellyfin", now=NOW,
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


async def test_a_catch_up_leaves_items_that_were_never_rendered_to_the_full_pass(
    session, config_with_badges
):
    item = await seed_media_item(session, "d", library="Movies", title="D")
    session.add(ItemFacts(item_id=item.id))
    await session.commit()

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), config_with_badges, "jellyfin", now=NOW,
    )
    await session.commit()

    assert (await session.execute(select(RenderDelivery))).scalars().all() == []
    # Its metadata row IS armed: a metadata write needs no render.
    row = (await session.execute(select(MetadataWrite.item_id, MetadataWrite.status))).one()
    assert row == (item.id, "pending")


async def test_a_catch_up_arms_no_metadata_row_for_an_item_it_has_nothing_to_write_for(
    session, config_with_badges
):
    """Controller ruling 1: no `item_facts` row and no override means there is
    nothing to write, so the row is left exactly as it is -- not created, and
    not moved off whatever it already says."""
    bare = await seed_media_item(session, "e1", library="Movies", title="E1")
    overridden = await seed_media_item(session, "e2", library="Movies", title="E2")
    session.add(ItemMetadataOverride(item_id=overridden.id, field="title", value="Renamed"))
    settled = await seed_media_item(session, "e3", library="Movies", title="E3")
    await deliveries.record_metadata(session, settled.id, "jellyfin", "written")
    await session.commit()

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), config_with_badges, "jellyfin", now=NOW,
    )
    await session.commit()

    rows = dict((await session.execute(
        select(MetadataWrite.item_id, MetadataWrite.status)
    )).all())
    # An override is a thing to write, so that item IS armed; the other two
    # are not, and the settled one keeps the word it already had.
    assert rows == {overridden.id: "pending", settled.id: "written"}
    assert bare.id not in rows


async def test_a_catch_up_recomputes_presence_first(session, config_with_badges):
    photo_item, photo = await _item_with_render(session, "e", library="Photos")
    await session.commit()

    await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin(("Movies",))}), config_with_badges,
        "jellyfin", now=NOW,
    )
    await session.commit()

    assert (await session.execute(select(RenderDelivery.status))).scalar_one() == "absent"
    assert (await session.execute(select(MetadataWrite.status))).scalar_one() == "absent"


async def test_a_server_that_lists_nothing_stamps_nothing_absent(session, config_with_badges):
    """`presence.read_presence`'s rule, which a catch-up has to keep: an
    empty-but-successful answer is a Jellyfin still starting up, a key without
    library scope, or an `excluded_libraries` that names every folder -- and
    stamping on it would mark the whole library absent on that server."""
    item, render = await _item_with_render(session, "f")
    await deliveries.record(session, render.id, "jellyfin", "uploaded", fingerprint="fp1")
    await session.commit()

    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin(())}), config_with_badges, "jellyfin", now=NOW,
    )
    await session.commit()

    assert (await session.execute(select(RenderDelivery.status))).scalar_one() == "uploaded"
    assert (await session.execute(select(MetadataWrite))).scalars().all() == []
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.server == "jellyfin"


async def test_a_catch_up_is_refused_while_one_is_in_flight(session, config_with_badges):
    servers = Servers({"jellyfin": _jellyfin()})
    await catchup.start_catch_up(session, servers, config_with_badges, "jellyfin", now=NOW)
    await session.commit()

    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.start_catch_up(session, servers, config_with_badges, "jellyfin", now=NOW)
    assert str(excinfo.value) == "a catch-up for jellyfin is already in flight"


async def test_a_finished_catch_up_does_not_refuse_the_next_one(session, config_with_badges):
    servers = Servers({"jellyfin": _jellyfin()})
    run_id = await catchup.start_catch_up(
        session, servers, config_with_badges, "jellyfin", now=NOW,
    )
    await close_run(session, run_id, status="ok", detail="drained")
    await session.commit()

    again = await catchup.start_catch_up(
        session, servers, config_with_badges, "jellyfin", now=NOW,
    )
    await session.commit()
    assert again != run_id


async def test_a_catch_up_is_refused_while_the_server_is_down(session, config_with_badges):
    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.start_catch_up(
            session, Servers({"jellyfin": _jellyfin()}), config_with_badges, "jellyfin",
            health={"jellyfin": SimpleNamespace(healthy=False)}, now=NOW,
        )
    assert str(excinfo.value) == (
        "jellyfin is not reachable right now; try again once it is back"
    )


async def test_a_catch_up_is_refused_for_a_server_this_deployment_does_not_have(
    session, config_with_badges
):
    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.start_catch_up(session, Servers({}), config_with_badges, "emby", now=NOW)
    assert str(excinfo.value) == "no media server named 'emby' is configured"


async def test_a_catch_up_refusal_never_carries_the_servers_address(
    session, config_with_badges
):
    import httpx

    jf = _jellyfin()

    async def boom():
        raise httpx.ConnectError("https://jellyfin.internal/Library/VirtualFolders")

    jf.library_names = boom
    with pytest.raises(catchup.CatchUpRefused) as excinfo:
        await catchup.start_catch_up(
            session, Servers({"jellyfin": jf}), config_with_badges, "jellyfin", now=NOW,
        )
    assert str(excinfo.value) == "jellyfin could not list its libraries (ConnectError)"
    assert "jellyfin.internal" not in str(excinfo.value)


async def test_the_run_carries_the_cadence_the_button_asked_for(session, config_with_badges):
    config_with_badges.scheduler.pending_deliveries_minutes = 15
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), config_with_badges, "jellyfin",
        cadence_seconds=90, now=NOW,
    )
    await session.commit()
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.cadence_seconds == 90


async def test_the_run_defaults_to_the_schedulers_cadence(session, config_with_badges):
    config_with_badges.scheduler.pending_deliveries_minutes = 15
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), config_with_badges, "jellyfin", now=NOW,
    )
    await session.commit()
    run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert run.cadence_seconds == 900


async def test_a_sub_minute_cadence_is_held_to_the_floor(session, config_with_badges):
    run_id = await catchup.start_catch_up(
        session, Servers({"jellyfin": _jellyfin()}), config_with_badges, "jellyfin",
        cadence_seconds=5, now=NOW,
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
    config.badges.upload_to_jellyfin = True
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
    session, config_with_badges, monkeypatch
):
    run_id, row = await _artwork_row_after_a_full_pass(
        session, config_with_badges, monkeypatch, finish_the_run=False,
    )
    assert row.status == "pending"
    assert row.run_id == run_id and row.previous_status == "uploaded"


async def test_a_full_pass_takes_a_finished_runs_artwork_row_back(
    session, config_with_badges, monkeypatch
):
    _, row = await _artwork_row_after_a_full_pass(
        session, config_with_badges, monkeypatch, finish_the_run=True,
    )
    assert row.status == "pending"
    assert row.run_id is None and row.previous_status is None


async def _metadata_row_after_a_full_pass(session, config, monkeypatch, *, finish_the_run):
    config.operations.write_to_jellyfin = True
    monkeypatch.setattr(pipeline, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline, "compose_badged_bytes", _compose_badged)
    servers, _, jf = _dual()
    await pipeline.process_item(
        session, config, None, servers, [], INTENT,
        tmdb_facts=_FakeTMDBFacts(), mdblist=NullMDBListClient(),
    )
    item_id = (await session.execute(select(MediaItem.id))).scalar_one()

    # The shape a catch-up leaves behind once the retry pass has spent the
    # row's budget: `failed`, still carrying the run that armed it.
    run_id = await open_run(session, kind=catchup.CATCH_UP_KIND, name="catch_up:jellyfin")
    await deliveries.record_metadata(session, item_id, "jellyfin", "failed", detail="error: X")
    await session.execute(
        update(MetadataWrite)
        .where(MetadataWrite.item_id == item_id, MetadataWrite.server == "jellyfin")
        .values(run_id=run_id, previous_status="written")
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
        select(
            MetadataWrite.status, MetadataWrite.attempts,
            MetadataWrite.run_id, MetadataWrite.previous_status,
        ).where(MetadataWrite.item_id == item_id, MetadataWrite.server == "jellyfin")
    )).one()


async def test_a_full_pass_leaves_an_open_runs_metadata_row_in_its_run(
    session, config_with_badges, monkeypatch
):
    run_id, row = await _metadata_row_after_a_full_pass(
        session, config_with_badges, monkeypatch, finish_the_run=False,
    )
    assert row.status == "pending"
    assert row.run_id == run_id and row.previous_status == "written"
    # Not re-armed either: the budget keeps climbing inside the run that owns
    # the row, rather than going back to zero from outside it.
    assert row.attempts == 2


async def test_a_full_pass_re_arms_a_finished_runs_metadata_row(
    session, config_with_badges, monkeypatch
):
    _, row = await _metadata_row_after_a_full_pass(
        session, config_with_badges, monkeypatch, finish_the_run=True,
    )
    assert row.status == "pending"
    assert row.run_id is None and row.previous_status is None
    # Reset, then counted: the whole budget back, minus this attempt.
    assert row.attempts == 1
