"""Row 87 -- lock/unlock/remove as a mass op's SOURCE.

Two STOP-and-file cells are deliberately absent and must stay absent:
``remove`` on the list-shaped ``genres`` (the row records no semantics for a
verb-as-source with no items supplied) and ``reset`` on any type (this project
holds no agent value to restore and has never called a Plex refresh).

The last test in this file is the gated-feature entry-point test the memory's
law requires: gate-off byte-identical, gate-on fires, second pass steady.
"""
from pathlib import Path

import pytest
import pytest_asyncio

from autoposter.config.loader import load_config
from autoposter.config.schema import OperationsConfig
from autoposter.db.models import MediaItem
from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import _PLEX_FIELD_NAMES, plan_edits, verb_edits

from test_mass_ops_fields import FakeItem

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


class FakeField:
    def __init__(self, name, locked):
        self.name = name
        self.locked = locked


class LockableItem(FakeItem):
    """A FakeItem that also reports Plex's per-field lock state."""

    def __init__(self, kind="movie", locks=(), **attrs):
        super().__init__(kind, **attrs)
        self.fields = [FakeField(name, locked) for name, locked in locks]


def test_no_verbs_configured_produces_nothing():
    assert verb_edits(LockableItem(), OperationsConfig()) == {}


def test_lock_sets_the_lock_bit_when_the_field_is_unlocked():
    item = LockableItem(studio="Warner", locks=[("studio", False)])
    operations = OperationsConfig(field_verbs={"studio": "lock"}, lock_apply=True)
    assert verb_edits(item, operations) == {"studio.locked": 1}


def test_lock_is_steady_when_the_field_is_already_locked():
    item = LockableItem(studio="Warner", locks=[("studio", True)])
    operations = OperationsConfig(field_verbs={"studio": "lock"}, lock_apply=True)
    assert verb_edits(item, operations) == {}


def test_unlock_clears_the_lock_bit():
    item = LockableItem(studio="Warner", locks=[("studio", True)])
    operations = OperationsConfig(field_verbs={"studio": "unlock"}, unlock_apply=True)
    assert verb_edits(item, operations) == {"studio.locked": 0}


def test_unlock_is_steady_when_the_field_is_already_unlocked():
    item = LockableItem(studio="Warner", locks=[("studio", False)])
    operations = OperationsConfig(field_verbs={"studio": "unlock"}, unlock_apply=True)
    assert verb_edits(item, operations) == {}


def test_an_apply_flag_left_off_changes_nothing():
    # Dry-run-by-default, per verb (facts C1.6).
    item = LockableItem(studio="Warner", locks=[("studio", True)])
    operations = OperationsConfig(field_verbs={"studio": "unlock"})
    assert verb_edits(item, operations) == {}


def test_remove_clears_a_scalar_and_locks_it():
    # Locked after clearing: a cleared-but-unlocked field is refilled by Plex's
    # own agent on its next refresh, which would make remove a no-op.
    item = LockableItem(studio="Warner", locks=[("studio", False)])
    operations = OperationsConfig(field_verbs={"studio": "remove"}, remove_apply=True)
    assert verb_edits(item, operations) == {"studio.value": "", "studio.locked": 1}


def test_remove_is_steady_on_an_already_empty_scalar():
    item = LockableItem(studio=None, locks=[("studio", True)])
    operations = OperationsConfig(field_verbs={"studio": "remove"}, remove_apply=True)
    assert verb_edits(item, operations) == {}


def test_remove_on_genres_does_nothing_because_it_is_stop_and_filed():
    item = LockableItem(genres=["Drama"], locks=[("genre", False)])
    operations = OperationsConfig(field_verbs={"genres": "remove"}, remove_apply=True)
    assert verb_edits(item, operations) == {}


def test_lock_on_genres_uses_plexs_singular_lock_field_name():
    # _PLEX_FIELD_NAMES["genres"] is the map's one asymmetric entry -- the
    # attribute is "genres" but Plex's lock field is "genre", singular. This
    # exercises that translation on the WRITE path (verb_edits), not just the
    # read path metadata_backup's capture_item goes through.
    item = LockableItem(genres=["Drama"], locks=[("genre", False)])
    operations = OperationsConfig(field_verbs={"genres": "lock"}, lock_apply=True)
    assert verb_edits(item, operations) == {"genre.locked": 1}


def test_unlock_on_genres_uses_plexs_singular_lock_field_name():
    item = LockableItem(genres=["Drama"], locks=[("genre", True)])
    operations = OperationsConfig(field_verbs={"genres": "unlock"}, unlock_apply=True)
    assert verb_edits(item, operations) == {"genre.locked": 0}


def test_reset_does_nothing_because_it_is_stop_and_filed():
    item = LockableItem(studio="Warner", locks=[("studio", False)])
    operations = OperationsConfig(field_verbs={"studio": "reset"}, remove_apply=True)
    assert verb_edits(item, operations) == {}


def test_a_verb_on_a_field_this_kind_cannot_carry_is_ignored():
    item = LockableItem(kind="episode", locks=[("genre", False)])
    operations = OperationsConfig(field_verbs={"genres": "unlock"}, unlock_apply=True)
    assert verb_edits(item, operations) == {}


