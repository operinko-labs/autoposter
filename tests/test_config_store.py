"""The store IS the configuration.

Three rules, each with its own test: the document that runs is the stored one;
the mounted file is read only when the store is empty, and seeds it once; and a
deployment whose store is seeded boots with no file at all.

And one event that happens once per deployment: a row written before the store
held whole documents is converted into one, with the delta it used to be kept
as a snapshot.
"""
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from autoposter.config.overrides import (
    MIGRATE_REASON,
    STORE_FORMAT,
    load_effective_config,
    load_store,
    migrate_delta_to_document,
    seed_store,
    store_meta,
    write_store,
)
from autoposter.config.snapshots import capture_snapshot
from autoposter.db.models import ConfigOverride, ConfigOverrideSnapshot

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


@pytest.mark.asyncio
async def test_an_empty_store_and_no_file_is_refused(session_factory, tmp_path):
    """The state this loader will not invent its way out of. A booted
    application cannot reach it -- ``boot.is_configured`` serves the wizard
    instead -- so reaching it means something is wrong with the deployment,
    and a config built from the schema's defaults would hide that behind a
    running application pointed at nothing."""
    async with session_factory() as session:
        with pytest.raises(ValueError, match="has not been configured"):
            await load_effective_config(tmp_path / "missing.yaml", session)
    async with session_factory() as session:
        assert await load_store(session) == ({}, {}), "the refusal wrote something"


@pytest.mark.asyncio
async def test_a_store_of_only_migrated_sections_is_not_an_empty_store(
    session_factory, config_file
):
    """A row whose every section has left the schema reads as an empty
    document, but it is a store somebody wrote: seeding over it would throw
    away the metadata -- the restart list -- that the row still carries. It
    takes the delta path instead, which merges nothing over the file and so
    converts to the file's own document, restart list intact."""
    stale = {"version_check": {"project": "operinko-labs"}}
    async with session_factory() as session:
        await write_store(session, stale, {"restart_paths": ["plex"]})
        await session.commit()

    async with session_factory() as session:
        config = await load_effective_config(config_file, session)
    assert config.workers == _document()["workers"]

    async with session_factory() as session:
        row = (await session.execute(select(ConfigOverride))).scalar_one()
        snapshots = (
            await session.execute(select(ConfigOverrideSnapshot))
        ).scalars().all()
    assert row.document == _document(), "the conversion did not write the file's document"
    assert row.meta == {"format": STORE_FORMAT, "restart_paths": ["plex"]}
    assert snapshots == [], "a delta with nothing left in it has nothing to snapshot"


# --- the one-time conversion of a delta-era row ---


async def _write_delta(session_factory, delta: dict) -> None:
    """A store exactly as it was before the row held the whole document: a
    delta, and no metadata to say so."""
    async with session_factory() as session:
        await session.execute(insert(ConfigOverride).values(id=1, document=delta, meta={}))
        await session.commit()


@pytest.mark.asyncio
async def test_a_delta_store_becomes_a_document_at_the_next_load(
    session_factory, config_file
):
    await _write_delta(session_factory, {"workers": 9})
    async with session_factory() as session:
        config = await load_effective_config(config_file, session)
    assert config.workers == 9

    async with session_factory() as session:
        document, meta = await load_store(session)
    assert meta == {"format": STORE_FORMAT}
    # The whole document, not the delta: every key the file carried is here.
    assert document["plex"]["url"] == _document()["plex"]["url"]
    assert document["workers"] == 9


@pytest.mark.asyncio
async def test_the_delta_is_snapshotted_before_it_is_replaced(
    session_factory, config_file
):
    """What makes the conversion undoable: the delta is still on file, as a
    delta, so a restore re-runs the merge it described."""
    await _write_delta(session_factory, {"workers": 9})
    async with session_factory() as session:
        await load_effective_config(config_file, session)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ConfigOverrideSnapshot).order_by(ConfigOverrideSnapshot.id)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].reason == MIGRATE_REASON
    assert rows[0].format == 1
    assert rows[0].document == {"workers": 9}


