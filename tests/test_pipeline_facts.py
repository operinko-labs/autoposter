import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select, text

from autoposter.config.loader import load_config
from autoposter.db.models import ItemFacts, MediaItem
from autoposter.facts.mdblist import NullMDBListClient
from autoposter.facts.models import GatheredFacts
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.render import pipeline

EXAMPLE = Path("config/autoposter.example.yaml")


def resolved():
    return ResolvedItem(
        rating_key="w1", library="Movies", kind="movie", title="X", year=2023,
        season_number=None, episode_number=None, root_folder="X", file_path=None,
        art_url=None, tmdb_id=1, tvdb_id=None, imdb_id="tt1", parent_rating_key=None,
    )


class RecordingPlexItem:
    type = "movie"
    rating = None
    audienceRating = None
    contentRating = None
    studio = None
    originallyAvailableAt = None
    genres: list = []

    def __init__(self):
        self.edits = {}

    def batchEdits(self):
        return self

    def saveEdits(self):
        return self

    def edit(self, **kwargs):
        self.edits.update(kwargs)
        return self

    def addGenre(self, genres, locked=True):
        return self


async def _media(session):
    media = MediaItem(rating_key="w1", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    return media


async def test_facts_are_gathered_persisted_and_written(session, monkeypatch):
    media = await _media(session)

    async def fake_gather(_session, _item, _tmdb, _mdblist):
        return GatheredFacts(critic_rating=4.9, sources={"critic_rating": "imdb"})

    monkeypatch.setattr(pipeline, "gather_facts", fake_gather)
    plex_item = RecordingPlexItem()
    config = load_config(EXAMPLE)

    facts = await pipeline.apply_metadata(
        session, config, media.id, resolved(), plex_item, object(), object()
    )

    assert facts.critic_rating == pytest.approx(4.9)
    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.critic_rating == pytest.approx(4.9)
    assert plex_item.edits["rating.value"] == pytest.approx(4.9)


async def test_disabling_operations_skips_everything(session, monkeypatch):
    media = await _media(session)

    async def fail_gather(*args, **kwargs):
        raise AssertionError("gather must not run when operations are disabled")

    monkeypatch.setattr(pipeline, "gather_facts", fail_gather)
    config = load_config(EXAMPLE)
    config.operations.enabled = False

    facts = await pipeline.apply_metadata(
        session, config, media.id, resolved(), RecordingPlexItem(), object(), object()
    )
    assert facts.is_empty()
    assert (await session.execute(select(ItemFacts))).scalars().all() == []


async def test_write_to_plex_false_still_stores_facts(session, monkeypatch):
    """The safe setting while the tool being replaced still owns these fields."""
    media = await _media(session)

    async def fake_gather(_session, _item, _tmdb, _mdblist):
        return GatheredFacts(critic_rating=4.9)

    monkeypatch.setattr(pipeline, "gather_facts", fake_gather)
    config = load_config(EXAMPLE)
    config.operations.write_to_plex = False
    plex_item = RecordingPlexItem()

    await pipeline.apply_metadata(
        session, config, media.id, resolved(), plex_item, object(), object()
    )

    assert (await session.execute(select(ItemFacts))).scalar_one().critic_rating
    assert plex_item.edits == {}


def test_example_config_enables_operations():
    config = load_config(EXAMPLE)
    assert config.operations.enabled is True
    assert config.operations.write_to_plex is True


async def test_no_mdblist_key_still_gathers_persists_and_writes_other_ratings(session):
    """Finding 1: with no MDBList key, critic and audience ratings are still
    gathered, persisted, and written to Plex; only content_rating is None."""
    from autoposter.facts.imdb import store_ratings

    await store_ratings(session, {"tt1": 4.9})
    media = await _media(session)

    class FakeTMDBFacts:
        async def movie(self, tmdb_id):
            return GatheredFacts(audience_rating=6.3, sources={"audience_rating": "tmdb"})

    config = load_config(EXAMPLE)
    plex_item = RecordingPlexItem()

    facts = await pipeline.apply_metadata(
        session, config, media.id, resolved(), plex_item,
        FakeTMDBFacts(), NullMDBListClient(),
    )

    assert facts.critic_rating == pytest.approx(4.9)
    assert facts.audience_rating == pytest.approx(6.3)
    assert facts.content_rating is None

    row = (await session.execute(select(ItemFacts))).scalar_one()
    assert row.critic_rating == pytest.approx(4.9)
    assert row.audience_rating == pytest.approx(6.3)
    assert row.content_rating is None

    assert plex_item.edits["rating.value"] == pytest.approx(4.9)
    assert plex_item.edits["audienceRating.value"] == pytest.approx(6.3)
    assert "contentRating.value" not in plex_item.edits


class _FakePlex:
    """Minimal Plex stand-in for process_item tests below: resolve() returns
    a fixed item and fetch_item() a fresh RecordingPlexItem, without any real
    Plex or network access."""

    def __init__(self, item):
        self._item = item

    async def resolve(self, intent):
        return self._item

    async def fetch_item(self, rating_key):
        return RecordingPlexItem()


async def test_metadata_failure_does_not_block_artwork(session, monkeypatch, caplog):
    """Finding 2: a rating provider hiccup must not fail the whole item —
    artwork still renders."""

    class BoomTMDBFacts:
        async def movie(self, tmdb_id):
            raise RuntimeError("provider hiccup")

    rendered = []

    async def fake_render_artifact(session, config, http, item, art_kind, providers):
        rendered.append(art_kind)
        return object()

    monkeypatch.setattr(pipeline, "render_artifact", fake_render_artifact)
    config = load_config(EXAMPLE)
    intent = RenderIntent(kind="movie", title="X", tmdb_id=1)

    with caplog.at_level("WARNING"):
        results = await pipeline.process_item(
            session, config, None, _FakePlex(resolved()), [], intent,
            tmdb_facts=BoomTMDBFacts(), mdblist=NullMDBListClient(),
        )

    assert rendered == ["poster", "background"]
    assert len(results) == 2
    assert any("metadata operations failed" in r.message for r in caplog.records)


async def test_metadata_db_error_still_lets_artwork_use_the_session(session, monkeypatch, caplog):
    """Finding 5: if the metadata step fails with a database error, the
    session's transaction is left aborted. Without a rollback in the
    containment, the artifact loop's first session.execute() would raise
    PendingRollbackError instead of rendering -- defeating the containment's
    whole purpose."""

    class BoomTMDBFacts:
        async def movie(self, tmdb_id):
            # Same failure shape as the worker's own DB-error tests
            # (test_worker.py): leaves the session mid-failed-transaction.
            await session.execute(text("SELECT 1/0"))

    async def fake_render_artifact(session_, config, http, item, art_kind, providers):
        # Proves the session is usable again: a PendingRollbackError here
        # would mean the rollback in process_item's except block is missing.
        await session_.execute(select(1))
        return object()

    monkeypatch.setattr(pipeline, "render_artifact", fake_render_artifact)
    config = load_config(EXAMPLE)
    intent = RenderIntent(kind="movie", title="X", tmdb_id=1)

    with caplog.at_level("WARNING"):
        results = await pipeline.process_item(
            session, config, None, _FakePlex(resolved()), [], intent,
            tmdb_facts=BoomTMDBFacts(), mdblist=NullMDBListClient(),
        )

    assert len(results) == 2
    assert any("metadata operations failed" in r.message for r in caplog.records)


async def test_cancelled_error_during_metadata_still_propagates(session, monkeypatch):
    """Finding 2 constraint: CancelledError is a BaseException used for
    shutdown and must not be swallowed by the containment."""

    class CancellingTMDBFacts:
        async def movie(self, tmdb_id):
            raise asyncio.CancelledError()

    async def fake_render_artifact(*args, **kwargs):
        raise AssertionError("must not reach the artifact loop on cancellation")

    monkeypatch.setattr(pipeline, "render_artifact", fake_render_artifact)
    config = load_config(EXAMPLE)
    intent = RenderIntent(kind="movie", title="X", tmdb_id=1)

    with pytest.raises(asyncio.CancelledError):
        await pipeline.process_item(
            session, config, None, _FakePlex(resolved()), [], intent,
            tmdb_facts=CancellingTMDBFacts(), mdblist=NullMDBListClient(),
        )


async def test_metadata_runs_before_the_artifact_loop(session, monkeypatch):
    """Finding 3: process_item must run metadata operations before the first
    artifact is rendered — the ordering the next phase's badges depend on."""
    order = []

    class OrderedTMDBFacts:
        async def movie(self, tmdb_id):
            order.append("metadata")
            return GatheredFacts()

    class OrderedMDBList:
        async def content_rating(self, **kwargs):
            return None

    async def fake_render_artifact(session, config, http, item, art_kind, providers):
        order.append(f"artifact:{art_kind}")
        return object()

    monkeypatch.setattr(pipeline, "render_artifact", fake_render_artifact)
    config = load_config(EXAMPLE)
    intent = RenderIntent(kind="movie", title="X", tmdb_id=1)

    await pipeline.process_item(
        session, config, None, _FakePlex(resolved()), [], intent,
        tmdb_facts=OrderedTMDBFacts(), mdblist=OrderedMDBList(),
    )

    assert order == ["metadata", "artifact:poster", "artifact:background"]


async def test_a_plex_without_fetch_item_is_not_swallowed(session, monkeypatch):
    """A `plex` missing fetch_item is a wiring bug, not the runtime failure
    the badge stage contains: it must propagate rather than become a WARNING
    that resurfaces later as an unrelated error."""

    class PlexWithoutFetchItem:
        async def resolve(self, intent):
            return resolved()

    async def fake_render_artifact(session, config, http, item, art_kind, providers):
        return object()

    monkeypatch.setattr(pipeline, "render_artifact", fake_render_artifact)
    config = load_config(EXAMPLE)
    assert config.badges.enabled, "the badge stage must be reached for this to mean anything"

    with pytest.raises(AttributeError, match="fetch_item"):
        await pipeline.process_item(
            session, config, None, PlexWithoutFetchItem(), [],
            RenderIntent(kind="movie", title="X", tmdb_id=1),
        )
