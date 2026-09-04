"""Roadmap row 111's per-art-kind version, through the REAL entry point.

`tests/test_config.py` proves `render_version_for` partitions correctly. Only
`process_item` proves that the value reaching `compute_fingerprint` is the
per-kind one -- and only `process_item` can show the row's actual promise: a
season-poster edit that leaves a movie's two stored fingerprints
byte-identical while the season's moves and re-renders.

This repository has twice shipped a gated feature whose helper tests passed
while the wired path differed. That is why this file exists rather than
another round of assertions about the hash function.

No `@pytest.mark.imagemagick` anywhere here, deliberately: every assertion is
over hash strings, and `compositor.run`/`fit_point_size` are stubbed exactly
the way `tests/test_pipeline_logo_guard.py:148-170` stubs them. CI's main run
is `-m "not imagemagick and not deep"` on a runner with no `magick` binary, so
a marked test in this file would be green locally and never run in CI.
"""
from pathlib import Path

import httpx
from conftest import decodable_png
from sqlalchemy import select

from autoposter.api.routes import _render_affecting
from autoposter.config.loader import load_config, moved_kinds, render_version
from autoposter.db.models import Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import process_item
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

MOVIE = ResolvedItem(
    rating_key="m1", library="Movies", kind="movie", title="A Movie", year=1999,
    season_number=None, episode_number=None, root_folder="A Movie (1999)",
    file_path=None, art_url=None, tmdb_id=550, tvdb_id=None, imdb_id=None,
)
SEASON = ResolvedItem(
    rating_key="s1", library="TV Shows", kind="season", title="A Show", year=1999,
    season_number=1, episode_number=None, root_folder="A Show (1999)",
    file_path=None, art_url=None, tmdb_id=1399, tvdb_id=None, imdb_id=None,
)
MOVIE_INTENT = RenderIntent(kind="movie", title="A Movie", tmdb_id=550)
SEASON_INTENT = RenderIntent(kind="season", title="A Show", tmdb_id=1399, season_number=1)


def _config(tmp_path):
    """The example config rooted in tmp_path, with both Plex-touching stages
    off: this file is about which version string reaches a fingerprint, and
    neither operations nor badges says anything about that."""
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    config.operations.enabled = False
    config.badges.enabled = False
    return config


class _Plex:
    """Resolves each intent to its fixed item. Operations and badges are off,
    so nothing may fetch a live Plex object."""

    async def resolve(self, intent):
        return MOVIE if intent.kind == "movie" else SEASON

    async def fetch_item(self, rating_key):
        raise AssertionError(
            "operations and badges are off in this file; nothing may fetch a "
            "live Plex object"
        )


class _Provider:
    """One candidate per art kind, at a URL naming that kind, so the fake CDN
    below can answer each without the tests caring which order they run in."""

    name = "TMDB"

    async def fetch(self, request):
        if request.art_kind == "logo":
            return []
        return [ArtCandidate("TMDB", f"https://img/{request.art_kind}.jpg", "en", 2000, 3000, 5.0)]


def _http():
    png = decodable_png()

    async def handler(request):
        return httpx.Response(200, content=png)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _stub_compositor(monkeypatch):
    """Every ImageMagick call on this path, silenced.

    `compositor.run` is the subprocess and `fit_point_size` is the only other
    function here that shells out. Recorded rather than merely stubbed so a
    test can assert a composite did NOT happen.
    """
    composites: list = []
    monkeypatch.setattr(
        pipeline_module.compositor, "run", lambda argv: composites.append(argv)
    )
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    return composites


async def _pass(session, config) -> dict[str, tuple[str, str | None]]:
    """One process_item over the movie and the season.

    Answers {art_kind: (fingerprint, detail)} -- the three rows the two
    intents imply, keyed by art kind because no two of them share one.
    """
    async with _http() as http:
        for intent in (MOVIE_INTENT, SEASON_INTENT):
            await process_item(session, config, http, _Plex(), [_Provider()], intent)
    rows = (await session.execute(select(Render))).scalars().all()
    return {row.art_kind: (row.fingerprint, row.detail) for row in rows}


