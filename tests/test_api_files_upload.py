"""Uploading an overlay image or a font face (roadmap row 55).

The bytes arrive from a browser, so this file is mostly about what is NOT
trusted. Three things row 123 established and this reuses verbatim: the cap is
enforced while the body is still ARRIVING (`api/manual.py::_budgeted_receive`)
rather than after Starlette has spooled it, the part's `Content-Type` is a
client claim that is never read, and every refusal is a fixed sentence.

One thing row 123 could sidestep and this cannot: the file NAME. Manual mode's
stored name comes from `manual_override_target`, so the browser's string was
read for nothing; here the operator's chosen name is the handle a config value
later writes, so it is read, normalised, and refused with a sentence that names
the RULE rather than the name.

And one rule that is this row's own: an existing name is refused (409) rather
than overwritten. The artwork stage hashes overlay and font BYTES into every
render fingerprint (`config/impact.py:163,170,174,181`) while the badge stage
hashes only the shipped manifest and the definitions' fields
(`badges/compose.py:259-262`), so an in-place replacement storms one and
freezes the other. `test_an_upload_moves_no_render_version` and its three
neighbours are the pin that says so with the real functions.

The multipart envelope is hand-built rather than handed to httpx's `files=`
for the reason `tests/test_api_manual_upload.py` gives: the body has to be
yielded lazily so the number of bytes the application actually consumed can be
asserted.
"""

import io
import os
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from autoposter.api.candidates import PICK_MAX_BYTES
from autoposter.badges.compose import badge_fingerprint
from autoposter.config.loader import RENDER_ART_KINDS, render_version_for
from autoposter.db.models import EventLog
from autoposter.plex.client import ResolvedItem
from autoposter.render.pipeline import gather_fingerprint_inputs

# Sibling fixtures, imported by BARE name: this is how CI's own `pytest
# /app/tests/...` invocation loads them, and `from tests.` would fail there
# while passing from the repository root. Same shape as
# tests/test_config_safety.py's import of test_api_config_editor.
from test_api_files import (  # noqa: F401
    _named,
    app,
    auth_headers,
    client,
    config,
    fonts_root,
    overlays_root,
)

BOUNDARY = "----autoposterfilestest"
MULTIPART = {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}


