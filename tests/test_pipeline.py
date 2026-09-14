import asyncio
import functools
import hashlib
import logging
from pathlib import Path

import httpx
import pytest
from conftest import decodable_png
from sqlalchemy import select, update

from autoposter.config.loader import load_config, render_version_for
from autoposter.db.models import MediaItem, MediaItemServerRef, Render, RenderDelivery
from autoposter.db.refs import item_id_for
from autoposter.facts.mdblist import NullMDBListClient
from autoposter.facts.models import GatheredFacts
from autoposter.intake.arr import RenderIntent
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
from autoposter.servers.base import ItemNotFound
from autoposter.servers.registry import Servers
from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, resolved as fake_resolved

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
ORACLE = Path(__file__).parent / "fixtures" / "oracle"


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
    # wired up two hops deep: a season's parent is the show, and an episode's
    # parent is its OWN season, not the show directly (config/impact.py's
    # model; ``parent_identity_key_for`` builds a "show" key for a season and
    # a "season" key for an episode). Exercises the real upsert functions
    # (_upsert_media_item, _get_or_create_render) against the live test
    # database, not a mock.
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
    season2_row = by_identity_key[identity_key_for(season2)]
    assert show_row.parent_id is None
    for season in (season1, season2):
        assert by_identity_key[identity_key_for(season)].parent_id == show_row.id
    # Both episodes are season 2's -- their parent is THAT season row, not
    # the show directly.
    for episode in (episode1, episode2):
        assert by_identity_key[identity_key_for(episode)].parent_id == season2_row.id


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
    entry, never the agent guess) becomes the base."""
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
    """The stability pin: the key Plex serves the frame from can
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


# Spec §10.6: process_item resolves on every configured server,
# renders once, and delivers per server. `render_artifact` and
# `compose_badged_bytes` are both faked here -- neither ImageMagick nor a
# real provider fetch is what these tests are about, and a fake
# `render_artifact` still upserts a REAL `media_items`/`renders` row (via the
# same `_upsert_media_item`/`_get_or_create_render` helpers the real one
# uses), which is what gives `deliveries.record` a real `render_id` to key on.
INTENT = RenderIntent(kind="movie", title="Title", tmdb_id=1, year=2020)


async def _fake_render_artifact(session, config, http, item, art_kind, providers, **_kwargs):
    media_item = await pipeline_module._upsert_media_item(session, item)
    render = await pipeline_module._get_or_create_render(
        session, media_item, art_kind, f"/tmp/{art_kind}.jpg"
    )
    render.status = "rendered"
    await session.commit()
    return render


async def _fake_compose(session, config, render, media_item, **_kwargs):
    return b"badged"


def _two_servers(plex_has=True, jelly_has=True):
    plex = FakeMediaServer(name="plex")
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    if plex_has:
        plex.items[INTENT.dedupe_key] = fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    if jelly_has:
        jf.items[INTENT.dedupe_key] = fake_resolved("jellyfin", "j1", file_path="/jf/m.mkv")
    return Servers({"plex": plex, "jellyfin": jf}), plex, jf


async def test_dual_delivery_records_each_server_and_rolls_up(session, config_with_badges, monkeypatch):
    config_with_badges.badges.upload_to_jellyfin = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    servers, plex, jf = _two_servers()

    renders = await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    poster = next(r for r in renders if r.art_kind == "poster")
    assert [u[0].server for u in plex.uploads] == ["plex"]
    assert [u[0].server for u in jf.uploads] == ["jellyfin"]
    rows = {
        (d.server, d.status)
        for d in (
            await session.execute(select(RenderDelivery).where(RenderDelivery.render_id == poster.id))
        ).scalars()
    }
    assert rows == {("plex", "uploaded"), ("jellyfin", "uploaded")}
    assert poster.upload_status == "uploaded"


async def test_an_unresolved_jellyfin_goes_pending_and_never_holds_plex(
    session, config_with_badges, monkeypatch
):
    config_with_badges.badges.upload_to_jellyfin = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    servers, plex, jf = _two_servers(jelly_has=False)

    renders = await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    poster = next(r for r in renders if r.art_kind == "poster")
    assert plex.uploads and not jf.uploads
    rows = {
        (d.server, d.status)
        for d in (
            await session.execute(select(RenderDelivery).where(RenderDelivery.render_id == poster.id))
        ).scalars()
    }
    assert rows == {("plex", "uploaded"), ("jellyfin", "pending")}
    assert poster.upload_status == "pending"


async def test_jellyfin_only_delivers_once_and_refuses_nothing(
    session, config_with_badges, monkeypatch
):
    config_with_badges.badges.upload_to_jellyfin = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items[INTENT.dedupe_key] = fake_resolved("jellyfin", "j1", file_path="/jf/m.mkv")

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, Servers({"jellyfin": jf}), [], INTENT,
    )

    poster = next(r for r in renders if r.art_kind == "poster")
    assert len(jf.uploads) >= 1
    assert poster.upload_status == "uploaded"


async def test_a_path_mismatch_on_one_server_fails_that_delivery_only(
    session, config_with_badges, monkeypatch
):
    config_with_badges.badges.upload_to_jellyfin = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    servers, plex, jf = _two_servers()
    jf.path_mismatch.add(INTENT.dedupe_key)

    renders = await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    poster = next(r for r in renders if r.art_kind == "poster")
    assert poster.upload_status == "failed" and plex.uploads
    # Category plus class name, never the exception's own message -- which
    # for a PathMismatch is a filesystem path (spec §5.2).
    detail = (
        await session.execute(
            select(RenderDelivery.detail).where(
                RenderDelivery.render_id == poster.id, RenderDelivery.server == "jellyfin",
            )
        )
    ).scalar_one()
    assert detail == "error: PathMismatch"


async def test_no_server_resolving_still_defers_the_job(session, config_with_badges, monkeypatch):
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    servers, plex, jf = _two_servers(plex_has=False, jelly_has=False)

    with pytest.raises(ItemNotFound):
        await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)


async def test_a_transport_error_from_one_server_resolve_does_not_abort_the_others(
    session, config_with_badges, monkeypatch, caplog,
):
    """A transport error resolving on ONE server must not
    abort the item for the others -- logged (class name only, never a URL),
    treated as a miss (that server gets `pending`), and the loop continues
    to deliver everything else normally.

    `upload_to_jellyfin` is turned on explicitly: the
    toggle now gates the MISSED half of `deliver` as well as the resolved
    one, so with it off this server's honest outcome would be `skipped` and
    the assertion below would stop testing what this test is named for."""
    config_with_badges.badges.upload_to_jellyfin = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    servers, plex, jf = _two_servers()
    jf.raise_on_resolve = httpx.ConnectError("https://jellyfin.internal/Items")

    with caplog.at_level("WARNING"):
        renders = await pipeline_module.process_item(
            session, config_with_badges, None, servers, [], INTENT,
        )

    poster = next(r for r in renders if r.art_kind == "poster")
    assert plex.uploads
    rows = {
        (d.server, d.status)
        for d in (
            await session.execute(select(RenderDelivery).where(RenderDelivery.render_id == poster.id))
        ).scalars()
    }
    assert ("jellyfin", "pending") in rows

    messages = [r.message for r in caplog.records if "jellyfin" in r.message]
    assert messages, "no warning was logged for the failed resolve"
    assert any("ConnectError" in m for m in messages)
    assert not any("jellyfin.internal" in m for m in messages), "the URL must never reach the log"


async def test_metadata_fan_out_reaches_every_resolved_server_with_its_own_ref(
    session, monkeypatch,
):
    """apply_metadata's write loop (ruling 4) must reach EVERY resolved
    server, each with its OWN ref -- and one server's exempting label must
    never leak into another server's own exemption check."""
    config = load_config(EXAMPLE)
    config.operations.write_to_jellyfin = True
    config.operations.ignore_labels = ["exempt-me"]
    # This test is about the metadata write loop, not badges -- disabled so
    # the badge stage (which would otherwise try to open a real base image
    # `_fake_render_artifact` never wrote) never runs at all.
    config.badges.enabled = False
    plex = FakeMediaServer(name="plex", labels=["exempt-me"])
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    plex.items[INTENT.dedupe_key] = fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    jf.items[INTENT.dedupe_key] = fake_resolved("jellyfin", "j1", file_path="/jf/m.mkv")
    servers = Servers({"plex": plex, "jellyfin": jf})

    class _FakeTMDBFacts:
        async def movie(self, tmdb_id):
            return GatheredFacts(audience_rating=6.3, sources={"audience_rating": "tmdb"})

    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    await pipeline_module.process_item(
        session, config, None, servers, [], INTENT,
        tmdb_facts=_FakeTMDBFacts(), mdblist=NullMDBListClient(),
    )

    assert plex.facts_written == [], "plex's own ignore_labels match must exempt plex, and only plex"
    assert len(jf.facts_written) == 1, "jellyfin must still be written -- plex's label must not leak"
    ref, _facts = jf.facts_written[0]
    assert ref.native_id == "j1", "jellyfin must be written with its OWN ref, not plex's"


