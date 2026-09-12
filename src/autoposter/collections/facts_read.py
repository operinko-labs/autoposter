"""The per-run facts read (roadmap row 156).

``enrichment.py`` one tier along, and deliberately shaped like it: one memo on
the pass's ``run_cache``, holding every ``item_facts`` value fetched so far
this pass, plus the pass-level failure -- because ``BuilderContext.run_cache``'s
own law is that a failure must be memoised too, or a dead dependency costs one
attempt per definition instead of one per pass.

**Three things differ from the tier-2 Plex read, and each is the reason a
separate module rather than a second function in that one.**

- The join is an OUTER join, deliberately. ``facts_enumeration.py`` inner-joins
  because it ENUMERATES: an item with no facts row has no value to offer a
  family. This module EVALUATES, and the missing row is the answer -- ruling
  C3's exclusion -- so it must come back rather than be filtered out.
- ASKED implies PRESENT, unconditionally. ``ensure_tags`` leaves a key Plex did
  not answer for OUT of its result, and the engine refuses the definition
  rather than evaluating, because "Plex skipped it" is not knowledge. Here it
  is: this service's own database is authoritative about its own rows, so an
  item with no ``item_facts`` row -- or with no ``media_items`` row at all,
  which is a Plex item the sync has not caught up with -- is answered with
  ``ItemFactsValues()``, all-None, which the missing rule excludes on. There is
  no refusal path for a missing row, only for a failed READ.
- The read is inside ``begin_nested()``. ``builders/facts_value.py:84-91``
  states the reason in as many words: ``engine.py``'s per-definition
  containment assumes a swallowed exception leaves the shared session usable
  for whatever the pass runs next, and that held for every filter-stage
  ancestor because none of them touched the database. A failed statement
  without a savepoint would poison the transaction, turning one bad definition
  into every later statement in the pass raising.

The values are keyed and named by the TABLE's attribute names
(``common_sense_rating``), not by the column names (``content_rating``): the
one translation between the two lives here, in ``_COLUMNS``, so no other module
has to know that our ``common_sense_rating`` is stored in a column carrying
Kometa's word for a different thing.
"""
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemFacts, MediaItem, MediaItemServerRef

__all__ = ["FactsUnavailable", "ItemFactsValues", "ensure_facts"]

logger = logging.getLogger(__name__)

_VALUES_KEY = "facts_read:values"
_FAILED_KEY = "facts_read:failed"


class FactsUnavailable(Exception):
    """The facts read failed this pass. Class-name-only by construction
    (roadmap row 213): a SQLAlchemy error's message can carry the statement
    and the DSN, and this string reaches an operator-facing action line."""


@dataclass(frozen=True)
class ItemFactsValues:
    """One item's facts, under the FILTER TABLE's names.

    Every field defaults to None, and an all-None instance is the answer for
    an item with a facts row whose columns are NULL, for an item with no facts
    row, and for an item with no ``media_items`` row -- three states the
    missing-value rule treats identically and this dataclass therefore does
    not distinguish. (``media_items.facts_attempted_at`` is what distinguishes
    "nobody looked" from "looked and found nothing"; it is a property of the
    item's VISIT, reported by ``facts_enumeration.coverage`` and disclosed in
    deploy/README.md, and no filter compares against it.)
    """

    common_sense_rating: str | None = None
    imdb_rating: float | None = None
    tmdb_rating: float | None = None


_NO_FACTS = ItemFactsValues()

# The table's attribute name -> the ``ItemFacts`` column that holds it. The
# whole of row 156's naming law, as data.
_COLUMNS = (
    ("common_sense_rating", ItemFacts.content_rating),
    ("imdb_rating", ItemFacts.critic_rating),
    ("tmdb_rating", ItemFacts.audience_rating),
)


async def ensure_facts(
    session: AsyncSession, run_cache: dict, library: str, rating_keys
) -> dict[str, ItemFactsValues]:
    """The run's facts cache, guaranteed to hold an answer for every key in
    ``rating_keys``.

    The failure memo is consulted AFTER the already-cached short-circuit, and
    the order is load-bearing for the reason ``ensure_tags`` documents: the
    memo records that FETCHING failed, so it may only refuse an ask that would
    have to fetch. Checking it first would refuse a definition whose every key
    was already cached -- an answer in hand, withheld because some other
    definition's query failed earlier in the same pass.
    """
    by_library: dict[str, dict[str, ItemFactsValues]] = run_cache.setdefault(_VALUES_KEY, {})
    cached = by_library.setdefault(library, {})
    wanted = [str(key) for key in rating_keys]
    to_fetch = [key for key in wanted if key not in cached]
    if not to_fetch:
        return cached
    failed = run_cache.get(_FAILED_KEY)
    if failed is not None:
        raise failed

    statement = (
        select(MediaItemServerRef.native_id, *[column for _, column in _COLUMNS])
        .select_from(MediaItemServerRef)
        .join(MediaItem, MediaItem.id == MediaItemServerRef.item_id)
        .outerjoin(ItemFacts, ItemFacts.item_id == MediaItem.id)
        .where(
            MediaItemServerRef.server == "plex",
            MediaItem.library == library,
            MediaItemServerRef.native_id.in_(to_fetch),
        )
    )
    try:
        async with session.begin_nested():
            rows = (await session.execute(statement)).all()
    # Broad, and argued rather than inherited: unlike ``ensure_tags`` -- where
    # a blanket catch would memoise a CALLER bug as a fact about the library
    # -- everything that can come out of this block is one fact, "the database
    # did not answer this pass". A ``SQLAlchemyError``, an asyncpg error that
    # escaped the dialect, a cancelled connection: the operator's remedy is
    # the same for all of them and the definition's outcome is the same for
    # all of them. The savepoint has already rolled back by the time this is
    # reached, so the session is usable for whatever the pass runs next.
    except Exception as error:
        # CLASS NAME only on the served surface (roadmap row 213); the full
        # detail, with traceback, goes to the pod log where a SQLAlchemy
        # error's statement and DSN are already expected to appear.
        logger.exception("%s: the facts read failed", library)
        failure = FactsUnavailable(
            "the facts read failed (%s); every definition filtering on a "
            "facts-backed attribute is refused this pass" % type(error).__name__
        )
        run_cache[_FAILED_KEY] = failure
        raise failure from None

    for row in rows:
        key, values = str(row[0]), row[1:]
        cached[key] = ItemFactsValues(
            common_sense_rating=None if values[0] is None else str(values[0]),
            imdb_rating=None if values[1] is None else float(values[1]),
            tmdb_rating=None if values[2] is None else float(values[2]),
        )
    # ASKED implies PRESENT: a key with no ``media_items`` row never came back
    # from the join and is answered here, once, rather than by every caller
    # having to decide what a ``.get`` miss means.
    for key in to_fetch:
        cached.setdefault(key, _NO_FACTS)
    return cached
