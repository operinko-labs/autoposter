import asyncio
import functools
import hashlib
from pathlib import Path

import httpx
import pytest
from conftest import decodable_png
from sqlalchemy import select

from autoposter.config.loader import load_config, render_version_for
from autoposter.db.models import MediaItem, Render
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import naming
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import (
    ART_KINDS_FOR, _should_skip_title, compute_fingerprint, find_logo_override,
    gather_fingerprint_inputs, logo_override_path, manual_override_path,
    render_artifact, title_text_for,
)
from autoposter.render.textfit import FitResult, prepare_text

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


def item(kind="movie", title="Dune: Part Two", season=None, episode=None, root="Dune (2024)"):
    return ResolvedItem(
        server="plex", native_id="1", library="Movies", kind=kind, title=title, year=2024,
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


def test_should_skip_title_flags_tba(config):
    # Finding 4: reachable only once the resolver returns real episode titles.
    assert _should_skip_title(
        config, item(kind="episode", title="TBA", season=1, episode=1), "title_card"
    ) is not None


def test_should_skip_title_leaves_a_normal_episode_title_alone(config):
    assert _should_skip_title(
        config,
        item(kind="episode", title="Who Is Alive?", season=2, episode=3),
        "title_card",
    ) is None


async def test_tba_title_card_is_skipped_without_writing_a_file(session, tmp_path):
    config = _logo_test_config(tmp_path)
    tba_item = item(kind="episode", title="TBA", season=1, episode=1)
    target = naming.asset_path(
        config, tba_item.library, tba_item.root_folder, "title_card",
        tba_item.season_number, tba_item.episode_number,
    )

    async with _fake_http() as http:
        render = await render_artifact(session, config, http, tba_item, "title_card", [])

    assert render.status == "skipped"
    assert "TBA" in render.detail
    assert not target.exists()


async def test_unnumbered_title_card_is_recorded_as_skipped_not_raised(session, tmp_path):
    """Plex's TV agent hands back ``index: None`` for year-grouped specials.

    The adoption walk already guards these (``naming.missing_number``), but a
    queued render job reaches ``naming._file_name`` directly and raised
    ``title_card requires episode_number`` -- parking ~63 jobs every full pass
    on the production server (roadmap row 125). The render side must record
    the same skip instead: a status the operator can see, no exception, no
    artifact.
    """
    config = _logo_test_config(tmp_path)
    unnumbered = item(kind="episode", title="Episode 05-28", season=0, episode=None)

    async with _fake_http() as http:
        render = await render_artifact(session, config, http, unnumbered, "title_card", [])

    assert render.status == "skipped"
    assert "episode_number" in render.detail
    # No artifact was written anywhere: the assets root was never even created.
    assert not Path(config.assets_root).exists()


async def test_unnumbered_season_poster_is_recorded_as_skipped_not_raised(session, tmp_path):
    """``season_poster`` needs ``season_number``; same guard, other raise site."""
    config = _logo_test_config(tmp_path)
    unnumbered = item(kind="season", title="2021", season=None)

    async with _fake_http() as http:
        render = await render_artifact(session, config, http, unnumbered, "season_poster", [])

    assert render.status == "skipped"
    assert "season_number" in render.detail
    assert not Path(config.assets_root).exists()


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


async def test_concurrent_upserts_of_the_same_identity_succeed_and_leave_one_row(
    session_factory,
):
    # Two workers can both resolve a fresh job for the same item while an earlier
    # one is still running (spec: finding 3). A select-then-insert would race;
    # the Postgres upsert must not.
    from autoposter.render.pipeline import _upsert_media_item
    from autoposter.servers.identity import identity_key_for

    async def upsert(title):
        async with session_factory() as s:
            await _upsert_media_item(s, item(title=title))
            await s.commit()

    await asyncio.gather(upsert("Dune: Part Two"), upsert("Dune: Part Two (Extended)"))

    async with session_factory() as s:
        rows = (
            await s.execute(
                select(MediaItem).where(MediaItem.identity_key == identity_key_for(item()))
            )
        ).scalars().all()
    assert len(rows) == 1


async def test_second_upsert_of_the_same_identity_refreshes_updated_at(session_factory):
    # on_conflict_do_update is an INSERT statement, so SQLAlchemy's onupdate=
    # hook (which only fires for genuine UPDATEs) never runs on its own; the
    # set_ mapping must refresh updated_at explicitly on every conflict.
    # Compare the two timestamps against each other, not against the host
    # clock: this machine's Postgres clock lags the host clock by several
    # seconds (see project constraints).
    from autoposter.render.pipeline import _upsert_media_item
    from autoposter.servers.identity import identity_key_for

    async with session_factory() as s:
        await _upsert_media_item(s, item(title="Dune: Part Two"))
        await s.commit()

    async with session_factory() as s:
        first = (
            await s.execute(
                select(MediaItem).where(MediaItem.identity_key == identity_key_for(item()))
            )
        ).scalar_one()

    await asyncio.sleep(1.1)

    async with session_factory() as s:
        await _upsert_media_item(s, item(title="Dune: Part Two (Extended)"))
        await s.commit()

    async with session_factory() as s:
        second = (
            await s.execute(
                select(MediaItem).where(MediaItem.identity_key == identity_key_for(item()))
            )
        ).scalar_one()

    assert second.created_at == first.created_at
    # The contract is that the conflict path RE-STAMPS updated_at (onupdate=
    # never fires on INSERT ... ON CONFLICT DO UPDATE); equality is the one
    # shape the regression produces. Strict `>` additionally assumed the DB
    # wall clock is monotonic across the sleep, and the hardening-sweep loop
    # reproduced a backwards step (row 195's close has the numbers) -- an
    # environment fact, not an upsert defect.
    assert second.updated_at != first.updated_at


async def test_show_two_seasons_and_two_episodes_produce_five_distinct_rows(
    session_factory, config
):
    # Regression guard: before the fix, PlexClient.resolve() returned the show's
    # own rating key for every season/episode intent, so upserting a show, two
    # of its seasons and two of its episodes collapsed onto one media_items row
    # (whose kind/season_number/episode_number churned as each intent overwrote
    # the last) and three renders rows (later intents overwriting earlier ones'
    # season_poster/title_card). With each item keyed on its own identity
    # (spec §4.2), the same five intents must produce five distinct rows in
    # each table, with distinct asset_paths and fingerprints, and parent_id
    # wired up to the show for every season and episode -- identity's parent
    # is always the show, never a season, ``parent_identity_key_for`` builds
    # a "show" key regardless of the child's own kind. Exercises the real
    # upsert functions (_upsert_media_item, _get_or_create_render) against
    # the live test database, not a mock.
    from autoposter.render.pipeline import _get_or_create_render, _upsert_media_item
    from autoposter.servers.identity import identity_key_for

    show = ResolvedItem(
        server="plex", native_id="900", library="Shows", kind="show",
        title="Severance", year=2022,
        season_number=None, episode_number=None, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
    )
    season1 = ResolvedItem(
        server="plex", native_id="901", library="Shows", kind="season",
        title="Season 1", year=None,
        season_number=1, episode_number=None, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
        parent_native_id="900", parent_tvdb_id=371980,
    )
    season2 = ResolvedItem(
        server="plex", native_id="902", library="Shows", kind="season",
        title="Season 2", year=None,
        season_number=2, episode_number=None, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
        parent_native_id="900", parent_tvdb_id=371980,
    )
    episode1 = ResolvedItem(
        server="plex", native_id="903", library="Shows", kind="episode",
        title="Who Is Alive?", year=None,
        season_number=2, episode_number=3, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
        parent_native_id="902", parent_tvdb_id=371980,
    )
    episode2 = ResolvedItem(
        server="plex", native_id="904", library="Shows", kind="episode",
        title="Woe's Hollow", year=None,
        season_number=2, episode_number=4, root_folder="Severance (2022)",
        file_path=None, art_url=None, tmdb_id=None, tvdb_id=371980, imdb_id=None,
        parent_native_id="902", parent_tvdb_id=371980,
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
                render_version_for(art_kind, config), art_kind, None, None,
                [t for t in (primary, secondary) if t],
            )
            render = await _get_or_create_render(s, media_item, art_kind, target)
            render.fingerprint = fingerprint
        await s.commit()

    async with session_factory() as s:
        media_rows = (await s.execute(select(MediaItem))).scalars().all()
        render_rows = (await s.execute(select(Render))).scalars().all()

    assert len(media_rows) == 5
    assert {row.identity_key for row in media_rows} == {
        identity_key_for(resolved) for resolved, _ in entries
    }
    assert len(render_rows) == 5
    assert len({row.asset_path for row in render_rows}) == 5
    assert len({row.fingerprint for row in render_rows}) == 5

    by_identity_key = {row.identity_key: row for row in media_rows}
    show_row = by_identity_key[identity_key_for(show)]
    assert show_row.parent_id is None
    for child in (season1, season2, episode1, episode2):
        assert by_identity_key[identity_key_for(child)].parent_id == show_row.id


class _LogoAwareProvider:
    """Serves a poster candidate always, and a logo candidate if configured."""

    name = "TMDB"

    def __init__(self, logo_url: str | None = None):
        self._logo_url = logo_url
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if request.art_kind == "poster":
            return [ArtCandidate("TMDB", "https://img/poster.jpg", None, 2000, 3000, 5.0)]
        if request.art_kind == "logo" and self._logo_url is not None:
            return [ArtCandidate("TMDB", self._logo_url, "en", 800, 300, 5.0)]
        return []


def _fake_http():
    async def handler(request):
        # A real, decodable PNG rather than a placeholder byte string:
        # ``pipeline._download`` now decodes every body it keeps, so a fake
        # that served non-image bytes would exercise the refusal path in every
        # test on this page rather than the behaviour each one is about.
        return httpx.Response(200, content=decodable_png())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_download_forwards_headers_and_follow_redirects(tmp_path):
    seen = {}

    async def handler(request):
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, content=decodable_png())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        destination = tmp_path / "base.jpg"
        sha = await pipeline_module._download(
            http, "https://plex.local/x.jpg", destination, stage="the title_card source",
            headers={"X-Plex-Token": "tok"}, follow_redirects=False,
        )

    assert sha
    assert seen["headers"]["x-plex-token"] == "tok"


