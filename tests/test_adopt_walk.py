"""The adoption walk: build media_items/renders from what is already on disk.

Adoption never resolves a provider, never writes to Plex, and never writes an
image -- it only reads Plex, reads the asset tree, and writes rows. These
tests use fake plexapi-shaped objects (style of ``tests/test_collection_resolve.py``)
and real small files under ``tmp_path`` so hashes are real hashes.
"""
import hashlib
import threading
from pathlib import Path

from sqlalchemy import select

from autoposter.adopt import walk
from autoposter.adopt.walk import adopt_library
from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem, Render
from autoposter.render import naming

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def _config(tmp_path):
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    return config


# --- fake plexapi objects ----------------------------------------------------


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeMovie:
    type = "movie"

    def __init__(self, rating_key, title, file_path, year=2024, guids=()):
        self.ratingKey = rating_key
        self.title = title
        self.year = year
        self.locations = [file_path]
        self.guids = [FakeGuid(g) for g in guids]


class FakeEpisode:
    type = "episode"

    def __init__(self, rating_key, title, season_number, episode_number, year=None, guids=()):
        self.ratingKey = rating_key
        self.title = title
        self.parentIndex = season_number
        self.index = episode_number
        self.year = year
        self.guids = [FakeGuid(g) for g in guids]


class FakeSeason:
    type = "season"

    def __init__(self, rating_key, title, season_number, episodes, year=None, guids=()):
        self.ratingKey = rating_key
        self.title = title
        self.index = season_number
        self.year = year
        self.guids = [FakeGuid(g) for g in guids]
        self._episodes = episodes

    def episodes(self):
        return self._episodes


class FakeShow:
    type = "show"

    def __init__(self, rating_key, title, directory, seasons, year=2020, guids=()):
        self.ratingKey = rating_key
        self.title = title
        self.year = year
        self.locations = [directory]
        self.guids = [FakeGuid(g) for g in guids]
        self._seasons = seasons

    def seasons(self):
        return self._seasons


class FakeSection:
    def __init__(self, title, locations, items):
        self.title = title
        self.locations = locations
        self._items = items

    def all(self):
        return self._items


def _movie_section(tmp_path, rating_key="1", title="Dune: Part Two", guids=()):
    library_root = tmp_path / "Movies"
    file_path = str(library_root / "Dune (2024)" / "movie.mkv")
    section = FakeSection("Movies", [str(library_root)], [
        FakeMovie(rating_key, title, file_path, guids=guids),
    ])
    return section


