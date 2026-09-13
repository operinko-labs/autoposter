"""The identity upsert -- the pipeline never mints a second row for one item.

It keys ``media_items`` on the item's identity (``identity_key_for``,
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

from autoposter.db.models import (
    ActionDismissal, EventLog, ItemMetadataOverride, MediaItem, MediaItemServerRef,
)
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
    """One row, its Plex ref re-pointed -- and exactly ONE Plex ref left
    (spec §4.1). The id the item used to be known by is gone: leaving it
    behind would give the item two live Plex ids and every reader an
    arbitrary choice between them."""
    await pipeline._upsert_media_item(session, resolved("plex", "42", tmdb_id=603, file_path="/m.mkv"))
    row = await pipeline._upsert_media_item(session, resolved("plex", "99", tmdb_id=603, file_path="/m.mkv"))
    refs = (await session.execute(select(MediaItemServerRef.native_id))).scalars().all()
    assert set(refs) == {"99"} and (await session.execute(select(MediaItem.id))).scalars().all() == [row.id]


async def test_an_episode_finds_its_parent_by_identity(session):
    """Two hops: a season's parent is the show, and an episode's parent is
    its own SEASON, not the show directly (config/impact.py's model;
    servers/identity.py's ``parent_identity_key_for``)."""
    show = await pipeline._upsert_media_item(session, resolved("plex", "5", kind="show", tvdb_id=71663, tmdb_id=None, file_path=None, root_folder="The Simpsons"))
    season = await pipeline._upsert_media_item(session, dataclasses.replace(
        resolved("plex", "20", kind="season", tvdb_id=71663, tmdb_id=None, season_number=2, file_path=None),
        parent_tvdb_id=71663,
    ))
    assert season.parent_id == show.id

    ep = dataclasses.replace(resolved("plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None, season_number=2, episode_number=3, file_path=None), parent_tvdb_id=71663)
    row = await pipeline._upsert_media_item(session, ep)
    assert row.parent_id == season.id


async def test_an_episode_upserted_before_its_season_gets_its_parent_once_the_season_arrives(session):
    """No trigger walks back over an episode when its season is later upserted
    -- the fill-in happens the next time THIS episode itself is upserted, the
    same "a later pass fills it in" contract ``_upsert_media_item`` has always
    carried for a not-yet-processed parent."""
    ep = dataclasses.replace(
        resolved("plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None,
                 season_number=2, episode_number=3, file_path=None),
        parent_tvdb_id=71663,
    )
    first = await pipeline._upsert_media_item(session, ep)
    assert first.parent_id is None

    season = await pipeline._upsert_media_item(session, resolved(
        "plex", "20", kind="season", tvdb_id=71663, tmdb_id=None, season_number=2, file_path=None,
    ))

    second = await pipeline._upsert_media_item(session, ep)
    assert second.parent_id == season.id


async def test_a_later_upsert_with_no_parent_id_does_not_clobber_an_existing_one(session):
    """The upsert's ``set_`` must not overwrite a resolved ``parent_id`` with
    NULL just because THIS pass found none -- a re-visit through a path that
    carries no parent guids at all must leave an already-linked child alone."""
    season = await pipeline._upsert_media_item(session, resolved(
        "plex", "20", kind="season", tvdb_id=71663, tmdb_id=None, season_number=2, file_path=None,
    ))
    ep = dataclasses.replace(
        resolved("plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None,
                 season_number=2, episode_number=3, file_path=None),
        parent_tvdb_id=71663,
    )
    row = await pipeline._upsert_media_item(session, ep)
    assert row.parent_id == season.id

    ep_no_parent_guid = dataclasses.replace(ep, parent_tvdb_id=None)
    row2 = await pipeline._upsert_media_item(session, ep_no_parent_guid)
    assert row2.parent_id == season.id, "a parent-less upsert nulled out an existing parent_id"


# --- re-key in place (spec §4.2/§4.6 amendments) -----------------------------


async def test_a_legacy_placeholder_key_is_promoted_to_the_full_key(session):
    """The migration's placeholder for an unresolved row (``kind:legacy:plex:<id>``)
    is promoted onto the real identity the first time Plex resolves it."""
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


async def test_a_file_replacement_rekeys_the_row_in_place(session):
    """A Radarr quality upgrade replaces a movie's file: same Plex
    ratingKey, same tmdb id, new basename -- and the basename is in the key.

    Inserting a second row under the new key would leave every child the
    operator created (an override they typed, a dismissal they made) on a
    row nothing will ever resolve again. Same ref plus the same
    ``kind:provider:id:coords:`` prefix means the same item, so the row
    itself moves onto the new key and keeps its id and its children.
    """
    row = await pipeline._upsert_media_item(
        session, resolved("plex", "42", tmdb_id=603,
                          file_path="/m/The Matrix (1999)/old.mkv"),
    )
    assert row.identity_key == "movie:tmdb:603::old.mkv"
    session.add(ItemMetadataOverride(item_id=row.id, field="critic_rating", value="8.5"))
    session.add(ActionDismissal(item_id=row.id, art_kind="poster", evidence="deadbeef"))
    await session.flush()

    upgraded = await pipeline._upsert_media_item(
        session, resolved("plex", "42", tmdb_id=603,
                          file_path="/m/The Matrix (1999)/new.mkv"),
    )

    assert upgraded.id == row.id
    assert upgraded.identity_key == "movie:tmdb:603::new.mkv"
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 1
    override = (await session.execute(select(ItemMetadataOverride))).scalar_one()
    assert override.item_id == row.id
    dismissal = (await session.execute(select(ActionDismissal))).scalar_one()
    assert dismissal.item_id == row.id


async def test_a_different_plex_item_with_the_same_tmdb_id_stays_a_second_row(session):
    """The 4K/HD pair: two Plex items, one tmdb id, two files. The ref is
    what separates them from the file-replacement case above -- ratingKey 43
    points at no row of ours, so nothing is re-keyed and the second file
    gets its own row, exactly as identity.py's basename rule intends."""
    hd = await pipeline._upsert_media_item(
        session, resolved("plex", "42", tmdb_id=603, file_path="/m/m - 1080p.mkv"),
    )
    uhd = await pipeline._upsert_media_item(
        session, resolved("plex", "43", tmdb_id=603, file_path="/m/m - 4K.mkv"),
    )

    assert uhd.id != hd.id
    assert {r.identity_key for r in (await session.execute(select(MediaItem))).scalars().all()} == {
        "movie:tmdb:603::m - 1080p.mkv", "movie:tmdb:603::m - 4K.mkv",
    }
    refs = dict((
        await session.execute(select(MediaItemServerRef.native_id, MediaItemServerRef.item_id))
    ).all())
    assert refs == {"42": hd.id, "43": uhd.id}


async def test_the_prefix_match_is_exact_on_all_four_fields(session):
    """Only the FILE field may differ. Kind, provider, provider id and the
    season/episode coordinates must all match, or the two keys name two
    items and re-keying one onto the other would silently merge them."""
    assert pipeline._is_file_replacement("movie:tmdb:603::old.mkv", "movie:tmdb:603::new.mkv")
    assert not pipeline._is_file_replacement("movie:tmdb:1::a.mkv", "movie:tmdb:2::a.mkv")
    assert not pipeline._is_file_replacement("movie:tmdb:603::a.mkv", "show:tmdb:603::a.mkv")
    assert not pipeline._is_file_replacement("movie:imdb:tt1::a.mkv", "movie:tmdb:603::a.mkv")
    assert not pipeline._is_file_replacement("episode:tvdb:71663:s2e3:a", "episode:tvdb:71663:s2e4:b")
    assert not pipeline._is_file_replacement("movie:tmdb:603::a.mkv", "movie:tmdb:603::a.mkv")
    # The legacy placeholder is four fields, not five: it has its own branch.
    assert not pipeline._is_file_replacement("movie:legacy:plex:4", "movie:tmdb:603::a.mkv")

    episode = dataclasses.replace(
        resolved("plex", "6", kind="episode", tvdb_id=71663, tmdb_id=None,
                 season_number=2, episode_number=3, file_path=None),
        parent_tvdb_id=71663,
    )
    third = await pipeline._upsert_media_item(session, episode)
    assert third.identity_key == "episode:tvdb:71663:s2e3:"

    fourth = await pipeline._upsert_media_item(
        session, dataclasses.replace(episode, episode_number=4),
    )
    assert fourth.id != third.id
    reloaded = (
        await session.execute(select(MediaItem).where(MediaItem.id == third.id))
    ).scalar_one()
    assert reloaded.identity_key == "episode:tvdb:71663:s2e3:", "s2e4 claimed s2e3's row"


async def test_a_rematch_onto_a_different_identity_is_not_rekeyed(session):
    """A Plex re-match onto a genuinely different item -- another tmdb id --
    is neither the legacy placeholder nor a file replacement. The old row is
    left exactly as it was and the ref simply re-points; the stale row is
    ``scheduler/prune.py``'s business, not this function's."""
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


async def test_a_legacy_key_is_not_promoted_when_the_full_key_already_exists_elsewhere(session):
    """The full key can already belong to ANOTHER row (resolved through a
    different native id first) -- promoting the legacy row on top of it
    would collide with the unique constraint. The legacy row is left alone
    and the ref re-points to the row that already carries the full key."""
    legacy_row = MediaItem(
        identity_key="movie:legacy:plex:4", library="Movies", kind="movie", title="Nothing",
        year=2002, season_number=None, episode_number=None, root_folder=None, file_path=None,
        tmdb_id=None, tvdb_id=None, imdb_id=None,
    )
    session.add(legacy_row)
    await session.flush()
    session.add(MediaItemServerRef(item_id=legacy_row.id, server="plex", native_id="4", library="Movies"))
    await session.commit()

    full_row = await pipeline._upsert_media_item(
        session, resolved("plex", "44", tmdb_id=603, file_path="/m/m.mkv"),
    )
    assert full_row.identity_key == "movie:tmdb:603::m.mkv"
    assert full_row.id != legacy_row.id

    result = await pipeline._upsert_media_item(
        session, resolved("plex", "4", tmdb_id=603, file_path="/m/m.mkv"),
    )
    assert result.id == full_row.id

    rows = (await session.execute(select(MediaItem))).scalars().all()
    assert len(rows) == 2
    legacy_reloaded = (
        await session.execute(select(MediaItem).where(MediaItem.id == legacy_row.id))
    ).scalar_one()
    assert legacy_reloaded.identity_key == "movie:legacy:plex:4"
    refs = dict((
        await session.execute(select(MediaItemServerRef.native_id, MediaItemServerRef.item_id))
    ).all())
    # ONE ref per (item, server), spec §4.1: the row keeps the Plex id the
    # LAST resolve gave it. "44" was how it was found first; "4" is what
    # Plex says now, and holding both would leave every reader to choose.
    assert refs == {"4": full_row.id}


async def test_a_legacy_key_is_never_promoted_by_a_non_plex_ref(session):
    """The legacy form is the migration's placeholder for a Plex id
    specifically (``kind:legacy:plex:<id>``) -- a Jellyfin ref sharing that
    same native id string must never promote it."""
    legacy_row = MediaItem(
        identity_key="movie:legacy:plex:4", library="Movies", kind="movie", title="Nothing",
        year=2002, season_number=None, episode_number=None, root_folder=None, file_path=None,
        tmdb_id=None, tvdb_id=None, imdb_id=None,
    )
    session.add(legacy_row)
    await session.flush()
    session.add(MediaItemServerRef(item_id=legacy_row.id, server="jellyfin", native_id="4", library="Movies"))
    await session.commit()

    new_row = await pipeline._upsert_media_item(
        session, resolved("jellyfin", "4", tmdb_id=603, file_path="/m/m.mkv"),
    )
    assert new_row.id != legacy_row.id
    rows = (await session.execute(select(MediaItem))).scalars().all()
    assert len(rows) == 2
    legacy_reloaded = (
        await session.execute(select(MediaItem).where(MediaItem.id == legacy_row.id))
    ).scalar_one()
    assert legacy_reloaded.identity_key == "movie:legacy:plex:4"


async def test_a_moved_native_id_writes_no_event_log_row(session):
    """The old re-key wrote an audit row every time a Plex key moved onto an
    identity match. Identity now IS the key, so a native id moving to another
    row of the ref table is an ordinary upsert and leaves events_log alone."""
    await pipeline._upsert_media_item(session, resolved("plex", "42", tmdb_id=603, file_path="/m.mkv"))
    await pipeline._upsert_media_item(session, resolved("plex", "99", tmdb_id=603, file_path="/m.mkv"))
    assert (await session.execute(select(EventLog))).scalars().all() == []
