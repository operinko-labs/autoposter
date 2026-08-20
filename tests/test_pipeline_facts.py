from pathlib import Path

import pytest
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db.models import ItemFacts, MediaItem
from autoposter.facts.models import GatheredFacts
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
