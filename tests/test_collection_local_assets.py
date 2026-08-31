"""Row 37 — assets_for_all_collections.

A local poster placed under assets_root for a collection this service does not
manage is applied to it. The negative case is the test: with the toggle unset,
an unmanaged collection with a local poster beside it is left alone entirely.

The hash ledger reuses ManagedCollection with kind='local_asset' -- the same
sweep-exempt shape kind='operator' already has (engine.py) -- so there is no
migration and no ownership claim: the ownership label is never applied.
"""
from pathlib import Path

from autoposter.collections.posters import apply_local_posters_to_unmanaged
from autoposter.config.loader import load_config
from autoposter.db.models import ManagedCollection
from sqlalchemy import select

EXAMPLE = Path("config/autoposter.example.yaml")


class FakeCollection:
    def __init__(self, title):
        self.title = title
        self.uploaded = []
        self.locked = []

    def uploadPoster(self, filepath=None, url=None):
        self.uploaded.append(Path(filepath).read_bytes())

    def edit(self, **kwargs):
        self.locked.append(kwargs)


def _config(tmp_path, **collections):
    config = load_config(EXAMPLE)
    return config.model_copy(update={
        "assets_root": tmp_path,
        "library_folders": True,
        "collections": config.collections.model_copy(
            update={"apply_to_plex": True, **collections}
        ),
    })


def _place(tmp_path, library, title, data):
    folder = tmp_path / library / title
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "poster.jpg").write_bytes(data)


async def test_unset_toggle_touches_nothing(session, tmp_path, jpeg_bytes):
    _place(tmp_path, "Movies", "Someone Elses Collection", jpeg_bytes)
    collection = FakeCollection("Someone Elses Collection")
    results = await apply_local_posters_to_unmanaged(
        session, _config(tmp_path), None, "Movies",
        {"Someone Elses Collection": collection}, dry_run=False,
    )
    assert results == []
    assert collection.uploaded == []
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert rows == []


async def test_a_local_poster_is_applied_and_ledgered(session, tmp_path, jpeg_bytes):
    _place(tmp_path, "Movies", "Someone Elses Collection", jpeg_bytes)
    collection = FakeCollection("Someone Elses Collection")
    config = _config(tmp_path, assets_for_all_collections=True)
    results = await apply_local_posters_to_unmanaged(
        session, config, None, "Movies",
        {"Someone Elses Collection": collection}, dry_run=False,
    )
    assert results == ["poster applied to 'Someone Elses Collection' from a local asset"]
    assert collection.uploaded == [jpeg_bytes]
    row = (await session.execute(select(ManagedCollection))).scalar_one()
    assert row.kind == "local_asset"
    assert row.poster_sha256 is not None


async def test_a_second_pass_uploads_nothing(session, tmp_path, jpeg_bytes):
    _place(tmp_path, "Movies", "Someone Elses Collection", jpeg_bytes)
    config = _config(tmp_path, assets_for_all_collections=True)
    first = FakeCollection("Someone Elses Collection")
    await apply_local_posters_to_unmanaged(
        session, config, None, "Movies", {"Someone Elses Collection": first},
        dry_run=False,
    )
    second = FakeCollection("Someone Elses Collection")
    results = await apply_local_posters_to_unmanaged(
        session, config, None, "Movies", {"Someone Elses Collection": second},
        dry_run=False,
    )
    assert second.uploaded == []
    assert results == []


async def test_a_collection_with_no_local_poster_is_left_alone(session, tmp_path):
    collection = FakeCollection("No Asset Here")
    results = await apply_local_posters_to_unmanaged(
        session, _config(tmp_path, assets_for_all_collections=True), None,
        "Movies", {"No Asset Here": collection}, dry_run=False,
    )
    assert results == []
    assert collection.uploaded == []
