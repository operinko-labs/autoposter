"""Roadmap row 99 -- the per-item metadata override store.

The table, its cascade, its parser and its one pure loader. Nothing here
writes to ``item_facts`` and nothing here reads a provider: an override row
holds only what an operator typed, which is the freezing hazard's own rule
(``frontend/src/api/overrides.ts:13-21``) read onto this row.

Keyed on ``media_items.id`` and never on ``rating_key``: the 2026-09-03
identity re-key MUTATES ``rating_key`` in place (``render/pipeline.py``'s
``_rekey_by_identity``) precisely so that children keyed on ``id`` survive the
move, and a rating-key-keyed table would silently detach on every re-key.
"""
from datetime import datetime

import pytest
from sqlalchemy import select

from autoposter.db.models import ItemMetadataOverride, MediaItem

from conftest import seed_media_item


async def _item(session, rating_key: str = "1", kind: str = "movie") -> MediaItem:
    return await seed_media_item(
        session, rating_key, library="Movies", kind=kind, title="Heat",
        year=1995, tmdb_id=949,
    )


async def test_an_override_row_round_trips(session):
    item = await _item(session)
    session.add(ItemMetadataOverride(
        item_id=item.id, field="tagline", value="A Los Angeles crime saga",
    ))
    await session.commit()

    row = (await session.execute(select(ItemMetadataOverride))).scalar_one()
    assert row.item_id == item.id
    assert row.field == "tagline"
    assert row.value == "A Los Angeles crime saga"
    assert isinstance(row.created_at, datetime)
    assert isinstance(row.updated_at, datetime)


async def test_one_row_per_item_and_field(session):
    """UNIQUE(item_id, field) is what makes a PUT an upsert rather than an
    append: without it, "the override for this field" would stop being a
    single answerable question the moment anyone pressed save twice."""
    from sqlalchemy.exc import IntegrityError

    item = await _item(session)
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="A24"))
    await session.commit()
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="MGM"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_two_fields_on_one_item_coexist(session):
    item = await _item(session)
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="A24"))
    session.add(ItemMetadataOverride(item_id=item.id, field="tagline", value="x"))
    await session.commit()

    rows = (await session.execute(select(ItemMetadataOverride))).scalars().all()
    assert sorted(row.field for row in rows) == ["studio", "tagline"]


async def test_deleting_the_item_cascades_the_overrides(session):
    """``scheduler/prune.py`` hard-deletes ``media_items`` rows, and every
    other per-item child of that table already cascades -- renders, facts,
    credits, dismissals and ``parent_id`` itself. An override that outlived
    its item would be a row pointing at nothing, invisible to every listing
    and impossible to delete from the panel."""
    item = await _item(session)
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="A24"))
    await session.commit()

    await session.delete(item)
    await session.commit()

    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() == []


async def test_an_override_survives_a_re_key(session):
    """The whole reason the FK is ``media_items.id``. A re-key mutates
    ``rating_key`` on the SAME row and leaves ``id`` alone, so the override
    stays attached with no work at all. A ``rating_key``-keyed table would
    have detached here, silently."""
    item = await _item(session, rating_key="16201")
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="A24"))
    await session.commit()

    item.rating_key = "165269"
    await session.commit()
    session.expire_all()

    row = (await session.execute(select(ItemMetadataOverride))).scalar_one()
    reloaded = (
        await session.execute(select(MediaItem).where(MediaItem.id == row.item_id))
    ).scalar_one()
    assert reloaded.rating_key == "165269"


# --- the vocabulary, the parser and the loader -----------------------------

from datetime import date  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from autoposter.plex.item_overrides import (  # noqa: E402
    BADGE_FACTS_FIELDS,
    OverrideValueError,
    TEXT_MAX_LENGTH,
    canonical_value,
    load_overrides,
    overlaid_badge_facts,
    parse_override,
    writable_fields,
)


def test_writable_fields_is_the_kind_s_own_set_sorted():
    """C3's field set, per libtype, from ``WRITABLE_BY_KIND`` rather than a
    second list -- two lists of writable fields would silently stop agreeing,
    and the 422 an operator gets for an unwritable field would then depend on
    which one the request happened to reach."""
    assert writable_fields("season") == [
        "audience_rating", "critic_rating", "summary", "title", "user_rating",
    ]
    assert "tagline" in writable_fields("movie")
    assert "tagline" not in writable_fields("episode")
    assert "original_title" in writable_fields("movie")
    assert "original_title" not in writable_fields("show")
    assert writable_fields("collection") == []