async def test_an_unresolved_jellyfin_delivers_from_a_pending_row_once_it_resolves(
    session, config_with_badges, monkeypatch,
):
    """A server added late (or one whose upload_to_<name> was only just
    turned on) must not wait for the NEXT fingerprint change to get its
    first delivery -- an unchanged compose (``data is None``) still catches
    a resolved, upload-enabled server with no delivery row up with a
    `pending` row and no delay, so the very next retry pass delivers it
    from the already-badged asset. Plex, already `uploaded` from an earlier
    pass, must not be touched."""
    from autoposter import deliveries

    config_with_badges.badges.upload_to_jellyfin = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    servers, plex, jf = _two_servers()

    async def _fake_compose_none(*args, **kwargs):
        return None  # an unchanged fingerprint, from a previous pass

    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose_none)

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
    )
    poster = next(r for r in renders if r.art_kind == "poster")
    # Seeds the "already delivered to plex on an earlier pass" state
    # directly: compose_badged_bytes always answers None here (an unchanged
    # fingerprint), so this pass alone could never produce it.
    await deliveries.record(session, poster.id, "plex", "uploaded")
    await deliveries.rollup(session, poster.id)
    await session.commit()

    await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    rows = {
        (d.server, d.status)
        for d in (
            await session.execute(select(RenderDelivery).where(RenderDelivery.render_id == poster.id))
        ).scalars()
    }
    assert rows == {("plex", "uploaded"), ("jellyfin", "pending")}
    assert plex.uploads == [], "the already-uploaded plex row must not be touched"


async def test_an_upload_disabled_server_gets_no_catchup_row(
    session, config_with_badges, monkeypatch,
):
    """The per-library toggle is checked BEFORE the catch-up behavior above
    -- an upload-disabled server must never get a `pending` row
    only for the very next retry pass to immediately rewrite it `skipped`.
    With `upload_to_jellyfin` off and an unchanged fingerprint, jellyfin
    gets no row at all; plex, already `uploaded`, is untouched."""
    from autoposter import deliveries

    config_with_badges.badges.upload_to_jellyfin = False
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    servers, plex, jf = _two_servers()

    async def _fake_compose_none(*args, **kwargs):
        return None  # an unchanged fingerprint, from a previous pass

    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose_none)

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
    )
    poster = next(r for r in renders if r.art_kind == "poster")
    await deliveries.record(session, poster.id, "plex", "uploaded")
    await deliveries.rollup(session, poster.id)
    await session.commit()

    await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    rows = {
        (d.server, d.status)
        for d in (
            await session.execute(select(RenderDelivery).where(RenderDelivery.render_id == poster.id))
        ).scalars()
    }
    assert rows == {("plex", "uploaded")}, "an upload-disabled server must get no row at all"


async def test_a_single_upload_enabled_server_skips_compose_on_matching_provenance(
    session, config_with_badges, monkeypatch,
):
    """With exactly one resolved, upload-enabled server, adoption is
    checked BEFORE any image work -- cutover (a whole library with no
    badge_fingerprint yet, every render already carrying its own EXIF
    fingerprint) must not recompose bytes already sitting on that server.

    Two passes, the ``tests/test_badge_pipeline.py::_fingerprint_of`` shape:
    the first pass badges normally (learning the real fingerprint, since a
    hand-picked string could never match what ``compose_badged_bytes``
    actually computes); the render is then reset to the state adoption or a
    database restore leaves it in, ``artwork_provenance`` answers with the
    now-known fingerprint, and the second pass must neither call
    ``compose_badges`` nor upload -- only record ``uploaded``.
    """
    config_with_badges.badges.adopt_from_plex = True

    async def _fake_render_artifact_with_real_base(session, config, http, item, art_kind, providers, **_kwargs):
        # Unlike the shared `_fake_render_artifact`, this one must point at a
        # REAL image: the first pass below composes for real (there is no
        # fingerprint to adopt onto yet), and `compose_badges` opens
        # `asset_path` with Pillow.
        media_item = await pipeline_module._upsert_media_item(session, item)
        render = await pipeline_module._get_or_create_render(
            session, media_item, art_kind, str(ORACLE / "All_Souls_base_no_overlay.jpg"),
        )
        render.status = "rendered"
        render.base_sha256 = "abc"
        await session.commit()
        return render

    monkeypatch.setattr(
        pipeline_module, "render_artifact", _fake_render_artifact_with_real_base,
    )

    class _FakePlexItem:
        """Just enough for `media_info_from_plex` to read without reloading."""

        def __init__(self):
            self.media = [type("M", (), {
                "parts": [], "videoResolution": "1080",
                "audioCodec": "eac3", "audioChannels": 6,
            })()]
            self.duration = 4845912
            self.seasonNumber = None
            self.episodeNumber = None

    class _ProvenanceServer(FakeMediaServer):
        provenance: str | None = None

        async def fetch_item(self, native_id):
            return _FakePlexItem()

        async def artwork_provenance(self, ref, art_kind):
            return self.provenance

    plex = _ProvenanceServer(name="plex")
    plex.items[INTENT.dedupe_key] = fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    servers = Servers({"plex": plex})

    renders = await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)
    poster = next(r for r in renders if r.art_kind == "poster")
    assert poster.upload_status == "uploaded"
    assert len(plex.uploads) == 1
    fingerprint = poster.badge_fingerprint

    # Reset to the adoption/restore state: no fingerprint, no delivery
    # history, upload count zeroed so a real second upload would be visible.
    poster.badge_fingerprint = None
    poster.upload_status = "pending"
    from sqlalchemy import delete as _delete
    await session.execute(_delete(RenderDelivery).where(RenderDelivery.render_id == poster.id))
    await session.commit()
    plex.uploads.clear()
    plex.provenance = fingerprint

    def _boom(*args, **kwargs):
        raise AssertionError("compose_badges must not run when adoption matches")

    monkeypatch.setattr(pipeline_module, "compose_badges", _boom)

    renders = await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    poster = next(r for r in renders if r.art_kind == "poster")
    assert poster.upload_status == "uploaded"
    assert poster.badge_fingerprint == fingerprint
    assert plex.uploads == [], "the real upload must not run either -- this is adoption, not a copy"
    # The adoption shortcut's own `uploaded` row must carry the fingerprint
    # provenance just matched -- otherwise a later catch-up reads it as
    # unconfirmed and re-uploads adopted artwork on every run.
    delivery_fingerprint = (
        await session.execute(
            select(RenderDelivery.fingerprint).where(
                RenderDelivery.render_id == poster.id, RenderDelivery.server == "plex",
            )
        )
    ).scalar_one()
    assert delivery_fingerprint == poster.badge_fingerprint


