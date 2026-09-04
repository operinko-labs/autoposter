"""The one-release dual-read that migrates ~16,000 fingerprints for free.

Roadmap row 111 moves the first component of every render fingerprint from one
wholesale hash to four per-kind ones. Four distinct payloads cannot all produce
the one 16-hex value the rows already carry except by collision, so the first
deploy would strand every stored fingerprint -- ~16k ImageMagick composites,
~16k asset writes and ~16k Plex uploads for artwork nobody asked to change.

So both compare sites accept EITHER value and, on a legacy match, write the new
one back. Both already hold `text_inputs`/`asset_hashes` and have already paid
their file I/O, so the legacy candidate is one extra sha256 over a joined
string and NO extra I/O. Rows migrate forward lazily on the passes that would
have run anyway.

WHAT THESE TESTS PROVE, AND WHAT THEY DO NOT. `_forbid_compositing` makes
`compositor.run`, `fit_point_size` and `_publish` raise, so **no composite and
no asset write** is proven by the tests not blowing up rather than by an
assertion somebody could forget to check. **No Plex upload** is true too, but
by CONSTRUCTION rather than by assertion: the grandfather returns from
`render_artifact` before any upload stage is reached, and every test here calls
`render_artifact` directly -- the upload stage is not inside it, and the
example config uploads nothing anyway (`tests/test_pipeline_e2e.py:120` pins
`upload_status == "skipped"` under it). Said plainly here rather than left as
an over-claim, because the difference between "proven" and "true by
construction" is exactly the distinction this row is otherwise careful about.

THE LIMITATION, and it is why the legacy arm is removed in a follow-up rather
than left standing: the legacy candidate is computed from the CURRENT
`config.version`, so the grandfather only holds while that value has not moved
since those rows were written. An artwork edit made before the pod has
completed one full pass re-storms whatever has not yet migrated. That is a
bounded, disclosed cost -- not a correctness hole -- and it is stated in the
roadmap cell, in deploy/README.md and in the pull request.

No `@pytest.mark.imagemagick` here either: `compositor.run` is monkeypatched to
RAISE, which is how "no composite happened" is proven rather than asserted.
"""
import hashlib
from pathlib import Path

import httpx
from conftest import decodable_png
from sqlalchemy import select

from autoposter.config.impact import count_affected
from autoposter.config.loader import load_config, render_version, render_version_for
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


def _forbid_compositing(monkeypatch):
    """Every ImageMagick entry point on this path, made loud.

    A stub that merely recorded would let a silent composite pass as a pass;
    raising means the grandfather's whole promise -- no composite, no asset
    write, no upload -- is proven by the test not blowing up rather than by an
    assertion about a list somebody could forget to check.
    """
    def refuse(*args, **kwargs):
        raise AssertionError(
            "the grandfather composited: a legacy fingerprint must be accepted "
            "and rewritten, never re-rendered"
        )

    monkeypatch.setattr(pipeline_module.compositor, "run", refuse)
    monkeypatch.setattr(pipeline_module, "fit_point_size", refuse)
    monkeypatch.setattr(pipeline_module, "_publish", refuse)


def _allow_compositing(monkeypatch):
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: None)
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )


