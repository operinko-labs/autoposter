"""ResetMode: unlock our artwork and hand the fields back to Plex's own agent art.

Fake Plex throughout. The provenance read is the real one -- ``artwork_provenance``
over a ``MockTransport`` serving real EXIF-stamped JPEGs -- because "is this
artwork ours?" is the whole decision the mode makes; faking it would fake the
test. The write side is a ``FakeItem`` carrying ``unlockPoster``/``posters``/
``setPoster`` spies and their ``unlockArt``/``arts``/``setArt`` mirrors, plus a
``refresh`` that exists only to prove nothing calls it.
"""
import asyncio
import io
from pathlib import Path

import httpx
from plexapi.exceptions import NotFound as PlexNotFound
import pytest
import pytest_asyncio
from PIL import Image

from autoposter.artwork_modes.reset import ResetMode
from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem
from autoposter.plex.artwork import artwork_provenance as _plex_artwork_provenance
from autoposter.plex.artwork import (
    reset_artwork_to_agent_default as _plex_reset_artwork_to_agent_default,
)
from autoposter.plex.exif import PROVENANCE_TAG, format_provenance
from autoposter.servers.base import (
    CAP_ARTWORK_PROVENANCE, CAP_RESET_TO_AGENT_DEFAULT, ServerItemRef,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PLEX_URL = "http://plex.local"
PLEX_TOKEN = "plex-token"
AGENT_KEY = "metadata://posters/tmdb_12345"
UPLOAD_KEY = "upload://posters/7f3c9a"
AGENT_ART_KEY = "metadata://art/tmdb_12345"
UPLOAD_ART_KEY = "upload://art/1b2c3d"
# What the select spies record when handed a listing entry with no ``ratingKey``
# -- an entry nothing can prove came from an agent, so nothing may select it.
NO_RATING_KEY = "<no rating key>"


def _stamped_jpeg(fingerprint: str) -> bytes:
    """A JPEG carrying our provenance in its header, where JPEGs put it."""
    exif = Image.Exif()
    exif[PROVENANCE_TAG] = format_provenance(fingerprint)
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _plain_jpeg() -> bytes:
    """A JPEG nobody stamped -- a hand-set poster, or Plex's own agent art."""
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buffer, format="JPEG")
    return buffer.getvalue()


class FakePoster:
    """One entry of a plexapi ``posters()`` or ``arts()`` listing, told apart
    from the others by its rating key: anything pushed to the server is keyed
    ``upload://``. A ``None`` key builds an entry with no ``ratingKey`` attribute
    at all -- the listing shape neither half can prove is agent art."""

    def __init__(self, rating_key):
        if rating_key is not None:
            self.ratingKey = rating_key  # noqa: N815 - plexapi name


class FakeItem:
    """The plexapi surface reset touches: the ``thumb`` and ``art`` the
    provenance probes read, and the unlock/select spies the reset itself drives
    for each of those two fields."""

    def __init__(self, thumb="/thumb", art=None, posters=(AGENT_KEY, UPLOAD_KEY),
                 arts=(AGENT_ART_KEY, UPLOAD_ART_KEY)):
        self.thumb = thumb
        self.art = art
        self._posters = [FakePoster(key) for key in posters]
        self._arts = [FakePoster(key) for key in arts]
        self.unlocked = []
        self.selected = []
        self.refreshed = False

    def refresh(self):
        self.refreshed = True

    def unlockPoster(self):  # noqa: N802 - plexapi name
        self.unlocked.append("poster")

    def posters(self):
        return list(self._posters)

    def setPoster(self, poster):  # noqa: N802 - plexapi name
        # getattr, not attribute access: a keyless entry must show up as a
        # recorded selection rather than as an AttributeError the mode's
        # per-item guard would swallow into the same tally as "skipped it".
        self.selected.append(getattr(poster, "ratingKey", NO_RATING_KEY))

    def unlockArt(self):  # noqa: N802 - plexapi name
        self.unlocked.append("art")

    def arts(self):
        return list(self._arts)

    def setArt(self, art):  # noqa: N802 - plexapi name
        self.selected.append(getattr(art, "ratingKey", NO_RATING_KEY))


