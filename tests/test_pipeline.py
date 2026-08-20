import asyncio
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem, Render
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import naming
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import (
    ART_KINDS_FOR, compute_fingerprint, manual_override_path, render_artifact,
    title_text_for,
)
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


def item(kind="movie", title="Dune: Part Two", season=None, episode=None, root="Dune (2024)"):
    return ResolvedItem(
        rating_key="1", library="Movies", kind=kind, title=title, year=2024,
        season_number=season, episode_number=episode, root_folder=root,
        file_path="/mnt/Media/Movies/Dune (2024)/x.mkv", art_url=None,
        tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
    )


def test_each_item_kind_maps_to_its_artifacts():
    assert ART_KINDS_FOR["movie"] == ["poster", "background"]
    assert ART_KINDS_FOR["show"] == ["poster", "background"]
    assert ART_KINDS_FOR["season"] == ["season_poster"]
    assert ART_KINDS_FOR["episode"] == ["title_card"]


def test_fingerprint_is_stable_for_identical_inputs():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    assert a == b


def test_fingerprint_changes_with_the_source_image():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v1", "poster", "http://x/a.jpg", "def", ["DUNE"])
    assert a != b


def test_fingerprint_changes_with_the_config_version():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v2", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    assert a != b


def test_fingerprint_changes_with_the_text():
    a = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["DUNE"])
    b = compute_fingerprint("v1", "poster", "http://x/a.jpg", "abc", ["HEAT"])
    assert a != b


def test_fingerprint_changes_when_the_overlay_file_bytes_change(tmp_path):
    from autoposter.render.pipeline import _file_sha256

    overlay = tmp_path / "overlay.png"
    overlay.write_bytes(b"overlay-v1")
    a = compute_fingerprint(
        "v1", "poster", "http://x/a.jpg", "abc", ["DUNE"], [_file_sha256(overlay)]
    )
    overlay.write_bytes(b"overlay-v2")
    b = compute_fingerprint(
        "v1", "poster", "http://x/a.jpg", "abc", ["DUNE"], [_file_sha256(overlay)]
    )
    assert a != b


def test_fingerprint_changes_when_the_font_file_bytes_change(tmp_path):
    from autoposter.render.pipeline import _file_sha256

    font = tmp_path / "Comfortaa-Medium.ttf"
    font.write_bytes(b"font-v1")
    a = compute_fingerprint(
        "v1", "poster", "http://x/a.jpg", "abc", ["DUNE"], [_file_sha256(font)]
    )
    font.write_bytes(b"font-v2")
    b = compute_fingerprint(
        "v1", "poster", "http://x/a.jpg", "abc", ["DUNE"], [_file_sha256(font)]
    )
    assert a != b


def test_missing_asset_file_hashes_to_the_empty_sentinel_without_raising(tmp_path):
    from autoposter.render.pipeline import _file_sha256

    assert _file_sha256(tmp_path / "does-not-exist.ttf") == ""


def test_poster_text_is_the_title(config):
    primary, secondary = title_text_for("poster", item(), config)
    assert primary == "Dune: Part Two"
    assert secondary is None


def test_background_has_no_text(config):
    primary, secondary = title_text_for("background", item(), config)
    assert primary is None
    assert secondary is None


def test_season_poster_text_is_the_season_title(config):
    primary, _ = title_text_for(
        "season_poster", item(kind="season", title="Season 2", season=2), config
    )
    assert primary == "Season 2"


def test_title_card_has_episode_title_and_a_numbering_line(config):
    primary, secondary = title_text_for(
        "title_card",
        item(kind="episode", title="Who Is Alive?", season=2, episode=3),
        config,
    )
    assert primary == "Who Is Alive?"
    assert secondary == "Season 2 • Episode 3"


def test_title_card_numbering_uses_unpadded_numbers(config):
    _, secondary = title_text_for(
        "title_card", item(kind="episode", title="X", season=1, episode=1), config
    )
    assert secondary == "Season 1 • Episode 1"


