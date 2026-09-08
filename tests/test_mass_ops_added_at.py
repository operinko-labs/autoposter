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
from datetime import date

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
