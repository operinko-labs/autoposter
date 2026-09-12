"""The ``asset_stats`` backfill pass (roadmap row 52).

``renders.size_bytes`` is stamped when an artifact is published, so every row
written before that column existed -- which is every row in a deployed
instance, plus every adoption row, ~16k artifacts -- is NULL forever unless
something goes and looks. This is that something: a weekly pass that stats at
most ``scheduler.asset_stats_batch_size`` unsized rows per run, off the
request path, holding no transaction across the walk.

Driven through the ``Job`` the factory returns, because the factory is what
``app.py`` registers and what the cadence deref lives on.
"""

from pathlib import Path

from sqlalchemy import select

from autoposter.api.routes import SCHEDULED_JOB_NAMES
from autoposter.config.holder import ConfigHolder
from autoposter.config.loader import load_config
from autoposter.db.models import Render
from autoposter.scheduler import jobs as jobs_module
from autoposter.scheduler.jobs import make_asset_stats_job

from conftest import seed_media_item

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def _holder(**scheduler_overrides):
    config = load_config(EXAMPLE)
    for name, value in scheduler_overrides.items():
        setattr(config.scheduler, name, value)
    return ConfigHolder(config)


async def _item(session, rating_key="1"):
    return await seed_media_item(session, rating_key, library="Movies", kind="movie", title="Dune")


async def _render(session, item, art_kind, asset_path, *, status="rendered",
                  size_bytes=None):
    row = Render(
        item_id=item.id, art_kind=art_kind, asset_path=str(asset_path),
        status=status, size_bytes=size_bytes,
    )
    session.add(row)
    await session.commit()
    return row


async def _sizes(session):
    rows = (
        await session.execute(select(Render).order_by(Render.art_kind))
    ).scalars().all()
    return {row.art_kind: row.size_bytes for row in rows}


async def test_the_sweep_stamps_every_unsized_rendered_row(session, tmp_path):
    item = await _item(session)
    poster = tmp_path / "poster.jpg"
    poster.write_bytes(b"0123456789")
    background = tmp_path / "background.jpg"
    background.write_bytes(b"01234")
    await _render(session, item, "poster", poster)
    await _render(session, item, "background", background)

    detail = await make_asset_stats_job(_holder()).run(session)

    session.expire_all()
    assert await _sizes(session) == {"poster": 10, "background": 5}
    assert detail == "stamped 2 render row(s) missing a size"


async def test_an_asset_that_cannot_be_read_is_stamped_zero(session, tmp_path):
    """A row whose file is gone is stamped 0, not left NULL: it occupies no
    storage, which is what the aggregate should report, and the pipeline
    restamps the real size the next time this artifact is published -- so
    the row is never re-selected and the backfill still makes progress."""
    item = await _item(session)
    present = tmp_path / "poster.jpg"
    present.write_bytes(b"0123456789")
    await _render(session, item, "poster", present)
    await _render(session, item, "background", tmp_path / "gone.jpg")

    detail = await make_asset_stats_job(_holder()).run(session)

    session.expire_all()
    assert await _sizes(session) == {"poster": 10, "background": 0}
    assert detail == (
        "stamped 2 render row(s) missing a size"
        "; 1 unreadable asset(s) recorded as 0 bytes"
    )
    # Row 213: counts only. No path, no filename, no root.
    assert "poster.jpg" not in detail
    assert str(tmp_path) not in detail


async def test_the_sweep_takes_at_most_one_batch_per_run(session, tmp_path):
    """The valve, ``drift_batch_size``'s rule: ~16k grandfathered rows on an
    NFS mount are worked through over successive runs rather than in one
    multi-minute stat storm."""
    item = await _item(session)
    for index, art_kind in enumerate(
        ("background", "poster", "season_poster", "title_card")
    ):
        asset = tmp_path / f"{art_kind}.jpg"
        asset.write_bytes(b"x" * (index + 1))
        await _render(session, item, art_kind, asset)

    detail = await make_asset_stats_job(_holder(asset_stats_batch_size=2)).run(session)

    session.expire_all()
    stamped = [size for size in (await _sizes(session)).values() if size is not None]
    assert len(stamped) == 2, "the batch valve did not bound the run"
    assert detail == "stamped 2 render row(s) missing a size"


