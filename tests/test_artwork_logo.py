"""LogoMode and LogoRevertMode: put a clearlogo on Plex, and take ours back off.

Fake Plex throughout -- a ``FakeItem`` carrying the clearlogo half of the
plexapi surface (``logo``/``uploadLogo``/``lockLogo``/``logos``/``unlockLogo``/
``deleteLogo``) plus a ``refresh`` that exists only to prove nothing calls it.
That surface is pinned against the real library in
``tests/test_plexapi_logo_contract.py``; here it is a spy.

The provider ladder is the real one (``providers/ladder.py``) over a fake
provider, because "which logo would we upload" is a real decision; the download
is a ``MockTransport``.

The marker under test is ``media_items.logo_upload_key``: the ``upload://``
rating key Plex filed OUR upload under. The revert acts on an item only when
that column is set *and* Plex still has that exact key selected -- so a logo an
operator set by hand (no marker) and a logo an operator replaced ours with
(marker present, different key selected) are both left alone.
"""
import logging
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from plexapi.exceptions import NotFound as PlexNotFound
from sqlalchemy import select

from conftest import decodable_png

from autoposter.artwork_modes.logo import LogoMode, LogoRevertMode
from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem
from autoposter.providers.base import LOGO, ArtCandidate

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PLEX_URL = "http://plex.local"
PLEX_TOKEN = "plex-token"

LOGO_URL = "https://provider.example/logo.png"
SVG_LOGO_URL = "https://provider.example/logo.svg"
# A genuinely decodable PNG, not a placeholder: the updater now decodes every
# body it keeps (``render/artwork_fetch._validate_image``), so a placeholder
# would send every test in this file down the refusal path instead of the
# behaviour it is about. Shared from conftest so the suites that fake a
# provider CDN cannot drift about what "an image" is.
LOGO_BYTES = decodable_png()

# What Plex keys an upload under -- ours and an operator's alike, which is
# exactly why the marker records *which* upload key was ours.
OUR_KEY = "upload://clearLogos/ours-7f3c9a"
THEIR_KEY = "upload://clearLogos/theirs-1b2c3d"
AGENT_KEY = "metadata://clearLogos/tmdb_12345"


class FakeLogo:
    """One entry of a plexapi ``logos()`` listing."""

    def __init__(self, rating_key, selected=False):
        self.ratingKey = rating_key  # noqa: N815 - plexapi name
        self.selected = selected


class FakeItem:
    """The plexapi clearlogo surface the two modes touch.

    ``logo`` is the path Plex serves the current clearlogo from, or ``None`` for
    an item that has none -- the updater's whole skip decision. ``uploadLogo``
    records the bytes and behaves the way Plex does: the upload becomes the
    selected entry of the listing, which is where the revert marker comes from.
    """

    def __init__(self, logo=None, logos=(), upload_key=OUR_KEY, pause=None):
        self.logo = logo
        self._logos = [FakeLogo(key, selected) for key, selected in logos]
        self._upload_key = upload_key
        self._pause = pause
        self.uploaded = []
        self.locked = []
        self.unlocked = []
        self.deleted = 0
        self.paused_during_write = []
        self.refreshed = False

    def refresh(self):
        self.refreshed = True

    def _note_pause(self):
        if self._pause is not None:
            self.paused_during_write.append(self._pause.is_paused)

    def uploadLogo(self, url=None, filepath=None):  # noqa: N802 - plexapi name
        self._note_pause()
        with open(filepath, "rb") as handle:
            self.uploaded.append(handle.read())
        self.logo = "/library/metadata/1/clearLogo/1"
        for entry in self._logos:
            entry.selected = False
        if self._upload_key is not None:
            self._logos.append(FakeLogo(self._upload_key, selected=True))

    def lockLogo(self):  # noqa: N802 - plexapi name
        self.locked.append("logo")

    def logos(self):
        return list(self._logos)

    def unlockLogo(self):  # noqa: N802 - plexapi name
        self._note_pause()
        self.unlocked.append("logo")

    def deleteLogo(self):  # noqa: N802 - plexapi name
        self.deleted += 1
        self.logo = None


class FakePlexClient:
    def __init__(self, items=None):
        self._items = items or {}
        self.fetched = []

    async def fetch_item(self, rating_key):
        self.fetched.append(rating_key)
        return self._items[rating_key]


class FakeProvider:
    """One rung of the ladder, answering only LOGO requests."""

    name = "Fake"

    def __init__(self, url=LOGO_URL, language="en"):
        self._url = url
        self._language = language
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if request.art_kind != LOGO or self._url is None:
            return []
        return [
            ArtCandidate(
                provider=self.name, url=self._url, language=self._language,
                width=800, height=310, score=8.0,
            )
        ]


@pytest_asyncio.fixture
async def serving():
    """A helper that installs a ``{url path: bytes}`` map and returns a client."""
    clients = []

    def install(by_path=None):
        by_path = by_path or {"/logo.png": LOGO_BYTES, "/logo.svg": b"<svg/>"}

        def handler(request):
            data = by_path.get(request.url.path)
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, content=data)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(http)
        return http

    yield install
    for http in clients:
        await http.aclose()


@pytest.fixture
def config():
    cfg = load_config(EXAMPLE)
    cfg.plex.url = PLEX_URL
    return cfg


def _headers():
    return {"X-Plex-Token": PLEX_TOKEN}


async def _add_item(session, *, rating_key, kind="movie", library="Movies",
                    logo_upload_key=None, tmdb_id=101):
    item = MediaItem(
        rating_key=rating_key, library=library, kind=kind, title="A",
        root_folder="A (1999)", tmdb_id=tmdb_id, logo_upload_key=logo_upload_key,
    )
    session.add(item)
    await session.commit()
    return item