async def test_a_failed_compose_after_a_provenance_mismatch_leaves_the_fingerprint_untouched(
    session, config_with_badges, monkeypatch,
):
    """The solo-adoption check must never write
    ``render.badge_fingerprint`` before compose actually succeeds. Provenance
    does NOT match here, so the shortcut falls through to a real compose --
    which then raises. The column must stay exactly as it was (``None``),
    or the next pass's unchanged-check (``fingerprint == render.badge_
    fingerprint``) would read a fingerprint no image ever actually matched
    and skip forever. A second pass, with compose no longer raising, must
    retry it rather than skip."""
    config_with_badges.badges.adopt_from_plex = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    class _FakePlexItem:
        def __init__(self):
            self.media = [type("M", (), {
                "parts": [], "videoResolution": "1080",
                "audioCodec": "eac3", "audioChannels": 6,
            })()]
            self.duration = 4845912
            self.seasonNumber = None
            self.episodeNumber = None

    class _MismatchProvenanceServer(FakeMediaServer):
        async def fetch_item(self, native_id):
            return _FakePlexItem()

        async def artwork_provenance(self, ref, art_kind):
            return "not-the-real-fingerprint"

    plex = _MismatchProvenanceServer(name="plex")
    plex.items[INTENT.dedupe_key] = fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    servers = Servers({"plex": plex})

    def _boom(*args, **kwargs):
        raise RuntimeError("compose_badges exploded")

    monkeypatch.setattr(pipeline_module, "compose_badges", _boom)

    # process_item's own "badge stage failed" containment rolls back on the
    # way out, expiring every object already in `results` -- read the row
    # back through a fresh query keyed on the resolved ref instead of the
    # returned renders, which a plain synchronous attribute access on an
    # expired ORM instance cannot survive here (MissingGreenlet).
    await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)
    item_id = await item_id_for(session, "plex", "p1")
    stored = (
        await session.execute(
            select(Render.badge_fingerprint).where(
                Render.item_id == item_id, Render.art_kind == "poster",
            )
        )
    ).scalar_one()
    assert stored is None, "a failed compose must never strand a fingerprint on the row"

    calls: list[int] = []

    def _spy(*args, **kwargs):
        calls.append(1)
        return b"badged"

    monkeypatch.setattr(pipeline_module, "compose_badges", _spy)
    await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)
    assert calls == [1], "the second pass must retry compose, not skip on a stale gate"


async def _refs_for_the_intent(session) -> set[str]:
    """Which servers hold a ref for the item ``INTENT`` resolves to."""
    return set((await session.execute(select(MediaItemServerRef.server))).scalars())


async def test_a_refused_art_kind_keeps_every_resolved_servers_ref(
    session, config_with_badges, monkeypatch,
):
    """The refusal handler rolls back while the non-primary refs written
    at the top of `process_item` are still uncommitted, and then re-establishes
    the item -- re-upserting the PRIMARY ref alone, silently dropping
    jellyfin's, and committing that loss. Worst for a season or an episode,
    whose single art kind makes any `SourceRefused` a first-kind refusal.

    Badges off: the badge block's own re-upsert must not be what repairs
    this, or the test would pass for the wrong reason."""
    from autoposter.render.artwork_fetch import SourceRefused

    config_with_badges.badges.enabled = False
    servers, plex, jf = _two_servers()

    async def refuse_the_poster(session, config, http, item, art_kind, providers, **kwargs):
        if art_kind != "poster":
            return await _fake_render_artifact(
                session, config, http, item, art_kind, providers, **kwargs
            )
        # The real `render_artifact` flushes its own render-row upsert before
        # it can refuse, which is what makes the handler's rollback matter.
        media_item = await pipeline_module._upsert_media_item(session, item)
        await pipeline_module._get_or_create_render(session, media_item, art_kind, "/tmp/p.jpg")
        raise SourceRefused("the poster source refused")

    monkeypatch.setattr(pipeline_module, "render_artifact", refuse_the_poster)

    await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    assert await _refs_for_the_intent(session) == {"plex", "jellyfin"}


async def test_a_metadata_failure_keeps_every_resolved_servers_ref(
    session, config_with_badges, monkeypatch,
):
    """The other rollback: the metadata-operations containment. It needs
    no refusal at all -- a TMDb hiccup, or one server's `apply_facts` failing
    -- and the only thing that re-established the item afterwards was
    `render_artifact`'s own `_upsert_media_item`, which knows nothing about
    the other servers."""
    config_with_badges.badges.enabled = False
    config_with_badges.operations.enabled = True
    servers, plex, jf = _two_servers()
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    async def explode(*args, **kwargs):
        raise RuntimeError("the facts provider hiccuped")

    monkeypatch.setattr(pipeline_module, "apply_metadata", explode)

    await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT, tmdb_facts=object(),
    )

    assert await _refs_for_the_intent(session) == {"plex", "jellyfin"}


async def test_an_upload_disabled_server_that_missed_gets_no_pending_row(
    session, config_with_badges, monkeypatch,
):
    """The toggle check sat inside `deliver`'s RESOLVED half, so a
    server the pass could not resolve still got a `pending` catch-up row with
    its upload toggle off. `upload_to_jellyfin` defaults to off, so that is
    every item Jellyfin has not scanned yet on a dual deployment's first
    pass -- each one dropping the render's roll-up from `uploaded` to
    `pending`, and each one rewritten `skipped` by the next retry pass."""
    config_with_badges.badges.upload_to_jellyfin = False
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    servers, plex, jf = _two_servers(jelly_has=False)

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
    )

    poster = next(r for r in renders if r.art_kind == "poster")
    rows = {
        (d.server, d.status)
        for d in (
            await session.execute(select(RenderDelivery).where(RenderDelivery.render_id == poster.id))
        ).scalars()
    }
    assert rows == {("plex", "uploaded"), ("jellyfin", "skipped")}
    assert poster.upload_status == "uploaded"


async def test_one_servers_metadata_write_failure_does_not_cost_the_other_its_write(
    session, monkeypatch, caplog,
):
    """`_write` was awaited in sequence with no `try`, so Plex's
    `apply_facts` failing aborted before Jellyfin was attempted at all and
    propagated into `process_item`'s containment -- whose rollback discards
    this item's `persist_facts` and its refs with it (spec
    §6.1: every server call is caught at the ref it belongs to)."""
    config = load_config(EXAMPLE)
    config.operations.write_to_plex = True
    config.operations.write_to_jellyfin = True
    config.badges.enabled = False
    plex = FakeMediaServer(name="plex")
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    plex.items[INTENT.dedupe_key] = fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    jf.items[INTENT.dedupe_key] = fake_resolved("jellyfin", "j1", file_path="/jf/m.mkv")
    servers = Servers({"plex": plex, "jellyfin": jf})

    async def explode(*args, **kwargs):
        raise httpx.ConnectError("https://plex.internal/library/metadata/1")

    monkeypatch.setattr(plex, "apply_facts", explode)

    class _FakeTMDBFacts:
        async def movie(self, tmdb_id):
            return GatheredFacts(audience_rating=6.3, sources={"audience_rating": "tmdb"})

    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    with caplog.at_level("WARNING"):
        await pipeline_module.process_item(
            session, config, None, servers, [], INTENT,
            tmdb_facts=_FakeTMDBFacts(), mdblist=NullMDBListClient(),
        )

    assert len(jf.facts_written) == 1, "plex's failure must not cost jellyfin its write"
    assert not any(
        "metadata operations failed" in record.message for record in caplog.records
    ), "one server's write failure must not reach process_item's rollback path"
    assert any("ConnectError" in record.message for record in caplog.records)
    assert not any("plex.internal" in record.message for record in caplog.records)


async def test_a_migration_backfilled_delivery_row_does_not_block_adoption(
    session, config_with_badges, monkeypatch,
):
    """The Phase-2 migration backfills one `plex` delivery row for EVERY
    pre-existing render, with no `attempted_at`. `_already_delivered`
    answered False as soon as any row existed, so on the production database
    the provenance probe could never run again -- for exactly the population
    (`badge_fingerprint IS NULL`) adoption exists for, which would then be
    recomposed and re-uploaded instead.

    `badge_fingerprint` is stubbed rather than learnt from a real first pass
    (the shape
    `test_a_single_upload_enabled_server_skips_compose_on_matching_provenance`
    uses): this test is about the probe running at all, and a fixed digest
    keeps it out of ImageMagick's way entirely."""
    from sqlalchemy import insert

    config_with_badges.badges.adopt_from_plex = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "badge_fingerprint", lambda *a, **k: "fp-from-exif")

    probed: list[str] = []

    class _ProvenanceServer(FakeMediaServer):
        async def fetch_item(self, native_id):
            return None

        async def artwork_provenance(self, ref, art_kind):
            probed.append(art_kind)
            return "fp-from-exif"

    plex = _ProvenanceServer(name="plex")
    plex.items[INTENT.dedupe_key] = fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    servers = Servers({"plex": plex})

    # The migration's own row shape: status copied off `renders.upload_status`,
    # every timestamp NULL.
    media_item = await pipeline_module._upsert_media_item(
        session, fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    )
    render = await pipeline_module._get_or_create_render(
        session, media_item, "poster", "/tmp/poster.jpg"
    )
    render.status = "rendered"
    await session.flush()
    await session.execute(
        insert(RenderDelivery).values(render_id=render.id, server="plex", status="pending")
    )
    await session.commit()

    def _boom(*args, **kwargs):
        raise AssertionError("compose must not run when adoption matches")

    monkeypatch.setattr(pipeline_module, "compose_badges", _boom)

    await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    assert probed == ["poster"], "a migration-backfilled row must not silence the probe"
    assert plex.uploads == [], "this is adoption, not a re-upload"
    row = (
        await session.execute(
            select(RenderDelivery.status, RenderDelivery.attempted_at).where(
                RenderDelivery.render_id == render.id, RenderDelivery.server == "plex",
            )
        )
    ).one()
    assert row.status == "uploaded" and row.attempted_at is not None