async def test_download_headers_default_to_none_and_redirects_default_to_true(tmp_path):
    """Every pre-existing call site passes neither kwarg -- this pins that the
    defaults reproduce today's behaviour exactly."""
    import inspect

    signature = inspect.signature(pipeline_module._download)
    assert signature.parameters["headers"].default is None
    assert signature.parameters["follow_redirects"].default is True


class _FakeGeneratedEntry:
    def __init__(self, rating_key, key):
        self.ratingKey = rating_key
        self.key = key


class _FakeGeneratedPlexItem:
    """A plex_item stand-in whose posters() answers a fixed listing -- the
    shape the live probe recorded (upload:// entries plus one media://)."""

    def __init__(self, listing):
        self._listing = listing

    def posters(self):
        return self._listing


class _FakePlexForGenerated:
    capabilities = frozenset({pipeline_module.CAP_TITLE_CARD_URL})

    def __init__(self, plex_item, expected_rating_key="900"):
        self._plex_item = plex_item
        self._expected_rating_key = expected_rating_key

    async def fetch_item(self, rating_key):
        assert rating_key == self._expected_rating_key
        return self._plex_item


GENERATED_LISTING_NO_SELF_FEED = [
    _FakeGeneratedEntry("metadata://posters/x", "/library/metadata/900/file?url=metadata..."),
    _FakeGeneratedEntry(
        "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
        "/library/metadata/900/file?url=media%3A%2F%2F5%2Fx.bundle...",
    ),
]