async def _marker(session, item_id):
    return (
        await session.execute(
            select(MediaItem.logo_upload_key).where(MediaItem.id == item_id)
        )
    ).scalar_one()


# --- the updater (roadmap row 71) --------------------------------------------


async def test_updater_uploads_a_logo_and_records_the_marker(session, config, serving):
    """The whole write path: an item Plex has no clearlogo for gets the ladder's
    best logo uploaded and locked, and the ``upload://`` key Plex filed it under
    is recorded so the revert can find it again."""
    row = await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None)
    plex = FakePlexClient({"rk1": item})
    provider = FakeProvider()

    result = await LogoMode(
        config, plex, serving(), _headers(), [provider], apply=True
    ).run(session)

    assert (result.items, result.items_missing_logo) == (1, 1)
    assert (result.uploaded, result.failed) == (1, 0)
    assert item.uploaded == [LOGO_BYTES]
    # Locked, or Plex's agent reclaims the field -- the same reason
    # ``upload_artwork`` locks the poster.
    assert item.locked == ["logo"]
    assert await _marker(session, row.id) == OUR_KEY
    response = result.as_response()
    assert response["mode"] == "logo" and response["status"] == "updated"
    assert response["uploaded"] == 1


async def test_updater_asks_the_ladder_for_a_logo_for_this_item(session, config, serving):
    """The request handed to the ladder is a LOGO request carrying the item's own
    ids -- not the poster request the render pipeline makes."""
    await _add_item(session, rating_key="rk1", tmdb_id=555)
    plex = FakePlexClient({"rk1": FakeItem(logo=None)})
    provider = FakeProvider()

    await LogoMode(config, plex, serving(), _headers(), [provider], apply=True).run(session)

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.art_kind == LOGO
    assert request.is_movie is True
    assert request.tmdb_id == 555


async def test_updater_skips_an_item_that_already_has_a_logo(session, config, serving):
    """The has-logo mutation-proof. Plex already shows a clearlogo for this item,
    so the updater must leave it alone -- it is an operator's or an agent's, and
    "fill in the missing ones" is not "overwrite every one". Drop the
    ``has_clearlogo`` skip in ``LogoMode.run`` and this item becomes a candidate:
    ``items_missing_logo`` goes to 1 and a second logo is uploaded over the
    existing one, reddening every assertion below.
    """
    row = await _add_item(session, rating_key="rk1")
    item = FakeItem(logo="/library/metadata/1/clearLogo/1", logos=((AGENT_KEY, True),))
    plex = FakePlexClient({"rk1": item})
    provider = FakeProvider()

    result = await LogoMode(
        config, plex, serving(), _headers(), [provider], apply=True
    ).run(session)

    assert (result.items, result.items_missing_logo) == (1, 0)
    assert (result.uploaded, result.failed) == (0, 0)
    assert item.uploaded == []
    assert provider.requests == []  # not even a provider call was spent
    assert await _marker(session, row.id) is None


async def test_updater_logs_a_missing_item_at_info_not_warning(
    session, config, serving, caplog
):
    """A stale rating_key 404s against Plex as plexapi's NotFound -- an
    expected probe failure (the item was deleted/moved in Plex), not a crash
    worth a traceback. Row 218's upgrade: logo.py already special-cased
    NotFound here, but at WARNING and with no tally -- this brings it onto
    backup.py's PR #112 hotfix shape (INFO, tallied, id-only, no traceback),
    the same shape the other four sites in this row now share.

    NotFound's message embeds the server URL, so the log line must never
    carry str(exc) -- only the rating key."""

    class GoneClient(FakePlexClient):
        async def fetch_item(self, rating_key):
            self.fetched.append(rating_key)
            raise PlexNotFound(
                f"(404) not_found ({rating_key}) http://plex.local:32400/library/metadata/{rating_key}"
            )

    await _add_item(session, rating_key="rk-gone")
    plex = GoneClient({})

    with caplog.at_level(logging.INFO):
        result = await LogoMode(
            config, plex, serving(), _headers(), [FakeProvider()], apply=True
        ).run(session)

    assert (result.items, result.items_missing_logo, result.missing) == (1, 0, 1)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == []
    assert not any(r.exc_info for r in caplog.records)

    logo_records = [r for r in caplog.records if r.name == "autoposter.artwork_modes.logo"]
    per_item = [r.message for r in logo_records if "rk-gone" in r.message]
    assert len(per_item) == 1
    assert per_item[0].startswith("logo: ") and "no longer in Plex" in per_item[0]
    assert "plex.local" not in per_item[0]  # never the URL-bearing str(exc)

    summary = [r.message for r in logo_records if r.message not in per_item]
    assert len(summary) == 1
    assert summary[0].startswith("logo: ") and "1" in summary[0]


