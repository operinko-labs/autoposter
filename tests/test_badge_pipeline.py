"""The badge stage inside the per-item pipeline."""
import asyncio
import threading
from pathlib import Path

from sqlalchemy import select

from autoposter.db.models import Render
from autoposter.plex.artwork import upload_artwork as _plex_upload_artwork
from autoposter.render.pipeline import compose_badged_bytes, deliver
from autoposter.servers.base import CAP_ARTWORK_PROVENANCE, CAP_LOCK_ARTWORK, ServerItemRef

from conftest import seed_media_item

ORACLE = Path("tests/fixtures/oracle")

REF = ServerItemRef("plex", "1", "Movies", "movie")


class FakePart:
    def __init__(self, file=None):
        self.streams = []
        self.file = file


class FakePlexItem:
    def __init__(self, file=None):
        self.media = [type("M", (), {"parts": [FakePart(file)], "videoResolution": "1080",
                                     "audioCodec": "eac3", "audioChannels": 6})()]
        self.duration = 4845912
        self.seasonNumber = None
        self.episodeNumber = None
        self.uploads = 0

    def uploadPoster(self, url=None, filepath=None):
        self.uploads += 1

    def lockPoster(self):
        pass


class Facts:
    critic_rating = 4.9
    audience_rating = 6.3
    content_rating = "17"


class FakeServer:
    """The MediaServer surface ``compose_badged_bytes``/``deliver`` now go
    through, wrapping a ``FakePlexItem`` so the real ``plex.artwork.upload_artwork``
    still runs against it, and answering ``artwork_provenance`` with whatever
    this test configured -- ``deliver`` reads this straight off the
    server/capability/config, so there is no longer a separate ``probe``
    callable to inject."""

    name = "plex"
    capabilities = frozenset({CAP_LOCK_ARTWORK, CAP_ARTWORK_PROVENANCE})

    def __init__(self, item, provenance=None, provenance_error=None):
        self._item = item
        self._provenance = provenance
        self._provenance_error = provenance_error
        self.provenance_calls = []

    async def fetch_item(self, native_id):
        return self._item

    async def upload_artwork(self, ref, data, art_kind, lock):
        await asyncio.to_thread(_plex_upload_artwork, self._item, data, art_kind, lock)

    async def artwork_provenance(self, ref, art_kind):
        self.provenance_calls.append(self._item)
        if self._provenance_error is not None:
            raise self._provenance_error
        return self._provenance


async def _apply(session, config, render, item, server, ref=REF, facts=None):
    """``compose_badged_bytes`` then ``deliver``, in one call -- the shape
    ``apply_badges`` used to be as a single function, restructured for Task
    19's compose/deliver split. Called unconditionally, exactly like
    ``process_item`` calls both halves: a background render, a render that
    never produced a base image, or a library with badges off are all
    ``deliver``'s own no-op checks now (see its docstring), not this
    helper's."""
    data = await compose_badged_bytes(
        session, config, render, item, server=server, ref=ref, facts=facts,
    )
    await deliver(session, config, render, item, {"plex": server}, {"plex": ref}, data)
    return data


async def _render(session, **kw):
    item = await seed_media_item(session, "1", kind="movie", library="Movies", title="X")
    kw.setdefault("status", "rendered")
    kw.setdefault("asset_path", str(ORACLE / "All_Souls_base_no_overlay.jpg"))
    render = Render(item_id=item.id, art_kind="poster", base_sha256="abc", **kw)
    session.add(render)
    await session.flush()
    return item, render


async def test_badging_records_a_fingerprint_and_uploads(session, config_with_badges):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    assert render.badge_fingerprint is not None
    assert render.upload_status == "uploaded"
    assert plex_item.uploads == 1


async def test_an_unchanged_fingerprint_skips_the_upload_entirely(
    session, config_with_badges
):
    """This is what stops the upload bloat seen in production, where repeated
    runs left five accumulated uploads on every item."""
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    first = render.badge_fingerprint
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    assert render.badge_fingerprint == first
    assert plex_item.uploads == 1


async def test_a_changed_rating_re_badges_and_re_uploads(session, config_with_badges):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())

    class Changed(Facts):
        critic_rating = 5.4

    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Changed())
    assert plex_item.uploads == 2