GENERATED_LISTING_WITH_SELF_FEED = [
    _FakeGeneratedEntry("upload://abc123", "/library/metadata/900/file?url=upload..."),
    _FakeGeneratedEntry(
        "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
        "/library/metadata/900/file?url=media%3A%2F%2F5%2Fx.bundle...",
    ),
]


async def test_fetch_plex_generated_base_downloads_the_media_entry(tmp_path):
    plex_item = _FakeGeneratedPlexItem(GENERATED_LISTING_NO_SELF_FEED)
    plex = _FakePlexForGenerated(plex_item)
    seen = {}

    async def handler(request):
        seen["headers"] = dict(request.headers)
        seen["url"] = str(request.url)
        return httpx.Response(200, content=decodable_png())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        destination = tmp_path / "base.jpg"
        sha = await pipeline_module.fetch_plex_generated_base(
            http, plex, "900", destination,
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
            stage="the title_card source",
        )

    assert sha
    assert destination.exists()
    assert seen["headers"]["x-plex-token"] == "tok"
    assert "media" in seen["url"] or "thumb1" in seen["url"]


async def test_fetch_plex_generated_base_ignores_our_own_upload(tmp_path):
    """The self-feed pin: a listing with an upload:// entry (ours) selected
    ahead of the media:// entry still resolves the media:// frame."""
    plex_item = _FakeGeneratedPlexItem(GENERATED_LISTING_WITH_SELF_FEED)
    plex = _FakePlexForGenerated(plex_item)

    async def handler(request):
        assert "upload" not in str(request.url)
        return httpx.Response(200, content=decodable_png())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sha = await pipeline_module.fetch_plex_generated_base(
            http, plex, "900", tmp_path / "base.jpg",
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
            stage="the title_card source",
        )

    assert sha


