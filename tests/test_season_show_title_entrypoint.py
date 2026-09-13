"""Roadmap row 78 through ``process_item``, the real entry point.

The law this file exists for: a gated feature gets ONE test through the path
production actually takes, because a helper test can pass while the wired path
differs. This ledger has earned that three times.

The compositor is stubbed -- ``compositor.run`` and ``fit_point_size`` both,
exactly as ``tests/test_pipeline.py`` stubs them -- so nothing shells out to
``magick`` and NOTHING here carries ``@pytest.mark.imagemagick``. CI's main
pytest step runs ``-m "not imagemagick and not deep"`` on a runner with no
binary; a marked non-compositing test would fail there and nowhere else.

Badges are disabled: the badge stack is a separate engine with its own
fingerprint and its own pinned literals, and this file is about the RENDER
fingerprint.
"""
from pathlib import Path

import httpx
import pytest
from conftest import decodable_png
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db.models import Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import process_item
from autoposter.render.textfit import FitResult
from autoposter.servers.registry import Servers

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config(tmp_path):
    cfg = load_config(EXAMPLE)
    cfg.assets_root = tmp_path / "assets"
    cfg.manual_assets_root = tmp_path / "manual"
    cfg.backup_root = tmp_path / "backup"
    cfg.fonts_root = tmp_path / "fonts"
    cfg.overlays_root = tmp_path / "overlays"
    cfg.badges.enabled = False
    cfg.operations.enabled = False
    return cfg


class _Plex:
    def __init__(self, item):
        self._item = item

    async def resolve(self, intent):
        return self._item

    async def fetch_item(self, rating_key):
        raise AssertionError("badges are disabled; nothing may fetch a live item")


class _Provider:
    name = "TMDB"

    async def fetch(self, request):
        if request.art_kind == "logo":
            return []
        return [ArtCandidate("TMDB", f"https://img/{request.art_kind}.jpg",
                             None, 2000, 3000, 5.0)]


def _http():
    async def handler(request):
        return httpx.Response(200, content=decodable_png())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _stub_magick(monkeypatch):
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: None)
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )


SEASON = ResolvedItem(
    server="plex", native_id="556", library="TV Shows", kind="season", title="Season 2",
    year=None, season_number=2, episode_number=None,
    root_folder="Severance (2022)", file_path=None, art_url=None,
    tmdb_id=None, tvdb_id=371980, imdb_id=None,
    parent_native_id="555", show_title="Severance",
)
SEASON_INTENT = RenderIntent(
    kind="season", title="Severance", tvdb_id=371980, season_number=2,
)
MOVIE = ResolvedItem(
    server="plex", native_id="12345", library="Movies", kind="movie",
    title="Dune: Part Two",
    year=2024, season_number=None, episode_number=None,
    root_folder="Dune Part Two (2024)",
    file_path="/mnt/Media/Movies/Dune Part Two (2024)/x.mkv",
    art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
)
MOVIE_INTENT = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)


async def _fingerprints(session, config, item, intent, monkeypatch):
    _stub_magick(monkeypatch)
    async with _http() as http:
        await process_item(session, config, http, Servers({"plex": _Plex(item)}), [_Provider()], intent)
    rows = (await session.execute(select(Render))).scalars().all()
    return {r.art_kind: r.fingerprint for r in rows}


async def test_the_gate_off_leaves_the_season_posters_fingerprint_where_it_was(
    session, config, monkeypatch,
):
    """(a) GATE OFF: the season poster's fingerprint is what it would have been
    before this row.

    ``show_title`` unset means ``title_text_for`` returns ``None`` for the
    secondary, so ``text_inputs`` and ``asset_hashes`` are byte-identical to
    today's -- and the season_poster render version is the only thing that
    moved, which is what the ``tests/test_season_show_title.py`` literals pin.
    Here the property asserted is the LOCAL one: a second pass over the same
    item with the gate still off is `unchanged`, so the row that lands on a
    live server settles after exactly one re-render rather than churning.
    """
    assert config.artwork.season_poster.show_title.add_text is False
    first = await _fingerprints(session, config, SEASON, SEASON_INTENT, monkeypatch)
    assert set(first) == {"season_poster"}

    _stub_magick(monkeypatch)
    async with _http() as http:
        renders = await process_item(
            session, config, http, Servers({"plex": _Plex(SEASON)}), [_Provider()], SEASON_INTENT,
        )
    assert [r.detail for r in renders] == ["unchanged"]
    assert {r.art_kind: r.fingerprint for r in renders} == first