def test_a_verbed_field_is_never_also_written_from_its_source():
    # The verb IS the source (the row's own phrasing), so a field under a verb
    # drops out of the value-write path entirely.
    item = LockableItem(studio="Warner", locks=[("studio", True)])
    operations = OperationsConfig(field_verbs={"studio": "unlock"}, unlock_apply=True)
    edits = plan_edits(item, GatheredFacts(studio="Universal"), operations)
    assert edits == {"studio.locked": 0}


def test_a_verb_whose_apply_is_off_does_not_fall_back_to_the_value_write():
    item = LockableItem(studio="Warner", locks=[("studio", True)])
    operations = OperationsConfig(field_verbs={"studio": "unlock"})
    assert plan_edits(item, GatheredFacts(studio="Universal"), operations) == {}


def test_an_unknown_verb_is_a_config_load_error():
    with pytest.raises(Exception):
        OperationsConfig(field_verbs={"studio": "obliterate"})


def test_an_unknown_field_name_is_a_config_load_error():
    with pytest.raises(Exception):
        OperationsConfig(field_verbs={"tagline": "unlock"})


def test_every_writable_field_has_a_plex_field_name():
    from autoposter.plex.writer import WRITABLE_BY_KIND

    every = set().union(*WRITABLE_BY_KIND.values())
    assert every <= set(_PLEX_FIELD_NAMES)


def test_the_verb_keys_default_to_the_pre_phase_behaviour():
    operations = OperationsConfig()
    assert operations.field_verbs == {}
    assert operations.lock_apply is False
    assert operations.unlock_apply is False
    assert operations.remove_apply is False


# --- the gated-feature entry-point test (Global Constraint 4) ---------------
#
# Through render.pipeline.apply_metadata -- the REAL seam, the one that gathers,
# persists, checks the row-35 exemption and calls apply_facts -- not through
# plan_edits. Three assertions, in the order the law states them.

from autoposter.facts.mdblist import NullMDBListClient  # noqa: E402
from autoposter.render.pipeline import apply_metadata  # noqa: E402

from test_mass_ops_fields import FakeTMDB, _item  # noqa: E402


@pytest.fixture
def config():
    return load_config(EXAMPLE)


@pytest_asyncio.fixture
async def media_item_id(session):
    media = MediaItem(rating_key="1", library="Movies", kind="movie", title="Heat")
    session.add(media)
    await session.flush()
    return media.id


class RecordingPlexItem(LockableItem):
    """Records every batched edit instead of talking to Plex."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.edits = []
        self.saved = 0

    def batchEdits(self):  # noqa: N802 - plexapi name
        pass

    def edit(self, **fields):
        self.edits.append(fields)

    def saveEdits(self):  # noqa: N802 - plexapi name
        self.saved += 1
        # What Plex would report on the next read: every field just written is
        # now in the state we asked for. This is what makes the second-pass
        # assertion below a real steady-state check and not a tautology.
        for payload in self.edits:
            for key, value in payload.items():
                name, _, suffix = key.rpartition(".")
                if suffix == "locked":
                    self.fields = [
                        f for f in self.fields if f.name != name
                    ] + [FakeField(name, bool(value))]
                elif suffix == "value":
                    setattr(self, name, value)

    def removeGenre(self, tags, locked=True):  # noqa: N802 - plexapi name
        pass

    def addGenre(self, tags, locked=True):  # noqa: N802 - plexapi name
        pass


@pytest.mark.asyncio
async def test_entry_point_gate_off_is_byte_identical(session, media_item_id, config):
    # (a) GATE OFF. field_verbs unset -- apply_metadata must produce exactly
    # the edits it produced before this row existed.
    plex_item = RecordingPlexItem(studio="Warner", locks=[("studio", True)])
    await apply_metadata(
        session, config, media_item_id, _item(), plex_item,
        FakeTMDB(GatheredFacts()), NullMDBListClient(),
    )
    assert plex_item.edits == []


@pytest.mark.asyncio
async def test_entry_point_gate_on_fires_and_the_second_pass_is_steady(
    session, media_item_id, config
):
    # (b) GATE ON: the verb fires through the real seam.
    config.operations.field_verbs = {"studio": "unlock"}
    config.operations.unlock_apply = True
    plex_item = RecordingPlexItem(studio="Warner", locks=[("studio", True)])

    await apply_metadata(
        session, config, media_item_id, _item(), plex_item,
        FakeTMDB(GatheredFacts()), NullMDBListClient(),
    )
    assert plex_item.edits == [{"studio.locked": 0}]
    assert plex_item.saved == 1

    # (c) SECOND PASS: steady state. Plex now reports the field unlocked, so
    # the verb has nothing left to do and no second write happens.
    await apply_metadata(
        session, config, media_item_id, _item(), plex_item,
        FakeTMDB(GatheredFacts()), NullMDBListClient(),
    )
    assert plex_item.edits == [{"studio.locked": 0}]
    assert plex_item.saved == 1