async def test_fetch_plex_generated_base_is_none_without_a_media_entry(tmp_path):
    plex_item = _FakeGeneratedPlexItem([
        _FakeGeneratedEntry("upload://abc123", "/library/metadata/900/file?url=upload..."),
    ])
    plex = _FakePlexForGenerated(plex_item)

    async def unreachable(request):
        raise AssertionError("no media:// entry -- _download must not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as http:
        sha = await pipeline_module.fetch_plex_generated_base(
            http, plex, "900", tmp_path / "base.jpg",
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
            stage="the title_card source",
        )

    assert sha is None


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


# --- the picked-logo override -----------------------------------------------
#
# A logo has no art kind downstream -- no render row, no naming path, no
# ART_KINDS_FOR entry -- so the picker's choice is carried by a file beside the
# poster's own override, and the only thing that makes it stick is that the
# render path consults it before the provider ladder and folds its sha into the
# poster's fingerprint. Both halves are pinned here: without the first, a picked
# logo is never used; without the second, it is used once and then every later
# pass reports "unchanged" and the poster keeps whatever it already had.


def _plant_logo(config, resolved, content: bytes = b"the operator's own logo") -> Path:
    path = logo_override_path(config, resolved, ".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_the_logo_override_lives_beside_the_poster_under_library_folders(tmp_path):
    config = _logo_test_config(tmp_path)
    assert config.library_folders is True

    assert logo_override_path(config, item(), ".png") == (
        Path(config.manual_assets_root) / "Movies" / "Dune (2024)" / "logo.png"
    )


def test_the_flat_layout_prefixes_the_logo_with_the_item_folder(tmp_path):
    """``library_folders: false`` is one flat directory, so ``logo.png`` on its
    own would be one file shared by the whole library."""
    config = _logo_test_config(tmp_path)
    config.library_folders = False

    assert logo_override_path(config, item(), ".png") == (
        Path(config.manual_assets_root) / "Dune (2024)_logo.png"
    )


def test_no_override_file_means_no_override(tmp_path):
    config = _logo_test_config(tmp_path)
    assert find_logo_override(config, item()) is None


def test_the_override_is_found_under_any_of_the_artwork_suffixes(tmp_path):
    config = _logo_test_config(tmp_path)
    path = logo_override_path(config, item(), ".webp")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"a webp logo")

    assert find_logo_override(config, item()) == path


async def test_a_picked_logo_is_used_and_no_provider_is_asked_for_one(
    session, tmp_path, monkeypatch
):
    """The consult comes first. A provider ladder run for the logo would both
    cost an outbound request the operator's choice made pointless and -- since
    the ladder's pick would then be staged -- overwrite that choice."""
    config = _logo_test_config(tmp_path)
    resolved = item()
    logo = _plant_logo(config, resolved)
    calls, logo_calls = _stub_out_imagemagick(monkeypatch)
    provider = _LogoAwareProvider(logo_url="https://img/ladder-logo.png")

    async with _fake_http() as http:
        render = await render_artifact(session, config, http, resolved, "poster", [provider])

    assert render.status == "rendered"
    assert len(logo_calls) == 1
    assert [request.art_kind for request in provider.requests] == ["poster"]
    # Composited from a staged copy in the render's tmpdir, never from the
    # manual mount itself -- ImageMagick must not read the operator's file.
    staged = logo_calls[0][0][2]
    assert Path(staged).name == "logo.png"
    assert Path(staged).parent != logo.parent
    flat = [token for call in calls for token in call]
    assert not any(str(token).startswith("caption:") for token in flat)


async def test_the_poster_fingerprint_carries_the_override_logos_own_sha(
    session, tmp_path, monkeypatch
):
    """Through ``gather_fingerprint_inputs``, the single definition of what a
    fingerprint is made of -- so the adoption walk and the short-circuit that
    reads it cannot drift from what the render actually did."""
    config = _logo_test_config(tmp_path)
    resolved = item()
    logo = _plant_logo(config, resolved)
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, resolved, "poster",
            [_LogoAwareProvider(logo_url="https://img/ladder-logo.png")],
        )

    logo_sha = hashlib.sha256(logo.read_bytes()).hexdigest()
    text_inputs, asset_hashes = await gather_fingerprint_inputs(
        config, resolved, "poster", draw_text=False, logo_sha=logo_sha,
    )
    assert render.fingerprint == compute_fingerprint(
        render_version_for("poster", config), "poster", render.source_url,
        render.base_sha256, text_inputs, asset_hashes,
    )


