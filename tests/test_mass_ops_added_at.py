"""Roadmap row 227 -- ``mass_added_at_update``, under the explicit-source model.

Kometa's transcription, which this row was blocked on and which
``.superpowers/sdd/p-decision-rows-2-recon.md`` § 1.2 found:
``tmdb_digital`` = TMDb's digital release date, ``tmdb_premiere`` = the
premiere, movie libraries only. The type codes and the all-regions ``min()``
are read out of Kometa's own ``modules/operations.py`` at ``v2.4.8``.

TWO gates, not one, and the split is row 85's: naming the source gates the
FETCH, ``added_at_apply`` gates the WRITE. The five other value-write sources
carry no apply flag -- naming them IS their arming -- and this one diverges on
purpose, because the write rewrites Plex's own "recently added" ordering for a
whole library and this service records no undo for it.

The last tests in this file are the gated-feature entry-point trio the
standing law requires: gate-off byte-identical, gate-on fires, second pass
steady.
"""
import logging
from datetime import date, datetime

import pytest

from autoposter.config.schema import OperationsConfig, OperationsOverride
from autoposter.facts.models import GatheredFacts


def test_the_two_new_keys_default_to_off():
    """The whole safety claim of this row, as one assertion: an untouched
    config makes no request and writes nothing."""
    operations = OperationsConfig()
    assert operations.added_at_source is None
    assert operations.added_at_apply is False


def test_an_unknown_source_is_a_config_load_error():
    """The imdb_search precedent (row 81), one vocabulary along: a source this
    service cannot serve is a load error, never a setting that silently never
    fires."""
    with pytest.raises(Exception):
        OperationsConfig(added_at_source="tmdb_physical")
    with pytest.raises(Exception):
        OperationsConfig(added_at_source="tmdb")


def test_both_sources_load():
    assert OperationsConfig(added_at_source="tmdb_digital").added_at_source == "tmdb_digital"
    assert OperationsConfig(added_at_source="tmdb_premiere").added_at_source == "tmdb_premiere"


def test_both_keys_are_settable_per_library():
    """Row 92's partial model. Easy to miss and load-bearing: without these two
    entries, a library block naming either key would be dropped silently and
    ``config_for_library`` would hand the render loop the global value.
    ``tests/test_library_overrides.py``'s parity assertion is the guard; this
    is the statement of intent."""
    override = OperationsOverride(added_at_source="tmdb_premiere", added_at_apply=True)
    assert override.added_at_source == "tmdb_premiere"
    assert override.added_at_apply is True
    assert OperationsOverride().added_at_source is None
    assert OperationsOverride().added_at_apply is None


def test_added_at_is_a_facts_field_that_counts_towards_is_empty():
    """C3: a mass-op WRITE value, not a badge input. It lives on
    ``GatheredFacts`` and NOT in ``item_facts`` -- ``facts/models.py:19-26``
    states the rule, and a column would be a migration for a cache."""
    assert GatheredFacts().added_at is None
    assert GatheredFacts().is_empty()
    assert not GatheredFacts(added_at=date(1999, 12, 1)).is_empty()


def test_added_at_is_not_persisted_as_an_item_facts_column():
    """The negative half of C3, asserted rather than assumed: no column means
    no alembic revision and no backfill for this row."""
    from autoposter.db.models import ItemFacts

    assert not hasattr(ItemFacts, "added_at")


# --- the explicit-source model at the gather seam ---------------------------

import pytest_asyncio  # noqa: E402,F401  (imported for the plugin's fixtures)

from autoposter.facts import gather as _gather_module  # noqa: E402
from autoposter.facts.gather import gather_facts  # noqa: E402
from autoposter.facts.mdblist import NullMDBListClient  # noqa: E402

from test_mass_ops_fields import FakeTMDB, _item  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_the_non_movie_latch():
    """The warning below is latched process-wide, so a test that fires it
    would otherwise decide whether a LATER test can see it. Reset around each
    test rather than at the end of one, so ordering cannot leak either way --
    the popped-module lesson, one global along."""
    _gather_module._added_at_non_movie_warned = False
    yield
    _gather_module._added_at_non_movie_warned = False


@pytest.mark.asyncio
async def test_no_source_named_makes_no_request_and_gathers_nothing(session):
    """The negative case IS the test: unset must be byte-identical to today.

    ``FakeTMDB`` is given a release date it would happily hand over; the
    source key is the only thing that keeps it out.
    """
    calls = []

    class CountingTMDB(FakeTMDB):
        async def release_date(self, tmdb_id, source):
            calls.append((tmdb_id, source))
            return date(1999, 12, 1)

    facts = await gather_facts(
        session, _item(), CountingTMDB(GatheredFacts()), NullMDBListClient()
    )
    assert calls == []
    assert facts.added_at is None
    assert "added_at" not in facts.sources


