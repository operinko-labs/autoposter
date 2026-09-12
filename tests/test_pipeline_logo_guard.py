"""The clearlogo guard, through the real ``render_artifact`` and ``process_item``.

Production, 2026-09-04: TMDB served a 32000x18839 clearlogo (602,848,000px)
for 'Inside Out 2'. ``providers/ladder.rank_key`` sorts on ``-pixels``, so the
bomb was the ladder's FIRST answer on every pass; ``_validate_image``'s
64,000,000px ceiling then refused it, ``_download`` raised ``SourceRefused``,
and the WHOLE POSTER was refused -- on every visit, until an operator picked a
smaller logo by hand.

Three rules are pinned here:

* a candidate whose provider-REPORTED dimensions are over the ceiling is never
  downloaded at all -- the guard reads the same numbers the ladder ranked by;
* a candidate that IS downloaded and then refused is skipped and the ladder is
  re-asked with that URL excluded, and with nothing usable left the poster
  renders WITHOUT a logo. The refusal outcome stays for the BASE image only: a
  missing logo is a styling difference, a missing base image is no artwork;
* a poster whose logo is under the ceiling and decodes is byte-identical --
  one ladder question, the same pick, the same fingerprint. That is the storm
  guard: a fingerprint that moved for a healthy row would re-render the
  library.

Every render test here drives ``render_artifact`` or ``process_item``, never a
helper in isolation: a helper test would prove the guard can be computed and
say nothing about whether the shipped path uses it, which is exactly how a
gated feature has twice passed its own tests in this tree.
"""
import hashlib
import logging
from pathlib import Path

import httpx
from conftest import decodable_png

from autoposter.config.loader import load_config, render_version_for
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import (
    compute_fingerprint, gather_fingerprint_inputs, render_artifact,
)
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

POSTER_URL = "https://img/poster.jpg"
BOMB_URL = "https://img/bomb.png"
GOOD_LOGO_URL = "https://img/logo.png"
BAD_LOGO_URL = "https://img/corrupt.png"

# The production bomb's own dimensions: 32000 * 18839 = 602,848,000px, nearly
# ten times pipeline._ARTWORK_MAX_PIXELS.
BOMB_WIDTH, BOMB_HEIGHT = 32000, 18839


def _config(tmp_path):
    """The example config, rooted in ``tmp_path``, with both Plex-touching
    stages off: this file is about which bytes are fetched and composited,
    and neither operations nor badges says anything about that."""
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    config.operations.enabled = False
    config.badges.enabled = False
    return config


def item():
    return ResolvedItem(
        server="plex", native_id="1", library="Movies", kind="movie",
        title="Inside Out 2",
        year=2024, season_number=None, episode_number=None,
        root_folder="Inside Out 2 (2024)",
        file_path="/mnt/Media/Movies/Inside Out 2 (2024)/x.mkv", art_url=None,
        tmdb_id=1022789, tvdb_id=None, imdb_id="tt22022452",
        parent_native_id=None,
    )


class _FakePlex:
    """Answers ``resolve`` with a fixed item. Operations and badges are off in
    this file, so nothing may fetch a live Plex object."""

    def __init__(self, resolved):
        self._resolved = resolved

    async def resolve(self, intent):
        return self._resolved

    async def fetch_item(self, rating_key):
        raise AssertionError(
            "operations and badges are off in this file; nothing may fetch a "
            "live Plex object"
        )


class _Provider:
    """A poster candidate always, and the logo candidates the test names.

    ``name`` is a class attribute on every real client, so ``_provider_rank``
    reads exactly this. ``requests`` records every ``ArtRequest``, which is how
    the tests count how many times the ladder was asked.
    """

    name = "TMDB"

    def __init__(self, logos):
        self._logos = logos
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if request.art_kind == "poster":
            return [ArtCandidate("TMDB", POSTER_URL, "en", 2000, 3000, 5.0)]
        if request.art_kind == "logo":
            return list(self._logos)
        return []


