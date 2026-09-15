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
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from autoposter.config.holder import ConfigHolder
from autoposter.db.models import Render, Run
from autoposter.scheduler import jobs
from autoposter.scheduler.jobs import find_orphaned_assets, make_cleanup_job, move_to_backup
from autoposter.scheduler.run_history import open_run

from conftest import seed_media_item

_next_rating_key = iter(str(n) for n in range(1, 1_000_000))


async def _make_render(session, asset_path, *, art_kind="poster") -> Render:
    item = await seed_media_item(
        session, next(_next_rating_key), library="Movies", kind="movie", title="Item",
    )
    render = Render(item_id=item.id, art_kind=art_kind, asset_path=str(asset_path))
    session.add(render)
    await session.commit()
    return render


def _config(assets_root, backup_root, apply=False, max_orphans=500, max_orphan_share=0.25):
    return SimpleNamespace(
        assets_root=str(assets_root),
        backup_root=str(backup_root),
        cleanup=SimpleNamespace(
            apply=apply, max_orphans=max_orphans, max_orphan_share=max_orphan_share
        ),
        scheduler=SimpleNamespace(cleanup_days=7),
    )


async def test_a_directory_referenced_by_a_render_is_left_alone(session, tmp_path):
    kept = tmp_path / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    orphaned = (await find_orphaned_assets(session, tmp_path)).orphaned

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

    scan = await find_orphaned_assets(session, tmp_path)

    assert orphan in scan.orphaned
    assert kept not in scan.orphaned
    # Both directories were candidates; only one was picked.
    assert scan.scanned == 2


async def test_the_generated_divider_cache_is_never_scanned(session, tmp_path):
    """``<assets_root>/.generated/`` is service-owned render cache, not a media
    asset directory -- see ``collections/separator_art.py``. No ``renders`` row
    ever points inside it, so an unguarded walk would report it as orphaned
    forever; it must instead be pruned from the walk entirely, which also keeps
    it out of ``scanned``."""
    cache = tmp_path / ".generated" / "separators" / "orig"
    cache.mkdir(parents=True)
    (cache / "genre.jpg").write_bytes(b"data")
    # A referenced directory must exist too, or the empty-renders guard would
    # apply instead.
    kept = tmp_path / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    scan = await find_orphaned_assets(session, tmp_path)

    assert cache not in scan.orphaned
    assert scan.scanned == 1


def test_move_to_backup_preserves_the_relative_path_and_returns_the_count(tmp_path):
    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    orphan = assets_root / "Movies" / "Orphan Movie (2019)"
    orphan.mkdir(parents=True)
    (orphan / "poster.jpg").write_bytes(b"data")

    outcome = move_to_backup([orphan], assets_root, backup_root)

    assert (outcome.moved, outcome.failed) == (1, 0)
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
    job = make_cleanup_job(ConfigHolder(config))
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
    job = make_cleanup_job(ConfigHolder(config))
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
    job = make_cleanup_job(ConfigHolder(config))
    summary = await job.run(session)

    assert (everything / "poster.jpg").exists()
    assert not backup_root.exists()
    assert "empty" in summary.lower() or "refus" in summary.lower()


def test_the_module_never_deletes_anything():
    source = inspect.getsource(jobs)
    for forbidden in (".unlink(", "rmtree", "os.remove(", "shutil.rmtree"):
        assert forbidden not in source, f"found forbidden call {forbidden!r} in scheduler/jobs.py"


