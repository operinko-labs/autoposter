"""Perf workstream B1: an unchanged provider image is not downloaded again.

Every test drives ``render_artifact`` -- the real entry point -- against a
counting ``httpx.MockTransport``, because the whole claim is about requests
that do NOT happen. A helper-level test could prove the fingerprint is
computable from the stored digest and say nothing about whether the CDN was
asked anyway, which is how a gated feature has twice passed its own tests in
this tree.
"""
import hashlib
from pathlib import Path

import httpx
import pytest
from conftest import decodable_png

from autoposter.config.loader import render_version_for
from autoposter.providers.base import ArtCandidate, ArtRequest
from autoposter.providers.ladder import select_artwork
from autoposter.render import pipeline as pipeline_module
from autoposter.render.artwork_fetch import pick_guarded_logo
from autoposter.render.pipeline import (
    compute_fingerprint,
    gather_fingerprint_inputs,
    manual_override_target,
    render_artifact,
)
from test_pipeline import (
    GENERATED_LISTING_NO_SELF_FEED,
    _TitleCardAwareProvider,
    _episode_item,
    _logo_test_config,
    _plant_logo,
    _plex_generated_base_for,
    _stub_out_imagemagick,
    item,
)

POSTER_URL = "https://img/poster.jpg"
OTHER_POSTER_URL = "https://img/poster-2.jpg"
PNG = decodable_png()
PNG_SHA = hashlib.sha256(PNG).hexdigest()


class _Cdn:
    """A provider CDN that records every image body it is asked for.

    ``bodies`` maps a URL to the bytes it serves; any other URL gets ``PNG``.
    Decodable bytes throughout, because ``_download`` decodes what it keeps.
    """

    def __init__(self, bodies=None):
        self.bodies = dict(bodies or {})
        self.urls: list[str] = []

    async def _handle(self, request):
        url = str(request.url)
        self.urls.append(url)
        return httpx.Response(200, content=self.bodies.get(url, PNG))

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))


class _Provider:
    """One poster candidate, and one logo candidate when ``logo_url`` is set.
    ``name`` is a class attribute on the real clients, so it is one here."""

    name = "TMDB"

    def __init__(self, poster_url=POSTER_URL, logo_url=None):
        self.poster_url = poster_url
        self.logo_url = logo_url

    async def fetch(self, request):
        if request.art_kind == "poster":
            return [ArtCandidate("TMDB", self.poster_url, None, 2000, 3000, 5.0)]
        if request.art_kind == "logo" and self.logo_url is not None:
            return [ArtCandidate("TMDB", self.logo_url, "en", 800, 300, 5.0)]
        return []


def _base_only_config(tmp_path):
    """No clearlogo, so the base image is the only body a pass can fetch."""
    config = _logo_test_config(tmp_path)
    config.artwork.use_logo = False
    return config


async def test_an_unchanged_provider_item_makes_zero_image_requests(
    session, tmp_path, monkeypatch
):
    config = _base_only_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    cdn = _Cdn()

    async with cdn.client() as http:
        first = await render_artifact(session, config, http, item(), "poster", [_Provider()])
        first_fingerprint = first.fingerprint
        assert cdn.urls == [POSTER_URL]
        cdn.urls.clear()
        second = await render_artifact(session, config, http, item(), "poster", [_Provider()])

    assert cdn.urls == []
    assert second.status == "rendered"
    assert second.detail == "unchanged"
    assert second.fingerprint == first_fingerprint
    assert second.base_sha256 == PNG_SHA


