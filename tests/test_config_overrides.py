"""The database overrides layer, the merged load, and the generation holder.

The overrides document is what the UI edits: the mounted YAML stays
git/Flux-owned, and the operator's deltas are merged over it at load time.
Three properties matter enough to be pinned here -- the merge replaces rather
than accumulates, a ``secrets`` key never survives it, and an empty (or
absent) document is exactly the old file-only behaviour.
"""
import logging
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

# From conftest rather than from os.environ directly, because the value that
# matters is the one conftest *derived*: under pytest-xdist it appends this
# worker's name, and the CLIs driven below have to reach the same database the
# ``session`` fixture wrote the overrides row into. conftest writes it back to
# AUTOPOSTER_TEST_DATABASE_URL too, so reading the variable would also work --
# but only because conftest is imported first, which is a load-bearing ordering
# that an import states and a getenv leaves to be rediscovered.
from conftest import TEST_DB_URL

from autoposter.adopt import __main__ as adopt_main
from autoposter.collections import __main__ as collections_main
from autoposter.config.holder import ConfigHolder
from autoposter.config.loader import load_config
from autoposter.config.overrides import (
    load_effective_config,
    load_overrides_document,
    merge_overrides,
    without_migrated_settings,
)
from autoposter.config.schema import Secrets
from autoposter.db.models import ConfigOverride

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

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


def test_a_library_named_secrets_is_not_rejected():
    """Roadmap row 92. ``libraries`` is keyed on Plex library NAMES, not
    config section names -- a library literally named "secrets" is a real
    library, not an attempt to smuggle an environment secret into the
    document, and this is the one level the check has to look past."""
    merged = merge_overrides(
        {}, {"libraries": {"secrets": {"operations": {"write_to_plex": False}}}}
    )
    assert merged["libraries"]["secrets"]["operations"]["write_to_plex"] is False


def test_a_secrets_key_inside_a_librarys_own_settings_is_still_rejected():
    """The exemption is narrow: only the library NAME (the key directly under
    ``libraries``) is exempt, not a key inside that library's own block."""
    with pytest.raises(ValueError, match=r"libraries\.Movies\.secrets"):
        merge_overrides({}, {"libraries": {"Movies": {"secrets": {"x": "y"}}}})


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


# --- sections that left the schema -------------------------------------------


def _overrides_warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and record.name == "autoposter.config.overrides"
    ]


def _strip_warnings(caplog) -> list[str]:
    """The strip's own warnings, apart from the one-time conversion line every
    one of these delta-era rows also logs as it becomes a document."""
    return [line for line in _overrides_warnings(caplog) if "dropping stale" in line]


async def test_a_stored_version_check_section_does_not_brick_the_boot(session, caplog):
    """The migration hazard this strip exists for. ``version_check`` was
    live-editable in the settings editor right up to the commit that took its
    three fields out of the config file, so any deployment whose operator
    ever saved that section carries the key in its ``config_overrides`` row --
    and ``Config._version_check_moved_to_an_env_var`` refuses the key in *any*
    document it validates, the merged one included.

    Refusing it here would fail the pod at boot with a message telling the
    operator to delete a block from ``autoposter.yaml``, which does not have
    it, while the only thing that could clear the row -- the settings editor --
    sits behind the app that will not start. So the key is dropped and the boot
    succeeds."""
    await _store(session, {"version_check": {"project": "operinko-labs"}, "workers": 2})

    with caplog.at_level(logging.WARNING):
        config = await load_effective_config(EXAMPLE, session)

    # The untouched neighbour still applies: this drops one key, not the row.
    assert config.workers == 2
    assert "version_check" not in config.model_dump()


async def test_dropping_a_stored_version_check_section_says_so(session, caplog):
    """Silent would be worse than the refusal: an operator whose stored
    override stopped doing anything is owed the reason, and the way out.

    Exactly three times for this row, on the one boot that converts it, and
    the three are countable rather than incidental: a row holding nothing but
    a stale section strips to empty, so the load reads it once, the seed's own
    locked re-read reads it again before deciding the row is not its to
    replace, and the conversion reads it a third time under the lock -- which
    it must, because the delta it converts has to be the one the lock holds.
    Anything above three is the spam this message must not become."""
    await _store(session, {"version_check": {"project": "operinko-labs"}})

    with caplog.at_level(logging.WARNING):
        await load_effective_config(EXAMPLE, session)

    warnings = _strip_warnings(caplog)
    assert len(warnings) == 3
    assert len(set(warnings)) == 1
    assert "dropping stale version_check from the stored configuration" in warnings[0]
    assert "no longer a setting" in warnings[0]
    # Self-healing, and the message has to say so: the editor can no longer
    # produce the key, and the document is always written whole.
    assert "next saved" in warnings[0]