@pytest.mark.asyncio
async def test_a_named_source_is_fetched_and_its_provenance_recorded(session):
    operations = OperationsConfig(added_at_source="tmdb_digital")
    facts = await gather_facts(
        session, _item(), FakeTMDB(GatheredFacts(), release_date=date(1999, 12, 1)),
        NullMDBListClient(), operations=operations,
    )
    assert facts.added_at == date(1999, 12, 1)
    assert facts.sources["added_at"] == "tmdb_digital"


@pytest.mark.asyncio
async def test_the_source_name_is_passed_through_to_the_client(session):
    """A mutation that hard-codes one type code would pass every parser test
    and fail here."""
    seen = []

    class RecordingTMDB(FakeTMDB):
        async def release_date(self, tmdb_id, source):
            seen.append((tmdb_id, source))
            return date(1999, 9, 21)

    operations = OperationsConfig(added_at_source="tmdb_premiere")
    await gather_facts(
        session, _item(), RecordingTMDB(GatheredFacts()), NullMDBListClient(),
        operations=operations,
    )
    assert seen == [(949, "tmdb_premiere")]


@pytest.mark.asyncio
async def test_a_source_that_yields_nothing_writes_nothing(session):
    """Kometa's rule 4. ``None`` from the provider is not an empty value to
    write -- it is no value at all."""
    operations = OperationsConfig(added_at_source="tmdb_digital")
    facts = await gather_facts(
        session, _item(), FakeTMDB(GatheredFacts(), release_date=None),
        NullMDBListClient(), operations=operations,
    )
    assert facts.added_at is None
    assert "added_at" not in facts.sources


@pytest.mark.asyncio
async def test_a_show_makes_no_request_and_says_so_exactly_once(session, caplog):
    """Kometa's rule 6, enforced where the KIND is actually known.

    A config-load refusal is impossible here: this config document holds
    library NAMES and nothing that says whether a name is a movie library
    (``config/schema.py``'s ``_library_names_must_be_configured`` records the
    same limit for itself). So the fixed sentence is emitted at the gather,
    once per process, on the ``_tvdb_source_unconfigured_warned`` latch
    precedent -- whose own comment gives the reason: a library-wide
    misconfiguration must not cost one warning line per item forever.
    """
    calls = []

    class CountingTMDB(FakeTMDB):
        async def release_date(self, tmdb_id, source):
            calls.append(tmdb_id)
            return date(1999, 12, 1)

    operations = OperationsConfig(added_at_source="tmdb_digital")
    tmdb = CountingTMDB(GatheredFacts())

    with caplog.at_level(logging.WARNING, logger="autoposter.facts.gather"):
        first = await gather_facts(
            session, _item(kind="show", tvdb_id=None), tmdb, NullMDBListClient(),
            operations=operations,
        )
        await gather_facts(
            session, _item(kind="show", tvdb_id=None), tmdb, NullMDBListClient(),
            operations=operations,
        )

    assert calls == []
    assert first.added_at is None
    lines = [r.getMessage() for r in caplog.records if "added_at_source" in r.getMessage()]
    assert lines == [
        "operations.added_at_source names a TMDb release date, which is served "
        "for movies only; it is ignored on every non-movie item"
    ]


@pytest.mark.asyncio
async def test_a_spent_tmdb_budget_does_not_throw_away_the_rest_of_the_pass(session):
    """The MDBList precedent, one provider along -- and ``gather.py:129-130``'s
    own shape. A 429 window costs this one value, not the whole gather."""
    from autoposter.facts.tmdb_budget import TmdbRateLimited

    class RefusingTMDB(FakeTMDB):
        async def release_date(self, tmdb_id, source):
            raise TmdbRateLimited("window open")

    operations = OperationsConfig(added_at_source="tmdb_digital")
    facts = await gather_facts(
        session, _item(), RefusingTMDB(GatheredFacts(studio="Warner")),
        NullMDBListClient(), operations=operations,
    )
    assert facts.added_at is None
    assert facts.studio == "Warner"


