"""The ``python -m autoposter.adopt`` entry point's Plex access.

``server.library`` is an HTTP-fetching property on a real ``PlexServer``, so
``asyncio.to_thread(server.library.section, name)`` evaluates the blocking part
*before* handing anything to the thread. On a two-library cutover that is only
two GETs, but it is the same class of defect the walk itself had, and the
event loop is shared with the Plex liveness probe and every worker.
"""
import threading

from autoposter.adopt.__main__ import fetch_section


class _RecordingServer:
    """``library`` is a property, as on a real ``PlexServer`` -- reading it records
    the thread that did so."""

    def __init__(self, reads):
        self._reads = reads

    @property
    def library(self):
        self._reads.append(threading.current_thread())
        return self

    def section(self, name):
        self._reads.append(threading.current_thread())
        return f"section:{name}"


async def test_the_library_property_is_evaluated_inside_the_worker_thread():
    reads = []
    server = _RecordingServer(reads)

    result = await fetch_section(server, "Movies")

    assert result == "section:Movies"
    assert len(reads) == 2  # the `library` property and the `section` call
    assert all(t is not threading.current_thread() for t in reads)
