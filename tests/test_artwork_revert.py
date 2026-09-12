"""RevertMode: push the un-badged ``/assets`` base back to Plex.

Fake Plex throughout: a ``FakeItem`` with ``uploadPoster``/``uploadArt``/
``lockPoster``/``lockArt`` spies that read back the temp file ``upload_artwork``
writes, and a tmp ``assets_root`` seeded with base files that ``renders`` rows
point at. The mode reads disk and pushes to Plex; nothing here touches the
network.
"""
from pathlib import Path

import asyncio

from plexapi.exceptions import NotFound as PlexNotFound
import pytest

from autoposter.artwork_modes.revert import RevertMode
from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem, Render
from autoposter.plex.artwork import upload_artwork as _plex_upload_artwork
from autoposter.servers.base import ServerItemRef

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PLEX_URL = "http://plex.local"
PLEX_TOKEN = "plex-token"


class FakeItem:
    """The plexapi surface revert writes. ``uploadPoster``/``uploadArt`` read the
    temp file ``upload_artwork`` hands them, so a test can assert both that the
    item was pushed and that the pushed bytes are the base file's."""

    def __init__(self):
        self.uploaded = []  # (field, bytes)
        self.locked = []
        self.refreshed = False

    def refresh(self):
        self.refreshed = True

    def uploadPoster(self, filepath=None):  # noqa: N802 - plexapi name
        with open(filepath, "rb") as handle:
            self.uploaded.append(("poster", handle.read()))

    def uploadArt(self, filepath=None):  # noqa: N802 - plexapi name
        with open(filepath, "rb") as handle:
            self.uploaded.append(("art", handle.read()))

    def lockPoster(self):  # noqa: N802 - plexapi name
        self.locked.append("poster")

    def lockArt(self):  # noqa: N802 - plexapi name
        self.locked.append("art")


class FakePlexClient:
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

    async def upload_artwork(self, ref, data, art_kind, lock=True):
        item = self._items[ref.native_id]
        await asyncio.to_thread(_plex_upload_artwork, item, data, art_kind, lock)


@pytest.fixture
def assets_root(tmp_path) -> Path:
    root = tmp_path / "assets"
    root.mkdir()
    return root


@pytest.fixture
def config(assets_root):
    cfg = load_config(EXAMPLE)
    cfg.plex.url = PLEX_URL
    cfg.assets_root = assets_root
    return cfg


def _headers():
    return {"X-Plex-Token": PLEX_TOKEN}


async def _add_item(session, *, rating_key, kind="movie", library="Movies",
                    root_folder="A (1999)"):
    item = MediaItem(
        rating_key=rating_key, library=library, kind=kind, title="A",
        root_folder=root_folder,
    )
    session.add(item)
    await session.commit()
    return item


async def _add_render(session, item, art_kind, path, *, base_sha256="digest"):
    render = Render(
        item_id=item.id, art_kind=art_kind, asset_path=str(path),
        base_sha256=base_sha256, status="rendered",
    )
    session.add(render)
    await session.commit()
    return render


def _seed_base(assets_root, name, data) -> Path:
    path = assets_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def test_dry_run_pushes_nothing(session, config, assets_root):
    item_row = await _add_item(session, rating_key="rk1")
    await _add_render(session, item_row, "poster", _seed_base(assets_root, "a.jpg", b"base"))
    item = FakeItem()
    plex = FakePlexClient({"rk1": item})

    result = await RevertMode(config, plex, None, _headers(), apply=False).run(session)

    assert result.dry_run is True
    assert (result.items, result.items_with_base, result.files) == (1, 1, 1)
    # Dry run: nothing resolved against Plex, nothing pushed.
    assert plex.fetched == []
    assert item.uploaded == []
    assert result.as_response() == {
        "mode": "revert", "status": "dry run", "dry_run": True, "items": 1,
        "items_with_base": 1, "files": 1, "missing": 0,
    }


async def test_apply_pushes_the_clean_base(session, config, assets_root):
    """The whole point of the mode: the badged image on Plex is replaced by the
    un-badged base bytes sitting under ``/assets``."""
    item_row = await _add_item(session, rating_key="rk1")
    await _add_render(
        session, item_row, "poster", _seed_base(assets_root, "a.jpg", b"the-clean-base")
    )
    await _add_render(
        session, item_row, "background", _seed_base(assets_root, "a-bg.jpg", b"the-clean-bg")
    )
    item = FakeItem()
    plex = FakePlexClient({"rk1": item})

    result = await RevertMode(config, plex, None, _headers(), apply=True).run(session)

    assert result.dry_run is False
    assert (result.pushed, result.failed, result.files) == (2, 0, 2)
    assert ("poster", b"the-clean-base") in item.uploaded
    assert ("art", b"the-clean-bg") in item.uploaded
    # One fetch for the item, not one per file: the planning phase groups the
    # render rows by rating key precisely so an applied run resolves each Plex
    # object once. Push per file, fetch per item.
    assert plex.fetched == ["rk1"]
    # background routes to uploadArt+lockArt, the rest to uploadPoster+lockPoster.
    assert set(item.locked) == {"poster", "art"}
    assert result.as_response()["status"] == "reverted"