async def test_one_permanently_pending_server_does_not_recompose_every_pass(
    session, config_with_badges, monkeypatch,
):
    """The unchanged-work gate was inherited from the single-server code
    (`fingerprint` unchanged AND `upload_status == "uploaded"`), but
    `upload_status` is now the roll-up, whose precedence puts `pending` above
    `uploaded`. So one server that has not scanned the item -- the normal
    steady state of a newly added Jellyfin over a large library -- meant
    ImageMagick per render per pass, plus a redundant re-upload to every
    server that already had the bytes."""
    config_with_badges.badges.upload_to_jellyfin = True
    config_with_badges.badges.adopt_from_plex = False
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    composes: list[str] = []

    def _spy(base_path, art_kind, *args, **kwargs):
        composes.append(art_kind)
        return b"badged"

    monkeypatch.setattr(pipeline_module, "compose_badges", _spy)

    class _NoMediaPlex(FakeMediaServer):
        async def fetch_item(self, native_id):
            return None

    plex = _NoMediaPlex(name="plex")
    plex.items[INTENT.dedupe_key] = fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    servers = Servers({"plex": plex, "jellyfin": jf})

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
    )
    poster = next(r for r in renders if r.art_kind == "poster")
    assert composes == ["poster"] and len(plex.uploads) == 1

    async def _jellyfin_horizon():
        return (
            await session.execute(
                select(RenderDelivery.next_attempt_at).where(
                    RenderDelivery.render_id == poster.id, RenderDelivery.server == "jellyfin",
                )
            )
        ).scalar_one()

    horizon = await _jellyfin_horizon()

    await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    assert composes == ["poster"], "an unchanged fingerprint must not recompose"
    assert len(plex.uploads) == 1, "plex already has these bytes"
    rows = {
        (d.server, d.status)
        for d in (
            await session.execute(select(RenderDelivery).where(RenderDelivery.render_id == poster.id))
        ).scalars()
    }
    assert rows == {("plex", "uploaded"), ("jellyfin", "pending")}
    # The horizon is NOT pushed forward by a pass that learned nothing
    # new. RETRY_SECONDS is 6h and the measured full pass is ~3.5h, so
    # re-stamping it every pass meant the row could never mature and the
    # retry pass never saw the population it was written for.
    assert await _jellyfin_horizon() == horizon, "a miss must not defer the row again"

    # Task 8 fix: the row falls through to the same code whether it was
    # never resolved or previously ran its budget out and was marked
    # `failed` -- so this re-arm needs `reset_attempts=True` too, or a row
    # that reached `failed` from a real delivery attempt stays over budget
    # forever, exhausted again by its very next failure.
    await session.execute(
        update(RenderDelivery)
        .where(RenderDelivery.render_id == poster.id, RenderDelivery.server == "jellyfin")
        .values(status="failed", attempts=99)
    )
    await session.commit()

    await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    row = (
        await session.execute(
            select(RenderDelivery.status, RenderDelivery.attempts).where(
                RenderDelivery.render_id == poster.id, RenderDelivery.server == "jellyfin",
            )
        )
    ).one()
    assert row.status == "pending" and row.attempts == 0


async def test_a_failed_delivery_is_rearmed_once_per_pass_without_recomposing(
    session, config_with_badges, monkeypatch,
):
    """With the unchanged-work gate on the fingerprint alone, a
    `failed` delivery row would never be retried again -- the single-server
    code retried one on every full pass, because its gate also required
    `upload_status == "uploaded"`, and `retry_pending_deliveries` only
    selects `pending`.

    So `deliver`'s catch-up re-arms a `failed` row as `pending` with no
    delay: the second pass composes ZERO times and uploads nothing itself,
    and the retry pass is what re-delivers to that one server."""
    from datetime import datetime, timezone

    from autoposter import deliveries

    config_with_badges.badges.upload_to_jellyfin = True
    config_with_badges.badges.adopt_from_plex = False
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    composes: list[str] = []

    def _spy(base_path, art_kind, *args, **kwargs):
        composes.append(art_kind)
        return b"badged"

    monkeypatch.setattr(pipeline_module, "compose_badges", _spy)

    class _NoMediaPlex(FakeMediaServer):
        async def fetch_item(self, native_id):
            return None

    plex = _NoMediaPlex(name="plex")
    plex.items[INTENT.dedupe_key] = fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items[INTENT.dedupe_key] = fake_resolved("jellyfin", "j1", file_path="/jf/m.mkv")
    jf.raise_on_upload = httpx.ConnectError("https://jellyfin.internal/Items/j1/Images")
    servers = Servers({"plex": plex, "jellyfin": jf})

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
    )
    poster = next(r for r in renders if r.art_kind == "poster")
    assert composes == ["poster"] and len(plex.uploads) == 1 and jf.uploads == []
    assert {
        (d.server, d.status)
        for d in (
            await session.execute(select(RenderDelivery).where(RenderDelivery.render_id == poster.id))
        ).scalars()
    } == {("plex", "uploaded"), ("jellyfin", "failed")}
    first_pass_attempts = (
        await session.execute(
            select(RenderDelivery.attempts).where(
                RenderDelivery.render_id == poster.id, RenderDelivery.server == "jellyfin",
            )
        )
    ).scalar_one()

    # The second pass, with the upload no longer failing and the fingerprint
    # unchanged: no image work, no upload from `deliver` itself, and the
    # failed row re-armed due.
    jf.raise_on_upload = None
    await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    assert composes == ["poster"], "a re-armed failure must not cost a recompose"
    assert len(plex.uploads) == 1 and jf.uploads == []
    row = (
        await session.execute(
            select(RenderDelivery.status, RenderDelivery.next_attempt_at, RenderDelivery.attempts).where(
                RenderDelivery.render_id == poster.id, RenderDelivery.server == "jellyfin",
            )
        )
    ).one()
    # Stamped, but deliberately NOT compared against the host clock (the
    # suite-discipline rule: this machine's container clock steps backwards
    # under it). That the stamp is DUE is proved behaviourally instead, by
    # the retry pass below reporting the row as `1 due`.
    assert row.status == "pending" and row.next_attempt_at is not None
    # A re-arm is not an attempt -- nothing was actually tried against
    # jellyfin this pass (no compose, no upload) -- and it is a fresh START:
    # review I1, the counter goes back to zero rather than staying where the
    # exhausted row left it, or the first failure after a re-arm would
    # exhaust the row again (`failed` is itself a counted attempt).
    assert first_pass_attempts > 0, "the failed delivery did spend budget"
    assert row.attempts == 0

    summary = await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges, now=datetime.now(timezone.utc),
    )

    assert summary == (
        "pending deliveries: 1 due, 1 done, 0 still pending, 0 failed, 0 skipped; "
        "jellyfin: 1 due, 1 uploaded, 0 written, 0 pending, 0 failed, 0 skipped"
    )
    assert [u[0].native_id for u in jf.uploads] == ["j1"]
    assert len(plex.uploads) == 1, "the retry pass owes nothing to the server that has the bytes"
    assert {
        (d.server, d.status)
        for d in (
            await session.execute(select(RenderDelivery).where(RenderDelivery.render_id == poster.id))
        ).scalars()
    } == {("plex", "uploaded"), ("jellyfin", "uploaded")}


async def test_a_server_missing_a_metadata_method_propagates_rather_than_being_contained(
    session, monkeypatch,
):
    """The per-server containment must not swallow an `AttributeError`.
    A server missing `item_labels`/`apply_facts` is a wiring bug -- a
    programming error, not the runtime server failure that `except` is for --
    and both of `process_item`'s own containments already re-raise it."""
    config = load_config(EXAMPLE)
    config.operations.write_to_plex = True
    config.badges.enabled = False
    plex = FakeMediaServer(name="plex")
    plex.items[INTENT.dedupe_key] = fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    servers = Servers({"plex": plex})

    async def no_such_method(*args, **kwargs):
        raise AttributeError("'PlexClient' object has no attribute 'apply_facts'")

    monkeypatch.setattr(plex, "apply_facts", no_such_method)

    class _FakeTMDBFacts:
        async def movie(self, tmdb_id):
            return GatheredFacts(audience_rating=6.3, sources={"audience_rating": "tmdb"})

    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    with pytest.raises(AttributeError, match="apply_facts"):
        await pipeline_module.process_item(
            session, config, None, servers, [], INTENT,
            tmdb_facts=_FakeTMDBFacts(), mdblist=NullMDBListClient(),
        )