def test_manual_override_is_found_when_present(config, tmp_path):
    config.manual_assets_root = tmp_path
    target = tmp_path / "Movies" / "Dune (2024)"
    target.mkdir(parents=True)
    (target / "poster.jpg").write_bytes(b"x")
    assert manual_override_path(config, item(), "poster") == target / "poster.jpg"


def test_manual_override_is_none_when_absent(config, tmp_path):
    config.manual_assets_root = tmp_path
    assert manual_override_path(config, item(), "poster") is None


def test_publish_keeps_one_previous_generation(tmp_path):
    from autoposter.render.pipeline import _publish

    assets_root = tmp_path / "assets"
    target = assets_root / "Movies" / "Dune (2024)" / "poster.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    working = tmp_path / "new.jpg"
    working.write_bytes(b"new")
    backup_root = tmp_path / "backup"

    _publish(working, target, backup_root, assets_root)

    assert target.read_bytes() == b"new"
    assert (backup_root / "Movies" / "Dune (2024)" / "poster.jpg").read_bytes() == b"old"


def test_publish_without_an_existing_asset_writes_no_backup(tmp_path):
    from autoposter.render.pipeline import _publish

    assets_root = tmp_path / "assets"
    target = assets_root / "Heat (1995)" / "poster.jpg"
    working = tmp_path / "new.jpg"
    working.write_bytes(b"new")
    backup_root = tmp_path / "backup"

    _publish(working, target, backup_root, assets_root)

    assert target.read_bytes() == b"new"
    assert not backup_root.exists()


def test_publish_keeps_backups_distinct_across_libraries_with_the_same_folder_name(tmp_path):
    from autoposter.render.pipeline import _publish

    assets_root = tmp_path / "assets"
    backup_root = tmp_path / "backup"

    movies_target = assets_root / "Movies" / "Dune (2024)" / "poster.jpg"
    movies_target.parent.mkdir(parents=True)
    movies_target.write_bytes(b"movies-old")

    fourk_target = assets_root / "4K Movies" / "Dune (2024)" / "poster.jpg"
    fourk_target.parent.mkdir(parents=True)
    fourk_target.write_bytes(b"4k-old")

    working = tmp_path / "new.jpg"
    working.write_bytes(b"new")

    _publish(working, movies_target, backup_root, assets_root)
    _publish(working, fourk_target, backup_root, assets_root)

    assert (backup_root / "Movies" / "Dune (2024)" / "poster.jpg").read_bytes() == b"movies-old"
    assert (backup_root / "4K Movies" / "Dune (2024)" / "poster.jpg").read_bytes() == b"4k-old"


