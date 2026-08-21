"""The adopted short-circuit, and the shared fingerprint inputs it rests on.

Adoption never resolves a provider, so it cannot know the ``source_url`` that
``compute_fingerprint`` normally takes. It stores the same fingerprint minus
that one input, and ``render_artifact`` recomputes the identical value before
touching a provider. Both sides gather their inputs through one function, so
the two can never drift apart -- the failure mode of a drift is re-rendering
the whole library, which is the thing adoption exists to avoid.
"""

from pathlib import Path

import httpx
import pytest

from autoposter.config.loader import load_config
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import naming
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import (
    _get_or_create_render, _upsert_media_item, adopted_fingerprint, compute_fingerprint,
    gather_fingerprint_inputs, render_artifact,
)
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def _config(tmp_path):
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    return config


def item(title="Dune: Part Two"):
    return ResolvedItem(
        rating_key="1", library="Movies", kind="movie", title=title, year=2024,
        season_number=None, episode_number=None, root_folder="Dune (2024)",
        file_path="/mnt/Media/Movies/Dune (2024)/x.mkv", art_url=None,
        tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
    )


class _RecordingProvider:
    """Serves a poster, and records every request it is asked to answer.

    The point of the adopted short-circuit is that an unchanged item costs zero
    outbound requests, so ``requests`` staying empty is the assertion that
    matters -- not merely that no HTTP call went out.
    """

    name = "TMDB"

    def __init__(self):
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if request.art_kind == "poster":
            return [ArtCandidate("TMDB", "https://img/poster.jpg", None, 2000, 3000, 5.0)]
        return []


def _fake_http():
    async def handler(request):
        return httpx.Response(200, content=b"fake-image-bytes")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _stub_out_imagemagick(monkeypatch):
    """Let a real render run to completion on a host without ImageMagick."""
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: None)
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )


async def _adopt(session, config, resolved, art_kind, base_sha):
    """Create the row the adoption walk will create, through the same functions."""
    media_item = await _upsert_media_item(session, resolved)
    target = naming.asset_path(
        config, resolved.library, resolved.root_folder, art_kind,
        resolved.season_number, resolved.episode_number,
    )
    render = await _get_or_create_render(session, media_item, art_kind, target)
    text_inputs, asset_hashes = await gather_fingerprint_inputs(config, resolved, art_kind)
    render.base_sha256 = base_sha
    render.fingerprint = adopted_fingerprint(
        config, art_kind, base_sha, text_inputs, asset_hashes
    )
    render.status = "rendered"
    render.adopted = True
    await session.commit()
    return render, target


def _write_asset(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"already-on-disk")


# --- adopted_fingerprint is compute_fingerprint minus the source URL ---------


def test_adopted_fingerprint_is_compute_fingerprint_without_the_source_url(tmp_path):
    config = _config(tmp_path)
    assert adopted_fingerprint(config, "poster", "abc", ["DUNE"], ["ov", "font", ""]) == (
        compute_fingerprint(config.version, "poster", None, "abc", ["DUNE"], ["ov", "font", ""])
    )


def test_adopted_fingerprint_differs_from_one_taken_with_a_source_url(tmp_path):
    config = _config(tmp_path)
    assert adopted_fingerprint(config, "poster", "abc", ["DUNE"], [""]) != (
        compute_fingerprint(config.version, "poster", "https://img/a.jpg", "abc", ["DUNE"], [""])
    )


def test_adopted_fingerprint_changes_with_the_base_hash(tmp_path):
    config = _config(tmp_path)
    assert adopted_fingerprint(config, "poster", "abc", ["DUNE"], [""]) != (
        adopted_fingerprint(config, "poster", "def", ["DUNE"], [""])
    )


def test_adopted_fingerprint_changes_with_a_text_input(tmp_path):
    config = _config(tmp_path)
    assert adopted_fingerprint(config, "poster", "abc", ["DUNE"], [""]) != (
        adopted_fingerprint(config, "poster", "abc", ["HEAT"], [""])
    )


def test_adopted_fingerprint_changes_with_an_asset_hash(tmp_path):
    config = _config(tmp_path)
    assert adopted_fingerprint(config, "poster", "abc", ["DUNE"], ["overlay-v1"]) != (
        adopted_fingerprint(config, "poster", "abc", ["DUNE"], ["overlay-v2"])
    )


# --- gather_fingerprint_inputs is the one definition of "what goes in" -------


async def test_gather_reflects_the_overlay_file_bytes(tmp_path):
    config = _config(tmp_path)
    overlay = Path(config.overlays_root) / config.artwork.poster.overlay_file
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_bytes(b"overlay-v1")
    _, before = await gather_fingerprint_inputs(config, item(), "poster")
    overlay.write_bytes(b"overlay-v2")
    _, after = await gather_fingerprint_inputs(config, item(), "poster")
    assert before != after


