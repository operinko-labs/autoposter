"""One job's memo (perf workstream B3).

``process_item`` asked Plex for the same item four to six times -- the labels
read, the metadata write, the title-card frame, the badge stage's media read,
the provenance probe and the upload each did their own ``fetch_item``. The
worker now runs every job inside ``scope()``, and ``PlexClient.fetch_item``
keeps what it fetched in ``current()`` for the rest of that job, so later steps
reuse the object (``media_info_from_plex``'s ``reload()`` warms it for all of
them). Every Plex write through ``PlexClient`` evicts the item, so a read after
a write goes back to Plex.

A ``ContextVar``, not an attribute on anything shared: each worker is its own
asyncio task with its own context, so ten workers hold ten memos and none can
see another's object. Outside a job -- API requests, scheduled passes, the
mass-ops walks -- ``current()`` is ``None`` and nothing is memoised, so a
long walk cannot accumulate thousands of plexapi objects here.
"""
import contextlib
from collections.abc import Iterator
from contextvars import ContextVar

_CURRENT: ContextVar[dict | None] = ContextVar("autoposter_job_memo", default=None)


def current() -> dict | None:
    """The running job's memo, or ``None`` outside a job."""
    return _CURRENT.get()


@contextlib.contextmanager
def scope() -> Iterator[dict]:
    """A fresh, empty memo for the body; the previous one (normally none) after."""
    memo: dict = {}
    token = _CURRENT.set(memo)
    try:
        yield memo
    finally:
        _CURRENT.reset(token)
