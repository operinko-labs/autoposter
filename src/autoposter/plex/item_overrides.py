"""Roadmap row 99 -- the per-item metadata override vocabulary and loader.

What an override IS: one field, on one item, whose value the operator has
declared. It beats the provider-facts pass for that field on that item, it is
re-applied every pass, and deleting it is how it is reverted.

**Nothing here ever writes a row, and nothing here ever reads a provider.**
This module parses, canonicalises, loads and overlays; the only thing that
creates an override row is the PUT endpoint, from the body the operator sent.
That is the freezing hazard (``frontend/src/api/overrides.ts:13-21``) read
onto this row: a "seed this item's overrides from what Plex currently says"
convenience -- the obvious panel affordance -- would store today's values as
overrides and freeze them against every future provider change, and reverting
a field would stop meaning anything. It is forbidden by a named test rather
than by discipline.

**Why one gate and no ``_apply`` flag** (the asymmetry with row 87, written
down rather than left to look like an oversight). Row 87's verbs are
LIBRARY-WIDE: ``operations.field_verbs`` names a field and every item in every
library gets it, so a dry run that reports what it would do before anything is
written is the only safe default, and each verb has its own ``*_apply``. A
row-99 override is the opposite shape -- one item, typed deliberately into a
panel, confirmed there. The panel's own confirm IS the consent, and a second
global flag would mean an operator who typed a value, confirmed it and watched
it save could still find that nothing was written. So there is exactly one
gate, ``operations.item_overrides_enabled``, defaulting to off because a
feature nobody has configured must change nothing; with it off, existing rows
are IGNORED rather than deleted, so switching it back on restores the
operator's work rather than finding it gone.

**The canonical string, and why the parse happens at write time.** The writer
compares what it is about to send against what Plex currently reports -- on
the FORMATTED value for ratings and on ``%Y-%m-%d`` for dates -- because that
comparison is what makes a second pass write nothing. A store holding
``"8.65"`` against a writer comparing ``"8.7"`` would look like a change every
single pass. So a value parses at the PUT, and what is stored is the form the
comparison is made in. It also means a typo is refused while the operator can
still see what they typed.
"""
import json
import logging
from datetime import date

from sqlalchemy import select

from autoposter.db.models import ItemMetadataOverride
from autoposter.plex.writer import _PLEX_FIELD_NAMES, WRITABLE_BY_KIND

logger = logging.getLogger(__name__)

__all__ = [
    "BADGE_FACTS_FIELDS",
    "DATE_FIELDS",
    "LIST_FIELDS",
    "OverrideValueError",
    "RATING_FIELDS",
    "TEXT_FIELDS",
    "canonical_value",
    "load_overrides",
    "overlaid_badge_facts",
    "parse_override",
    "writable_fields",
]


class OverrideValueError(ValueError):
    """An override value this service will not store.

    Deliberately does NOT set ``served_detail`` (roadmap row 213): the value
    that failed is operator-typed free text and may be anything at all, so a
    served refusal gets the bare class name. Every message raised below names
    the FIELD and the SHAPE and never interpolates the value -- pinned by
    ``tests/test_item_overrides_store.py``'s
    ``test_a_refusal_never_carries_the_operator_s_value``.
    """


# The four field shapes, and every writable field is in exactly one of them.
# Spelled as four sets rather than one dict of parsers so that the membership
# tests below read as the sentences the roadmap close's field table is
# written from.
TEXT_FIELDS = frozenset({
    "title", "sort_title", "summary", "tagline",
    "content_rating", "studio", "original_title",
})
RATING_FIELDS = frozenset({"critic_rating", "audience_rating", "user_rating"})
DATE_FIELDS = frozenset({"originally_available"})
LIST_FIELDS = frozenset({"genres"})

# The ONLY three facts values ``render/pipeline.py::apply_badges`` reads off
# the facts object: ``critic_rating`` and ``audience_rating`` directly, and
# ``content_rating`` through ``BadgeInputs``. Nothing else on that object
# reaches ``badge_values``, so overlaying anything else would be state no
# consumer can observe.
BADGE_FACTS_FIELDS = ("critic_rating", "audience_rating", "content_rating")


def writable_fields(kind: str) -> list[str]:
    """The field names an operator may override on an item of this kind.

    Derived from ``WRITABLE_BY_KIND`` rather than kept as a second list: two
    lists of writable fields would silently stop agreeing, and the 422 an
    operator gets for an unwritable field would then depend on which of them
    the request happened to reach. Intersected with ``_PLEX_FIELD_NAMES``
    because a field with no Plex attribute could be stored and never written.
    """
    return sorted(WRITABLE_BY_KIND.get(kind, set()) & set(_PLEX_FIELD_NAMES))