async def test_revert_skips_an_item_whose_base_is_not_on_disk(session, config, assets_root):
    """The base-exists mutation-proof: the render row names a path nothing was
    ever written to. Drop the "does this file exist inside the asset tree"
    guard in RevertMode.run and the item is planned and pushed, reddening the
    ``items_with_base == 0`` and ``uploaded == []`` assertions."""
    item_row = await _add_item(session, rating_key="rk1")
    await _add_render(session, item_row, "poster", assets_root / "never-written.jpg")
    item = FakeItem()
    plex = FakePlexClient({"rk1": item})

    result = await RevertMode(config, plex, None, _headers(), apply=True).run(session)

    assert (result.items, result.items_with_base, result.files) == (1, 0, 0)
    assert plex.fetched == []
    assert item.uploaded == []


async def test_revert_skips_a_render_row_with_no_base_digest(session, config, assets_root):
    """A NULL ``base_sha256`` means this project never rendered or adopted bytes
    into the row, so whatever sits at that path is not ours to push -- the
    ``base_artwork`` endpoint refuses to serve it for the same reason."""
    item_row = await _add_item(session, rating_key="rk1")
    await _add_render(
        session, item_row, "poster", _seed_base(assets_root, "foreign.jpg", b"kometa-era"),
        base_sha256=None,
    )
    item = FakeItem()
    plex = FakePlexClient({"rk1": item})

    result = await RevertMode(config, plex, None, _headers(), apply=True).run(session)

    assert (result.items, result.items_with_base, result.files) == (1, 0, 0)
    assert item.uploaded == []


async def test_revert_refuses_a_base_outside_the_asset_tree(session, config, tmp_path):
    """``renders.asset_path`` is a filesystem path read out of the database, and
    the database is not a trust boundary: a row pointing outside ``assets_root``
    must not become "upload any file this process can read" to Plex."""
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"not ours")
    item_row = await _add_item(session, rating_key="rk1")
    await _add_render(session, item_row, "poster", outside)
    item = FakeItem()
    plex = FakePlexClient({"rk1": item})

    result = await RevertMode(config, plex, None, _headers(), apply=True).run(session)

    assert (result.items, result.items_with_base, result.files) == (1, 0, 0)
    assert item.uploaded == []


async def test_apply_pushes_exactly_the_filtered_set(session, config, assets_root):
    """A Movies-only revert must push the movie and NOT the show. Drop the
    library filter and the show gets pushed too, reddening ``tv.uploaded == []``."""
    movie_row = await _add_item(session, rating_key="rk-m", kind="movie", library="Movies")
    show_row = await _add_item(session, rating_key="rk-t", kind="show", library="TV Shows",
                               root_folder="The Show")
    await _add_render(session, movie_row, "poster", _seed_base(assets_root, "m.jpg", b"movie"))
    await _add_render(session, show_row, "poster", _seed_base(assets_root, "t.jpg", b"show"))
    movie, tv = FakeItem(), FakeItem()
    plex = FakePlexClient({"rk-m": movie, "rk-t": tv})

    result = await RevertMode(
        config, plex, None, _headers(), apply=True, library="Movies"
    ).run(session)

    assert result.items == 1  # only the Movies item is a candidate
    assert ("poster", b"movie") in movie.uploaded
    assert tv.uploaded == []
    assert plex.fetched == ["rk-m"]


async def test_apply_filters_by_type_and_item(session, config, assets_root):
    movie_row = await _add_item(session, rating_key="rk-m", kind="movie", library="Movies")
    show_row = await _add_item(session, rating_key="rk-t", kind="show", library="TV Shows",
                               root_folder="The Show")
    await _add_render(session, movie_row, "poster", _seed_base(assets_root, "m.jpg", b"movie"))
    await _add_render(session, show_row, "poster", _seed_base(assets_root, "t.jpg", b"show"))
    movie, tv = FakeItem(), FakeItem()
    plex = FakePlexClient({"rk-m": movie, "rk-t": tv})

    result = await RevertMode(
        config, plex, None, _headers(), apply=True, kind="show", item_id=show_row.id
    ).run(session)

    assert result.items == 1
    assert ("poster", b"show") in tv.uploaded
    assert movie.uploaded == []