def _write(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


# --- a movie whose poster exists on disk -------------------------------------


async def test_movie_with_poster_on_disk_gets_a_media_item_and_an_adopted_render(session, tmp_path):
    config = _config(tmp_path)
    section = _movie_section(tmp_path, guids=["tmdb://693134", "imdb://tt15239678"])
    poster = naming.asset_path(config, "Movies", "Dune (2024)", "poster")
    _write(poster, b"poster-bytes")

    report = await adopt_library(session, config, section, dry_run=False)

    assert report.items == 1
    assert report.renders == 1  # background is missing -- see next test
    assert report.missing_assets == 1

    media_item = (
        await session.execute(select(MediaItem).where(MediaItem.rating_key == "1"))
    ).scalar_one()
    assert media_item.title == "Dune: Part Two"
    assert media_item.tmdb_id == 693134
    assert media_item.imdb_id == "tt15239678"

    render = (
        await session.execute(
            select(Render).where(Render.item_id == media_item.id, Render.art_kind == "poster")
        )
    ).scalar_one()
    assert render.adopted is True
    assert render.status == "rendered"
    assert render.base_sha256 == hashlib.sha256(b"poster-bytes").hexdigest()
    assert render.asset_path == str(poster)


# --- a movie whose asset file is absent --------------------------------------


async def test_missing_asset_creates_no_render_row_and_is_counted(session, tmp_path):
    config = _config(tmp_path)
    section = _movie_section(tmp_path)
    # No files written at all: neither poster nor background exists.

    report = await adopt_library(session, config, section, dry_run=False)

    assert report.missing_assets == 2
    assert report.renders == 0
    rows = (await session.execute(select(Render))).scalars().all()
    assert rows == []


# --- an item that already has a non-adopted render ---------------------------


async def test_existing_non_adopted_render_is_left_untouched_and_skipped(session, tmp_path):
    config = _config(tmp_path)
    section = _movie_section(tmp_path)
    poster = naming.asset_path(config, "Movies", "Dune (2024)", "poster")
    _write(poster, b"poster-bytes")

    media_item = MediaItem(rating_key="1", library="Movies", kind="movie", title="Dune: Part Two")
    session.add(media_item)
    await session.flush()
    real_render = Render(
        item_id=media_item.id, art_kind="poster", asset_path=str(poster),
        status="rendered", fingerprint="a-real-fingerprint", adopted=False,
    )
    session.add(real_render)
    await session.commit()

    report = await adopt_library(session, config, section, dry_run=False)

    assert report.skipped == 1
    reloaded = (
        await session.execute(select(Render).where(Render.id == real_render.id))
    ).scalar_one()
    assert reloaded.fingerprint == "a-real-fingerprint"
    assert reloaded.adopted is False
    assert reloaded.base_sha256 is None


# --- running twice is idempotent ---------------------------------------------


async def test_running_twice_creates_nothing_new(session, tmp_path):
    config = _config(tmp_path)
    section = _movie_section(tmp_path)
    poster = naming.asset_path(config, "Movies", "Dune (2024)", "poster")
    _write(poster, b"poster-bytes")

    await adopt_library(session, config, section, dry_run=False)
    first_pass = (await session.execute(select(Render))).scalars().all()
    first_media = (await session.execute(select(MediaItem))).scalars().all()

    await adopt_library(session, config, section, dry_run=False)
    second_pass = (await session.execute(select(Render))).scalars().all()
    second_media = (await session.execute(select(MediaItem))).scalars().all()

    assert len(first_pass) == len(second_pass) == 1
    assert {r.id for r in first_pass} == {r.id for r in second_pass}
    assert len(first_media) == len(second_media) == 1
    assert {m.id for m in first_media} == {m.id for m in second_media}


# --- dry_run writes nothing but reports the same counts ----------------------


async def test_dry_run_writes_nothing_but_reports_the_same_counts(session, tmp_path):
    config = _config(tmp_path)
    section = _movie_section(tmp_path)
    poster = naming.asset_path(config, "Movies", "Dune (2024)", "poster")
    _write(poster, b"poster-bytes")

    dry_report = await adopt_library(session, config, section, dry_run=True)
    assert (await session.execute(select(MediaItem))).scalars().all() == []
    assert (await session.execute(select(Render))).scalars().all() == []

    real_report = await adopt_library(session, config, section, dry_run=False)

    assert dry_report == real_report


# --- by_kind counts -----------------------------------------------------------


async def test_by_kind_counts_are_right(session, tmp_path):
    config = _config(tmp_path)
    section = _movie_section(tmp_path)
    poster = naming.asset_path(config, "Movies", "Dune (2024)", "poster")
    background = naming.asset_path(config, "Movies", "Dune (2024)", "background")
    _write(poster, b"poster-bytes")
    _write(background, b"background-bytes")

    report = await adopt_library(session, config, section, dry_run=True)

    assert report.by_kind == {"poster": 1, "background": 1}
    assert report.missing_assets == 0


# --- hashing happens off the event loop --------------------------------------


async def test_hashing_happens_off_the_event_loop(session, tmp_path, monkeypatch):
    config = _config(tmp_path)
    section = _movie_section(tmp_path)
    poster = naming.asset_path(config, "Movies", "Dune (2024)", "poster")
    _write(poster, b"poster-bytes")

    test_thread = threading.current_thread()
    hashing_threads = []
    real_hash_file = walk._hash_file

    def recording_hash_file(path):
        hashing_threads.append(threading.current_thread())
        return real_hash_file(path)

    monkeypatch.setattr(walk, "_hash_file", recording_hash_file)

    await adopt_library(session, config, section, dry_run=True)

    assert hashing_threads  # at least the poster was hashed
    assert all(t is not test_thread for t in hashing_threads)


# --- shows: seasons and episodes get walked and linked correctly -------------


async def test_show_walk_adopts_seasons_and_episodes_with_correct_parents(session, tmp_path):
    config = _config(tmp_path)
    library_root = tmp_path / "TV Shows"
    show_dir = str(library_root / "Breaking Bad (2008)")
    episode = FakeEpisode("30", "Pilot", season_number=1, episode_number=1)
    season = FakeSeason("20", "Season 1", season_number=1, episodes=[episode])
    show = FakeShow("10", "Breaking Bad", show_dir, seasons=[season])
    section = FakeSection("TV Shows", [str(library_root)], [show])

    show_poster = naming.asset_path(config, "TV Shows", "Breaking Bad (2008)", "poster")
    show_background = naming.asset_path(config, "TV Shows", "Breaking Bad (2008)", "background")
    season_poster = naming.asset_path(
        config, "TV Shows", "Breaking Bad (2008)", "season_poster", season_number=1
    )
    title_card = naming.asset_path(
        config, "TV Shows", "Breaking Bad (2008)", "title_card", season_number=1, episode_number=1
    )
    _write(show_poster, b"show-poster")
    _write(show_background, b"show-background")
    _write(season_poster, b"season-poster")
    _write(title_card, b"title-card")

    report = await adopt_library(session, config, section, dry_run=False)

    assert report.items == 3  # show, season, episode
    assert report.renders == 4  # poster+background, season_poster, title_card

    show_row = (
        await session.execute(select(MediaItem).where(MediaItem.rating_key == "10"))
    ).scalar_one()
    season_row = (
        await session.execute(select(MediaItem).where(MediaItem.rating_key == "20"))
    ).scalar_one()
    episode_row = (
        await session.execute(select(MediaItem).where(MediaItem.rating_key == "30"))
    ).scalar_one()

    assert season_row.parent_id == show_row.id
    assert episode_row.parent_id == season_row.id
    assert episode_row.season_number == 1
    assert episode_row.episode_number == 1

    season_render = (
        await session.execute(
            select(Render).where(Render.item_id == season_row.id, Render.art_kind == "season_poster")
        )
    ).scalar_one()
    assert season_render.adopted is True
    assert season_render.base_sha256 == hashlib.sha256(b"season-poster").hexdigest()
