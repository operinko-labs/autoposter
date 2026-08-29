"""What the facts pipeline knows, as an enumeration a collection family can use.

**Why this exists at all.** Phase 10a's dynamic engine enumerates through
Plex's ``listFilterChoices`` -- one round trip, and the values are the
library's own tags. Three of Kometa's dynamic types cannot be served that way,
and roadmap rows 189 and 192 say why in the same words: ``origin_country`` and
``original_language`` are TMDb's fields and Plex has no equivalent (its own
``<Country>`` is the PRODUCTION country, which the shipped ``country`` type
already enumerates and which disagrees with TMDb's on exactly the
co-productions an operator would notice), and ``tmdb_collection`` is a walk
over every item's ``belongs_to_collection``. Upstream answers all three with a
full-library TMDb walk. This service already HAS that walk -- the facts
pipeline reads ``/movie/{id}`` and ``/tv/{id}`` for every item and, since this
phase, keeps the three fields off the payload it was fetching anyway. So the
enumeration is a database read, not a network one.

**The missing-value rule, which is most of the design.** Row 156 predicted it:
"the missing-value rule would do most of the work". An item with no
``item_facts`` row, or with a NULL scalar or an empty array in the column, is
ABSENT from every query here. It is never a bucket, never a zero, never an
error. That single rule is what makes a partially-visited library produce a
correct-but-incomplete family rather than a wrong one.

**Coverage is honest, not hidden.** The enumeration reflects only the items the
facts pipeline has visited. ``coverage`` measures exactly that -- attempts
against library size -- so a pack description can state the convergence story
in numbers an operator can check, and the drift sweep (500 items a week by
default, ``scheduler.drift_batch_size``) is what closes the gap over time.

Pure SQL and nothing else: no Plex, no HTTP, no config. Everything here takes a
session and returns data, which is what lets the whole of it be proven against
a real Postgres in ``tests/test_collection_facts_enumeration.py``.
"""
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemFacts, MediaItem

__all__ = [
    "FACTS_FIELDS",
    "FactsField",
    "coverage",
    "enumerate_values",
    "items_with_values",
]

# Our library-type spelling to the ``media_items.kind`` rows a family is built
# from. A Show family is a family of SHOWS: ``gather_facts`` gives an episode
# only its two ratings and a season nothing at all, so no other kind carries
# the columns below.
_KINDS: dict[str, tuple[str, ...]] = {"Movie": ("movie",), "Show": ("show",)}


@dataclass(frozen=True)
class FactsField:
    """One ``item_facts`` column a family can be enumerated from.

    ``column`` is the attribute name on ``ItemFacts`` -- ours, never Kometa's
    (row 156's naming law) -- and ``name`` is the short word this service uses
    for the field in a params block and in a type row.

    ``multi`` says the column is a JSONB ARRAY rather than a scalar, which
    changes both queries: the enumeration unnests it and the membership test is
    ``jsonb_exists`` rather than ``=``. One flag rather than two field classes,
    because everything else about the two shapes is identical.

    ``kinds`` is which library types the field can be enumerated on, and it is
    the field's own property rather than a type row's: TMDb has no
    ``belongs_to_collection`` for a series, so ``tmdb_collection`` is
    movie-only wherever it is used.

    ``value_type`` is the Python type ``items_with_values``' scalar branch
    binds a value as. SQLAlchemy infers a bind parameter's Postgres type from
    the Python value it is given, not from the column being compared, so a
    scalar column that is not text -- ``tmdb_collection_id`` is an Integer
    (``db/models.py:237``) -- needs its own coercion here, or the asyncpg
    dialect renders a cast the column refuses (``integer = character
    varying``). Unused for a ``multi`` field: the membership side is always
    ``jsonb_exists_any`` against text, matching what
    ``jsonb_array_elements_text`` -- the enumeration side -- always yields.
    """

    name: str
    column: str
    multi: bool
    kinds: tuple[str, ...]
    note: str
    value_type: type = str


FACTS_FIELDS: dict[str, FactsField] = {
    field.name: field
    for field in (
        FactsField(
            "origin_country", "tmdb_origin_country", True, ("Movie", "Show"),
            "TMDb's own origin country, an ISO-3166-1 code, and a LIST -- a "
            "co-production carries several and belongs in each bucket. NOT "
            "Plex's `<Country>`, which is the production country the shipped "
            "`country` dynamic type already enumerates: roadmap row 189 rules "
            "the substitution out by name, because the two disagree on exactly "
            "the co-productions an operator would notice.",
        ),
        FactsField(
            "original_language", "tmdb_original_language", False, ("Movie", "Show"),
            "TMDb's `original_language`, an ISO-639-1 code. Titled from the "
            "CODE: neither this service nor Kometa's pack files carry a "
            "code->name table (roadmap row 190 records the same absence for the "
            "audio/subtitle language families, where Plex at least supplies a "
            "display title and here nothing does).",
        ),
        FactsField(
            "tmdb_collection", "tmdb_collection_id", False, ("Movie",),
            "`belongs_to_collection.id`. Movie-only, and not by policy: TMDb "
            "collections ARE movie franchises and their `parts` are movies "
            "(`builders/tmdb.TmdbCollectionBuilder`). Enumerated as the id "
            "stringified, because `dynamic_keys._strlist` matches every "
            "narrowing entry as a string.",
            int,
        ),
    )
}