async def test_updater_counts_a_probe_failure_in_the_dry_run_body(
    session, config, serving, caplog
):
    """The operationally dangerous one. A probe that raises for an item drops it
    from the candidate set, so a dry run against an unreachable Plex reports
    ``items_missing_logo: 0`` -- "nothing to do" -- when the truth is "nothing
    could be asked". ``probe_failed`` is therefore in the DRY-RUN body, not just
    the applied one: it is the number that tells the two apart.

    Delete the ``probe_failed += 1`` in ``LogoMode.run``'s ``except Exception``
    and this reds on the count while every other counter stays exactly where it
    is -- which is the mutation this test exists to catch.
    """

    class HalfBrokenClient(FakePlexClient):
        async def fetch_item(self, rating_key):
            self.fetched.append(rating_key)
            if rating_key == "rk-bad":
                raise RuntimeError("connection reset by peer")
            return self._items[rating_key]

    await _add_item(session, rating_key="rk-good", tmdb_id=1)
    await _add_item(session, rating_key="rk-bad", tmdb_id=2)
    plex = HalfBrokenClient({"rk-good": FakeItem(logo=None)})

    with caplog.at_level(logging.INFO):
        result = await LogoMode(
            config, plex, serving(), _headers(), [FakeProvider()], apply=False
        ).run(session)

    assert (result.items, result.items_missing_logo) == (2, 1)
    assert (result.probe_failed, result.missing) == (1, 0)

    response = result.as_response()
    assert response["status"] == "dry run"
    assert response["probe_failed"] == 1
    # Still a dry run: the applied-only counts stay out of the body.
    assert "uploaded" not in response

    logo_records = [r for r in caplog.records if r.name == "autoposter.artwork_modes.logo"]
    per_item = [r for r in logo_records if "rk-bad" in r.getMessage()]
    assert len(per_item) == 1 and per_item[0].levelname == "WARNING"
    # Row 213: the rating key and a traceback, never the exception's own text.
    assert "connection reset" not in per_item[0].getMessage()
    assert per_item[0].exc_info is not None

    summary = [
        r.getMessage() for r in logo_records
        if r.levelname == "INFO" and "rk-bad" not in r.getMessage()
    ]
    assert summary == ["logo: could not probe 1 item(s)"]


async def test_updater_dry_run_uploads_nothing(session, config, serving):
    """Dry run is the default posture: it reports how many items are missing a
    logo and never calls a provider, let alone Plex."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None)
    plex = FakePlexClient({"rk1": item})
    provider = FakeProvider()

    result = await LogoMode(
        config, plex, serving(), _headers(), [provider], apply=False
    ).run(session)

    assert result.dry_run is True
    assert (result.items, result.items_missing_logo) == (1, 1)
    assert item.uploaded == [] and provider.requests == []
    response = result.as_response()
    assert response["status"] == "dry run"
    assert "uploaded" not in response


async def test_updater_counts_an_item_with_no_logo_on_any_provider_as_failed(
    session, config, serving
):
    """Nothing on the ladder: the item still has no logo, so nothing changed and
    no marker may be recorded."""
    row = await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None)
    plex = FakePlexClient({"rk1": item})

    result = await LogoMode(
        config, plex, serving(), _headers(), [FakeProvider(url=None)], apply=True
    ).run(session)

    assert (result.uploaded, result.failed) == (0, 1)
    assert item.uploaded == []
    assert await _marker(session, row.id) is None


async def test_updater_refuses_to_upload_an_svg_logo(session, config, serving):
    """The render pipeline can take an SVG logo because ImageMagick rasterises it
    while compositing. Plex's clearLogo field takes a raster image, so an SVG
    pick is skipped rather than pushed -- counted as failed, because the item
    still has no logo."""
    row = await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None)
    plex = FakePlexClient({"rk1": item})

    result = await LogoMode(
        config, plex, serving(), _headers(), [FakeProvider(url=SVG_LOGO_URL)], apply=True
    ).run(session)

    assert (result.uploaded, result.failed) == (0, 1)
    assert item.uploaded == []
    assert await _marker(session, row.id) is None


async def test_updater_isolates_one_failing_item(session, config, serving):
    """A download that 404s costs that item its line in the tally, not the run."""
    good = await _add_item(session, rating_key="rk-good", tmdb_id=1)
    bad = await _add_item(session, rating_key="rk-bad", tmdb_id=2)
    good_item, bad_item = FakeItem(logo=None), FakeItem(logo=None)
    plex = FakePlexClient({"rk-good": good_item, "rk-bad": bad_item})

    class PerItemProvider(FakeProvider):
        async def fetch(self, request):
            self.requests.append(request)
            url = LOGO_URL if request.tmdb_id == 1 else "https://provider.example/gone.png"
            return [ArtCandidate(
                provider=self.name, url=url, language="en",
                width=800, height=310, score=8.0,
            )]

    result = await LogoMode(
        config, plex, serving(), _headers(), [PerItemProvider()], apply=True
    ).run(session)

    assert (result.uploaded, result.failed) == (1, 1)
    assert good_item.uploaded == [LOGO_BYTES] and bad_item.uploaded == []
    assert await _marker(session, good.id) == OUR_KEY
    assert await _marker(session, bad.id) is None


async def test_updater_commits_each_marker_before_the_next_upload(session, config, serving):
    """The interruption proof. The Plex side of the loop is immediate -- item
    one's logo is on the server and locked before item two is even fetched --
    so item one's marker has to be committed before item two runs, not banked
    until the end of the run.

    Here item two's upload dies with a ``BaseException`` (task cancellation,
    a worker torn down mid-request), which escapes the per-item ``except
    Exception`` and unwinds ``_run_plex_writing_mode``'s session scope -- the
    rollback below. Bank the markers in one ``session.commit()`` after the loop
    instead and item one's marker dies with it, leaving a locked logo on Plex
    that the revert can never claim and a re-run can never re-mark (Plex now
    reports a clearlogo for it, so it is not a candidate).
    """

    class Interrupted(BaseException):
        """Deliberately not an ``Exception``: this is not a per-item failure."""

    class DyingItem(FakeItem):
        def uploadLogo(self, url=None, filepath=None):  # noqa: N802 - plexapi name
            raise Interrupted("the container went away")

    # Read out of the ORM object before the rollback below expires it.
    first_id = (await _add_item(session, rating_key="rk1", tmdb_id=1)).id
    await _add_item(session, rating_key="rk2", tmdb_id=2)
    first, second = FakeItem(logo=None), DyingItem(logo=None)
    plex = FakePlexClient({"rk1": first, "rk2": second})

    with pytest.raises(Interrupted):
        await LogoMode(
            config, plex, serving(), _headers(), [FakeProvider()], apply=True
        ).run(session)
    await session.rollback()  # what the unwinding session scope does

    assert first.uploaded == [LOGO_BYTES]  # item one's logo really is on Plex
    assert await _marker(session, first_id) == OUR_KEY  # ...and findable
    assert second.uploaded == []


async def test_updater_survives_a_failed_marker_write(session, config, serving):
    """A database error on one item's marker is that item's own: the session is
    rolled back so the next item's write starts clean, and the run carries on.
    The item still counts as uploaded, because its logo is on Plex -- the same
    outcome as an upload Plex reported no key for."""

    class OneBadCommit:
        """The real session, with the first marker commit blowing up.

        Identified as "the first commit once a logo has actually landed on
        Plex", not by ordinal: the mode also ends its read transaction with a
        commit before the upload phase begins, and breaking *that* would be a
        different test entirely (a run that never starts, rather than a run
        that loses one item's marker)."""

        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.commits = 0
            self.rollbacks = 0
            self.broke = False

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        async def commit(self):
            self.commits += 1
            if first.uploaded and not self.broke:
                self.broke = True
                raise RuntimeError("the database went away")
            await self._wrapped.commit()

        async def rollback(self):
            self.rollbacks += 1
            await self._wrapped.rollback()

    # Read out of the ORM objects before the mode's rollback expires them.
    first_id = (await _add_item(session, rating_key="rk1", tmdb_id=1)).id
    second_id = (await _add_item(session, rating_key="rk2", tmdb_id=2)).id
    first, second = FakeItem(logo=None), FakeItem(logo=None)
    plex = FakePlexClient({"rk1": first, "rk2": second})

    result = await LogoMode(
        config, plex, serving(), _headers(), [FakeProvider()], apply=True
    ).run(OneBadCommit(session))

    assert (result.uploaded, result.failed) == (2, 0)
    assert first.uploaded == [LOGO_BYTES] and second.uploaded == [LOGO_BYTES]
    assert await _marker(session, first_id) is None
    assert await _marker(session, second_id) == OUR_KEY


async def test_updater_records_no_marker_when_plex_reports_no_upload_key(
    session, config, serving
):
    """The logo is on the item -- that half succeeded and counts -- but Plex did
    not report an ``upload://`` entry to key it by, so there is no marker to
    record. The revert then leaves the item alone, which is the safe direction:
    it would rather miss one of ours than clear one of theirs."""
    row = await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None, upload_key=None)
    plex = FakePlexClient({"rk1": item})

    result = await LogoMode(
        config, plex, serving(), _headers(), [FakeProvider()], apply=True
    ).run(session)

    assert (result.uploaded, result.failed) == (1, 0)
    assert item.uploaded == [LOGO_BYTES]
    assert await _marker(session, row.id) is None


