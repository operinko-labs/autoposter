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
from autoposter.db.models import MediaItem, MediaItemServerRef, Render
from autoposter.db.refs import item_id_for
from autoposter.render import naming
from autoposter.render.pipeline import _upsert_media_item
from autoposter.servers.identity import identity_key_for
from media_server_doubles import resolved

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


async def _media_item_by_native_id(session, native_id: str) -> MediaItem:
    """The ``media_items`` row for a Plex native id -- ``media_items`` no
    longer has a ``rating_key`` column to query directly (Task 6); the id
    lives in ``media_item_server_refs`` instead."""
    item_id = await item_id_for(session, "plex", native_id)
    return (
        await session.execute(select(MediaItem).where(MediaItem.id == item_id))
    ).scalar_one()


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

    media_item = await _media_item_by_native_id(session, "1")
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

    # Its own row, sharing the SAME identity the walk's own discovery will
    # compute (no guids, so a movie's identity falls back to its root folder
    # and file's basename): upsert_server_ref repoints native_id "1" to
    # whatever row _upsert_media_item's own call resolves to, so a row under
    # a DIFFERENT identity here would have its ref stolen out from under it
    # and the render below would attach to a row the walk never touches.
    media_item = await _upsert_media_item(
        session, resolved("plex", "1", title="Dune: Part Two", tmdb_id=None,
                           file_path=str(tmp_path / "Movies" / "Dune (2024)" / "movie.mkv"),
                           root_folder="Dune (2024)"),
    )
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

    show_row = await _media_item_by_native_id(session, "10")
    season_row = await _media_item_by_native_id(session, "20")
    episode_row = await _media_item_by_native_id(session, "30")

    # Two hops: a season's parent is the show, and an episode's parent is
    # its own SEASON, not the show directly (config/impact.py's model;
    # servers/identity.py's ``parent_identity_key_for``).
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


async def test_show_walk_adopts_seasons_and_episodes_with_correct_parents_when_the_show_has_a_guid(
    session, tmp_path
):
    """The guid-bearing counterpart of the test above: the show carries a
    real tvdb guid, so the season and episode's PARENT ids come from the
    show's own guids (``_resolved_season``/``_resolved_episode`` mirror the
    Plex resolver's own ``parent_guids``), and both hops resolve by provider
    id rather than falling back to root_folder."""
    config = _config(tmp_path)
    library_root = tmp_path / "TV Shows"
    show_dir = str(library_root / "Breaking Bad (2008)")
    episode = FakeEpisode("30", "Pilot", season_number=1, episode_number=1)
    season = FakeSeason("20", "Season 1", season_number=1, episodes=[episode])
    show = FakeShow("10", "Breaking Bad", show_dir, seasons=[season], guids=["tvdb://81189"])
    section = FakeSection("TV Shows", [str(library_root)], [show])

    report = await adopt_library(session, config, section, dry_run=False)

    assert report.items == 3

    show_row = await _media_item_by_native_id(session, "10")
    season_row = await _media_item_by_native_id(session, "20")
    episode_row = await _media_item_by_native_id(session, "30")

    assert show_row.identity_key == "show:tvdb:81189::"
    assert season_row.identity_key == "season:tvdb:81189:s1:"
    assert season_row.parent_id == show_row.id
    assert episode_row.parent_id == season_row.id


