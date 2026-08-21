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

from autoposter.collections.posters import _write_and_upload, apply_poster
from autoposter.db.models import ManagedCollection

LIBRARY = "Movies"
TITLE = "IMDb Top 250"
KIND = "chart"
KEY = "IMDb Top 250"


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
