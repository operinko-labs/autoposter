"""Row 111's dual-read is gone: a pre-111 fingerprint is now a stale one.

Roadmap row 111 moved the first component of every render fingerprint from one
wholesale hash to four per-kind ones, and shipped a one-release arm that
accepted EITHER value and wrote the new one back -- which is what made that
deploy cost zero composites and zero Plex uploads for ~16,000 rows. Roadmap
row 247 removes it.

WHY THE REMOVAL IS FREE, and it is the claim these tests exist to hold the
line under. The legacy candidate was computed from the CURRENT
`config.version`, so the arm only ever recognised rows written under the value
the pod was running with. Row 78 moved that value (`tests/
test_season_show_title.py:48` pins the pre-78 wholesale hash and `:188` the
post one) and deployed AFTER the 2026-09-05 migration pass drained, so the arm
has been inert in production since. Any row that missed that pass was already
condemned to one re-render on its next visit; removing the arm does not cause
those re-renders, it stops paying a second sha256 per compare and per
previewed row to pretend otherwise.

WHAT THESE TESTS NOW PROVE. The inverse of what they used to: a row carrying
the pre-111 wholesale element 0 falls through both compare sites, re-renders,
and comes out with the per-kind value; and the impact preview counts it. The
composite stub RECORDS rather than raises, because the assertions here are
positive ones about re-rendering.

`config.version` is NOT dead after this row: it is still
`api/routes._render_affecting`'s cheap superset short-circuit, still the
Settings page's "version A to B" line, and still what six test files read.
`config/loader.py`'s two docstrings say so.

No `@pytest.mark.imagemagick`: `compositor.run` and `fit_point_size` are
stubbed, never spawned.
"""
import hashlib
from pathlib import Path

import httpx
from conftest import decodable_png

from autoposter.config.impact import count_affected
from autoposter.config.loader import load_config, render_version_for
from autoposter.db.models import Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import (
    _get_or_create_render, _upsert_media_item, compute_fingerprint,
    gather_fingerprint_inputs, process_item, render_artifact,
)
from autoposter.render import naming
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
POSTER_URL = "https://img/poster.jpg"


def _url_for(art_kind: str) -> str:
    return POSTER_URL if art_kind == "poster" else f"https://img/{art_kind}.jpg"


ITEM = ResolvedItem(
    rating_key="1", library="Movies", kind="movie", title="A Movie", year=1999,
    season_number=None, episode_number=None, root_folder="A Movie (1999)",
    file_path=None, art_url=None, tmdb_id=550, tvdb_id=None, imdb_id=None,
)


def _config(tmp_path):
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    config.operations.enabled = False
    config.badges.enabled = False
    # As tests/test_adopt_fingerprint.py:195. The example ships `use_logo:
    # true` with `logo_text_fallback: false`, and `_Provider` below has no
    # logo, so a live poster pass would draw NO text -- and the legacy rows
    # `_seed_legacy_live_row` writes are fingerprinted with adoption's
    # defaults (text drawn, no logo). Off, the live pass and the seed agree on
    # every input but element 0, which is the one this file is about.
    config.artwork.use_logo = False
    return config


class _Plex:
    async def resolve(self, intent):
        return ITEM

    async def fetch_item(self, rating_key):
        raise AssertionError("badges and operations are off in this file")


class _Provider:
    """A candidate for every art kind but the logo.

    The five `render_artifact` tests below only ever ask for the poster, so
    this used to answer only that. The `process_item` test at the end walks a
    movie's poster AND background, and a kind with no candidate at all would
    take a refusal path rather than the compare path this file is about.
    """

    name = "TMDB"

    async def fetch(self, request):
        if request.art_kind == "logo":
            return []
        return [ArtCandidate("TMDB", _url_for(request.art_kind), "en", 2000, 3000, 5.0)]


def _http(png):
    async def handler(request):
        return httpx.Response(200, content=png)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _allow_compositing(monkeypatch) -> list:
    """Every ImageMagick entry point on this path, stubbed and RECORDING.

    Before row 247 these stubs RAISED, because the claim was "no composite
    happened" and a landmine proves that better than a list somebody could
    forget to check. The claim is now the opposite one -- the row re-renders
    -- so the stub has to be observable instead. The returned list is the
    argvs `compositor.run` was called with.
    """
    composites: list = []
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: composites.append(argv))
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    return composites


async def _seed_legacy_live_row(
    session, config, png, art_kind: str = "poster"
) -> tuple[Render, str]:
    """A rendered row fingerprinted the way this service did BEFORE row 111:
    element 0 is the wholesale `config.version`.

    `art_kind` defaults to `"poster"`. Three of the surviving four tests call
    this to seed a legacy row (`test_a_whole_item_re_renders_through_process_item`
    calls it twice, once per art kind, to seed a whole item); what each of
    them proves is the same outcome: the row's pre-111 fingerprint no longer
    matches, so it falls out of its short-circuit or live-compare arm and
    re-renders once.
    """
    source_url = _url_for(art_kind)
    media_item = await _upsert_media_item(session, ITEM)
    target = naming.asset_path(
        config, ITEM.library, ITEM.root_folder, art_kind, None, None
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png)
    render = await _get_or_create_render(session, media_item, art_kind, str(target))
    text_inputs, asset_hashes = await gather_fingerprint_inputs(config, ITEM, art_kind)
    base_sha = hashlib.sha256(png).hexdigest()
    render.base_sha256 = base_sha
    render.source_url = source_url
    render.status = "rendered"
    render.fingerprint = compute_fingerprint(
        config.version, art_kind, source_url, base_sha, text_inputs, asset_hashes
    )
    legacy = render.fingerprint
    await session.commit()
    return render, legacy