async def test_a_fingerprint_mismatch_downloads_exactly_once_and_recomputes(
    session, tmp_path, monkeypatch
):
    """A new title moves the text input, so the provisional fingerprint
    misses. The source is fetched ONCE -- compose needs the bytes -- and the
    stored fingerprint is the one recomputed from the real digest."""
    config = _base_only_config(tmp_path)
    calls, _ = _stub_out_imagemagick(monkeypatch)
    cdn = _Cdn()
    retitled = item(title="Dune: Part Three")

    async with cdn.client() as http:
        await render_artifact(session, config, http, item(), "poster", [_Provider()])
        cdn.urls.clear()
        calls.clear()
        second = await render_artifact(session, config, http, retitled, "poster", [_Provider()])

    assert cdn.urls == [POSTER_URL]
    assert calls, "a changed title must recompose"
    assert second.detail != "unchanged"
    text_inputs, asset_hashes = await gather_fingerprint_inputs(config, retitled, "poster")
    assert second.fingerprint == compute_fingerprint(
        render_version_for("poster", config), "poster", POSTER_URL, PNG_SHA,
        text_inputs, asset_hashes,
    )


async def test_a_missing_target_downloads_exactly_once_and_republishes(
    session, tmp_path, monkeypatch
):
    config = _base_only_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    cdn = _Cdn()

    async with cdn.client() as http:
        first = await render_artifact(session, config, http, item(), "poster", [_Provider()])
        Path(first.asset_path).unlink()
        cdn.urls.clear()
        second = await render_artifact(session, config, http, item(), "poster", [_Provider()])

    assert cdn.urls == [POSTER_URL]
    assert second.detail != "unchanged"
    assert Path(second.asset_path).exists()


@pytest.mark.parametrize(
    "column, value",
    [("fingerprint", None), ("base_sha256", None), ("source_url", OTHER_POSTER_URL)],
)
async def test_a_row_failing_any_skip_condition_downloads(
    session, tmp_path, monkeypatch, column, value
):
    """The spec's three conditions besides the branch itself, one at a time.
    A cleared fingerprint is what every force path writes (api/routes.py's
    ``_clear_render_fingerprints``, /actions/rebuild, backfill, the picker,
    the manual upload); a NULL digest or a different stored URL means the
    stored digest describes nothing this candidate serves."""
    config = _base_only_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    cdn = _Cdn()

    async with cdn.client() as http:
        first = await render_artifact(session, config, http, item(), "poster", [_Provider()])
        setattr(first, column, value)
        await session.commit()
        cdn.urls.clear()
        await render_artifact(session, config, http, item(), "poster", [_Provider()])

    assert cdn.urls == [POSTER_URL]


async def test_a_new_candidate_url_is_downloaded(session, tmp_path, monkeypatch):
    config = _base_only_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    cdn = _Cdn()

    async with cdn.client() as http:
        await render_artifact(session, config, http, item(), "poster", [_Provider()])
        cdn.urls.clear()
        second = await render_artifact(
            session, config, http, item(), "poster", [_Provider(poster_url=OTHER_POSTER_URL)],
        )

    assert cdn.urls == [OTHER_POSTER_URL]
    assert second.source_url == OTHER_POSTER_URL


async def test_a_manual_override_is_re_read_on_every_pass(session, tmp_path, monkeypatch):
    """Overrides are files operators overwrite in place, so they never take
    the skip: the override is staged and hashed on every pass, as today."""
    config = _base_only_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = item()
    override = manual_override_target(config, resolved, "poster")
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_bytes(PNG)
    staged = []
    original_stage = pipeline_module._stage_override

    def counting_stage(*args, **kwargs):
        staged.append(args[0])
        return original_stage(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "_stage_override", counting_stage)
    cdn = _Cdn()

    async with cdn.client() as http:
        await render_artifact(session, config, http, resolved, "poster", [_Provider()])
        second = await render_artifact(session, config, http, resolved, "poster", [_Provider()])

    assert len(staged) == 2
    assert cdn.urls == []
    assert second.detail == "unchanged"


