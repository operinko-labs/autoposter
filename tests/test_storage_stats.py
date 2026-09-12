"""``renders.size_bytes`` and the storage aggregation (roadmap row 52).

Two halves, one file, because they are one claim: a rendered artifact records
how many bytes it occupies, and the storage endpoint's numbers are that column
grouped by library and art kind in SQL. Splitting them would let the stamp
drift from what the aggregation assumes it means.

The stamp half drives the REAL ``process_item`` -- the pipeline's only entry
point -- rather than ``render_artifact`` or ``_publish`` alone (memory: gated
features need one test through the real entry point). ImageMagick is stubbed
out the way ``tests/test_pipeline_rekey.py`` does it: ``compositor.run`` is a
no-op and ``compose_styled`` styles ``working`` IN PLACE, so the bytes that
reach the asset tree are the downloaded PNG's and their size is known exactly.
That is what lets these tests assert an exact number without a real ``magick``
and without the ``imagemagick`` marker (memory: real-compositor tests need the
imagemagick marker -- these are not real-compositor tests).

The aggregation half seeds rows directly. Its point is arithmetic over a
join, and seeding is the only way to put a NULL size, a ``failed`` row and two
libraries in front of it at once.
"""

from pathlib import Path

import httpx
from conftest import decodable_png
from sqlalchemy import select

from autoposter.api.stats import ART_KINDS, storage_snapshot
from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem, Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import pipeline
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

# Deliberately not a real-looking path: it exists to be seeded into
# ``renders.asset_path`` and then proven ABSENT from every served body
# (row 213 -- a path is a disclosure).
FAKE_ASSET = "/nowhere/fake-asset.jpg"


def _config(tmp_path):
    """The example config rooted in ``tmp_path``, with both Plex-touching
    stages off -- ``tests/test_pipeline_rekey.py::_config``'s shape. Neither
    ``operations`` nor ``badges`` says anything about an artifact's size."""
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    config.operations.enabled = False
    config.badges.enabled = False
    return config


class _FakePlex:
    def __init__(self, item):
        self._item = item

    async def resolve(self, intent):
        return self._item

    async def fetch_item(self, rating_key):
        raise AssertionError(
            "operations and badges are off in this file; nothing may fetch a "
            "live Plex object"
        )


class _Provider:
    name = "TMDB"

    async def fetch(self, request):
        return [ArtCandidate("TMDB", "https://cdn.test/art.png", None, 2000, 3000, 5.0)]


def _resolved():
    return ResolvedItem(
        server="plex", native_id="1", library="Movies", kind="movie",
        title="Dune: Part Two",
        year=2024, season_number=None, episode_number=None,
        root_folder="Dune Part Two (2024)",
        file_path="/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
        parent_native_id=None,
    )


def _intent():
    return RenderIntent(
        kind="movie", title="Dune: Part Two", tmdb_id=693134,
        imdb_id="tt15239678", year=2024, rating_key="1",
    )


def _fake_http():
    async def handler(request):
        # A decodable PNG, not a placeholder: pipeline._download decodes every
        # body it keeps (see conftest.decodable_png).
        return httpx.Response(200, content=decodable_png())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _stub_magick(monkeypatch):
    """``compose_styled`` styles ``working`` in place, so a no-op compositor
    leaves the downloaded bytes exactly as they arrived -- which is what makes
    the published size an exact, asserted number."""
    monkeypatch.setattr(pipeline.compositor, "run", lambda argv: None)
    monkeypatch.setattr(
        pipeline, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )


async def _renders(session):
    return (await session.execute(select(Render).order_by(Render.art_kind))).scalars().all()


async def _seed(session, library, rows):
    """One media_items row plus one renders row per ``rows`` entry.

    ``rows`` is a list of ``(art_kind, status, size_bytes)``. A fresh item per
    call, keyed by the library name and the call count, so a test can seed two
    libraries without inventing rating keys by hand.
    """
    item = MediaItem(
        rating_key=f"{library}-{len(rows)}-{id(rows)}",
        library=library, kind="movie", title=f"{library} title",
    )
    session.add(item)
    await session.flush()
    for art_kind, status, size_bytes in rows:
        session.add(Render(
            item_id=item.id, art_kind=art_kind, asset_path=FAKE_ASSET,
            status=status, size_bytes=size_bytes,
        ))
    await session.commit()
    return item


# --- the stamp -------------------------------------------------------------


async def test_a_rendered_asset_carries_its_byte_size(session, tmp_path, monkeypatch):
    """The whole column, through the real entry point: after a pass, every
    render row that produced a file knows how big that file is -- and knows it
    from the file, not from a guess."""
    _stub_magick(monkeypatch)
    expected = len(decodable_png())

    async with _fake_http() as http:
        await pipeline.process_item(
            session, _config(tmp_path), http, _FakePlex(_resolved()),
            [_Provider()], _intent(),
        )

    session.expire_all()
    rendered = [row for row in await _renders(session) if row.status == "rendered"]
    assert rendered, "the pass produced no rendered row at all"
    for row in rendered:
        assert row.size_bytes == expected, f"{row.art_kind} did not record its size"
        assert Path(row.asset_path).stat().st_size == expected, (
            "the recorded size and the file on disk disagree"
        )


