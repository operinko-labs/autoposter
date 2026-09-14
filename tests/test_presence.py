"""The `absent` rule (spec §1): decided once per library and server."""
from sqlalchemy import select

from autoposter import deliveries
from autoposter.db.models import MetadataWrite, Render, RenderDelivery
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


async def _upload_status(session, render_id: int) -> str:
    return (await session.execute(
        select(Render.upload_status).where(Render.id == render_id)
    )).scalar_one()


async def test_a_reclassification_rolls_up_the_render_it_stamped(session):
    """Review I2/T4: `renders.upload_status` is what /api/library's filter and
    the dashboard tiles read, and nothing recomputed it after presence wrote
    `render_deliveries` directly -- so the retry queue was cleared and every
    operator-visible surface still said `pending`."""
    item = await seed_media_item(session, "rk8", library="Photos", title="P")
    render = await _rendered(session, item)
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=60)
    assert await deliveries.rollup(session, render.id) == "pending"
    await session.commit()

    await presence.apply_presence(session, "jellyfin", {"Movies"})
    await session.commit()

    assert await _upload_status(session, render.id) == "skipped"


async def test_the_roll_up_keeps_what_the_servers_that_do_carry_it_say(session):
    """The whole ladder, not a blanket `skipped`: a render Plex already holds
    is uploaded, whatever Jellyfin's own row just became."""
    item = await seed_media_item(session, "rk9", library="Photos", title="P")
    render = await _rendered(session, item)
    await deliveries.record(session, render.id, "plex", "uploaded", fingerprint="fp")
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=60)
    assert await deliveries.rollup(session, render.id) == "pending"
    await session.commit()

    await presence.apply_presence(session, "jellyfin", {"Movies"})
    await session.commit()

    assert await _upload_status(session, render.id) == "uploaded"


async def test_a_re_armed_render_rolls_back_up_to_pending(session):
    """The other direction: a library that reappears puts its renders back in
    the queue, and the column the operator reads has to follow."""
    item = await seed_media_item(session, "rk10", library="Photos", title="P")
    render = await _rendered(session, item)
    await deliveries.record(session, render.id, "jellyfin", "pending", retry_in=60)
    await session.commit()
    await presence.apply_presence(session, "jellyfin", {"Movies"})
    await session.commit()
    assert await _upload_status(session, render.id) == "skipped"

    await presence.apply_presence(session, "jellyfin", {"Movies", "Photos"})
    await session.commit()

    assert await _upload_status(session, render.id) == "pending"


async def test_an_absent_only_render_rolls_up_as_skipped(session):
    """`rollup`'s own ladder has an explicit `absent` arm now: an absent row
    never raises the roll-up above what the present servers say, and a render
    whose every row is absent is exactly "nothing to report"."""
    item = await seed_media_item(session, "rk11", library="Photos", title="P")
    render = await _rendered(session, item)
    await deliveries.record(session, render.id, "jellyfin", "absent", detail=presence.ABSENT_DETAIL)
    assert await deliveries.rollup(session, render.id) == "skipped"


async def test_the_identity_server_stamping_absent_is_warned_about_by_name(session, caplog):
    """Review I5: presence applies to Plex too -- a section renamed after
    ingest, or added to excluded_libraries later, genuinely no longer holds
    the item. That is the one place "Plex-only deployments see no behaviour
    change beyond recorded rows" could stop being true, so it is announced
    with the counts and the section names rather than going silent."""
    item = await seed_media_item(session, "rk12", library="Retired Section", title="P")
    await _rendered(session, item)

    with caplog.at_level("WARNING", logger="autoposter.servers.presence"):
        outcome = await presence.apply_presence(session, "plex", {"Movies"})
    await session.commit()

    assert outcome == {
        "metadata": {"absent": 1, "rearmed": 0},
        "artwork": {"absent": 1, "rearmed": 0},
    }
    messages = [
        r.getMessage() for r in caplog.records if r.name == "autoposter.servers.presence"
    ]
    assert len(messages) == 1, "once per pass per server, not once per row"
    assert "plex is the identity server" in messages[0]
    assert "1 item(s) and 1 render(s)" in messages[0]
    assert "Retired Section" in messages[0]
    assert "http" not in messages[0]


async def test_a_matching_plex_stamps_nothing_and_says_nothing(session, caplog):
    """The steady state on the identity server: every section still carried,
    so no stamp and no warning."""
    await seed_media_item(session, "rk13", library="Movies", title="M")

    with caplog.at_level("WARNING", logger="autoposter.servers.presence"):
        outcome = await presence.apply_presence(session, "plex", {"Movies", "TV Shows"})
    await session.commit()

    assert outcome == {
        "metadata": {"absent": 0, "rearmed": 0},
        "artwork": {"absent": 0, "rearmed": 0},
    }
    assert [r for r in caplog.records if r.name == "autoposter.servers.presence"] == []
