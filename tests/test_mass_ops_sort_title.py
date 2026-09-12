"""Roadmap row 268 -- franchise sort titles, under the explicit-source model.

A movie in a TMDb franchise collection gets a Plex sort title of
``"<collection name> <NN>"``, so the library's own title sort lists the
franchise in release order instead of alphabetically. Row 227's shape exactly:
naming the source gates the FETCH, ``sort_title_apply`` gates the WRITE, and
the value is a derived ``GatheredFacts`` field that is never persisted.

The last tests in this file are the gated-feature entry-point trio the
standing law requires: gate-off byte-identical, gate-on fires, second pass
steady.
"""
import logging

import httpx
import pytest

from autoposter.config.schema import OperationsConfig, OperationsOverride
from autoposter.facts.models import GatheredFacts


def test_the_two_new_keys_default_to_off():
    operations = OperationsConfig()
    assert operations.sort_title_source is None
    assert operations.sort_title_apply is False


def test_an_unknown_source_is_a_config_load_error():
    with pytest.raises(Exception):
        OperationsConfig(sort_title_source="tmdb_list")
    with pytest.raises(Exception):
        OperationsConfig(sort_title_source="tmdb")


def test_the_source_loads():
    assert (
        OperationsConfig(sort_title_source="tmdb_collection").sort_title_source
        == "tmdb_collection"
    )


def test_both_keys_are_settable_per_library():
    override = OperationsOverride(sort_title_source="tmdb_collection", sort_title_apply=True)
    assert override.sort_title_source == "tmdb_collection"
    assert override.sort_title_apply is True
    assert OperationsOverride().sort_title_source is None
    assert OperationsOverride().sort_title_apply is None


def test_sort_title_is_a_facts_field_that_counts_towards_is_empty():
    assert GatheredFacts().sort_title is None
    assert GatheredFacts().is_empty()
    assert not GatheredFacts(sort_title="Alien 01").is_empty()


def test_sort_title_is_not_persisted_as_an_item_facts_column():
    from autoposter.db.models import ItemFacts

    assert not hasattr(ItemFacts, "sort_title")


# --- the collection read -----------------------------------------------------

from autoposter.facts.tmdb_facts import (  # noqa: E402
    CollectionOrder,
    TMDBFactsClient,
    parse_collection_order,
)

ALIEN = {
    "id": 8091,
    "name": "Alien Collection",
    "parts": [
        {"id": 348, "title": "Alien", "release_date": "1979-05-25"},
        {"id": 679, "title": "Aliens", "release_date": "1986-07-18"},
        {"id": 8077, "title": "Alien³", "release_date": "1992-05-22"},
    ],
}


def test_parts_keep_the_source_s_own_order():
    """The order is the SOURCE's, verbatim -- the operator's rule. Nothing is
    re-sorted here, not even by release date: a payload listing the third
    film first is a source that says the third film is first."""
    assert parse_collection_order(ALIEN) == CollectionOrder(
        name="Alien Collection", parts=[348, 679, 8077]
    )
    shuffled = {
        "id": 1,
        "name": "X Collection",
        "parts": [
            {"id": 3, "release_date": "1992-01-01"},
            {"id": 1, "release_date": "1979-01-01"},
            {"id": 2, "release_date": ""},
        ],
    }
    assert parse_collection_order(shuffled).parts == [3, 1, 2]


def test_a_payload_with_no_parts_array_is_no_order():
    assert parse_collection_order({"id": 1, "name": "X"}) is None
    assert parse_collection_order({"id": 1, "name": "X", "parts": "nope"}) is None


def test_a_part_with_no_usable_id_is_skipped():
    payload = {"id": 1, "name": "X", "parts": [{"id": "abc"}, {"title": "no id"}, {"id": 5}]}
    assert parse_collection_order(payload).parts == [5]


async def test_the_client_reads_the_collection_endpoint():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=ALIEN)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        order = await TMDBFactsClient("tok", http).collection_order(8091)

    assert order == CollectionOrder(name="Alien Collection", parts=[348, 679, 8077])
    assert calls == ["https://api.themoviedb.org/3/collection/8091"]


async def test_an_unknown_collection_is_no_order():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))
    ) as http:
        assert await TMDBFactsClient("tok", http).collection_order(1) is None


# --- the sort title itself ---------------------------------------------------

from autoposter.facts.franchise_sort import franchise_sort_title  # noqa: E402


def test_the_position_is_one_based_and_zero_padded():
    order = CollectionOrder(name="Alien Collection", parts=[348, 679, 8077])
    assert franchise_sort_title(order, 348) == "Alien 01"
    assert franchise_sort_title(order, 679) == "Alien 02"
    assert franchise_sort_title(order, 8077) == "Alien 03"