async def test_an_adopted_episode_keys_on_its_shows_ids_not_its_own(session, tmp_path):
    """The episode carries a tvdb guid OF ITS OWN, different from the show's.

    The render path never sees that id: ``PlexClient`` rebinds its match
    container to the SHOW for an episode intent, so the ``ResolvedItem`` it
    builds carries the SERIES' ids. Adoption reading the episode's own guids
    instead would store a row under a key no later pass can ever compute --
    a second row for one episode on the next full pass. So the adopted row's
    key must equal the key the resolver-shaped item computes.
    """
    config = _config(tmp_path)
    library_root = tmp_path / "TV Shows"
    show_dir = str(library_root / "Breaking Bad (2008)")
    episode = FakeEpisode("30", "Pilot", season_number=1, episode_number=1,
                          guids=["tvdb://5479030"])
    season = FakeSeason("20", "Season 1", season_number=1, episodes=[episode])
    show = FakeShow("10", "Breaking Bad", show_dir, seasons=[season], guids=["tvdb://81189"])
    section = FakeSection("TV Shows", [str(library_root)], [show])

    await adopt_library(session, config, section, dry_run=False)

    episode_row = await _media_item_by_native_id(session, "30")
    from_the_resolver = resolved(
        "plex", "30", kind="episode", library="TV Shows", title="Pilot", year=None,
        tmdb_id=None, tvdb_id=81189, season_number=1, episode_number=1,
        file_path=None, root_folder="Breaking Bad (2008)",
    )
    assert episode_row.identity_key == identity_key_for(from_the_resolver)
    assert episode_row.identity_key == "episode:tvdb:81189:s1e1:"
    assert episode_row.tvdb_id == 81189, "the episode's own 5479030 must not be stored"


def test_the_adoption_walk_carries_the_shows_title_onto_each_season():
    """The third producer. ``_resolve_section`` already holds the show (``top``)
    and passes it into ``_resolved_season``, so this is a field to thread, not
    a request to buy -- and it must agree with what ``PlexClient.resolve``
    produces or an adopted fingerprint and the pipeline's would disagree the
    first time the gate is turned on."""
    episode = FakeEpisode("30", "Pilot", season_number=1, episode_number=1)
    season = FakeSeason("20", "Season 1", season_number=1, episodes=[episode])
    show = FakeShow("10", "Breaking Bad", "/tv/Breaking Bad (2008)", seasons=[season])
    section = FakeSection("TV Shows", ["/tv"], [show])

    resolved = walk._resolve_section(section)
    by_kind = {r.kind: r for r in resolved}

    assert by_kind["season"].title == "Season 1"
    assert by_kind["season"].show_title == "Breaking Bad"
    assert by_kind["show"].show_title is None
    assert by_kind["episode"].show_title is None


# --- every plexapi attribute is read inside the worker thread ----------------


class LazyPlexObject:
    """A plexapi-shaped double where *reading any attribute* records the thread.

    ``PlexPartialObject.__getattribute__`` issues a blocking ``_reload()`` HTTP
    GET whenever the value it finds is ``None`` or ``[]`` -- which for seasons
    and episodes (``year is None``) and unmatched items (empty ``guids``) is
    the common case, not the exception. Any such read landing on the event loop
    is up to ~16,000 synchronous GETs on the loop, so the guard is simply that
    no attribute of these objects is ever touched from the test's own thread.
    """

    def __init__(self, reads, **values):
        self.__dict__["_reads"] = reads
        self.__dict__["_values"] = values

    def __getattr__(self, name):
        values = self.__dict__["_values"]
        if name not in values:
            raise AttributeError(name)
        self.__dict__["_reads"].append(threading.current_thread())
        return values[name]


def _lazy_show_section(tmp_path, reads):
    library_root = tmp_path / "TV Shows"
    episode = LazyPlexObject(
        reads, type="episode", ratingKey="30", title="Pilot",
        parentIndex=1, index=1, year=None, guids=[],
    )
    season = LazyPlexObject(
        reads, type="season", ratingKey="20", title="Season 1", index=1,
        year=None, guids=[], episodes=lambda: [episode],
    )
    show = LazyPlexObject(
        reads, type="show", ratingKey="10", title="Breaking Bad", year=2008,
        locations=[str(library_root / "Breaking Bad (2008)")],
        guids=[], seasons=lambda: [season],
    )
    return LazyPlexObject(
        reads, title="TV Shows", locations=[str(library_root)], all=lambda: [show],
    )


async def test_no_plexapi_attribute_is_read_on_the_event_loop(session, tmp_path):
    config = _config(tmp_path)
    reads = []
    section = _lazy_show_section(tmp_path, reads)
    show_poster = naming.asset_path(config, "TV Shows", "Breaking Bad (2008)", "poster")
    _write(show_poster, b"show-poster")

    report = await adopt_library(session, config, section, dry_run=False)

    assert report.items == 3
    assert reads, "the walk read no plexapi attributes at all"
    assert all(t is not threading.current_thread() for t in reads)