def _logo(url, *, width=800, height=300, score=5.0):
    # "en", never None: the example config's artwork.logo_language_order is
    # [en, fi], and language_rank drops an untagged candidate as UNRANKED.
    return ArtCandidate("TMDB", url, "en", width, height, score)


def _logo_requests(provider):
    return [request for request in provider.requests if request.art_kind == "logo"]


def _http(bodies):
    """A CDN answering each URL with the bytes ``bodies`` names.

    A URL the map does not hold raises rather than 404s: every test here is
    about WHICH urls are fetched, and a silent miss would read as a pass.
    """
    async def handler(request):
        url = str(request.url)
        if url not in bodies:
            raise AssertionError(f"unexpected download: {url}")
        return httpx.Response(200, content=bodies[url])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _stub_imagemagick(monkeypatch):
    """Record every logo composite without invoking ImageMagick.

    ``fit_point_size`` is the only other function on this path that shells
    out, so it is stubbed too -- the same shape ``tests/test_pipeline.py``'s
    ``_stub_out_imagemagick`` uses, and for the same reason: this file's
    claims are about downloads and compositing decisions, not pixels.
    """
    logo_calls: list = []
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: None)
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    original = pipeline_module.compositor.build_logo_argv

    def spy(*args, **kwargs):
        logo_calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline_module.compositor, "build_logo_argv", spy)
    return logo_calls


async def test_a_candidate_over_the_ceiling_is_never_downloaded(
    session, tmp_path, monkeypatch
):
    """The bomb by metadata. ``rank_key`` sorts on ``-pixels``, so the biggest
    candidate is the ladder's first answer; the guard reads the SAME
    dimensions the ladder ranked by and drops it before a byte is fetched.
    The fake CDN has no entry for the bomb's URL, so a download would raise."""
    logo_calls = _stub_imagemagick(monkeypatch)
    provider = _Provider([_logo(BOMB_URL, width=BOMB_WIDTH, height=BOMB_HEIGHT)])

    async with _http({POSTER_URL: decodable_png()}) as http:
        render = await render_artifact(
            session, _config(tmp_path), http, item(), "poster", [provider],
        )

    assert render.status == "rendered"
    assert logo_calls == [], "the bomb was composited"


async def test_a_candidate_with_unknown_dimensions_is_still_downloaded(
    session, tmp_path, monkeypatch
):
    """TMDB omits width/height on some entries and Fanart's ``_int_or_none``
    answers None for a non-numeric one. Unknown is not "too big": such a
    candidate must reach the download exactly as it does today, where the
    full decode settles it."""
    logo_calls = _stub_imagemagick(monkeypatch)
    provider = _Provider([_logo(GOOD_LOGO_URL, width=None, height=None)])

    async with _http({POSTER_URL: decodable_png(), GOOD_LOGO_URL: decodable_png()}) as http:
        render = await render_artifact(
            session, _config(tmp_path), http, item(), "poster", [provider],
        )

    assert render.status == "rendered"
    assert len(logo_calls) == 1


async def test_a_candidate_that_fails_to_decode_is_skipped_and_the_next_used(
    session, tmp_path, monkeypatch
):
    """The bomb by bytes -- a header that lies, which only a full decode
    catches (``_validate_image``'s own docstring). The ladder is asked again
    with the refused URL excluded, and the second candidate is composited."""
    logo_calls = _stub_imagemagick(monkeypatch)
    provider = _Provider([
        _logo(BAD_LOGO_URL, score=9.0), _logo(GOOD_LOGO_URL, score=1.0),
    ])

    async with _http({
        POSTER_URL: decodable_png(),
        BAD_LOGO_URL: b"not an image at all",
        GOOD_LOGO_URL: decodable_png(),
    }) as http:
        render = await render_artifact(
            session, _config(tmp_path), http, item(), "poster", [provider],
        )

    assert render.status == "rendered"
    assert len(logo_calls) == 1, "the poster was rendered without a logo"
    assert str(logo_calls[0][2]).endswith(".png")
    assert len(_logo_requests(provider)) == 2, (
        "the ladder was not re-asked with the refused candidate excluded"
    )


