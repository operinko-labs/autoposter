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

import os
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.api.files import OutsideRoot, contained, normalised_name
from autoposter.app import create_app
from autoposter.config.loader import build_config, load_config, read_config_document
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog
from autoposter.overlays.schema import OverlayDefinition

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


# --- the reference enumeration, reader by reader ------------------------------

# `status` is the shipped family whose every definition names `Inter-Medium.ttf`
# (`overlays/families.py:934`). Any of the eleven would do; this one is picked
# because its four definitions make the "one sentence per naming path" dedupe
# visible in the assertion below rather than only in the implementation.
FAMILY = "status"
FAMILY_FONT = "Inter-Medium.ttf"


def _with_definitions(config):
    """`config` carrying one badge definition per reachability rung.

    Three rungs and no more, because `overlays/sources.py:175-215` has three
    that land under these roots: an explicit `file:`, the name-keyed
    `<name>.png` a definition naming no `file`/`builtin`/`url` falls back to,
    and `resolve_font_path`'s `fonts_root`-first `font:` rung (`:127-128`).
    The example config carries no definition at all, so nothing else in this
    suite executes any of them.
    """
    definitions = [
        OverlayDefinition(name="ribbon", file="ribbon-art.png"),
        OverlayDefinition(name="sticker"),
        OverlayDefinition(name="labelled", font="Badge-Face.ttf"),
    ]
    return config.model_copy(
        update={"badges": config.badges.model_copy(update={"definitions": definitions})}
    )


@pytest.fixture
def library_family_config(config):
    """`config` with one library drawing `status` and nothing else naming its
    face.

    Built through `build_config` rather than assembled by hand, so the
    `libraries:` block runs the load-time rules a real config runs -- the key
    must be one of `collections.libraries`, and the merged `BadgesConfig` must
    pass `_check_families`.

    Both collection poster-title faces are repointed, and that is what makes
    the pair of tests below discriminating: every shipped family names
    `Inter-Medium.ttf`, which `collections.poster_title.title.font` also names
    by default (`config/schema.py:632`), so against the stock example the
    refusal would fire through the collections reader whatever the per-library
    enumeration did.
    """
    document = read_config_document(EXAMPLE)
    document["libraries"] = {"TV Shows": {"badges": {"families": [FAMILY]}}}
    built = build_config(document)
    poster_title = built.collections.poster_title
    repointed = poster_title.model_copy(
        update={
            "title": poster_title.title.model_copy(update={"font": "Title-Face.ttf"}),
            "collection_line": poster_title.collection_line.model_copy(
                update={"font": "Line-Face.ttf"}
            ),
        }
    )
    effective = built.model_copy(
        update={
            "overlays_root": config.overlays_root,
            "fonts_root": config.fonts_root,
            "collections": built.collections.model_copy(update={"poster_title": repointed}),
        }
    )
    # The fixture's own premise, stated rather than assumed: the GLOBAL config
    # names this face nowhere.
    assert effective.badges.families == []
    assert FAMILY_FONT not in {
        effective.artwork.poster.text.font,
        effective.collections.poster_title.title.font,
        effective.collections.poster_title.collection_line.font,
    }
    return effective


async def test_a_font_named_only_by_a_library_scoped_family_reports_that_library(
    client, auth_headers, app, library_family_config, fonts_root
):
    """`badges.families` is overridable per library (`config/schema.py:1716`),
    so a family enabled on one library expands into definitions whose `font:`
    resolves under `fonts_root` first (`overlays/sources.py:127-128`).

    `app.state.config` is swapped wholesale, which is what a config-editor save
    does and why `root_for` and `_references` read it per request.

    One sentence, not four: the family has four definitions naming this one
    face, and `referenced_by` names the family an operator edits once.
    """
    app.state.config = library_family_config
    (fonts_root / FAMILY_FONT).write_bytes(b"x")

    row = _named((await client.get("/api/files/fonts", headers=auth_headers)).json(), FAMILY_FONT)

    assert row["referenced_by"] == [f"libraries.TV Shows.badges.families.{FAMILY}.font"]