class FakePlexClient:
    """Stands in for PlexClient. ``artwork_provenance`` and
    ``reset_artwork_to_agent_default`` are the real ones, run against whatever
    ``MockTransport`` the ``serving`` fixture most recently built -- the same
    one every test in this file already hands ``ResetMode`` directly, so
    nothing here needs its own copy threaded through the constructor.
    """

    capabilities = frozenset({CAP_ARTWORK_PROVENANCE, CAP_RESET_TO_AGENT_DEFAULT})
    name = "plex"

    def __init__(self, items=None):
        self._items = items or {}
        self.fetched = []

    async def fetch_item(self, rating_key):
        self.fetched.append(rating_key)
        return self._items[rating_key]

    async def fetch_ref(self, rating_key):
        try:
            await self.fetch_item(rating_key)
        except PlexNotFound:
            return None
        return ServerItemRef("plex", rating_key, "", "")

    async def artwork_provenance(self, ref, art_kind):
        item = await self.fetch_item(ref.native_id)
        return await _plex_artwork_provenance(
            _current_http, item, PLEX_URL, _headers(), art_kind
        )

    async def reset_artwork_to_agent_default(self, ref, art_kind):
        item = await self.fetch_item(ref.native_id)
        return await asyncio.to_thread(_plex_reset_artwork_to_agent_default, item, art_kind)


# The MockTransport the ``serving`` fixture most recently built -- FakePlexClient
# reads it lazily rather than taking it in its own constructor, so every one of
# this file's existing ``FakePlexClient({...})`` call sites keeps working
# unchanged even though ``ResetMode`` no longer routes its http reads through
# its own ``http``/``headers`` (it now goes through the server).
_current_http = None


def _ranged(data, request):
    """Serve a byte range out of ``data`` the way Plex does."""
    header = request.headers.get("Range")
    if header is None:
        return httpx.Response(200, content=data)
    spec = header.removeprefix("bytes=")
    if spec.startswith("-"):
        chunk = data[-int(spec[1:]):]
    else:
        start, end = spec.split("-")
        chunk = data[int(start):int(end) + 1]
    return httpx.Response(206, content=chunk)


