"""The process-wide cap on heavy image work (perf workstream B4).

``workers`` (config/schema.py) is how many items are in flight at once, and
most of an item's time is spent waiting on providers and Plex. The memory is
spent elsewhere: ImageMagick composites (render/compositor.py's own note puts
a worst-case stamp at 3.2 GB in one process), the Pillow badge compose, and
the full decode of a downloaded source (render/artwork_fetch._validate_image).
Those three take a slot here first, so raising ``workers`` buys network
concurrency without multiplying peak image memory -- the production OOM
queue/jobs.py's reclaim stagger was written after.

A constant, not a setting: the operator's decision (2026-09-22).

One ``asyncio.Semaphore`` per running event loop. A semaphore created once at
import binds itself to whichever loop first makes it wait, and would then
refuse every other loop -- harmless in the one-loop production process, fatal
in a test suite that runs each test on a fresh loop. Leaf module: stdlib only,
so render/artwork_fetch.py (itself a leaf) and config/schema.py can import it.
"""
import asyncio
import contextlib
import weakref
from collections.abc import AsyncIterator

#: How many heavy image operations may run at once in this process.
RENDER_SLOTS = 2

_SEMAPHORES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


@contextlib.asynccontextmanager
async def render_slot() -> AsyncIterator[None]:
    """Hold one of ``RENDER_SLOTS`` for the body.

    Never nested: no holder awaits anything that takes a slot itself, which
    is what keeps two slots from deadlocking on each other.
    """
    loop = asyncio.get_running_loop()
    semaphore = _SEMAPHORES.get(loop)
    if semaphore is None:
        semaphore = _SEMAPHORES[loop] = asyncio.Semaphore(RENDER_SLOTS)
    async with semaphore:
        yield
