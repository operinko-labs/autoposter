"""Fetching and applying a collection's poster.

The "valid image" cases build a small real JPEG with Pillow rather than
mocking image decoding, so the validation in ``fetch_poster`` is exercised
against real bytes.
"""
import hashlib
import io
import os
import tempfile
import threading

import httpx
import pytest
from PIL import Image

from autoposter.collections.posters import (
    TMDB_PROFILE_KIND,
    _write_and_upload,
    apply_poster,
    hosted_poster_url,
    tmdb_profile_url,
)
from autoposter.db.models import ManagedCollection

LIBRARY = "Movies"
TITLE = "IMDb Top 250"
KIND = "chart"
KEY = "IMDb Top 250"

PROFILE_PATH = "/a-constructed-profile-path.jpg"
PROFILE_URL = "https://image.tmdb.org/t/p/original/a-constructed-profile-path.jpg"


def _jpeg_bytes(color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, format="JPEG")
    return buffer.getvalue()


class _FakeCollection:
    """Records what was uploaded, reading the temp file's content before it
    is cleaned up so a test can assert on the bytes after the call returns."""

    def __init__(self):
        self.uploaded_paths: list[str] = []
        self.uploaded_bytes: list[bytes] = []
        self.locks = 0

    def uploadPoster(self, filepath):
        self.uploaded_paths.append(filepath)
        with open(filepath, "rb") as handle:
            self.uploaded_bytes.append(handle.read())

    def lockPoster(self):
        self.locks += 1


class _ExplodingCollection:
    """Captures the temp filepath it was given, then blows up -- so a test
    can confirm cleanup still happens on the failure path."""

    def __init__(self):
        self.captured_path: str | None = None

    def uploadPoster(self, filepath):
        self.captured_path = filepath
        raise RuntimeError("plex rejected the upload")

    def lockPoster(self):
        raise AssertionError("must not lock a poster that failed to upload")


async def _record(session, poster_sha256=None):
    row = ManagedCollection(
        library=LIBRARY, title=TITLE, kind="manual", definition_hash="a" * 64,
        poster_sha256=poster_sha256,
    )
    session.add(row)
    await session.flush()
    return row


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_hosted_poster_is_fetched_uploaded_and_the_hash_recorded(
    tmp_path, config_factory, session
):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert message is not None
    assert collection.uploaded_bytes == [data]
    assert record.poster_sha256 == hashlib.sha256(data).hexdigest()


async def test_a_local_file_takes_precedence_and_no_request_is_made(
    tmp_path, config_factory, session
):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    folder = tmp_path / LIBRARY / TITLE
    folder.mkdir(parents=True)
    local_bytes = _jpeg_bytes("blue")
    (folder / "poster.jpg").write_bytes(local_bytes)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        raise AssertionError("must not fetch when a local poster exists")

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert message is not None
    assert collection.uploaded_bytes == [local_bytes]
    assert record.poster_sha256 == hashlib.sha256(local_bytes).hexdigest()


async def test_an_unchanged_hash_uploads_nothing_on_a_second_pass(
    tmp_path, config_factory, session
):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session, poster_sha256=hashlib.sha256(data).hexdigest())
    collection = _FakeCollection()

    async def handler(request):
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert message is None
    assert collection.uploaded_bytes == []


async def test_a_changed_local_file_re_uploads(tmp_path, config_factory, session):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    folder = tmp_path / LIBRARY / TITLE
    folder.mkdir(parents=True)
    new_bytes = _jpeg_bytes("green")
    (folder / "poster.jpg").write_bytes(new_bytes)
    record = await _record(session, poster_sha256=hashlib.sha256(b"old poster bytes").hexdigest())
    collection = _FakeCollection()

    async def handler(request):
        raise AssertionError("must not fetch when a local poster exists")

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert message is not None
    assert collection.uploaded_bytes == [new_bytes]
    assert record.poster_sha256 == hashlib.sha256(new_bytes).hexdigest()


async def test_a_corrupt_local_file_falls_through_to_the_hosted_default(
    tmp_path, config_factory, session
):
    """The operator's file can be truncated, zero-byte, or saved HTML just as
    easily as a response body can. Validating only the fetch path would let it
    be uploaded, hashed and recorded as current -- never self-correcting."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    folder = tmp_path / LIBRARY / TITLE
    folder.mkdir(parents=True)
    (folder / "poster.jpg").write_bytes(b"<html><body>404 not found</body></html>")
    hosted = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        return httpx.Response(200, content=hosted)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert message is not None
    assert collection.uploaded_bytes == [hosted]
    assert record.poster_sha256 == hashlib.sha256(hosted).hexdigest()


async def test_a_zero_byte_local_file_falls_through_to_the_hosted_default(
    tmp_path, config_factory, session
):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    folder = tmp_path / LIBRARY / TITLE
    folder.mkdir(parents=True)
    (folder / "poster.jpg").write_bytes(b"")
    hosted = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        return httpx.Response(200, content=hosted)

    async with _client(handler) as http:
        await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert collection.uploaded_bytes == [hosted]


async def test_the_upload_is_locked_and_runs_off_the_event_loop(
    tmp_path, config_factory, session
):
    """``uploadPoster`` is a synchronous ``requests`` POST of the whole image,
    so it must not run on the loop the scheduler and liveness probe share; and
    without ``lockPoster`` Plex's agent can reclaim the field, which the stored
    hash would then stop us ever re-applying."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()
    upload_threads: list[int] = []

    original = collection.uploadPoster

    def recording_upload(filepath):
        upload_threads.append(threading.get_ident())
        original(filepath)

    collection.uploadPoster = recording_upload

    async def handler(request):
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert collection.locks == 1
    assert upload_threads and upload_threads[0] != threading.get_ident()