async def test_replacing_the_override_logo_re_renders_the_poster(
    session, tmp_path, monkeypatch
):
    """The whole point of hashing the file rather than naming it: an operator
    picking a different logo changes no file name, and a fingerprint that
    stopped at the name would report "unchanged" for ever."""
    config = _logo_test_config(tmp_path)
    resolved = item()
    logo = _plant_logo(config, resolved, b"logo one")
    _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        first = (await render_artifact(
            session, config, http, resolved, "poster", [_LogoAwareProvider()],
        )).fingerprint
        logo.write_bytes(b"logo two")
        second = await render_artifact(
            session, config, http, resolved, "poster", [_LogoAwareProvider()],
        )

    assert second.fingerprint != first
    assert second.detail != "unchanged"


async def test_use_logo_false_ignores_the_override_file(session, tmp_path, monkeypatch):
    """Config wins, absolutely. ``use_logo: false`` is an operator saying this
    deployment does not composite logos; a file on a mount does not overrule
    it, and the poster gets its title text as it would have without one."""
    config = _logo_test_config(tmp_path)
    config.artwork.use_logo = False
    resolved = item()
    _plant_logo(config, resolved)
    calls, logo_calls = _stub_out_imagemagick(monkeypatch)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, resolved, "poster", [_LogoAwareProvider()],
        )

    assert render.status == "rendered"
    assert logo_calls == []
    flat = [token for call in calls for token in call]
    assert any(str(token).startswith("caption:") for token in flat)


# --- the show-poster fallback for season posters ------------------------------
#
# Roadmap row 132. A season poster is classically the show's own poster with the
# season text applied -- which is exactly what this pipeline does to whatever
# base image it is handed -- so a season with no season-specific art on any
# provider should be styled from the show's poster rather than recorded as
# no_art. 19 production items sat at no_art for that reason.


