"""The identity upsert -- the pipeline never mints a second row for one item.

Phase 2 keys ``media_items`` on the item's identity (``identity_key_for``,
servers/identity.py), never on any one server's own id. A server id is a
``MediaItemServerRef`` row instead, and a native id that moves -- a Plex
re-match, a library rebuild, a second server resolving the same item under
its own id -- just re-points that ref. That is the entirety of what the old
``_rekey_by_identity``/fork-stop machinery existed to do by hand: it is now a
side effect of the row being keyed by identity in the first place, so a twin
row for one identity cannot be minted and there is nothing left to re-key or
fork-stop.
"""
import dataclasses

from sqlalchemy import select

from autoposter.db.models import EventLog, MediaItem, MediaItemServerRef
from autoposter.render import pipeline
from media_server_doubles import resolved


async def test_upsert_keys_on_identity_and_writes_one_ref_per_server(session):
    plex = resolved("plex", "42", tmdb_id=603, file_path="/plex/Movies/m.mkv")
    jelly = resolved("jellyfin", "0a1b", tmdb_id=603, file_path="/jf/Movies/m.mkv")
    row_a = await pipeline._upsert_media_item(session, plex)
    row_b = await pipeline._upsert_media_item(session, jelly)
    assert row_a.id == row_b.id
    assert row_a.identity_key == "movie:tmdb:603::m.mkv"
    refs = (await session.execute(select(MediaItemServerRef).order_by(MediaItemServerRef.server))).scalars().all()
    assert [(r.server, r.native_id, r.item_id) for r in refs] == [("jellyfin", "0a1b", row_a.id), ("plex", "42", row_a.id)]


async def test_a_moved_plex_key_updates_the_ref_not_the_item(session):
    await pipeline._upsert_media_item(session, resolved("plex", "42", tmdb_id=603, file_path="/m.mkv"))
    row = await pipeline._upsert_media_item(session, resolved("plex", "99", tmdb_id=603, file_path="/m.mkv"))
    refs = (await session.execute(select(MediaItemServerRef.native_id))).scalars().all()
    assert set(refs) == {"42", "99"} and (await session.execute(select(MediaItem.id))).scalars().all() == [row.id]