async def test_dry_run_composes_and_fingerprints_but_uploads_nothing(
    session, config_badges_dry_run
):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _apply(session, config_badges_dry_run, render, item, FakeServer(plex_item), facts=Facts())
    assert render.badge_fingerprint is not None
    assert render.upload_status == "skipped"
    assert plex_item.uploads == 0


async def test_disabled_badges_do_nothing_at_all(session, config_badges_disabled):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _apply(session, config_badges_disabled, render, item, FakeServer(plex_item), facts=Facts())
    assert render.badge_fingerprint is None
    assert plex_item.uploads == 0


async def test_a_re_rendered_base_re_badges_even_though_the_source_bytes_match(
    session, config_with_badges
):
    """The gate has to key on the base *fingerprint*, not on base_sha256.

    An episode stored as "TBA" getting its real title re-renders the title card
    from the identical downloaded source: base_sha256 is unchanged, everything
    visible is not. Keying the badge fingerprint on the source sha leaves Plex
    holding the stale badged image permanently -- and silently, because every
    stage reports success.
    """
    item, render = await _render(session, fingerprint="f" * 64)
    plex_item = FakePlexItem()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    assert plex_item.uploads == 1

    render.fingerprint = "e" * 64  # base re-rendered; render.base_sha256 untouched
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    assert plex_item.uploads == 2


async def test_a_changed_rating_still_re_badges_without_touching_the_base(
    session, config_with_badges
):
    """The other half of the property: the base fingerprint is unchanged, only
    the badge values differ, and that must still re-badge and re-upload."""
    item, render = await _render(session, fingerprint="f" * 64)
    plex_item = FakePlexItem()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())

    class Changed(Facts):
        critic_rating = 5.4

    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Changed())
    assert plex_item.uploads == 2
    assert render.fingerprint == "f" * 64


async def test_reading_media_off_plex_is_offloaded_from_the_event_loop(
    session, config_with_badges
):
    """media_info_from_plex() reloads when `.media` is absent, and that reload
    is a blocking requests GET. Shows and seasons never carry `.media`, so on
    the event loop one slow Plex response stalls the whole worker pool."""
    item, render = await _render(session)

    class Reloading(FakePlexItem):
        def __init__(self):
            super().__init__()
            self._loaded, self.media = self.media, []
            self.reload_thread = None

        def reload(self):
            self.reload_thread = threading.get_ident()
            self.media = self._loaded

    plex_item = Reloading()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    assert plex_item.reload_thread is not None, "reload() never ran"
    assert plex_item.reload_thread != threading.get_ident()


async def test_a_render_that_produced_no_file_is_not_badged(session, config_with_badges):
    """asset_path is written when the row is created, before any file exists.
    A no_art render has a path and nothing at it; badging it raised
    FileNotFoundError on every run, forever."""
    item, render = await _render(
        session, status="no_art", asset_path="/nonexistent/never-written.jpg"
    )
    plex_item = FakePlexItem()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    assert render.badge_fingerprint is None
    assert plex_item.uploads == 0


async def test_the_video_format_badge_is_derived_from_the_media_file_path(
    session, config_with_badges, monkeypatch
):
    """Hardcoding None here meant the badge could never be drawn in production,
    however well the composer handled it."""
    from autoposter.render import pipeline

    seen = []
    real = pipeline.compose_badges
    monkeypatch.setattr(
        pipeline, "compose_badges",
        lambda *args, **kwargs: seen.append(args[2]) or real(*args, **kwargs),
    )

    item, render = await _render(session)
    plex_item = FakePlexItem(file="/media/Movies/Dune (2021)/Dune.2021.REMUX-2160p.mkv")
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    assert [i.video_format for i in seen] == ["REMUX"]
    assert seen[0].media.file_paths == ("/media/Movies/Dune (2021)/Dune.2021.REMUX-2160p.mkv",)


async def test_a_successful_upload_survives_a_later_rollback(
    session, config_with_badges
):
    """The badge stage's error handler rolls back. A fingerprint that is only
    flushed is lost with it -- and the image is already on the Plex server, so
    the next pass uploads a byte-for-byte redundant copy."""
    item, render = await _render(session)
    await session.commit()
    render_id = render.id  # rollback expires the instance; the row is the subject

    plex_item = FakePlexItem()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    await session.rollback()

    stored = (
        await session.execute(
            select(Render.badge_fingerprint, Render.upload_status).where(
                Render.id == render_id
            )
        )
    ).one()
    assert stored.badge_fingerprint is not None
    assert stored.upload_status == "uploaded"