async def test_updater_only_considers_movies_and_shows(session, config, serving):
    """A clearlogo is a movie's or a show's, the same restriction
    ``api/candidates.py`` makes. An episode's LOGO request would answer with its
    show's logo, and putting that on the episode is a defect, not a feature -- so
    seasons and episodes are not candidates at all, cap denominator included."""
    await _add_item(session, rating_key="rk-m", kind="movie")
    await _add_item(session, rating_key="rk-e", kind="episode", library="TV Shows")
    movie, episode = FakeItem(logo=None), FakeItem(logo=None)
    plex = FakePlexClient({"rk-m": movie, "rk-e": episode})

    result = await LogoMode(
        config, plex, serving(), _headers(), [FakeProvider()], apply=True
    ).run(session)

    assert (result.items, result.items_missing_logo) == (1, 1)
    assert movie.uploaded == [LOGO_BYTES]
    assert episode.uploaded == []
    assert plex.fetched.count("rk-e") == 0


async def test_updater_filters_by_library_type_and_item(session, config, serving):
    await _add_item(session, rating_key="rk-m", kind="movie", library="Movies")
    show = await _add_item(session, rating_key="rk-t", kind="show", library="TV Shows")
    movie, tv = FakeItem(logo=None), FakeItem(logo=None)
    plex = FakePlexClient({"rk-m": movie, "rk-t": tv})

    result = await LogoMode(
        config, plex, serving(), _headers(), [FakeProvider()], apply=True,
        kind="show", library="TV Shows", item_id=show.id,
    ).run(session)

    assert result.items == 1
    assert tv.uploaded == [LOGO_BYTES] and movie.uploaded == []


async def test_updater_respects_the_cap(session, config, serving):
    """Two items would change but the absolute cap is 1, so it refuses with the
    real numbers and uploads nothing even though apply is true."""
    config.artwork_modes.max_changes = 1
    await _add_item(session, rating_key="rk1", tmdb_id=1)
    await _add_item(session, rating_key="rk2", tmdb_id=2)
    first, second = FakeItem(logo=None), FakeItem(logo=None)
    plex = FakePlexClient({"rk1": first, "rk2": second})

    result = await LogoMode(
        config, plex, serving(), _headers(), [FakeProvider()], apply=True
    ).run(session)

    assert result.refused is not None and "2 of 2" in result.refused
    assert first.uploaded == [] and second.uploaded == []
    response = result.as_response()
    assert response["status"] == "refused"
    assert response["dry_run"] is False  # apply=True was requested
    assert (response["items"], response["items_missing_logo"]) == (2, 2)