async def test_a_font_named_only_by_a_library_scoped_family_cannot_be_deleted(
    client, auth_headers, app, library_family_config, fonts_root
):
    """Deleting it would leave that library's badge stage on the bundled face,
    and `badges/compose.py::badge_fingerprint` hashes the manifest and the
    definitions' fields rather than font bytes -- so nothing would re-render
    and nothing would be signalled."""
    app.state.config = library_family_config
    (fonts_root / FAMILY_FONT).write_bytes(b"x")

    response = await client.delete(f"/api/files/fonts/{FAMILY_FONT}", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "the running configuration still names that file"
    assert (fonts_root / FAMILY_FONT).exists()


async def test_the_same_font_is_deletable_when_no_library_names_the_family(
    client, auth_headers, app, library_family_config, fonts_root
):
    """The other half of the pair: the very same config with its `libraries:`
    block emptied deletes the face cleanly. So the refusal above comes from the
    per-library enumeration and from nothing else in this config."""
    app.state.config = library_family_config.model_copy(update={"libraries": {}})
    (fonts_root / FAMILY_FONT).write_bytes(b"x")

    response = await client.delete(f"/api/files/fonts/{FAMILY_FONT}", headers=auth_headers)

    assert response.status_code == 200
    assert not (fonts_root / FAMILY_FONT).exists()


async def test_a_badge_definitions_own_overlay_and_font_report_who_names_them(
    client, auth_headers, app, config, overlays_root, fonts_root
):
    """The three definition rungs, which the example config leaves unexecuted:
    an explicit `file:` (`overlays/sources.py:187`), the name-keyed
    `<name>.png` fallback a definition naming no source takes (`:212`), and
    `font:` under `fonts_root` (`:128`)."""
    app.state.config = _with_definitions(config)
    (overlays_root / "ribbon-art.png").write_bytes(b"x")
    (overlays_root / "sticker.png").write_bytes(b"x")
    (fonts_root / "Badge-Face.ttf").write_bytes(b"x")

    overlays = (await client.get("/api/files/overlays", headers=auth_headers)).json()
    fonts = (await client.get("/api/files/fonts", headers=auth_headers)).json()

    assert _named(overlays, "ribbon-art.png")["referenced_by"] == ["badges.definitions.ribbon.file"]
    assert _named(overlays, "sticker.png")["referenced_by"] == ["badges.definitions.sticker.name"]
    assert _named(fonts, "Badge-Face.ttf")["referenced_by"] == ["badges.definitions.labelled.font"]


# Nothing constrains these fields to a bare basename: `config/schema.py:329`
# (`TextStyle.font`) and `:428` (`overlay_file`) are plain `str` with a
# description that SAYS "found under fonts_root" and no validator that enforces
# it. Both spellings below render correctly today, so nothing ever tells an
# operator who writes one that it is unusual.
@pytest.mark.parametrize("spelling", ["absolute", "dot-relative"])
async def test_an_overlay_a_config_value_names_by_path_is_reported_and_refused(
    client, auth_headers, app, config, overlays_root, spelling
):
    """`config/impact.py:161-166` joins the configured value onto the root with
    pathlib's `/`, which returns an ABSOLUTE right-hand side whole and drops a
    leading `./` -- so both spellings resolve to exactly the file this listing
    serves as `brand.png`, and its bytes are hashed into every poster
    fingerprint.

    Reported as naming nothing, this file lists with an empty "Named by" column
    and a Delete button, over a confirm control that says in the page's own
    words that nothing names it. The delete goes through, `_cached_sha` on the
    now-missing path returns `""` (`config/impact.py:117-123`), and the poster
    fingerprint moves for every item in the library.
    """
    (overlays_root / "brand.png").write_bytes(b"x")
    value = str(overlays_root / "brand.png") if spelling == "absolute" else "./brand.png"
    app.state.config = config.model_copy(
        update={
            "artwork": config.artwork.model_copy(
                update={"poster": config.artwork.poster.model_copy(update={"overlay_file": value})}
            )
        }
    )

    row = _named(
        (await client.get("/api/files/overlays", headers=auth_headers)).json(), "brand.png"
    )
    response = await client.delete("/api/files/overlays/brand.png", headers=auth_headers)

    assert row["referenced_by"] == ["artwork.poster.overlay_file"]
    assert response.status_code == 409
    assert response.json()["detail"] == "the running configuration still names that file"
    assert (overlays_root / "brand.png").exists()


@pytest.mark.parametrize("spelling", ["absolute", "dot-relative"])
async def test_a_font_a_config_value_names_by_path_is_reported_and_refused(
    client, auth_headers, app, config, fonts_root, spelling
):
    """The font half of the same join (`config/impact.py:168`), because the two
    roots are read by different readers and a fix that closed only the overlay
    one would still hand the operator a face the artwork stage hashes."""
    (fonts_root / "Brand-Face.ttf").write_bytes(b"x")
    value = str(fonts_root / "Brand-Face.ttf") if spelling == "absolute" else "./Brand-Face.ttf"
    poster = config.artwork.poster
    app.state.config = config.model_copy(
        update={
            "artwork": config.artwork.model_copy(
                update={
                    "poster": poster.model_copy(
                        update={"text": poster.text.model_copy(update={"font": value})}
                    )
                }
            )
        }
    )

    row = _named(
        (await client.get("/api/files/fonts", headers=auth_headers)).json(), "Brand-Face.ttf"
    )
    response = await client.delete("/api/files/fonts/Brand-Face.ttf", headers=auth_headers)

    assert row["referenced_by"] == ["artwork.poster.text.font"]
    assert response.status_code == 409
    assert response.json()["detail"] == "the running configuration still names that file"
    assert (fonts_root / "Brand-Face.ttf").exists()


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("overlays", "ribbon-art.png"),
        ("overlays", "sticker.png"),
        ("fonts", "Badge-Face.ttf"),
    ],
)
async def test_a_file_a_badge_definition_names_cannot_be_deleted(
    client, auth_headers, app, config, overlays_root, fonts_root, kind, name
):
    app.state.config = _with_definitions(config)
    root = overlays_root if kind == "overlays" else fonts_root
    (root / name).write_bytes(b"x")

    response = await client.delete(f"/api/files/{kind}/{name}", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "the running configuration still names that file"
    assert (root / name).exists()


# --- one refusal at a time ----------------------------------------------------
#
# Every case below is chosen so that exactly ONE check can refuse it: delete
# that check and the test goes red. Two of the name rule's disjuncts have no
# such case and cannot have one, which the tests that reach them say.


async def test_a_legal_name_over_the_length_limit_is_refused_on_its_length(client, auth_headers):
    """65 legal characters, one dot, an allowlisted suffix, a bare basename:
    `len(submitted) > NAME_MAX` is the only check that can fire. The suite's
    other long name, `"over.png" + "x" * 70`, carries the suffix `.pngxxx...`
    and is refused on that instead."""
    name = "a" * 61 + ".png"
    assert len(name) == 65

    response = await client.delete(f"/api/files/overlays/{name}", headers=auth_headers)

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "the file name must be ASCII letters, digits, dashes and underscores "
        "plus one extension this directory accepts, and at most 64 characters"
    )