async def test_an_episode_finds_its_parent_by_identity(session):
    show = await pipeline._upsert_media_item(session, resolved("plex", "5", kind="show", tvdb_id=71663, tmdb_id=None, file_path=None, root_folder="The Simpsons"))
    ep = dataclasses.replace(resolved("plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None, season_number=2, episode_number=3, file_path="/tv/s02e03.mkv"), parent_tvdb_id=71663)
    row = await pipeline._upsert_media_item(session, ep)
    assert row.parent_id == show.id


async def test_an_episode_upserted_before_its_show_gets_its_parent_once_the_show_arrives(session):
    """No trigger walks back over an episode when its show is later upserted
    -- the fill-in happens the next time THIS episode itself is upserted, the
    same "a later pass fills it in" contract ``_upsert_media_item`` has always
    carried for a not-yet-processed parent."""
    ep = dataclasses.replace(
        resolved("plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None,
                 season_number=2, episode_number=3, file_path="/tv/s02e03.mkv"),
        parent_tvdb_id=71663,
    )
    first = await pipeline._upsert_media_item(session, ep)
    assert first.parent_id is None

    show = await pipeline._upsert_media_item(session, resolved(
        "plex", "5", kind="show", tvdb_id=71663, tmdb_id=None, file_path=None,
        root_folder="The Simpsons",
    ))

    second = await pipeline._upsert_media_item(session, ep)
    assert second.parent_id == show.id


async def test_a_later_upsert_with_no_parent_id_does_not_clobber_an_existing_one(session):
    """The upsert's ``set_`` must not overwrite a resolved ``parent_id`` with
    NULL just because THIS pass found none -- a re-visit through a path that
    carries no parent guids at all must leave an already-linked child alone."""
    show = await pipeline._upsert_media_item(session, resolved(
        "plex", "5", kind="show", tvdb_id=71663, tmdb_id=None, file_path=None,
        root_folder="The Simpsons",
    ))
    ep = dataclasses.replace(
        resolved("plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None,
                 season_number=2, episode_number=3, file_path="/tv/s02e03.mkv"),
        parent_tvdb_id=71663,
    )
    row = await pipeline._upsert_media_item(session, ep)
    assert row.parent_id == show.id

    ep_no_parent_guid = dataclasses.replace(ep, parent_tvdb_id=None)
    row2 = await pipeline._upsert_media_item(session, ep_no_parent_guid)
    assert row2.parent_id == show.id, "a parent-less upsert nulled out an existing parent_id"


# --- weak-key promotion (task 8d, spec §4.2 amendment) ----------------------


async def test_a_bare_episode_key_is_promoted_to_the_full_key_when_the_file_arrives(session):
    """An adopted episode used to carry no file_path at all, keying on the
    bare ``kind:ns:id:coords:`` form. The next real resolve of the same
    native id must promote that row onto the full key in place, not mint a
    second row beside it."""
    bare = await pipeline._upsert_media_item(session, resolved(
        "plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None,
        season_number=2, episode_number=3, file_path=None,
    ))
    assert bare.identity_key == "episode:tvdb:71663:s2e3:"

    full = await pipeline._upsert_media_item(session, resolved(
        "plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None,
        season_number=2, episode_number=3, file_path="/tv/s02e03.mkv",
    ))
    assert full.id == bare.id
    assert full.identity_key == "episode:tvdb:71663:s2e3:s02e03.mkv"
    rows = (await session.execute(select(MediaItem))).scalars().all()
    assert len(rows) == 1
    refs = (
        await session.execute(select(MediaItemServerRef.native_id, MediaItemServerRef.item_id))
    ).all()
    assert refs == [("6", full.id)]


async def test_a_legacy_placeholder_key_is_promoted_to_the_full_key(session):
    """The migration's placeholder for an unresolved row (``kind:legacy:plex:<id>``)
    is promoted onto the real identity the first time Plex resolves it, exactly
    like the bare-key case above."""
    legacy_row = MediaItem(
        identity_key="movie:legacy:plex:4", library="Movies", kind="movie", title="Nothing",
        year=2002, season_number=None, episode_number=None, root_folder=None, file_path=None,
        tmdb_id=None, tvdb_id=None, imdb_id=None,
    )
    session.add(legacy_row)
    await session.flush()
    session.add(MediaItemServerRef(item_id=legacy_row.id, server="plex", native_id="4", library="Movies"))
    await session.commit()

    full = await pipeline._upsert_media_item(
        session, resolved("plex", "4", tmdb_id=603, file_path="/m/m.mkv"),
    )
    assert full.id == legacy_row.id
    assert full.identity_key == "movie:tmdb:603::m.mkv"
    rows = (await session.execute(select(MediaItem))).scalars().all()
    assert len(rows) == 1


async def test_a_genuine_rematch_is_not_promoted(session):
    """A Plex re-match onto a genuinely DIFFERENT identity is not weak -- the
    old row is left exactly as it was, and the ref simply re-points, same as
    today's behaviour after Task 7."""
    old = await pipeline._upsert_media_item(
        session, resolved("plex", "42", tmdb_id=1, file_path="/a.mkv"),
    )
    assert old.identity_key == "movie:tmdb:1::a.mkv"

    new = await pipeline._upsert_media_item(
        session, resolved("plex", "42", tmdb_id=2, file_path="/b.mkv"),
    )
    assert new.id != old.id
    rows = (await session.execute(select(MediaItem))).scalars().all()
    assert len(rows) == 2
    refs = (
        await session.execute(select(MediaItemServerRef.native_id, MediaItemServerRef.item_id))
    ).all()
    assert refs == [("42", new.id)]


async def test_a_weak_row_is_not_promoted_when_the_full_key_already_exists_elsewhere(session):
    """The full key can already belong to ANOTHER row (resolved through a
    different native id first) -- promoting the weak row on top of it would
    collide with the unique constraint. The weak row is left alone and the
    ref re-points to the row that already carries the full key."""
    weak = await pipeline._upsert_media_item(session, resolved(
        "plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None,
        season_number=2, episode_number=3, file_path=None,
    ))
    assert weak.identity_key == "episode:tvdb:71663:s2e3:"

    full_row = await pipeline._upsert_media_item(session, resolved(
        "plex", "66", kind="episode", tvdb_id=71663, tmdb_id=None,
        season_number=2, episode_number=3, file_path="/tv/s02e03.mkv",
    ))
    assert full_row.identity_key == "episode:tvdb:71663:s2e3:s02e03.mkv"
    assert full_row.id != weak.id

    result = await pipeline._upsert_media_item(session, resolved(
        "plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None,
        season_number=2, episode_number=3, file_path="/tv/s02e03.mkv",
    ))
    assert result.id == full_row.id

    rows = (await session.execute(select(MediaItem))).scalars().all()
    assert len(rows) == 2
    weak_reloaded = (
        await session.execute(select(MediaItem).where(MediaItem.id == weak.id))
    ).scalar_one()
    assert weak_reloaded.identity_key == "episode:tvdb:71663:s2e3:"
    refs = dict((
        await session.execute(select(MediaItemServerRef.native_id, MediaItemServerRef.item_id))
    ).all())
    assert refs == {"6": full_row.id, "66": full_row.id}


async def test_a_moved_native_id_writes_no_event_log_row(session):
    """The old re-key wrote an audit row every time a Plex key moved onto an
    identity match. Identity now IS the key, so a native id moving to another
    row of the ref table is an ordinary upsert and leaves events_log alone."""
    await pipeline._upsert_media_item(session, resolved("plex", "42", tmdb_id=603, file_path="/m.mkv"))
    await pipeline._upsert_media_item(session, resolved("plex", "99", tmdb_id=603, file_path="/m.mkv"))
    assert (await session.execute(select(EventLog))).scalars().all() == []
