"""The credits cache: scan, attempt stamp, missing-value rule, counted enumeration.

The fixtures and the async convention are ``tests/test_collection_facts_enumeration``'s
-- the same real-Postgres ``session``, the same tiny seed helper -- because this
module is that module's twin over a different table: a dedicated composite-PK
mapping, and the same missing-value rule read over it.

Nothing here asserts a COMPLETE cast, and nothing may: the phase-B probe
measured a server-side cap of 200 ``Role`` children per item
(docs/research/plex-batch-probe/README.md, D1 -- 54 of 200 shows hit it
exactly), so a scanned item's actor rows are what Plex returned, not
necessarily everyone who appeared in it.
"""
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from autoposter.collections.credits import (
    CREDIT_KINDS,
    credits_coverage,
    enumerate_credits,
    scan_credits,
    scan_library_credits,
)
from autoposter.db.models import ItemCredit, MediaItem, MediaItemServerRef

from conftest import seed_media_item


class FakeTag:
    def __init__(self, tag):
        self.tag = tag


class FakeItem:
    def __init__(self, rating_key, actors=(), directors=(), writers=(), producers=()):
        self.ratingKey = rating_key
        self.roles = [FakeTag(a) for a in actors]
        self.directors = [FakeTag(d) for d in directors]
        self.writers = [FakeTag(w) for w in writers]
        self.producers = [FakeTag(p) for p in producers]


class FakeSection:
    def __init__(self, key, type_, items):
        self.key = key
        self.type = type_
        self._items = {int(i.ratingKey): i for i in items}
        self.calls = []

    def fetchItems(self, ekey):
        self.calls.append(list(ekey))
        return [self._items[k] for k in ekey if k in self._items]


class FakeLibrary:
    def __init__(self, sections):
        self._sections = {s_name: s for s_name, s in sections.items()}

    def section(self, name):
        return self._sections[name]


class FakeServer:
    def __init__(self, sections):
        self.library = FakeLibrary(sections)


@pytest.fixture
def config():
    """Only ``collections.libraries`` is read by the scan, so only it is built
    -- the ``SimpleNamespace`` stand-in ``tests/test_scheduler_drift_job`` uses
    for the same reason."""
    return SimpleNamespace(collections=SimpleNamespace(libraries=["Movies"]))


async def _item(session, rating_key, *, library="Movies", kind="movie", title="X"):
    # Committed, not only flushed: ``scan_library_credits`` ends its read
    # transaction before the threaded Plex fetch (perf C2), and that rollback
    # would take a flushed-only seed with it.
    item = await seed_media_item(session, rating_key, library=library, kind=kind, title=title)
    await session.commit()
    return item


def _movies(*items) -> FakeServer:
    return FakeServer({"Movies": FakeSection("10", "movie", list(items))})


async def _rows(session, item_id) -> list[tuple[str, str]]:
    result = await session.execute(
        select(ItemCredit.kind, ItemCredit.person)
        .where(ItemCredit.item_id == item_id)
        .order_by(ItemCredit.kind, ItemCredit.person)
    )
    return [(kind, person) for kind, person in result.all()]


async def _stamps(session) -> dict[str, object]:
    result = await session.execute(
        select(MediaItemServerRef.native_id, MediaItem.credits_attempted_at)
        .join(MediaItem, MediaItem.id == MediaItemServerRef.item_id)
        .where(MediaItemServerRef.server == "plex")
    )
    return {key: stamp for key, stamp in result.all()}


async def test_scan_writes_rows_and_stamps_the_attempt(session, config):
    """Found-no-credits IS an answer: both items carry the stamp, and only the
    one Plex credited carries rows."""
    # Ids read before the scan: its rollback (perf C2) expires every loaded
    # ORM object, and an expired ``.id`` would lazy-load outside the greenlet.
    credited_id = (await _item(session, "1", title="Credited")).id
    bare_id = (await _item(session, "2", title="Bare")).id
    server = _movies(
        FakeItem(1, actors=("Ann", "Bob"), directors=("Dee",),
                 writers=("Wes",), producers=("Pat",)),
        FakeItem(2),
    )

    summary = await scan_credits(session, server, config)

    assert summary == "Movies: 2 item(s) scanned, 5 credit(s)"
    stamps = await _stamps(session)
    assert stamps["1"] is not None
    assert stamps["2"] is not None
    assert await _rows(session, credited_id) == [
        ("actor", "Ann"),
        ("actor", "Bob"),
        ("director", "Dee"),
        ("producer", "Pat"),
        ("writer", "Wes"),
    ]
    assert await _rows(session, bare_id) == []