@pytest.mark.asyncio
async def test_an_item_with_no_tmdb_id_makes_no_request(session):
    calls = []

    class CountingTMDB(FakeTMDB):
        async def release_date(self, tmdb_id, source):
            calls.append(tmdb_id)
            return date(1999, 12, 1)

    operations = OperationsConfig(added_at_source="tmdb_digital")
    facts = await gather_facts(
        session, _item(tmdb_id=None), CountingTMDB(GatheredFacts()),
        NullMDBListClient(), operations=operations,
    )
    assert calls == []
    assert facts.added_at is None


# --- the write ---------------------------------------------------------------

from autoposter.plex.writer import (  # noqa: E402
    _PLEX_FIELD_NAMES,
    _REMOVABLE_FIELDS,
    WRITABLE_BY_KIND,
    plan_edits,
)

from test_mass_ops_fields import FakeItem  # noqa: E402


def test_added_at_is_writable_on_movies_only():
    """Kometa's rule 6. plexapi itself imposes no libtype limit here --
    ``AddedAtMixin`` is on all four Edit mixins -- so this map is the whole
    enforcement, exactly as it is for ``original_title``."""
    assert "added_at" in WRITABLE_BY_KIND["movie"]
    for kind in ("show", "season", "episode"):
        assert "added_at" not in WRITABLE_BY_KIND[kind]


def test_the_plex_names_are_the_same_string_twice():
    assert _PLEX_FIELD_NAMES["added_at"] == ("addedAt", "addedAt")


def test_added_at_is_not_removable():
    """Plex regenerates ``addedAt`` from the file, so 'remove a date the
    scanner owns' has no meaning here (row 229's sibling analysis). Absent
    from this set is what makes ``{added_at: remove}`` load and then be
    skipped with the existing INFO rather than clearing anything."""
    assert "added_at" not in _REMOVABLE_FIELDS


def test_the_epoch_is_exactly_what_plexapis_own_mixin_would_send():
    """Pinned against the REAL ``plexapi.mixins.AddedAtMixin``, offline -- no
    server, no network -- because the whole point of the epoch conversion is
    that our hand-built dict is byte-identical to plexapi's own.

    A literal integer would be wrong here: the value depends on the
    container's timezone (both sides convert a naive local midnight), so the
    only stable assertion is against plexapi itself.
    """
    from plexapi.mixins import AddedAtMixin

    class Probe(AddedAtMixin):
        def __init__(self):
            self.sent = {}

        def _edit(self, **kwargs):
            self.sent = kwargs

    probe = Probe()
    probe.editAddedAt("1999-10-15")
    assert set(probe.sent) == {"addedAt.value", "addedAt.locked"}
    assert isinstance(probe.sent["addedAt.value"], int)

    item = FakeItem(addedAt=None)
    edits = plan_edits(
        item, GatheredFacts(added_at=date(1999, 10, 15)),
        OperationsConfig(added_at_apply=True),
    )
    assert edits == probe.sent


def test_the_epoch_round_trips_back_to_the_same_date():
    """plexapi's ``DATETIME_TIMEZONE`` is ``None``, so the read side is naive
    local too. Write local-midnight, read it back, get the same date -- which
    is what makes the second pass steady rather than a rewrite forever."""
    from autoposter.plex.writer import _added_at_epoch

    epoch = _added_at_epoch(date(1999, 10, 15))
    assert datetime.fromtimestamp(epoch).date() == date(1999, 10, 15)


def test_a_1970_target_writes_a_real_zero_and_not_an_empty_string():
    """plexapi's ``editField`` does ``value or ''``, which turns epoch 0 into
    "". We build the dict ourselves and never call ``editField``, so a
    1970-01-01 target writes honestly. Asserted on the TYPE, because whether
    the epoch is exactly 0 depends on the container's timezone."""
    item = FakeItem(addedAt=None)
    edits = plan_edits(
        item, GatheredFacts(added_at=date(1970, 1, 1)),
        OperationsConfig(added_at_apply=True),
    )
    assert isinstance(edits["addedAt.value"], int)
    assert edits["addedAt.locked"] == 1


def test_an_equal_date_writes_nothing():
    """Compared at DATE granularity -- Kometa's own compare, and the sibling
    ``originally_available`` branch's. A container timezone change moves the
    epoch by the offset and must not rewrite the whole library; anything short
    of a day is absorbed here."""
    item = FakeItem(addedAt=datetime(1999, 10, 15, 3, 42, 11))
    edits = plan_edits(
        item, GatheredFacts(added_at=date(1999, 10, 15)),
        OperationsConfig(added_at_apply=True),
    )
    assert edits == {}


