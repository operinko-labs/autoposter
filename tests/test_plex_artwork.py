"""Uploading badged artwork to Plex."""
import os
import tempfile

import pytest

from autoposter.plex.artwork import (
    GENERATED_ARTWORK_PREFIX, UPLOADED_ARTWORK_PREFIX, _agent_default, _generated_default,
    generated_title_card_url, upload_artwork,
)


class FakePlexItem:
    def __init__(self):
        self.uploaded_poster = None
        self.uploaded_art = None
        self.poster_locked = False
        self.art_locked = False
        self.seen_bytes = None

    def _capture(self, filepath):
        with open(filepath, "rb") as handle:
            self.seen_bytes = handle.read()

    def uploadPoster(self, url=None, filepath=None):
        self.uploaded_poster = filepath
        self._capture(filepath)

    def uploadArt(self, url=None, filepath=None):
        self.uploaded_art = filepath
        self._capture(filepath)

    def lockPoster(self):
        self.poster_locked = True

    def lockArt(self):
        self.art_locked = True


@pytest.mark.parametrize("art_kind", ["poster", "season_poster", "title_card"])
def test_posters_and_title_cards_upload_as_posters(art_kind):
    item = FakePlexItem()
    upload_artwork(item, b"webp-bytes", art_kind)
    assert item.uploaded_poster is not None
    assert item.uploaded_art is None
    assert item.seen_bytes == b"webp-bytes"


def test_backgrounds_upload_as_art():
    item = FakePlexItem()
    upload_artwork(item, b"webp-bytes", "background")
    assert item.uploaded_art is not None
    assert item.uploaded_poster is None


def test_upload_locks_the_field_so_plex_cannot_reclaim_it():
    item = FakePlexItem()
    upload_artwork(item, b"x", "poster")
    assert item.poster_locked is True

    art_item = FakePlexItem()
    upload_artwork(art_item, b"x", "background")
    assert art_item.art_locked is True


def test_locking_can_be_disabled():
    item = FakePlexItem()
    upload_artwork(item, b"x", "poster", lock=False)
    assert item.poster_locked is False


def test_the_temporary_file_is_removed_afterwards():
    item = FakePlexItem()
    upload_artwork(item, b"x", "poster")
    assert not os.path.exists(item.uploaded_poster)


def test_the_temporary_file_is_removed_when_the_upload_fails():
    class Failing(FakePlexItem):
        def uploadPoster(self, url=None, filepath=None):
            self.uploaded_poster = filepath
            raise RuntimeError("plex said no")

    item = Failing()
    with pytest.raises(RuntimeError):
        upload_artwork(item, b"x", "poster")
    assert not os.path.exists(item.uploaded_poster)


class _FailingHandle:
    """A ``NamedTemporaryFile`` stand-in whose write blows up, so a test can
    tell whether the handle was closed before ``finally`` unlinked it."""

    def __init__(self, path):
        self.name = path
        self.closed = False

    def write(self, data):
        raise OSError("no space left on device")

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_a_failing_write_closes_the_handle_before_the_file_is_unlinked(tmp_path, monkeypatch):
    """``close()`` used to sit inside the ``try`` after ``write()``, so a
    failing write left ``finally`` unlinking a still-open descriptor."""
    handle = _FailingHandle(str(tmp_path / "artwork.tmp"))
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", lambda **kw: handle)

    with pytest.raises(OSError):
        upload_artwork(FakePlexItem(), b"webp-bytes", "poster")

    assert handle.closed is True


class FakePosterEntry:
    def __init__(self, rating_key, key="", selected=False):
        self.ratingKey = rating_key
        self.key = key
        self.selected = selected


def test_generated_artwork_prefix_is_media():
    assert GENERATED_ARTWORK_PREFIX == "media://"


def test_generated_default_picks_the_media_prefixed_entry():
    """C5: the media:// entry, never the agent's own guess or an upload."""
    listing = [
        FakePosterEntry("upload://abc", "/x"),
        FakePosterEntry("com.plexapp.agents.themoviedb://1", "/y"),
        FakePosterEntry("media://5/x.bundle/Contents/Thumbnails/thumb1.jpg", "/z"),
    ]
    entry = _generated_default(listing)
    assert entry is not None
    assert entry.ratingKey.startswith("media://")


def test_generated_default_is_none_without_a_media_entry():
    """The self-feed pin's other half: an upload:// entry (ours or anyone
    else's) and an agent guess are both present, but neither is a derived
    frame -- there is nothing here to select."""
    listing = [
        FakePosterEntry("upload://abc", "/x"),
        FakePosterEntry("com.plexapp.agents.themoviedb://1", "/y"),
    ]
    assert _generated_default(listing) is None


def test_generated_default_skips_an_entry_with_no_rating_key():
    assert _generated_default([FakePosterEntry("", "/x")]) is None


def test_neither_selector_ever_returns_a_selected_upload_entry():
    """Regression pin for the selection safety law's clause 2
    (.superpowers/sdd/p-upload-cleanup-recon.md Q3: "Never delete -- or
    re-select -- the selected entry" if it is an ``upload://`` one, the
    wrong-title-Jaws-poster case). This already holds on ``main``:
    ``_agent_default`` skips every ``upload://``-prefixed ratingKey
    regardless of ``selected`` (plex/artwork.py:195, which never reads
    ``.selected`` at all) and ``_generated_default`` only ever returns a
    ``media://``-prefixed entry (plex/artwork.py:223) -- so an ``upload://``
    entry being Plex's current SELECTED choice cannot change either result.
    Green against main; this pins the behaviour as a contract.
    """
    listing = [
        FakePosterEntry("upload://abc", "/x", selected=True),
        FakePosterEntry("com.plexapp.agents.themoviedb://1", "/y"),
        FakePosterEntry("media://5/x.bundle/Contents/Thumbnails/thumb1.jpg", "/z"),
    ]

    agent_entry = _agent_default(listing)
    assert agent_entry is not None
    assert agent_entry.ratingKey == "com.plexapp.agents.themoviedb://1"

    generated_entry = _generated_default(listing)
    assert generated_entry is not None
    assert generated_entry.ratingKey.startswith(GENERATED_ARTWORK_PREFIX)

    for entry in (agent_entry, generated_entry):
        assert not entry.ratingKey.startswith(UPLOADED_ARTWORK_PREFIX)


async def test_generated_title_card_url_joins_the_entrys_key_to_base_url():
    entry_path = "/library/metadata/1/file?url=media%3A%2F%2F5%2Fx.bundle...thumb1.jpg"
    listing = [FakePosterEntry("media://5/x.bundle/Contents/Thumbnails/thumb1.jpg", entry_path)]

    class FakePlexItem:
        def posters(self):
            return listing

    url = await generated_title_card_url(FakePlexItem(), "http://plex.local/")
    assert url == f"http://plex.local{entry_path}"


async def test_generated_title_card_url_is_none_without_a_media_entry():
    class FakePlexItem:
        def posters(self):
            return [FakePosterEntry("upload://abc", "/x")]

    assert await generated_title_card_url(FakePlexItem(), "http://plex.local/") is None
