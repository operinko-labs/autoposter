"""Plex doubles for the event-loop offload tests (perf workstream C).

Every Plex touch these doubles expose goes into a shared ``CallLog`` with the
thread it ran on, and each can block with ``time.sleep`` to stand in for a slow
server. That covers the methods AND the attribute reads plexapi can turn into a
synchronous ``reload()`` (``guids``, ``labels``, ``smart``, ``subtype``,
``summary``, ``titleSort``, ``collectionMode``, ``fields``). ``title`` and
``ratingKey`` stay plain attributes: plexapi loads both with the object, and
the engine reads them on the loop by design (``resolve.py``'s identity rule,
``lists._members_hash``).

The tests assert THREADS, not time. A touch recorded on the event loop's own
thread is a stall however fast the double answered, and a thread check cannot
flake on a loaded runner. ``max_loop_lag`` is the one timing probe. It runs
once per phase group against a double that really blocks, and prints the
number the lane records in ``.superpowers/sdd/perf/pcs-measure.md``.

Imported by bare module name (``from plex_offload_doubles import ...``), the
way every sibling helper in tests/ is.
"""
import asyncio
import threading
import time
from types import SimpleNamespace


class CallLog:
    """``(what, thread ident)`` for every recorded Plex touch, in call order."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, int]] = []
        self._lock = threading.Lock()

    def record(self, what: str, delay: float = 0.0) -> None:
        with self._lock:
            self.entries.append((what, threading.get_ident()))
        if delay:
            time.sleep(delay)

    def names(self) -> list[str]:
        with self._lock:
            return [what for what, _ in self.entries]

    def on_thread(self, ident: int) -> list[str]:
        """Every touch that ran on thread ``ident``. In a test, pass the event
        loop's thread (``threading.get_ident()`` inside the test coroutine)."""
        with self._lock:
            return [what for what, seen in self.entries if seen == ident]


def _tags(names) -> list:
    return [SimpleNamespace(tag=name) for name in names]


class BlockingServer:
    """``PlexServer``'s surface the collections pass reaches: ``library``
    (a property, as in plexapi), ``_uriRoot``, ``query`` and ``_session``."""

    def __init__(self, log: CallLog, delay: float = 0.0) -> None:
        self._log = log
        self._delay = delay
        self._sections: dict[str, object] = {}
        self._session = SimpleNamespace(post="POST", put="PUT")
        # ``smart.count_matches`` reads the container's ``totalSize``.
        self.total_size = "3"

    @property
    def library(self):
        return self

    def add_section(self, name: str, section) -> None:
        self._sections[name] = section

    def section(self, name: str):
        self._log.record("server.section", self._delay)
        return self._sections[name]

    def _uriRoot(self) -> str:
        return "server://machine/com.plexapp.plugins.library"

    def query(self, key, method=None, headers=None, **kwargs):
        self._log.record("server.query", self._delay)
        return SimpleNamespace(attrib={"totalSize": self.total_size})


class BlockingItem:
    """A library item. ``guids`` is the read plexapi reloads for an unmatched
    item (``adopt/walk.py:210-214``), so it is recorded like a request."""

    def __init__(self, log: CallLog, key: str, guids=(), delay: float = 0.0) -> None:
        self._log = log
        self._delay = delay
        self.ratingKey = key
        self.title = key
        self._guids = [SimpleNamespace(id=guid) for guid in guids]
        # Read by ``plex.client.fetch_tag_index`` through ``object.__getattribute__``.
        self.genres: list = []
        self.labels_added: list[str] = []

    @property
    def guids(self):
        self._log.record("item %s.guids" % self.ratingKey, self._delay)
        return list(self._guids)

    def addLabel(self, label, locked=True):
        self._log.record("item %s.addLabel" % self.ratingKey, self._delay)
        self.labels_added.append(label)


class BlockingCollection:
    """A Plex collection whose lazy attributes and every method are recorded."""

    def __init__(self, log: CallLog, server: BlockingServer, title: str, *,
                 items=(), labels=(), smart: bool = False, subtype: str = "movie",
                 delay: float = 0.0) -> None:
        self._log = log
        self._delay = delay
        self._server = server
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._snapshot = list(items)
        self._labels = _tags(labels)
        self._smart = smart
        self._subtype = subtype
        self._summary = None
        self._title_sort = None
        self.deleted = False

    def _touch(self, what: str) -> None:
        self._log.record("collection %r.%s" % (self.title, what), self._delay)

    @property
    def labels(self):
        self._touch("labels")
        return list(self._labels)

    @property
    def smart(self):
        self._touch("smart")
        return self._smart

    @property
    def subtype(self):
        self._touch("subtype")
        return self._subtype

    @property
    def summary(self):
        self._touch("summary")
        return self._summary

    @property
    def titleSort(self):
        self._touch("titleSort")
        return self._title_sort

    @property
    def collectionMode(self):
        self._touch("collectionMode")
        return -1

    @property
    def fields(self):
        self._touch("fields")
        return []

    def reload(self, **kwargs):
        # plexapi's ``items()`` is a cached property only ``reload()``
        # invalidates -- ``lists._enforce_order``'s docstring -- so the
        # snapshot moves only here.
        self._touch("reload")
        self._snapshot = list(self._live)

    def items(self):
        self._touch("items")
        return list(self._snapshot)

    def addItems(self, items):
        self._touch("addItems")
        self._live.extend(items)

    def removeItems(self, items):
        self._touch("removeItems")
        gone = {item.ratingKey for item in items}
        self._live = [item for item in self._live if item.ratingKey not in gone]

    def moveItem(self, item, after=None):
        self._touch("moveItem")
        self._live = [one for one in self._live if one.ratingKey != item.ratingKey]
        position = 0 if after is None else (
            [one.ratingKey for one in self._live].index(after.ratingKey) + 1
        )
        self._live.insert(position, item)

    def addLabel(self, label, locked=True):
        self._touch("addLabel")
        self._labels.append(SimpleNamespace(tag=label))

    def removeLabel(self, label, locked=True):
        self._touch("removeLabel")
        self._labels = [tag for tag in self._labels if tag.tag != label]

    def sortUpdate(self, sort=None):
        self._touch("sortUpdate")

    def editSortTitle(self, sortTitle, locked=True):
        self._touch("editSortTitle")
        self._title_sort = sortTitle

    def modeUpdate(self, mode=None):
        self._touch("modeUpdate")

    def delete(self):
        self._touch("delete")
        self.deleted = True