SEASON_URL = "https://img/season2.jpg"
SHOW_URL = "https://img/show-poster.jpg"


class _SeasonAwareProvider:
    """Serves season-poster art, show-poster art, both or neither, on demand."""

    name = "TMDB"

    def __init__(self, *, season_art: bool, show_art: bool):
        self._season_art = season_art
        self._show_art = show_art
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if request.art_kind == "season_poster" and self._season_art:
            return [ArtCandidate("TMDB", SEASON_URL, None, 2000, 3000, 5.0)]
        if request.art_kind == "poster" and self._show_art:
            return [ArtCandidate("TMDB", SHOW_URL, None, 2000, 3000, 5.0)]
        return []


def _season_item():
    return item(kind="season", title="Season 2", season=2, root="Severance (2022)")


async def test_season_poster_falls_back_to_the_shows_poster_when_no_season_art(
    session, tmp_path, monkeypatch
):
    """The point of the row: no season art is not the same as no art."""
    config = _logo_test_config(tmp_path)
    calls, _ = _stub_out_imagemagick(monkeypatch)
    provider = _SeasonAwareProvider(season_art=False, show_art=True)
    resolved = _season_item()

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, resolved, "season_poster", [provider],
        )

    assert render.status == "rendered"
    assert render.source_url == SHOW_URL
    # Provenance: this row must be distinguishable from one built on the
    # season's own art, both in the operator-facing detail and in a column that
    # survives the "unchanged" short-circuit on the next pass.
    assert render.source_mode == "show_fallback"
    assert "show" in (render.detail or "")

    # The season ladder was asked first, at season scope; the fallback asks the
    # SHOW's poster ladder, with the show's own ids and no season number.
    assert [request.art_kind for request in provider.requests] == [
        "season_poster", "poster",
    ]
    season_request, show_request = provider.requests
    assert season_request.season_number == 2
    assert show_request.season_number is None
    assert show_request.episode_number is None
    assert show_request.tmdb_id == resolved.tmdb_id
    assert show_request.is_movie is False

    # And the season text styling still ran, exactly as for real season art --
    # that is what makes the show's poster into this season's poster.
    flat = [str(token) for call in calls for token in call]
    season_text = prepare_text("Season 2", config.artwork.season_poster.text)
    assert any(t.startswith("caption:") and season_text in t for t in flat)


async def test_a_season_whose_show_has_no_poster_art_either_is_still_no_art(
    session, tmp_path, monkeypatch
):
    """The fallback adds a second chance, it does not invent one."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    provider = _SeasonAwareProvider(season_art=False, show_art=False)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _season_item(), "season_poster", [provider],
        )

    assert render.status == "no_art"
    assert render.detail == "no season_poster art on any provider"
    assert render.source_mode == "generate"
    assert not Path(config.assets_root).exists()


async def test_season_art_that_exists_is_used_and_the_show_is_never_asked(
    session, tmp_path, monkeypatch
):
    """Ladder preference is untouched: the fallback is only for the empty case.

    Mutation proof for the guard -- dropping ``selection.candidate is None``
    from the fallback condition reds this on both assertions at once.
    """
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    provider = _SeasonAwareProvider(season_art=True, show_art=True)

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _season_item(), "season_poster", [provider],
        )

    assert render.status == "rendered"
    assert render.source_url == SEASON_URL
    assert render.source_mode == "generate"
    assert [request.art_kind for request in provider.requests] == ["season_poster"]


async def test_season_art_appearing_later_re_renders_and_clears_the_fallback(
    session, tmp_path, monkeypatch
):
    """The fingerprint carries the source URL, so a fallen-back row is not
    frozen: once a provider gains season art the ladder prefers it, the
    fingerprint changes, and the next pass re-renders from the season's own art
    -- which must also stop the row claiming a fallback it no longer made."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = _season_item()

    async with _fake_http() as http:
        first = await render_artifact(
            session, config, http, resolved, "season_poster",
            [_SeasonAwareProvider(season_art=False, show_art=True)],
        )
        fallback_fingerprint = first.fingerprint
        assert first.source_mode == "show_fallback"

        second = await render_artifact(
            session, config, http, resolved, "season_poster",
            [_SeasonAwareProvider(season_art=True, show_art=True)],
        )

    assert second.fingerprint != fallback_fingerprint
    assert second.detail != "unchanged"
    assert second.source_url == SEASON_URL
    assert second.source_mode == "generate"