# --- art kinds this config would never render are not phantom gaps -----------


async def test_a_disabled_art_kind_is_not_counted_as_a_missing_asset(session, tmp_path):
    config = _config(tmp_path)
    config.artwork.background.enabled = False
    section = _movie_section(tmp_path)
    poster = naming.asset_path(config, "Movies", "Dune (2024)", "poster")
    _write(poster, b"poster-bytes")

    report = await adopt_library(session, config, section, dry_run=False)

    # Without this gate the disabled background counts as a missing asset --
    # the one number deploy/README.md tells the operator to scrutinise.
    assert report.missing_assets == 0
    assert report.skipped_by_config == 1
    assert report.renders == 1


async def test_an_unnumbered_episode_is_counted_and_does_not_abort_the_walk(session, tmp_path):
    """A single ``index: None`` episode must not kill the library walk.

    Plex's TV agent returns ``index: None`` for year-grouped specials -- 74 of
    12,694 episodes on the production server. ``naming._file_name`` raises
    ``ValueError: title_card requires episode_number`` for those, and before the
    guard that exception propagated out of ``_adopt_item`` and ended the entire
    run, ~12,000 items in.
    """
    config = _config(tmp_path)
    library_root = tmp_path / "TV Shows"
    good = FakeEpisode("30", "Pilot", season_number=1, episode_number=1)
    unnumbered = FakeEpisode("31", "Episode 05-28", season_number=1, episode_number=None)
    season = FakeSeason("20", "Season 1", season_number=1, episodes=[unnumbered, good])
    show = FakeShow("10", "Breaking Bad", str(library_root / "Breaking Bad (2008)"), [season])
    section = FakeSection("TV Shows", [str(library_root)], [show])

    show_poster = naming.asset_path(config, "TV Shows", "Breaking Bad (2008)", "poster")
    season_poster = naming.asset_path(
        config, "TV Shows", "Breaking Bad (2008)", "season_poster", season_number=1
    )
    title_card = naming.asset_path(
        config, "TV Shows", "Breaking Bad (2008)", "title_card", season_number=1, episode_number=1
    )
    _write(show_poster, b"show-poster")
    _write(season_poster, b"season-poster")
    _write(title_card, b"title-card")

    report = await adopt_library(session, config, section, dry_run=False)

    # The walk reached the end: all four items were visited, the unnumbered one
    # included -- it gets a media_items row like anything else.
    assert report.items == 4
    assert report.unnumbered == 1
    # Everything else still adopted: the numbered sibling's title card, the
    # season poster and the show poster. The unnumbered episode is *not* a
    # missing asset (only the show background is) and *not* skipped_by_config.
    assert report.renders == 3
    assert report.by_kind == {"poster": 1, "season_poster": 1, "title_card": 1}
    assert report.missing_assets == 1
    assert report.skipped_by_config == 0

    native_ids = set(
        (
            await session.execute(
                select(MediaItemServerRef.native_id).where(MediaItemServerRef.server == "plex")
            )
        ).scalars()
    )
    assert native_ids == {"10", "20", "30", "31"}
    # The unnumbered episode has no render row -- there was no file to name.
    unnumbered_row = await _media_item_by_native_id(session, "31")
    assert (
        await session.execute(select(Render).where(Render.item_id == unnumbered_row.id))
    ).scalars().all() == []


