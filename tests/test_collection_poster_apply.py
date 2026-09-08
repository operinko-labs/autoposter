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

from autoposter.collections.default_images import default_image_url
from autoposter.collections.posters import (
    DEFINITION_POSTER_SOURCE,
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

# Row 222. An address that is obviously nobody's: `.invalid` is reserved by
# RFC 2606 and can never resolve, and `resolve_host` is patched out below so
# nothing ever asks. The guard's own suite makes the same call
# (tests/test_fetch_guard.py's module docstring).
POSTER_URL = "https://posters.invalid/dc-extended-universe.jpg"
# A public literal, so `_address_refusal` passes it. TEST-NET (192.0.2.0/24)
# would NOT do -- Python's ipaddress marks the documentation ranges private.
PUBLIC_ADDRESS = "93.184.216.34"
# What an SSRF attempt through a definition's poster_url looks like, with a
# credential and a signed parameter attached: every one of these three
# fragments must be absent from every action string and every log record.
CREDENTIAL_URL = (
    "http://operator:hunter2@169.254.169.254/latest/meta-data/?token=hunter2"
)


@pytest.fixture
def public_resolver(monkeypatch):
    """Every name in this module's row-222 tests resolves to one public
    address, without a lookup. ``resolve_host`` is a module attribute for
    exactly this reason (``net/guard.py:133-143``)."""
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda host, port: [PUBLIC_ADDRESS]
    )


def _image_handler(data, seen):
    """A 200 that is a real image AND says so. ``store_body`` refuses a body
    whose Content-Type is outside the allowlist before a byte is kept
    (``net/guard.py:225-232``), so a header-less response would be refused
    for the wrong reason."""

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            200, content=data, headers={"content-type": "image/jpeg"}
        )

    return handler


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
    new branch in front of it could introduce without any test noticing.

    Driven by ``content_rating`` rather than by the file's ``KIND`` since
    roadmap row 252: ``chart`` moved to ``default_images.FAMILIES`` and is no
    longer one of the kinds this test speaks for."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    fetched: list[str] = []

    async def handler(request):
        fetched.append(str(request.url))
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        await apply_poster(
            session, http, config, _FakeCollection(), record, LIBRARY,
            "content_rating", "17", dry_run=False,
        )

    assert fetched == [hosted_poster_url("content_rating", "17")]
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


@pytest.mark.parametrize(
    "kind,key,poster_url,expected,caches",
    [
        ("award_static", "oscars:best_picture_winner", None,
         hosted_poster_url("award_static", "oscars:best_picture_winner"), False),
        ("award_year", "oscars:2026", None,
         hosted_poster_url("award_year", "oscars:2026"), False),
        ("content_rating", "17", None, hosted_poster_url("content_rating", "17"), False),
        ("content_rating_other", "other", None,
         hosted_poster_url("content_rating_other", "other"), False),
        ("separator", "orig:content_rating", None,
         hosted_poster_url("separator", "orig:content_rating"), False),
        (KIND, KEY, None, default_image_url(KIND, KEY), True),
        (TMDB_PROFILE_KIND, PROFILE_PATH, None, PROFILE_URL, False),
        # Roadmap row 222's rung, on the one kind that would otherwise take
        # the CACHED rung: the definition's URL outranks it, so the fetch is
        # the operator's address and nothing is written under `.generated/`.
        (KIND, KEY, POSTER_URL, POSTER_URL, False),
    ],
)
async def test_every_poster_source_takes_the_rung_its_inputs_name(
    kind, key, poster_url, expected, caches, tmp_path, config_factory, session,
    public_resolver,
):
    """One case per source ``apply_poster`` can be called with, and the rung
    each one takes.

    This was ``test_the_seven_poster_kinds_each_take_the_rung_their_kind_names``,
    and before that ``test_the_six_original_kinds_are_untouched_by_the_new_branch``.
    It is renamed again for the same reason it was renamed before: the
    regression it guards -- a new branch in front of the dispatch quietly
    swallowing a source -- is a property of the WHOLE dispatch, and roadmap row
    222 makes the dispatch's input a ``kind`` plus a ``poster_url`` rather than
    a ``kind`` alone. The eighth row is the new rung, deliberately paired with
    the kind that takes the cached rung when no URL is given (the row above
    it): the two rows differ in one input and disagree about both the URL
    fetched and whether anything is cached, which is what makes the ordering
    falsifiable rather than merely covered.

    The URL each rung produces is asserted against the pure function that owns
    it, so a change to a path is a change in one place and this test follows
    it; ``caches`` is the half that tells the two file-producing rungs apart.
    """
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()
    seen: list[str] = []

    async with _client(_image_handler(data, seen)) as http:
        await apply_poster(
            session, http, config, collection, record, LIBRARY, kind, key,
            dry_run=False, poster_url=poster_url,
        )

    assert seen == [expected]
    assert (tmp_path / ".generated").exists() is caches