def _identity_plex():
    """A Plex double `compose_badged_bytes` can sample live media info from.

    The bare `FakeMediaServer` has no `fetch_item`, which the real
    `compose_badged_bytes` calls whenever it is handed a `server`/`ref` --
    and `process_item` re-raises `AttributeError` rather than containing it.
    """

    class _NoMediaPlex(FakeMediaServer):
        async def fetch_item(self, native_id):
            return None

    plex = _NoMediaPlex(name="plex")
    plex.items[INTENT.dedupe_key] = fake_resolved("plex", "p1", file_path="/plex/m.mkv")
    return plex


async def test_a_retry_composes_from_the_identity_server_and_leaves_the_fingerprint(
    session, config_with_badges, monkeypatch,
):
    """The retry pass composed with neither ``server`` nor ``ref``, so
    `plex_item` was `None` -- an empty `MediaInfo`, no native ratings, and a
    digest that differs from the full pass's. The retried server got a poster
    missing the resolution/format overlays every other server already has,
    and the media-less digest was then written to `render.badge_fingerprint`,
    so the NEXT full pass recomposed and re-uploaded to everyone.

    Both halves, through `retry_pending_deliveries`: the compose is handed
    the identity server's own resolved item even though the row being
    retried belongs to the other server, and the column does not move."""
    from datetime import datetime, timezone

    from autoposter import deliveries

    config_with_badges.badges.upload_to_jellyfin = True
    config_with_badges.badges.adopt_from_plex = False
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(
        pipeline_module, "compose_badges", lambda *a, **k: b"badged",
    )

    sampled: list[tuple[object, object]] = []
    real_compose = pipeline_module.compose_badged_bytes

    async def spy_compose(*args, **kwargs):
        sampled.append((kwargs.get("server"), kwargs.get("ref")))
        return await real_compose(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", spy_compose)

    plex = _identity_plex()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    jf.items[INTENT.dedupe_key] = fake_resolved("jellyfin", "j1", file_path="/jf/m.mkv")
    jf.raise_on_upload = httpx.ConnectError("jellyfin refused the upload")
    servers = Servers({"plex": plex, "jellyfin": jf})

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
    )
    poster_id = next(r for r in renders if r.art_kind == "poster").id
    jf.raise_on_upload = None
    # The second pass re-arms the failed jellyfin row `pending`, due now.
    await pipeline_module.process_item(session, config_with_badges, None, servers, [], INTENT)

    async def _fingerprint():
        return (
            await session.execute(
                select(Render.badge_fingerprint).where(Render.id == poster_id)
            )
        ).scalar_one()

    before = await _fingerprint()
    assert before is not None
    calls_before = len(sampled)

    summary = await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges, now=datetime.now(timezone.utc),
    )

    assert summary == (
        "pending deliveries: 1 due, 1 done, 0 still pending, 0 failed, 0 skipped; "
        "jellyfin: 1 due, 1 uploaded, 0 written, 0 pending, 0 failed, 0 skipped"
    )
    assert len(jf.uploads) == 1
    sampled_server, sampled_ref = sampled[calls_before]
    assert sampled_server is plex, "the retry must sample the identity server, not nothing"
    assert sampled_ref is not None and sampled_ref.server == "plex" and sampled_ref.native_id == "p1"
    assert await _fingerprint() == before, (
        "a compose FOR DELIVERY must not move the column the full pass owns"
    )


async def test_a_retry_waits_when_the_identity_server_cannot_be_sampled(
    session, config_with_badges, monkeypatch,
):
    """The other half of retrying via the identity server: if the identity server cannot be resolved this pass,
    the retry must WAIT rather than deliver overlay-less bytes. The row keeps
    its normal horizon and is reported still pending; nothing is uploaded."""
    from datetime import datetime, timezone

    from autoposter import deliveries

    config_with_badges.badges.upload_to_jellyfin = True
    config_with_badges.badges.adopt_from_plex = False
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)

    plex = _identity_plex()
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    servers = Servers({"plex": plex, "jellyfin": jf})

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
    )
    poster = next(r for r in renders if r.art_kind == "poster")
    # Jellyfin can see it now; Plex -- the identity -- cannot.
    jf.items[INTENT.dedupe_key] = fake_resolved("jellyfin", "j1", file_path="/jf/m.mkv")
    plex.not_found.add(INTENT.dedupe_key)
    # Ten minutes in the past, not `retry_in=0`: this machine's container
    # clock steps backwards a few seconds at a time, and a horizon stamped at
    # exactly "now" can land after the `now` the pass below reads.
    await deliveries.record(session, poster.id, "jellyfin", "pending", retry_in=-600)
    await session.commit()

    summary = await deliveries.retry_pending_deliveries(
        session, servers, config_with_badges, now=datetime.now(timezone.utc),
    )

    assert summary == (
        "pending deliveries: 1 due, 0 done, 1 still pending, 0 failed, 0 skipped; "
        "jellyfin: 1 due, 0 uploaded, 0 written, 1 pending, 0 failed, 0 skipped"
    )
    assert jf.uploads == [], "overlay-less bytes must never be delivered"
    row = (
        await session.execute(
            select(RenderDelivery.status).where(
                RenderDelivery.render_id == poster.id, RenderDelivery.server == "jellyfin",
            )
        )
    ).scalar_one()
    assert row == "pending"


class _MinimalTMDBFacts:
    """A ``tmdb_facts`` stub returning one populated field -- just enough for
    ``apply_metadata``'s write gate (``facts.is_empty()``) to admit the
    write, matching the fixture classes ``test_pipeline_facts.py`` already
    uses for the same reason. These tests are about the WRITE's outcome, not
    what TMDb said, so the value itself is arbitrary."""

    async def movie(self, tmdb_id):
        return GatheredFacts(audience_rating=6.3, sources={"audience_rating": "tmdb"})


async def test_a_failed_metadata_write_is_recorded_pending_with_its_class_name(
    session, config_with_badges, monkeypatch
):
    """spec §1: the per-server write records instead of only logging."""
    import httpx
    from sqlalchemy import select
    from autoposter.db.models import MetadataWrite
    from autoposter.render import pipeline as pipeline_module
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, resolved

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    response = httpx.Response(400, request=httpx.Request("POST", "https://jf.internal/Items/1"))

    async def boom(ref, facts, operations=None, parental_categories=None, overrides=None):
        raise httpx.HTTPStatusError("bad", request=response.request, response=response)

    monkeypatch.setattr(jf, "apply_facts", boom)
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True
    item = resolved("jellyfin", "j1")
    media_item = await pipeline_module._upsert_media_item(session, item)
    await session.commit()

    await pipeline_module.apply_metadata(
        session, config_with_badges, media_item.id, item, jf,
        _MinimalTMDBFacts(), NullMDBListClient(),
    )

    row = (await session.execute(select(MetadataWrite))).scalar_one()
    assert row.server == "jellyfin" and row.status == "pending"
    assert row.detail == "status: HTTPStatusError 400"
    assert row.next_attempt_at is not None and row.attempts == 1
    assert "jf.internal" not in (row.detail or "")