async def test_backgrounds_are_never_badged(session, config_with_badges):
    """The tool being replaced overlays posters, season posters and episode
    title cards only -- never fanart backdrops."""
    item = await seed_media_item(session, "2", kind="movie", library="Movies", title="Y")
    render = Render(item_id=item.id, art_kind="background",
                    asset_path=str(ORACLE / "All_Souls_base_no_overlay.jpg"),
                    base_sha256="abc")
    session.add(render)
    await session.flush()

    plex_item = FakePlexItem()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    assert render.badge_fingerprint is None
    assert plex_item.uploads == 0


# --- adopting what Plex is already serving -----------------------------------
#
# Uploaded artwork carries its own fingerprint in EXIF ImageDescription, so the
# artwork -- not the database -- is the source of truth about what is in Plex.
# At cutover every render has a NULL badge_fingerprint; without this the whole
# library re-composes and re-uploads bytes that are already there.


async def _fingerprint_of(session, config, item, render, plex_item):
    """Badge once normally to learn the fingerprint, then reset the row to the
    state adoption (or a database restore) leaves it in.

    The ``render_deliveries`` row this pass just recorded is deleted too --
    ``_already_delivered`` checks THAT for delivery history, not
    ``render.badge_fingerprint`` (``compose_badged_bytes`` overwrites it before
    ``deliver`` ever runs), so a real adoption/restore -- a fresh database
    with no delivery rows at all -- has to be reproduced here the same way.
    """
    from autoposter.db.models import RenderDelivery
    from sqlalchemy import delete as _delete

    await _apply(session, config, render, item, FakeServer(plex_item), facts=Facts())
    fingerprint = render.badge_fingerprint
    render.badge_fingerprint = None
    render.upload_status = "generate"  # the column default; NOT NULL
    plex_item.uploads = 0
    await session.execute(_delete(RenderDelivery).where(RenderDelivery.render_id == render.id))
    await session.commit()
    return fingerprint


async def test_matching_provenance_records_the_fingerprint_and_skips_the_upload(
    session, config_with_badges
):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    fingerprint = await _fingerprint_of(session, config_with_badges, item, render, plex_item)

    server = FakeServer(plex_item, provenance=fingerprint)
    await _apply(session, config_with_badges, render, item, server, facts=Facts())

    assert plex_item.uploads == 0
    assert render.badge_fingerprint == fingerprint
    assert render.upload_status == "uploaded"
    assert server.provenance_calls == [plex_item]
    await session.refresh(render)
    assert render.uploaded_at is not None


async def test_a_stale_fingerprint_in_plex_still_uploads(session, config_with_badges):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _fingerprint_of(session, config_with_badges, item, render, plex_item)

    server = FakeServer(plex_item, provenance="an-older-fingerprint")
    await _apply(session, config_with_badges, render, item, server, facts=Facts())

    assert plex_item.uploads == 1
    assert render.upload_status == "uploaded"


async def test_artwork_nobody_stamped_still_uploads(session, config_with_badges):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _fingerprint_of(session, config_with_badges, item, render, plex_item)

    server = FakeServer(plex_item, provenance=None)
    await _apply(session, config_with_badges, render, item, server, facts=Facts())

    assert plex_item.uploads == 1


async def test_a_failing_probe_falls_through_to_the_normal_upload(
    session, config_with_badges
):
    """Best-effort: reading provenance is an optimisation, never a gate."""
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _fingerprint_of(session, config_with_badges, item, render, plex_item)

    server = FakeServer(
        plex_item, provenance_error=RuntimeError("plex is having a moment"),
    )
    await _apply(session, config_with_badges, render, item, server, facts=Facts())

    assert plex_item.uploads == 1
    assert render.upload_status == "uploaded"