async def test_a_chart_poster_is_cached_and_the_second_pass_fetches_nothing(
    tmp_path, config_factory, session
):
    """Roadmap row 252, the request it exists to remove. ``chart`` is a
    ``default_images`` family now, so the image is written under
    ``.generated/collection-posters/chart/`` on the first pass and READ from
    there on the second -- where before, every pass that reached the poster
    block re-downloaded roughly 480 KB from the public Default-Images CDN and
    only the sha256 compare stopped the re-upload.

    The cache stem is OUR key percent-encoded with nothing safe
    (``default_images._cache_paths``), which is why the file on disk is
    ``IMDb%20Top%20250.jpg`` and not ``IMDb Top 250.jpg``."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()
    seen: list[str] = []

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        first = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY,
            dry_run=False,
        )
        second = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY,
            dry_run=False,
        )

    assert first == "set the poster for %r from the hosted default image" % TITLE
    assert second is None, "the bytes were unchanged, so nothing was re-uploaded"
    assert seen == [default_image_url(KIND, KEY)], "one fetch across two passes"
    assert collection.uploaded_bytes == [data]
    assert (
        tmp_path / ".generated" / "collection-posters" / "chart"
        / "IMDb%20Top%20250.jpg"
    ).is_file()


async def test_a_chart_miss_writes_the_marker_and_is_not_re_fetched(
    tmp_path, config_factory, session
):
    """The other half of row 252, and the reason ``hosted_poster_url``'s own
    ``chart`` branch was DELETED rather than left as an unreachable-on-hit
    fallback: with the kind in ``FAMILIES`` and that branch still present, a
    proven 404 would be fetched TWICE in a single pass -- once by
    ``ensure_default_image`` writing the ``.miss`` marker, once by the hosted
    rung immediately after -- which is the opposite of the request this row
    removes. So ``seen`` is the assertion that matters: one URL, once, for a
    miss across two passes.

    The disclosure that comes with it is the twelve other families'
    disclosure verbatim (``default_images``' module docstring): a PROVEN
    absence is sticky, and deleting
    ``<assets_root>/.generated/collection-posters/chart/`` is how it is
    re-checked. A chart miss is impossible for the eight keys that ship today
    -- all are upstream's own mapping names and row 146 verified the five TMDb
    URLs live 200 before the keys were chosen -- so the marker guards a future
    key, not a live population. Unproven failures (429/5xx/timeout/non-image)
    write nothing and are retried, which
    ``test_collection_default_images.py::test_a_transient_failure_leaves_no_marker_and_is_retried``
    already pins for the family machinery."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    record = await _record(session)
    collection = _FakeCollection()
    seen: list[str] = []

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(404)

    async with _client(handler) as http:
        first = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY,
            dry_run=False,
        )
        second = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY,
            dry_run=False,
        )

    assert first == "no poster source for %r" % TITLE
    assert second == "no poster source for %r" % TITLE
    assert seen == [default_image_url(KIND, KEY)], "no second fetch, in either pass"
    assert collection.uploaded_bytes == []
    assert record.poster_sha256 is None
    assert (
        tmp_path / ".generated" / "collection-posters" / "chart"
        / "IMDb%20Top%20250.miss"
    ).is_file()