def _column(field: FactsField):
    return getattr(ItemFacts, field.column)


def _scope(field: FactsField, library: str, library_type: str):
    """The joins and predicates every query here shares.

    Library-scoped because ``media_items`` holds every library at once and a
    second Movie section's values are not this one's -- the same narrowing
    ``engine.definition_titles_for`` applies for the same reason.
    """
    return (
        MediaItem.library == library,
        MediaItem.kind.in_(_KINDS.get(library_type, ())),
    )


async def enumerate_values(
    session: AsyncSession, field: FactsField, *, library: str, library_type: str
) -> list[tuple[str, int]]:
    """``(value, item_count)`` for one field, most-populated first.

    Ties break on the value ascending so the order is total and a family's
    collections are created in a stable order between passes -- unlike the
    Plex-backed enumeration, whose order is the server's own and is preserved
    for that reason, this order is ours and has to be decided.

    The COUNT rides along because it is free here (the GROUP BY is already
    running) and because it is the only thing a caller could use to bound a
    family without a second query. Nothing in this phase uses it to filter;
    it is reported.
    """
    column = _column(field)
    if field.multi:
        # jsonb_array_elements_text as a FROM item. Postgres allows a
        # set-returning function in FROM to reference an earlier FROM item
        # without the LATERAL keyword, so no `.lateral()` is needed.
        elements = func.jsonb_array_elements_text(column).table_valued("value")
        value = elements.c.value
        stmt = (
            select(value, func.count())
            .select_from(ItemFacts)
            .join(MediaItem, MediaItem.id == ItemFacts.item_id)
            .join(elements, true())
            .where(*_scope(field, library, library_type))
            .group_by(value)
            .order_by(func.count().desc(), value.asc())
        )
    else:
        stmt = (
            select(column, func.count())
            .select_from(ItemFacts)
            .join(MediaItem, MediaItem.id == ItemFacts.item_id)
            .where(*_scope(field, library, library_type), column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc(), column.asc())
        )
    rows = (await session.execute(stmt)).all()
    return [(str(value), int(count)) for value, count in rows if str(value)]


async def items_with_values(
    session: AsyncSession,
    field: FactsField,
    values: Sequence[str],
    *,
    library: str,
    library_type: str,
) -> list[str]:
    """The Plex rating keys of every item carrying ANY of ``values``.

    An OR over the list, because a bucket's values are its own key plus every
    addon member the library carries (``dynamic_keys.DynamicKey.values``) --
    the same ``any:`` base the smart families emit, and for the same reason:
    ``all:`` would ask for an item that is at once American and British.

    Ordered by title so a collection's custom order is stable and readable.
    An empty ``values`` returns nothing rather than everything: a membership
    query with no terms would match the whole library, which is the accident
    ``builders/dynamic.py`` refuses by name.
    """
    if not values:
        return []
    column = _column(field)
    if field.multi:
        matches = func.jsonb_exists_any(column, list(values))
    else:
        matches = column.in_([field.value_type(one) for one in values])
    stmt = (
        select(MediaItem.rating_key)
        .select_from(ItemFacts)
        .join(MediaItem, MediaItem.id == ItemFacts.item_id)
        .where(*_scope(field, library, library_type), matches)
        .order_by(MediaItem.title.asc(), MediaItem.rating_key.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def coverage(
    session: AsyncSession, *, library: str, library_type: str
) -> tuple[int, int]:
    """``(items whose facts have been attempted, items in the library)``.

    The attempt, not the row: ``persist_facts`` stamps
    ``media_items.facts_attempted_at`` on every call including an empty gather
    (adjudication C4), so an item TMDb has nothing for counts as VISITED -- and
    so does an item the TMDb rate budget refused this pass, whose gather caught
    ``TmdbRateLimited`` and stored nothing but was still stamped; it re-fills on
    its next drift turn. That
    is the whole difference between "this family has enumerated 40% of the
    library" and "60% of the library has no origin country", which are very
    different sentences and only one of them is true.
    """
    kinds = _KINDS.get(library_type, ())
    total = (
        await session.execute(
            select(func.count())
            .select_from(MediaItem)
            .where(MediaItem.library == library, MediaItem.kind.in_(kinds))
        )
    ).scalar_one()
    attempted = (
        await session.execute(
            select(func.count())
            .select_from(MediaItem)
            .where(
                MediaItem.library == library,
                MediaItem.kind.in_(kinds),
                MediaItem.facts_attempted_at.is_not(None),
            )
        )
    ).scalar_one()
    return int(attempted), int(total)