@pytest_asyncio.fixture
async def serving():
    """Yields a helper that installs a ``{thumb path: bytes}`` map and returns
    an httpx client serving it with Range support."""
    clients = []

    def install(by_path):
        def handler(request):
            data = by_path.get(request.url.path)
            if data is None:
                return httpx.Response(404)
            return _ranged(data, request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(http)
        global _current_http
        _current_http = http
        return http

    yield install
    for http in clients:
        await http.aclose()
    _current_http = None


@pytest.fixture
def config():
    cfg = load_config(EXAMPLE)
    cfg.plex.url = PLEX_URL
    return cfg


def _headers():
    return {"X-Plex-Token": PLEX_TOKEN}


async def _add_item(session, *, rating_key, kind="movie", library="Movies"):
    item = MediaItem(
        rating_key=rating_key, library=library, kind=kind, title="A",
        root_folder="A (1999)",
    )
    session.add(item)
    await session.commit()
    return item


async def test_dry_run_changes_nothing(session, config, serving):
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/ours")
    plex = FakePlexClient({"rk1": item})
    http = serving({"/ours": _stamped_jpeg("fp-abc")})

    result = await ResetMode(config, plex, http, _headers(), apply=False).run(session)

    assert result.dry_run is True
    assert (result.items, result.items_with_our_art, result.fields) == (1, 1, 1)
    assert item.unlocked == [] and item.selected == []
    response = result.as_response()
    assert response["status"] == "dry run"
    assert (response["items"], response["items_with_our_art"]) == (1, 1)
    # One field of the one item is ours: its poster, not its (absent) background.
    assert response["fields"] == 1
    # The operator has to be told the replaced upload stays on the server.
    assert "orphan" in response["note"]


async def test_apply_unlocks_and_selects_the_agent_default(session, config, serving):
    """The mode's whole write path: unlock the field this project locked, then
    hand it back to the agent-supplied entry rather than any ``upload://`` one."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/ours", posters=(UPLOAD_KEY, AGENT_KEY))
    plex = FakePlexClient({"rk1": item})
    http = serving({"/ours": _stamped_jpeg("fp-abc")})

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert result.dry_run is False
    assert (result.reset, result.failed) == (1, 0)
    assert item.unlocked == ["poster"]
    # The upload:// entry is ours (or an operator's); the agent entry is Plex's.
    assert item.selected == [AGENT_KEY]
    assert result.as_response()["status"] == "reset"


async def test_reset_leaves_a_hand_set_poster_alone(session, config, serving):
    """The provenance mutation-proof: only art carrying OUR EXIF provenance is
    ours to reset. Drop the ``parse_provenance``/``is None`` guard in
    ResetMode.run and the hand-set item becomes a candidate too, reddening both
    the ``items_with_our_art == 1`` count and the untouched-item assertions."""
    await _add_item(session, rating_key="rk-ours")
    await _add_item(session, rating_key="rk-theirs")
    ours, theirs = FakeItem(thumb="/ours"), FakeItem(thumb="/theirs")
    plex = FakePlexClient({"rk-ours": ours, "rk-theirs": theirs})
    http = serving({"/ours": _stamped_jpeg("fp-abc"), "/theirs": _plain_jpeg()})

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert (result.items, result.items_with_our_art) == (2, 1)
    assert ours.unlocked == ["poster"] and ours.selected == [AGENT_KEY]
    assert theirs.unlocked == [] and theirs.selected == []


async def test_reset_leaves_an_item_with_no_artwork_alone(session, config, serving):
    """No thumb at all: ``artwork_provenance`` answers None, which is "not ours"."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb=None)
    plex = FakePlexClient({"rk1": item})
    http = serving({})

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert (result.items, result.items_with_our_art) == (1, 0)
    assert item.unlocked == []


async def test_reset_counts_an_item_with_no_agent_art_as_failed(session, config, serving):
    """Plex holds nothing but ``upload://`` entries, so there is no default to
    hand the field back to. The field is still unlocked -- that much the
    operator asked for -- but the reset itself could not complete."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/ours", posters=(UPLOAD_KEY,))
    plex = FakePlexClient({"rk1": item})
    http = serving({"/ours": _stamped_jpeg("fp-abc")})

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert (result.reset, result.failed) == (0, 1)
    assert item.unlocked == ["poster"]
    assert item.selected == []


async def test_reset_counts_a_listing_without_rating_keys_as_failed(session, config, serving):
    """A listing entry with no ``ratingKey`` is not something we can prove came
    from an agent, so it must be skipped rather than selected. Turn the guard
    back into ``if rating_key.startswith(UPLOADED_ARTWORK_PREFIX)`` and the
    keyless entry is returned as the agent default and handed to ``setPoster``,
    reddening both assertions below."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/ours", posters=(None, None))
    plex = FakePlexClient({"rk1": item})
    http = serving({"/ours": _stamped_jpeg("fp-abc")})

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    # Same accounting as "Plex holds no agent art": unlocked, nothing selected.
    assert (result.reset, result.failed) == (0, 1)
    assert item.unlocked == ["poster"]
    assert item.selected == []  # not [NO_RATING_KEY]: nothing was handed over


async def test_apply_resets_the_background_as_well_as_the_poster(session, config, serving):
    """Reset is a complete undo of what ``upload_artwork`` locked, and it locks
    the background too -- so both fields are unlocked and handed back to the
    agent, and both count."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/ours-poster", art="/ours-art")
    plex = FakePlexClient({"rk1": item})
    http = serving({
        "/ours-poster": _stamped_jpeg("fp-poster"),
        "/ours-art": _stamped_jpeg("fp-art"),
    })

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    # One item, two fields: the cap still counts items, the tally counts fields.
    assert (result.items, result.items_with_our_art, result.fields) == (1, 1, 2)
    assert (result.reset, result.failed) == (2, 0)
    assert item.unlocked == ["poster", "art"]
    assert item.selected == [AGENT_KEY, AGENT_ART_KEY]


async def test_reset_leaves_a_hand_set_background_alone(session, config, serving):
    """The provenance rule holds per field: an item whose poster is ours but
    whose background an operator set by hand has only its poster reset."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/ours", art="/theirs")
    plex = FakePlexClient({"rk1": item})
    http = serving({"/ours": _stamped_jpeg("fp-abc"), "/theirs": _plain_jpeg()})

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert (result.items_with_our_art, result.fields) == (1, 1)
    assert (result.reset, result.failed) == (1, 0)
    assert item.unlocked == ["poster"] and item.selected == [AGENT_KEY]


