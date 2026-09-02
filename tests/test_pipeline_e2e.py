from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db.models import Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render.pipeline import SourceRefused, process_item

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
GOLDEN = Path(__file__).parent / "fixtures" / "golden"

# Needs a real `magick` and the harvested fixtures. The `imagemagick` fixture
# in conftest.py decides what a missing one means: a skip locally, a failure
# in CI. The marker also selects these tests -- the main CI run deselects them
# with `-m "not imagemagick"` and a later step runs them where a `magick`
# exists, so a skip here can no longer pass for a pass.
pytestmark = pytest.mark.imagemagick


class FakePlexItem:
    """The live object the badge stage fetches, carrying only what
    media_info_from_plex() reads. `.media` is populated because its absence
    makes that function call reload(), a blocking request to a Plex server
    no test has."""

    def __init__(self, file_path):
        part = type("Part", (), {"streams": [], "file": file_path})()
        self.media = [type("Media", (), {"parts": [part], "videoResolution": "1080",
                                         "audioCodec": "eac3", "audioChannels": 6})()]
        self.duration = 4845912
        self.seasonNumber = None
        self.episodeNumber = None


class FakePlex:
    def __init__(self, item):
        self._item = item

    async def resolve(self, intent):
        return self._item

    async def fetch_item(self, rating_key):
        return FakePlexItem(self._item.file_path)


class FakeProvider:
    name = "TMDB"

    def __init__(self, url):
        self._url = url

    async def fetch(self, request):
        return [ArtCandidate("TMDB", self._url, None, 2000, 3000, 5.0)]


class KindAwareProvider:
    """A provider whose candidate URL depends on the art kind being fetched
    -- FakeProvider above always answers the same URL regardless of kind, and
    the per-kind containment tests need one source that decodes and one that
    does not, in the same call to process_item."""

    name = "TMDB"

    def __init__(self, urls: dict[str, str]):
        self._urls = urls

    async def fetch(self, request):
        url = self._urls.get(request.art_kind)
        if url is None:
            return []
        return [ArtCandidate("TMDB", url, None, 2000, 3000, 5.0)]


@pytest.fixture
def config(tmp_path):
    cfg = load_config(EXAMPLE)
    cfg.assets_root = tmp_path / "assets"
    cfg.manual_assets_root = tmp_path / "manual"
    cfg.fonts_root = GOLDEN
    cfg.overlays_root = GOLDEN
    return cfg


async def test_movie_intent_writes_poster_and_background(config, session, tmp_path):
    source = GOLDEN / "source_textless.jpg"

    async def handler(request):
        return httpx.Response(200, content=source.read_bytes())

    item = ResolvedItem(
        rating_key="12345", library="Movies", kind="movie", title="Dune: Part Two",
        year=2024, season_number=None, episode_number=None,
        root_folder="Dune Part Two (2024)",
        file_path="/mnt/Media/Movies/Dune Part Two (2024)/x.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        renders = await process_item(
            session, config, http, FakePlex(item),
            [FakeProvider("https://image.tmdb.org/t/p/original/x.jpg")],
            RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134),
        )

    assert {r.art_kind for r in renders} == {"poster", "background"}
    assert all(r.status == "rendered" for r in renders)
    poster = config.assets_root / "Movies" / "Dune Part Two (2024)" / "poster.jpg"
    assert poster.exists()
    assert poster.stat().st_size > 0

    # The badge stage swallows its own failures, so "no exception" proves
    # nothing about it -- assert it ran. The example config badges but does
    # not upload, so the poster is composed and the upload is skipped.
    poster_render = next(r for r in renders if r.art_kind == "poster")
    assert poster_render.badge_fingerprint is not None
    assert poster_render.upload_status == "skipped"


async def test_second_run_is_a_no_op(config, session):
    source = GOLDEN / "source_textless.jpg"

    async def handler(request):
        return httpx.Response(200, content=source.read_bytes())

    item = ResolvedItem(
        rating_key="12345", library="Movies", kind="movie", title="Dune: Part Two",
        year=2024, season_number=None, episode_number=None,
        root_folder="Dune Part Two (2024)",
        file_path="/mnt/Media/Movies/Dune Part Two (2024)/x.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
    )
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)
    providers = [FakeProvider("https://image.tmdb.org/t/p/original/x.jpg")]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await process_item(session, config, http, FakePlex(item), providers, intent)
        poster = config.assets_root / "Movies" / "Dune Part Two (2024)" / "poster.jpg"
        first_mtime = poster.stat().st_mtime_ns
        renders = await process_item(session, config, http, FakePlex(item), providers, intent)

    assert poster.stat().st_mtime_ns == first_mtime
    assert all(r.detail == "unchanged" for r in renders)