async def test_updater_refuses_an_empty_table(session, config, serving):
    result = await LogoMode(
        config, FakePlexClient({}), serving(), _headers(), [], apply=True
    ).run(session)

    assert result.refused is not None and "media_items" in result.refused
    assert result.as_response() == {
        "mode": "logo", "status": "refused", "reason": result.refused,
        "dry_run": False, "items": 0, "items_missing_logo": 0, "missing": 0,
        "probe_failed": 0,
    }


async def test_updater_never_refreshes_the_plex_object(session, config, serving):
    """The behavioural companion to the project-wide no-.refresh() AST guard: a
    refresh would have Plex re-pull from its agents and undo the lock."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None)
    plex = FakePlexClient({"rk1": item})

    await LogoMode(
        config, plex, serving(), _headers(), [FakeProvider()], apply=True
    ).run(session)

    assert item.uploaded == [LOGO_BYTES]  # the write half actually ran
    assert item.refreshed is False


# --- the revert (roadmap row 67) ---------------------------------------------


async def test_revert_clears_a_logo_this_service_set(session, config, serving):
    """The marked item's selected logo is still the exact upload we recorded, so
    the field is unlocked and the logo removed."""
    row = await _add_item(session, rating_key="rk1", logo_upload_key=OUR_KEY)
    item = FakeItem(logo="/clearLogo", logos=((AGENT_KEY, False), (OUR_KEY, True)))
    plex = FakePlexClient({"rk1": item})

    result = await LogoRevertMode(
        config, plex, serving(), _headers(), apply=True
    ).run(session)

    assert (result.items, result.items_with_our_logo) == (1, 1)
    assert (result.cleared, result.failed) == (1, 0)
    assert item.unlocked == ["logo"] and item.deleted == 1
    # The marker goes with the logo: there is nothing of ours left to find.
    assert await _marker(session, row.id) is None
    response = result.as_response()
    assert response["mode"] == "logo_revert" and response["status"] == "reverted"
    assert response["cleared"] == 1


async def test_revert_leaves_a_hand_set_logo_alone(session, config, serving):
    """The marker mutation-proof, for the half of it the database owns: an item
    this service never set a logo on has no ``logo_upload_key``, so it is not
    ours to remove -- whether an operator uploaded its logo (keyed ``upload://``
    exactly as ours is) or Plex's own agent supplied it.

    Drop the ``logo_upload_key IS NOT NULL`` restriction in
    ``LogoRevertMode.run`` and the agent-logo item becomes a candidate: its
    ``selected_uploaded_logo_key`` is None because no upload is selected, its
    unset marker is None too, and the equality that is meant to prove "this is
    still the exact logo we uploaded" matches None against None. Its logo is
    then unlocked and deleted, reddening ``items_with_our_logo == 1`` and both
    untouched-item assertions.
    """
    await _add_item(session, rating_key="rk-ours", logo_upload_key=OUR_KEY)
    await _add_item(session, rating_key="rk-theirs", tmdb_id=2)
    await _add_item(session, rating_key="rk-agent", tmdb_id=3)
    ours = FakeItem(logo="/ours", logos=((OUR_KEY, True),))
    theirs = FakeItem(logo="/theirs", logos=((THEIR_KEY, True),))
    agent = FakeItem(logo="/agent", logos=((AGENT_KEY, True),))
    plex = FakePlexClient({"rk-ours": ours, "rk-theirs": theirs, "rk-agent": agent})

    result = await LogoRevertMode(
        config, plex, serving(), _headers(), apply=True
    ).run(session)

    assert (result.items, result.items_with_our_logo) == (3, 1)
    assert ours.unlocked == ["logo"] and ours.deleted == 1
    assert theirs.unlocked == [] and theirs.deleted == 0
    assert agent.unlocked == [] and agent.deleted == 0


async def test_revert_leaves_a_logo_an_operator_replaced_ours_with_alone(
    session, config, serving
):
    """The marker mutation-proof, for the half Plex owns. The marker says this
    service set a logo here once, but Plex has a *different* upload selected
    now -- an operator changed it after we ran. That choice is theirs, so the
    mode leaves it: a stale marker is not enough on its own to make an item a
    candidate.

    Weaken ``selected == row.logo_upload_key`` in ``LogoRevertMode.run`` to
    ``selected is not None`` -- "any uploaded logo is ours" -- and this item is
    unlocked and deleted, reddening every assertion below.
    """
    await _add_item(session, rating_key="rk1", logo_upload_key=OUR_KEY)
    item = FakeItem(logo="/theirs", logos=((OUR_KEY, False), (THEIR_KEY, True)))
    plex = FakePlexClient({"rk1": item})

    result = await LogoRevertMode(
        config, plex, serving(), _headers(), apply=True
    ).run(session)

    assert (result.items, result.items_with_our_logo) == (1, 0)
    assert item.unlocked == [] and item.deleted == 0


async def test_revert_leaves_an_item_showing_agent_art_alone(session, config, serving):
    """Marked, but what is selected now is Plex's own agent logo rather than any
    upload -- the field already went back on its own."""
    await _add_item(session, rating_key="rk1", logo_upload_key=OUR_KEY)
    item = FakeItem(logo="/agent", logos=((AGENT_KEY, True), (OUR_KEY, False)))
    plex = FakePlexClient({"rk1": item})

    result = await LogoRevertMode(
        config, plex, serving(), _headers(), apply=True
    ).run(session)

    assert result.items_with_our_logo == 0
    assert item.unlocked == [] and item.deleted == 0


async def test_revert_dry_run_changes_nothing(session, config, serving):
    await _add_item(session, rating_key="rk1", logo_upload_key=OUR_KEY)
    item = FakeItem(logo="/ours", logos=((OUR_KEY, True),))
    plex = FakePlexClient({"rk1": item})

    result = await LogoRevertMode(
        config, plex, serving(), _headers(), apply=False
    ).run(session)

    assert result.dry_run is True
    assert (result.items, result.items_with_our_logo) == (1, 1)
    assert item.unlocked == [] and item.deleted == 0
    response = result.as_response()
    assert response["status"] == "dry run"
    assert "cleared" not in response


async def test_revert_filters_by_library(session, config, serving):
    await _add_item(session, rating_key="rk-m", library="Movies", logo_upload_key=OUR_KEY)
    await _add_item(
        session, rating_key="rk-t", kind="show", library="TV Shows",
        logo_upload_key=OUR_KEY, tmdb_id=2,
    )
    movie = FakeItem(logo="/m", logos=((OUR_KEY, True),))
    tv = FakeItem(logo="/t", logos=((OUR_KEY, True),))
    plex = FakePlexClient({"rk-m": movie, "rk-t": tv})

    result = await LogoRevertMode(
        config, plex, serving(), _headers(), apply=True, library="Movies"
    ).run(session)

    assert result.items == 1
    assert movie.deleted == 1 and tv.deleted == 0


async def test_revert_respects_the_cap(session, config, serving):
    config.artwork_modes.max_changes = 1
    await _add_item(session, rating_key="rk1", logo_upload_key=OUR_KEY)
    await _add_item(session, rating_key="rk2", logo_upload_key=OUR_KEY, tmdb_id=2)
    first = FakeItem(logo="/one", logos=((OUR_KEY, True),))
    second = FakeItem(logo="/two", logos=((OUR_KEY, True),))
    plex = FakePlexClient({"rk1": first, "rk2": second})

    result = await LogoRevertMode(
        config, plex, serving(), _headers(), apply=True
    ).run(session)

    assert result.refused is not None and "2 of 2" in result.refused
    assert first.deleted == 0 and second.deleted == 0
    response = result.as_response()
    assert response["status"] == "refused" and response["dry_run"] is False


async def test_revert_refuses_an_empty_table(session, config, serving):
    result = await LogoRevertMode(
        config, FakePlexClient({}), serving(), _headers(), apply=True
    ).run(session)

    assert result.refused is not None and "media_items" in result.refused
    assert result.as_response() == {
        "mode": "logo_revert", "status": "refused", "reason": result.refused,
        "dry_run": False, "items": 0, "items_with_our_logo": 0, "missing": 0,
        "probe_failed": 0,
    }


async def test_revert_counts_a_failed_clear(session, config, serving):
    """One item that cannot be fetched back for the write costs its own line in
    the tally, and keeps its marker: its logo is still ours and still there."""

    class OneShotPlex(FakePlexClient):
        async def fetch_item(self, rating_key):
            item = await super().fetch_item(rating_key)
            if rating_key == "rk-bad" and self.fetched.count("rk-bad") > 1:
                raise RuntimeError("Plex went away")
            return item

    good = await _add_item(session, rating_key="rk-good", logo_upload_key=OUR_KEY)
    bad = await _add_item(session, rating_key="rk-bad", logo_upload_key=OUR_KEY, tmdb_id=2)
    good_item = FakeItem(logo="/good", logos=((OUR_KEY, True),))
    bad_item = FakeItem(logo="/bad", logos=((OUR_KEY, True),))
    plex = OneShotPlex({"rk-good": good_item, "rk-bad": bad_item})

    result = await LogoRevertMode(
        config, plex, serving(), _headers(), apply=True
    ).run(session)

    assert (result.cleared, result.failed) == (1, 1)
    assert await _marker(session, good.id) is None
    assert await _marker(session, bad.id) == OUR_KEY


async def test_revert_never_refreshes_the_plex_object(session, config, serving):
    await _add_item(session, rating_key="rk1", logo_upload_key=OUR_KEY)
    item = FakeItem(logo="/ours", logos=((OUR_KEY, True),))
    plex = FakePlexClient({"rk1": item})

    await LogoRevertMode(config, plex, serving(), _headers(), apply=True).run(session)

    assert item.deleted == 1  # the write half actually ran
    assert item.refreshed is False


async def test_revert_logs_a_missing_item_at_probe_at_info_not_warning(
    session, config, serving, caplog
):
    """A marked item 404s on the LogoRevertMode PROBE fetch -- it never
    becomes a candidate, so it costs its own INFO line and a tally, not a
    WARNING. Row 218."""
    await _add_item(session, rating_key="rk-gone", logo_upload_key=OUR_KEY)
    await _add_item(session, rating_key="rk-ok", logo_upload_key=OUR_KEY, tmdb_id=2)
    ok = FakeItem(logo="/ok", logos=((OUR_KEY, True),))

    class GoneClient(FakePlexClient):
        async def fetch_item(self, rating_key):
            self.fetched.append(rating_key)
            if rating_key == "rk-gone":
                raise PlexNotFound(f"(404) not_found ({rating_key})")
            return self._items[rating_key]

    plex = GoneClient({"rk-ok": ok})

    with caplog.at_level(logging.INFO):
        result = await LogoRevertMode(
            config, plex, serving(), _headers(), apply=True
        ).run(session)

    assert (result.items, result.items_with_our_logo, result.missing) == (2, 1, 1)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == []
    assert not any(r.exc_info for r in caplog.records)

    revert_records = [
        r for r in caplog.records if r.name == "autoposter.artwork_modes.logo"
    ]
    per_item = [r.message for r in revert_records if "rk-gone" in r.message]
    assert len(per_item) == 1
    assert per_item[0].startswith("logo revert: ") and "no longer in Plex" in per_item[0]


async def test_revert_logs_a_missing_item_at_apply_at_info_not_warning(
    session, config, serving, caplog
):
    """The marked item is still ours at probe time, but is gone from Plex by
    the time the apply loop re-fetches it to clear -- a second, later 404 the
    probe cannot see."""
    await _add_item(session, rating_key="rk1", logo_upload_key=OUR_KEY)
    item = FakeItem(logo="/ours", logos=((OUR_KEY, True),))

    class SecondFetchGoneClient(FakePlexClient):
        async def fetch_item(self, rating_key):
            self.fetched.append(rating_key)
            if self.fetched.count(rating_key) > 1:
                raise PlexNotFound(f"(404) not_found ({rating_key})")
            return self._items[rating_key]

    plex = SecondFetchGoneClient({"rk1": item})

    with caplog.at_level(logging.INFO):
        result = await LogoRevertMode(
            config, plex, serving(), _headers(), apply=True
        ).run(session)

    assert (result.items_with_our_logo, result.cleared, result.failed, result.missing) == (1, 0, 0, 1)
    assert item.unlocked == [] and item.deleted == 0

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == []


async def test_revert_counts_a_probe_failure_in_the_dry_run_body(
    session, config, serving, caplog
):
    """The revert's probe drops an unaskable item too, and a marked item that
    cannot be probed is not "not ours" -- it is unknown. Counted, and served in
    the dry-run body for the same reason the updater's is."""

    class HalfBrokenClient(FakePlexClient):
        async def fetch_item(self, rating_key):
            self.fetched.append(rating_key)
            if rating_key == "rk-bad":
                raise RuntimeError("connection reset by peer")
            return self._items[rating_key]

    await _add_item(session, rating_key="rk-good", logo_upload_key=OUR_KEY)
    await _add_item(session, rating_key="rk-bad", logo_upload_key=OUR_KEY, tmdb_id=2)
    good = FakeItem(logo="/ours", logos=((OUR_KEY, True),))
    plex = HalfBrokenClient({"rk-good": good})

    with caplog.at_level(logging.INFO):
        result = await LogoRevertMode(
            config, plex, serving(), _headers(), apply=False
        ).run(session)

    assert (result.items, result.items_with_our_logo) == (2, 1)
    assert (result.probe_failed, result.missing) == (1, 0)

    response = result.as_response()
    assert response["status"] == "dry run"
    assert response["probe_failed"] == 1
    assert "cleared" not in response
    assert good.deleted == 0  # a dry run really did nothing

    logo_records = [r for r in caplog.records if r.name == "autoposter.artwork_modes.logo"]
    per_item = [r for r in logo_records if "rk-bad" in r.getMessage()]
    assert len(per_item) == 1 and per_item[0].levelname == "WARNING"
    assert "connection reset" not in per_item[0].getMessage()

    summary = [
        r.getMessage() for r in logo_records
        if r.levelname == "INFO" and "rk-bad" not in r.getMessage()
    ]
    assert summary == ["logo revert: could not probe 1 item(s)"]


