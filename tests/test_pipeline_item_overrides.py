"""Roadmap row 99 through ``process_item``, the real entry point.

The law this file exists for: a gated feature gets ONE test through the path
production actually takes, because a helper test can pass while the wired path
differs. This ledger has earned that three times.

Artwork is disabled in every test here, exactly as row 87's own entry-point
test does it ("no provider, no imagemagick, needed for the render loop below
it") -- so nothing composites, nothing touches ImageMagick, and NO
``@pytest.mark.imagemagick`` marker belongs on any of it. CI's main pytest
step runs ``-m "not imagemagick and not deep"`` on a runner with no ``magick``
and ``tests/conftest.py::imagemagick`` turns a missing binary into a
``pytest.fail`` when ``CI`` is set, so marking a non-compositing test would
make it fail in CI and nowhere else.

``config`` is a LOCAL fixture. ``tests/conftest.py`` has no bare ``config`` --
it defines ``config_with_badges``, ``config_badges_dry_run``,
``config_badges_disabled`` and ``config_factory`` and nothing plainer -- and
``tests/test_mass_ops_verbs.py`` already answers that by defining its own
two-line one. This module does the same rather than adding a ninth fixture to
a shared conftest for one file's benefit.
"""
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db import refs
from autoposter.db.models import ImdbRating, ItemFacts, ItemMetadataOverride
from autoposter.facts.mdblist import NullMDBListClient
from autoposter.facts.models import GatheredFacts
from autoposter.intake.arr import RenderIntent
from autoposter.render.pipeline import process_item
from autoposter.servers.registry import Servers

from test_mass_ops_fields import FakeTMDB, _item
from test_mass_ops_verbs import FakeField, FakePlexServer, RecordingPlexItem

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    """The example config, exactly as ``tests/test_mass_ops_verbs.py`` builds
    it for row 87's own entry-point tests."""
    return load_config(EXAMPLE)


def _intent() -> RenderIntent:
    return RenderIntent(kind="movie", title="Heat", tmdb_id=949)


async def _override(session, field: str, value: str) -> None:
    """Plant an override on whichever ``media_items`` row the pipeline made.

    Deliberately LOOKS THE ROW UP rather than creating one: the point of an
    entry-point test is that the id the pipeline writes under and the id the
    store reads by are the same id, and pre-creating the row would assume
    exactly that. ``_item``'s rating key is ``"1"``.
    """
    item_id = await refs.item_id_for(session, "plex", "1")
    session.add(ItemMetadataOverride(item_id=item_id, field=field, value=value))
    await session.commit()


@pytest.mark.asyncio
async def test_gate_off_writes_nothing_the_pipeline_would_not_have_written(
    session, config
):
    """(a) GATE OFF: byte-identical to before this row. The override row is
    ignored, NOT deleted -- so turning the gate on restores the operator's
    work rather than finding it gone."""
    config.artwork.poster.enabled = False
    config.artwork.background.enabled = False
    config.operations.item_overrides_enabled = False

    plex_item = RecordingPlexItem(tagline="Old words")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    ) as http:
        await process_item(
            session, config, http, Servers({"plex": FakePlexServer(_item(), plex_item)}), [],
            _intent(), tmdb_facts=FakeTMDB(GatheredFacts()),
            mdblist=NullMDBListClient(),
        )
        await _override(session, "tagline", "New words")
        await process_item(
            session, config, http, Servers({"plex": FakePlexServer(_item(), plex_item)}), [],
            _intent(), tmdb_facts=FakeTMDB(GatheredFacts()),
            mdblist=NullMDBListClient(),
        )

    assert plex_item.edits == []
    assert plex_item.saved == 0
    assert (
        await session.execute(select(ItemMetadataOverride))
    ).scalars().all() != [], "the row is ignored, never deleted"