@pytest.mark.asyncio
async def test_the_conversion_runs_once_and_then_the_file_is_not_read(
    session_factory, config_file
):
    """A converted row is a seeded store like any other: the file is gone
    after the conversion, and the loads that follow neither miss it nor
    convert anything a second time."""
    await _write_delta(session_factory, {"workers": 9})
    async with session_factory() as session:
        await load_effective_config(config_file, session)

    config_file.unlink()
    for _ in range(2):
        async with session_factory() as session:
            config = await load_effective_config(config_file, session)
    assert config.workers == 9

    async with session_factory() as session:
        count = len(
            (await session.execute(select(ConfigOverrideSnapshot))).scalars().all()
        )
    assert count == 1, "a second load must find format 2 and do nothing"


@pytest.mark.asyncio
async def test_a_delta_with_no_file_left_is_refused_rather_than_guessed(
    session_factory, tmp_path
):
    """A delta is meaningless without the base it was a delta OF. Refusing is
    the only honest answer -- inventing a document from the delta alone would
    boot the service with most of its configuration silently defaulted."""
    await _write_delta(session_factory, {"workers": 9})
    async with session_factory() as session:
        with pytest.raises(ValueError, match="delta"):
            await load_effective_config(tmp_path / "missing.yaml", session)
    async with session_factory() as session:
        row = (await session.execute(select(ConfigOverride))).scalar_one()
    assert row.document == {"workers": 9}, "the refusal rewrote the row it refused"


@pytest.mark.asyncio
async def test_a_merge_the_schema_refuses_leaves_the_delta_standing(
    session_factory, tmp_path
):
    """The conversion is the last load that reads the file, so writing a
    document the schema refuses would be unrecoverable: the application would
    not start, and the editor that could repair the row sits behind it. The
    refusal has to leave both the delta and the file exactly as they were."""
    path = tmp_path / "autoposter.yaml"
    path.write_text(yaml.safe_dump(_document()), encoding="utf-8")
    await _write_delta(session_factory, {"workers": "eleven"})

    async with session_factory() as session:
        with pytest.raises(ValueError, match="workers"):
            await load_effective_config(path, session)

    async with session_factory() as session:
        row = (await session.execute(select(ConfigOverride))).scalar_one()
        snapshots = (
            await session.execute(select(ConfigOverrideSnapshot))
        ).scalars().all()
    assert row.document == {"workers": "eleven"}, "the refusal rewrote the delta"
    assert row.meta == {}, "a refused conversion stamped the row as converted"
    assert snapshots == [], "a refused conversion left a snapshot orphan"


@pytest.mark.asyncio
async def test_a_write_that_lands_while_a_delta_converts_is_not_overwritten(
    session_factory, config_file
):
    """The window the row lock closes. A converter reads a delta, and before
    it writes, somebody else's write lands on the row -- another process
    converting, or a save the outgoing pod is still serving. The conversion
    has to find that under the lock and leave it alone: replacing a document
    nobody has seen, and the restart list beside it, with a merge of a delta
    that is no longer there would be a lost update with no 409 and no snapshot
    of what it took."""
    await _write_delta(session_factory, {"workers": 9})
    async with session_factory() as converting:
        stale, _ = await load_store(converting)

        async with session_factory() as other:
            await load_effective_config(config_file, other)
            saved = {**_document(), "workers": 4}
            await write_store(other, saved, store_meta(restart_paths=["plex"]))
            await other.commit()

        held = await migrate_delta_to_document(converting, _document(), stale)

    assert held["workers"] == 4, "the conversion ran on a delta that was already gone"
    async with session_factory() as session:
        row = (await session.execute(select(ConfigOverride))).scalar_one()
        snapshots = (
            await session.execute(select(ConfigOverrideSnapshot))
        ).scalars().all()
    assert row.document == saved
    assert row.meta == {"format": STORE_FORMAT, "restart_paths": ["plex"]}
    assert len(snapshots) == 1, "the late conversion snapshotted and wrote again"


@pytest.mark.asyncio
async def test_a_snapshot_is_stored_with_the_format_it_is_given(session_factory):
    """The keyword reaches the row rather than being left to the column's
    default. Seven is deliberately neither of the two real formats: asserting
    1 here would pass just as well with the argument dropped from the
    conversion's call, the model's default being 1."""
    async with session_factory() as session:
        await capture_snapshot(session, {"workers": 9}, "save", format=7)
        await session.commit()
    async with session_factory() as session:
        row = (await session.execute(select(ConfigOverrideSnapshot))).scalar_one()
    assert row.format == 7