async def test_revert_respects_the_cap(session, config, assets_root):
    """Two items would change but the absolute cap is 1, so it refuses with the
    real numbers and pushes nothing -- proven by fetch_item never being called
    even though apply is true."""
    config.artwork_modes.max_changes = 1
    first = await _add_item(session, rating_key="rk1")
    second = await _add_item(session, rating_key="rk2", root_folder="B (2000)")
    await _add_render(session, first, "poster", _seed_base(assets_root, "a.jpg", b"a"))
    await _add_render(session, second, "poster", _seed_base(assets_root, "b.jpg", b"b"))
    plex = FakePlexClient({"rk1": FakeItem(), "rk2": FakeItem()})

    result = await RevertMode(config, plex, None, _headers(), apply=True).run(session)

    assert result.refused is not None
    assert "2 of 2" in result.refused
    assert plex.fetched == []
    response = result.as_response()
    assert response["status"] == "refused"
    assert response["dry_run"] is False  # apply=True was requested
    # The refusal carries the real numbers -- the UI should not have to parse
    # them back out of the reason string.
    assert (response["items"], response["items_with_base"], response["files"]) == (2, 2, 2)


async def test_revert_never_refreshes_the_plex_object(session, config, assets_root):
    """The behavioural companion to the project-wide no-.refresh() AST guard: a
    revert pushes art and must never trigger a metadata refresh, which would
    have Plex re-pull from its agents and overwrite what was just pushed."""
    item_row = await _add_item(session, rating_key="rk1")
    await _add_render(session, item_row, "poster", _seed_base(assets_root, "a.jpg", b"base"))
    item = FakeItem()
    plex = FakePlexClient({"rk1": item})

    await RevertMode(config, plex, None, _headers(), apply=True).run(session)

    assert item.refreshed is False


async def test_revert_refuses_an_empty_renders_table(session, config):
    """An empty ``renders`` is a restore that has not finished, not a library
    with nothing to revert."""
    await _add_item(session, rating_key="rk1")  # media_items is NOT empty
    plex = FakePlexClient({})

    result = await RevertMode(config, plex, None, _headers(), apply=True).run(session)

    assert result.refused is not None
    assert "renders" in result.refused
    # apply=True was requested, so dry_run mirrors the success paths: False.
    assert result.as_response() == {
        "mode": "revert", "status": "refused", "reason": result.refused,
        "dry_run": False, "items": 0, "items_with_base": 0, "files": 0, "missing": 0,
    }


async def test_revert_counts_a_failed_push(session, config, assets_root):
    """One unreachable item costs its own line in the tally, not the run."""
    good = await _add_item(session, rating_key="rk-good")
    bad = await _add_item(session, rating_key="rk-bad", root_folder="B (2000)")
    await _add_render(session, good, "poster", _seed_base(assets_root, "a.jpg", b"a"))
    await _add_render(session, bad, "poster", _seed_base(assets_root, "b.jpg", b"b"))
    item = FakeItem()
    plex = FakePlexClient({"rk-good": item})  # rk-bad raises KeyError on fetch

    result = await RevertMode(config, plex, None, _headers(), apply=True).run(session)

    assert (result.pushed, result.failed) == (1, 1)
    assert ("poster", b"a") in item.uploaded


async def test_revert_logs_a_missing_item_at_info_not_warning(
    session, config, assets_root, caplog
):
    """An item deleted from Plex since its render row was written 404s on the
    apply-loop fetch -- expected, not a crash. Row 218, backup.py's PR #112
    hotfix shape: one concise INFO line, tallied, no WARNING, no traceback."""
    gone = await _add_item(session, rating_key="rk-gone")
    ok = await _add_item(session, rating_key="rk-ok", root_folder="B (2000)")
    await _add_render(session, gone, "poster", _seed_base(assets_root, "a.jpg", b"a"))
    await _add_render(session, ok, "poster", _seed_base(assets_root, "b.jpg", b"b"))
    ok_item = FakeItem()

    class GoneClient(FakePlexClient):
        async def fetch_item(self, rating_key):
            self.fetched.append(rating_key)
            if rating_key == "rk-gone":
                raise PlexNotFound(f"(404) not_found ({rating_key})")
            return self._items[rating_key]

    plex = GoneClient({"rk-ok": ok_item})

    with caplog.at_level("INFO"):
        result = await RevertMode(config, plex, None, _headers(), apply=True).run(session)

    assert (result.pushed, result.failed, result.missing) == (1, 0, 1)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == []
    assert not any(r.exc_info for r in caplog.records)

    revert_records = [r for r in caplog.records if r.name == "autoposter.artwork_modes.revert"]
    per_item = [r.message for r in revert_records if "rk-gone" in r.message]
    assert len(per_item) == 1
    assert per_item[0].startswith("revert: ") and "no longer in Plex" in per_item[0]

    summary = [r.message for r in revert_records if r.message not in per_item]
    assert len(summary) == 1
    assert summary[0].startswith("revert: ") and "1" in summary[0]