async def test_a_404_leaves_the_collection_untouched_and_reports_it(
    tmp_path, config_factory, session
):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        return httpx.Response(404)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert message is not None
    assert collection.uploaded_bytes == []
    assert record.poster_sha256 is None


async def test_a_200_with_a_non_image_body_is_rejected_without_uploading(
    tmp_path, config_factory, session
):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        return httpx.Response(200, content=b"<html><body>not found</body></html>")

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert message is not None
    assert collection.uploaded_bytes == []
    assert record.poster_sha256 is None


async def test_dry_run_uploads_and_records_nothing(tmp_path, config_factory, session):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()
    requests_seen = []

    async def handler(request):
        requests_seen.append(request)
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=True
        )

    assert message is not None
    assert len(requests_seen) == 1  # fetched, to prove the source is reachable
    assert collection.uploaded_bytes == []
    assert record.poster_sha256 is None


async def test_the_temporary_file_is_removed_on_the_success_path(
    tmp_path, config_factory, session
):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert collection.uploaded_paths
    assert not os.path.exists(collection.uploaded_paths[0])


async def test_the_temporary_file_is_removed_on_the_failure_path(
    tmp_path, config_factory, session
):
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _ExplodingCollection()

    async def handler(request):
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY, dry_run=False
        )

    assert message is not None
    assert collection.captured_path is not None
    assert not os.path.exists(collection.captured_path)
    assert record.poster_sha256 is None


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
    handle = _FailingHandle(str(tmp_path / "poster.tmp"))
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", lambda **kw: handle)

    with pytest.raises(OSError):
        _write_and_upload(_FakeCollection(), b"poster-bytes")

    assert handle.closed is True


async def test_a_title_that_escapes_the_assets_root_refuses_the_whole_step(
    tmp_path, config_factory, session
):
    """Row 124. A managed title is interpolated into the poster path, and a
    title carrying ``..`` steers it outside ``assets_root``. The poster step
    refuses that collection outright -- it does not fall through to the
    hosted default, because the refusal is about the collection's identity
    rather than about this one source being unusable -- and nothing is
    fetched, written or uploaded."""
    root = tmp_path / "assets"
    root.mkdir()
    config = config_factory(assets_root=str(root), library_folders=True)
    record = await _record(session)
    record.title = "../../escaped"
    await session.flush()
    collection = _FakeCollection()
    fetches: list[str] = []

    async def handler(request):
        fetches.append(str(request.url))
        return httpx.Response(200, content=_jpeg_bytes())

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY,
            dry_run=False,
        )

    assert message == (
        "refused a poster for '../../escaped': the title steers its path "
        "outside the assets root"
    )
    assert fetches == [], "a refused collection must not reach the network"
    assert collection.uploaded_paths == []
    assert record.poster_sha256 is None


# --- the TMDb profile photo, the one source that is not a hosted default -----


def test_a_tmdb_profile_path_becomes_an_image_cdn_url():
    """The one poster source that is not a Kometa ``Default-Images`` path. The
    base is ``providers/tmdb.py``'s, the same one the artwork pipeline fetches
    every other TMDb image from, so a size change happens in one place."""
    assert tmdb_profile_url(PROFILE_PATH) == PROFILE_URL


def test_a_profile_path_tmdb_did_not_shape_has_no_url():
    """Same rule as an unrecognised ``hosted_poster_url`` kind: a guessed URL
    404s and the collection quietly keeps no poster, which is harder to spot
    than an error. TMDb's file paths are rooted at ``/``."""
    assert tmdb_profile_url("") is None
    assert tmdb_profile_url("a-constructed-profile-path.jpg") is None


def test_the_profile_kind_is_not_a_row_of_the_hosted_table():
    """``hosted_poster_url`` must keep answering ``None`` for it -- if it ever
    grew a matching row, two functions would both claim the kind and the
    dispatch in ``apply_poster`` would silently stop mattering."""
    assert hosted_poster_url(TMDB_PROFILE_KIND, PROFILE_PATH) is None


