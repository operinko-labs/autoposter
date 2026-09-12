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
from autoposter.plex.writer import apply_facts as _plex_apply_facts
from autoposter.render import pipeline

EXAMPLE = Path("config/autoposter.example.yaml")


def resolved():
    return ResolvedItem(
        server="plex", native_id="w1", library="Movies", kind="movie", title="X", year=2023,
        season_number=None, episode_number=None, root_folder="X", file_path=None,
        art_url=None, tmdb_id=1, tvdb_id=None, imdb_id="tt1", parent_native_id=None,
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


class RecordingServer:
    """The MediaServer surface ``apply_metadata`` now goes through, wrapping a
    ``RecordingPlexItem`` so the real ``plex.writer.apply_facts`` still runs
    against it -- the object under test is what got written, not this shim."""

    name = "plex"

    def __init__(self, plex_item):
        self._item = plex_item

    async def item_labels(self, ref):
        return [tag.tag for tag in getattr(self._item, "labels", None) or []]

    async def apply_facts(self, ref, facts, operations=None, parental_categories=None, overrides=None):
        return await _plex_apply_facts(self._item, facts, operations, parental_categories, overrides)


async def _media(session):
    media = MediaItem(rating_key="w1", library="Movies", kind="movie", title="X")
    session.add(media)
    await session.flush()
    return media


async def test_facts_are_gathered_persisted_and_written(session, monkeypatch):
    media = await _media(session)

    async def fake_gather(_session, _item, _tmdb, _mdblist, **_kwargs):
        return GatheredFacts(critic_rating=4.9, sources={"critic_rating": "imdb"})

    monkeypatch.setattr(pipeline, "gather_facts", fake_gather)
    plex_item = RecordingPlexItem()
    config = load_config(EXAMPLE)

    facts = await pipeline.apply_metadata(
        session, config, media.id, resolved(), RecordingServer(plex_item), object(), object()
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
        session, config, media.id, resolved(), RecordingServer(RecordingPlexItem()),
        object(), object(),
    )
    assert facts.is_empty()
    assert (await session.execute(select(ItemFacts))).scalars().all() == []


async def test_write_to_plex_false_still_stores_facts(session, monkeypatch):
    """The safe setting while the tool being replaced still owns these fields."""
    media = await _media(session)

    async def fake_gather(_session, _item, _tmdb, _mdblist, **_kwargs):
        return GatheredFacts(critic_rating=4.9)

    monkeypatch.setattr(pipeline, "gather_facts", fake_gather)
    config = load_config(EXAMPLE)
    config.operations.write_to_plex = False
    plex_item = RecordingPlexItem()

    await pipeline.apply_metadata(
        session, config, media.id, resolved(), RecordingServer(plex_item), object(), object()
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
        session, config, media.id, resolved(), RecordingServer(plex_item),
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
    Plex or network access. item_labels/apply_facts are the MediaServer
    surface apply_metadata now goes through directly."""

    def __init__(self, item):
        self._item = item

    async def resolve(self, intent):
        return self._item

    async def fetch_item(self, rating_key):
        return RecordingPlexItem()

    async def item_labels(self, ref):
        return []

    async def apply_facts(self, ref, facts, operations=None, parental_categories=None, overrides=None):
        return await _plex_apply_facts(
            RecordingPlexItem(), facts, operations, parental_categories, overrides
        )


async def test_metadata_failure_does_not_block_artwork(session, monkeypatch, caplog):
    """Finding 2: a rating provider hiccup must not fail the whole item —
    artwork still renders."""

    class BoomTMDBFacts:
        async def movie(self, tmdb_id):
            raise RuntimeError("provider hiccup")

    rendered = []

    async def fake_render_artifact(session, config, http, item, art_kind, providers, **_kwargs):
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

    async def fake_render_artifact(session_, config, http, item, art_kind, providers, **_kwargs):
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

    async def fake_render_artifact(session, config, http, item, art_kind, providers, **_kwargs):
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


async def test_a_plex_missing_fetch_item_is_contained_in_the_badge_stage(
    session, monkeypatch, caplog
):
    """Task 4 moved the badge stage's raw-item read behind ``apply_badges``
    itself (``server.fetch_item(ref.native_id)``, still Plex-only), which now
    sits INSIDE that call's own frame rather than behind a standalone
    ``fetch_item = plex.fetch_item`` bound outside process_item's try block
    (deleted by this task). So a `plex` missing ``fetch_item`` -- a wiring
    bug -- is now caught by the same containment a runtime Plex hiccup gets,
    logged rather than propagated. This pins the new shape."""

    class PlexWithoutFetchItem:
        async def resolve(self, intent):
            return resolved()

    class _RenderStub:
        art_kind = "poster"
        status = "rendered"
        badge_fingerprint = None

    async def fake_render_artifact(session, config, http, item, art_kind, providers, **_kwargs):
        return _RenderStub()

    monkeypatch.setattr(pipeline, "render_artifact", fake_render_artifact)
    config = load_config(EXAMPLE)
    assert config.badges.enabled, "the badge stage must be reached for this to mean anything"

    with caplog.at_level("WARNING"):
        results = await pipeline.process_item(
            session, config, None, PlexWithoutFetchItem(), [],
            RenderIntent(kind="movie", title="X", tmdb_id=1),
        )

    assert len(results) == 2
    assert any("badge stage failed" in r.message for r in caplog.records)


@pytest.mark.imagemagick
async def test_title_card_self_feed_is_refused_through_process_item(session, tmp_path):
    """Finding #1's regression test, run through process_item -- the real
    entry point. An episode whose thumb is already our own badged output
    still resolves the media:// frame, never the upload:// entry Plex is
    currently showing.

    Marked ``imagemagick`` because render_artifact runs for real here, all
    the way through compositing: CI's main pytest step runs on a runner
    with no ``magick`` and deselects this marker, and the ImageMagick step
    runs it inside the shipped image, where the binary exists."""
    import functools

    import httpx
    from conftest import GOLDEN, decodable_png

    from autoposter.render.pipeline import fetch_plex_generated_base

    class _FakeEntry:
        def __init__(self, rating_key, key):
            self.ratingKey = rating_key
            self.key = key

    class _FakePlexItem:
        def posters(self):
            return [
                _FakeEntry("upload://abc123", "/library/metadata/900/file?url=upload..."),
                _FakeEntry(
                    "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
                    "/library/metadata/900/file?url=media%3A%2F%2F5%2Fx.bundle...",
                ),
            ]

    class _FakePlex:
        capabilities = frozenset({pipeline.CAP_TITLE_CARD_URL})
        async def resolve(self, intent):
            return ResolvedItem(
                server="plex", native_id="900", library="Severance (2022)", kind="episode",
                title="Chapter One", year=2022, season_number=1, episode_number=1,
                root_folder="Severance (2022)", file_path="/mnt/Media/x.mkv",
                art_url=None, tmdb_id=1, tvdb_id=None, imdb_id=None,
            )

        async def fetch_item(self, rating_key):
            return _FakePlexItem()

    class _NoArtProvider:
        name = "TMDB"

        async def fetch(self, request):
            return []

    async def handler(request):
        assert "upload" not in str(request.url)
        return httpx.Response(200, content=decodable_png())

    config = load_config(EXAMPLE)
    config.badges.enabled = False
    # This test lets render_artifact run for real, all the way through
    # ImageMagick compositing (the gated-features law: the real entry
    # point, not a stubbed render_artifact) -- the EXAMPLE config's own
    # roots (/assets, /app/assets/overlays, ...) do not exist in the test
    # environment, so it needs the same fixture-backed roots
    # test_pipeline_e2e.py's own ``config`` fixture points at.
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.fonts_root = GOLDEN
    config.overlays_root = GOLDEN

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        plex_generated_base = functools.partial(
            fetch_plex_generated_base, http, _FakePlex(),
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
        )
        results = await pipeline.process_item(
            session, config, http, _FakePlex(), [_NoArtProvider()],
            RenderIntent(kind="episode", title="Chapter One", season_number=1, episode_number=1),
            plex_generated_base=plex_generated_base,
        )

    title_card = next(r for r in results if r.art_kind == "title_card")
    assert title_card.status == "rendered"
    assert title_card.source_mode == "plex_generated"
    assert title_card.source_url == "plex://900/title_card"


async def test_title_card_refusal_is_contained_and_carries_no_token(session, caplog):
    """A Plex body that does not decode raises SourceRefused inside
    fetch_plex_generated_base -> render_artifact, uncaught there. An
    episode's only art kind is title_card (ART_KINDS_FOR["episode"]), so
    process_item's per-kind containment catches it, records the render row
    `failed`, and then -- every kind having refused -- re-raises SourceRefused
    itself, exactly as `test_every_kind_refusing_still_fails_the_job`
    (tests/test_pipeline_e2e.py) proves for a movie's two kinds both
    refusing. The token must appear nowhere: not in the raised exception, not
    in any log record, and not in the committed render row's detail."""
    import functools

    import httpx

    from autoposter.db.models import Render
    from autoposter.render.pipeline import SourceRefused, fetch_plex_generated_base

    class _FakeEntry:
        def __init__(self, rating_key, key):
            self.ratingKey = rating_key
            self.key = key

    class _FakePlexItem:
        def posters(self):
            return [
                _FakeEntry(
                    "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
                    "/library/metadata/900/file?url=media%3A%2F%2F5%2Fx.bundle...",
                ),
            ]

    class _FakePlex:
        capabilities = frozenset({pipeline.CAP_TITLE_CARD_URL})
        async def resolve(self, intent):
            return ResolvedItem(
                server="plex", native_id="900", library="Severance (2022)", kind="episode",
                title="Chapter One", year=2022, season_number=1, episode_number=1,
                root_folder="Severance (2022)", file_path="/mnt/Media/x.mkv",
                art_url=None, tmdb_id=1, tvdb_id=None, imdb_id=None,
            )

        async def fetch_item(self, rating_key):
            return _FakePlexItem()

    class _NoArtProvider:
        name = "TMDB"

        async def fetch(self, request):
            return []

    async def bad_handler(request):
        return httpx.Response(200, content=b"not an image")

    config = load_config(EXAMPLE)
    config.badges.enabled = False

    async with httpx.AsyncClient(transport=httpx.MockTransport(bad_handler)) as http:
        plex_generated_base = functools.partial(
            fetch_plex_generated_base, http, _FakePlex(),
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
        )
        with caplog.at_level("WARNING"):
            with pytest.raises(SourceRefused) as excinfo:
                await pipeline.process_item(
                    session, config, http, _FakePlex(), [_NoArtProvider()],
                    RenderIntent(kind="episode", title="Chapter One", season_number=1, episode_number=1),
                    plex_generated_base=plex_generated_base,
                )

    raised_message = str(excinfo.value)
    assert "tok" not in raised_message
    assert "X-Plex-Token" not in raised_message

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "tok" not in log_text
    assert "X-Plex-Token" not in log_text

    # render_artifact's per-kind row is committed before process_item's
    # aggregate raise (pipeline.py's containment commits render.status =
    # "failed" first, then re-raises once every kind has refused), so it is
    # still there to read back on a fresh query.
    rows = (await session.execute(select(Render))).scalars().all()
    title_card = next(r for r in rows if r.art_kind == "title_card")
    assert title_card.status == "failed"
    assert "the title_card source did not decode after download" in (title_card.detail or "")
    assert "tok" not in (title_card.detail or "")
    assert "X-Plex-Token" not in (title_card.detail or "")


async def test_title_card_non_2xx_from_plex_records_no_art_through_process_item(
    session, caplog,
):
    """M1: a non-2xx from Plex (a rotated token's 401, here) must not fail
    the whole job the way a bad-frame refusal does above -- it is Plex's own
    status, not a bad frame, so ``fetch_plex_generated_base`` swallows it and
    the row records the same ``no_art`` outcome a listing with no media://
    entry would. process_item must complete with no exception, and the
    warning it logs must name the rating key and the status code without
    ever carrying the token."""
    import functools

    import httpx

    from autoposter.db.models import Render
    from autoposter.render.pipeline import fetch_plex_generated_base

    class _FakeEntry:
        def __init__(self, rating_key, key):
            self.ratingKey = rating_key
            self.key = key

    class _FakePlexItem:
        def posters(self):
            return [
                _FakeEntry(
                    "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
                    "/library/metadata/900/file?url=media%3A%2F%2F5%2Fx.bundle...",
                ),
            ]

    class _FakePlex:
        capabilities = frozenset({pipeline.CAP_TITLE_CARD_URL})
        async def resolve(self, intent):
            return ResolvedItem(
                server="plex", native_id="900", library="Severance (2022)", kind="episode",
                title="Chapter One", year=2022, season_number=1, episode_number=1,
                root_folder="Severance (2022)", file_path="/mnt/Media/x.mkv",
                art_url=None, tmdb_id=1, tvdb_id=None, imdb_id=None,
            )

        async def fetch_item(self, rating_key):
            return _FakePlexItem()

    class _NoArtProvider:
        name = "TMDB"

        async def fetch(self, request):
            return []

    async def unauthorized_handler(request):
        return httpx.Response(401, content=b"unauthorized")

    config = load_config(EXAMPLE)
    config.badges.enabled = False

    async with httpx.AsyncClient(transport=httpx.MockTransport(unauthorized_handler)) as http:
        plex_generated_base = functools.partial(
            fetch_plex_generated_base, http, _FakePlex(),
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
        )
        with caplog.at_level("WARNING"):
            results = await pipeline.process_item(
                session, config, http, _FakePlex(), [_NoArtProvider()],
                RenderIntent(kind="episode", title="Chapter One", season_number=1, episode_number=1),
                plex_generated_base=plex_generated_base,
            )

    title_card = next(r for r in results if r.art_kind == "title_card")
    assert title_card.status == "no_art"
    assert title_card.detail == "no title_card art on any provider"

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "900" in log_text
    assert "401" in log_text
    assert "tok" not in log_text
    assert "X-Plex-Token" not in log_text

    rows = (await session.execute(select(Render))).scalars().all()
    title_card_row = next(r for r in rows if r.art_kind == "title_card")
    assert title_card_row.status == "no_art"