@pytest.mark.parametrize("field,raw,parsed,stored", [
    ("title", "  Heat  ", "Heat", "Heat"),
    ("sort_title", "Heat", "Heat", "Heat"),
    ("summary", "A crime saga.", "A crime saga.", "A crime saga."),
    ("tagline", "A Los Angeles crime saga", "A Los Angeles crime saga",
     "A Los Angeles crime saga"),
    ("content_rating", "R", "R", "R"),
    ("studio", "Warner Bros.", "Warner Bros.", "Warner Bros."),
    ("original_title", "Heat", "Heat", "Heat"),
    ("critic_rating", "8.65", 8.7, "8.7"),
    ("audience_rating", "7", 7.0, "7.0"),
    ("user_rating", "10", 10.0, "10.0"),
    ("originally_available", "1995-12-15", date(1995, 12, 15), "1995-12-15"),
    ("genres", '["Crime", "Drama"]', ["Crime", "Drama"], '["Crime", "Drama"]'),
])
def test_the_parse_and_canonical_forms_agree_per_field(field, raw, parsed, stored):
    """The stored string is the CANONICAL form the writer compares against.
    A rating stored as the operator typed it (``8.65``) against a writer that
    compares on the formatted value (``8.7``) would look like a change every
    pass -- which is exactly the churn ``plan_edits``' formatted comparison
    exists to prevent."""
    assert parse_override(field, raw) == parsed
    assert canonical_value(field, parse_override(field, raw)) == stored


@pytest.mark.parametrize("field,raw", [
    ("title", ""),
    ("title", "   "),
    ("summary", ""),
    ("critic_rating", "not a number"),
    ("critic_rating", "-1"),
    ("critic_rating", "10.1"),
    ("user_rating", ""),
    ("originally_available", "15/12/1995"),
    ("originally_available", "1995-13-45"),
    ("genres", "Crime, Drama"),
    ("genres", '"Crime"'),
    ("genres", "[1, 2]"),
    ("genres", "[]"),
])
def test_an_unparsable_value_is_refused_rather_than_stored(field, raw):
    """Refused loudly at the write, never stored and quietly ignored. The
    row-87 precedent: an accepted-but-ignored setting is indistinguishable
    from a working one that has just been switched off."""
    with pytest.raises(OverrideValueError):
        parse_override(field, raw)


def test_an_unknown_field_is_refused():
    """``added_at`` is roadmap row 227's, STOP-and-filed for having no source
    and no recorded semantics. It must not become reachable by the side door
    of a per-item value."""
    with pytest.raises(OverrideValueError):
        parse_override("added_at", "2026-01-01")


def test_a_refusal_never_carries_the_operator_s_value():
    """Row 213's served-string law, at the raise site. What an operator typed
    is theirs and may be anything -- a summary with a URL in it, a pasted
    token. The refusal names the FIELD and the SHAPE and nothing else, so
    that serving ``type(exc).__name__`` -- or even the message -- can never
    leak it."""
    secret = "https://plex.example/library?X-Plex-Token=abcd1234"
    with pytest.raises(OverrideValueError) as caught:
        parse_override("critic_rating", secret)
    assert secret not in str(caught.value)
    assert "abcd1234" not in str(caught.value)
    assert getattr(caught.value, "served_detail", False) is False


async def test_a_non_string_value_is_refused_by_its_own_shape():
    """A non-string body value -- reachable from a loosely typed PUT such as
    ``{"value": 5}`` -- must not be told to clear an override it never
    created. ``cannot be empty`` describes a different fault; the refusal
    here names the type and nothing else."""
    with pytest.raises(OverrideValueError) as caught:
        parse_override("title", 5)
    assert "cannot be empty" not in str(caught.value)
    assert "int" in str(caught.value)


def test_an_oversized_text_value_is_refused_without_echoing_it():
    """MIN-1's close. Unbounded, an oversized text override rides
    ``writer.py``'s single batched ``item.edit()`` call and can cost that item
    every other metadata write, every pass, if Plex refuses it on size. The
    cap is cheap and sits beside the other text-field refusals; the refusal
    itself follows row 213's served-string law -- it names the field and the
    length, never the value."""
    oversized = "x" * (TEXT_MAX_LENGTH + 1)
    with pytest.raises(OverrideValueError) as caught:
        parse_override("summary", oversized)
    assert oversized not in str(caught.value)
    assert str(TEXT_MAX_LENGTH) in str(caught.value)


def test_a_text_value_exactly_at_the_cap_is_accepted():
    at_cap = "x" * TEXT_MAX_LENGTH
    assert parse_override("summary", at_cap) == at_cap


async def test_the_loader_returns_typed_values_keyed_by_our_field_names(session):
    item = await _item(session)
    session.add(ItemMetadataOverride(item_id=item.id, field="critic_rating", value="8.7"))
    session.add(ItemMetadataOverride(
        item_id=item.id, field="originally_available", value="1995-12-15"
    ))
    session.add(ItemMetadataOverride(item_id=item.id, field="tagline", value="x"))
    await session.commit()

    assert await load_overrides(session, item.id) == {
        "critic_rating": 8.7,
        "originally_available": date(1995, 12, 15),
        "tagline": "x",
    }