class BlockingSection:
    """A ``LibrarySection``. ``all_delay`` blocks only ``all()`` -- the walk the
    spec's loop probe is written against."""

    def __init__(self, log: CallLog, server: BlockingServer, *, items=(), choices=(),
                 section_type: str = "movie", key: str = "1", delay: float = 0.0,
                 all_delay: float = 0.0) -> None:
        self._log = log
        self._server = server
        self._items = list(items)
        self._collections: dict[str, BlockingCollection] = {}
        # ``(key, title)`` pairs answered for EVERY field: the tests name one
        # vocabulary per section, and the field lookup is not what they test.
        self._choices = list(choices)
        self.type = section_type
        self.key = key
        self._delay = delay
        self._all_delay = all_delay

    def add(self, collection: BlockingCollection) -> BlockingCollection:
        self._collections[collection.title] = collection
        return collection

    def _touch(self, what: str, delay: float | None = None) -> None:
        self._log.record("section." + what, self._delay if delay is None else delay)

    def all(self, **kwargs):
        self._touch("all", self._all_delay or self._delay)
        return list(self._items)

    def search(self, libtype=None, **kwargs):
        self._touch("search")
        return list(self._items)

    def collections(self, label=None, **kwargs):
        self._touch("collections")
        found = list(self._collections.values())
        if label is None:
            return found
        # Server-side and case-insensitive, like Plex's own label filter.
        wanted = label.casefold()
        return [c for c in found if any(tag.tag.casefold() == wanted for tag in c._labels)]

    def collection(self, title: str):
        self._touch("collection")
        if title not in self._collections:
            self._collections[title] = BlockingCollection(
                self._log, self._server, title, subtype=self.type
            )
        return self._collections[title]

    def createCollection(self, title, items=None, smart=False, **kwargs):
        self._touch("createCollection")
        return self.add(BlockingCollection(self._log, self._server, title, items=items or []))

    def fetchItems(self, ekey, **kwargs):
        self._touch("fetchItems")
        if isinstance(ekey, list):
            # plexapi's list-of-ints form: the tier-2 batched metadata read.
            wanted = {int(key) for key in ekey}
            return [item for item in self._items if int(item.ratingKey) in wanted]
        return list(self._items)

    def listFilterChoices(self, field, libtype=None):
        self._touch("listFilterChoices")
        return [SimpleNamespace(key=key, title=title) for key, title in self._choices]

    def listFilters(self, libtype=None):
        self._touch("listFilters")
        return []

    def managedHubs(self):
        self._touch("managedHubs")
        return []


def returning(value):
    """An async callable answering ``value`` whatever it is asked. This is the
    shape ``PlexSectionAccess``'s index and ``SmartContext.listing`` take now
    that the engine builds both with ``asyncio.to_thread``."""

    async def answer(*args, **kwargs):
        return value

    return answer


async def max_loop_lag(work, *, tick: float = 0.05) -> tuple[object, float]:
    """Await ``work`` while a probe repeatedly sleeps ``tick`` seconds. Return
    ``(work's result, the worst lateness any probe sleep woke with)``.

    The spec's loop-responsiveness probe: a concurrent 50 ms ``asyncio.sleep``
    must not wake more than 200 ms late while the pass runs. ``perf_counter``
    is monotonic, and only an UPPER bound is ever asserted, so a clock step
    cannot manufacture a pass. One ``sleep(0)`` before ``work`` starts arms the
    probe's first sleep, so a stall at the very start of ``work`` is caught.
    """
    worst = 0.0
    done = asyncio.Event()

    async def probe() -> None:
        nonlocal worst
        while not done.is_set():
            started = time.perf_counter()
            await asyncio.sleep(tick)
            worst = max(worst, time.perf_counter() - started - tick)

    task = asyncio.create_task(probe())
    await asyncio.sleep(0)
    try:
        result = await work
    finally:
        done.set()
        await task
    return result, worst
