"""Row 86 -- export what Plex currently holds to a YAML backup.

Shaped exactly like artwork_modes/backup.py: walk media_items, refuse on an
empty table, refuse on a missing mount, tally per item, write atomically. What
differs is what is serialised (the WRITABLE_BY_KIND field set, as Plex holds
it now) and that the write is one file per LIBRARY rather than per item.

The last test here is the gated-feature entry-point test the memory's law
requires, through POST /api/metadata-backup: gate-off writes nothing, gate-on
writes the file, second pass writes byte-identical bytes. Bytes, never mtimes
-- this machine's Docker clock steps backwards.
"""
from datetime import date
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import MediaItem
from autoposter.metadata_backup import capture_item

from test_mass_ops_verbs import LockableItem

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


def test_capture_reads_the_writable_fields_a_movie_carries():
    item = LockableItem(
        contentRating="R", studio="Warner", genres=["Drama", "Crime"],
        locks=[("studio", True), ("genre", False)],
    )
    captured = capture_item(item, "movie")
    assert captured["content_rating"] == "R"
    assert captured["studio"] == "Warner"
    assert captured["genres"] == ["Drama", "Crime"]
    assert captured["studio_locked"] is True
    assert captured["genres_locked"] is False


def test_capture_carries_the_human_anchors():
    item = LockableItem(title="Heat", year=1995)
    captured = capture_item(item, "movie")
    assert captured["title"] == "Heat"
    assert captured["year"] == 1995
    assert captured["kind"] == "movie"


def test_capture_omits_a_lock_plex_did_not_report():
    item = LockableItem(studio="Warner", locks=[])
    assert "studio_locked" not in capture_item(item, "movie")


def test_capture_reads_only_the_fields_the_kind_can_carry():
    item = LockableItem(kind="season")
    captured = capture_item(item, "season")
    assert "studio" not in captured
    assert "genres" not in captured


def test_capture_formats_a_date_as_a_string():
    item = LockableItem(originallyAvailableAt=date(1995, 12, 15))
    assert capture_item(item, "movie")["originally_available"] == "1995-12-15"


# --- the gated-feature entry-point test (Global Constraint 4) ---------------
#
# Through POST /api/metadata-backup, on a real create_app with app.state.plex
# wired by hand -- the shape tests/test_api_artwork_modes.py uses. Three
# assertions, in the order the law states them. The steady-state assertion
# compares BYTES, never mtimes: this machine's Docker clock steps backwards.


class FakePlexClient:
    def __init__(self, items):
        self._items = items

    async def fetch_item(self, rating_key):
        return self._items[rating_key]


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="plex-token-for-this-test", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest.fixture
def backup_root(tmp_path) -> Path:
    # Pre-created, because in a deployment it is a MOUNT: the mode refuses
    # rather than mkdir-ing a tree into the container's filesystem.
    root = tmp_path / "metadatabackup"
    root.mkdir()
    return root


@pytest.fixture
def config(backup_root):
    cfg = load_config(EXAMPLE)
    cfg.operations.metadata_backup_root = backup_root
    return cfg


@pytest_asyncio.fixture
async def api_client(config, session_factory, session):
    media = MediaItem(rating_key="rk1", library="Movies", kind="movie", title="Heat")
    session.add(media)
    await session.commit()

    app = create_app(config, session_factory, _secrets())
    app.state.plex = FakePlexClient(
        {"rk1": LockableItem(studio="Warner", locks=[("studio", True)])}
    )
    # _require_plex demands both; metadata_backup never uses http itself.
    app.state.http = AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(404)
    ))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/login", json={"password": PASSWORD})
        token = login.json()["token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


@pytest.mark.asyncio
async def test_entry_point_gate_off_writes_nothing(api_client, backup_root, config):
    # (a) GATE OFF: metadata_backup_enabled defaults False.
    response = await api_client.post("/api/metadata-backup")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refused"
    assert "metadata_backup_enabled" in body["reason"]
    assert list(Path(backup_root).iterdir()) == []


@pytest.mark.asyncio
async def test_entry_point_gate_on_writes_and_the_second_pass_is_byte_identical(
    api_client, backup_root, config
):
    # (b) GATE ON: the export fires and produces one file per library.
    config.operations.metadata_backup_enabled = True
    response = await api_client.post("/api/metadata-backup")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "backed up"
    assert body["files"] == 1
    written = sorted(Path(backup_root).glob("*.yml"))
    assert len(written) == 1
    first = written[0].read_bytes()
    parsed = yaml.safe_load(first.decode())
    assert "metadata" in parsed

    # (c) SECOND PASS: steady state. Same library, same Plex values, so the
    # same bytes -- which is what makes "has anything changed since the last
    # backup" answerable with a diff.
    response = await api_client.post("/api/metadata-backup")
    assert response.status_code == 200
    assert written[0].read_bytes() == first
