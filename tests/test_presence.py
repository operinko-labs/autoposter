"""The `absent` rule (spec §1): decided once per library and server."""
from sqlalchemy import select

from autoposter.db.models import MetadataWrite, RenderDelivery
from autoposter.render import pipeline
from autoposter.servers import presence
from autoposter.servers.registry import Servers

from conftest import seed_media_item
from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS


async def _rendered(session, item):
    render = await pipeline._get_or_create_render(session, item, "poster", "/a/p.jpg")
    render.status = "rendered"
    await session.commit()
    return render


async def test_an_item_in_an_uncarried_library_is_absent_in_both_tables(session):
    movie = await seed_media_item(session, "rk1", library="Movies", title="M")
    photo = await seed_media_item(session, "rk2", library="Photos", title="P")
    await _rendered(session, photo)

    outcome = await presence.apply_presence(session, "jellyfin", {"Movies"})
    await session.commit()

    metadata = {row.item_id: row for row in (await session.execute(select(MetadataWrite))).scalars()}
    assert set(metadata) == {photo.id}
    assert metadata[photo.id].status == "absent"
    assert metadata[photo.id].detail == presence.ABSENT_DETAIL
    assert metadata[photo.id].next_attempt_at is None
    delivery = (await session.execute(select(RenderDelivery))).scalar_one()
    assert delivery.status == "absent" and delivery.server == "jellyfin"
    # Per table, not summed: one ITEM and one RENDER, which a single `2` gave
    # an operator no way to tell apart (review minor 2).
    assert outcome == {
        "metadata": {"absent": 1, "rearmed": 0},
        "artwork": {"absent": 1, "rearmed": 0},
    }
    assert movie.id not in metadata


async def test_absent_never_overwrites_what_the_server_already_holds(session):
    item = await seed_media_item(session, "rk3", library="Photos", title="P")
    render = await _rendered(session, item)
    session.add(RenderDelivery(render_id=render.id, server="jellyfin", status="uploaded"))
    session.add(MetadataWrite(item_id=item.id, server="jellyfin", status="written"))
    await session.commit()

    await presence.apply_presence(session, "jellyfin", {"Movies"})
    await session.commit()

    assert (await session.execute(select(RenderDelivery.status))).scalar_one() == "uploaded"
    assert (await session.execute(select(MetadataWrite.status))).scalar_one() == "written"


async def test_a_reappearing_library_re_arms_its_absent_rows(session):
    item = await seed_media_item(session, "rk4", library="Photos", title="P")
    await _rendered(session, item)
    await presence.apply_presence(session, "jellyfin", {"Movies"})
    await session.commit()

    outcome = await presence.apply_presence(session, "jellyfin", {"Movies", "Photos"})
    await session.commit()

    assert outcome == {
        "metadata": {"absent": 0, "rearmed": 1},
        "artwork": {"absent": 0, "rearmed": 1},
    }
    delivery = (await session.execute(select(RenderDelivery))).scalar_one()
    assert delivery.status == "pending" and delivery.next_attempt_at is not None
    metadata = (await session.execute(select(MetadataWrite))).scalar_one()
    assert metadata.status == "pending" and metadata.attempts == 0


async def test_a_stale_pending_row_for_an_absent_library_is_reclassified(session):
    """Spec §1's first-pass reclassification: the retry queue a mismatched
    map leaves behind is cleared rather than retried forever."""
    item = await seed_media_item(session, "rk5", library="Photos", title="P")
    render = await _rendered(session, item)
    session.add(RenderDelivery(render_id=render.id, server="jellyfin", status="pending"))
    await session.commit()

    await presence.apply_presence(session, "jellyfin", {"Movies"})
    await session.commit()

    row = (await session.execute(select(RenderDelivery.status, RenderDelivery.next_attempt_at))).one()
    assert row.status == "absent" and row.next_attempt_at is None


async def test_present_libraries_reads_the_server_and_refresh_walks_the_registry(session):
    await seed_media_item(session, "rk6", library="Photos", title="P")
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS, libraries={"Movies"})
    assert await presence.present_libraries(jf) == {"Movies"}

    outcomes = await presence.refresh_presence(session, Servers({"jellyfin": jf}))
    await session.commit()
    assert outcomes == {"jellyfin": {
        "metadata": {"absent": 1, "rearmed": 0},
        "artwork": {"absent": 0, "rearmed": 0},
    }}


async def test_a_server_that_cannot_list_its_libraries_is_skipped(session):
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)

    async def boom():
        raise RuntimeError("down")

    jf.library_names = boom
    assert await presence.refresh_presence(session, Servers({"jellyfin": jf})) == {}


async def test_a_server_that_lists_no_libraries_at_all_is_skipped_too(session):
    """Review C2: an empty answer is not "carries nothing" -- a Jellyfin still
    starting up, an API key without library scope, or an excluded_libraries
    naming every folder all answer cleanly with nothing in them, and stamping
    on that would mark the entire database absent on that server and overwrite
    every non-terminal row's detail, attempts and horizon on the way."""
    item = await seed_media_item(session, "rk7", library="Photos", title="P")
    await _rendered(session, item)
    # Neither `libraries` nor `items`, which is exactly what an unconfigured
    # double -- and a real server with nothing mounted yet -- answers.
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    assert await presence.present_libraries(jf) == set()

    assert await presence.refresh_presence(session, Servers({"jellyfin": jf})) == {}
    await session.commit()

    assert (await session.execute(select(MetadataWrite))).scalars().all() == []
    assert (await session.execute(select(RenderDelivery))).scalars().all() == []