# --- the clearlogo guard (the #153 follow-up) --------------------------------
#
# Production, 2026-09-04: TMDB served a 32000x18839 clearlogo (602,848,000px)
# for 'Inside Out 2'. ``providers/ladder.rank_key`` sorts on ``-pixels``, so
# the bomb is the ladder's FIRST answer on every pass. #153 closed the render
# path's door on it; the updater below was the second one -- it took that first
# answer with no ceiling and no re-ask, and fetched it with a bare
# ``http.get``, missing the byte cap and the decode validation entirely.
#
# Every test here drives ``LogoMode(...).run(session)`` -- the object
# ``api/routes.run_artwork_logo`` constructs -- never ``_upload_one`` in
# isolation: a helper test would prove the guard can be computed and say
# nothing about whether the shipped path uses it, which is exactly how a gated
# feature has twice passed its own tests in this tree.

BOMB_URL = "https://provider.example/bomb.png"
BOMB2_URL = "https://provider.example/bomb2.png"
CORRUPT_URL = "https://provider.example/corrupt.png"

# The production bomb's own dimensions: 32000 * 18839 = 602,848,000px, nearly
# ten times ``render/artwork_fetch._ARTWORK_MAX_PIXELS`` (64,000,000).
BOMB_WIDTH, BOMB_HEIGHT = 32000, 18839


