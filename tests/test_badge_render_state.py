"""Badge upload state on the render row."""
from sqlalchemy import select

from autoposter.db.models import Render

from conftest import seed_media_item


async def _item(session):
    return await seed_media_item(session, "1", kind="movie", library="Movies", title="X")


async def test_a_new_render_has_no_badge_state(session):
    item = await _item(session)
    render = Render(item_id=item.id, art_kind="poster", asset_path="/x.jpg")
    session.add(render)
    await session.flush()
    assert render.badge_fingerprint is None
    assert render.uploaded_at is None
    assert render.upload_status == "pending"


async def test_badge_state_round_trips(session):
    item = await _item(session)
    render = Render(item_id=item.id, art_kind="poster", asset_path="/x.jpg",
                    badge_fingerprint="a" * 64, upload_status="uploaded")
    session.add(render)
    await session.flush()
    loaded = (await session.execute(select(Render).where(Render.id == render.id))).scalar_one()
    assert loaded.badge_fingerprint == "a" * 64
    assert loaded.upload_status == "uploaded"


async def test_badge_fingerprint_is_independent_of_the_base_fingerprint(session):
    """The whole point of a second fingerprint: a rating change re-badges
    without invalidating the base render."""
    item = await _item(session)
    render = Render(item_id=item.id, art_kind="poster", asset_path="/x.jpg",
                    fingerprint="b" * 64, badge_fingerprint="a" * 64)
    session.add(render)
    await session.flush()
    render.badge_fingerprint = "c" * 64
    await session.flush()
    assert render.fingerprint == "b" * 64