async def test_a_render_from_a_manual_override_carries_its_byte_size(
    session, tmp_path, monkeypatch
):
    """The manual path, end to end. ``POST /api/items/{id}/artwork/{kind}/manual``
    writes an override SOURCE under ``manual_assets_root`` and enqueues a
    reprocess -- it never writes a ``renders`` asset itself -- so the size of
    an operator's own artwork is stamped here, on the pass that publishes it.
    This seeds the override file the endpoint would have written and drives
    the same pass."""
    _stub_magick(monkeypatch)
    config = _config(tmp_path)
    override = pipeline.manual_override_target(config, _resolved(), "poster")
    override.parent.mkdir(parents=True, exist_ok=True)
    # A DIFFERENT size from the provider's PNG, so the assertion cannot pass
    # by reading the wrong file.
    override.write_bytes(decodable_png(size=(64, 96)))
    expected = override.stat().st_size

    async with _fake_http() as http:
        await pipeline.process_item(
            session, config, http, _FakePlex(_resolved()), [_Provider()], _intent(),
        )

    session.expire_all()
    poster = [row for row in await _renders(session) if row.art_kind == "poster"]
    assert poster, "the pass produced no poster row"
    assert poster[0].size_bytes == expected, (
        "the poster was published from the manual override but recorded the "
        "provider image's size"
    )


def test_asset_size_answers_none_when_the_file_is_not_there(tmp_path):
    """A stat that fails costs the SIZE, never the render. The row stays NULL
    and the ``asset_stats`` sweep picks it up on its next pass."""
    assert pipeline._asset_size(tmp_path / "missing.jpg") is None

    real = tmp_path / "there.jpg"
    real.write_bytes(b"0123456789")
    assert pipeline._asset_size(real) == 10


# --- the aggregation -------------------------------------------------------


async def test_the_snapshot_groups_by_library_and_art_kind(session):
    await _seed(session, "Movies", [
        ("poster", "rendered", 100),
        ("background", "rendered", 200),
    ])
    await _seed(session, "TV Shows", [("title_card", "rendered", 50)])

    body = await storage_snapshot(session)

    assert set(body["by_library"]) == {"Movies", "TV Shows"}
    movies = body["by_library"]["Movies"]
    assert movies["assets"] == 2
    assert movies["bytes"] == 300
    assert movies["by_art_kind"]["poster"] == {
        "assets": 1, "bytes": 100, "unknown_size": 0
    }
    assert movies["by_art_kind"]["background"] == {
        "assets": 1, "bytes": 200, "unknown_size": 0
    }
    assert body["by_library"]["TV Shows"]["by_art_kind"]["title_card"]["bytes"] == 50
    assert body["totals"] == {
        "items": 2, "assets": 3, "bytes": 350, "unknown_size": 0
    }


async def test_a_null_size_is_counted_as_unknown_and_never_as_zero_bytes(session):
    """The honesty rule. Until the backfill has run, most rows in a deployed
    instance have no size -- reporting them as 0 bytes would make the total
    look like a measurement instead of a floor."""
    await _seed(session, "Movies", [
        ("poster", "rendered", 100),
        ("background", "rendered", None),
    ])

    body = await storage_snapshot(session)

    movies = body["by_library"]["Movies"]
    assert movies["assets"] == 2, "an unsized row is still an asset"
    assert movies["bytes"] == 100, "the NULL row was summed as zero into bytes"
    assert movies["unknown_size"] == 1
    assert movies["by_art_kind"]["background"] == {
        "assets": 1, "bytes": 0, "unknown_size": 1
    }
    assert body["totals"]["unknown_size"] == 1
    assert body["totals"]["bytes"] == 100


async def test_only_rendered_rows_are_counted(session):
    """``status`` is what says a file exists. A ``no_art``/``skipped``/
    ``failed``/``truncated``/``pending`` row names an asset path that was
    never written, and counting it would report storage nobody is using."""
    await _seed(session, "Movies", [
        ("poster", "rendered", 100),
        ("background", "no_art", None),
        ("season_poster", "skipped", None),
        ("title_card", "failed", 999),
    ])

    body = await storage_snapshot(session)

    assert body["totals"] == {
        "items": 1, "assets": 1, "bytes": 100, "unknown_size": 0
    }
    assert body["by_library"]["Movies"]["by_art_kind"]["title_card"]["bytes"] == 0


async def test_every_art_kind_key_is_present_even_at_zero(session):
    """``jobs_by_state``'s rule (api/snapshots.py): always all keys, so a
    Homepage mapping never points at a field that vanished because a library
    happens to have no title cards this week."""
    await _seed(session, "Movies", [("poster", "rendered", 100)])

    body = await storage_snapshot(session)

    kinds = body["by_library"]["Movies"]["by_art_kind"]
    assert tuple(kinds) == ART_KINDS
    assert kinds["title_card"] == {"assets": 0, "bytes": 0, "unknown_size": 0}


async def test_an_empty_database_answers_zeroes_and_no_libraries(session):
    body = await storage_snapshot(session)

    assert body["by_library"] == {}
    assert body["totals"] == {
        "items": 0, "assets": 0, "bytes": 0, "unknown_size": 0
    }
    assert body["generated_at"] is not None


async def test_the_item_total_counts_distinct_items_not_render_rows(session):
    """``items`` cannot be summed out of the grouped query -- four artifacts
    for one show are one item -- so it is its own COUNT(DISTINCT)."""
    await _seed(session, "TV Shows", [
        ("poster", "rendered", 1),
        ("background", "rendered", 1),
        ("season_poster", "rendered", 1),
        ("title_card", "rendered", 1),
    ])

    body = await storage_snapshot(session)

    assert body["totals"]["assets"] == 4
    assert body["totals"]["items"] == 1
