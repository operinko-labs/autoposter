"""``db.refs``: the read-only lookups every reader outside the pipeline uses
to translate between a ``media_items`` row and its per-server native ids."""
from autoposter.db import refs
from autoposter.render import pipeline
from media_server_doubles import resolved


async def test_refs_for_and_item_id_for(session):
    row = await pipeline._upsert_media_item(session, resolved("plex", "42", file_path="/m.mkv"))
    await pipeline._upsert_media_item(session, resolved("jellyfin", "0a", file_path="/m.mkv"))
    assert await refs.refs_for(session, row.id) == {"plex": "42", "jellyfin": "0a"}
    assert await refs.item_id_for(session, "jellyfin", "0a") == row.id
    assert await refs.item_id_for(session, "plex", "nope") is None
    assert await refs.native_ids(session, [row.id], "plex") == {row.id: "42"}


async def test_refs_for_items_batches_over_a_page(session):
    row1 = await pipeline._upsert_media_item(session, resolved("plex", "42", file_path="/m.mkv"))
    await pipeline._upsert_media_item(session, resolved("jellyfin", "0a", file_path="/m.mkv"))
    row2 = await pipeline._upsert_media_item(session, resolved("plex", "43", file_path="/n.mkv"))
    assert await refs.refs_for_items(session, [row1.id, row2.id]) == {
        row1.id: {"plex": "42", "jellyfin": "0a"},
        row2.id: {"plex": "43"},
    }
    assert await refs.refs_for_items(session, []) == {}