async def test_plexs_generated_frame_is_downloaded_on_every_pass(
    session, tmp_path, monkeypatch
):
    """The Plex frame's source_url is a synthetic key on purpose (row 241), so
    only its bytes can say whether it moved: it never takes the skip."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = _episode_item()
    provider = _TitleCardAwareProvider(has_art=False)
    cdn = _Cdn()

    async with cdn.client() as http:
        for _ in range(2):
            render = await render_artifact(
                session, config, http, resolved, "title_card", [provider],
                plex_generated_base=_plex_generated_base_for(http, GENERATED_LISTING_NO_SELF_FEED),
            )

    assert len(cdn.urls) == 2
    assert render.source_mode == "plex_generated"
    assert render.detail == "unchanged"


# --- the clearlogo half ------------------------------------------------------
#
# A poster's logo is downloaded by the guard walk (render/artwork_fetch.py)
# before the fingerprint on every pass. B1 stores the accepted logo's URL and
# digest on the poster row and reuses the digest when the ladder's FIRST
# candidate is that same URL; anything else runs the guard exactly as today.

LOGO_URL = "https://img/logo.png"
OTHER_LOGO_URL = "https://img/logo-2.png"
OTHER_PNG = decodable_png((10, 6))
OTHER_PNG_SHA = hashlib.sha256(OTHER_PNG).hexdigest()


async def test_an_unchanged_logo_poster_makes_zero_image_requests(
    session, tmp_path, monkeypatch
):
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    cdn = _Cdn()
    provider = _Provider(logo_url=LOGO_URL)

    async with cdn.client() as http:
        first = await render_artifact(session, config, http, item(), "poster", [provider])
        # A new row has nothing stored, so the base is fetched in the provider
        # arm and the logo after it, by the guard.
        assert cdn.urls == [POSTER_URL, LOGO_URL]
        assert first.logo_source_url == LOGO_URL
        assert first.logo_sha256 == PNG_SHA
        cdn.urls.clear()
        second = await render_artifact(session, config, http, item(), "poster", [provider])

    assert cdn.urls == []
    assert second.detail == "unchanged"


async def test_a_different_first_logo_candidate_runs_the_guard_as_before(
    session, tmp_path, monkeypatch
):
    """The guard fetches the new logo; its new digest moves the fingerprint,
    so the deferred base is fetched too and the poster recomposes with it."""
    config = _logo_test_config(tmp_path)
    _, logo_calls = _stub_out_imagemagick(monkeypatch)
    cdn = _Cdn({OTHER_LOGO_URL: OTHER_PNG})

    async with cdn.client() as http:
        await render_artifact(
            session, config, http, item(), "poster", [_Provider(logo_url=LOGO_URL)],
        )
        cdn.urls.clear()
        logo_calls.clear()
        second = await render_artifact(
            session, config, http, item(), "poster", [_Provider(logo_url=OTHER_LOGO_URL)],
        )

    assert cdn.urls == [OTHER_LOGO_URL, POSTER_URL]
    assert len(logo_calls) == 1
    assert second.detail != "unchanged"
    assert second.logo_source_url == OTHER_LOGO_URL
    assert second.logo_sha256 == OTHER_PNG_SHA


async def test_a_row_without_logo_columns_pays_once_then_reuses(
    session, tmp_path, monkeypatch
):
    """Every poster row predating the migration. Its first pass after deploy
    still downloads the logo, and must RECORD it even though that pass ends at
    "unchanged" -- otherwise a settled library would never populate the
    columns and never stop paying."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    cdn = _Cdn()
    provider = _Provider(logo_url=LOGO_URL)

    async with cdn.client() as http:
        first = await render_artifact(session, config, http, item(), "poster", [provider])
        first.logo_source_url = None
        first.logo_sha256 = None
        await session.commit()
        cdn.urls.clear()
        second = await render_artifact(session, config, http, item(), "poster", [provider])
        assert cdn.urls == [LOGO_URL]
        assert second.detail == "unchanged"
        assert (second.logo_source_url, second.logo_sha256) == (LOGO_URL, PNG_SHA)
        cdn.urls.clear()
        await render_artifact(session, config, http, item(), "poster", [provider])

    assert cdn.urls == []


