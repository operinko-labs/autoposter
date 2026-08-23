"""The database overrides layer, the merged load, and the generation holder.

The overrides document is what the UI edits: the mounted YAML stays
git/Flux-owned, and the operator's deltas are merged over it at load time.
Three properties matter enough to be pinned here -- the merge replaces rather
than accumulates, a ``secrets`` key never survives it, and an empty (or
absent) document is exactly the old file-only behaviour.
"""
import asyncio
import logging
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from autoposter import main as main_module
from autoposter.adopt import __main__ as adopt_main
from autoposter.collections import __main__ as collections_main
from autoposter.config.holder import ConfigHolder
from autoposter.config.loader import load_config
from autoposter.config.overrides import (
    load_effective_config,
    load_overrides_document,
    merge_overrides,
)
from autoposter.config.schema import Secrets
from autoposter.db.models import ConfigOverride

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

TEST_DB_URL = os.environ["AUTOPOSTER_TEST_DATABASE_URL"]

SECRET_ENV = {
    "AUTOPOSTER_DATABASE_URL": TEST_DB_URL,
    "AUTOPOSTER_PLEX_TOKEN": "token",
    "AUTOPOSTER_TMDB_TOKEN": "token",
    "AUTOPOSTER_TVDB_APIKEY": "key",
    "AUTOPOSTER_FANART_APIKEY": "key",
    "AUTOPOSTER_WEBHOOK_SECRET": "secret",
}


async def _store(session, document: dict) -> None:
    session.add(ConfigOverride(id=1, document=document))
    await session.commit()


# --- merge semantics ---------------------------------------------------------


def test_scalar_in_the_overrides_replaces_the_base_value():
    assert merge_overrides({"workers": 5}, {"workers": 2})["workers"] == 2


def test_a_list_is_replaced_wholesale_not_concatenated():
    """Every list in this config is a complete statement of intent, so an
    operator dropping a provider must get a shorter list back, not the union."""
    merged = merge_overrides(
        {"providers": {"order": ["TMDB", "TVDB", "Fanart"]}},
        {"providers": {"order": ["TMDB"]}},
    )
    assert merged["providers"]["order"] == ["TMDB"]


def test_nested_dicts_merge_key_by_key():
    merged = merge_overrides(
        {"artwork": {"output_quality": "92%", "poster": {"min_point_size": 83}}},
        {"artwork": {"poster": {"min_point_size": 90}}},
    )
    assert merged["artwork"] == {
        "output_quality": "92%",
        "poster": {"min_point_size": 90},
    }


def test_a_dict_replaces_a_scalar_and_a_scalar_replaces_a_dict():
    assert merge_overrides({"a": 1}, {"a": {"b": 2}})["a"] == {"b": 2}
    assert merge_overrides({"a": {"b": 2}}, {"a": 1})["a"] == 1


def test_neither_argument_is_mutated():
    base = {"artwork": {"poster": {"min_point_size": 83}}}
    overrides = {"artwork": {"poster": {"min_point_size": 90}}}
    merge_overrides(base, overrides)
    assert base == {"artwork": {"poster": {"min_point_size": 83}}}
    assert overrides == {"artwork": {"poster": {"min_point_size": 90}}}


def test_a_top_level_secrets_key_is_rejected():
    with pytest.raises(ValueError, match="secrets"):
        merge_overrides({}, {"secrets": {"plex_token": "leaked"}})


def test_a_nested_secrets_key_is_rejected():
    """Rejected wherever it appears, not just at the root: the point is that no
    token ever reaches a database row, and nesting one under ``plex`` would
    put it there just as effectively."""
    with pytest.raises(ValueError, match=r"plex\.secrets"):
        merge_overrides({}, {"plex": {"secrets": {"token": "leaked"}}})


def test_a_rejected_document_merges_nothing():
    """Never half-applied: the ValueError comes before any merging, so a
    document carrying one bad key does not get its other keys through."""
    base = {"workers": 5}
    with pytest.raises(ValueError):
        merge_overrides(base, {"workers": 2, "secrets": {"x": "y"}})
    assert base == {"workers": 5}


# --- the merged load ---------------------------------------------------------


async def test_no_overrides_row_is_identical_to_load_config(session):
    effective = await load_effective_config(EXAMPLE, session)
    plain = load_config(EXAMPLE)
    assert effective.model_dump(mode="json") == plain.model_dump(mode="json")
    assert effective.version == plain.version


async def test_an_empty_document_is_identical_to_load_config(session):
    await _store(session, {})
    effective = await load_effective_config(EXAMPLE, session)
    assert effective.model_dump(mode="json") == load_config(EXAMPLE).model_dump(mode="json")


async def test_load_overrides_document_returns_the_stored_document(session):
    await _store(session, {"workers": 2})
    assert await load_overrides_document(session) == {"workers": 2}


async def test_an_override_reaches_the_validated_config(session):
    await _store(session, {"workers": 2, "plex": {"resolve_max_attempts": 3}})
    config = await load_effective_config(EXAMPLE, session)
    assert config.workers == 2
    assert config.plex.resolve_max_attempts == 3
    # Untouched neighbours keep the file's values.
    assert config.plex.excluded_libraries == load_config(EXAMPLE).plex.excluded_libraries


async def test_an_artwork_override_changes_the_version(session):
    """``version`` is re-derived from the merged config, so an overridden
    render-affecting setting invalidates fingerprints exactly as editing the
    file would."""
    await _store(session, {"artwork": {"output_quality": "88%"}})
    config = await load_effective_config(EXAMPLE, session)
    assert config.artwork.output_quality == "88%"
    assert config.version != load_config(EXAMPLE).version