# --- row 222: the definition's own poster URL -------------------------------


async def test_a_local_file_still_wins_over_the_definitions_poster_url(
    tmp_path, config_factory, session, public_resolver
):
    """The cell's own stated priority, and the half of it that is a decision
    rather than a convenience: a file on disk still wins, because an operator
    who put one there meant it -- and `api/manual.py::install_collection_poster`
    writes THROUGH that rung, so this is also what happens when the operator
    used the Collections page's install form and later added a poster_url."""
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
            session, http, config, collection, record, LIBRARY, KIND, KEY,
            dry_run=False, poster_url=POSTER_URL,
        )

    assert collection.uploaded_bytes == [mine]
    assert "local file" in message


async def test_the_poster_url_is_never_fetched_through_fetch_poster(
    tmp_path, config_factory, session, public_resolver, monkeypatch
):
    """`posters.fetch_poster` is a bare `http.get` with no scheme allowlist, no
    address check and no redirect control, on the shared client -- safe only
    while every URL it sees was built by this repository from
    DEFAULT_IMAGES_BASE or TMDb's CDN, which `net/guard.py:258-262` names it by
    name for. An operator-typed URL breaks that invariant outright, so this
    rung must go through `guarded_download` instead.

    The kind and key here are a real Default-Images family, so an
    implementation that fell through to a later rung would call the exploding
    fetcher and fail loudly rather than silently passing."""

    async def explode(http, url):
        raise AssertionError("the definition's poster URL must not reach fetch_poster")

    monkeypatch.setattr("autoposter.collections.posters.fetch_poster", explode)

    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()
    seen = []

    async with _client(_image_handler(data, seen)) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY,
            dry_run=False, poster_url=POSTER_URL,
        )

    assert seen == [POSTER_URL]
    assert message == "set the poster for %r from %s" % (TITLE, DEFINITION_POSTER_SOURCE)
    assert collection.uploaded_bytes == [data]


async def test_a_poster_url_pointing_at_a_private_address_is_refused_unrequested(
    tmp_path, config_factory, session
):
    """The security invariant, asserted the way `tests/test_fetch_guard.py`
    asserts it: the REQUEST LOG first. "It raised" would also be true of an
    implementation that fetched the metadata service and complained
    afterwards, by which time the credentials are already in this process.

    No resolver patch: 169.254.169.254 is a literal, so getaddrinfo answers
    out of the string and nothing is looked up."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    record = await _record(session)
    collection = _FakeCollection()
    seen = []

    async def handler(request):
        seen.append(str(request.url))
        raise AssertionError("the guard must refuse before any request is made")

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, None, None,
            dry_run=False, poster_url=CREDENTIAL_URL,
        )

    assert seen == []
    assert collection.uploaded_bytes == []
    assert record.poster_sha256 is None
    assert "link-local" in message


async def test_a_refused_poster_url_reaches_neither_the_action_nor_the_log(
    tmp_path, config_factory, session, caplog
):
    """Roadmap row 213, on the one value in this module that an operator
    typed. `apply_poster`'s return string is served: it reaches
    `collections/service.py`'s `actions` list, is rendered in the run report
    and is logged. The guard's own refusal carries the reason and the hop and
    never the URL (`net/guard.py:79-84`), which is what makes it safe to hand
    back verbatim -- and the value itself must appear nowhere."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        raise AssertionError("the guard must refuse before any request is made")

    with caplog.at_level("DEBUG"):
        async with _client(handler) as http:
            message = await apply_poster(
                session, http, config, collection, record, LIBRARY, None, None,
                dry_run=False, poster_url=CREDENTIAL_URL,
            )

    logged = "\n".join(record_.getMessage() for record_ in caplog.records)
    for secret in ("hunter2", "169.254", "operator:", "token="):
        assert secret not in message, message
        assert secret not in logged, logged
    assert message.strip()