def _png(size=(8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (10, 20, 30, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(size=(8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _truncated_png() -> bytes:
    """A PNG whose header is valid and whose IDAT stream stops short of what it
    promises -- job 40478's shape.

    Noise rather than a flat fill, because a flat 64x64 compresses to an IDAT
    too short to cut: the point of this fixture is a file `Image.open` accepts
    and reports as PNG, so only `_validate_image`'s full-pixel `load()` can
    refuse it.
    """
    buffer = io.BytesIO()
    Image.frombytes("RGB", (64, 64), os.urandom(64 * 64 * 3)).save(buffer, format="PNG")
    return buffer.getvalue()[:-1000]


SVG_BYTES = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'


# A real TrueType face the repository already tracks, for
# `tests/test_golden.py`'s byte comparison. Reused rather than vendoring a
# second one: another face would need its own licence file beside it, which is
# exactly what `PROVENANCE.md` exists to say. Uploaded under a DIFFERENT name in
# every test below -- `Comfortaa-Medium.ttf` itself is in `PROTECTED`.
FACE_BYTES = (Path(__file__).parent / "fixtures" / "golden" / "Comfortaa-Medium.ttf").read_bytes()


def _envelope(payload: bytes, filename: str, field: str = "file") -> bytes:
    """One file part, spelled out. `Content-Type` is deliberately a lie in some
    tests below: this endpoint reads it for nothing."""
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    return head + payload + f"\r\n--{BOUNDARY}--\r\n".encode()


class _CountingBody:
    """A request body yielded chunk by chunk, counting what was consumed.

    httpx's ASGITransport pulls one chunk per ASGI `receive()`, so `sent` is the
    number of bytes the application actually read -- the only way to tell a
    streaming cap from a cap applied after the fact.
    """

    def __init__(self, payload: bytes, filename: str, chunk: int = 64 * 1024):
        self._body = _envelope(payload, filename)
        self._chunk = chunk
        self.sent = 0

    async def __aiter__(self):
        for start in range(0, len(self._body), self._chunk):
            piece = self._body[start : start + self._chunk]
            self.sent += len(piece)
            yield piece


def _upload(client, kind: str, headers, content, extra_headers=None):
    return client.post(
        f"/api/files/{kind}",
        content=content,
        headers={**headers, **MULTIPART, **(extra_headers or {})},
    )


# --- the happy path ---------------------------------------------------------


async def test_an_uploaded_png_appears_in_the_listing(client, auth_headers, overlays_root):
    response = await _upload(client, "overlays", auth_headers, _envelope(_png(), "my-overlay.png"))

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "stored", "name": "my-overlay.png"}
    assert (overlays_root / "my-overlay.png").read_bytes() == _png()
    listing = (await client.get("/api/files/overlays", headers=auth_headers)).json()
    assert _named(listing, "my-overlay.png")["referenced_by"] == []


async def test_an_uploaded_face_appears_in_the_listing(client, auth_headers, fonts_root):
    response = await _upload(client, "fonts", auth_headers, _envelope(FACE_BYTES, "Mine.ttf"))

    assert response.status_code == 200
    assert (fonts_root / "Mine.ttf").read_bytes() == FACE_BYTES
    listing = (await client.get("/api/files/fonts", headers=auth_headers)).json()
    assert _named(listing, "Mine.ttf")["size"] == len(FACE_BYTES)


async def test_the_stored_file_is_readable_by_the_render_path(client, auth_headers, overlays_root):
    """0644, matching what the init container's `cp` leaves behind, so the
    listing does not show two classes of file. `mkstemp` gives 0600 and the
    handler widens it deliberately -- see the handler's docstring."""
    await _upload(client, "overlays", auth_headers, _envelope(_png(), "mode.png"))

    assert oct(os.stat(overlays_root / "mode.png").st_mode & 0o777) == "0o644"


async def test_an_upload_writes_one_events_row(client, auth_headers, session):
    await _upload(client, "overlays", auth_headers, _envelope(_png(), "audited.png"))

    rows = (await session.execute(select(EventLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].source == "files"
    assert rows[0].event_type == "asset_file_uploaded"
    assert rows[0].payload == {"kind": "overlays", "name": "audited.png"}


async def test_nothing_is_left_staged_under_the_root(client, auth_headers, overlays_root):
    await _upload(client, "overlays", auth_headers, _envelope(_png(), "clean.png"))
    await _upload(client, "overlays", auth_headers, _envelope(b"not an image", "bad.png"))

    assert sorted(entry.name for entry in overlays_root.iterdir()) == ["clean.png"]


# --- refusals ---------------------------------------------------------------


async def test_a_second_upload_of_the_same_name_is_refused(client, auth_headers, overlays_root):
    """The whole storm-vs-freeze decision, as one status code. See the module
    docstring: the two stages disagree about what an overwrite means, so the
    operator says which they meant with a delete.

    The stored bytes are asserted as well as the status, because that is what
    separates `os.link` from `os.replace`: a handler that replaced first and
    reported afterwards would still be able to answer 409."""
    first = _png(size=(8, 8))
    await _upload(client, "overlays", auth_headers, _envelope(first, "same.png"))

    response = await _upload(
        client, "overlays", auth_headers, _envelope(_png(size=(16, 16)), "same.png")
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "a file of that name is already there; delete it first"
    assert (overlays_root / "same.png").read_bytes() == first


@pytest.mark.parametrize("name", ["Comfortaa-Medium.ttf", "OFL.txt"])
async def test_a_protected_name_cannot_be_uploaded(client, auth_headers, fonts_root, name):
    """Two members of `PROTECTED["fonts"]`, and the second is why the set is
    checked before the name rule rather than after it: `OFL.txt` carries a
    suffix `SUFFIXES["fonts"]` does not manage, so behind the name rule it
    would earn the refusal about ASCII letters and extensions -- true, and
    useless to the operator who has just been told this file ships with the
    service."""
    response = await _upload(client, "fonts", auth_headers, _envelope(FACE_BYTES, name))

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "that file ships with this service and cannot be replaced or removed"
    )
    assert sorted(entry.name for entry in fonts_root.iterdir()) == []


@pytest.mark.parametrize(
    "payload, name",
    [
        (b"not an image at all", "broken.png"),
        (_jpeg(), "secretly-a-jpeg.png"),
        (SVG_BYTES, "secretly-an-svg.png"),
        (_truncated_png(), "truncated.png"),
    ],
    ids=["undecodable", "jpeg", "svg", "truncated"],
)
async def test_bytes_that_are_not_a_png_are_refused(
    client, auth_headers, overlays_root, payload, name
):
    """Four shapes, one for each check `_verify` makes, so no check can be
    removed without one of these going red.

    `undecodable` and `svg` are refused by the `Image.open` guard -- and the
    svg case is the first gap `_validate_image` leaves open by design, since it
    returns early for anything `_looks_like_svg` recognises
    (`render/artwork_fetch.py:129-130`) and would therefore pass these bytes.
    `jpeg` is the second gap: `_validate_image` accepts any format Pillow
    decodes, and only the format assertion refuses it. `truncated` is the one
    the other three cannot reach -- a header Pillow opens and reports as PNG
    over an IDAT stream that stops early, which is job 40478's shape and which
    only `_validate_image`'s full-pixel `load()` refuses."""
    response = await _upload(client, "overlays", auth_headers, _envelope(payload, name))

    assert response.status_code == 422
    assert response.json()["detail"] == "the uploaded file is not one this service can use"
    assert list(overlays_root.iterdir()) == []


async def test_bytes_that_are_not_a_face_are_refused(client, auth_headers, fonts_root):
    response = await _upload(client, "fonts", auth_headers, _envelope(_png(), "fake.ttf"))

    assert response.status_code == 422
    assert response.json()["detail"] == "the uploaded file is not one this service can use"
    assert list(fonts_root.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    [
        "../escape.png",
        "sub/inner.png",
        "/etc/passwd.png",
        ".hidden.png",
        "two.dots.png",
        "noextension",
        "n" * 70 + ".png",
        "n\u00e4me.png",
    ],
)
async def test_a_name_outside_the_rule_is_refused_without_repeating_it(
    client, auth_headers, overlays_root, tmp_path, name
):
    response = await _upload(client, "overlays", auth_headers, _envelope(_png(), name))

    assert response.status_code == 422
    body = response.json()["detail"]
    assert body == (
        "the file name must be ASCII letters, digits, dashes and underscores "
        "plus one extension this directory accepts, and at most 64 characters"
    )
    assert "escape" not in body and "passwd" not in body
    assert str(tmp_path) not in body
    assert list(overlays_root.iterdir()) == []
    assert not (tmp_path / "escape.png").exists()


@pytest.mark.parametrize(
    "kind, name",
    [
        ("overlays", "web.woff2"),
        ("overlays", "photo.jpg"),
        ("fonts", "face.woff"),
        ("fonts", "art.png"),
    ],
)
async def test_a_suffix_this_directory_does_not_manage_is_refused(client, auth_headers, kind, name):
    response = await _upload(client, kind, auth_headers, _envelope(_png(), name))

    assert response.status_code == 422


@pytest.mark.parametrize(
    "overshoot",
    [1024, 2 * 1024 * 1024],
    ids=["the-exact-cap-in-the-copy-loop", "the-budget-on-receive"],
)
async def test_a_body_past_the_cap_is_refused_while_it_is_still_arriving(
    client, auth_headers, overlays_root, overshoot
):
    """The difference between a cap and a promise: `sent` is what the
    application actually read, so a cap applied after Starlette had spooled the
    whole body would show the full length here.

    Two overshoots, because there are two ceilings and each one answers alone.
    A kibibyte over the cap is still inside `UPLOAD_ENVELOPE_BYTES` of it, so
    `_budgeted_receive` hands the whole body over and the running total in the
    copy loop is what refuses it. Two mebibytes over is past the budget, so the
    read stops at the wire -- and that case is the one the `sent` bound
    discriminates, since the copy loop alone would have consumed a megabyte
    more than this allows."""
    body = _CountingBody(b"\x89PNG\r\n\x1a\n" + b"\x00" * (PICK_MAX_BYTES + overshoot), "huge.png")

    response = await _upload(client, "overlays", auth_headers, body)

    assert response.status_code == 413
    assert response.json()["detail"] == "the upload exceeds the size cap"
    assert body.sent <= PICK_MAX_BYTES + 1024 * 1024
    assert list(overlays_root.iterdir()) == []


@pytest.mark.parametrize(
    "extra_headers",
    [None, {"Content-Type": "multipart/form-data"}],
    ids=["a-boundary-the-body-contradicts", "no-boundary-at-all"],
)
async def test_a_malformed_multipart_body_is_refused(client, auth_headers, extra_headers):
    """Two shapes, and they come out of two different libraries.

    Without a boundary, Starlette's own parser raises `MultiPartException` and
    `Request._get_form` re-raises it as `HTTPException(400, exc.message)`
    (`starlette/requests.py:292`) -- the case `tests/test_api_manual_upload.py`
    covers. WITH a boundary the body then contradicts, the error comes from
    `python_multipart` instead, which Starlette does not convert at all; that
    one is an unhandled 500 unless the handler catches it itself. Both messages
    carry offsets and byte values, so both are answered with the fixed
    sentence."""
    response = await _upload(
        client, "overlays", auth_headers, b"not multipart at all", extra_headers
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "the upload is not a usable multipart form"


async def test_a_body_with_no_file_part_is_refused(client, auth_headers):
    response = await _upload(client, "overlays", auth_headers, f"--{BOUNDARY}--\r\n".encode())

    assert response.status_code == 422
    assert response.json()["detail"] == "the upload has no file part"


async def test_a_part_with_an_empty_file_name_is_refused(client, auth_headers, overlays_root):
    """Starlette builds an `UploadFile` for any part whose `Content-Disposition`
    carries a `filename` parameter at all, the empty string included, so the
    `isinstance` check alone would pass this to the name rule and the operator
    would be told about ASCII letters and extensions. "there is no file part
    here" is the truer answer, and it is one row 123 already serves."""
    response = await _upload(client, "overlays", auth_headers, _envelope(_png(), ""))

    assert response.status_code == 422
    assert response.json()["detail"] == "the upload has no file part"
    assert list(overlays_root.iterdir()) == []


async def test_an_empty_file_part_is_refused(client, auth_headers, overlays_root):
    """The sentence matters as much as the status: with the zero-length check
    gone these bytes reach `_verify`, which refuses them too -- as unusable
    content rather than as an absent file, which is a different thing to tell
    an operator whose browser sent an empty part."""
    response = await _upload(client, "overlays", auth_headers, _envelope(b"", "empty.png"))

    assert response.status_code == 422
    assert response.json()["detail"] == "the upload has no file part"
    assert list(overlays_root.iterdir()) == []


async def test_no_refusal_body_carries_a_root_path(client, auth_headers, overlays_root):
    for payload, name in ((b"junk", "bad.png"), (_png(), "../x.png"), (b"", "e.png")):
        body = (await _upload(client, "overlays", auth_headers, _envelope(payload, name))).text
        assert str(overlays_root) not in body


# --- the storm guard (Law B) ------------------------------------------------
#
# Three fingerprints, and an upload has to leave all three where they were.
# These use the REAL functions -- `render_version_for`,
# `gather_fingerprint_inputs` and `badge_fingerprint` -- not a stand-in that
# would agree with a bug.


def _item() -> ResolvedItem:
    return ResolvedItem(
        rating_key="rk1",
        library="Movies",
        kind="movie",
        title="A Movie",
        year=1999,
        season_number=None,
        episode_number=None,
        root_folder="A Movie (1999)",
        file_path=None,
        art_url=None,
        tmdb_id=550,
        tvdb_id=660,
        imdb_id="tt0137523",
    )


@pytest.mark.parametrize("art_kind", RENDER_ART_KINDS)
async def test_an_upload_moves_no_render_version(client, auth_headers, config, art_kind):
    """`config/loader.py:153-161` puts the two roots into `_shared_render_inputs`,
    a literal member of all four payloads, and `render_version_for` is element
    0 of every stored fingerprint. An upload writes no config value, so every
    one of the four must be byte-identical across it."""
    before = render_version_for(art_kind, config)

    response = await _upload(client, "overlays", auth_headers, _envelope(_png(), "new.png"))

    assert response.status_code == 200, response.text
    assert render_version_for(art_kind, config) == before


async def test_an_upload_moves_no_asset_hash(client, auth_headers, config, overlays_root):
    """`render/pipeline.py::gather_fingerprint_inputs` is the single definition
    of what goes into a fingerprint besides the source URL and the base image;
    `config/impact.py:163,170,174,181` sha-256s the overlay and the fonts by
    CONTENT. A file no config value names contributes nothing to either."""
    before = await gather_fingerprint_inputs(config, _item(), "poster")

    await _upload(client, "overlays", auth_headers, _envelope(_png(), "unnamed.png"))

    assert await gather_fingerprint_inputs(config, _item(), "poster") == before


async def test_an_upload_moves_no_badge_fingerprint(client, auth_headers, config):
    """`badges/compose.py:259-262` hashes the shipped badge MANIFEST and the
    definitions' FIELDS -- nothing under `overlays_root`. The pin exists
    because that is precisely why an in-place overwrite would FREEZE rather
    than storm, and why this route refuses one."""
    args = ("f" * 64, "poster", {"rating": "8.1"}, "m" * 64, config.badges.all_definitions())
    before = badge_fingerprint(*args)

    await _upload(client, "overlays", auth_headers, _envelope(_png(), "quiet.png"))

    assert badge_fingerprint(*args) == before


async def test_naming_the_uploaded_file_in_a_config_value_does_move_it(
    client, auth_headers, config, overlays_root
):
    """The other half, without which the three pins above are vacuous: they
    would pass just as well against a fingerprint function that ignored these
    files entirely. Naming the new file in `artwork.poster.overlay_file` MUST
    move the poster's asset hashes -- and that is the moment the operator asked
    for, not the upload."""
    before = await gather_fingerprint_inputs(config, _item(), "poster")

    await _upload(client, "overlays", auth_headers, _envelope(_png(), "named.png"))
    after_config = config.model_copy(
        update={
            "artwork": config.artwork.model_copy(
                update={
                    "poster": config.artwork.poster.model_copy(
                        update={"overlay_file": "named.png", "add_overlay": True}
                    )
                }
            )
        }
    )

    assert await gather_fingerprint_inputs(after_config, _item(), "poster") != before