async def test_an_ordinary_stored_document_warns_about_nothing(session, caplog):
    """The cost every other deployment pays for the strip: none, and no noise.

    The one line this load does write is not the strip's: it is the
    conversion, said once in the life of a deployment by the boot that turns
    this delta into a document. Pinned to exactly that one line, so neither
    message can grow a second copy unnoticed."""
    await _store(session, {"workers": 2})

    with caplog.at_level(logging.WARNING):
        config = await load_effective_config(EXAMPLE, session)

    assert config.workers == 2
    [warning] = _overrides_warnings(caplog)
    assert "has been merged into a whole document" in warning


async def test_the_stored_document_reader_drops_it_too(session):
    """Stripped at the read, not at the merge, so ``GET /api/config`` cannot
    serve -- and the page cannot then send back, as part of the whole document
    it round-trips -- a setting the schema no longer has a field for."""
    await _store(session, {"version_check": {"project": "x"}, "workers": 2})

    assert await load_overrides_document(session) == {"workers": 2}


async def test_a_version_check_key_in_the_yaml_file_is_still_refused(tmp_path, session):
    """The other half of the pair, asserted here beside the strip so the
    asymmetry is deliberate on the page rather than only in a docstring: the
    file is operator-actionable -- delete the block -- so it stays a hard
    refusal. Only the document an operator cannot reach self-heals."""
    bad = tmp_path / "autoposter.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8")
        + "\nversion_check:\n  project: operinko-labs\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="version_check"):
        await load_effective_config(bad, session)


# --- settings that left the schema -------------------------------------------


async def test_stored_settings_that_left_the_schema_are_dropped_at_the_leaf(
    session, caplog
):
    """The 422 a v0.4.0 deployment met on every save. ``providers.favourite``
    and ``ArtKindConfig``'s ``min_width`` left the schema in ``f8a7440``, but
    the mounted file still named them and the seed copied them into the stored
    document -- and from then on every whole-document write walked
    ``unknown_key_paths`` over them and refused. Nothing in the UI could take
    them out, because the editor never rendered them.

    Dropped at their exact positions, with their neighbours untouched: these
    are individual settings that left, not whole sections."""
    await _store(
        session,
        {
            "providers": {"favourite": "TMDB", "order": ["tmdb"]},
            "artwork": {"poster": {"min_width": 1000, "add_border": True}},
        },
    )

    with caplog.at_level(logging.WARNING):
        document = await load_overrides_document(session)

    assert document == {
        "providers": {"order": ["tmdb"]},
        "artwork": {"poster": {"add_border": True}},
    }
    warnings = _strip_warnings(caplog)
    assert len(warnings) == 2, "once per key, and only for the keys that are there"
    assert "providers.favourite" in warnings[0]
    assert "artwork.poster.min_width" in warnings[1]


async def test_a_section_the_strip_empties_goes_with_the_leaf(session):
    """A block that said nothing but the stale key is not left behind as
    ``{}``. The write path refuses an empty object on a delta-era store
    (``api/routes.py``'s empty-leaf gate), so leaving one here would trade one
    unsaveable document for another."""
    await _store(session, {"providers": {"favourite": "TMDB"}, "workers": 2})

    assert await load_overrides_document(session) == {"workers": 2}


def test_a_document_with_nothing_stale_in_it_is_the_object_handed_in():
    """The cost every deployment whose store is clean pays: a membership test
    and no allocation, which is the posture the section strip already took."""
    document = {"workers": 2, "providers": {"order": ["tmdb"]}}

    assert without_migrated_settings(document) is document


async def test_a_file_naming_a_stale_setting_seeds_a_store_without_it(
    tmp_path, session
):
    """The half that keeps a fresh deployment out of this state altogether.

    The mounted file is git-owned and nobody edits it for a key that stopped
    doing anything, so it is free to still name one -- which is exactly how the
    v0.4.0 deployment got its stale keys, through the seed. A store nobody has
    saved yet must not start life holding a document the write path refuses.

    The file's SECTION refusal is untouched by this, and the asymmetry is the
    point (``test_a_version_check_key_in_the_yaml_file_is_still_refused``): a
    setting that left was never refused in a file, so dropping it changes
    nothing about what the file means, while a section that left is an error an
    operator can act on by deleting the block."""
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    document["providers"]["favourite"] = "TMDB"
    seeded = tmp_path / "autoposter.yaml"
    seeded.write_text(yaml.safe_dump(document), encoding="utf-8")

    config = await load_effective_config(seeded, session)

    assert config.providers.order, "the file's own settings still seeded"
    row = (await session.execute(select(ConfigOverride))).scalar_one()
    assert "favourite" not in row.document["providers"]