async def test_an_unnumbered_season_is_counted_and_takes_its_episodes_with_it(session, tmp_path):
    """``season_poster`` needs ``season_number``; its episodes inherit the gap.

    ``_resolved_episode`` takes ``season_number`` from ``episode.parentIndex``,
    so an unnumbered season yields episodes that also fail the ``title_card
    requires season_number`` branch of ``naming._file_name`` -- a second raise
    site the same guard has to cover.
    """
    config = _config(tmp_path)
    library_root = tmp_path / "TV Shows"
    episode = FakeEpisode("31", "Episode 05-28", season_number=None, episode_number=None)
    orphan_season = FakeSeason("21", "Specials", season_number=None, episodes=[episode])
    good_season = FakeSeason(
        "20", "Season 1", season_number=1,
        episodes=[FakeEpisode("30", "Pilot", season_number=1, episode_number=1)],
    )
    show = FakeShow(
        "10", "Breaking Bad", str(library_root / "Breaking Bad (2008)"),
        [orphan_season, good_season],
    )
    section = FakeSection("TV Shows", [str(library_root)], [show])

    show_poster = naming.asset_path(config, "TV Shows", "Breaking Bad (2008)", "poster")
    title_card = naming.asset_path(
        config, "TV Shows", "Breaking Bad (2008)", "title_card", season_number=1, episode_number=1
    )
    _write(show_poster, b"show-poster")
    _write(title_card, b"title-card")

    report = await adopt_library(session, config, section, dry_run=False)

    assert report.items == 5  # show, two seasons, two episodes
    # The unnumbered season's season_poster and its episode's title_card.
    assert report.unnumbered == 2
    assert report.renders == 2
    assert report.by_kind == {"poster": 1, "title_card": 1}


async def test_a_tba_titled_episode_title_card_is_not_counted_as_a_missing_asset(
    session, tmp_path
):
    config = _config(tmp_path)
    library_root = tmp_path / "TV Shows"
    episode = FakeEpisode("30", "TBA", season_number=1, episode_number=1)
    season = FakeSeason("20", "Season 1", season_number=1, episodes=[episode])
    show = FakeShow("10", "Breaking Bad", str(library_root / "Breaking Bad (2008)"), [season])
    section = FakeSection("TV Shows", [str(library_root)], [show])

    report = await adopt_library(session, config, section, dry_run=True)

    assert config.skip_tba is True
    # show poster + show background + season poster are genuinely absent; the
    # title card is one render_artifact would refuse to make at all.
    assert report.missing_assets == 3
    assert report.skipped_by_config == 1


async def test_a_rerun_splits_newly_adopted_from_re_confirmed(session, tmp_path):
    """Row 122: a rerun over an already-adopted library reported the same
    ``renders`` count as the first run, so an operator watching the number
    could not tell a resumed pass made progress -- or reasonably suspected it
    made none. One walk, one of each: a brand new item alongside one a prior
    run already adopted.
    """
    config = _config(tmp_path)
    library_root = tmp_path / "Movies"
    # Distinct basenames, not both "movie.mkv": with no guids on either
    # FakeMovie, a movie's identity falls back to ``root_folder/basename``
    # (identity.py's path branch), so two same-named files in the SAME
    # folder would collide onto one identity -- kept distinct here so the
    # two rows this test needs really are two rows.
    new_path = str(library_root / "Dune (2024)" / "Dune Part Two.mkv")
    old_path = str(library_root / "Arrival (2016)" / "Arrival.mkv")
    section = FakeSection("Movies", [str(library_root)], [
        FakeMovie("1", "Dune: Part Two", new_path),
        FakeMovie("2", "Arrival", old_path, year=2016),
    ])
    new_poster = naming.asset_path(config, "Movies", "Dune (2024)", "poster")
    old_poster = naming.asset_path(config, "Movies", "Arrival (2016)", "poster")
    _write(new_poster, b"poster-bytes")
    _write(old_poster, b"poster-bytes")

    # Sharing the SAME identity the walk's own discovery will compute for
    # "2" (no guids, so a movie's identity falls back to its root folder and
    # file's basename) -- see test_existing_non_adopted_render_is_left_untouched_
    # and_skipped's comment for why a mismatch here would have
    # upsert_server_ref steal the ref out from under this row.
    already_adopted = await _upsert_media_item(
        session, resolved("plex", "2", title="Arrival", tmdb_id=None, file_path=old_path,
                           root_folder="Arrival (2016)"),
    )
    session.add(Render(
        item_id=already_adopted.id, art_kind="poster", asset_path=str(old_poster),
        status="rendered", fingerprint="a-stale-fingerprint", adopted=True,
    ))
    await session.commit()

    report = await adopt_library(session, config, section, dry_run=False)

    assert report.adopted == 1
    assert report.reconfirmed == 1
    assert report.renders == 2  # the old total: still both, for compatibility