def test_a_leading_article_is_dropped_the_way_plex_drops_it():
    """Plex files "The Lord of the Rings" under L by stripping the article
    itself. A locked sort title is taken verbatim, so the article has to be
    stripped here or the whole franchise moves from L to T."""
    order = CollectionOrder(name="The Lord of the Rings Collection", parts=[120, 121, 122])
    assert franchise_sort_title(order, 121) == "Lord of the Rings 02"
    assert franchise_sort_title(CollectionOrder("A Quiet Place Collection", [1]), 1) == "Quiet Place 01"
    assert franchise_sort_title(CollectionOrder("An Education Collection", [1]), 1) == "Education 01"


def test_the_padding_grows_with_the_franchise():
    order = CollectionOrder(name="Big Collection", parts=list(range(1, 101)))
    assert franchise_sort_title(order, 1) == "Big 001"
    assert franchise_sort_title(order, 100) == "Big 100"


def test_a_movie_absent_from_its_own_collection_gets_no_sort_title():
    order = CollectionOrder(name="Alien Collection", parts=[348, 679])
    assert franchise_sort_title(order, 8077) is None
    assert franchise_sort_title(None, 348) is None


def test_a_name_that_is_only_the_suffix_is_kept_rather_than_blanked():
    assert franchise_sort_title(CollectionOrder("Collection", [1]), 1) == "Collection 01"
    assert franchise_sort_title(CollectionOrder("", [1]), 1) is None


# --- the explicit-source model at the gather seam ---------------------------

import pytest_asyncio  # noqa: E402,F401  (imported for the plugin's fixtures)

from autoposter.facts import gather as _gather_module  # noqa: E402
from autoposter.facts.gather import gather_facts  # noqa: E402
from autoposter.facts.mdblist import NullMDBListClient  # noqa: E402

from test_mass_ops_fields import FakeTMDB, _item  # noqa: E402

IN_ALIEN = GatheredFacts(tmdb_collection_id=8091, sources={"tmdb_collection_id": "tmdb"})


class FranchiseTMDB(FakeTMDB):
    """``FakeTMDB`` plus the one read this row adds. Records every call so a
    test can assert that the unset source makes none."""

    def __init__(self, facts=None, order=None):
        super().__init__(facts)
        self._order = order
        self.collection_calls = []

    async def collection_order(self, collection_id):
        self.collection_calls.append(collection_id)
        return self._order


@pytest.fixture(autouse=True)
def _reset_the_non_movie_latch():
    _gather_module._sort_title_non_movie_warned = False
    yield
    _gather_module._sort_title_non_movie_warned = False


@pytest.mark.asyncio
async def test_no_source_named_makes_no_request_and_gathers_nothing(session):
    tmdb = FranchiseTMDB(IN_ALIEN, order=parse_collection_order(ALIEN))
    facts = await gather_facts(session, _item(tmdb_id=348), tmdb, NullMDBListClient())
    assert tmdb.collection_calls == []
    assert facts.sort_title is None
    assert "sort_title" not in facts.sources


@pytest.mark.asyncio
async def test_a_named_source_reads_the_collection_and_records_provenance(session):
    tmdb = FranchiseTMDB(IN_ALIEN, order=parse_collection_order(ALIEN))
    operations = OperationsConfig(sort_title_source="tmdb_collection")
    facts = await gather_facts(
        session, _item(tmdb_id=679), tmdb, NullMDBListClient(), operations=operations
    )
    assert tmdb.collection_calls == [8091]
    assert facts.sort_title == "Alien 02"
    assert facts.sources["sort_title"] == "tmdb_collection"
    assert facts.tmdb_collection_id == 8091, "the provider's own facts are kept"


@pytest.mark.asyncio
async def test_a_movie_in_no_franchise_makes_no_request(session):
    tmdb = FranchiseTMDB(GatheredFacts(studio="Warner"), order=parse_collection_order(ALIEN))
    operations = OperationsConfig(sort_title_source="tmdb_collection")
    facts = await gather_facts(
        session, _item(), tmdb, NullMDBListClient(), operations=operations
    )
    assert tmdb.collection_calls == []
    assert facts.sort_title is None
    assert facts.studio == "Warner"


@pytest.mark.asyncio
async def test_a_collection_tmdb_no_longer_knows_yields_nothing(session):
    tmdb = FranchiseTMDB(IN_ALIEN, order=None)
    operations = OperationsConfig(sort_title_source="tmdb_collection")
    facts = await gather_facts(
        session, _item(tmdb_id=348), tmdb, NullMDBListClient(), operations=operations
    )
    assert facts.sort_title is None
    assert "sort_title" not in facts.sources


