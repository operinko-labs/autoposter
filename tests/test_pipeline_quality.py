"""The Action Center's quality facts, through the real pipeline entry point.

Every test here drives ``render_artifact``. A helper-level test would prove
the values can be computed and say nothing about whether the write-back
records them -- and the write-back's placement is the whole design: it sits
below the "unchanged" fingerprint short-circuit, so a fact recorded there
survives a pass that changes nothing, unlike `detail`, which is blanked.
"""
from pathlib import Path

import httpx
from conftest import decodable_png
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db.models import Render
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import render_artifact
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


def _item():
    return ResolvedItem(
        rating_key="1", library="Movies", kind="movie", title="Dune: Part Two", year=2024,
        season_number=None, episode_number=None, root_folder="Dune (2024)",
        file_path="/mnt/Media/Movies/Dune (2024)/x.mkv", art_url=None,
        tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
    )


class _Provider:
    """One poster candidate, and optionally one logo candidate.

    ``name`` is a class-level attribute on the real clients
    (providers/tmdb.py's ``name = "TMDB"``), so it is one here too -- the
    runtime provider rank is read off exactly this attribute.
    """

    def __init__(self, name="TMDB", *, language=None, art=True, logo_url=None):
        self.name = name
        self._language = language
        self._art = art
        self._logo_url = logo_url
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if request.art_kind == "poster" and self._art:
            return [
                ArtCandidate(self.name, "https://img/poster.jpg", self._language, 2000, 3000, 5.0)
            ]
        if request.art_kind == "logo" and self._logo_url is not None:
            return [ArtCandidate(self.name, self._logo_url, "en", 800, 300, 5.0)]
        return []


def _fake_http():
    async def handler(request):
        # A decodable PNG, not a placeholder: ``pipeline._download`` decodes
        # every body it keeps (see conftest.decodable_png).
        return httpx.Response(200, content=decodable_png())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _stub_out_imagemagick(monkeypatch):
    """Record every argv passed to compositor.run without invoking ImageMagick.

    ``fit_point_size`` is the only other function on this path that shells out,
    so it is stubbed too -- and its stubbed 120 is exactly what the point-size
    capture test asserts arrives in the column.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: calls.append(argv))
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    return calls


async def test_a_render_records_the_language_it_achieved_and_its_rank(
    session, tmp_path, monkeypatch
):
    """Roadmap 11a's own stated acceptance: a forced en-only render on an
    xx-preferring config produces the language fact."""
    config = _config(tmp_path)
    assert config.artwork.poster.language_order == ["xx", "en", "fi"]
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )

    # `quality_scored_at` is assigned a SQL `now()`, so the flush leaves it
    # expired on the instance and reading it would be a lazy load -- a
    # MissingGreenlet under asyncio, not a query. The refresh is what makes
    # the database's own stamp readable here.
    await session.refresh(render)

    assert render.status == "rendered"
    assert render.selected_language == "en"
    assert render.language_rank == 1
    assert render.quality_scored_at is not None


async def test_an_unchanged_pass_does_not_wipe_the_quality_facts(
    session, tmp_path, monkeypatch
):
    """The entry-point law for this phase. The fingerprint short-circuit
    returns above the write-back, so a second identical pass must leave every
    fact exactly where the first one put it -- the failure mode `detail` has
    and `source_mode` was written to avoid."""
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        first = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )
        assert first.selected_language == "en"

        # A sentinel the second pass has no way to reproduce. If the
        # short-circuit path touched these columns at all -- blanking them, or
        # rewriting them from a selection it never made -- this is what
        # catches it.
        first.selected_language = "fi"
        first.language_rank = 2
        await session.commit()

        second = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )

    assert second.detail == "unchanged"
    assert second.selected_language == "fi"
    assert second.language_rank == 2
    assert second.quality_scored_at is not None


async def test_the_textless_fallback_is_recorded_when_a_text_bearing_image_is_taken(
    session, tmp_path, monkeypatch
):
    """`Selection.is_fallback` has been returned by the ladder since it was
    written and read by nobody. This is the line that reads it."""
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )

    assert render.textless_fallback is True


async def test_a_textless_pick_records_no_fallback(session, tmp_path, monkeypatch):
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language=None)],
        )

    assert render.textless_fallback is False
    assert render.selected_language is None
    assert render.language_rank == 0


async def test_the_logo_to_text_fallback_is_recorded_when_the_fallback_is_on(
    session, tmp_path, monkeypatch
):
    """The "taken" half of the logo decision, which until now materialised no
    variable at all -- only its suppressed sibling did."""
    config = _config(tmp_path)
    config.artwork.logo_text_fallback = True
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster",
            [_Provider(language="en", logo_url=None)],
        )

    assert render.logo_text_fallback is True


async def test_no_logo_with_the_fallback_off_records_no_logo_fallback(
    session, tmp_path, monkeypatch
):
    """With the fallback off the poster gets neither logo nor text, which is a
    different thing from wearing text in a logo's place."""
    config = _config(tmp_path)
    assert config.artwork.logo_text_fallback is False
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster",
            [_Provider(language="en", logo_url=None)],
        )

    assert render.logo_text_fallback is False


async def test_the_provider_rank_is_taken_against_the_runtime_ladder(
    session, tmp_path, monkeypatch
):
    """Against the list the pipeline was handed, never config.providers.order:
    app.py drops a configured provider with no implementation, so a
    config-index rank can name a position that never existed."""
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster",
            [_Provider("TVDB", art=False), _Provider("TMDB", language="en")],
        )

    assert render.provider == "TMDB"
    assert render.provider_rank == 1


async def test_a_manual_override_records_no_provider_rank_and_no_language(
    session, tmp_path, monkeypatch
):
    """A hand-placed file has no ladder position at all. Recording 0 would
    read as "the operator's first-choice provider"; recording anything else
    would read as a downgrade of a choice no ladder made."""
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = _item()
    override = Path(config.manual_assets_root) / "Movies" / "Dune (2024)" / "poster.jpg"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_bytes(b"the operator's own poster")

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, resolved, "poster", [_Provider(language="en")],
        )

    assert render.provider == "manual"
    assert render.provider_rank is None
    assert render.selected_language is None
    assert render.language_rank is None


async def test_the_base_dimensions_and_the_fitted_point_size_are_captured(
    session, tmp_path, monkeypatch
):
    """Captured, enforced nowhere. Row 219 keeps the resolution-floor
    decision; what this buys is that answering it later is a small change
    instead of a re-backfill of the whole library."""
    config = _config(tmp_path)
    # use_logo off so the title text is actually drawn and a point size exists
    # to capture -- a logo poster suppresses the text entirely.
    config.artwork.use_logo = False
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )

    assert render.base_width == 2000
    assert render.base_height == 3000
    assert render.text_point_size == 120


async def test_the_facts_are_committed_not_merely_set_on_the_instance(
    session, tmp_path, monkeypatch
):
    """A page reads the row back from the database, not from the pipeline's
    identity map."""
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider(language="en")],
        )

    # Held before the expiry: reading it afterwards would be a lazy load off
    # an expired instance, which under asyncio is a MissingGreenlet.
    render_id = render.id
    session.expire_all()
    reread = (
        await session.execute(select(Render).where(Render.id == render_id))
    ).scalar_one()
    assert reread.selected_language == "en"
    assert reread.provider_rank == 0
    assert reread.quality_scored_at is not None