async def test_a_legacy_fingerprint_now_re_renders(session, tmp_path, monkeypatch):
    """Site B, the live compare. The pre-111 value is no longer a match, so
    the row goes through the ladder and the compositor like any other stale
    row and comes out carrying its per-kind fingerprint.

    This is also what `test_a_genuinely_stale_row_still_re_renders` used to
    say one test along -- after the removal the "legacy" case and the
    "genuinely stale" case ARE one case, which is why there is now one test.
    """
    config = _config(tmp_path)
    png = decodable_png()
    composites = _allow_compositing(monkeypatch)
    _row, legacy = await _seed_legacy_live_row(session, config, png)

    async with _http(png) as http:
        result = await render_artifact(
            session, config, http, ITEM, "poster", [_Provider()]
        )

    assert result.status == "rendered"
    assert result.detail != "unchanged"
    assert composites, "a pre-111 fingerprint must now re-render"
    assert result.fingerprint != legacy
    text_inputs, asset_hashes = await gather_fingerprint_inputs(config, ITEM, "poster")
    assert result.fingerprint == compute_fingerprint(
        render_version_for("poster", config), "poster", POSTER_URL,
        result.base_sha256, text_inputs, asset_hashes,
    )


async def test_an_adopted_rows_legacy_fingerprint_no_longer_short_circuits(
    session, tmp_path, monkeypatch
):
    """Site A, the adopted short-circuit, and the expensive one to get wrong:
    it sits ABOVE the provider ladder, so an adopted row that no longer
    matches pays the whole ladder before it re-renders. That is the cost row
    247 accepts, and it is already sunk -- these rows stopped matching when
    row 78 moved `config.version`."""
    config = _config(tmp_path)
    png = decodable_png()
    composites = _allow_compositing(monkeypatch)

    media_item = await _upsert_media_item(session, ITEM)
    target = naming.asset_path(config, ITEM.library, ITEM.root_folder, "poster", None, None)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png)
    render = await _get_or_create_render(session, media_item, "poster", str(target))
    text_inputs, asset_hashes = await gather_fingerprint_inputs(config, ITEM, "poster")
    base_sha = hashlib.sha256(png).hexdigest()
    render.base_sha256 = base_sha
    render.adopted = True
    render.status = "rendered"
    render.fingerprint = compute_fingerprint(
        config.version, "poster", None, base_sha, text_inputs, asset_hashes
    )
    legacy = render.fingerprint
    await session.commit()

    async with _http(png) as http:
        result = await render_artifact(
            session, config, http, ITEM, "poster", [_Provider()]
        )

    assert result.status == "rendered"
    assert result.detail != "adopted", "the short-circuit must not have fired"
    assert composites, "an adopted pre-111 row must now re-render"
    assert result.fingerprint != legacy


async def test_the_preview_now_counts_a_legacy_row_as_affected(session, tmp_path, monkeypatch):
    """Site C, and the reason the walk had to move in the same commit as the
    pipeline: a preview that still grandfathered would report 0 for an edit
    that now re-renders the row, which is the same lie in the other
    direction."""
    config = _config(tmp_path)
    png = decodable_png()
    await _seed_legacy_live_row(session, config, png)

    assert (await count_affected(session, config)).affected == 1


async def test_a_whole_item_re_renders_through_process_item(session, tmp_path, monkeypatch):
    """The claim at the real entry point rather than at `render_artifact`.

    `process_item` is where the badge-and-upload stage lives, so this is the
    only test in the file that can say anything about uploads at all. Both of
    the movie's pre-111 rows now composite and come out with their per-kind
    fingerprints.

    The upload spy STAYS, and it is worth saying what it is now worth: with
    the short-circuit gone it no longer proves "the grandfather did not
    upload", it proves that this example config -- which uploads nothing
    (`tests/test_pipeline_e2e.py:120` pins `upload_status == "skipped"`) --
    still uploads nothing on a re-render. It goes red if anyone makes
    `process_item` upload unconditionally, which is the standing guard the
    row-111 branch put it there to be.
    """
    config = _config(tmp_path)
    png = decodable_png()
    uploads: list = []
    composites = _allow_compositing(monkeypatch)
    monkeypatch.setattr(
        pipeline_module, "upload_artwork", lambda *a, **k: uploads.append(a)
    )

    legacy = {}
    for art_kind in ("poster", "background"):
        row, value = await _seed_legacy_live_row(session, config, png, art_kind)
        row.upload_status = "skipped"
        legacy[art_kind] = value
    await session.commit()

    async with _http(png) as http:
        renders = await process_item(
            session, config, http, _Plex(), [_Provider()],
            RenderIntent(kind="movie", title="A Movie", tmdb_id=550),
        )

    assert {r.art_kind for r in renders} == {"poster", "background"}
    assert len(composites) >= 2, "both kinds must have re-rendered"
    assert uploads == [], "process_item uploaded under a config that uploads nothing"
    for render in renders:
        assert render.detail != "unchanged"
        assert render.fingerprint != legacy[render.art_kind]
