"""Row 35 — item exemptions. Metadata WRITES only (facts adjudication 1).

The negative case is the test: an exempt item's facts are still gathered and
still persisted, and the Plex edit is the thing that must not happen.
"""
from pathlib import Path

from autoposter.config.loader import load_config
from autoposter.db.models import ItemFacts
from autoposter.facts.models import GatheredFacts
from autoposter.plex.client import ResolvedItem
from autoposter.plex.writer import apply_facts as _plex_apply_facts
from autoposter.plex.writer import exemption_reason
from autoposter.render import pipeline
from sqlalchemy import select

from conftest import seed_media_item

EXAMPLE = Path("config/autoposter.example.yaml")


def resolved(rating_key="w1", imdb_id="tt1"):
    return ResolvedItem(
        server="plex", native_id=rating_key, library="Movies", kind="movie", title="X",
        year=2023, season_number=None, episode_number=None, root_folder="X",
        file_path=None, art_url=None, tmdb_id=1, tvdb_id=None, imdb_id=imdb_id,
        parent_native_id=None,
    )


class _Tag:
    def __init__(self, tag):
        self.tag = tag


class RecordingPlexItem:
    type = "movie"
    rating = None
    audienceRating = None
    contentRating = None
    studio = None
    originallyAvailableAt = None
    genres: list = []

    def __init__(self, labels=()):
        self.edits = {}
        self.labels = [_Tag(name) for name in labels]

    def batchEdits(self):
        return self

    def saveEdits(self):
        return self

    def edit(self, **kwargs):
        self.edits.update(kwargs)
        return self

    def addGenre(self, genres, locked=True):
        return self


class RecordingServer:
    """The MediaServer surface ``apply_metadata`` now goes through, wrapping a
    ``RecordingPlexItem`` so the real ``plex.writer.apply_facts`` still runs
    against it -- the object under test is what got written, not this shim."""

    name = "plex"

    def __init__(self, plex_item):
        self._item = plex_item

    async def item_labels(self, ref):
        return [tag.tag for tag in self._item.labels]

    async def apply_facts(self, ref, facts, operations=None, parental_categories=None, overrides=None):
        return await _plex_apply_facts(self._item, facts, operations, parental_categories, overrides)


async def _media(session, rating_key="w1"):
    return await seed_media_item(session, rating_key, library="Movies", kind="movie", title="X")


def _config(**operations):
    config = load_config(EXAMPLE)
    return config.model_copy(
        update={"operations": config.operations.model_copy(update=operations)}
    )


async def _run(session, config, item, plex_item, monkeypatch):
    async def fake_gather(_session, _item, _tmdb, _mdblist, **_kwargs):
        return GatheredFacts(critic_rating=4.9, sources={"critic_rating": "imdb"})

    monkeypatch.setattr(pipeline, "gather_facts", fake_gather)
    media = await _media(session, item.native_id)
    await pipeline.apply_metadata(
        session, config, media.id, item, RecordingServer(plex_item), object(), object()
    )
    return media


async def test_an_ignored_rating_key_is_never_written_to_plex(session, monkeypatch):
    plex_item = RecordingPlexItem()
    media = await _run(
        session, _config(ignore_ids=["w1"]), resolved(), plex_item, monkeypatch
    )
    assert plex_item.edits == {}
    stored = (
        await session.execute(select(ItemFacts).where(ItemFacts.item_id == media.id))
    ).scalar_one()
    assert stored.critic_rating == 4.9


async def test_an_ignored_imdb_id_is_never_written_to_plex(session, monkeypatch):
    plex_item = RecordingPlexItem()
    await _run(
        session, _config(ignore_imdb_ids=["tt1"]), resolved(), plex_item, monkeypatch
    )
    assert plex_item.edits == {}


async def test_an_ignored_label_is_never_written_to_plex(session, monkeypatch):
    plex_item = RecordingPlexItem(labels=["skip_autoposter"])
    await _run(
        session, _config(ignore_labels=["skip_autoposter"]), resolved(),
        plex_item, monkeypatch,
    )
    assert plex_item.edits == {}


async def test_labels_are_matched_case_insensitively(session, monkeypatch):
    """Plex canonicalizes label case, so an exact compare would miss."""
    plex_item = RecordingPlexItem(labels=["Skip_Autoposter"])
    await _run(
        session, _config(ignore_labels=["skip_autoposter"]), resolved(),
        plex_item, monkeypatch,
    )
    assert plex_item.edits == {}


async def test_no_exemption_configured_still_writes(session, monkeypatch):
    """The default pin: an untouched config writes exactly as it did before."""
    plex_item = RecordingPlexItem()
    await _run(session, _config(), resolved(), plex_item, monkeypatch)
    assert plex_item.edits == {"rating.value": 4.9, "rating.locked": 1}


def test_exemption_reason_names_which_setting_matched():
    config = _config(ignore_labels=["skip_autoposter"])
    reason = exemption_reason(
        config.operations, "w9", "tt9", ["Skip_Autoposter"]
    )
    assert reason == "operations.ignore_labels matched label 'Skip_Autoposter'"
    assert exemption_reason(config.operations, "w9", "tt9", []) is None