async def test_a_transport_failure_fetching_the_poster_url_reaches_neither_the_action_nor_the_log(
    tmp_path, config_factory, session, caplog, public_resolver
):
    """Task 2 review I-2, the sibling of
    `test_a_refused_poster_url_reaches_neither_the_action_nor_the_log` on the
    OTHER except clause (`posters.py:539-549`, `except httpx.HTTPError`). That
    branch's own comment names the danger: httpx's exception messages -- and
    the traceback `exc_info` would attach -- can embed the full request URL.
    Nothing exercised it: every fake elsewhere either returns a 200 or raises
    `AssertionError` on a request the guard is expected never to make.

    The `MockTransport` handler here raises `httpx.ConnectError` carrying the
    URL in ITS OWN message, deliberately -- so a mutation that formats `exc`
    into the returned string, or adds `exc_info=True` to the `logger.warning`
    call, is exactly what turns this test red. The second half needs its own
    assertion (Task 3 review I-2): `caplog.records[...].getMessage()` never
    renders what `exc_info` attaches, so the explicit `record_.exc_info is
    None` check below is what actually catches that mutant."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        raise httpx.ConnectError("failed to connect to %s" % POSTER_URL)

    with caplog.at_level("DEBUG"):
        async with _client(handler) as http:
            message = await apply_poster(
                session, http, config, collection, record, LIBRARY, None, None,
                dry_run=False, poster_url=POSTER_URL,
            )

    logged = "\n".join(record_.getMessage() for record_ in caplog.records)
    for secret in ("posters.invalid", "dc-extended-universe"):
        assert secret not in message, message
        assert secret not in logged, logged
    # `getMessage()` never renders what `exc_info` attaches -- only a
    # `Formatter` does -- so the loop above cannot catch `exc_info=True`
    # being added to the `logger.warning` call. Assert on the record
    # directly (Task 3 review I-2).
    assert all(record_.exc_info is None for record_ in caplog.records)
    assert message.strip()
    assert collection.uploaded_bytes == []
    assert record.poster_sha256 is None


async def test_an_unchanged_poster_url_image_uploads_nothing_on_a_second_pass(
    tmp_path, config_factory, session, public_resolver
):
    """The hash-compare the rung inherits by being a rung: `poster_sha256`
    already means "the bytes we last uploaded", so an image that has not
    changed behind a stable URL is fetched, hashed, found equal and not
    re-uploaded. That is what keeps a definition that adopts the field from
    re-uploading a poster on every pass that reaches the poster block."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()
    seen = []

    async with _client(_image_handler(data, seen)) as http:
        first = await apply_poster(
            session, http, config, collection, record, LIBRARY, None, None,
            dry_run=False, poster_url=POSTER_URL,
        )
        second = await apply_poster(
            session, http, config, collection, record, LIBRARY, None, None,
            dry_run=False, poster_url=POSTER_URL,
        )

    assert first == "set the poster for %r from %s" % (TITLE, DEFINITION_POSTER_SOURCE)
    assert second is None
    assert collection.uploaded_bytes == [data]
    assert record.poster_sha256 == hashlib.sha256(data).hexdigest()
    assert seen == [POSTER_URL, POSTER_URL]


async def test_a_poster_url_body_that_is_not_an_image_is_rejected_unuploaded(
    tmp_path, config_factory, session, public_resolver
):
    """A Content-Type is the other end's claim and only a decoder settles it --
    the same argument `api/candidates._prepare_jpeg` makes for the installed
    image. Uploading a non-image would be worse than uploading nothing: it
    would be hashed and recorded, so a later pass would never retry it."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    record = await _record(session)
    collection = _FakeCollection()
    seen = []

    async with _client(_image_handler(b"<html>not an image</html>", seen)) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY, None, None,
            dry_run=False, poster_url=POSTER_URL,
        )

    assert seen == [POSTER_URL]
    assert collection.uploaded_bytes == []
    assert record.poster_sha256 is None
    assert "did not decode as an image" in message
