import asyncio

import pytest

from autoposter.artwork_modes.base import (
    SHARE_CHECK_MIN_ITEMS,
    ModeJob,
    WorkerPause,
    refuse_if_empty,
    refuse_if_implausible,
)
from autoposter.db.models import MediaItem


# --- the plausibility cap ----------------------------------------------------


def test_a_believable_count_is_not_refused():
    assert refuse_if_implausible(3, 100, cap=500, share=0.25) is None


def test_past_the_absolute_cap_it_refuses_with_the_real_numbers():
    summary = refuse_if_implausible(600, 16000, cap=500, share=0.25)
    assert summary is not None
    # refuse-with-numbers, the cleanup precedent: both the count and the cap
    # appear so the operator can see how far off it is.
    assert "600" in summary
    assert "16000" in summary
    assert "500" in summary
    assert summary.startswith("refused:")


def test_past_the_share_cap_on_a_small_library_it_refuses_with_the_share():
    # 15 of 40 is 38% (rounded), over the 25% share cap; the sample (40) clears
    # the minimum for the share check to mean anything.
    summary = refuse_if_implausible(15, 40, cap=500, share=0.25)
    assert summary is not None
    assert "38%" in summary
    assert "25%" in summary


def test_the_share_cap_needs_a_large_enough_sample():
    # 2 of 3 is 67% but only three candidates -- a normal small operation, not
    # evidence of a misconfigured filter. Below SHARE_CHECK_MIN_ITEMS only the
    # absolute cap applies, so this must NOT refuse.
    assert 3 < SHARE_CHECK_MIN_ITEMS
    assert refuse_if_implausible(2, 3, cap=500, share=0.25) is None


def test_zero_changes_is_never_refused():
    assert refuse_if_implausible(0, 0, cap=500, share=0.25) is None


# --- the empty-table guard ---------------------------------------------------


async def test_refuse_if_empty_refuses_an_empty_table(session):
    summary = await refuse_if_empty(session, MediaItem, table_name="media_items")
    assert summary is not None
    assert summary.startswith("refused:")
    assert "media_items" in summary


async def test_refuse_if_empty_passes_once_a_row_exists(session):
    session.add(MediaItem(rating_key="1", library="Movies", kind="movie", title="Dune"))
    await session.commit()
    assert await refuse_if_empty(session, MediaItem, table_name="media_items") is None


# --- the worker-pause fence --------------------------------------------------


def test_worker_pause_starts_unpaused():
    assert WorkerPause().is_paused is False


def test_worker_pause_toggles():
    pause = WorkerPause()
    pause.pause()
    assert pause.is_paused is True
    pause.resume()
    assert pause.is_paused is False


def test_paused_context_manager_pauses_then_resumes():
    pause = WorkerPause()
    with pause.paused():
        assert pause.is_paused is True
    assert pause.is_paused is False


def test_paused_context_manager_resumes_even_on_error():
    pause = WorkerPause()
    try:
        with pause.paused():
            assert pause.is_paused is True
            raise RuntimeError("mode blew up")
    except RuntimeError:
        pass
    assert pause.is_paused is False


async def test_drain_returns_at_once_when_nothing_is_in_flight():
    """The normal case, and the case of an application with no worker pool at
    all: there is nothing to wait for, so the mode starts immediately."""
    await asyncio.wait_for(WorkerPause().drain(), timeout=1)


async def test_drain_waits_while_any_worker_is_mid_job():
    """Counted, not a flag: the pool has several workers, and the fence must
    not be treated as drained until the LAST of them leaves its handler."""
    pause = WorkerPause()
    with pause.running_job():
        assert pause.active_jobs == 1
        with pause.running_job():
            assert pause.active_jobs == 2
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(pause.drain(), timeout=0.05)
        # One of the two has left; the other is still working. A flag would
        # have cleared here and let the mode start writing.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(pause.drain(), timeout=0.05)
    assert pause.active_jobs == 0
    await asyncio.wait_for(pause.drain(), timeout=1)


async def test_a_job_that_raises_still_leaves_the_fence_drainable():
    """The count is given back in a ``finally``: a handler that blows up must
    not leave every later mode run waiting forever."""
    pause = WorkerPause()
    with pytest.raises(RuntimeError):
        with pause.running_job():
            raise RuntimeError("handler blew up")
    assert pause.active_jobs == 0
    await asyncio.wait_for(pause.drain(), timeout=1)


# --- the ModeJob contract ----------------------------------------------------


def test_mode_job_protocol_is_runtime_checkable():
    class Backup:
        async def run(self, session):
            return "dry run: 0 of 0 item(s) would change"

    class NotAMode:
        pass

    assert isinstance(Backup(), ModeJob)
    assert not isinstance(NotAMode(), ModeJob)