async def test_a_season_poster_edit_moves_only_the_season_s_stored_fingerprint(
    session, tmp_path, monkeypatch
):
    """THE ROW, at the only place it is observable.

    Before row 111 all three fingerprints moved on this edit and the whole
    library re-rendered. `render_version_for` reaches `compute_fingerprint`'s
    element 0 through `render_artifact`, so now the season's moves and the
    movie's two are byte-identical -- and the movie's renders report
    `detail == "unchanged"`, which is the pipeline saying it did no work
    rather than the test inferring it.
    """
    _stub_compositor(monkeypatch)
    config = _config(tmp_path)
    before = await _pass(session, config)
    assert set(before) == {"poster", "background", "season_poster"}

    config.artwork.season_poster.text.max_point_size += 1
    # `build_config` is the only production path that derives this; a test
    # that mutates the model in place has to do it by hand, and the dual-read
    # grandfather (Task 4) reads it.
    config.version = render_version(config)

    after = await _pass(session, config)
    assert after["season_poster"][0] != before["season_poster"][0]
    assert after["poster"][0] == before["poster"][0]
    assert after["background"][0] == before["background"][0]
    assert after["poster"][1] == "unchanged"
    assert after["background"][1] == "unchanged"


async def test_a_global_input_edit_moves_every_kind_s_stored_fingerprint(
    session, tmp_path, monkeypatch
):
    """The converse, and it is the correct behaviour rather than a leak.

    `artwork.output_quality` is passed to `build_base_argv` on every kind
    (render/pipeline.py:855), so it is a literal member of all four payloads
    and still costs the whole library. Without this test the partition could
    silently drop a shared input and nothing would notice until artwork
    stopped matching the config.
    """
    _stub_compositor(monkeypatch)
    config = _config(tmp_path)
    before = await _pass(session, config)

    config.artwork.output_quality = "88%"
    config.version = render_version(config)

    after = await _pass(session, config)
    for art_kind in before:
        assert after[art_kind][0] != before[art_kind][0], art_kind


def test_moved_kinds_names_only_the_kinds_an_edit_touched():
    """What `_render_affecting` and the preview both need, as a plain helper
    rather than a field on Config: a version MAP on the config would grow
    COMPUTED_PATHS, change what GET /api/config serves, and turn the Settings
    page's one-line "version A to B" into a four-way display."""
    before = load_config(EXAMPLE)
    after = load_config(EXAMPLE)
    after.artwork.title_card.season_label = "Kausi"
    assert moved_kinds(before, after) == {"title_card"}


def test_moved_kinds_is_empty_when_nothing_render_affecting_moved():
    before = load_config(EXAMPLE)
    after = load_config(EXAMPLE)
    after.scheduler.drift_batch_size = 250
    assert moved_kinds(before, after) == set()


def test_render_affecting_still_fires_on_skip_tba_alone():
    """`skip_tba` is a GATE and is deliberately outside every version -- it
    changes which rows are examined without moving a fingerprint. It stays a
    separate term, or a preview of that edit reports "no re-renders" while the
    denominator it would have shown moves under the operator."""
    before = load_config(EXAMPLE)
    after = load_config(EXAMPLE)
    after.skip_tba = not before.skip_tba
    after.version = render_version(after)
    assert moved_kinds(before, after) == set()
    assert _render_affecting(before, after) is True


def test_render_affecting_short_circuits_on_the_wholesale_version():
    """C1's reason for keeping `config.version`: it covers a strict superset
    of every per-kind payload, so an edit that does not move it cannot move
    any kind's version, and the eight per-kind hashes are skipped."""
    before = load_config(EXAMPLE)
    after = load_config(EXAMPLE)
    after.workers = before.workers + 1
    after.version = render_version(after)
    assert after.version == before.version
    assert _render_affecting(before, after) is False
    # And the short-circuit is not hiding a real move: computed the long way,
    # nothing moved either. If these two ever disagreed, the cheap check would
    # be swallowing re-renders rather than skipping eight hashes.
    assert moved_kinds(before, after) == set()