async def test_the_whole_match_runs_off_the_event_loop(session, tmp_path, monkeypatch):
    """Not just the walk: the orphan decision too.

    Off-loading ``os.walk`` alone is not enough and spying only on it cannot
    tell the difference. The comparison that decides orphan-hood is the
    expensive half -- it used to be one ``is_relative_to`` per (directory,
    render) pair -- so this asserts every ``Path.parents`` access and every
    ``Path.resolve`` call also happens on a worker thread.
    """
    kept = tmp_path / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    orphan = tmp_path / "Movies" / "Orphan Movie (2019)"
    orphan.mkdir(parents=True)
    (orphan / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    main_thread = threading.current_thread()
    threads: dict[str, set] = {"walk": set(), "match": set()}
    root = str(tmp_path)
    real_walk = jobs.os.walk
    real_resolve = Path.resolve
    real_parents = Path.parents.fget

    def spying_walk(*args, **kwargs):
        threads["walk"].add(threading.current_thread())
        return real_walk(*args, **kwargs)

    def spying_resolve(self, *args, **kwargs):
        if str(self).startswith(root):
            threads["match"].add(threading.current_thread())
        return real_resolve(self, *args, **kwargs)

    def spying_parents(self):
        if str(self).startswith(root):
            threads["match"].add(threading.current_thread())
        return real_parents(self)

    monkeypatch.setattr(jobs.os, "walk", spying_walk)
    monkeypatch.setattr(Path, "resolve", spying_resolve)
    monkeypatch.setattr(Path, "parents", property(spying_parents))

    await find_orphaned_assets(session, tmp_path)

    assert threads["walk"] and main_thread not in threads["walk"], (
        "the directory walk must go through asyncio.to_thread, not run "
        "directly on the event loop thread"
    )
    assert threads["match"] and main_thread not in threads["match"], (
        "the orphan match must run inside the same asyncio.to_thread as the "
        "walk; it is the expensive half and blocks the loop if left behind"
    )


async def test_the_match_is_linear_not_quadratic(session, tmp_path, monkeypatch):
    """The orphan decision must not compare every directory to every render.

    ``Path.is_relative_to`` costs microseconds; against ~18,000 directories
    and ~16,000 render rows the pairwise form is 2.5e8 calls and minutes of
    a blocked event loop. Counting the calls is the only way to see the
    difference from a small fixture -- the pairwise form makes
    directories * renders of them, the set form makes none.
    """
    for n in range(10):
        directory = tmp_path / "Movies" / f"Movie {n} (20{n:02d})"
        directory.mkdir(parents=True)
        (directory / "poster.jpg").write_bytes(b"data")
        await _make_render(session, directory / "poster.jpg")

    calls = []
    real_is_relative_to = Path.is_relative_to

    def counting_is_relative_to(self, *args, **kwargs):
        calls.append(1)
        return real_is_relative_to(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_relative_to", counting_is_relative_to)

    scan = await find_orphaned_assets(session, tmp_path)

    assert scan.orphaned == []
    assert scan.scanned == 10
    assert len(calls) < 10, (
        "orphan-hood is being decided by comparing every directory against "
        "every render path, which is quadratic; use one set of kept ancestors"
    )


async def test_assets_root_itself_is_never_a_candidate(session, tmp_path):
    """A file sitting directly in ``assets_root`` made the root its own
    orphan: ``relative_to`` yields ``Path('.')``, ``backup_root / '.'`` is
    ``backup_root``, and one ``shutil.move`` relocates the entire tree."""
    assets_root = tmp_path / "assets"
    stray = assets_root / "stray.jpg"
    assets_root.mkdir(parents=True)
    stray.write_bytes(b"data")
    kept = assets_root / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    scan = await find_orphaned_assets(session, assets_root)

    assert assets_root not in scan.orphaned
    assert assets_root.resolve() not in scan.orphaned

    config = _config(assets_root, tmp_path / "backup", apply=True)
    await make_cleanup_job(ConfigHolder(config)).run(session)

    assert stray.exists()
    assert (kept / "poster.jpg").exists()


async def test_an_implausible_orphan_share_refuses_the_whole_pass(session, tmp_path):
    """Rows exist, but they describe a different tree -- a repointed
    ``assets_root``, a remounted volume, ``library_folders`` toggled, a
    partial restore. Every directory then looks orphaned and the
    empty-renders guard passes happily."""
    assets_root = tmp_path / "assets"
    for n in range(30):
        directory = assets_root / "Movies" / f"Movie {n} (2020)"
        directory.mkdir(parents=True)
        (directory / "poster.jpg").write_bytes(b"data")
    # The one render row points somewhere else entirely, as it would after
    # the root moved.
    elsewhere = tmp_path / "old-assets" / "Movies" / "Movie 0 (2020)"
    elsewhere.mkdir(parents=True)
    await _make_render(session, elsewhere / "poster.jpg")

    config = _config(assets_root, tmp_path / "backup", apply=True)
    summary = await make_cleanup_job(ConfigHolder(config)).run(session)

    assert "refus" in summary.lower()
    assert "30" in summary, "the refusal must report the real numbers: %r" % summary
    assert not (tmp_path / "backup").exists()
    assert (assets_root / "Movies" / "Movie 0 (2020)" / "poster.jpg").exists()


async def test_an_implausible_absolute_orphan_count_refuses_the_whole_pass(session, tmp_path):
    """The share cap cannot fire on a tree that is mostly fine, so the
    absolute cap covers a large tree that has gone wrong in bulk."""
    assets_root = tmp_path / "assets"
    for n in range(4):
        directory = assets_root / "Movies" / f"Orphan {n} (2020)"
        directory.mkdir(parents=True)
        (directory / "poster.jpg").write_bytes(b"data")
    for n in range(20):
        directory = assets_root / "Movies" / f"Kept {n} (2020)"
        directory.mkdir(parents=True)
        (directory / "poster.jpg").write_bytes(b"data")
        await _make_render(session, directory / "poster.jpg")

    config = _config(assets_root, tmp_path / "backup", apply=True, max_orphans=3)
    summary = await make_cleanup_job(ConfigHolder(config)).run(session)

    assert "refus" in summary.lower()
    assert "4" in summary and "3" in summary
    assert not (tmp_path / "backup").exists()

    # One under the cap and the same tree is worked normally.
    config = _config(assets_root, tmp_path / "backup", apply=True, max_orphans=4)
    summary = await make_cleanup_job(ConfigHolder(config)).run(session)
    assert "moved 4" in summary


async def test_a_colliding_backup_destination_gets_a_suffix_and_does_not_nest(tmp_path):
    """``shutil.move`` onto an existing directory moves the source *inside*
    it (``backup/Movies/X/X``); the next collision raises ``shutil.Error``
    mid-loop and takes the count of what already moved with it."""
    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    orphans = []
    for n in range(2):
        orphan = assets_root / "Movies" / f"Orphan {n} (2019)"
        orphan.mkdir(parents=True)
        (orphan / "poster.jpg").write_bytes(b"new")
        orphans.append(orphan)
        # A previous pass already backed up a directory of the same name.
        existing = backup_root / "Movies" / f"Orphan {n} (2019)"
        existing.mkdir(parents=True)
        (existing / "poster.jpg").write_bytes(b"old")

    outcome = move_to_backup(orphans, assets_root, backup_root)

    assert (outcome.moved, outcome.failed) == (2, 0)
    for n in range(2):
        assert not orphans[n].exists()
        original = backup_root / "Movies" / f"Orphan {n} (2019)"
        assert original.joinpath("poster.jpg").read_bytes() == b"old"
        assert not original.joinpath(f"Orphan {n} (2019)").exists(), "the move nested"
        assert original.with_name(f"Orphan {n} (2019).1" ).joinpath(
            "poster.jpg"
        ).read_bytes() == b"new"


def test_one_unmovable_directory_does_not_discard_the_others(tmp_path, monkeypatch):
    """A mid-loop exception used to skip every remaining orphan *and* lose
    the count of what had already moved."""
    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    orphans = []
    for n in range(3):
        orphan = assets_root / "Movies" / f"Orphan {n}"
        orphan.mkdir(parents=True)
        (orphan / "poster.jpg").write_bytes(b"data")
        orphans.append(orphan)

    real_move = jobs.shutil.move

    def failing_move(src, dst, *args, **kwargs):
        if "Orphan 1" in str(src):
            raise OSError("device is busy")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(jobs.shutil, "move", failing_move)

    outcome = move_to_backup(orphans, assets_root, backup_root)

    assert (outcome.moved, outcome.failed) == (2, 1)
    assert orphans[1].exists(), "the failing directory must be left in place"
    assert not orphans[0].exists()
    assert not orphans[2].exists(), "orphans after the failure must still be attempted"


async def test_an_asset_path_recorded_through_an_equivalent_spelling_still_matches(
    session, tmp_path
):
    """Lexical matching made a directory look orphaned whenever its
    ``asset_path`` was recorded through a symlink or a ``..`` segment.
    ``resolve()`` on both sides is what makes them the same directory."""
    assets_root = tmp_path / "assets"
    kept = assets_root / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    indirect = assets_root / "Movies" / ".." / "Movies" / "Kept Movie (2020)" / "poster.jpg"
    await _make_render(session, indirect)

    scan = await find_orphaned_assets(session, assets_root)

    assert scan.orphaned == [], "an equivalently-spelled path must still count as a reference"


def test_move_to_backup_refuses_assets_root_itself(tmp_path):
    """``find_orphaned_assets`` no longer offers it, but this is the function
    that actually moves files and the failure mode is the whole library:
    ``relative_to`` on the root yields ``Path('.')``, ``backup_root / '.'``
    is ``backup_root``, and one ``shutil.move`` relocates everything."""
    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    (assets_root / "Movies" / "A Movie (2020)").mkdir(parents=True)
    (assets_root / "Movies" / "A Movie (2020)" / "poster.jpg").write_bytes(b"data")

    outcome = move_to_backup([assets_root, assets_root.parent], assets_root, backup_root)

    assert (outcome.moved, outcome.failed) == (0, 2)
    assert (assets_root / "Movies" / "A Movie (2020)" / "poster.jpg").exists()
    assert not backup_root.exists()


async def test_the_cleanup_pass_trims_the_run_history(session, tmp_path, monkeypatch):
    """The table is bounded by a clause in a pass that already runs, not
    by a fifth scheduled job nobody asked for."""
    monkeypatch.setattr(jobs, "RUN_HISTORY_KEEP", 2)
    for _ in range(3):
        await open_run(session, kind="scheduled", name="demo")
    await session.commit()

    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    kept = assets_root / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    config = _config(assets_root, backup_root, apply=True)
    job = make_cleanup_job(ConfigHolder(config))
    summary = await job.run(session)
    await session.commit()

    remaining = (await session.execute(select(Run))).scalars().all()
    assert len(remaining) == 2
    assert "trimmed 1 run history row(s)" in summary


async def test_the_run_history_is_trimmed_even_when_the_cleanup_refuses(
    session, tmp_path, monkeypatch
):
    """The trim runs FIRST, before the empty-renders refusal and before the
    orphan scan's plausibility caps. Those refusals are about the operator's
    files on an NFS mount and can hold for weeks; retention is about an
    unbounded table and must not be hostage to them."""
    monkeypatch.setattr(jobs, "RUN_HISTORY_KEEP", 2)
    for _ in range(3):
        await open_run(session, kind="scheduled", name="demo")
    await session.commit()

    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    # No renders rows at all -- the loudest refusal the pass has.
    everything = assets_root / "Movies" / "Some Movie (2020)"
    everything.mkdir(parents=True)
    (everything / "poster.jpg").write_bytes(b"data")

    config = _config(assets_root, backup_root, apply=True)
    job = make_cleanup_job(ConfigHolder(config))
    summary = await job.run(session)
    await session.commit()

    assert "refused" in summary
    assert "trimmed 1 run history row(s)" in summary
    assert len((await session.execute(select(Run))).scalars().all()) == 2


async def test_the_trim_survives_a_raise_later_in_the_pass(
    session, session_factory, tmp_path, monkeypatch
):
    """The trim commits in its OWN transaction, immediately --
    not the rest of the pass's session -- so a later raise (the orphan walk,
    the move) cannot roll it back with everything else."""
    monkeypatch.setattr(jobs, "RUN_HISTORY_KEEP", 2)
    for _ in range(3):
        await open_run(session, kind="scheduled", name="demo")
    await session.commit()

    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"
    kept = assets_root / "Movies" / "Kept Movie (2020)"
    kept.mkdir(parents=True)
    (kept / "poster.jpg").write_bytes(b"data")
    await _make_render(session, kept / "poster.jpg")

    async def _boom(*args, **kwargs):
        raise RuntimeError("NFS mount timed out")

    monkeypatch.setattr(jobs, "find_orphaned_assets", _boom)

    config = _config(assets_root, backup_root, apply=True)
    job = make_cleanup_job(ConfigHolder(config))
    with pytest.raises(RuntimeError, match="NFS mount timed out"):
        await job.run(session)

    async with session_factory() as fresh:
        remaining = (await fresh.execute(select(Run))).scalars().all()
    assert len(remaining) == 2, "the trim must survive a raise later in the same pass"