TITLE_CARD_URL = "https://img/title-card.jpg"


class _TitleCardAwareProvider:
    """Serves a title_card candidate only when configured to."""

    name = "TMDB"

    def __init__(self, *, has_art: bool):
        self._has_art = has_art
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if request.art_kind == "title_card" and self._has_art:
            return [ArtCandidate("TMDB", TITLE_CARD_URL, None, 1920, 1080, 5.0)]
        return []


def _episode_item():
    return item(kind="episode", title="Chapter One", season=1, episode=1, root="Severance (2022)")


def _plex_generated_base_for(http, listing, rating_key="1"):
    plex_item = _FakeGeneratedPlexItem(listing)
    plex = _FakePlexForGenerated(plex_item, expected_rating_key=rating_key)
    return functools.partial(
        pipeline_module.fetch_plex_generated_base, http, plex,
        base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
    )


async def test_title_card_gate_off_when_a_provider_has_art(session, tmp_path, monkeypatch):
    """The gate-off pin: a provider offering a title card is untouched, and
    the Plex rung is never even asked."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    provider = _TitleCardAwareProvider(has_art=True)

    async def unreachable(*args, **kwargs):
        raise AssertionError("the Plex rung must not run when a provider has art")

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _episode_item(), "title_card", [provider],
            plex_generated_base=unreachable,
        )

    assert render.status == "rendered"
    assert render.source_url == TITLE_CARD_URL
    assert render.source_mode == "generate"


async def test_title_card_falls_back_to_plexs_generated_frame(session, tmp_path, monkeypatch):
    """No provider has a title card; Plex's own derived frame (the media://
    entry, never the agent guess -- C5) becomes the base."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    provider = _TitleCardAwareProvider(has_art=False)
    resolved = _episode_item()

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, resolved, "title_card", [provider],
            plex_generated_base=_plex_generated_base_for(http, GENERATED_LISTING_NO_SELF_FEED),
        )

    assert render.status == "rendered"
    assert render.source_url == f"plex://{resolved.native_id}/title_card"
    assert render.source_mode == "plex_generated"
    assert render.provider == "plex"
    assert render.provider_rank is None
    assert render.selected_language is None
    assert "Plex's generated frame" in (render.detail or "")


async def test_title_card_stays_no_art_when_plex_has_no_generated_frame(
    session, tmp_path, monkeypatch
):
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    provider = _TitleCardAwareProvider(has_art=False)
    listing_without_generated = [
        _FakeGeneratedEntry("upload://abc123", "/library/metadata/1/file?url=upload..."),
        _FakeGeneratedEntry("com.plexapp.agents.themoviedb://1", "https://image.tmdb.org/x.jpg"),
    ]

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _episode_item(), "title_card", [provider],
            plex_generated_base=_plex_generated_base_for(http, listing_without_generated),
        )

    assert render.status == "no_art"
    assert render.detail == "no title_card art on any provider"
    assert render.source_mode == "generate"
    assert not Path(config.assets_root).exists()