async def test_apply_poster_fetches_a_profile_photo_from_the_image_cdn(
    tmp_path, config_factory, session
):
    """The end-to-end proof that the new source reaches the existing upload
    machinery: same fetch, same ``_is_image`` validation, same hash-compare,
    same ``poster_sha256`` write. The transport answers only the image-CDN URL,
    so the dispatch is pinned by what was actually requested rather than by the
    message alone."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()
    fetched: list[str] = []

    async def handler(request):
        fetched.append(str(request.url))
        if str(request.url) != PROFILE_URL:
            return httpx.Response(404)
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY,
            TMDB_PROFILE_KIND, PROFILE_PATH, dry_run=False,
        )

    assert fetched == [PROFILE_URL]
    assert "set the poster" in message
    assert collection.uploaded_bytes == [data]
    assert record.poster_sha256 == hashlib.sha256(data).hexdigest()


async def test_a_person_with_no_profile_photo_reports_no_source(
    tmp_path, config_factory, session
):
    """A person whose record carries no photo reaches here with an empty key,
    and an empty key is not a URL. Cosmetic, so it is reported rather than
    raised -- the same answer an unrecognised hosted kind gets."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        raise AssertionError("must not fetch without a resolvable URL")

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY,
            TMDB_PROFILE_KIND, "", dry_run=False,
        )

    assert message == "no poster source for %r" % TITLE
    assert record.poster_sha256 is None


async def test_a_local_poster_still_wins_over_a_profile_photo(
    tmp_path, config_factory, session
):
    """The override order is the source's, not the kind's. An operator file at
    ``<assets_root>/<library>/<title>/poster.jpg`` beats every hosted source
    (``prioritize_assets: true`` in the tool being replaced), and adding a
    seventh source must not have carved out an exception."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    folder = tmp_path / LIBRARY / TITLE
    folder.mkdir(parents=True)
    local_bytes = _jpeg_bytes("blue")
    (folder / "poster.jpg").write_bytes(local_bytes)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        raise AssertionError("must not fetch when a local poster exists")

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY,
            TMDB_PROFILE_KIND, PROFILE_PATH, dry_run=False,
        )

    assert "local file" in message
    assert collection.uploaded_bytes == [local_bytes]


async def test_the_hosted_defaults_are_untouched_by_the_new_branch(
    tmp_path, config_factory, session
):
    """The dispatch is on ``kind``, so every other kind must still reach
    ``hosted_poster_url`` and its ``Default-Images`` URL -- the regression a
    new branch in front of it could introduce without any test noticing."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    fetched: list[str] = []

    async def handler(request):
        fetched.append(str(request.url))
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        await apply_poster(
            session, http, config, _FakeCollection(), record, LIBRARY, KIND, KEY,
            dry_run=False,
        )

    assert fetched == [hosted_poster_url(KIND, KEY)]
    assert fetched[0].startswith(
        "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master/"
    )


# --- the Default-Images family posters (default_images.FAMILIES) -----------


async def test_a_default_image_family_is_fetched_cached_and_reported_as_such(
    tmp_path, config_factory, session
):
    """The new fourth source. It rides the same rank the generated separator
    art already occupies -- below the operator's own file, above nothing --
    so the priority guarantee `prioritize_assets` gave is unchanged."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        assert request.url.path.endswith("/genre/Action.jpg")
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY,
            "genre", "Action", dry_run=False,
        )

    assert message == "set the poster for %r from the hosted default image" % TITLE
    assert collection.uploaded_bytes == [data]
    assert record.poster_sha256 == hashlib.sha256(data).hexdigest()
    assert (
        tmp_path / ".generated" / "collection-posters" / "genre" / "Action.jpg"
    ).is_file()


async def test_a_local_override_beats_a_default_image_and_makes_no_request(
    tmp_path, config_factory, session
):
    """Constraint 4, pinned on the new branch: the operator's file is read
    first, unconditionally, before `kind`/`key` is consulted."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    local = tmp_path / LIBRARY / TITLE
    local.mkdir(parents=True)
    mine = _jpeg_bytes("blue")
    (local / "poster.jpg").write_bytes(mine)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        raise AssertionError("a local override must not be fetched over")

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY,
            "genre", "Action", dry_run=False,
        )

    assert collection.uploaded_bytes == [mine]
    assert "local file" in message


async def test_a_default_image_that_404s_leaves_the_collection_exactly_as_today(
    tmp_path, config_factory, session
):
    """The fallback that makes the whole feature safe to switch on: a family
    with no matching asset renders today's behaviour -- no upload, no hash
    written, the same 'no poster source' report a `kind=None` collection gets."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        return httpx.Response(404)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY,
            "franchise", "A Franchise Kometa Never Drew", dry_run=False,
        )

    assert message == "no poster source for %r" % TITLE
    assert collection.uploaded_bytes == []
    assert record.poster_sha256 is None


async def test_the_six_original_kinds_are_untouched_by_the_new_branch(
    tmp_path, config_factory, session
):
    """`hosted_poster_url`'s table is not extended by this phase, and a chart
    still goes straight down the old path with no cache file written."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()
    seen = []

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY,
            dry_run=False,
        )

    assert seen == [hosted_poster_url(KIND, KEY)]
    assert not (tmp_path / ".generated").exists()