async def test_the_full_pass_re_arms_an_exhausted_metadata_row_with_its_whole_budget(
    session, config_with_badges, monkeypatch
):
    """Review I1: `_write`'s own write-failure record is `metadata_writes`'
    only door out of `failed`, and it did not reset the budget -- so from the
    first exhaustion onward the row's effective budget was 1, not 8. Spec §2
    promises a row re-armed by a full pass (or a catch-up) the whole budget.

    The artwork twin is `test_a_re_armed_row_gets_its_whole_budget_again` in
    test_deliveries.py; this is the table that fix did not reach.
    """
    import httpx
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from autoposter import deliveries
    from autoposter.db.models import MetadataWrite
    from autoposter.render import pipeline as pipeline_module
    from autoposter.servers.registry import Servers
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, resolved

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)

    async def boom(ref, facts, operations=None, parental_categories=None, overrides=None):
        raise httpx.ConnectError("jellyfin is down")

    monkeypatch.setattr(jf, "apply_facts", boom)
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True
    assert config_with_badges.scheduler.delivery_attempts == 8
    item = resolved("jellyfin", "j-rearm", file_path="/m.mkv")
    media_item = await pipeline_module._upsert_media_item(session, item)
    jf.resolve_any = item
    # The row runs its budget out: eight counted attempts and the `failed`
    # the retry pass stamps on top of the eighth.
    for _ in range(8):
        await deliveries.record_metadata(session, media_item.id, "jellyfin", "pending", retry_in=0)
    assert await deliveries.record_metadata(
        session, media_item.id, "jellyfin", "failed", detail="connect: ConnectError",
    ) == 9
    await session.commit()

    await pipeline_module.apply_metadata(
        session, config_with_badges, media_item.id, item, jf,
        _MinimalTMDBFacts(), NullMDBListClient(),
    )

    # Columns rather than the entity: the session's identity map hands an
    # already-loaded `MetadataWrite` back unrefreshed, so a re-read would
    # assert against the values the first read saw.
    columns = select(MetadataWrite.status, MetadataWrite.attempts, MetadataWrite.detail)
    row = (await session.execute(columns)).one()
    assert row.status == "pending"
    # One, not ten: the re-arm starts the row over AND this failure is its
    # first new attempt.
    assert row.attempts == 1

    # And the budget that follows is the whole one -- seven more passes.
    base = datetime.now(timezone.utc)
    for pass_number in range(1, 8):
        await deliveries.retry_pending_deliveries(
            session, Servers({"jellyfin": jf}), config_with_badges,
            now=base + timedelta(hours=12 * pass_number),
        )
        row = (await session.execute(columns)).one()
        if pass_number < 7:
            assert row.status == "pending", f"exhausted early, on pass {pass_number}"
            assert row.attempts == pass_number + 1
        else:
            assert row.status == "failed" and row.detail == "connect: ConnectError"


async def test_a_successful_write_is_recorded_written(session, config_with_badges):
    from sqlalchemy import select
    from autoposter.db.models import MetadataWrite
    from autoposter.render import pipeline as pipeline_module
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, resolved

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True
    item = resolved("jellyfin", "j2")
    media_item = await pipeline_module._upsert_media_item(session, item)
    await session.commit()

    await pipeline_module.apply_metadata(
        session, config_with_badges, media_item.id, item, jf,
        _MinimalTMDBFacts(), NullMDBListClient(),
    )

    row = (await session.execute(select(MetadataWrite))).scalar_one()
    assert row.status == "written" and row.written_at is not None and row.attempts == 0


async def test_the_write_toggle_being_off_still_says_so_on_the_row(session, config_with_badges):
    from sqlalchemy import select
    from autoposter.db.models import MetadataWrite
    from autoposter.render import pipeline as pipeline_module
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, resolved

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = False
    item = resolved("jellyfin", "j3")
    media_item = await pipeline_module._upsert_media_item(session, item)
    await session.commit()

    await pipeline_module.apply_metadata(
        session, config_with_badges, media_item.id, item, jf,
        _MinimalTMDBFacts(), NullMDBListClient(),
    )

    row = (await session.execute(select(MetadataWrite))).scalar_one()
    assert row.status == "skipped"
    assert row.detail == "config: operations.write_to_jellyfin is off"
    assert jf.facts_written == []


async def test_an_exemption_is_recorded_skipped_with_its_reason(session, config_with_badges):
    """Sibling of the toggle-off case: an exemption is a different reason on
    the same `skipped` status (spec §1), and must not fall through to a write."""
    from sqlalchemy import select
    from autoposter.db.models import MetadataWrite
    from autoposter.render import pipeline as pipeline_module
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, resolved

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS, labels=["autoposter-exempt"])
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True
    config_with_badges.operations.ignore_labels = ["autoposter-exempt"]
    item = resolved("jellyfin", "j4")
    media_item = await pipeline_module._upsert_media_item(session, item)
    await session.commit()

    await pipeline_module.apply_metadata(
        session, config_with_badges, media_item.id, item, jf,
        _MinimalTMDBFacts(), NullMDBListClient(),
    )

    row = (await session.execute(select(MetadataWrite))).scalar_one()
    assert row.status == "skipped" and row.detail
    assert jf.facts_written == []


async def test_a_server_absent_from_the_registry_gets_no_row(session, config_with_badges):
    """`target_server is None`: there is nothing to be owed to a server this
    deployment does not have (spec §1's closing sentence)."""
    from sqlalchemy import select
    from autoposter.db.models import MetadataWrite
    from autoposter.render import pipeline as pipeline_module
    from media_server_doubles import resolved

    config_with_badges.operations.enabled = True
    item = resolved("plex", "p1")
    media_item = await pipeline_module._upsert_media_item(session, item)
    await session.commit()

    await pipeline_module.apply_metadata(
        session, config_with_badges, media_item.id, item, None,
        _MinimalTMDBFacts(), NullMDBListClient(),
    )

    rows = (await session.execute(select(MetadataWrite))).scalars().all()
    assert rows == []


async def test_an_absent_row_is_left_alone_by_a_later_write(session, config_with_badges):
    """spec §1: a library `presence.apply_presence` has already stamped
    `absent` for this item/server owes it nothing, even if this pass's own
    resolution found the item anyway -- the row must not flip back out of
    `absent` (Task 3's `presence.py`, `ABSENT_DETAIL`)."""
    from sqlalchemy import select
    from autoposter import deliveries
    from autoposter.db.models import MetadataWrite
    from autoposter.render import pipeline as pipeline_module
    from autoposter.servers.presence import ABSENT_DETAIL
    from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, resolved

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True
    item = resolved("jellyfin", "j5")
    media_item = await pipeline_module._upsert_media_item(session, item)
    await deliveries.record_metadata(session, media_item.id, "jellyfin", "absent", detail=ABSENT_DETAIL)
    await session.commit()

    await pipeline_module.apply_metadata(
        session, config_with_badges, media_item.id, item, jf,
        _MinimalTMDBFacts(), NullMDBListClient(),
    )

    row = (await session.execute(select(MetadataWrite))).scalar_one()
    assert row.status == "absent" and row.detail == ABSENT_DETAIL
    assert jf.facts_written == [], "an absent row must never be written to"


async def test_a_dual_registry_pass_writes_metadata_written_and_pending_per_server(
    session, config_with_badges, monkeypatch,
):
    """Task 4 review I1: the branch matrix above is exercised directly against
    `apply_metadata`; this proves the same outcomes through the real entry
    point, `process_item`, on a dual registry -- Plex accepts the write,
    Jellyfin's `apply_facts` raises -- and that the artwork path (an
    unrelated seam) still completes for both servers regardless."""
    config_with_badges.badges.upload_to_jellyfin = True
    config_with_badges.operations.write_to_jellyfin = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    servers, plex, jf = _two_servers()

    async def boom(ref, facts, operations=None, parental_categories=None, overrides=None):
        raise RuntimeError("jellyfin refused it")

    monkeypatch.setattr(jf, "apply_facts", boom)

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
        tmdb_facts=_MinimalTMDBFacts(), mdblist=NullMDBListClient(),
    )

    poster = next(r for r in renders if r.art_kind == "poster")
    assert poster.upload_status == "uploaded", "the artwork path must still complete for both servers"
    assert plex.uploads and jf.uploads
    from autoposter.db.models import MetadataWrite

    rows = {row.server: row for row in (await session.execute(select(MetadataWrite))).scalars()}
    assert rows["plex"].status == "written"
    assert rows["jellyfin"].status == "pending"
    assert rows["jellyfin"].attempts == 1
    assert rows["jellyfin"].detail == "error: RuntimeError"