@pytest.mark.asyncio
async def test_a_show_makes_no_request_and_says_so_exactly_once(session, caplog):
    tmdb = FranchiseTMDB(IN_ALIEN, order=parse_collection_order(ALIEN))
    operations = OperationsConfig(sort_title_source="tmdb_collection")

    with caplog.at_level(logging.WARNING, logger="autoposter.facts.gather"):
        first = await gather_facts(
            session, _item(kind="show", tvdb_id=None), tmdb, NullMDBListClient(),
            operations=operations,
        )
        await gather_facts(
            session, _item(kind="show", tvdb_id=None), tmdb, NullMDBListClient(),
            operations=operations,
        )

    assert tmdb.collection_calls == []
    assert first.sort_title is None
    lines = [r.getMessage() for r in caplog.records if "sort_title_source" in r.getMessage()]
    assert lines == [
        "operations.sort_title_source names a TMDb franchise collection, which "
        "exists for movies only; it is ignored on every non-movie item"
    ]


@pytest.mark.asyncio
async def test_a_spent_tmdb_budget_does_not_throw_away_the_rest_of_the_pass(session):
    from autoposter.facts.tmdb_budget import TmdbRateLimited

    class RefusingTMDB(FranchiseTMDB):
        async def collection_order(self, collection_id):
            raise TmdbRateLimited("window open")

    operations = OperationsConfig(sort_title_source="tmdb_collection")
    facts = await gather_facts(
        session, _item(tmdb_id=348),
        RefusingTMDB(GatheredFacts(studio="Warner", tmdb_collection_id=8091)),
        NullMDBListClient(), operations=operations,
    )
    assert facts.sort_title is None
    assert facts.studio == "Warner"


@pytest.mark.asyncio
async def test_a_transient_tmdb_failure_costs_only_this_value(session, caplog):
    class FailingTMDB(FranchiseTMDB):
        async def collection_order(self, collection_id):
            raise httpx.ConnectError("boom", request=httpx.Request("GET", "https://x/y"))

    operations = OperationsConfig(sort_title_source="tmdb_collection")
    with caplog.at_level(logging.WARNING, logger="autoposter.facts.gather"):
        facts = await gather_facts(
            session, _item(tmdb_id=348),
            FailingTMDB(GatheredFacts(studio="Warner", tmdb_collection_id=8091)),
            NullMDBListClient(), operations=operations,
        )
    assert facts.sort_title is None
    assert facts.studio == "Warner"
    message = next(r.getMessage() for r in caplog.records if "sort_title" in r.getMessage())
    assert "https://" not in message, "class name only, never the URL"


# --- the write ---------------------------------------------------------------

from autoposter.plex.writer import plan_edits  # noqa: E402

from test_mass_ops_fields import FakeItem  # noqa: E402


def test_a_differing_sort_title_is_written_and_locked():
    item = FakeItem(titleSort="Alien")
    edits = plan_edits(
        item, GatheredFacts(sort_title="Alien 01"), OperationsConfig(sort_title_apply=True)
    )
    assert edits == {"titleSort.value": "Alien 01", "titleSort.locked": 1}


def test_an_equal_sort_title_writes_nothing():
    item = FakeItem(titleSort="Alien 01")
    edits = plan_edits(
        item, GatheredFacts(sort_title="Alien 01"), OperationsConfig(sort_title_apply=True)
    )
    assert edits == {}


def test_no_gathered_sort_title_writes_nothing():
    item = FakeItem(titleSort="Alien")
    assert plan_edits(item, GatheredFacts(), OperationsConfig(sort_title_apply=True)) == {}


def test_apply_off_writes_nothing_and_logs_the_item_and_the_field_only(caplog):
    item = FakeItem(titleSort="Alien", title="Alien", year=1979)
    with caplog.at_level(logging.INFO, logger="autoposter.plex.writer"):
        edits = plan_edits(
            item, GatheredFacts(sort_title="Alien 01"),
            OperationsConfig(sort_title_source="tmdb_collection"),
        )
    assert edits == {}
    message = next(m for m in (r.getMessage() for r in caplog.records) if "sort_title" in m)
    assert message == (
        "plex: would set sort_title on movie 'Alien' (1979) "
        "(operations.sort_title_apply is off)"
    )
    assert "01" not in message


def test_a_per_item_override_on_sort_title_wins():
    """Row 99's precedence: an override IS the field's source, so the
    franchise value drops out for that item."""
    item = FakeItem(titleSort="Alien")
    edits = plan_edits(
        item, GatheredFacts(sort_title="Alien 01"), OperationsConfig(sort_title_apply=True),
        overrides={"sort_title": "Mine"},
    )
    assert edits == {"titleSort.value": "Mine", "titleSort.locked": 1}