def test_no_gathered_date_writes_nothing():
    item = FakeItem(addedAt=datetime(2005, 1, 1))
    assert plan_edits(item, GatheredFacts(), OperationsConfig(added_at_apply=True)) == {}


def test_apply_off_writes_nothing_and_logs_the_item_and_the_field_only(caplog):
    """Row 213: the log names the item and the field, and NEVER the value. The
    date is the whole content of this operation, so a line carrying it would
    put library metadata into the pod log every pass."""
    item = FakeItem(addedAt=datetime(2005, 1, 1), title="Heat", year=1995)
    with caplog.at_level(logging.INFO, logger="autoposter.plex.writer"):
        edits = plan_edits(
            item, GatheredFacts(added_at=date(1999, 10, 15)),
            OperationsConfig(added_at_source="tmdb_digital"),
        )
    assert edits == {}
    message = next(m for m in (r.getMessage() for r in caplog.records) if "added_at" in m)
    assert message == (
        "plex: would set added_at on movie 'Heat' (1995) "
        "(operations.added_at_apply is off)"
    )
    assert "1999" not in message


def test_a_show_is_never_written_even_with_both_gates_on():
    item = FakeItem(kind="show")
    edits = plan_edits(
        item, GatheredFacts(added_at=date(1999, 10, 15)),
        OperationsConfig(added_at_source="tmdb_digital", added_at_apply=True),
    )
    assert edits == {}


def test_the_lock_verb_now_loads_and_locks_added_at():
    """The map growing is what makes row 87's verbs reach this field -- for
    free, and deliberately: ``config/schema.py``'s ``_validate_field_verbs``
    accepts any name in ``set().union(*WRITABLE_BY_KIND.values())``."""
    from autoposter.plex.writer import verb_edits

    from test_mass_ops_verbs import LockableItem

    item = LockableItem(locks=[("addedAt", False)])
    operations = OperationsConfig(field_verbs={"added_at": "lock"}, lock_apply=True)
    assert verb_edits(item, operations) == {"addedAt.locked": 1}


def test_the_remove_verb_is_skipped_with_the_existing_info(caplog):
    from autoposter.plex.writer import verb_edits

    from test_mass_ops_verbs import LockableItem

    item = LockableItem(locks=[("addedAt", False)])
    operations = OperationsConfig(field_verbs={"added_at": "remove"}, remove_apply=True)
    with caplog.at_level(logging.INFO, logger="autoposter.plex.writer"):
        assert verb_edits(item, operations) == {}
    assert any("does not clear" in r.getMessage() for r in caplog.records)


def test_added_at_is_not_offered_as_a_per_item_override():
    """Option (d), filed rather than shipped. ``parse_override`` dispatches on
    four frozensets and RAISES for a field in none of them, so advertising
    ``added_at`` in the served ``writable`` list would offer a field every PUT
    to it refuses. Opening it later means a ``DATE_FIELDS`` entry, a
    ``canonical_value`` branch, an ``override_edits`` branch carrying this
    same epoch conversion, and the ItemDetail panel -- a decision of its
    own."""
    from autoposter.plex.item_overrides import (
        OVERRIDE_EXCLUDED_FIELDS,
        OverrideValueError,
        parse_override,
        writable_fields,
    )

    assert "added_at" in OVERRIDE_EXCLUDED_FIELDS
    assert "added_at" not in writable_fields("movie")
    assert "original_title" in writable_fields("movie")
    with pytest.raises(OverrideValueError):
        parse_override("added_at", "1999-10-15")


def test_the_metadata_backup_picks_it_up_and_serialises_it_as_a_date():
    """The one mitigation this row brings with it: for the first time, this
    project's own undo file records the pre-write ``addedAt``. No code change
    -- ``capture_item`` walks ``WRITABLE_BY_KIND`` and already formats a
    datetime as ``%Y-%m-%d``."""
    from autoposter.metadata_backup import capture_item

    from test_mass_ops_verbs import LockableItem

    item = LockableItem(addedAt=datetime(1999, 10, 15, 12, 0, 0))
    assert capture_item(item, "movie")["added_at"] == "1999-10-15"


# --- the gated-feature entry-point tests ------------------------------------
#
# Through ``render.pipeline.apply_metadata`` -- the REAL seam, the one that
# resolves the per-library config, gathers, persists, checks the row-35
# exemption and calls ``apply_facts`` -- and not through ``plan_edits``. Three
# assertions, in the order the standing law states them.

from pathlib import Path  # noqa: E402