async def test_gather_returns_the_title_as_a_text_input(tmp_path):
    config = _config(tmp_path)
    text_inputs, _ = await gather_fingerprint_inputs(config, item(), "poster")
    assert text_inputs == ["Dune: Part Two"]


async def test_render_artifact_fingerprints_exactly_what_gather_returns(
    session, tmp_path, monkeypatch
):
    """The drift guard: the pipeline's own fingerprint must be reconstructable
    from ``gather_fingerprint_inputs`` alone, plus the source URL and base hash
    it records. If ``render_artifact`` ever grows a second, private notion of
    what goes into the fingerprint, this fails."""
    config = _config(tmp_path)
    config.artwork.use_logo = False
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, item(), "poster", [_RecordingProvider()]
        )

    assert render.status == "rendered"
    text_inputs, asset_hashes = await gather_fingerprint_inputs(config, item(), "poster")
    assert render.fingerprint == compute_fingerprint(
        config.version, "poster", render.source_url, render.base_sha256,
        text_inputs, asset_hashes,
    )


# --- the short-circuit ------------------------------------------------------


async def test_adopted_render_is_skipped_without_consulting_any_provider(session, tmp_path):
    config = _config(tmp_path)
    resolved = item()
    render, target = await _adopt(session, config, resolved, "poster", "base-sha")
    _write_asset(target)
    provider = _RecordingProvider()

    async with _fake_http() as http:
        result = await render_artifact(session, config, http, resolved, "poster", [provider])

    assert result.status == "rendered"
    assert result.detail == "adopted"
    assert result.adopted is True
    assert provider.requests == []
    assert target.read_bytes() == b"already-on-disk"


async def test_adopted_render_whose_asset_file_is_gone_is_re_rendered(
    session, tmp_path, monkeypatch
):
    config = _config(tmp_path)
    resolved = item()
    await _adopt(session, config, resolved, "poster", "base-sha")
    # No _write_asset(): the adopted row points at a file that is not there.
    provider = _RecordingProvider()
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        result = await render_artifact(session, config, http, resolved, "poster", [provider])

    assert result.detail != "adopted"
    assert provider.requests != []
    assert result.status == "rendered"
    assert result.adopted is False


async def test_adopted_render_whose_text_changed_is_re_rendered_and_loses_adopted(
    session, tmp_path, monkeypatch
):
    config = _config(tmp_path)
    _, target = await _adopt(session, config, item(title="Dune"), "poster", "base-sha")
    _write_asset(target)
    provider = _RecordingProvider()
    _stub_out_imagemagick(monkeypatch)

    # Same rating key, retitled in Plex: the text input the fingerprint covers
    # changed, so the adoption no longer describes what is on disk.
    async with _fake_http() as http:
        result = await render_artifact(
            session, config, http, item(title="Dune: Part Two"), "poster", [provider]
        )

    assert provider.requests != []
    assert result.status == "rendered"
    assert result.detail is None
    assert result.adopted is False
    assert target.read_bytes() == b"fake-image-bytes"


async def test_a_render_that_was_never_adopted_is_unaffected(session, tmp_path, monkeypatch):
    config = _config(tmp_path)
    resolved = item()
    render, target = await _adopt(session, config, resolved, "poster", "base-sha")
    _write_asset(target)
    # Everything an adopted row has, except the flag itself.
    render.adopted = False
    await session.commit()
    provider = _RecordingProvider()
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        result = await render_artifact(session, config, http, resolved, "poster", [provider])

    assert result.detail != "adopted"
    assert provider.requests != []


async def test_adopted_render_without_a_base_hash_is_not_skipped(
    session, tmp_path, monkeypatch
):
    config = _config(tmp_path)
    resolved = item()
    render, target = await _adopt(session, config, resolved, "poster", "base-sha")
    _write_asset(target)
    # An adopted row that never got a hash cannot claim the file is unchanged --
    # even though its fingerprint is internally consistent with that null hash,
    # which is what makes this a test of the guard rather than of the compare.
    text_inputs, asset_hashes = await gather_fingerprint_inputs(config, resolved, "poster")
    render.base_sha256 = None
    render.fingerprint = adopted_fingerprint(config, "poster", None, text_inputs, asset_hashes)
    await session.commit()
    provider = _RecordingProvider()
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        result = await render_artifact(session, config, http, resolved, "poster", [provider])

    assert result.detail != "adopted"
    assert provider.requests != []


@pytest.mark.parametrize("art_kind", ["poster", "background"])
async def test_the_short_circuit_holds_for_every_art_kind_a_movie_implies(
    session, tmp_path, art_kind
):
    config = _config(tmp_path)
    resolved = item()
    _, target = await _adopt(session, config, resolved, art_kind, "base-sha")
    _write_asset(target)
    provider = _RecordingProvider()

    async with _fake_http() as http:
        result = await render_artifact(session, config, http, resolved, art_kind, [provider])

    assert result.detail == "adopted"
    assert provider.requests == []
