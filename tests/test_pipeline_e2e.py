from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db.models import Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render.pipeline import process_item

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