async def test_a_scheduler_override_does_not_change_the_version(session):
    """And a setting that cannot change a pixel must not strand ~16,000
    adopted fingerprints -- the same rule the file path already obeys."""
    await _store(session, {"scheduler": {"drift_batch_size": 250}})
    config = await load_effective_config(EXAMPLE, session)
    assert config.scheduler.drift_batch_size == 250
    assert config.version == load_config(EXAMPLE).version


async def test_an_invalid_override_fails_validation(session):
    """Validated whole: a merged document that the schema rejects raises
    rather than producing a config with the bad value in it."""
    await _store(session, {"artwork": {"poster": {"text": {"text_offset": "300"}}}})
    with pytest.raises(ValueError, match="explicit sign"):
        await load_effective_config(EXAMPLE, session)


async def test_a_stored_secrets_key_is_rejected_at_load(session):
    """A row written by something other than the (yet to exist) API endpoint
    still cannot smuggle a secret into the running config."""
    await _store(session, {"secrets": {"plex_token": "leaked"}})
    with pytest.raises(ValueError, match="secrets"):
        await load_effective_config(EXAMPLE, session)


# --- the generation holder ---------------------------------------------------


def test_holder_starts_on_the_initial_config():
    config = load_config(EXAMPLE)
    assert ConfigHolder(config).current is config


def test_a_swap_is_visible_through_current():
    first = load_config(EXAMPLE)
    second = load_config(EXAMPLE)
    holder = ConfigHolder(first)
    holder.swap(second)
    assert holder.current is second


def test_a_reader_holding_the_holder_sees_the_new_generation():
    """The point of handing consumers the holder rather than the instance: a
    closure captured before the swap reads the new config after it."""
    holder = ConfigHolder(load_config(EXAMPLE))

    def read_workers():
        return holder.current.workers

    before = read_workers()
    swapped = load_config(EXAMPLE)
    swapped.workers = before + 1
    holder.swap(swapped)
    assert read_workers() == before + 1


# --- the entry points load the effective config -------------------------------


async def test_main_effective_config_applies_the_overrides(session, monkeypatch):
    """``main.effective_config`` is synchronous (uvicorn calls ``build()``
    before any event loop exists), so it is exercised here from a worker
    thread, which is the closest thing to its real caller a test can be."""
    monkeypatch.setattr(main_module, "CONFIG_PATH", EXAMPLE)
    await _store(session, {"workers": 3})
    config = await asyncio.to_thread(main_module.effective_config, TEST_DB_URL)
    assert config.workers == 3


async def test_collections_cli_reads_the_overrides(session, monkeypatch, caplog):
    """Flipping ``collections.enabled`` off in the overrides stops the CLI
    before it ever reaches Plex; with the file alone the example config has it
    enabled and this would connect."""
    for name, value in SECRET_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(collections_main, "CONFIG_PATH", EXAMPLE)
    monkeypatch.setattr(collections_main, "PlexServer", _refuse_to_connect)
    assert load_config(EXAMPLE).collections.enabled is True

    await _store(session, {"collections": {"enabled": False}})
    with caplog.at_level(logging.INFO, logger=collections_main.__name__):
        await collections_main.main()
    assert "collections are disabled in config" in caplog.text


async def test_adopt_cli_reads_the_overrides(session, monkeypatch, caplog):
    """``adopt.apply`` overridden to true makes the run a real one; the report
    line loses its dry-run suffix. ``adopt.libraries`` is overridden to the
    empty list, which is also the list-replacement rule doing real work."""
    for name, value in SECRET_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(adopt_main, "CONFIG_PATH", EXAMPLE)
    monkeypatch.setattr(adopt_main, "PlexServer", lambda *a, **k: object())
    assert load_config(EXAMPLE).adopt.apply is False

    await _store(session, {"adopt": {"apply": True, "libraries": []}})
    with caplog.at_level(logging.INFO, logger=adopt_main.__name__):
        await adopt_main.main()
    assert "total:" in caplog.text
    assert "dry run" not in caplog.text


def _refuse_to_connect(*args, **kwargs):
    raise AssertionError("the CLI reached Plex despite collections being disabled")


def test_secrets_still_come_from_the_environment(monkeypatch):
    """The overrides layer changes nothing about where secrets live."""
    for name, value in SECRET_ENV.items():
        monkeypatch.setenv(name, value)
    assert Secrets.from_env().plex_token == "token"


# --- the store's own invariants ----------------------------------------------


async def test_a_second_overrides_row_is_refused_by_the_database(session):
    """One row, pinned to id=1, enforced rather than merely documented.

    Every reader selects id=1 and the writer upserts id=1, so a second row
    would sit in the table looking like configuration that is in force while
    having no effect at all -- the most expensive kind of wrong a config store
    can be.
    """
    await _store(session, {"workers": 2})
    session.add(ConfigOverride(id=2, document={"workers": 3}))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_a_non_dict_document_fails_with_a_clear_error(session):
    """Unreachable through the API, which only ever writes an object -- but
    JSONB will hold a list quite happily if the row is edited by hand, and the
    merge would otherwise fail with an AttributeError three frames down."""
    await session.execute(
        text("INSERT INTO config_overrides (id, document) VALUES (1, '[1, 2, 3]'::jsonb)")
    )
    await session.commit()
    with pytest.raises(ValueError, match="must be a JSON object"):
        await load_overrides_document(session)