def test_a_field_verb_on_sort_title_wins():
    """Row 87's precedence: a verbed field drops out of the value path."""
    from test_mass_ops_verbs import LockableItem

    item = LockableItem(titleSort="Alien", locks=[("titleSort", False)])
    operations = OperationsConfig(
        sort_title_apply=True, field_verbs={"sort_title": "lock"}, lock_apply=True
    )
    edits = plan_edits(item, GatheredFacts(sort_title="Alien 01"), operations)
    assert edits == {"titleSort.locked": 1}


def test_a_show_is_never_written_even_with_both_gates_on():
    """Belt and braces below the gather's own guard: a show carries no
    franchise, but a hand-built facts object must not reach Plex either."""
    from autoposter.plex.writer import WRITABLE_BY_KIND

    assert "sort_title" in WRITABLE_BY_KIND["show"], "the override path keeps it writable"
    item = FakeItem(kind="show", titleSort="Dark")
    edits = plan_edits(
        item, GatheredFacts(sort_title="Dark 01"),
        OperationsConfig(sort_title_source="tmdb_collection", sort_title_apply=True),
    )
    assert edits == {}


# --- the gated-feature entry-point tests ------------------------------------

from pathlib import Path  # noqa: E402

import pytest_asyncio as _pytest_asyncio  # noqa: E402,F401

from autoposter.config.loader import load_config  # noqa: E402
from autoposter.render.pipeline import apply_metadata  # noqa: E402

from conftest import seed_media_item  # noqa: E402
from test_mass_ops_verbs import FakeField, RecordingPlexItem, RecordingServer  # noqa: E402

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


@_pytest_asyncio.fixture
async def media_item_id(session):
    media = await seed_media_item(session, "1", library="Movies", kind="movie", title="Aliens")
    return media.id


@pytest.mark.asyncio
async def test_entry_point_gate_off_is_byte_identical(session, media_item_id, config):
    """(a) GATE OFF: no source named. The studio fact equals Plex's own value,
    so ``plan_edits`` is reached and it is the unset source that keeps the
    sort title out."""
    plex_item = RecordingPlexItem(studio="Fox", titleSort="Aliens", locks=[])
    tmdb = FranchiseTMDB(
        GatheredFacts(studio="Fox", tmdb_collection_id=8091),
        order=parse_collection_order(ALIEN),
    )
    await apply_metadata(
        session, config, media_item_id, _item(tmdb_id=679), RecordingServer(plex_item), tmdb,
        NullMDBListClient(),
    )
    assert plex_item.edits == []
    assert tmdb.collection_calls == []


@pytest.mark.asyncio
async def test_entry_point_source_named_but_apply_off_still_writes_nothing(
    session, media_item_id, config
):
    config.operations.sort_title_source = "tmdb_collection"
    plex_item = RecordingPlexItem(studio="Fox", titleSort="Aliens", locks=[])
    tmdb = FranchiseTMDB(
        GatheredFacts(tmdb_collection_id=8091), order=parse_collection_order(ALIEN)
    )
    await apply_metadata(
        session, config, media_item_id, _item(tmdb_id=679), RecordingServer(plex_item), tmdb,
        NullMDBListClient(),
    )
    assert plex_item.edits == []
    assert tmdb.collection_calls == [8091], "the fetch is armed, the write is not"


@pytest.mark.asyncio
async def test_entry_point_gate_on_fires_and_the_second_pass_is_steady(
    session, media_item_id, config
):
    config.operations.sort_title_source = "tmdb_collection"
    config.operations.sort_title_apply = True
    plex_item = RecordingPlexItem(studio="Fox", titleSort="Aliens", locks=[])
    tmdb = FranchiseTMDB(
        GatheredFacts(tmdb_collection_id=8091), order=parse_collection_order(ALIEN)
    )

    # (b) GATE ON: the write reaches Plex through the real seam, as one value
    # plus one lock, inside one batched edit.
    await apply_metadata(
        session, config, media_item_id, _item(tmdb_id=679), RecordingServer(plex_item), tmdb,
        NullMDBListClient(),
    )
    assert plex_item.edits == [{"titleSort.value": "Alien 02", "titleSort.locked": 1}]
    assert plex_item.saved == 1

    # (c) SECOND PASS: Plex now reports the value we wrote, locked, so there is
    # nothing to do.
    plex_item.titleSort = "Alien 02"
    plex_item.fields = [FakeField("titleSort", True)]
    await apply_metadata(
        session, config, media_item_id, _item(tmdb_id=679), RecordingServer(plex_item), tmdb,
        NullMDBListClient(),
    )
    assert len(plex_item.edits) == 1
    assert plex_item.saved == 1
