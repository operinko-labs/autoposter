"""The badge stage inside the per-item pipeline."""
from pathlib import Path

from autoposter.db.models import MediaItem, Render
from autoposter.render.pipeline import apply_badges

ORACLE = Path("tests/fixtures/oracle")


class FakePlexItem:
    def __init__(self):
        self.media = [type("M", (), {"parts": [], "videoResolution": "1080",
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
    render = Render(item_id=item.id, art_kind="poster",
                    asset_path=str(ORACLE / "All_Souls_base_no_overlay.jpg"),
                    base_sha256="abc", **kw)
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