async def test_the_loader_is_empty_for_an_item_with_no_rows(session):
    item = await _item(session)
    assert await load_overrides(session, item.id) == {}


async def test_the_loader_reads_only_this_item_s_rows(session):
    one = await _item(session, rating_key="1")
    two = await _item(session, rating_key="2")
    session.add(ItemMetadataOverride(item_id=one.id, field="studio", value="A24"))
    session.add(ItemMetadataOverride(item_id=two.id, field="studio", value="MGM"))
    await session.commit()

    assert await load_overrides(session, one.id) == {"studio": "A24"}


async def test_a_row_the_writer_can_no_longer_parse_is_skipped_not_raised(session):
    """Defence in depth against a hand-edited row or a future narrowing of
    the parser. A pass must not die on the way to the artwork because one
    field's stored string stopped parsing; it logs and writes the rest."""
    item = await _item(session)
    session.add(ItemMetadataOverride(item_id=item.id, field="critic_rating", value="???"))
    session.add(ItemMetadataOverride(item_id=item.id, field="studio", value="A24"))
    await session.commit()

    assert await load_overrides(session, item.id) == {"studio": "A24"}


def test_the_badge_overlay_covers_exactly_the_three_fields_the_badge_reads():
    """``apply_badges`` reads ``facts.critic_rating``,
    ``facts.audience_rating`` and, through ``BadgeInputs``,
    ``facts.content_rating`` -- and nothing else off the facts object reaches
    ``badge_values``. Overlaying more would be state nothing consumes."""
    assert BADGE_FACTS_FIELDS == ("critic_rating", "audience_rating", "content_rating")


def test_the_overlay_answers_the_override_and_delegates_everything_else():
    base = SimpleNamespace(
        critic_rating=4.9, audience_rating=6.3, content_rating="17", studio="MGM",
    )
    view = overlaid_badge_facts(base, {"critic_rating": 8.7, "studio": "A24"})

    assert view.critic_rating == 8.7          # overridden
    assert view.audience_rating == 6.3        # delegated
    assert view.content_rating == "17"        # delegated
    assert view.studio == "MGM"               # NOT overlaid: not a badge input


def test_the_overlay_returns_the_object_unchanged_when_there_is_nothing_to_lay_on():
    """Gate-off and no-overrides must be byte-identical to today: the SAME
    object, not a wrapper around it, so that no consumer can tell the feature
    exists even by identity."""
    base = SimpleNamespace(critic_rating=4.9)
    assert overlaid_badge_facts(base, {}) is base
    assert overlaid_badge_facts(base, {"studio": "A24"}) is base


def test_the_overlay_never_mutates_what_it_was_handed():
    """``apply_badges`` is handed the ORM ``ItemFacts`` row the session is
    tracking. Mutating it would be flushed into ``item_facts`` by the next
    commit in that block -- the provider's record poisoned by accident, which
    is the one thing this row must never do."""
    base = SimpleNamespace(critic_rating=4.9)
    overlaid_badge_facts(base, {"critic_rating": 8.7})
    assert base.critic_rating == 4.9


# --- the gate --------------------------------------------------------------

from pathlib import Path  # noqa: E402

from autoposter.config.live import FROZEN_SECTIONS  # noqa: E402
from autoposter.config.loader import load_config, render_version  # noqa: E402
from autoposter.config.schema import OperationsConfig  # noqa: E402

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def test_the_gate_defaults_off():
    """A feature nobody has configured changes nothing. C8: default False."""
    assert OperationsConfig().item_overrides_enabled is False


def test_the_gate_is_live_rather_than_restart_only():
    """``FROZEN_SECTIONS`` names four ``operations.*`` paths and no whole
    ``operations`` prefix, so a new scalar there hot-swaps through
    ``swap_config`` for free. Asserted as a standing guard rather than argued:
    a later phase that froze this key would make the panel lie about when a
    change takes effect."""
    assert "operations" not in FROZEN_SECTIONS
    assert "operations.item_overrides_enabled" not in FROZEN_SECTIONS


def test_the_gate_does_not_move_the_render_version():
    """The storm answer for the render side, asserted rather than argued.
    ``config/loader.py::render_version`` hashes ``artwork`` +
    ``library_folders`` + the four roots and names ``operations`` among the
    sections it excludes, so two configs differing only in this key produce
    the same version and turning the feature on re-renders NOTHING."""
    off = load_config(EXAMPLE)
    on = load_config(EXAMPLE)
    on.operations.item_overrides_enabled = True
    assert render_version(off) == render_version(on)