async def _seed_legacy_live_row(
    session, config, png, art_kind: str = "poster"
) -> tuple[Render, str]:
    """A rendered row fingerprinted the way this service did BEFORE row 111:
    element 0 is the wholesale `config.version`.

    `art_kind` defaults to `"poster"`, which is what the five `render_artifact`
    tests want; the `process_item` test seeds the background through the same
    function so a whole item can migrate in one pass.
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


async def test_a_legacy_fingerprint_is_accepted_and_rewritten_without_a_composite(
    session, tmp_path, monkeypatch
):
    """The whole migration, in one pass.

    `compositor.run`, `fit_point_size` and `_publish` all RAISE here, so
    reaching any of them fails the test outright -- which is what "zero
    composites, zero asset writes, zero Plex uploads" has to mean to be worth
    claiming. The row comes out carrying the NEW per-kind value, so the next
    pass matches outright and the legacy arm is never consulted again.
    """
    config = _config(tmp_path)
    png = decodable_png()
    _forbid_compositing(monkeypatch)
    _row, legacy = await _seed_legacy_live_row(session, config, png)

    async with _http(png) as http:
        result = await render_artifact(
            session, config, http, ITEM, "poster", [_Provider()]
        )

    assert result.status == "rendered"
    assert result.detail == "unchanged"
    assert result.fingerprint != legacy
    text_inputs, asset_hashes = await gather_fingerprint_inputs(config, ITEM, "poster")
    assert result.fingerprint == compute_fingerprint(
        render_version_for("poster", config), "poster", POSTER_URL,
        result.base_sha256, text_inputs, asset_hashes,
    )


async def test_a_second_pass_over_a_rewritten_row_matches_outright(
    session, tmp_path, monkeypatch
):
    """Idempotence. If the write-back did not persist, this second pass would
    take the legacy arm again for ever and the migration would never end."""
    config = _config(tmp_path)
    png = decodable_png()
    _forbid_compositing(monkeypatch)
    await _seed_legacy_live_row(session, config, png)

    async with _http(png) as http:
        await render_artifact(session, config, http, ITEM, "poster", [_Provider()])
        second = await render_artifact(session, config, http, ITEM, "poster", [_Provider()])

    assert second.detail == "unchanged"
    stored = (await session.execute(select(Render))).scalars().all()
    assert len(stored) == 1
    assert stored[0].fingerprint == second.fingerprint


async def test_an_adopted_rows_legacy_fingerprint_is_accepted_and_rewritten(
    session, tmp_path, monkeypatch
):
    """The other compare site, and the expensive one to get wrong: the adopted
    short-circuit sits ABOVE the provider ladder, so an adopted row whose
    version moved pays the whole ladder before it discovers nothing changed.
    Its detail stays 'adopted' -- no new served string is invented to make the
    migration visible (the pod log is the sink for that)."""
    config = _config(tmp_path)
    png = decodable_png()
    _forbid_compositing(monkeypatch)

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
    assert result.detail == "adopted"
    assert result.fingerprint != legacy
    assert result.fingerprint == compute_fingerprint(
        render_version_for("poster", config), "poster", None, base_sha,
        text_inputs, asset_hashes,
    )


async def test_a_genuinely_stale_row_still_re_renders(session, tmp_path, monkeypatch):
    """The grandfather must not swallow a real change.

    The stored value is a legacy fingerprint taken under DIFFERENT settings,
    so neither the new candidate nor the legacy one matches and the row
    re-renders exactly as it should. Compositing is allowed here, and this is
    the only test in the file where it is.
    """
    config = _config(tmp_path)
    png = decodable_png()
    _allow_compositing(monkeypatch)
    _row, legacy = await _seed_legacy_live_row(session, config, png)

    config.artwork.poster.border_width = 31
    config.version = render_version(config)

    async with _http(png) as http:
        result = await render_artifact(
            session, config, http, ITEM, "poster", [_Provider()]
        )

    assert result.detail != "unchanged"
    assert result.fingerprint != legacy


async def test_the_preview_does_not_count_a_legacy_row_as_affected(
    session, tmp_path, monkeypatch
):
    """The preview must grandfather too, or the first `skip_tba` edit after
    the deploy reports '~16,000 affected' for a change that re-renders
    nothing -- and an operator who is shown that number once stops trusting
    every number the panel gives them."""
    config = _config(tmp_path)
    png = decodable_png()
    _forbid_compositing(monkeypatch)
    await _seed_legacy_live_row(session, config, png)

    assert (await count_affected(session, config)).affected == 0


async def test_a_whole_item_migrates_through_process_item_with_no_composite_and_no_upload(
    session, tmp_path, monkeypatch
):
    """C3's binding claim, at the entry point, with SPIES rather than landmines.

    The tests above prove "no composite, no asset write" by making
    `compositor.run`, `fit_point_size` and `_publish` RAISE -- a strong proof,
    but every one of them calls `render_artifact`, and the Plex upload stage
    is not inside it. So "and no Plex upload" was true by construction and
    asserted nowhere. This closes that: it drives the real `process_item`,
    which is where the badge-and-upload stage lives, records every call to
    `compositor.run` and to `upload_artwork`, and asserts BOTH lists are empty
    while both of the movie's rows come out carrying their new per-kind
    fingerprints and their Plex state exactly where the migration found it.

    Badges are off in this file, so the uploader spy is a STANDING guard
    rather than a defeated one, and it is worth saying so rather than letting
    an empty list read as more than it is: its job is to go red if the
    grandfather ever grows an upload of its own, or if anyone moves an upload
    above the fingerprint compare. Spies rather than raisers here because the
    claim is a positive one about two call lists, and because a raiser inside
    `process_item` would be swallowed by its per-kind `except SourceRefused`.
    """
    config = _config(tmp_path)
    png = decodable_png()
    composites: list = []
    uploads: list = []
    monkeypatch.setattr(
        pipeline_module.compositor, "run", lambda argv: composites.append(argv)
    )
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
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
    assert composites == [], "the grandfather composited"
    assert uploads == [], "the grandfather uploaded to Plex"
    for render in renders:
        assert render.detail == "unchanged"
        assert render.fingerprint != legacy[render.art_kind]
        assert render.upload_status == "skipped"