async def test_the_gate_on_moves_the_season_poster_and_leaves_a_movie_poster_alone(
    session, config, monkeypatch,
):
    """(b) GATE ON: it fires where it should and only where it should.

    The season poster's fingerprint moves, because the show title is now one
    of its text inputs and the block's font is one of its asset hashes. The
    movie's poster and background do not, because ``show_title_for`` refuses
    every art kind but ``season_poster`` and ``ResolvedItem.show_title`` is
    ``None`` on a movie anyway. Both halves through the real entry point, in
    one test, because "it moved" and "it moved only there" are the two
    assertions this row's storm argument actually rests on.
    """
    before_season = await _fingerprints(
        session, config, SEASON, SEASON_INTENT, monkeypatch
    )
    before_movie = await _fingerprints(
        session, config, MOVIE, MOVIE_INTENT, monkeypatch
    )
    assert set(before_movie) >= {"poster", "background"}

    config.artwork.season_poster.show_title.add_text = True

    after_season = await _fingerprints(
        session, config, SEASON, SEASON_INTENT, monkeypatch
    )
    after_movie = await _fingerprints(
        session, config, MOVIE, MOVIE_INTENT, monkeypatch
    )

    assert after_season["season_poster"] != before_season["season_poster"]
    for art_kind in ("poster", "background"):
        assert after_movie[art_kind] == before_movie[art_kind], (
            f"a movie's {art_kind} moved for a season-poster-only key"
        )


class _NoSeasonArtProvider:
    """Nothing for a season poster, a poster for everything else.

    Drives roadmap row 132's show-poster fallback: when the season ladder
    comes back empty, ``render_artifact`` re-issues the request as a POSTER
    with the season and episode numbers dropped, re-ranks it against the
    poster language order, and records ``source_mode = "show_fallback"``.
    """

    name = "TMDB"

    async def fetch(self, request):
        if request.art_kind in ("season_poster", "logo"):
            return []
        return [ArtCandidate("TMDB", "https://img/poster.jpg", None, 2000, 3000, 5.0)]


async def test_the_show_title_still_draws_on_a_show_fallback_base(
    session, config, monkeypatch,
):
    """Global Constraint 8's one un-pinned clause, pinned.

    Facts C6 names row 132's ``show_fallback`` the only fallback interaction
    this row has, and the ruling is that the block DRAWS: the base is still
    just a base image, and a special case here would be the fourth precedence
    rule this pipeline does not have. Drawing the show's title onto the show's
    own poster is arguably redundant and defensibly correct -- the point of
    this test is that the answer is a DECISION with a pin behind it, not an
    accident of which branch happened to run.

    Asserted on the rendered argv rather than on a fingerprint, because the
    fingerprint moving proves only that something changed.
    """
    config.artwork.season_poster.show_title.add_text = True
    calls: list[list[str]] = []
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: calls.append(argv))
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )

    async with _http() as http:
        renders = await process_item(
            session, config, http, Servers({"plex": _Plex(SEASON)}), [_NoSeasonArtProvider()],
            SEASON_INTENT,
        )

    assert [r.source_mode for r in renders] == ["show_fallback"], (
        "the fallback did not fire, so this test proved nothing about it"
    )
    captions = [
        str(t)[len("caption:"):]
        for argv in calls for t in argv
        if str(t).startswith("caption:")
    ]
    assert captions == ["SEASON 2", "SEVERANCE"]