class ListProvider(FakeProvider):
    """A rung answering LOGO with a fixed candidate list, ranked by the real ladder."""

    def __init__(self, candidates):
        super().__init__()
        self._candidates = candidates

    async def fetch(self, request):
        self.requests.append(request)
        if request.art_kind != LOGO:
            return []
        return list(self._candidates)


def _candidate(url, *, width=800, height=310):
    return ArtCandidate(
        provider="Fake", url=url, language="en",
        width=width, height=height, score=8.0,
    )


@pytest_asyncio.fixture
async def recording():
    """``serving``'s twin: answers a ``{url path: bytes}`` map AND records every
    path it was asked for, so "never downloaded" is a provable assertion."""
    clients = []

    def install(by_path):
        asked = []

        def handler(request):
            asked.append(request.url.path)
            data = by_path.get(request.url.path)
            if data is None:
                return httpx.Response(404)
            return httpx.Response(200, content=data)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(http)
        return http, asked

    yield install
    for http in clients:
        await http.aclose()


async def test_a_bomb_by_metadata_is_skipped_without_being_downloaded(
    session, config, recording
):
    """The 'Inside Out 2' case. The bomb's provider-REPORTED dimensions are over
    the ceiling, so it is never fetched at all -- the check reads the same
    numbers the ladder ranked it by -- and the updater re-asks with that URL
    excluded, uploading the next best instead."""
    row = await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None)
    plex = FakePlexClient({"rk1": item})
    provider = ListProvider([
        _candidate(BOMB_URL, width=BOMB_WIDTH, height=BOMB_HEIGHT),
        _candidate(LOGO_URL),
    ])
    http, asked = recording({"/logo.png": LOGO_BYTES})

    result = await LogoMode(
        config, plex, http, _headers(), [provider], apply=True
    ).run(session)

    assert (result.uploaded, result.failed) == (1, 0)
    assert asked == ["/logo.png"]
    assert item.uploaded == [LOGO_BYTES]
    assert await _marker(session, row.id) == OUR_KEY