@pytest.mark.asyncio
async def test_gate_on_writes_the_override_even_with_no_provider_facts(
    session, config
):
    """(b) GATE ON: it fires. This is Established fact **h** in action -- the
    provider gather is empty, so without ``has_overrides`` in the write gate
    ``apply_facts`` would be skipped entirely and the operator's value would
    never reach Plex."""
    config.artwork.poster.enabled = False
    config.artwork.background.enabled = False
    config.operations.item_overrides_enabled = True

    plex_item = RecordingPlexItem(tagline="Old words")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    ) as http:
        await process_item(
            session, config, http, Servers({"plex": FakePlexServer(_item(), plex_item)}), [],
            _intent(), tmdb_facts=FakeTMDB(GatheredFacts()),
            mdblist=NullMDBListClient(),
        )
        await _override(session, "tagline", "New words")
        await process_item(
            session, config, http, Servers({"plex": FakePlexServer(_item(), plex_item)}), [],
            _intent(), tmdb_facts=FakeTMDB(GatheredFacts()),
            mdblist=NullMDBListClient(),
        )

    assert plex_item.edits == [
        {"tagline.value": "New words", "tagline.locked": 1}
    ]
    assert plex_item.saved == 1


@pytest.mark.asyncio
async def test_the_second_pass_over_an_applied_override_writes_nothing(
    session, config
):
    """(c) SECOND PASS: steady state. Plex now holds the operator's value AND
    reports it locked, so the diff is empty and no second write happens --
    which is what makes this re-applied every pass without churning the
    server. The lock matters as of task-2 fix round 1's I-1 ruling: an item
    Plex reports as matching but NOT locked still gets a lock-only write
    (pinned directly against ``override_edits`` in
    ``tests/test_item_overrides_writer.py``), so this steady-state pass has
    to simulate the locked state the first write would actually leave
    behind, not just the matching value."""
    config.artwork.poster.enabled = False
    config.artwork.background.enabled = False
    config.operations.item_overrides_enabled = True

    plex_item = RecordingPlexItem(tagline="Old words")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    ) as http:
        await process_item(
            session, config, http, Servers({"plex": FakePlexServer(_item(), plex_item)}), [],
            _intent(), tmdb_facts=FakeTMDB(GatheredFacts()),
            mdblist=NullMDBListClient(),
        )
        await _override(session, "tagline", "New words")
        plex_item.tagline = "New words"
        plex_item.fields = [FakeField("tagline", True)]
        await process_item(
            session, config, http, Servers({"plex": FakePlexServer(_item(), plex_item)}), [],
            _intent(), tmdb_facts=FakeTMDB(GatheredFacts()),
            mdblist=NullMDBListClient(),
        )

    assert plex_item.edits == []
    assert plex_item.saved == 0


@pytest.mark.asyncio
async def test_the_pass_never_writes_the_operator_s_value_into_item_facts(
    session, config
):
    """Global Constraint 13 through the real entry point. ``persist_facts``
    stores what the PROVIDER said; the override layers on top of that record
    and never into it, so a later provider change is still visible in the row
    and ``sources`` still says who supplied what.

    ``critic_rating`` is IMDb's alone (``facts/gather.py::_critic_rating`` --
    ``tmdb_facts`` never populates it; TMDb only ever supplies
    ``audience_rating``), so the "provider" here is a seeded ``ImdbRating``
    row for ``_item()``'s own ``imdb_id`` (``tt0113277``), not the TMDB fake.
    """
    config.artwork.poster.enabled = False
    config.artwork.background.enabled = False
    config.operations.item_overrides_enabled = True
    session.add(ImdbRating(tconst="tt0113277", rating=4.9))
    await session.commit()

    plex_item = RecordingPlexItem(rating=4.9)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    ) as http:
        await process_item(
            session, config, http, Servers({"plex": FakePlexServer(_item(), plex_item)}), [],
            _intent(), tmdb_facts=FakeTMDB(GatheredFacts()),
            mdblist=NullMDBListClient(),
        )
        await _override(session, "critic_rating", "9.9")
        await process_item(
            session, config, http, Servers({"plex": FakePlexServer(_item(), plex_item)}), [],
            _intent(), tmdb_facts=FakeTMDB(GatheredFacts()),
            mdblist=NullMDBListClient(),
        )

    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.critic_rating == 4.9, "the provider's record, untouched"