async def test_reset_counts_an_item_with_no_agent_background_as_failed(session, config, serving):
    """The art half of the no-agent-art accounting: the field is unlocked, but
    Plex holds nothing but uploads to hand it back to."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb=None, art="/ours-art", arts=(UPLOAD_ART_KEY,))
    plex = FakePlexClient({"rk1": item})
    http = serving({"/ours-art": _stamped_jpeg("fp-art")})

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert (result.reset, result.failed) == (0, 1)
    assert item.unlocked == ["art"]
    assert item.selected == []


@pytest.mark.parametrize("kind", ["season", "episode"])
async def test_reset_never_touches_a_background_below_a_show(session, config, serving, kind):
    """Plex fills a season's and an episode's ``art`` from the show's backdrop,
    which on a badged library is OUR stamped upload -- so probing that field
    would find our own provenance on a field this service never wrote to, and
    an applied run would unlock and re-select it. The pipeline uploads a
    background only to movies and shows (``ART_KINDS_FOR``), so the reset has
    to stop there too: nothing is counted and nothing is written."""
    await _add_item(session, rating_key="rk1", kind=kind)
    # The item's own poster is not ours; its ``art`` serves the show's backdrop,
    # stamped by us when the show's background was uploaded.
    item = FakeItem(thumb=None, art="/show-backdrop")
    plex = FakePlexClient({"rk1": item})
    http = serving({"/show-backdrop": _stamped_jpeg("fp-show")})

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert (result.items, result.items_with_our_art, result.fields) == (1, 0, 0)
    assert (result.reset, result.failed) == (0, 0)
    assert item.unlocked == [] and item.selected == []


async def test_reset_still_covers_an_episodes_own_poster(session, config, serving):
    """The other half of the restriction: an episode's badged image is its
    *poster*, so that field stays in scope."""
    await _add_item(session, rating_key="rk1", kind="episode")
    item = FakeItem(thumb="/ours", art="/show-backdrop")
    plex = FakePlexClient({"rk1": item})
    http = serving({
        "/ours": _stamped_jpeg("fp-abc"),
        "/show-backdrop": _stamped_jpeg("fp-show"),
    })

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert (result.items_with_our_art, result.fields) == (1, 1)
    assert item.unlocked == ["poster"]
    assert item.selected == [AGENT_KEY]


async def test_apply_resets_exactly_the_filtered_set(session, config, serving):
    """A Movies-only reset must not touch the show, even though the show's
    poster is just as much ours."""
    await _add_item(session, rating_key="rk-m", kind="movie", library="Movies")
    await _add_item(session, rating_key="rk-t", kind="show", library="TV Shows")
    movie, tv = FakeItem(thumb="/m"), FakeItem(thumb="/t")
    plex = FakePlexClient({"rk-m": movie, "rk-t": tv})
    http = serving({"/m": _stamped_jpeg("fp-m"), "/t": _stamped_jpeg("fp-t")})

    result = await ResetMode(
        config, plex, http, _headers(), apply=True, library="Movies"
    ).run(session)

    assert result.items == 1
    assert movie.selected == [AGENT_KEY]
    assert tv.selected == [] and tv.unlocked == []


async def test_apply_filters_by_type_and_item(session, config, serving):
    await _add_item(session, rating_key="rk-m", kind="movie", library="Movies")
    show = await _add_item(session, rating_key="rk-t", kind="show", library="TV Shows")
    movie, tv = FakeItem(thumb="/m"), FakeItem(thumb="/t")
    plex = FakePlexClient({"rk-m": movie, "rk-t": tv})
    http = serving({"/m": _stamped_jpeg("fp-m"), "/t": _stamped_jpeg("fp-t")})

    result = await ResetMode(
        config, plex, http, _headers(), apply=True, kind="show", item_id=show.id
    ).run(session)

    assert result.items == 1
    assert tv.selected == [AGENT_KEY]
    assert movie.selected == []


async def test_reset_respects_the_cap(session, config, serving):
    """Two items are ours but the absolute cap is 1, so it refuses with the real
    numbers and changes nothing -- proven by neither item being unlocked even
    though apply is true."""
    config.artwork_modes.max_changes = 1
    await _add_item(session, rating_key="rk1")
    await _add_item(session, rating_key="rk2")
    first, second = FakeItem(thumb="/one"), FakeItem(thumb="/two")
    plex = FakePlexClient({"rk1": first, "rk2": second})
    http = serving({"/one": _stamped_jpeg("fp-1"), "/two": _stamped_jpeg("fp-2")})

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert result.refused is not None
    assert "2 of 2" in result.refused
    assert first.unlocked == [] and second.unlocked == []
    response = result.as_response()
    assert response["status"] == "refused"
    assert response["dry_run"] is False  # apply=True was requested
    assert (response["items"], response["items_with_our_art"]) == (2, 2)


async def test_reset_never_refreshes_the_plex_object(session, config, serving):
    """The behavioural companion to the project-wide no-.refresh() AST guard --
    the guard cannot see ``getattr(item, "refresh")()`` alias evasion, and a
    refresh here would have Plex re-pull metadata and undo the unlock."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/ours", art="/ours-art")
    plex = FakePlexClient({"rk1": item})
    http = serving({
        "/ours": _stamped_jpeg("fp-abc"), "/ours-art": _stamped_jpeg("fp-art"),
    })

    await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert item.unlocked == ["poster", "art"]  # both halves actually ran
    assert item.refreshed is False


