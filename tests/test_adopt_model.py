"""The adopted marker on renders."""
from sqlalchemy import select

from autoposter.db.models import Render

from conftest import seed_media_item


async def _render(session, **kw):
    item = await seed_media_item(session, "1", kind="movie", title="X", library="Movies")
    render = Render(item_id=item.id, art_kind="poster", asset_path="/x.jpg", **kw)
    session.add(render)
    await session.flush()
    return render


async def test_renders_are_not_adopted_by_default(session):
    render = await _render(session)
    assert render.adopted is False


async def test_the_adopted_marker_round_trips(session):
    render = await _render(session, adopted=True)
    loaded = (
        await session.execute(select(Render).where(Render.id == render.id))
    ).scalar_one()
    assert loaded.adopted is True
