"""POST /api/items/{item_id}/renders/{art_kind}/clear-override.

A manual override is a *file* on the manualassets mount -- nothing in the
database can suppress one -- so clearing it means moving that file out of the
way. What is pinned here is mostly what the endpoint refuses to do: it will
not silently succeed when there is no override, it will not touch the mount
for an art kind the item cannot have, and a mount that refuses the rename
leaves the render row exactly as it found it, because a cleared fingerprint
with the override still in place would re-render straight back to the
override and report success.

``manual_assets_root`` is pointed at ``tmp_path`` for every test here, so the
only files any of this can reach are ones the test itself planted.
"""
import os
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Job, MediaItem, Render
from autoposter.queue.jobs import enqueue

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
OVERRIDE_BYTES = b"the operator's own poster"
# The example config has library_folders: true, so an override lives at
# <manual_assets_root>/<library>/<root_folder>/<name>.
LIBRARY = "Movies"
ROOT_FOLDER = "A Movie (1999)"
TMDB_ID = 550
DEDUPE_KEY = f"process_item:movie:tmdb{TMDB_ID}"


@pytest.fixture
def manual_root(tmp_path) -> Path:
    root = tmp_path / "manualassets"
    root.mkdir()
    return root


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest_asyncio.fixture
async def app(session_factory, manual_root):
    config = load_config(EXAMPLE).model_copy(update={"manual_assets_root": manual_root})
    return create_app(config, session_factory, _secrets())


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _item(session, kind: str = "movie", with_render: bool = True) -> int:
    """One media item, optionally with a rendered row carrying fingerprints."""
    item = MediaItem(
        rating_key="rk1", library=LIBRARY, kind=kind, title="A Movie",
        year=1999, tmdb_id=TMDB_ID, root_folder=ROOT_FOLDER,
    )
    session.add(item)
    await session.flush()
    if with_render:
        session.add(
            Render(
                item_id=item.id, art_kind="poster", status="rendered",
                asset_path="/assets/Movies/A Movie (1999)/poster.jpg",
                fingerprint="f" * 64, badge_fingerprint="b" * 64,
            )
        )
    await session.commit()
    return item.id


def _plant(manual_root: Path, name: str = "poster.jpg") -> Path:
    override = manual_root / LIBRARY / ROOT_FOLDER / name
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_bytes(OVERRIDE_BYTES)
    return override


async def test_requires_a_session(client, session, manual_root):
    item_id = await _item(session)
    _plant(manual_root)

    response = await client.post(f"/api/items/{item_id}/renders/poster/clear-override")

    assert response.status_code == 401


async def test_an_unknown_item_is_404(client, auth_headers):
    response = await client.post(
        "/api/items/999999/renders/poster/clear-override", headers=auth_headers
    )
    assert response.status_code == 404


