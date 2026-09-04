"""The badge stage inside the per-item pipeline."""
import threading
from pathlib import Path

from sqlalchemy import select

from autoposter.db.models import MediaItem, Render
from autoposter.render.pipeline import apply_badges

ORACLE = Path("tests/fixtures/oracle")


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


async def _render(session, **kw):
    item = MediaItem(kind="movie", rating_key="1", library="Movies", title="X")
    session.add(item)
    await session.flush()
    kw.setdefault("status", "rendered")
    kw.setdefault("asset_path", str(ORACLE / "All_Souls_base_no_overlay.jpg"))
    render = Render(item_id=item.id, art_kind="poster", base_sha256="abc", **kw)
    session.add(render)
    await session.flush()
    return item, render


async def test_badging_records_a_fingerprint_and_uploads(session, config_with_badges):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
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
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
    first = render.badge_fingerprint
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
    assert render.badge_fingerprint == first
    assert plex_item.uploads == 1


async def test_a_changed_rating_re_badges_and_re_uploads(session, config_with_badges):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())

    class Changed(Facts):
        critic_rating = 5.4

    await apply_badges(session, config_with_badges, render, item, plex_item, Changed())
    assert plex_item.uploads == 2


async def test_dry_run_composes_and_fingerprints_but_uploads_nothing(
    session, config_badges_dry_run
):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await apply_badges(session, config_badges_dry_run, render, item, plex_item, Facts())
    assert render.badge_fingerprint is not None
    assert render.upload_status == "skipped"
    assert plex_item.uploads == 0


async def test_disabled_badges_do_nothing_at_all(session, config_badges_disabled):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await apply_badges(session, config_badges_disabled, render, item, plex_item, Facts())
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
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
    assert plex_item.uploads == 1

    render.fingerprint = "e" * 64  # base re-rendered; render.base_sha256 untouched
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
    assert plex_item.uploads == 2


async def test_a_changed_rating_still_re_badges_without_touching_the_base(
    session, config_with_badges
):
    """The other half of the property: the base fingerprint is unchanged, only
    the badge values differ, and that must still re-badge and re-upload."""
    item, render = await _render(session, fingerprint="f" * 64)
    plex_item = FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())

    class Changed(Facts):
        critic_rating = 5.4

    await apply_badges(session, config_with_badges, render, item, plex_item, Changed())
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
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
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
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
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
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
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
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
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
    item = MediaItem(kind="movie", rating_key="2", library="Movies", title="Y")
    session.add(item)
    await session.flush()
    render = Render(item_id=item.id, art_kind="background",
                    asset_path=str(ORACLE / "All_Souls_base_no_overlay.jpg"),
                    base_sha256="abc")
    session.add(render)
    await session.flush()

    plex_item = FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
    assert render.badge_fingerprint is None
    assert plex_item.uploads == 0


# --- adopting what Plex is already serving -----------------------------------
#
# Uploaded artwork carries its own fingerprint in EXIF ImageDescription, so the
# artwork -- not the database -- is the source of truth about what is in Plex.
# At cutover every render has a NULL badge_fingerprint; without this the whole
# library re-composes and re-uploads bytes that are already there.


def _probe_returning(value, calls):
    async def probe(plex_item):
        calls.append(plex_item)
        return value

    return probe


async def _fingerprint_of(session, config, item, render, plex_item):
    """Badge once normally to learn the fingerprint, then reset the row to the
    state adoption (or a database restore) leaves it in."""
    await apply_badges(session, config, render, item, plex_item, Facts())
    fingerprint = render.badge_fingerprint
    render.badge_fingerprint = None
    render.upload_status = "generate"  # the column default; NOT NULL
    plex_item.uploads = 0
    await session.commit()
    return fingerprint


async def test_matching_provenance_records_the_fingerprint_and_skips_the_upload(
    session, config_with_badges
):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    fingerprint = await _fingerprint_of(session, config_with_badges, item, render, plex_item)
    calls = []

    await apply_badges(
        session, config_with_badges, render, item, plex_item, Facts(),
        probe=_probe_returning(fingerprint, calls),
    )

    assert plex_item.uploads == 0
    assert render.badge_fingerprint == fingerprint
    assert render.upload_status == "uploaded"
    assert calls == [plex_item]
    await session.refresh(render)
    assert render.uploaded_at is not None


async def test_a_stale_fingerprint_in_plex_still_uploads(session, config_with_badges):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _fingerprint_of(session, config_with_badges, item, render, plex_item)

    await apply_badges(
        session, config_with_badges, render, item, plex_item, Facts(),
        probe=_probe_returning("an-older-fingerprint", []),
    )

    assert plex_item.uploads == 1
    assert render.upload_status == "uploaded"


async def test_artwork_nobody_stamped_still_uploads(session, config_with_badges):
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _fingerprint_of(session, config_with_badges, item, render, plex_item)

    await apply_badges(
        session, config_with_badges, render, item, plex_item, Facts(),
        probe=_probe_returning(None, []),
    )

    assert plex_item.uploads == 1


async def test_a_failing_probe_falls_through_to_the_normal_upload(
    session, config_with_badges
):
    """Best-effort: reading provenance is an optimisation, never a gate."""
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await _fingerprint_of(session, config_with_badges, item, render, plex_item)

    async def exploding(_plex_item):
        raise RuntimeError("plex is having a moment")

    await apply_badges(
        session, config_with_badges, render, item, plex_item, Facts(), probe=exploding
    )

    assert plex_item.uploads == 1
    assert render.upload_status == "uploaded"


async def test_a_render_we_already_have_a_fingerprint_for_is_never_probed(
    session, config_with_badges
):
    """The stored fingerprint already answers the question for free; asking
    Plex anyway would be one range request per item, every pass."""
    item, render = await _render(session)
    plex_item = FakePlexItem()
    await apply_badges(session, config_with_badges, render, item, plex_item, Facts())
    calls = []

    # A changed rating: the fingerprint moves, so this must re-upload without
    # consulting Plex -- render.badge_fingerprint is not NULL.
    class Changed(Facts):
        critic_rating = 7.7

    await apply_badges(
        session, config_with_badges, render, item, plex_item, Changed(),
        probe=_probe_returning("whatever", calls),
    )

    assert calls == []
    assert plex_item.uploads == 2


async def test_the_probe_is_not_consulted_when_the_config_disables_it(
    session, config_with_badges
):
    config_with_badges.badges.adopt_from_plex = False
    item, render = await _render(session)
    plex_item = FakePlexItem()
    fingerprint = await _fingerprint_of(session, config_with_badges, item, render, plex_item)
    calls = []

    await apply_badges(
        session, config_with_badges, render, item, plex_item, Facts(),
        probe=_probe_returning(fingerprint, calls),
    )

    assert calls == []
    assert plex_item.uploads == 1


async def test_a_dry_run_never_probes_plex(session, config_badges_dry_run):
    """With upload_to_plex off there is no upload to skip, so spending ~16,000
    range requests to learn that would be pure waste."""
    item, render = await _render(session)
    plex_item = FakePlexItem()
    calls = []

    await apply_badges(
        session, config_badges_dry_run, render, item, plex_item, Facts(),
        probe=_probe_returning("anything", calls),
    )

    assert calls == []
    assert render.upload_status == "skipped"
