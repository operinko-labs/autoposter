"""The operator's own overlay images and font faces, as files (roadmap row 55).

Three routes over two directories `Config` already names -- `overlays_root` and
`fonts_root` -- so an operator can see what is there, add to it and remove what
they added, without a shell on the pod.

What this suite is really pinning is the *containment*, because these are the
only two request-named filesystem paths in this service that are WRITTEN rather
than read. Four separate refusals stand between a request and a byte outside the
root: the name rule (a basename, an allowlisted character set, one extension),
the protected set, the double-``realpath`` check that the target is a regular
file directly under the root, and the ``referenced_by`` refusal that stops an
operator deleting a file the running configuration still names.

Every test points both roots at ``tmp_path``. Nothing here reads
``/app/assets``.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x",
        tmdb_token="x",
        tvdb_apikey="x",
        fanart_apikey="x",
        webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest.fixture
def overlays_root(tmp_path) -> Path:
    root = tmp_path / "overlays"
    root.mkdir()
    return root


@pytest.fixture
def fonts_root(tmp_path) -> Path:
    root = tmp_path / "fonts"
    root.mkdir()
    return root


@pytest.fixture
def config(overlays_root, fonts_root):
    return load_config(EXAMPLE).model_copy(
        update={"overlays_root": overlays_root, "fonts_root": fonts_root}
    )


@pytest_asyncio.fixture
async def app(session_factory, config):
    # No app.state.http: nothing in this row may reach the network, and a
    # missing client makes that a failure rather than a passing test.
    return create_app(config, session_factory, _secrets())


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _named(payload: dict, name: str) -> dict:
    found = [row for row in payload["files"] if row["name"] == name]
    assert len(found) == 1, f"{name} appears {len(found)} times in {payload}"
    return found[0]


# --- the listing ------------------------------------------------------------


async def test_the_overlay_listing_gives_a_name_and_a_size(client, auth_headers, overlays_root):
    (overlays_root / "mine.png").write_bytes(b"x" * 11)

    response = await client.get("/api/files/overlays", headers=auth_headers)

    assert response.status_code == 200
    row = _named(response.json(), "mine.png")
    assert row["size"] == 11
    assert row["referenced_by"] == []
    assert row["protected"] is False


async def test_the_font_listing_gives_a_name_and_a_size(client, auth_headers, fonts_root):
    (fonts_root / "Mine.ttf").write_bytes(b"y" * 7)

    response = await client.get("/api/files/fonts", headers=auth_headers)

    assert response.status_code == 200
    assert _named(response.json(), "Mine.ttf")["size"] == 7


async def test_the_listing_skips_dotfiles_a_cache_and_subdirectories(
    client, auth_headers, overlays_root
):
    """`overlays/sources.py:239` caches a `url:` definition's download under
    `.cache/`. It is this service's own working directory, not the operator's
    file, and neither it nor anything in it is an asset they may manage."""
    (overlays_root / ".cache").mkdir()
    (overlays_root / ".cache" / "cached.png").write_bytes(b"x")
    (overlays_root / ".hidden.png").write_bytes(b"x")
    (overlays_root / "sub").mkdir()
    (overlays_root / "keep.png").write_bytes(b"x")

    payload = (await client.get("/api/files/overlays", headers=auth_headers)).json()

    assert [row["name"] for row in payload["files"]] == ["keep.png"]


async def test_the_listing_skips_a_suffix_this_kind_does_not_manage(
    client, auth_headers, overlays_root, fonts_root
):
    (overlays_root / "notes.txt").write_bytes(b"x")
    (overlays_root / "photo.jpg").write_bytes(b"x")
    (fonts_root / "OFL.txt").write_bytes(b"x")
    (fonts_root / "web.woff2").write_bytes(b"x")

    overlays = (await client.get("/api/files/overlays", headers=auth_headers)).json()
    fonts = (await client.get("/api/files/fonts", headers=auth_headers)).json()

    assert overlays["files"] == []
    assert fonts["files"] == []


async def test_the_listing_is_sorted_by_name(client, auth_headers, overlays_root):
    for name in ("c.png", "a.png", "b.png"):
        (overlays_root / name).write_bytes(b"x")

    payload = (await client.get("/api/files/overlays", headers=auth_headers)).json()

    assert [row["name"] for row in payload["files"]] == ["a.png", "b.png", "c.png"]


async def test_a_bundled_font_is_marked_protected(client, auth_headers, fonts_root):
    (fonts_root / "Comfortaa-Medium.ttf").write_bytes(b"x")

    row = _named(
        (await client.get("/api/files/fonts", headers=auth_headers)).json(),
        "Comfortaa-Medium.ttf",
    )

    assert row["protected"] is True


async def test_an_overlay_named_by_the_running_config_reports_who_names_it(
    client, auth_headers, config, overlays_root
):
    """`artwork.<kind>.overlay_file` (config/impact.py:163) and a badge
    definition's `file:` (overlays/sources.py:187) are the two config values
    that name a file under this root."""
    named = config.artwork.poster.overlay_file
    (overlays_root / named).write_bytes(b"x")

    row = _named((await client.get("/api/files/overlays", headers=auth_headers)).json(), named)

    assert "artwork.poster.overlay_file" in row["referenced_by"]


async def test_a_font_named_by_the_running_config_reports_who_names_it(
    client, auth_headers, config, fonts_root
):
    """Six config values name a font under this root: each art kind's
    `text.font` (impact.py:170), the title card's `episode_text.font` (:174),
    the season poster's `show_title.font` (:181), the two collection
    poster-title blocks (collections/poster_title.py:457) and each badge
    definition's `font:` (overlays/sources.py:128).

    Two files rather than one, because the example config does not point the
    art kinds and the collection poster title at the same face: `artwork.*`
    names the bundled `Comfortaa-Medium.ttf` and both poster-title blocks name
    `Inter-Medium.ttf`. Asserting both readers on one row would only have
    pinned that they happened to agree.
    """
    art_font = config.artwork.poster.text.font
    collection_font = config.collections.poster_title.title.font
    assert art_font != collection_font
    (fonts_root / art_font).write_bytes(b"x")
    (fonts_root / collection_font).write_bytes(b"x")

    payload = (await client.get("/api/files/fonts", headers=auth_headers)).json()

    assert "artwork.poster.text.font" in _named(payload, art_font)["referenced_by"]
    assert (
        "collections.poster_title.title.font" in _named(payload, collection_font)["referenced_by"]
    )


async def test_an_unknown_kind_is_refused(client, auth_headers):
    """`kind` is a Literal path parameter, so an unmanaged directory name is
    refused by the router before a handler chooses a root."""
    response = await client.get("/api/files/secrets", headers=auth_headers)

    assert response.status_code == 422


# --- delete -----------------------------------------------------------------


async def test_deleting_an_uploaded_overlay_removes_it_from_the_listing(
    client, auth_headers, overlays_root
):
    (overlays_root / "mine.png").write_bytes(b"x")

    response = await client.delete("/api/files/overlays/mine.png", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "name": "mine.png"}
    assert not (overlays_root / "mine.png").exists()
    listing = (await client.get("/api/files/overlays", headers=auth_headers)).json()
    assert listing["files"] == []


async def test_a_delete_writes_one_events_row(client, auth_headers, session, overlays_root):
    """The ops rule this repository already applies to every write endpoint: a
    write nobody can find afterwards is not an audited system. The NAME is
    recorded here -- unlike an upload's own file name in `api/manual.py:430`,
    which is withheld -- because by this point it is a name this service
    normalised and already serves in its own listing, not a browser's string."""
    (overlays_root / "mine.png").write_bytes(b"x")

    await client.delete("/api/files/overlays/mine.png", headers=auth_headers)

    rows = (await session.execute(select(EventLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].source == "files"
    assert rows[0].event_type == "asset_file_deleted"
    assert rows[0].payload == {"kind": "overlays", "name": "mine.png"}


async def test_a_protected_font_is_refused(client, auth_headers, fonts_root):
    """`collections/separator_art.py:86` reads this exact file directly, past
    `fonts_root`, and passes it to magick's `-font`."""
    (fonts_root / "Comfortaa-Medium.ttf").write_bytes(b"x")

    response = await client.delete("/api/files/fonts/Comfortaa-Medium.ttf", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "that file ships with this service and cannot be replaced or removed"
    )
    assert (fonts_root / "Comfortaa-Medium.ttf").exists()


@pytest.mark.parametrize("name", ["OFL.txt", "PROVENANCE.md"])
async def test_the_licence_and_provenance_files_are_refused(client, auth_headers, fonts_root, name):
    """Both carry a suffix `SUFFIXES["fonts"]` does not manage, so the protected
    set is checked ahead of the name rule: behind it these would earn a refusal
    about file names, which is true and useless."""
    (fonts_root / name).write_bytes(b"x")

    response = await client.delete(f"/api/files/fonts/{name}", headers=auth_headers)

    assert response.status_code == 409
    assert (fonts_root / name).exists()


async def test_a_file_the_running_config_names_is_refused(
    client, auth_headers, config, overlays_root
):
    named = config.artwork.poster.overlay_file
    (overlays_root / named).write_bytes(b"x")

    response = await client.delete(f"/api/files/overlays/{named}", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "the running configuration still names that file"
    assert (overlays_root / named).exists()


@pytest.mark.parametrize(
    "name",
    [
        "..%2Fescape.png",
        "sub%2Finner.png",
        "%2Fetc%2Fpasswd",
        ".hidden.png",
        "two.dots.png",
        "no-extension",
        "over.png" + "x" * 70,
    ],
)
async def test_a_name_outside_the_rule_is_refused_without_repeating_it(
    client, auth_headers, tmp_path, name
):
    outside = tmp_path / "escape.png"
    outside.write_bytes(b"secret")

    response = await client.delete(f"/api/files/overlays/{name}", headers=auth_headers)

    assert response.status_code in (404, 422)
    body = response.json()["detail"]
    assert "escape" not in body and "passwd" not in body and str(tmp_path) not in body
    assert outside.exists()


async def test_a_symlink_planted_under_the_root_is_refused_on_its_target(
    client, auth_headers, overlays_root, tmp_path
):
    """`overlays/sources.py::_confined` resolves and then checks parentage,
    which accepts exactly this. The `realpath` idiom does not."""
    target = tmp_path / "outside.png"
    target.write_bytes(b"secret")
    try:
        (overlays_root / "link.png").symlink_to(target)
    except OSError:
        # OSError alone, not `(OSError, NotImplementedError)`: ruff's formatter
        # targets this project's 3.14 floor, where PEP 758 drops the
        # parentheses -- a spelling nothing else in this repository uses. The
        # second class buys nothing anyway; `Path.symlink_to` raises OSError on
        # every platform this suite runs on.
        pytest.skip("this filesystem does not allow the test to plant a symlink")

    response = await client.delete("/api/files/overlays/link.png", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "there is no such file in that directory"
    assert target.exists()
    assert (overlays_root / "link.png").is_symlink()


async def test_a_directory_is_refused(client, auth_headers, overlays_root):
    (overlays_root / "sub.png").mkdir()

    response = await client.delete("/api/files/overlays/sub.png", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "there is no such file in that directory"
    assert (overlays_root / "sub.png").is_dir()


async def test_an_absent_name_is_a_fixed_sentence(client, auth_headers):
    response = await client.delete("/api/files/overlays/gone.png", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "there is no such file in that directory"


async def test_a_refusal_never_carries_a_root_path(client, auth_headers, overlays_root, fonts_root):
    """Row 213: the listing serves basenames, and no refusal serves a path at
    all -- not the root, not a resolved target, not a `str(exc)`."""
    for url in (
        "/api/files/overlays/gone.png",
        "/api/files/fonts/gone.ttf",
        "/api/files/overlays/..%2Fx.png",
    ):
        body = (await client.delete(url, headers=auth_headers)).text
        assert str(overlays_root) not in body
        assert str(fonts_root) not in body