def test_publish_removes_the_staging_file_when_replace_fails(tmp_path, monkeypatch):
    from autoposter.render.pipeline import _publish

    assets_root = tmp_path / "assets"
    target = assets_root / "Dune (2024)" / "poster.jpg"
    target.parent.mkdir(parents=True)
    working = tmp_path / "new.jpg"
    working.write_bytes(b"new")

    def boom(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr("autoposter.render.pipeline.os.replace", boom)

    with pytest.raises(OSError):
        _publish(working, target, None, assets_root)

    assert not (target.parent / ".poster.jpg.tmp").exists()


async def test_concurrent_upserts_of_the_same_rating_key_succeed_and_leave_one_row(
    session_factory,
):
    # Two workers can both resolve a fresh job for the same item while an earlier
    # one is still running (spec: finding 3). A select-then-insert would race;
    # the Postgres upsert must not.
    from autoposter.render.pipeline import _upsert_media_item

    async def upsert(title):
        async with session_factory() as s:
            await _upsert_media_item(s, item(title=title))
            await s.commit()

    await asyncio.gather(upsert("Dune: Part Two"), upsert("Dune: Part Two (Extended)"))

    async with session_factory() as s:
        rows = (
            await s.execute(select(MediaItem).where(MediaItem.rating_key == "1"))
        ).scalars().all()
    assert len(rows) == 1


async def test_second_upsert_of_the_same_rating_key_refreshes_updated_at(session_factory):
    # on_conflict_do_update is an INSERT statement, so SQLAlchemy's onupdate=
    # hook (which only fires for genuine UPDATEs) never runs on its own; the
    # set_ mapping must refresh updated_at explicitly on every conflict.
    # Compare the two timestamps against each other, not against the host
    # clock: this machine's Postgres clock lags the host clock by several
    # seconds (see project constraints).
    from autoposter.render.pipeline import _upsert_media_item

    async with session_factory() as s:
        await _upsert_media_item(s, item(title="Dune: Part Two"))
        await s.commit()

    async with session_factory() as s:
        first = (
            await s.execute(select(MediaItem).where(MediaItem.rating_key == "1"))
        ).scalar_one()

    await asyncio.sleep(1.1)

    async with session_factory() as s:
        await _upsert_media_item(s, item(title="Dune: Part Two (Extended)"))
        await s.commit()

    async with session_factory() as s:
        second = (
            await s.execute(select(MediaItem).where(MediaItem.rating_key == "1"))
        ).scalar_one()

    assert second.created_at == first.created_at
    assert second.updated_at > first.updated_at


async def test_show_two_seasons_and_two_episodes_produce_five_distinct_rows(
    session_factory, config
):
    # Regression guard: before the fix, PlexClient.resolve() returned the show's
    # own rating key for every season/episode intent, so upserting a show, two
    # of its seasons and two of its episodes collapsed onto one media_items row
    # (whose kind/season_number/episode_number churned as each intent overwrote
    # the last) and three renders rows (later intents overwriting earlier ones'
    # season_poster/title_card). With each item carrying its own rating key,
    # the same five intents must produce five distinct rows in each table, with
    # distinct asset_paths and fingerprints, and parent_id wired up as each
    # parent is processed before its children. Exercises the real upsert
    # functions (_upsert_media_item, _get_or_create_render) against the live
    # test database, not a mock.
    from autoposter.render.pipeline import _get_or_create_render, _upsert_media_item

    show = ResolvedItem(
        rating_key="900", library="Shows", kind="show", title="Severance", year=2022,
        season_number=None, episode_number=None, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
        parent_rating_key=None,
    )
    season1 = ResolvedItem(
        rating_key="901", library="Shows", kind="season", title="Season 1", year=None,
        season_number=1, episode_number=None, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
        parent_rating_key="900",
    )
    season2 = ResolvedItem(
        rating_key="902", library="Shows", kind="season", title="Season 2", year=None,
        season_number=2, episode_number=None, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
        parent_rating_key="900",
    )
    episode1 = ResolvedItem(
        rating_key="903", library="Shows", kind="episode", title="Who Is Alive?", year=None,
        season_number=2, episode_number=3, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
        parent_rating_key="902",
    )
    episode2 = ResolvedItem(
        rating_key="904", library="Shows", kind="episode", title="Woe's Hollow", year=None,
        season_number=2, episode_number=4, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
        parent_rating_key="902",
    )

    entries = [
        (show, "poster"),
        (season1, "season_poster"),
        (season2, "season_poster"),
        (episode1, "title_card"),
        (episode2, "title_card"),
    ]

    async with session_factory() as s:
        for resolved, art_kind in entries:
            media_item = await _upsert_media_item(s, resolved)
            target = naming.asset_path(
                config, resolved.library, resolved.root_folder, art_kind,
                resolved.season_number, resolved.episode_number,
            )
            primary, secondary = title_text_for(art_kind, resolved, config)
            fingerprint = compute_fingerprint(
                config.version, art_kind, None, None,
                [t for t in (primary, secondary) if t],
            )
            render = await _get_or_create_render(s, media_item, art_kind, target)
            render.fingerprint = fingerprint
        await s.commit()

    async with session_factory() as s:
        media_rows = (await s.execute(select(MediaItem))).scalars().all()
        render_rows = (await s.execute(select(Render))).scalars().all()

    assert len(media_rows) == 5
    assert {row.rating_key for row in media_rows} == {"900", "901", "902", "903", "904"}
    assert len(render_rows) == 5
    assert len({row.asset_path for row in render_rows}) == 5
    assert len({row.fingerprint for row in render_rows}) == 5

    by_rating_key = {row.rating_key: row for row in media_rows}
    assert by_rating_key["900"].parent_id is None
    assert by_rating_key["901"].parent_id == by_rating_key["900"].id
    assert by_rating_key["902"].parent_id == by_rating_key["900"].id
    assert by_rating_key["903"].parent_id == by_rating_key["902"].id
    assert by_rating_key["904"].parent_id == by_rating_key["902"].id


class _LogoAwareProvider:
    """Serves a poster candidate always, and a logo candidate if configured."""

    name = "TMDB"

    def __init__(self, logo_url: str | None = None):
        self._logo_url = logo_url

    async def fetch(self, request):
        if request.art_kind == "poster":
            return [ArtCandidate("TMDB", "https://img/poster.jpg", None, 2000, 3000, 5.0)]
        if request.art_kind == "logo" and self._logo_url is not None:
            return [ArtCandidate("TMDB", self._logo_url, "en", 800, 300, 5.0)]
        return []


def _fake_http():
    async def handler(request):
        return httpx.Response(200, content=b"fake-image-bytes")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _stub_out_imagemagick(monkeypatch):
    """Record every argv passed to compositor.run, without invoking ImageMagick.

    fit_point_size is the only other function on this path that shells out, so
    it is stubbed too. This lets render_artifact's logo branching be exercised
    on hosts without ImageMagick installed. build_logo_argv is wrapped (not
    replaced) so its call count can be asserted directly, rather than sniffing
    argv strings for "logo" — pytest's own tmp_path can coincidentally contain
    that substring, since it is derived from the (truncated) test name.
    """
    calls: list[list[str]] = []
    logo_calls: list = []
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: calls.append(argv))
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    original_build_logo_argv = pipeline_module.compositor.build_logo_argv

    def spy_build_logo_argv(*args, **kwargs):
        logo_calls.append((args, kwargs))
        return original_build_logo_argv(*args, **kwargs)

    monkeypatch.setattr(pipeline_module.compositor, "build_logo_argv", spy_build_logo_argv)
    return calls, logo_calls