async def test_a_legal_shaped_name_carrying_an_illegal_character_is_refused(
    client, auth_headers, overlays_root
):
    """`~` is a legal filename character and an unreserved URL path character,
    so this reaches the handler as typed; it is not in `_NAME_CHARS`, and every
    other disjunct passes. The file is planted so the refusal cannot be the
    absent-file one."""
    (overlays_root / "my~file.png").write_bytes(b"x")

    response = await client.delete("/api/files/overlays/my~file.png", headers=auth_headers)

    assert response.status_code == 422
    assert (overlays_root / "my~file.png").exists()


async def test_a_suffix_this_kind_does_not_manage_is_refused_on_the_suffix(
    client, auth_headers, overlays_root
):
    """A JPEG under `overlays_root`: the length, the character set, the dot
    count and the basename shape all pass, and only the suffix allowlist
    stands. The refusal precedes the filesystem, which the surviving file
    shows."""
    (overlays_root / "photo.jpg").write_bytes(b"x")

    response = await client.delete("/api/files/overlays/photo.jpg", headers=auth_headers)

    assert response.status_code == 422
    assert (overlays_root / "photo.jpg").exists()


@pytest.mark.parametrize("submitted", ["../escape.png", "/etc/passwd.png", "sub/inner.png"])
def test_the_name_rule_refuses_a_path_through_the_helper(submitted):
    """Exercised on the helper because the router cannot deliver one: the three
    `%2F` cases above are unquoted by the ASGI transport into three-segment
    paths that match no route, so they 404 before FastAPI parses a path
    parameter and the handler never sees them.

    Deleting `Path(submitted).name != submitted` on its own does NOT turn this
    red, and that is structural rather than a gap in the case: every separator
    POSIX has is outside `_NAME_CHARS`, so the character-set disjunct refuses
    each of these too. The check is first because it is the one a reader should
    see first, not because it is independently reachable.
    """
    with pytest.raises(HTTPException) as raised:
        normalised_name("overlays", submitted)

    assert raised.value.status_code == 422


