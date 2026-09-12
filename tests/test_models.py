import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from autoposter.db.models import EventLog, Job, MediaItem, Render


async def test_media_item_roundtrip(session):
    session.add(MediaItem(
        identity_key="movie:legacy:plex:12345", library="Movies", kind="movie",
        tmdb_id=693134, imdb_id="tt15239678",
        title="Dune: Part Two", year=2024, root_folder="Dune Part Two (2024)",
    ))
    await session.commit()
    found = (
        await session.execute(
            select(MediaItem).where(MediaItem.identity_key == "movie:legacy:plex:12345")
        )
    ).scalar_one()
    assert found.title == "Dune: Part Two"
    assert found.kind == "movie"


async def test_identity_key_is_unique(session):
    session.add(MediaItem(identity_key="movie:legacy:plex:dup", library="Movies", kind="movie", title="A"))
    await session.commit()
    session.add(MediaItem(identity_key="movie:legacy:plex:dup", library="Movies", kind="movie", title="B"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_episode_links_to_parent_season(session):
    show = MediaItem(identity_key="show:legacy:plex:s1", library="TV Shows", kind="show", title="Severance")
    session.add(show)
    await session.flush()
    season = MediaItem(identity_key="season:legacy:plex:s1s2", library="TV Shows", kind="season",
                       title="Season 2", parent_id=show.id, season_number=2)
    session.add(season)
    await session.flush()
    ep = MediaItem(identity_key="episode:legacy:plex:e1", library="TV Shows", kind="episode",
                   title="Who Is Alive?", parent_id=season.id,
                   season_number=2, episode_number=3)
    session.add(ep)
    await session.commit()
    assert ep.parent_id == season.id


async def test_render_defaults_to_generate_source_mode(session):
    item = MediaItem(identity_key="movie:legacy:plex:r1", library="Movies", kind="movie", title="X")
    session.add(item)
    await session.flush()
    render = Render(item_id=item.id, art_kind="poster",
                    asset_path="/assets/Movies/X/poster.jpg")
    session.add(render)
    await session.commit()
    assert render.source_mode == "generate"
    assert render.status == "pending"


async def test_one_render_per_item_and_art_kind(session):
    item = MediaItem(identity_key="movie:legacy:plex:r2", library="Movies", kind="movie", title="Y")
    session.add(item)
    await session.flush()
    session.add(Render(item_id=item.id, art_kind="poster", asset_path="/a.jpg"))
    await session.commit()
    session.add(Render(item_id=item.id, art_kind="poster", asset_path="/b.jpg"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_two_art_kinds_for_one_item_are_allowed(session):
    item = MediaItem(identity_key="movie:legacy:plex:r3", library="Movies", kind="movie", title="Z")
    session.add(item)
    await session.flush()
    session.add(Render(item_id=item.id, art_kind="poster", asset_path="/p.jpg"))
    session.add(Render(item_id=item.id, art_kind="background", asset_path="/b.jpg"))
    await session.commit()
    rows = (await session.execute(select(Render).where(Render.item_id == item.id))).scalars().all()
    assert len(rows) == 2


async def test_only_one_pending_job_per_dedupe_key(session):
    session.add(Job(kind="process_item", payload={"rating_key": "1"},
                    dedupe_key="process_item:1"))
    await session.commit()
    session.add(Job(kind="process_item", payload={"rating_key": "1"},
                    dedupe_key="process_item:1"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_done_job_does_not_block_a_new_pending_one(session):
    session.add(Job(kind="process_item", payload={}, dedupe_key="k", state="done"))
    await session.commit()
    session.add(Job(kind="process_item", payload={}, dedupe_key="k"))
    await session.commit()
    rows = (await session.execute(select(Job).where(Job.dedupe_key == "k"))).scalars().all()
    assert len(rows) == 2


async def test_event_log_stores_raw_payload(session):
    session.add(EventLog(source="sonarr", event_type="Download",
                         payload={"series": {"tvdbId": 371980}}))
    await session.commit()
    row = (await session.execute(select(EventLog))).scalar_one()
    assert row.payload["series"]["tvdbId"] == 371980