async def test_no_art_records_the_reason_without_writing(config, session):
    class Empty:
        name = "TMDB"

        async def fetch(self, request):
            return []

    item = ResolvedItem(
        rating_key="99", library="Movies", kind="movie", title="Obscure Film",
        year=1970, season_number=None, episode_number=None, root_folder="Obscure (1970)",
        file_path="/mnt/Media/Movies/Obscure (1970)/x.mkv", art_url=None,
        tmdb_id=1, tvdb_id=None, imdb_id=None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as http:
        renders = await process_item(
            session, config, http, FakePlex(item), [Empty()],
            RenderIntent(kind="movie", title="Obscure Film", tmdb_id=1),
        )
    assert all(r.status == "no_art" for r in renders)
    assert not (config.assets_root / "Movies" / "Obscure (1970)" / "poster.jpg").exists()
    rows = (await session.execute(select(Render))).scalars().all()
    assert {r.status for r in rows} == {"no_art"}


def _movie_item(rating_key, title="Identity Fork Movie", tmdb_id=1):
    return ResolvedItem(
        rating_key=rating_key, library="Movies", kind="movie", title=title,
        year=2024, season_number=None, episode_number=None,
        root_folder=f"{title} (2024)",
        file_path=f"/mnt/Media/Movies/{title} (2024)/x.mkv",
        art_url=None, tmdb_id=tmdb_id, tvdb_id=None, imdb_id=None,
    )


async def test_identity_fork_logs_when_resolve_returns_a_different_key(config, session, caplog):
    """render/pipeline.py, right after `plex.resolve` (roadmap: the
    unscorable-floor investigation, mechanism M1a): resolve() treats
    `intent.rating_key` as a hint and is free to return a DIFFERENT key --
    the live copy's, after a re-match or a library rebuild renumbers the item
    -- silently, until now. The served surfaces stay class-name-only
    everywhere in this queue; this is a WARNING in the pod log only, naming
    both keys and the title, so a silent 390-row hole becomes a grep."""
    source = GOLDEN / "source_textless.jpg"

    async def handler(request):
        return httpx.Response(200, content=source.read_bytes())

    item = _movie_item("12345")
    # The intent's hint (a stale key resolve() refused) differs from what
    # `item` -- what resolve() actually returned -- carries.
    intent = RenderIntent(kind="movie", title="Identity Fork Movie", tmdb_id=1, rating_key="99999")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with caplog.at_level("WARNING", logger="autoposter.render.pipeline"):
            await process_item(
                session, config, http, FakePlex(item), [FakeProvider("https://x/y.jpg")], intent,
            )

    forks = [r for r in caplog.records if "differs from the intent's" in r.message]
    assert len(forks) == 1
    assert "12345" in forks[0].message
    assert "99999" in forks[0].message
    assert "Identity Fork Movie" in forks[0].message


async def test_no_identity_fork_log_when_the_resolved_key_matches(config, session, caplog):
    source = GOLDEN / "source_textless.jpg"

    async def handler(request):
        return httpx.Response(200, content=source.read_bytes())

    item = _movie_item("12345")
    intent = RenderIntent(kind="movie", title="Identity Fork Movie", tmdb_id=1, rating_key="12345")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with caplog.at_level("WARNING", logger="autoposter.render.pipeline"):
            await process_item(
                session, config, http, FakePlex(item), [FakeProvider("https://x/y.jpg")], intent,
            )

    assert not any("differs from the intent's" in r.message for r in caplog.records)


async def test_no_identity_fork_log_when_the_intent_carries_no_hint(config, session, caplog):
    """A fresh item this queue has never resolved before carries no
    `rating_key` hint at all -- nothing to compare against, so nothing to
    warn about."""
    source = GOLDEN / "source_textless.jpg"

    async def handler(request):
        return httpx.Response(200, content=source.read_bytes())

    item = _movie_item("12345")
    intent = RenderIntent(kind="movie", title="Identity Fork Movie", tmdb_id=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with caplog.at_level("WARNING", logger="autoposter.render.pipeline"):
            await process_item(
                session, config, http, FakePlex(item), [FakeProvider("https://x/y.jpg")], intent,
            )

    assert not any("differs from the intent's" in r.message for r in caplog.records)


async def test_a_refused_kind_does_not_abort_its_sibling(config, session):
    """render/pipeline.py's per-kind containment (roadmap: the
    unscorable-floor investigation, mechanism M2): a source that raises
    SourceRefused (job 40478's "Inside Out 2" clearlogo incident, or here a
    poster source that will not decode) used to abort the whole artifact
    loop, costing the item its background too. It must not: the background
    still renders and scores, and the poster's own row records the refusal
    -- `status="failed"`, the reason in `detail` -- instead of being left at
    whatever it was before the press, unscored forever."""
    good = GOLDEN / "source_textless.jpg"

    async def handler(request):
        if "poster" in str(request.url):
            return httpx.Response(200, content=b"not an image")
        return httpx.Response(200, content=good.read_bytes())

    item = _movie_item("55555", title="Corrupt Poster")
    provider = KindAwareProvider({
        "poster": "https://image.tmdb.org/t/p/original/poster.jpg",
        "background": "https://image.tmdb.org/t/p/original/background.jpg",
    })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        renders = await process_item(
            session, config, http, FakePlex(item), [provider],
            RenderIntent(kind="movie", title="Corrupt Poster", tmdb_id=1),
        )

    poster = next(r for r in renders if r.art_kind == "poster")
    background = next(r for r in renders if r.art_kind == "background")
    assert poster.status == "failed"
    assert "did not decode" in poster.detail
    assert background.status == "rendered"
    # `quality_scored_at` is a SQL `now()`, so the poster's later commit
    # leaves it expired on this instance -- reading it would be a lazy load
    # (a MissingGreenlet under asyncio, not a query). The refresh is what
    # makes the database's own stamp readable here (test_pipeline_quality.py
    # establishes this precedent).
    await session.refresh(background)
    assert background.quality_scored_at is not None


async def test_a_non_first_kind_refusing_still_badges_the_earlier_kind(config, session):
    """The discriminating test roadmap review flags as M2: SQLAlchemy's
    `rollback()` expires the ENTIRE identity map (`dirty_only=False`,
    independent of `expire_on_commit`, which this app sets False) -- not just
    the just-refused kind's own row. Refusing a NON-FIRST kind (poster
    renders fine, background refuses) used to leave the poster's own
    `Render` object -- already committed and sitting in `results` -- expired
    when the badge loop later read `render.art_kind`, a plain attribute
    access outside greenlet context: a MissingGreenlet, silently swallowed by
    the badge stage's own `except Exception`. The whole item would silently
    lose its badge upkeep, on every pass, because the refusal is
    deterministic. `test_a_refused_kind_does_not_abort_its_sibling` above
    refuses the FIRST kind (poster), where `results` is still empty at the
    rollback -- it cannot reach this."""
    good = GOLDEN / "source_textless.jpg"

    async def handler(request):
        if "background" in str(request.url):
            return httpx.Response(200, content=b"not an image")
        return httpx.Response(200, content=good.read_bytes())

    item = _movie_item("77777", title="Corrupt Background")
    provider = KindAwareProvider({
        "poster": "https://image.tmdb.org/t/p/original/poster3.jpg",
        "background": "https://image.tmdb.org/t/p/original/background3.jpg",
    })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        renders = await process_item(
            session, config, http, FakePlex(item), [provider],
            RenderIntent(kind="movie", title="Corrupt Background", tmdb_id=3),
        )

    poster = next(r for r in renders if r.art_kind == "poster")
    background = next(r for r in renders if r.art_kind == "background")
    assert poster.status == "rendered"
    assert background.status == "failed"
    # The example config badges (but does not upload); asserting no exception
    # proves nothing, since the badge stage swallows its own failures -- this
    # asserts the stage actually ran for the poster rather than dying on a
    # MissingGreenlet before it got there.
    assert poster.badge_fingerprint is not None


async def test_every_kind_refusing_still_fails_the_job(config, session):
    """If NO kind produced anything, the job must still fail so the operator
    sees it on Failures -- the one place per-kind containment must not go all
    the way, or a total refusal reads as an ordinary `done` with nothing
    rendered and nothing to show for it."""
    async def handler(request):
        return httpx.Response(200, content=b"not an image")

    item = _movie_item("66666", title="All Corrupt")
    provider = KindAwareProvider({
        "poster": "https://image.tmdb.org/t/p/original/poster2.jpg",
        "background": "https://image.tmdb.org/t/p/original/background2.jpg",
    })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SourceRefused):
            await process_item(
                session, config, http, FakePlex(item), [provider],
                RenderIntent(kind="movie", title="All Corrupt", tmdb_id=2),
            )

    rows = (await session.execute(select(Render))).scalars().all()
    assert {r.status for r in rows} == {"failed"}
