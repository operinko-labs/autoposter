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
from autoposter.providers.base import ArtCandidate
from autoposter.render import pipeline as pipeline_module
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