def parse_override(field: str, raw: str) -> object:
    """The typed value for ``raw``, or ``OverrideValueError``.

    ``raw`` is exactly what the operator typed. Text is stripped of
    surrounding whitespace and must not be empty -- an empty override is a
    DELETE, and accepting one here would give two spellings for one act.
    """
    if field in TEXT_FIELDS:
        if not isinstance(raw, str):
            raise OverrideValueError(
                f"{field} must be text, not {type(raw).__name__}"
            )
        text = raw.strip()
        if not text:
            raise OverrideValueError(
                f"{field} cannot be empty; clear the override instead"
            )
        return text
    if field in RATING_FIELDS:
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise OverrideValueError(
                f"{field} must be a number between 0 and 10"
            ) from None
        if not 0.0 <= number <= 10.0:
            raise OverrideValueError(f"{field} must be between 0 and 10")
        # Rounded HERE, to the one decimal ``writer._one_decimal`` writes and
        # ``format_critic``/``format_audience`` compare on, so the stored
        # string and the written value are the same number.
        return float(f"{number:.1f}")
    if field in DATE_FIELDS:
        try:
            return date.fromisoformat(raw.strip())
        except (TypeError, AttributeError, ValueError):
            raise OverrideValueError(
                f"{field} must be an ISO date, YYYY-MM-DD"
            ) from None
    if field in LIST_FIELDS:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            raise OverrideValueError(
                f"{field} must be a JSON list of strings"
            ) from None
        if not isinstance(parsed, list) or not parsed:
            raise OverrideValueError(
                f"{field} must be a non-empty JSON list of strings"
            )
        if not all(isinstance(entry, str) and entry.strip() for entry in parsed):
            raise OverrideValueError(f"{field} must be a JSON list of strings")
        # SYNC semantics, stated where it is parsed: the override IS the list.
        # A genre override does not add to what Plex holds -- ``_genre_plan``
        # computes the additions and removals that make Plex's list exactly
        # this one, which is upstream's ``genre.sync`` and not its ``genre``.
        return [entry.strip() for entry in parsed]
    raise OverrideValueError(f"{field!r} is not a field this service overrides")


def canonical_value(field: str, parsed: object) -> str:
    """The string form stored in ``item_metadata_overrides.value``.

    The inverse of ``parse_override`` for every field, and the form the
    writer's diff is made in -- see the module docstring for why those have to
    be the same thing.
    """
    if field in DATE_FIELDS:
        return parsed.strftime("%Y-%m-%d")
    if field in RATING_FIELDS:
        return f"{float(parsed):.1f}"
    if field in LIST_FIELDS:
        return json.dumps(list(parsed))
    return str(parsed)


async def load_overrides(session, item_id: int) -> dict[str, object]:
    """This item's overrides, as ``{our field name: typed value}``.

    Pure: one indexed SELECT, no write, no config. The GATE is at the call
    sites (``apply_metadata`` and ``apply_badges``), so gate-off costs no
    query at all rather than one whose result is discarded.

    A stored string that no longer parses is SKIPPED with a warning rather
    than raised: a hand-edited row, or a future narrowing of the parser, must
    not stop a pass on its way to the artwork. The warning names the item and
    the field and never the value.
    """
    rows = (
        await session.execute(
            select(ItemMetadataOverride.field, ItemMetadataOverride.value)
            .where(ItemMetadataOverride.item_id == item_id)
        )
    ).all()
    overrides: dict[str, object] = {}
    for field, value in rows:
        try:
            overrides[field] = parse_override(field, value)
        except OverrideValueError:
            logger.warning(
                "item %s: the stored override for %s no longer parses and is "
                "skipped for this pass", item_id, field,
            )
    return overrides


class _OverlaidFacts:
    """A read-only view of a facts object with the badge overrides on top.

    A delegating view rather than a copy or a mutation, and both alternatives
    are wrong for a reason worth writing down. ``apply_badges`` is handed the
    ORM ``ItemFacts`` row the session is tracking: MUTATING it would be
    flushed into ``item_facts`` by the next commit in that block, poisoning
    the provider's own record by accident -- the one thing roadmap row 99 must
    never do. And COPYING it into a ``GatheredFacts`` would silently drop
    every attribute the ORM row carries that the dataclass does not, which is
    a bug that only appears the next time a column is added.

    ``__getattr__`` runs only for names not found as real attributes, and the
    two slots below are real, so neither is ever intercepted.
    """

    __slots__ = ("_base", "_values")

    def __init__(self, base, values: dict) -> None:
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_values", values)

    def __getattr__(self, name: str):
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        return getattr(object.__getattribute__(self, "_base"), name)


def overlaid_badge_facts(facts, overrides: dict) -> object:
    """``facts`` with any overridden badge value laid on top.

    Roadmap row 99's C4, at the seam that actually reads facts for the badge.
    ``persist_facts`` has already stored the PROVIDER's record untouched; this
    layers on top of the READ, so Plex and the badge show the same number and
    the item re-badges once. Nothing here writes.

    Returns the object it was handed, unchanged and BY IDENTITY, when there is
    nothing to lay on -- so gate-off and no-overrides are indistinguishable
    from today for every consumer, including one that checks identity.
    """
    values = {
        field: overrides[field]
        for field in BADGE_FACTS_FIELDS
        if field in overrides
    }
    if not values:
        return facts
    return _OverlaidFacts(facts, values)