async def test_a_typo_beside_a_stale_setting_is_left_where_it_is(session):
    """The strip knows the paths that left the schema and nothing else.
    ``providers.favorite_typo`` never was a setting, so dropping it would turn
    a 422 that names the typo into an override that silently does nothing --
    which is the failure ``unknown_key_paths`` exists to stop."""
    await _store(session, {"providers": {"favourite": "TMDB", "favorite_typo": "x"}})

    assert await load_overrides_document(session) == {
        "providers": {"favorite_typo": "x"}
    }


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


# The API entry point's own overrides read lives in its lifespan rather than
# in ``main.build()`` -- ``uvicorn --factory`` calls ``build()`` from inside a
# running event loop, where nothing can be awaited or bridged. It is covered
# in tests/test_app.py, which is where the fixture that drives a lifespan
# lives:
#   test_the_lifespan_boots_on_the_effective_config_not_the_file_alone
#   test_the_lifespan_builds_the_plex_client_from_the_effective_config
# The two CLIs below load theirs in their own async mains and are unaffected.


async def test_collections_cli_reads_the_overrides(session, monkeypatch, caplog):
    """Flipping ``collections.enabled`` off in the overrides stops the CLI
    before it ever reaches Plex; with the file alone the example config has it
    enabled and this would connect.

    ``playlists.enabled`` is flipped off in the same override: roadmap row
    98a's sibling pass runs from the same CLI guard, EXAMPLE ships
    ``playlists.enabled: true``, and a playlists half left on would still
    reach Plex and defeat this test's whole point -- see
    ``collections/__main__.py``'s ``if not config.collections.enabled and not
    config.playlists.enabled``.
    """
    for name, value in SECRET_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(collections_main, "CONFIG_PATH", EXAMPLE)
    monkeypatch.setattr(collections_main, "PlexServer", _refuse_to_connect)
    assert load_config(EXAMPLE).collections.enabled is True

    await _store(
        session, {"collections": {"enabled": False}, "playlists": {"enabled": False}}
    )
    with caplog.at_level(logging.INFO, logger=collections_main.__name__):
        await collections_main.main()
    assert "collections and playlists are disabled in config" in caplog.text


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


def test_the_document_revision_is_stable_across_key_order_and_whitespace():
    """The token is about what the document says, not how it was serialised.
    A page that rebuilt the same deltas in a different order must not be told
    its seed is stale."""
    from autoposter.config.overrides import document_revision

    one = {"badges": {"enabled": True}, "workers": 9}
    other = {"workers": 9, "badges": {"enabled": True}}
    assert document_revision(one) == document_revision(other)


def test_the_empty_document_revision_is_the_pinned_constant():
    """Pinned by value, not by re-deriving it: a change to the canonical dump
    would invalidate every seed every open page is holding, and that must be a
    deliberate act with a red test in front of it."""
    from autoposter.config.overrides import (
        EMPTY_DOCUMENT_REVISION,
        document_revision,
    )

    assert EMPTY_DOCUMENT_REVISION == (
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )
    assert document_revision({}) == EMPTY_DOCUMENT_REVISION


def test_a_changed_value_changes_the_revision():
    from autoposter.config.overrides import document_revision

    assert document_revision({"workers": 9}) != document_revision({"workers": 10})


def test_the_first_save_lock_key_is_pinned_and_fits_a_postgres_bigint():
    """Pinned by value, like EMPTY_DOCUMENT_REVISION and for the same kind of
    reason: during a rolling deploy two pods run at once, and two pods holding
    different keys would not serialise against each other at all -- which is
    the one moment the lock exists for. Changing it must be deliberate.

    Also pinned as *derived*: the key is the top 63 bits of the sha256 of what
    it protects, so it cannot collide by accident with an advisory lock some
    other part of this database picks by hand. `pg_advisory_xact_lock(bigint)`
    would raise on anything that does not fit a signed 64-bit integer.
    """
    import hashlib

    from autoposter.config.overrides import OVERRIDES_INSERT_LOCK_KEY

    assert OVERRIDES_INSERT_LOCK_KEY == 4907594664404778877
    digest = hashlib.sha256(b"autoposter.config_overrides.insert").digest()
    assert OVERRIDES_INSERT_LOCK_KEY == int.from_bytes(digest[:8], "big") >> 1
    assert 0 < OVERRIDES_INSERT_LOCK_KEY < 2**63