async def test_the_pipeline_re_arming_a_row_takes_it_out_of_its_catch_up_run(
    session, config_with_badges, monkeypatch,
):
    """Review I4: `run_id`/`previous_status` were never named by the outcome
    writers, so a row armed by catch-up run 5 kept `run_id=5` for the rest of
    its life -- including after the ordinary pipeline re-armed it weeks later.
    Phase C's run-scoped progress and its cancel would then act on rows that
    run no longer owns. A row leaves a run when the pipeline re-arms it;
    a terminal outcome INSIDE a run keeps its scope.

    The run is FINISHED here: a row whose run is still in flight is already
    pending under it and the pipeline leaves it alone entirely, `run_id` and
    all (spec §3, and `tests/test_catchup.py`'s open-run pair)."""
    from sqlalchemy import update
    from autoposter.db.models import MetadataWrite
    from autoposter.scheduler.run_history import close_run, open_run

    config_with_badges.badges.upload_to_jellyfin = True
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_jellyfin = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    servers, plex, jf = _two_servers()

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
        tmdb_facts=_MinimalTMDBFacts(), mdblist=NullMDBListClient(),
    )
    poster = next(r for r in renders if r.art_kind == "poster")

    # What a catch-up leaves behind: its own rows, exhausted and scoped to it.
    run_id = await open_run(session, kind="catch_up", name="catch_up:jellyfin")
    await session.execute(
        update(RenderDelivery)
        .where(RenderDelivery.server == "jellyfin")
        .values(status="failed", run_id=run_id, previous_status="uploaded")
    )
    await session.execute(
        update(MetadataWrite)
        .where(MetadataWrite.server == "jellyfin")
        .values(status="failed", run_id=run_id, previous_status="written")
    )
    await close_run(session, run_id, status="ok", detail="drained")
    await session.commit()

    async def nothing_new(session, config, render, media_item, **_kwargs):
        # An unchanged fingerprint, which is `deliver`'s own re-arm door.
        return None

    async def boom(ref, facts, operations=None, parental_categories=None, overrides=None):
        raise RuntimeError("jellyfin refused it")

    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", nothing_new)
    monkeypatch.setattr(jf, "apply_facts", boom)

    await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
        tmdb_facts=_MinimalTMDBFacts(), mdblist=NullMDBListClient(),
    )

    delivery = (await session.execute(
        select(RenderDelivery.status, RenderDelivery.run_id, RenderDelivery.previous_status)
        .where(RenderDelivery.server == "jellyfin", RenderDelivery.render_id == poster.id)
    )).one()
    assert delivery.status == "pending"
    assert delivery.run_id is None and delivery.previous_status is None
    write = (await session.execute(
        select(MetadataWrite.status, MetadataWrite.run_id, MetadataWrite.previous_status)
        .where(MetadataWrite.server == "jellyfin")
    )).one()
    assert write.status == "pending"
    assert write.run_id is None and write.previous_status is None


async def test_a_process_item_pass_leaves_an_absent_jellyfin_row_untouched(
    session, monkeypatch,
):
    """Task 4 review I1's second case: the same guard as the direct-call
    absent test above, now proven through `process_item` -- presence has
    already stamped Jellyfin's row `absent` for this item, and a pass that
    resolves it there anyway (this double still has it in `jf.items`) must
    never call `apply_facts` on Jellyfin or move the row off `absent`."""
    from autoposter import deliveries
    from autoposter.db.models import MetadataWrite
    from autoposter.servers.presence import ABSENT_DETAIL

    config = load_config(EXAMPLE)
    config.operations.write_to_jellyfin = True
    # This test is about the metadata write loop, not badges -- disabled so
    # the badge stage never runs at all (the established pattern above, in
    # test_metadata_fan_out_reaches_every_resolved_server_with_its_own_ref).
    config.badges.enabled = False
    servers, plex, jf = _two_servers()
    plex_item = plex.items[INTENT.dedupe_key]

    media_item = await pipeline_module._upsert_media_item(session, plex_item)
    await deliveries.record_metadata(session, media_item.id, "jellyfin", "absent", detail=ABSENT_DETAIL)
    await session.commit()

    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    await pipeline_module.process_item(
        session, config, None, servers, [], INTENT,
        tmdb_facts=_MinimalTMDBFacts(), mdblist=NullMDBListClient(),
    )

    assert jf.facts_written == [], "an absent row must never be written to, even through the real pass"
    row = (
        await session.execute(
            select(MetadataWrite).where(
                MetadataWrite.item_id == media_item.id, MetadataWrite.server == "jellyfin",
            )
        )
    ).scalar_one()
    assert row.status == "absent" and row.detail == ABSENT_DETAIL


async def test_an_absent_server_is_never_asked_to_resolve(session, monkeypatch):
    """Phase A review ruling 2: the guards in `apply_metadata` and `deliver`
    keep an `absent` ROW right, but the pass still spent a resolve request on
    that server for every item, every pass -- on a library the server does not
    carry at all. The absent set is read once, before the fan-out, off the
    refs the intent already carries (a full pass builds every intent from a
    `media_items` row), and the servers in it are asked nothing."""
    from autoposter import deliveries
    from autoposter.db.models import MetadataWrite
    from autoposter.servers.presence import ABSENT_DETAIL

    config = load_config(EXAMPLE)
    config.operations.write_to_jellyfin = True
    config.badges.enabled = False
    servers, plex, jf = _two_servers()
    plex_item = plex.items[INTENT.dedupe_key]

    media_item = await pipeline_module._upsert_media_item(session, plex_item)
    await deliveries.record_metadata(
        session, media_item.id, "jellyfin", "absent", detail=ABSENT_DETAIL
    )
    await session.commit()

    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    await pipeline_module.process_item(
        session, config, None, servers, [], RenderIntent(
            kind="movie", title="Title", tmdb_id=1, year=2020,
            refs={"plex": plex_item.native_id},
        ),
        tmdb_facts=_MinimalTMDBFacts(), mdblist=NullMDBListClient(),
    )

    assert jf.resolve_calls == 0, "a server that does not carry the library is asked nothing"
    assert plex.resolve_calls == 1, "every other server is resolved exactly as before"
    assert jf.facts_written == []
    row = (
        await session.execute(
            select(MetadataWrite).where(
                MetadataWrite.item_id == media_item.id, MetadataWrite.server == "jellyfin",
            )
        )
    ).scalar_one()
    assert row.status == "absent" and row.detail == ABSENT_DETAIL


async def test_a_second_pass_leaves_an_absent_jellyfin_artwork_row_untouched(
    session, config_with_badges, monkeypatch,
):
    """The artwork half of the same rule, with the badge gate ON (review T1).

    The metadata test above runs with `badges.enabled = False`, so `deliver`
    returns at its first guard and only the metadata guard is proven. Here
    badges and `upload_to_jellyfin` are both on and Jellyfin does not have
    the item, which is the exact shape presence stamps `absent` for: the
    pass composes bytes, `deliver` reaches its miss branch, and the row must
    still be `absent` afterwards -- with no retry horizon -- rather than
    flipped back to `pending` for the next pass's presence step to re-stamp.
    """
    from sqlalchemy import update

    from autoposter.servers.presence import ABSENT_DETAIL

    config_with_badges.badges.upload_to_jellyfin = True
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)
    servers, plex, jf = _two_servers(jelly_has=False)

    renders = await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
    )
    poster = next(r for r in renders if r.art_kind == "poster")

    # What a full pass's opening writes, set-shaped, before the jobs run
    # (servers/presence.py): the row this pass left `pending` is reclassified.
    await session.execute(
        update(RenderDelivery)
        .where(RenderDelivery.render_id == poster.id, RenderDelivery.server == "jellyfin")
        .values(status="absent", detail=ABSENT_DETAIL, next_attempt_at=None, attempts=0)
    )
    await session.commit()

    await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
    )

    rows = {
        d.server: d
        for d in (
            await session.execute(
                select(RenderDelivery).where(RenderDelivery.render_id == poster.id)
            )
        ).scalars()
    }
    assert rows["jellyfin"].status == "absent", "the pass flipped an absent row back"
    assert rows["jellyfin"].detail == ABSENT_DETAIL
    assert rows["jellyfin"].next_attempt_at is None, "an absent row must never gain a retry horizon"
    assert jf.uploads == [], "nothing is ever delivered to a server that does not carry the library"
    assert rows["plex"].status == "uploaded", "the other server is unaffected"


