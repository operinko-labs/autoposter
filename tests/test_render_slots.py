"""Perf workstream B4: heavy image work is capped at RENDER_SLOTS at once.

``workers`` now defaults to 10 for network and Plex concurrency, and the
memory-heavy steps -- ``compose_styled``'s magick calls, the badge compose
thread, ``_download``'s full decode -- take one of ``RENDER_SLOTS`` first, so
the pod's peak image memory no longer scales with the worker count. Overlap is
measured with counters under a lock, never with timestamps (the dev Docker
clock steps backwards).
"""
import asyncio
import contextlib
import threading
import time
from pathlib import Path

import httpx
from conftest import decodable_png

from autoposter.config.loader import load_config
from autoposter.config.schema import Config
from autoposter.render import artwork_fetch
from autoposter.render import pipeline as pipeline_module
from autoposter.render.slots import RENDER_SLOTS, render_slot
from autoposter.render.textfit import FitResult
from test_badge_pipeline import REF, Facts, FakePlexItem, FakeServer, _render

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


class _Overlap:
    """Counts how many callers are inside at once, from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self.now = 0
        self.peak = 0

    def enter(self):
        with self._lock:
            self.now += 1
            self.peak = max(self.peak, self.now)

    def leave(self):
        with self._lock:
            self.now -= 1


async def _contend(overlap: _Overlap, tasks: int) -> None:
    async def one():
        async with render_slot():
            overlap.enter()
            await asyncio.to_thread(time.sleep, 0.05)
            overlap.leave()

    await asyncio.gather(*(one() for _ in range(tasks)))


async def test_the_slots_never_admit_more_than_render_slots():
    overlap = _Overlap()
    await _contend(overlap, RENDER_SLOTS * 3)
    assert overlap.peak == RENDER_SLOTS


def test_the_cap_holds_on_a_second_event_loop():
    """One semaphore per running loop: a semaphore bound to a finished loop
    would raise on the next loop's contention (every pytest-asyncio test runs
    on its own loop)."""
    for _ in range(2):
        overlap = _Overlap()
        asyncio.run(_contend(overlap, RENDER_SLOTS * 2))
        assert overlap.peak == RENDER_SLOTS


async def test_compose_styled_runs_at_most_render_slots_at_once(tmp_path, monkeypatch):
    config = load_config(EXAMPLE)
    config.overlays_root = tmp_path / "overlays"
    config.fonts_root = tmp_path / "fonts"
    overlap = _Overlap()

    def fake_run(argv):
        overlap.enter()
        time.sleep(0.05)
        overlap.leave()

    monkeypatch.setattr(pipeline_module.compositor, "run", fake_run)
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    workings = []
    for index in range(RENDER_SLOTS * 3):
        working = tmp_path / f"w{index}.jpg"
        working.write_bytes(decodable_png())
        workings.append(working)

    await asyncio.gather(*(
        pipeline_module.compose_styled(
            config, "background", working,
            primary_text=None, secondary_text=None, draw_text=False,
        )
        for working in workings
    ))

    assert overlap.peak == RENDER_SLOTS


def _spy_slot(monkeypatch, module):
    state = {"held": 0}

    @contextlib.asynccontextmanager
    async def spy():
        state["held"] += 1
        try:
            yield
        finally:
            state["held"] -= 1

    monkeypatch.setattr(module, "render_slot", spy)
    return state


async def test_the_badge_compose_thread_runs_inside_a_slot(
    session, config_with_badges, monkeypatch
):
    state = _spy_slot(monkeypatch, pipeline_module)
    held_during_compose = []

    def fake_compose(*args, **kwargs):
        held_during_compose.append(state["held"])
        return b"badged"

    monkeypatch.setattr(pipeline_module, "compose_badges", fake_compose)
    item, render = await _render(session)

    data = await pipeline_module.compose_badged_bytes(
        session, config_with_badges, render, item,
        server=FakeServer(FakePlexItem()), ref=REF, facts=Facts(),
    )

    assert data == b"badged"
    assert held_during_compose == [1]


async def test_a_downloads_full_decode_runs_inside_a_slot(tmp_path, monkeypatch):
    state = _spy_slot(monkeypatch, artwork_fetch)
    held_during_decode = []

    def fake_validate(path, stage):
        held_during_decode.append(state["held"])

    monkeypatch.setattr(artwork_fetch, "_validate_image", fake_validate)

    async def handler(request):
        return httpx.Response(200, content=decodable_png())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await artwork_fetch._download(
            http, "https://img/x.png", tmp_path / "x.png", stage="the poster source",
        )

    assert held_during_decode == [1]


def test_the_workers_default_is_ten_and_its_description_names_the_render_cap():
    field = Config.model_fields["workers"]
    assert field.default == 10
    assert f"at most {RENDER_SLOTS} at a time" in field.description