async def test_a_mismatch_with_a_reused_logo_fetches_both_and_composites_the_logo(
    session, tmp_path, monkeypatch
):
    config = _logo_test_config(tmp_path)
    _, logo_calls = _stub_out_imagemagick(monkeypatch)
    cdn = _Cdn()
    provider = _Provider(logo_url=LOGO_URL)

    async with cdn.client() as http:
        first = await render_artifact(session, config, http, item(), "poster", [provider])
        first_fingerprint = first.fingerprint
        first.fingerprint = "0" * 64
        await session.commit()
        cdn.urls.clear()
        logo_calls.clear()
        second = await render_artifact(session, config, http, item(), "poster", [provider])

    assert cdn.urls == [POSTER_URL, LOGO_URL]
    assert len(logo_calls) == 1, "the recompose must composite the fetched logo"
    assert second.detail != "unchanged"
    assert second.fingerprint == first_fingerprint, (
        "recomputed from the real digests, which have not moved"
    )


async def test_an_operator_picked_logo_clears_the_provider_logo_columns(
    session, tmp_path, monkeypatch
):
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = item()
    cdn = _Cdn()
    provider = _Provider(logo_url=LOGO_URL)

    async with cdn.client() as http:
        first = await render_artifact(session, config, http, resolved, "poster", [provider])
        assert first.logo_source_url == LOGO_URL
        _plant_logo(config, resolved)
        second = await render_artifact(session, config, http, resolved, "poster", [provider])

    assert second.detail != "unchanged"
    assert second.logo_source_url is None
    assert second.logo_sha256 is None


class _CountingLogoProvider:
    name = "TMDB"

    def __init__(self, logo_url):
        self.logo_url = logo_url
        self.asks = 0

    async def fetch(self, request):
        self.asks += 1
        return [ArtCandidate("TMDB", self.logo_url, "en", 800, 300, 5.0)]


async def test_the_guard_starts_from_a_selection_it_is_handed(tmp_path):
    """The render path asks the ladder once to learn the first candidate;
    handing that answer in keeps the walk from asking the same question twice
    (tests/test_pipeline_logo_guard.py pins "one ladder question" end to end)."""
    provider = _CountingLogoProvider(LOGO_URL)
    request = ArtRequest(art_kind="logo", is_movie=True, tmdb_id=1)
    first = await select_artwork([provider], ["en"], request)
    provider.asks = 0
    cdn = _Cdn()

    async with cdn.client() as http:
        path, sha, skipped, url = await pick_guarded_logo(
            http, [provider], ["en"], request, tmp_path, native_id="1", first=first,
        )

    assert provider.asks == 0
    assert (url, sha, skipped) == (LOGO_URL, PNG_SHA, 0)
    assert path.exists()


async def test_new_bytes_at_the_same_url_on_a_miss_store_the_fresh_digest(
    session, tmp_path, monkeypatch
):
    """The accepted risk's own safety net: a CDN that serves new bytes at an
    old URL. When the provisional fingerprint misses anyway (here a new
    title), the one download it costs must be what the row records -- the
    fresh digest, and the fingerprint those bytes produce -- never the stored
    digest that stood in for them."""
    config = _base_only_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    cdn = _Cdn()
    retitled = item(title="Dune: Part Three")

    async with cdn.client() as http:
        await render_artifact(session, config, http, item(), "poster", [_Provider()])
        cdn.urls.clear()
        cdn.bodies[POSTER_URL] = OTHER_PNG
        second = await render_artifact(session, config, http, retitled, "poster", [_Provider()])

    assert cdn.urls == [POSTER_URL]
    assert second.base_sha256 == OTHER_PNG_SHA

    async def fingerprint_for(base_sha):
        return await pipeline_module._render_fingerprint(
            config, retitled, "poster", POSTER_URL, base_sha,
            draw_text=True, logo_sha="", suppress_styling=False,
        )

    assert second.fingerprint == await fingerprint_for(OTHER_PNG_SHA)
    assert second.fingerprint != await fingerprint_for(PNG_SHA), (
        "the stale digest must not be what the stored fingerprint describes"
    )