async def test_a_render_we_already_have_a_fingerprint_for_is_never_probed(
    session, config_with_badges
):
    """The stored fingerprint already answers the question for free; asking
    Plex anyway would be one range request per item, every pass."""
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())

    # A changed rating: the fingerprint moves, so this must re-upload without
    # consulting Plex -- render.badge_fingerprint is not NULL.
    class Changed(Facts):
        critic_rating = 7.7

    server = FakeServer(plex_item, provenance="whatever")
    await _apply(session, config_with_badges, render, item, server, facts=Changed())

    assert server.provenance_calls == []
    assert plex_item.uploads == 2


async def test_the_probe_is_not_consulted_when_the_config_disables_it(
    session, config_with_badges
):
    config_with_badges.badges.adopt_from_plex = False
    item, render = await _render(session)
    plex_item = FakePlexItem()
    fingerprint = await _fingerprint_of(session, config_with_badges, item, render, plex_item)

    server = FakeServer(plex_item, provenance=fingerprint)
    await _apply(session, config_with_badges, render, item, server, facts=Facts())

    assert server.provenance_calls == []
    assert plex_item.uploads == 1


async def test_a_dry_run_never_probes_plex(session, config_badges_dry_run):
    """With upload_to_plex off there is no upload to skip, so spending ~16,000
    range requests to learn that would be pure waste."""
    item, render = await _render(session)
    plex_item = FakePlexItem()
    server = FakeServer(plex_item, provenance="anything")

    await _apply(session, config_badges_dry_run, render, item, server, facts=Facts())

    assert server.provenance_calls == []
    assert render.upload_status == "skipped"


# --- roadmap row 99: the badge reads the overlaid facts ---------------------


async def test_the_badge_ignores_an_override_while_the_gate_is_off(
    session, config_with_badges
):
    """Gate off is byte-identical to before this row.

    One item, run twice: once with an override row present and once with it
    gone. With the gate off the two runs must produce the same fingerprint
    and the same single upload -- which is only true if the row had no effect
    at all. (One item rather than two on purpose: ``_render`` hardcodes
    ``rating_key="1"``, and ``media_items.rating_key`` is UNIQUE, so calling
    it twice in one test is an IntegrityError rather than a second item.)

    Note also that the row is still there to be deleted: gate-off IGNORES
    existing overrides, it never removes them, so turning the gate back on
    restores the operator's work rather than finding it gone."""
    from sqlalchemy import delete as _delete

    from autoposter.db.models import ItemMetadataOverride

    item, render = await _render(session)
    session.add(ItemMetadataOverride(
        item_id=item.id, field="critic_rating", value="9.9",
    ))
    await session.commit()
    config_with_badges.operations.item_overrides_enabled = False
    plex_item = FakePlexItem()

    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    with_row = render.badge_fingerprint

    await session.execute(_delete(ItemMetadataOverride))
    await session.commit()
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())

    assert render.badge_fingerprint == with_row
    assert plex_item.uploads == 1


async def test_an_overridden_rating_moves_this_item_s_badge(
    session, config_with_badges
):
    """At the seam that actually reads facts for a badge: Plex
    shows the operator's rating and so does the badge.

    Measured on ONE item across the gate rather than between two items, for
    the ``rating_key`` reason above: badge it with the gate off, then turn the
    gate on with the row already in place. A moved fingerprint is the whole
    claim -- the badge is now built from the operator's 9.9 and not from
    ``Facts.critic_rating``'s 4.9.

    "And nothing else moves" is proven elsewhere and deliberately not
    re-proven here: ``config/loader.py::render_version`` excludes ``operations``
    entirely (pinned in ``tests/test_item_overrides_store.py``), and nothing in
    this row touches ``_definitions_digest``, ``_outcomes_digest`` or
    ``_rating_values_digest`` (pinned by the untouched
    ``tests/test_overlay_entrypoint.py`` literals)."""
    from autoposter.db.models import ItemMetadataOverride

    item, render = await _render(session)
    session.add(ItemMetadataOverride(
        item_id=item.id, field="critic_rating", value="9.9",
    ))
    await session.commit()
    config_with_badges.operations.item_overrides_enabled = False
    plex_item = FakePlexItem()

    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    ungated = render.badge_fingerprint

    config_with_badges.operations.item_overrides_enabled = True
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())

    assert render.badge_fingerprint != ungated
    assert plex_item.uploads == 2


