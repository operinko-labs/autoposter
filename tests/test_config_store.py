"""The store IS the configuration.

Three rules, each with its own test: the document that runs is the stored one;
the mounted file is read only when the store is empty, and seeds it once; and a
deployment whose store is seeded boots with no file at all.
"""
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from autoposter.config.overrides import (
    STORE_FORMAT,
    load_effective_config,
    load_store,
    seed_store,
    store_meta,
    write_store,
)
from autoposter.db.models import ConfigOverride

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def _document() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


@pytest.fixture
def config_file(tmp_path) -> Path:
    path = tmp_path / "autoposter.yaml"
    path.write_text(yaml.safe_dump(_document()), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_an_empty_store_is_seeded_from_the_file_once(session_factory, config_file):
    async with session_factory() as session:
        config = await load_effective_config(config_file, session)
        assert config.workers >= 1
        document, meta = await load_store(session)
    assert document["plex"]["url"] == _document()["plex"]["url"]
    assert meta == {"format": STORE_FORMAT}


@pytest.mark.asyncio
async def test_the_stored_document_wins_and_the_file_is_not_read(session_factory, config_file):
    """The file is removed after the seed. A load that still works is a load
    that did not open it."""
    async with session_factory() as session:
        await load_effective_config(config_file, session)
        stored, _ = await load_store(session)
        stored["workers"] = 11
        await write_store(session, stored, store_meta())
        await session.commit()

    config_file.unlink()
    async with session_factory() as session:
        config = await load_effective_config(config_file, session)
    assert config.workers == 11


@pytest.mark.asyncio
async def test_seed_store_never_overwrites_a_seeded_store(session_factory):
    document = _document()
    async with session_factory() as session:
        await seed_store(session, document)
        await session.commit()
    changed = {**document, "workers": 7}
    async with session_factory() as session:
        held = await seed_store(session, changed)
        await session.commit()
    assert held["workers"] == document["workers"], "a second seed must be a no-op"


@pytest.mark.asyncio
async def test_a_store_with_no_file_loads(session_factory, tmp_path):
    """A deployment whose store is seeded needs no file at all."""
    async with session_factory() as session:
        await seed_store(session, _document())
        await session.commit()
    async with session_factory() as session:
        config = await load_effective_config(tmp_path / "missing.yaml", session)
    assert config.plex is not None


@pytest.mark.asyncio
async def test_write_store_keeps_one_row(session_factory, config_file):
    async with session_factory() as session:
        await load_effective_config(config_file, session)
        await write_store(session, _document(), store_meta(restart_paths=["jellyfin"]))
        await session.commit()
    async with session_factory() as session:
        rows = (await session.execute(select(ConfigOverride))).scalars().all()
        assert len(rows) == 1
        assert rows[0].meta == {"format": STORE_FORMAT, "restart_paths": ["jellyfin"]}


@pytest.mark.asyncio
async def test_a_file_the_schema_refuses_does_not_seed_the_store(session_factory, tmp_path):
    """The seed is the last time the file is read, so seeding an invalid
    document would be unrecoverable: the application would not start, and the
    editor that could clear the row sits behind the application. The refusal
    has to leave the store empty and the file editable."""
    bad = tmp_path / "autoposter.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8")
        + "\nversion_check:\n  project: operinko-labs\n",
        encoding="utf-8",
    )
    async with session_factory() as session:
        with pytest.raises(ValueError, match="version_check"):
            await load_effective_config(bad, session)
    async with session_factory() as session:
        assert await load_store(session) == ({}, {})


@pytest.mark.asyncio
async def test_a_stored_secrets_key_is_refused_at_load(session_factory):
    """The document is now served back by the config API, so a ``secrets`` key
    in a hand-edited row would be echoed rather than merely ignored."""
    async with session_factory() as session:
        await write_store(
            session, {**_document(), "secrets": {"plex_token": "leaked"}}, store_meta()
        )
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(ValueError, match="secrets"):
            await load_effective_config(None, session)