async def test_an_unanswered_key_is_neither_stamped_nor_written(session, config):
    """A refusal never enters the cache. The section answers for two of the
    three seeded items; the third keeps a NULL stamp and no rows, so it stays
    honestly unvisited rather than looking like "we looked and found none"."""
    await _item(session, "1")
    await _item(session, "2")
    silent_id = (await _item(session, "3", title="Not Answered")).id
    server = _movies(FakeItem(1, actors=("Ann",)), FakeItem(2, actors=("Bob",)))

    summary = await scan_credits(session, server, config)

    assert summary == "Movies: 2 item(s) scanned, 2 credit(s)"
    stamps = await _stamps(session)
    assert stamps["1"] is not None
    assert stamps["2"] is not None
    assert stamps["3"] is None
    assert await _rows(session, silent_id) == []


async def test_rescan_replaces_an_items_rows(session, config):
    """Stale credits do not accumulate: a person Plex no longer credits is
    gone from the cache on the next pass, not merged with the new answer."""
    item_id = (await _item(session, "1")).id

    await scan_credits(session, _movies(FakeItem(1, actors=("A", "B"))), config)
    assert await _rows(session, item_id) == [("actor", "A"), ("actor", "B")]

    await scan_credits(session, _movies(FakeItem(1, actors=("A",))), config)

    assert await _rows(session, item_id) == [("actor", "A")]


async def test_two_plex_refs_on_one_item_cannot_double_insert_a_credit(session, config):
    """Spec §4.1 makes one ref per (item, server) an invariant, so this shape
    should not exist -- but ``by_key`` is inverted from (item_id, native_id)
    pairs, so if it ever did, Plex answering for BOTH ids would hand the
    identical (item_id, kind, person) triple to ``insert(ItemCredit)`` twice
    and the UniqueViolation would fail the whole scan. Guarded, not assumed."""
    item_id = (await _item(session, "1")).id
    session.add(MediaItemServerRef(item_id=item_id, server="plex", native_id="2", library="Movies"))
    await session.commit()
    server = _movies(FakeItem(1, actors=("Ann",)), FakeItem(2, actors=("Ann",)))

    summary = await scan_credits(session, server, config)

    assert summary == "Movies: 1 item(s) scanned, 1 credit(s)"
    assert await _rows(session, item_id) == [("actor", "Ann")]


async def test_enumerate_credits_counts_most_first_ties_on_name(session, config):
    """The GROUP BY shape ``facts_enumeration.enumerate_values`` keeps: a
    (person, item_count) pair, most appearances first, ties on the person
    ascending so the order is total between passes. The count is over the
    items whose cast Plex RETURNED -- a cast truncated at the 200-Role cap
    undercounts everyone Plex left out."""
    await _item(session, "1")
    await _item(session, "2")
    await _item(session, "3")
    server = _movies(
        FakeItem(1, actors=("Ann", "Bob")),
        FakeItem(2, actors=("Ann",)),
        FakeItem(3, actors=("Cy",)),
    )
    await scan_credits(session, server, config)

    actors = await enumerate_credits(
        session, "actor", library="Movies", library_type="Movie"
    )
    directors = await enumerate_credits(
        session, "director", library="Movies", library_type="Movie"
    )

    assert actors == [("Ann", 2), ("Bob", 1), ("Cy", 1)]
    assert directors == []


async def test_no_row_means_absent_never_a_bucket(session, config):
    """The missing-value rule, verbatim from ``facts_enumeration``: an item
    with a stamped attempt and zero rows is ABSENT from every kind's
    enumeration. Never an empty-string bucket, never a zero, never an error --
    and it still counts as VISITED, which is the honest half of the story."""
    await _item(session, "1")
    await _item(session, "2", title="Credited Nobody")
    server = _movies(FakeItem(1, actors=("Ann",)), FakeItem(2))
    await scan_credits(session, server, config)

    per_kind = {
        kind: await enumerate_credits(
            session, kind, library="Movies", library_type="Movie"
        )
        for kind in CREDIT_KINDS
    }

    assert per_kind == {
        "actor": [("Ann", 1)],
        "director": [],
        "writer": [],
        "producer": [],
    }
    assert await credits_coverage(session, library="Movies", library_type="Movie") == (2, 2)


async def test_coverage_counts_attempts_against_library_size(session, config):
    """Attempts against library size. The item the batch did not answer for is
    what keeps the number honest -- it is not counted as visited."""
    await _item(session, "1")
    await _item(session, "2")
    await _item(session, "3")
    server = _movies(FakeItem(1, actors=("Ann",)), FakeItem(2))
    await scan_credits(session, server, config)

    assert await credits_coverage(session, library="Movies", library_type="Movie") == (2, 3)