async def test_reset_refuses_an_empty_table(session, config, serving):
    plex = FakePlexClient({})
    result = await ResetMode(config, plex, serving({}), _headers(), apply=True).run(session)

    assert result.refused is not None
    assert "media_items" in result.refused
    # apply=True was requested, so dry_run mirrors the success paths: False.
    assert result.as_response() == {
        "mode": "reset", "status": "refused", "reason": result.refused,
        "dry_run": False, "items": 0, "items_with_our_art": 0, "fields": 0,
        "missing": 0, "probe_failed": 0,
    }


async def test_reset_counts_a_failed_reset(session, config, serving):
    """One item that cannot be fetched back for the write costs its own line in
    the tally, not the run."""

    class OneShotPlex(FakePlexClient):
        """Fails the SECOND time this item is resolved to a ref -- once for
        the probe, then again for the write -- rather than counting raw
        ``fetch_item`` calls: ``artwork_provenance`` re-resolves the item once
        per art kind it checks, so a plain call count would trip during
        probing instead of at the write this test is about."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._ref_calls = 0

        async def fetch_ref(self, rating_key):
            if rating_key == "rk-bad":
                self._ref_calls += 1
                if self._ref_calls > 1:
                    raise RuntimeError("Plex went away")
            return await super().fetch_ref(rating_key)

    await _add_item(session, rating_key="rk-good")
    await _add_item(session, rating_key="rk-bad")
    good, bad = FakeItem(thumb="/good"), FakeItem(thumb="/bad")
    plex = OneShotPlex({"rk-good": good, "rk-bad": bad})
    http = serving({"/good": _stamped_jpeg("fp-g"), "/bad": _stamped_jpeg("fp-b")})

    result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert (result.reset, result.failed) == (1, 1)
    assert good.selected == [AGENT_KEY]
    assert bad.selected == []


async def test_reset_logs_a_missing_item_at_probe_at_info_not_warning(
    session, config, serving, caplog
):
    """A stale rating_key 404s on the PROBE fetch -- the item never becomes a
    candidate, so it costs its own INFO line and a tally, not a WARNING and
    not the run. Row 218, matching backup.py's PR #112 hotfix shape."""
    await _add_item(session, rating_key="rk-gone")
    await _add_item(session, rating_key="rk-ok")
    ok = FakeItem(thumb="/ours")

    class GoneClient(FakePlexClient):
        async def fetch_item(self, rating_key):
            self.fetched.append(rating_key)
            if rating_key == "rk-gone":
                raise PlexNotFound(f"(404) not_found ({rating_key})")
            return self._items[rating_key]

    plex = GoneClient({"rk-ok": ok})
    http = serving({"/ours": _stamped_jpeg("fp-abc")})

    with caplog.at_level("INFO"):
        result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert (result.items, result.items_with_our_art, result.missing) == (2, 1, 1)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == []
    assert not any(r.exc_info for r in caplog.records)

    reset_records = [r for r in caplog.records if r.name == "autoposter.artwork_modes.reset"]
    per_item = [r.message for r in reset_records if "rk-gone" in r.message]
    assert len(per_item) == 1
    assert per_item[0].startswith("reset: ") and "no longer in Plex" in per_item[0]

    summary = [r.message for r in reset_records if r.message not in per_item]
    assert len(summary) == 1
    assert summary[0].startswith("reset: ") and "1" in summary[0]