async def test_the_sweep_advances_past_dead_rows_across_runs(
    session, tmp_path, monkeypatch
):
    """A leading block of dead rows must not stall the backfill forever
    (Important #1): once a dead row is stamped 0 it is no longer NULL, so the
    next run's ``ORDER BY renders.id`` selection reaches the live rows behind
    it. Batch size 2, four rows, dead ones first -- run 1 sees only the dead
    block, run 2 must reach the live block and must not re-stat the dead
    rows, and run 3 is then a no-op."""
    item = await _item(session)
    dead = [
        await _render(session, item, f"dead_{i}", tmp_path / f"gone_{i}.jpg")
        for i in range(2)
    ]
    live_paths = []
    for i in range(2):
        asset = tmp_path / f"live_{i}.jpg"
        asset.write_bytes(b"x" * (i + 1))
        live_paths.append(asset)
    for i, path in enumerate(live_paths):
        await _render(session, item, f"live_{i}", path)

    calls: list[set[int]] = []
    real = jobs_module._stat_sizes

    def _tracking(rows):
        calls.append({render_id for render_id, _ in rows})
        return real(rows)

    monkeypatch.setattr(jobs_module, "_stat_sizes", _tracking)

    job = make_asset_stats_job(_holder(asset_stats_batch_size=2))

    detail_1 = await job.run(session)
    session.expire_all()
    sizes_1 = await _sizes(session)
    measured_1 = sum(1 for size in sizes_1.values() if size is not None)
    assert measured_1 == 2, "run 1 should stamp exactly the dead block"
    assert sizes_1["dead_0"] == 0 and sizes_1["dead_1"] == 0
    assert sizes_1["live_0"] is None and sizes_1["live_1"] is None
    assert detail_1 == (
        "stamped 2 render row(s) missing a size"
        "; 2 unreadable asset(s) recorded as 0 bytes"
    )

    detail_2 = await job.run(session)
    session.expire_all()
    sizes_2 = await _sizes(session)
    measured_2 = sum(1 for size in sizes_2.values() if size is not None)
    assert measured_2 > measured_1, "run 2 must make progress past the dead rows"
    assert measured_2 == 4
    assert sizes_2["live_0"] == 1 and sizes_2["live_1"] == 2

    dead_ids = {row.id for row in dead}
    assert calls[0] == dead_ids, "run 1 should see exactly the dead block"
    assert calls[1].isdisjoint(dead_ids), "run 2 must not re-stat the dead rows"
    assert detail_2 == "stamped 2 render row(s) missing a size"

    detail_3 = await job.run(session)
    assert detail_3 == "no render row is missing a size", "run 3 must be a no-op"
    assert len(calls) == 2, "a no-op run must not touch the filesystem at all"


async def test_the_sweep_holds_no_transaction_across_the_walk(
    session, tmp_path, monkeypatch
):
    """The idle-in-transaction law (scheduler/prune.py, scheduler/merge.py).
    The SELECT above opens a transaction; holding it across a stat of every
    unsized asset on an NFS mount pins a pooled connection and the vacuum
    horizon for the whole walk. ``_stat_sizes`` is replaced with a stand-in
    that reports what the session was doing when it was called."""
    item = await _item(session)
    asset = tmp_path / "poster.jpg"
    asset.write_bytes(b"0123456789")
    await _render(session, item, "poster", asset)
    observed = {}

    real = jobs_module._stat_sizes

    def _watching(rows):
        observed["in_transaction"] = session.in_transaction()
        return real(rows)

    monkeypatch.setattr(jobs_module, "_stat_sizes", _watching)

    await make_asset_stats_job(_holder()).run(session)

    assert observed["in_transaction"] is False, (
        "the read transaction was still open across the filesystem walk"
    )
    session.expire_all()
    assert (await _sizes(session))["poster"] == 10, (
        "closing the transaction cost the write"
    )


async def test_the_job_is_named_in_the_run_now_allowlist_and_runs_weekly(session):
    """Weekly, like every other maintenance pass, and hand-triggerable from
    the dashboard. ``tests/test_api_scheduled_runs.py``'s agreement guard is
    the other half of this: it reads the ``Job(name=...)`` literals out of
    scheduler/jobs.py, merge.py and prune.py and demands exact agreement with
    ``SCHEDULED_JOB_NAMES`` in both directions."""
    assert "asset_stats" in SCHEDULED_JOB_NAMES

    holder = _holder()
    job = make_asset_stats_job(holder)
    assert job.name == "asset_stats"
    assert job.current_interval() == 7 * 24 * 3600

    # The cadence is a deref, not a captured value: an edited interval reaches
    # the running scheduler on its next poll (scheduler/core.py's Job).
    holder.current.scheduler.asset_stats_days = 3
    assert job.current_interval() == 3 * 24 * 3600