async def test_no_override_file_is_409(client, auth_headers, session):
    """Not a silent success: the button is only shown for an item that has one,
    so being asked without one means the UI and the mount disagree."""
    item_id = await _item(session)

    response = await client.post(
        f"/api/items/{item_id}/renders/poster/clear-override", headers=auth_headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "no manual override for this art kind"


async def test_clearing_renames_the_file_and_queues_a_reprocess(
    client, auth_headers, session, manual_root
):
    item_id = await _item(session)
    override = _plant(manual_root)

    response = await client.post(
        f"/api/items/{item_id}/renders/poster/clear-override", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {"status": "cleared", "queued": True}
    # Renamed, not deleted -- the operator's file survives and restoring it is
    # a rename back.
    assert not override.exists()
    disabled = override.with_name(override.name + ".disabled")
    assert disabled.read_bytes() == OVERRIDE_BYTES

    job = (await session.execute(select(Job).where(Job.dedupe_key == DEDUPE_KEY))).scalar_one()
    assert job.kind == "process_item"
    assert job.payload["tmdb_id"] == TMDB_ID


async def test_clearing_clears_both_fingerprints_so_the_next_pass_regenerates(
    client, auth_headers, session, manual_root
):
    """The fingerprint short-circuit (render/pipeline.py) would otherwise
    return "unchanged" and leave the override's image live for ever."""
    item_id = await _item(session)
    _plant(manual_root)

    response = await client.post(
        f"/api/items/{item_id}/renders/poster/clear-override", headers=auth_headers
    )

    assert response.status_code == 200
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint is None
    assert render.badge_fingerprint is None


async def test_clearing_works_when_no_render_row_exists(
    client, auth_headers, session, manual_root
):
    """An override placed before this project ever rendered the item has no row
    to clear; the rename and the enqueue still have to happen."""
    item_id = await _item(session, with_render=False)
    override = _plant(manual_root)

    response = await client.post(
        f"/api/items/{item_id}/renders/poster/clear-override", headers=auth_headers
    )

    assert response.status_code == 200
    assert not override.exists()


async def test_an_existing_disabled_file_is_overwritten(
    client, auth_headers, session, manual_root
):
    """Second clear of a re-placed override: os.replace, not rename, so the
    stale ``.disabled`` from last time does not make this fail."""
    item_id = await _item(session)
    override = _plant(manual_root)
    stale = override.with_name(override.name + ".disabled")
    stale.write_bytes(b"a previously disabled override")

    response = await client.post(
        f"/api/items/{item_id}/renders/poster/clear-override", headers=auth_headers
    )

    assert response.status_code == 200
    assert stale.read_bytes() == OVERRIDE_BYTES


async def test_the_enqueue_dedupes_like_reprocess(client, auth_headers, session, manual_root):
    """Same enqueue path as POST /items/{id}/reprocess: a pending job on the
    same dedupe key means nothing is inserted and ``queued`` is false. The file
    is still cleared -- the dedupe is about the queue, not the mount."""
    item_id = await _item(session)
    override = _plant(manual_root)
    await enqueue(
        session, kind="process_item", payload={"kind": "movie"}, dedupe_key=DEDUPE_KEY
    )

    response = await client.post(
        f"/api/items/{item_id}/renders/poster/clear-override", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {"status": "cleared", "queued": False}
    assert not override.exists()


@pytest.mark.parametrize("art_kind", ["season_poster", "title_card", "bogus"])
async def test_an_art_kind_the_item_cannot_have_never_reaches_the_mount(
    client, auth_headers, session, manual_root, art_kind
):
    """``art_kind`` is the only caller-supplied input that reaches a path
    builder, so it is checked against the item's own kind before anything
    stats the mount. Files that a season/episode kind would name are planted
    here: an unvalidated handler either finds one or blows up building the
    path, and either way answers something other than 404."""
    item_id = await _item(session)
    planted = [_plant(manual_root, "Season01.jpg"), _plant(manual_root, "S01E01.jpg")]

    response = await client.post(
        f"/api/items/{item_id}/renders/{art_kind}/clear-override", headers=auth_headers
    )

    assert response.status_code == 404
    assert all(path.exists() for path in planted)


async def test_a_failed_rename_is_503_and_changes_nothing(
    client, auth_headers, session, manual_root, monkeypatch, caplog
):
    """A read-only mount, simulated. Nothing may be cleared or queued: a
    cleared fingerprint with the override still in place re-renders straight
    back to the override while the UI says it was cleared.

    Roadmap row 248: the served detail is the FIXED sentence, never the
    OSError. str(exc) here was "[Errno 30] Read-only file system: '<absolute
    path on the mount>'" -- an errno and the server's directory layout on a
    served surface, against row 213's law. The sentence asserted below is
    byte-identical to the one api/manual.py and api/candidates.py already
    serve for the same mount and the same OSError, so the three sites agree.
    The errno and the path are not lost: they stay on the endpoint's WARNING,
    and the pod log is the trusted sink (row 207) -- pinned below, so a later
    edit that trims the path or the errno off that WARNING fails here instead
    of only being noticed by rereading routes.py.
    """
    item_id = await _item(session)
    override = _plant(manual_root)

    def refuse(src, dst):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(os, "replace", refuse)

    with caplog.at_level("WARNING"):
        response = await client.post(
            f"/api/items/{item_id}/renders/poster/clear-override", headers=auth_headers
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "could not write to the override mount"
    assert any(
        str(override) in record.getMessage()
        and record.getMessage() != "could not write to the override mount"
        for record in caplog.records
        if record.levelname == "WARNING"
    )
    assert override.read_bytes() == OVERRIDE_BYTES
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint == "f" * 64
    assert render.badge_fingerprint == "b" * 64
    assert (await session.execute(select(Job))).scalars().all() == []