async def test_a_second_pass_over_an_overridden_item_re_badges_nothing(
    session, config_with_badges
):
    """The steady state. The overlay is a function of the stored row, so the
    same override produces the same fingerprint and the upload count does not
    move -- which is what stops the upload bloat this stage exists to avoid."""
    from autoposter.db.models import ItemMetadataOverride

    item, render = await _render(session)
    session.add(ItemMetadataOverride(
        item_id=item.id, field="critic_rating", value="9.9",
    ))
    await session.commit()
    config_with_badges.operations.item_overrides_enabled = True
    plex_item = FakePlexItem()

    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    first = render.badge_fingerprint
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())

    assert render.badge_fingerprint == first
    assert plex_item.uploads == 1


async def test_an_exempt_item_s_badge_ignores_the_override_too(
    session, config_with_badges
):
    """Row 35's exemption gates the Plex
    WRITE (``apply_metadata``, tested elsewhere) and, as of this fix, the
    badge overlay too -- an item Plex will never receive the override for
    must not show it in the badge either, or the two visibly disagree.
    ``ignore_ids`` stands in for all three exemption reasons; the write side
    already shares this exact check (``exemption_reason``), not a duplicate.

    Proven the way the gate-off test above is: with the override row
    present, a run made exempt via ``ignore_ids`` must fingerprint identically
    to a later run with the row gone and the item no longer exempt -- the
    only way that holds is if the exempt run never saw the override."""
    from sqlalchemy import delete as _delete

    from autoposter.db.models import ItemMetadataOverride

    item, render = await _render(session)
    session.add(ItemMetadataOverride(
        item_id=item.id, field="critic_rating", value="9.9",
    ))
    await session.commit()
    config_with_badges.operations.item_overrides_enabled = True
    config_with_badges.operations.ignore_ids = ["1"]
    plex_item = FakePlexItem()

    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())
    exempt = render.badge_fingerprint

    await session.execute(_delete(ItemMetadataOverride))
    await session.commit()
    config_with_badges.operations.ignore_ids = []
    await _apply(session, config_with_badges, render, item, FakeServer(plex_item), facts=Facts())

    assert render.badge_fingerprint == exempt
    assert plex_item.uploads == 1


async def test_the_overlay_never_writes_the_operator_s_value_into_item_facts(
    session, config_with_badges
):
    """Global Constraint 13, proven rather than asserted. ``persist_facts`` is
    the PROVIDER's record; an operator's value in it would be the freezing
    hazard in database form -- the provider's own reading lost, and the row
    no longer tracking the provider."""
    from sqlalchemy import select as _select

    from autoposter.db.models import ItemFacts, ItemMetadataOverride

    item, render = await _render(session)
    # Captured before ``session.expire_all()`` below: an AsyncSession cannot
    # transparently refresh an expired attribute on synchronous access
    # (``MissingGreenlet``), so the post-expire query below reads this rather
    # than ``item.id`` directly.
    item_id = item.id
    session.add(ItemFacts(item_id=item_id, critic_rating=4.9))
    session.add(ItemMetadataOverride(
        item_id=item_id, field="critic_rating", value="9.9",
    ))
    await session.commit()
    config_with_badges.operations.item_overrides_enabled = True

    facts = (
        await session.execute(_select(ItemFacts).where(ItemFacts.item_id == item_id))
    ).scalar_one()
    await _apply(session, config_with_badges, render, item, FakeServer(FakePlexItem()), facts=facts)
    await session.commit()
    session.expire_all()

    stored = (
        await session.execute(_select(ItemFacts).where(ItemFacts.item_id == item_id))
    ).scalar_one()
    assert stored.critic_rating == 4.9


async def test_compose_reports_back_the_fingerprint_it_composed(session, config_with_badges):
    """`out["fingerprint"]` is where `retry_pending_deliveries`
    gets the value it records against the bytes it just uploaded. On the
    ordinary path it is the same fingerprint the column receives -- which is
    what makes it the render's own answer rather than a second, independently
    computed one that could drift from it."""
    item, render = await _render(session)
    out: dict = {}
    data = await compose_badged_bytes(
        session, config_with_badges, render, item,
        server=FakeServer(FakePlexItem()), ref=REF, facts=Facts(), out=out,
    )
    assert data is not None
    assert out["fingerprint"] == render.badge_fingerprint