async def test_scan_call_count_is_ceil_n_over_chunk(session):
    """One batched read per chunk and no more -- the whole economics of a
    whole-library scan (``ceil(N/chunk)`` round trips, not N)."""
    for key in range(1, 6):
        await _item(session, str(key))
    section = FakeSection("10", "movie", [FakeItem(k) for k in range(1, 6)])

    stamped, written = await scan_library_credits(
        session, section, "Movies", "Movie", chunk_size=2
    )

    assert (stamped, written) == (5, 0)
    assert [len(call) for call in section.calls] == [2, 2, 1]


async def test_a_shows_credits_are_the_shows_not_its_episodes(session, config):
    """``_KINDS``' Show entry is ``("show",)``: an episode row in the same
    library is neither scanned nor enumerated. Shows also carry ACTOR credits
    only on a real server (probe D1: zero director/writer/producer in the
    batch, in a single-key fetch and in ``listFilterChoices``), so nothing here
    synthesises the three kinds Plex does not answer for."""
    show_id = (await _item(session, "1", library="TV Shows", kind="show", title="A Show")).id
    episode_id = (await _item(
        session, "2", library="TV Shows", kind="episode", title="An Episode"
    )).id
    section = FakeSection("11", "show", [FakeItem(1, actors=("Ann",)), FakeItem(2)])
    server = FakeServer({"TV Shows": section})
    config.collections.libraries = ["TV Shows"]

    await scan_credits(session, server, config)

    assert await _rows(session, show_id) == [("actor", "Ann")]
    assert await _rows(session, episode_id) == []
    assert (await _stamps(session))["2"] is None
    assert section.calls == [[1]]
    assert await enumerate_credits(
        session, "actor", library="TV Shows", library_type="Show"
    ) == [("Ann", 1)]


async def test_a_failing_library_is_reported_not_fatal(session, config):
    """``reconcile_libraries``' containment shape: one library that raises is
    logged and reported by exception class only -- the message can quote a URL
    -- while the others still scan and commit."""
    await _item(session, "1")
    # Committed rather than merely flushed: the failure handler rolls the
    # session back, exactly as ``reconcile_libraries`` does, so a seed left
    # uncommitted would vanish with the failed library's partial work.
    await session.commit()

    class Exploding:
        type = "movie"

        def fetchItems(self, ekey):
            raise RuntimeError("https://plex.example/secret")

    server = FakeServer({"Movies": Exploding(), "Other": FakeSection("12", "movie", [])})
    config.collections.libraries = ["Movies", "Other"]

    summary = await scan_credits(session, server, config)

    assert summary == "Movies: failed (RuntimeError); Other: 0 item(s) scanned, 0 credit(s)"
    assert (await _stamps(session))["1"] is None


async def test_an_unsupported_library_type_is_skipped(session, config):
    """A photo or music section has no credits to scan and is not an error."""
    server = FakeServer({"Movies": FakeSection("13", "photo", [])})

    assert await scan_credits(session, server, config) == "Movies: skipped (unsupported type)"


async def test_counts_never_claim_a_complete_cast(session, config):
    """The 200-``Role`` cap disclosure has to be readable where the counts are.
    Not a behaviour assertion about Plex -- a guard that the module and the
    table say so, so the next person to surface these numbers finds the caveat
    attached to them rather than in a probe report they have not read."""
    from autoposter.collections import credits as credits_module
    from autoposter.db.models import ItemCredit as table

    for text in (
        credits_module.__doc__,
        table.__doc__,
        credits_module.enumerate_credits.__doc__,
        credits_module.credits_coverage.__doc__,
    ):
        assert "200" in text, text


async def test_the_read_transaction_ends_before_the_threaded_fetch(session):
    """perf C2: the rows are plain tuples, so nothing needs the SELECT's
    transaction while Plex answers the batched reads; holding it idle pinned a
    pooled connection and the vacuum horizon for the whole walk."""
    await _item(session, "1")
    in_transaction = []

    class _Watching(FakeSection):
        def fetchItems(self, ekey):
            in_transaction.append(session.in_transaction())
            return super().fetchItems(ekey)

    section = _Watching("10", "movie", [FakeItem(1, actors=("Ann",))])

    assert await scan_library_credits(session, section, "Movies", "Movie") == (1, 1)
    assert in_transaction == [False]


async def test_the_server_library_is_read_off_the_event_loop(session, config):
    """perf C2: plexapi's ``PlexServer.library`` is a ``cached_data_property``
    whose first read is a request (``self.query('/library')``), so
    ``to_thread(server.library.section, name)`` made that request on the loop
    while it built the call's arguments."""
    await _item(session, "1")
    loop_thread = threading.get_ident()
    seen = []
    library = FakeLibrary({"Movies": FakeSection("10", "movie", [FakeItem(1)])})

    class _Server:
        @property
        def library(self):
            seen.append(threading.get_ident())
            return library

    assert await scan_credits(session, _Server(), config) == (
        "Movies: 1 item(s) scanned, 0 credit(s)"
    )
    assert seen and loop_thread not in seen