async def test_an_absent_only_deliver_does_no_rollup_and_no_commit(
    session, config_with_badges, monkeypatch,
):
    """Review test gap 7: `test_an_absent_server_is_never_asked_to_resolve`
    pins `resolve_calls == 0`; the other half of spec §1's "no row, no
    roll-up, no commit" rested on `recorded` staying False and was untested.
    An UPDATE and a COMMIT per render per pass, for a server that has nothing
    to do with the item, is exactly what that flag exists to avoid."""
    from autoposter import deliveries
    from autoposter.servers.presence import ABSENT_DETAIL

    config_with_badges.badges.upload_to_jellyfin = True
    jf_item = fake_resolved("jellyfin", "j1", file_path="/jf/m.mkv")
    media_item = await pipeline_module._upsert_media_item(session, jf_item)
    render = await pipeline_module._get_or_create_render(session, media_item, "poster", "/a/p.jpg")
    render.status = "rendered"
    await deliveries.record(session, render.id, "jellyfin", "absent", detail=ABSENT_DETAIL)
    # A value the roll-up itself would never write, so "unchanged" is proof
    # that `rollup` did not run rather than proof that it agreed.
    await session.execute(
        update(Render).where(Render.id == render.id).values(upload_status="rendered")
    )
    await session.commit()

    commits = 0
    real_commit = session.commit

    async def counting_commit():
        nonlocal commits
        commits += 1
        return await real_commit()

    monkeypatch.setattr(session, "commit", counting_commit)
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)

    # `data=None`: the unchanged-fingerprint pass, where nothing was composed
    # and there is therefore nothing owed to the database either.
    await pipeline_module.deliver(
        session, config_with_badges, render, media_item,
        Servers({"jellyfin": jf}), {"jellyfin": jf_item.ref}, None,
        absent_servers={"jellyfin"},
    )
    assert commits == 0, "an absent-only render must not commit"

    # And with bytes in hand the commit IS owed -- `compose_badged_bytes`
    # flushed the fingerprint before handing them over -- but the roll-up
    # still is not, because no delivery row was written either way.
    await pipeline_module.deliver(
        session, config_with_badges, render, media_item,
        Servers({"jellyfin": jf}), {"jellyfin": jf_item.ref}, b"badged",
        absent_servers={"jellyfin"},
    )
    assert commits == 1, "the compose's own commit, and no other"

    assert jf.uploads == []
    monkeypatch.setattr(session, "commit", real_commit)
    status = (await session.execute(
        select(Render.upload_status).where(Render.id == render.id)
    )).scalar_one()
    assert status == "rendered", "an absent-only render must not be rolled up"
    row = (await session.execute(
        select(RenderDelivery.status, RenderDelivery.detail)
    )).one()
    assert row.status == "absent" and row.detail == ABSENT_DETAIL


async def test_process_item_reports_a_failed_metadata_write_as_a_warning(
    session, config_with_badges, monkeypatch,
):
    """Spec §4, through the real entry point: a metadata write that fails
    hands the caller the sentence its job finishes `done_with_warnings` on."""
    config_with_badges.badges.enabled = False
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_plex = True
    config_with_badges.operations.write_to_jellyfin = True
    servers, plex, jf = _two_servers()
    response = httpx.Response(400, request=httpx.Request("POST", "https://jf.internal/Items/j1"))

    async def boom(ref, facts, operations=None, parental_categories=None, overrides=None):
        raise httpx.HTTPStatusError("bad", request=response.request, response=response)

    monkeypatch.setattr(jf, "apply_facts", boom)
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    warnings: list[str] = []
    await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
        tmdb_facts=_MinimalTMDBFacts(), mdblist=NullMDBListClient(), warnings=warnings,
    )

    assert warnings == ["jellyfin: metadata pending (status: HTTPStatusError 400)"]
    assert len(plex.facts_written) == 1, "the server that settled is not in the sentence"


async def test_process_item_reports_nothing_when_every_server_settled(
    session, config_with_badges, monkeypatch,
):
    """The other half: both tables settle on both servers, so the job that
    ran this item finishes plain `done`."""
    from autoposter.db.models import MetadataWrite

    config_with_badges.badges.upload_to_jellyfin = True
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_plex = True
    config_with_badges.operations.write_to_jellyfin = True
    servers, plex, jf = _two_servers()
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)
    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", _fake_compose)

    warnings: list[str] = []
    await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
        tmdb_facts=_MinimalTMDBFacts(), mdblist=NullMDBListClient(), warnings=warnings,
    )

    assert warnings == []
    # The vacuity guard: an empty list means "every row settled" only once
    # there are rows. A pass that wrote none would assert the same thing.
    assert {
        (d.server, d.status)
        for d in (await session.execute(select(RenderDelivery))).scalars()
    } == {("plex", "uploaded"), ("jellyfin", "uploaded")}
    assert dict((await session.execute(
        select(MetadataWrite.server, MetadataWrite.status)
    )).all()) == {"plex": "written", "jellyfin": "written"}


async def test_a_badge_stage_failure_stays_contained_with_warnings_asked_for(
    session, config_with_badges, monkeypatch, caplog,
):
    """The badge stage's own `except` rolls the session back, which EXPIRES
    every object it tracks -- `media_item` included. Reading an ORM attribute
    after it, outside an awaited call, is a lazy refresh that raises
    `MissingGreenlet`, and that escapes `process_item` into the worker's
    generic failure branch: a contained badge failure became a charged
    attempt and eventually a parked job, losing the containment this block
    exists for. The artwork is already on disk either way."""
    config_with_badges.badges.enabled = True
    config_with_badges.badges.upload_to_plex = True
    servers, plex, jf = _two_servers()
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    async def dirty_then_boom(session_, config, render, media_item, **_kwargs):
        # Dirtying first is what makes the rollback expire objects rather than
        # being a no-op on a clean session.
        session_.add(MediaItem(identity_key="dirt", library="Movies", kind="movie", title="D"))
        await session_.flush()
        raise RuntimeError("badge stage blew up")

    monkeypatch.setattr(pipeline_module, "compose_badged_bytes", dirty_then_boom)

    warnings: list[str] = []
    with caplog.at_level(logging.WARNING, logger="autoposter.render.pipeline"):
        await pipeline_module.process_item(
            session, config_with_badges, None, servers, [], INTENT, warnings=warnings,
        )

    assert any("badge stage failed" in r.message for r in caplog.records)
    # Whatever the rows say is fine; what must NOT happen is the call raising.
    assert isinstance(warnings, list)


async def test_process_item_reports_a_server_that_never_resolved(
    session, config_with_badges, monkeypatch,
):
    """Badges off, so a missed server leaves no `pending` delivery row and no
    metadata row at all -- the deployment shape where the old code finished
    plain `done` and said nothing about a server that has never seen the
    item."""
    from sqlalchemy import select as _select
    from autoposter.db.models import MetadataWrite

    config_with_badges.badges.enabled = False
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_plex = True
    config_with_badges.operations.write_to_jellyfin = True
    servers, plex, jf = _two_servers(jelly_has=False)
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    warnings: list[str] = []
    await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
        tmdb_facts=_MinimalTMDBFacts(), mdblist=NullMDBListClient(), warnings=warnings,
    )

    assert warnings == ["jellyfin: not found"]
    # The vacuity guard for the premise: no row names jellyfin at all, which
    # is why the miss had to be carried separately.
    assert (await session.execute(_select(RenderDelivery))).all() == []
    assert dict((await session.execute(
        _select(MetadataWrite.server, MetadataWrite.status)
    )).all()) == {"plex": "written"}


async def test_process_item_never_reports_a_miss_on_an_absent_library(
    session, config_with_badges, monkeypatch,
):
    """Spec §1: a server whose row says it does not carry this item's library
    "is never resolved there, and is never retried", so it is owed nothing
    and must not be named. A webhook intent carries no refs, so the resolve
    loop's own absent check has nothing to key off yet and asks the server
    anyway -- the sentence has to make the subtraction itself."""
    from autoposter import deliveries as deliveries_module
    from autoposter.servers.presence import ABSENT_DETAIL

    config_with_badges.badges.enabled = False
    config_with_badges.operations.enabled = True
    config_with_badges.operations.write_to_plex = True
    config_with_badges.operations.write_to_jellyfin = True
    servers, plex, jf = _two_servers(jelly_has=False)
    monkeypatch.setattr(pipeline_module, "render_artifact", _fake_render_artifact)

    seeded = await pipeline_module._upsert_media_item(
        session, fake_resolved("plex", "p1", file_path="/plex/m.mkv"),
    )
    await deliveries_module.record_metadata(
        session, seeded.id, "jellyfin", "absent", detail=ABSENT_DETAIL,
    )
    await session.commit()

    warnings: list[str] = []
    await pipeline_module.process_item(
        session, config_with_badges, None, servers, [], INTENT,
        tmdb_facts=_MinimalTMDBFacts(), mdblist=NullMDBListClient(), warnings=warnings,
    )

    assert warnings == []