async def test_a_candidate_that_does_not_decode_is_skipped_for_the_next(
    session, config, recording
):
    """A body whose header parses and whose pixels do not is only found unusable
    AFTER it has been downloaded. Until now the updater had no way to ask for
    the next best, so one corrupt clearlogo cost the item its logo on every
    run, forever."""
    row = await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None)
    plex = FakePlexClient({"rk1": item})
    provider = ListProvider([
        # Ranked first: 1600*620 = 992,000px beats 800*310 = 248,000px.
        _candidate(CORRUPT_URL, width=1600, height=620),
        _candidate(LOGO_URL),
    ])
    http, asked = recording({
        "/corrupt.png": b"\x89PNG\r\n\x1a\n not really a PNG",
        "/logo.png": LOGO_BYTES,
    })

    result = await LogoMode(
        config, plex, http, _headers(), [provider], apply=True
    ).run(session)

    assert (result.uploaded, result.failed) == (1, 0)
    assert asked == ["/corrupt.png", "/logo.png"]
    assert item.uploaded == [LOGO_BYTES]
    assert await _marker(session, row.id) == OUR_KEY


async def test_an_svg_candidate_is_skipped_for_the_next_raster_one(
    session, config, recording
):
    """The render path can take an SVG clearlogo because ImageMagick rasterises
    it while compositing; Plex's clearLogo field cannot. So the SVG is skipped
    and the ladder re-asked, rather than costing the item its logo -- and what
    decides SVG-ness is the BYTES (``_looks_like_svg``), not a suffix the
    provider's URL merely claims."""
    row = await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None)
    plex = FakePlexClient({"rk1": item})
    provider = ListProvider([
        _candidate(SVG_LOGO_URL, width=1600, height=620),
        _candidate(LOGO_URL),
    ])
    http, asked = recording({"/logo.svg": b"<svg/>", "/logo.png": LOGO_BYTES})

    result = await LogoMode(
        config, plex, http, _headers(), [provider], apply=True
    ).run(session)

    assert (result.uploaded, result.failed) == (1, 0)
    assert asked == ["/logo.svg", "/logo.png"]
    assert item.uploaded == [LOGO_BYTES]
    assert await _marker(session, row.id) == OUR_KEY


async def test_one_item_whose_every_candidate_fails_does_not_stop_the_run(
    session, config, recording, caplog
):
    """The op-level contract. An item whose whole logo set is over the ceiling
    costs itself a logo and one WARNING naming it, never the run: the healthy
    item beside it still uploads and still records its marker."""
    good = await _add_item(session, rating_key="rk-good", tmdb_id=1)
    bad = await _add_item(session, rating_key="rk-bad", tmdb_id=2)
    good_item, bad_item = FakeItem(logo=None), FakeItem(logo=None)
    plex = FakePlexClient({"rk-good": good_item, "rk-bad": bad_item})

    class PerItemProvider(ListProvider):
        async def fetch(self, request):
            self.requests.append(request)
            if request.tmdb_id == 1:
                return [_candidate(LOGO_URL)]
            return [
                _candidate(BOMB_URL, width=BOMB_WIDTH, height=BOMB_HEIGHT),
                _candidate(BOMB2_URL, width=BOMB_WIDTH, height=BOMB_HEIGHT - 1),
            ]

    http, asked = recording({"/logo.png": LOGO_BYTES})

    with caplog.at_level(logging.WARNING):
        result = await LogoMode(
            config, plex, http, _headers(), [PerItemProvider([])], apply=True
        ).run(session)

    assert (result.uploaded, result.failed) == (1, 1)
    assert good_item.uploaded == [LOGO_BYTES] and bad_item.uploaded == []
    assert await _marker(session, good.id) == OUR_KEY
    assert await _marker(session, bad.id) is None
    # Neither bomb was fetched, and the run reached the healthy item.
    assert asked == ["/logo.png"]
    # Named by rating key, and by nothing else -- no URL (row 209).
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("rk-bad" in message for message in warnings)
    assert not any(BOMB_URL in message for message in warnings)


async def test_a_healthy_logo_is_uploaded_byte_identical_after_one_question(
    session, config, recording
):
    """The storm pin, and the one test here that must pass BEFORE this change as
    well as after. An item whose best clearlogo is under the ceiling asks the
    ladder exactly once, picks exactly what it picked before the guard existed,
    and uploads those bytes unchanged -- so the guard re-uploads for the
    affected items only, and no library-wide re-push follows it."""
    row = await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None)
    plex = FakePlexClient({"rk1": item})
    provider = ListProvider([_candidate(LOGO_URL)])
    http, asked = recording({"/logo.png": LOGO_BYTES})

    result = await LogoMode(
        config, plex, http, _headers(), [provider], apply=True
    ).run(session)

    assert (result.uploaded, result.failed) == (1, 0)
    assert len(provider.requests) == 1
    assert asked == ["/logo.png"]
    assert item.uploaded == [LOGO_BYTES]
    assert await _marker(session, row.id) == OUR_KEY