async def test_reset_logs_a_missing_item_at_apply_at_info_not_warning(
    session, config, serving, caplog
):
    """The item is ours at probe time, but is gone from Plex by the time the
    apply loop re-fetches it to write -- a second, later 404 the probe cannot
    see. Same shape as the probe-phase 404, tallied into the same field."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/ours")

    class SecondFetchGoneClient(FakePlexClient):
        """Fails the SECOND time this item is resolved to a ref -- once for
        the probe, then again for the write -- rather than counting raw
        ``fetch_item`` calls: ``artwork_provenance`` re-resolves the item once
        per art kind it checks, so a plain call count would trip during
        probing instead of at the apply-loop fetch this test is about."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._ref_calls = 0

        async def fetch_ref(self, rating_key):
            self._ref_calls += 1
            if self._ref_calls > 1:
                return None
            return await super().fetch_ref(rating_key)

    plex = SecondFetchGoneClient({"rk1": item})
    http = serving({"/ours": _stamped_jpeg("fp-abc")})

    with caplog.at_level("INFO"):
        result = await ResetMode(config, plex, http, _headers(), apply=True).run(session)

    assert (result.items_with_our_art, result.reset, result.failed, result.missing) == (1, 0, 0, 1)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == []
    assert item.unlocked == []


async def test_reset_counts_a_probe_failure_in_the_dry_run_body(
    session, config, serving, caplog
):
    """The reset probe drops an unaskable item from ``ours`` exactly as the logo
    modes' probes do, so a dry run against an unreachable Plex says "no artwork
    of ours" when it means "nothing could be asked". Counted, and in the
    dry-run body."""

    class HalfBrokenClient(FakePlexClient):
        async def fetch_item(self, rating_key):
            self.fetched.append(rating_key)
            if rating_key == "rk-bad":
                raise RuntimeError("connection reset by peer")
            return self._items[rating_key]

    await _add_item(session, rating_key="rk-good")
    await _add_item(session, rating_key="rk-bad")
    plex = HalfBrokenClient({"rk-good": FakeItem(thumb="/ours")})
    http = serving({"/ours": _stamped_jpeg("fp-abc")})

    with caplog.at_level("INFO"):
        result = await ResetMode(config, plex, http, _headers(), apply=False).run(session)

    assert (result.items, result.items_with_our_art, result.fields) == (2, 1, 1)
    assert (result.probe_failed, result.missing) == (1, 0)

    response = result.as_response()
    assert response["status"] == "dry run"
    assert response["probe_failed"] == 1
    assert "reset" not in response

    reset_records = [r for r in caplog.records if r.name == "autoposter.artwork_modes.reset"]
    per_item = [r for r in reset_records if "rk-bad" in r.getMessage()]
    assert len(per_item) == 1 and per_item[0].levelname == "WARNING"
    assert "connection reset" not in per_item[0].getMessage()

    summary = [
        r.getMessage() for r in reset_records
        if r.levelname == "INFO" and "rk-bad" not in r.getMessage()
    ]
    assert summary == ["reset: could not probe 1 item(s)"]