def test_the_symlink_check_alone_refuses_a_link_whose_target_is_a_file_in_the_root(
    monkeypatch, overlays_root
):
    """`candidate.is_symlink()` is the only check that can refuse this.

    On a real filesystem the two halves mask each other: a planted symlink
    resolves either to a parent outside the root or to a DIFFERENT name inside
    it, and the parent/name check refuses it even with the symlink check gone.
    The one shape that check could not catch is a link resolving to
    `<root>/<its own name>` -- which is the link itself, a loop no filesystem
    will resolve. So the discriminating case is built on the resolver: with
    `realpath` returning the candidate unchanged, the parent IS the root, the
    name IS the submitted one and the target IS a regular file.

    The plain file asserted first is the control: it shows the fake resolver is
    not itself the refusal.
    """
    (overlays_root / "real.png").write_bytes(b"x")
    try:
        (overlays_root / "link.png").symlink_to(overlays_root / "real.png")
    except OSError:
        pytest.skip("this filesystem does not allow the test to plant a symlink")
    monkeypatch.setattr(os.path, "realpath", str)

    assert contained(overlays_root, "real.png") == overlays_root / "real.png"
    with pytest.raises(OutsideRoot):
        contained(overlays_root, "link.png")


def test_the_parent_check_alone_refuses_a_name_that_resolves_outside(
    monkeypatch, overlays_root, tmp_path
):
    """`resolved.parent != real_root` is the only check that can refuse this:
    the candidate is not a symlink, the resolved target is a regular file, and
    the resolved name matches the submitted one.

    The resolver is faked because a real filesystem cannot produce this shape
    without a symlink -- a hard link resolves to its own path, so it lands back
    under the root and is served, correctly, as the same file under a second
    name.
    """
    (overlays_root / "mine.png").write_bytes(b"x")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "mine.png").write_bytes(b"secret")
    real = os.path.realpath

    def resolves_out(path):
        return str(elsewhere / "mine.png") if Path(path).name == "mine.png" else real(path)

    monkeypatch.setattr(os.path, "realpath", resolves_out)

    with pytest.raises(OutsideRoot):
        contained(overlays_root, "mine.png")


def test_the_name_check_alone_refuses_a_name_that_resolves_to_another_file(
    monkeypatch, overlays_root
):
    """`resolved.name != name` is the only check that can refuse this: the
    candidate is not a symlink, the resolved parent IS the root and the
    resolved target is a regular file. Faked for the same reason as above --
    without a symlink no real resolution renames a final component."""
    (overlays_root / "mine.png").write_bytes(b"x")
    (overlays_root / "other.png").write_bytes(b"x")
    real = os.path.realpath

    def resolves_renamed(path):
        return str(overlays_root / "other.png") if Path(path).name == "mine.png" else real(path)

    monkeypatch.setattr(os.path, "realpath", resolves_renamed)

    with pytest.raises(OutsideRoot):
        contained(overlays_root, "mine.png")


async def test_the_listing_skips_a_symlink_whose_target_is_inside_the_root(
    client, auth_headers, overlays_root
):
    """The listing's own symlink skip, which the outside-target delete case
    does not reach. A link to a file in the same root is a regular file to
    `is_file()` and carries a listed suffix, so only `entry.is_symlink()` keeps
    the same bytes from being served twice under two names -- one of which the
    delete route refuses, which is a listing an operator cannot act on."""
    (overlays_root / "real.png").write_bytes(b"x")
    try:
        (overlays_root / "link.png").symlink_to(overlays_root / "real.png")
    except OSError:
        pytest.skip("this filesystem does not allow the test to plant a symlink")

    payload = (await client.get("/api/files/overlays", headers=auth_headers)).json()

    assert [row["name"] for row in payload["files"]] == ["real.png"]


async def test_an_unseeded_root_lists_nothing_rather_than_failing(
    client, auth_headers, app, config, tmp_path
):
    """The PVC this row mounts is seeded once by an init container. A pod whose
    volume has not been seeded yet must get "there is nothing here" on its first
    page load rather than a 500 out of `iterdir`."""
    app.state.config = config.model_copy(update={"overlays_root": tmp_path / "unseeded"})

    response = await client.get("/api/files/overlays", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"files": []}


async def test_a_file_that_vanishes_before_the_unlink_answers_the_fixed_refusal(
    client, auth_headers, overlays_root, monkeypatch
):
    """Two browser tabs deleting the same file. The containment check passed a
    millisecond ago, so this 404 is not one the route computed -- it is the one
    it must still answer, rather than a 500 carrying a traceback. `Path.unlink`
    is `os.unlink`, so that is where the concurrent removal is planted."""

    def vanishes(path, *args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", str(path))

    (overlays_root / "mine.png").write_bytes(b"x")
    monkeypatch.setattr(os, "unlink", vanishes)

    response = await client.delete("/api/files/overlays/mine.png", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "there is no such file in that directory"
    assert str(overlays_root) not in response.text