def _logo_test_config(tmp_path):
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    return config


async def test_poster_composites_the_logo_and_draws_no_text_when_one_is_found(
    session, tmp_path, monkeypatch
):
    config = _logo_test_config(tmp_path)
    calls, logo_calls = _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, item(), "poster",
            [_LogoAwareProvider(logo_url="https://img/logo.png")],
        )

    assert render.status == "rendered"
    assert len(logo_calls) == 1
    flat = [token for call in calls for token in call]
    assert not any(str(token).startswith("caption:") for token in flat)


async def test_poster_draws_neither_logo_nor_text_when_none_found_and_fallback_disabled(
    session, tmp_path, monkeypatch
):
    config = _logo_test_config(tmp_path)
    assert config.artwork.logo_text_fallback is False
    calls, logo_calls = _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, item(), "poster", [_LogoAwareProvider(logo_url=None)],
        )

    assert render.status == "rendered"
    assert logo_calls == []
    flat = [token for call in calls for token in call]
    assert not any(str(token).startswith("caption:") for token in flat)


async def test_poster_falls_back_to_text_when_no_logo_and_fallback_enabled(
    session, tmp_path, monkeypatch
):
    config = _logo_test_config(tmp_path)
    config.artwork.logo_text_fallback = True
    calls, logo_calls = _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, item(), "poster", [_LogoAwareProvider(logo_url=None)],
        )

    assert render.status == "rendered"
    assert logo_calls == []
    flat = [token for call in calls for token in call]
    assert any(str(token).startswith("caption:") for token in flat)
