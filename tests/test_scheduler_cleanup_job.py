"""``find_orphaned_assets`` / ``move_to_backup`` / ``make_cleanup_job``: the
periodic orphaned-asset sweep.

The tool being replaced backs up rather than deletes, and this module keeps
that behaviour: files move to ``backup_root``, never removed. An empty
``renders`` table is treated as a signal something is wrong, not as "nothing
is referenced" -- see ``test_nothing_moves_when_renders_is_empty`` for why
that distinction matters here specifically.
"""
import inspect
import threading
from types import SimpleNamespace

from sqlalchemy import select

from autoposter.db.models import MediaItem, Render
from autoposter.scheduler import jobs
from autoposter.scheduler.jobs import find_orphaned_assets, make_cleanup_job, move_to_backup

_next_rating_key = iter(str(n) for n in range(1, 1_000_000))


async def _make_render(session, asset_path, *, art_kind="poster") -> Render:
    item = MediaItem(
        rating_key=next(_next_rating_key),
        library="Movies",
        kind="movie",
        title="Item",
    )
    session.add(item)
    await session.commit()
    render = Render(item_id=item.id, art_kind=art_kind, asset_path=str(asset_path))
    session.add(render)
    await session.commit()
    return render


def _config(assets_root, backup_root, apply=False):
    return SimpleNamespace(
        assets_root=str(assets_root),
        backup_root=str(backup_root),
        cleanup=SimpleNamespace(apply=apply),
        scheduler=SimpleNamespace(cleanup_days=7),
    )


async def test_a_directory_referenced_by_a_render_is_left_alone(session, tmp_path):
    kept = tmp_path / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    orphaned = await find_orphaned_assets(session, tmp_path)

    assert kept not in orphaned


async def test_an_unreferenced_directory_is_reported(session, tmp_path):
    orphan = tmp_path / "Movies" / "Orphan Movie (2019)"
    orphan.mkdir(parents=True)
    (orphan / "poster.jpg").write_bytes(b"data")
    # A referenced directory must exist too, or the empty-renders guard would
    # apply instead -- this test is about the directory-matching logic.
    kept = tmp_path / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    orphaned = await find_orphaned_assets(session, tmp_path)

    assert orphan in orphaned
    assert kept not in orphaned


def test_move_to_backup_preserves_the_relative_path_and_returns_the_count(tmp_path):
    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    orphan = assets_root / "Movies" / "Orphan Movie (2019)"
    orphan.mkdir(parents=True)
    (orphan / "poster.jpg").write_bytes(b"data")

    moved = move_to_backup([orphan], assets_root, backup_root)

    assert moved == 1
    assert not orphan.exists()
    assert (backup_root / "Movies" / "Orphan Movie (2019)" / "poster.jpg").read_bytes() == b"data"


async def test_dry_run_moves_nothing(session, tmp_path):
    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    orphan = assets_root / "Movies" / "Orphan Movie (2019)"
    orphan.mkdir(parents=True)
    (orphan / "poster.jpg").write_bytes(b"data")
    # A real render elsewhere keeps the renders table non-empty so the
    # empty-table guard does not short-circuit this test.
    kept = assets_root / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    config = _config(assets_root, backup_root, apply=False)
    job = make_cleanup_job(config)
    summary = await job.run(session)

    assert (orphan / "poster.jpg").exists()
    assert not backup_root.exists()
    assert "dry run" in summary.lower()
    assert "1" in summary


async def test_apply_moves_the_file_and_preserves_the_relative_path(session, tmp_path):
    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    orphan = assets_root / "Movies" / "Orphan Movie (2019)"
    orphan.mkdir(parents=True)
    (orphan / "poster.jpg").write_bytes(b"data")
    kept = assets_root / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    config = _config(assets_root, backup_root, apply=True)
    job = make_cleanup_job(config)
    summary = await job.run(session)

    assert not orphan.exists()
    moved_file = backup_root / "Movies" / "Orphan Movie (2019)" / "poster.jpg"
    assert moved_file.exists()
    assert moved_file.read_bytes() == b"data"
    # The still-referenced directory must be untouched.
    assert (kept / "poster.jpg").exists()
    assert "1" in summary


async def test_nothing_moves_when_renders_is_empty(session, tmp_path):
    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    everything = assets_root / "Movies" / "Some Movie (2020)"
    everything.mkdir(parents=True)
    (everything / "poster.jpg").write_bytes(b"data")

    assert (await session.execute(select(Render))).first() is None

    config = _config(assets_root, backup_root, apply=True)
    job = make_cleanup_job(config)
    summary = await job.run(session)

    assert (everything / "poster.jpg").exists()
    assert not backup_root.exists()
    assert "empty" in summary.lower() or "refus" in summary.lower()


def test_the_module_never_deletes_anything():
    source = inspect.getsource(jobs)
    for forbidden in (".unlink(", "rmtree", "os.remove(", "shutil.rmtree"):
        assert forbidden not in source, f"found forbidden call {forbidden!r} in scheduler/jobs.py"


async def test_the_walk_runs_off_the_event_loop(session, tmp_path, monkeypatch):
    main_thread = threading.current_thread()
    walk_thread = {}
    real_walk = jobs.os.walk

    def spying_walk(*args, **kwargs):
        walk_thread["thread"] = threading.current_thread()
        return real_walk(*args, **kwargs)

    monkeypatch.setattr(jobs.os, "walk", spying_walk)

    kept = tmp_path / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    await find_orphaned_assets(session, tmp_path)

    assert walk_thread["thread"] is not None
    assert walk_thread["thread"] is not main_thread, (
        "the directory walk must go through asyncio.to_thread, not run "
        "directly on the event loop thread"
    )