async def test_every_candidate_failing_renders_the_poster_without_a_logo(
    session, tmp_path, monkeypatch, caplog
):
    """The production outcome that must change. The poster is RENDERED, not
    refused: the refusal stays for the BASE image only. One WARNING names the
    item and how many candidates were skipped, and carries no URL."""
    logo_calls = _stub_imagemagick(monkeypatch)
    provider = _Provider([
        _logo(BAD_LOGO_URL, score=9.0), _logo(GOOD_LOGO_URL, score=1.0),
    ])

    with caplog.at_level(logging.WARNING):
        async with _http({
            POSTER_URL: decodable_png(),
            BAD_LOGO_URL: b"not an image at all",
            GOOD_LOGO_URL: b"also not an image",
        }) as http:
            render = await render_artifact(
                session, _config(tmp_path), http, item(), "poster", [provider],
            )

    assert render.status == "rendered"
    assert render.detail is None
    assert logo_calls == []
    summary = next(
        record.getMessage() for record in caplog.records
        if "no usable clearlogo" in record.getMessage()
    )
    assert "Inside Out 2" in summary
    assert "2 candidate(s)" in summary
    assert "https://" not in summary, "a provider URL reached the log line"


async def test_the_job_completes_with_a_rendered_poster_through_process_item(
    session, tmp_path, monkeypatch
):
    """Through the wired entry point, not the helper. Before this guard the
    only logo candidate's refusal propagated out of ``render_artifact`` and
    ``process_item`` recorded a ``failed`` poster row (and, with the
    background also refused, re-raised and parked the job)."""
    _stub_imagemagick(monkeypatch)
    provider = _Provider([_logo(BAD_LOGO_URL)])
    intent = RenderIntent(
        kind="movie", title="Inside Out 2", tmdb_id=1022789,
        imdb_id="tt22022452", year=2024, refs={"plex": "1"},
    )

    async with _http({
        POSTER_URL: decodable_png(), BAD_LOGO_URL: b"not an image at all",
    }) as http:
        results = await pipeline_module.process_item(
            session, _config(tmp_path), http, _FakePlex(item()), [provider], intent,
        )

    statuses = {render.art_kind: render.status for render in results}
    # The background has no candidate on this provider, so it is the ordinary
    # no_art row; the poster is the claim.
    assert statuses == {"poster": "rendered", "background": "no_art"}


async def test_a_healthy_row_keeps_the_exact_fingerprint_it_has_today(
    session, tmp_path, monkeypatch
):
    """GREEN-BEFORE-AND-AFTER -- the storm guard.

    Recomputed independently from the pipeline's own published helpers, so it
    holds whatever the guard does internally: the fingerprint's logo input is
    the chosen logo's sha256 and nothing else, and a healthy logo costs
    exactly the one ladder question it costs today. A fingerprint that moved
    here would re-render every poster in the library.
    """
    config = _config(tmp_path)
    logo_calls = _stub_imagemagick(monkeypatch)
    provider = _Provider([_logo(GOOD_LOGO_URL)])
    png = decodable_png()
    png_sha = hashlib.sha256(png).hexdigest()

    async with _http({POSTER_URL: png, GOOD_LOGO_URL: png}) as http:
        render = await render_artifact(
            session, config, http, item(), "poster", [provider],
        )

    # draw_text=False because a composited logo replaces the title text
    # (pipeline.py's `draw_text = not (poster and (logo_path is not None ...))`).
    text_inputs, asset_hashes = await gather_fingerprint_inputs(
        config, item(), "poster", draw_text=False, logo_sha=png_sha,
    )
    assert render.fingerprint == compute_fingerprint(
        render_version_for("poster", config), "poster", POSTER_URL, png_sha,
        text_inputs, asset_hashes,
    )
    assert len(logo_calls) == 1
    assert len(_logo_requests(provider)) == 1, (
        "a healthy logo must cost exactly the one ladder question it costs today"
    )