async def test_title_card_art_appearing_later_re_renders_and_clears_the_fallback(
    session, tmp_path, monkeypatch
):
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = _episode_item()

    async with _fake_http() as http:
        first = await render_artifact(
            session, config, http, resolved, "title_card",
            [_TitleCardAwareProvider(has_art=False)],
            plex_generated_base=_plex_generated_base_for(http, GENERATED_LISTING_NO_SELF_FEED),
        )
        fallback_fingerprint = first.fingerprint
        assert first.source_mode == "plex_generated"

        second = await render_artifact(
            session, config, http, resolved, "title_card",
            [_TitleCardAwareProvider(has_art=True)],
            plex_generated_base=_plex_generated_base_for(http, GENERATED_LISTING_NO_SELF_FEED),
        )

    assert second.fingerprint != fallback_fingerprint
    assert second.source_url == TITLE_CARD_URL
    assert second.source_mode == "generate"


async def test_title_card_losing_its_generated_frame_clears_stale_source_mode(
    session, tmp_path, monkeypatch
):
    """L1: a row that rendered plex_generated earlier and later finds no
    media:// entry (Plex frame generation turned off, bundle pruned) must
    not keep claiming a fallback it no longer made -- the no_art arm clears
    source_mode the same symmetric way the write-back's own elif does."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = _episode_item()
    provider = _TitleCardAwareProvider(has_art=False)
    listing_without_generated = [
        _FakeGeneratedEntry("upload://abc123", "/library/metadata/1/file?url=upload..."),
    ]

    async with _fake_http() as http:
        first = await render_artifact(
            session, config, http, resolved, "title_card", [provider],
            plex_generated_base=_plex_generated_base_for(http, GENERATED_LISTING_NO_SELF_FEED),
        )
        assert first.source_mode == "plex_generated"

        second = await render_artifact(
            session, config, http, resolved, "title_card", [provider],
            plex_generated_base=_plex_generated_base_for(http, listing_without_generated),
        )

    assert second.status == "no_art"
    assert second.source_mode == "generate"


async def test_title_card_fingerprint_is_stable_across_a_bumped_plex_thumb_epoch(
    session, tmp_path, monkeypatch
):
    """The stability pin (C1.3): the key Plex serves the frame from can
    change (our own lockPoster bumps its epoch every pass) without moving the
    fingerprint -- only the BYTES (base_sha256) decide re-render, because
    source_url is the stable synthetic key, never the live URL."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = _episode_item()
    provider = _TitleCardAwareProvider(has_art=False)
    listing_epoch_1 = [
        _FakeGeneratedEntry(
            "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
            "/library/metadata/1/file?url=media%3A%2F%2F5%2Fx.bundle...&epoch=1",
        ),
    ]
    listing_epoch_2 = [
        _FakeGeneratedEntry(
            "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
            "/library/metadata/1/file?url=media%3A%2F%2F5%2Fx.bundle...&epoch=2",
        ),
    ]

    async with _fake_http() as http:
        first = await render_artifact(
            session, config, http, resolved, "title_card", [provider],
            plex_generated_base=_plex_generated_base_for(http, listing_epoch_1),
        )
        second = await render_artifact(
            session, config, http, resolved, "title_card", [provider],
            plex_generated_base=_plex_generated_base_for(http, listing_epoch_2),
        )

    assert second.fingerprint == first.fingerprint
    assert second.detail == "unchanged"


def test_library_language_overrides_are_per_library_and_per_art_kind():
    """Row 38. An art kind the override does not name keeps its own order --
    which is how 'the title card keeps its xx lead' is expressed: name the
    other kinds and leave title_card out."""
    from autoposter.config.loader import load_config
    from autoposter.render.pipeline import language_order_for

    config = load_config(Path("config/autoposter.example.yaml"))
    plain = language_order_for(config, "Movies", "poster")
    assert plain == config.artwork.poster.language_order

    overridden = config.model_copy(update={
        "artwork": config.artwork.model_copy(update={
            "library_language_overrides": {"Anime": {"poster": ["ja", "en"]}},
        }),
    })
    assert language_order_for(overridden, "Anime", "poster") == ["ja", "en"]
    assert language_order_for(overridden, "Anime", "title_card") == (
        overridden.artwork.title_card.language_order
    )
    assert language_order_for(overridden, "Movies", "poster") == (
        overridden.artwork.poster.language_order
    )