import pytest_asyncio as _pytest_asyncio  # noqa: E402,F401

from autoposter.config.loader import load_config  # noqa: E402
from conftest import seed_media_item  # noqa: E402
from autoposter.render.pipeline import apply_metadata  # noqa: E402

from test_mass_ops_verbs import RecordingPlexItem, RecordingServer  # noqa: E402

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


@_pytest_asyncio.fixture
async def media_item_id(session):
    media = await seed_media_item(session, "1", library="Movies", kind="movie", title="Heat")
    return media.id


class AddedAtPlexItem(RecordingPlexItem):
    """``RecordingPlexItem`` plus the one round-trip plexapi performs.

    ``addedAt`` goes out as a unix epoch int and comes BACK, on the next read,
    as a naive local datetime (``plexapi/utils.py``'s ``toDatetime`` with
    ``DATETIME_TIMEZONE = None``). Without modelling that, a second pass would
    compare a formatted date against an int, differ every time, and rewrite the
    item forever -- which is precisely the defect the steady-state leg below
    exists to catch, so the double has to be able to expose it.
    """

    def saveEdits(self):  # noqa: N802 - plexapi name
        super().saveEdits()
        if isinstance(getattr(self, "addedAt", None), int):
            self.addedAt = datetime.fromtimestamp(self.addedAt)


@pytest.mark.asyncio
async def test_entry_point_gate_off_is_byte_identical(session, media_item_id, config):
    """(a) GATE OFF: no source named. The item carries a studio fact equal to
    Plex's own value, so ``apply_metadata``'s ``facts.is_empty()``
    short-circuit is NOT what keeps this green -- ``apply_facts`` and
    ``plan_edits`` are both reached and it is the unset source that keeps
    ``added_at`` out."""
    plex_item = AddedAtPlexItem(studio="Warner", addedAt=datetime(2005, 1, 1), locks=[])
    await apply_metadata(
        session, config, media_item_id, _item(), RecordingServer(plex_item),
        FakeTMDB(GatheredFacts(studio="Warner"), release_date=date(1999, 10, 15)),
        NullMDBListClient(),
    )
    assert plex_item.edits == []
    assert plex_item.addedAt == datetime(2005, 1, 1)


@pytest.mark.asyncio
async def test_entry_point_source_named_but_apply_off_still_writes_nothing(
    session, media_item_id, config
):
    """The middle rung the five sibling sources do not have: the fetch is
    armed, the write is not."""
    config.operations.added_at_source = "tmdb_digital"
    plex_item = AddedAtPlexItem(studio="Warner", addedAt=datetime(2005, 1, 1), locks=[])
    await apply_metadata(
        session, config, media_item_id, _item(), RecordingServer(plex_item),
        FakeTMDB(GatheredFacts(), release_date=date(1999, 10, 15)),
        NullMDBListClient(),
    )
    assert plex_item.edits == []
    assert plex_item.addedAt == datetime(2005, 1, 1)


@pytest.mark.asyncio
async def test_entry_point_gate_on_fires_and_the_second_pass_is_steady(
    session, media_item_id, config
):
    from autoposter.plex.writer import _added_at_epoch

    # (b) GATE ON: both keys set, so the write reaches Plex through the real
    # seam, as one epoch int plus one lock, inside one batched edit.
    config.operations.added_at_source = "tmdb_digital"
    config.operations.added_at_apply = True
    plex_item = AddedAtPlexItem(studio="Warner", addedAt=datetime(2005, 1, 1), locks=[])

    await apply_metadata(
        session, config, media_item_id, _item(), RecordingServer(plex_item),
        FakeTMDB(GatheredFacts(), release_date=date(1999, 10, 15)),
        NullMDBListClient(),
    )
    assert plex_item.edits == [{
        "addedAt.value": _added_at_epoch(date(1999, 10, 15)),
        "addedAt.locked": 1,
    }]
    assert plex_item.saved == 1
    assert plex_item.addedAt == datetime(1999, 10, 15)

    # (c) SECOND PASS: steady state. Plex now reports the date we wrote, so
    # the date-granular compare finds nothing to do. This is the one-time
    # cost, ending -- and the whole basis of the blast-radius claim.
    await apply_metadata(
        session, config, media_item_id, _item(), RecordingServer(plex_item),
        FakeTMDB(GatheredFacts(), release_date=date(1999, 10, 15)),
        NullMDBListClient(),
    )
    assert len(plex_item.edits) == 1
    assert plex_item.saved == 1
