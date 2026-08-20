import logging
from dataclasses import replace

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemFacts
from autoposter.facts import imdb
from autoposter.facts.mdblist import MDBListLimitReached
from autoposter.facts.models import GatheredFacts
from autoposter.plex.client import ResolvedItem

logger = logging.getLogger(__name__)


def format_critic(value: float | None) -> str | None:
    """One decimal, always — ``9.0`` rather than ``9``.

    This is the string the badge will draw, so it is also what decides whether
    a re-render is needed.
    """
    return None if value is None else f"{float(value):.1f}"


def format_audience(value: float | None) -> str | None:
    """``int(value * 10)`` then a literal ``%``.

    Truncates rather than rounds, matching the tool being replaced: ``8.65``
    renders ``86%``, not ``87%``.
    """
    return None if value is None else f"{int(float(value) * 10)}%"


async def _critic_rating(session: AsyncSession, item: ResolvedItem) -> float | None:
    if not item.imdb_id:
        return None
    if item.kind == "episode":
        if item.season_number is None or item.episode_number is None:
            return None
        return await imdb.get_episode_rating(
            session, item.imdb_id, item.season_number, item.episode_number
        )
    return await imdb.get_rating(session, item.imdb_id)


async def gather_facts(
    session: AsyncSession, item: ResolvedItem, tmdb, mdblist
) -> GatheredFacts:
    """Collect everything the providers know about one item.

    A season carries no facts of its own — its badge inherits the show's
    content rating — so it returns empty rather than making pointless calls.
    """
    if item.kind == "season":
        return GatheredFacts()

    facts = GatheredFacts()
    sources: dict[str, str] = {}

    if item.kind == "episode":
        audience = None
        if item.tmdb_id and item.season_number is not None:
            ratings = await tmdb.season_episode_ratings(item.tmdb_id, item.season_number)
            audience = ratings.get(item.episode_number)
        if audience is not None:
            sources["audience_rating"] = "tmdb"
        facts = GatheredFacts(audience_rating=audience)
    elif item.tmdb_id:
        facts = await (tmdb.movie(item.tmdb_id) if item.kind == "movie"
                       else tmdb.show(item.tmdb_id))
        sources.update(facts.sources)

    critic = await _critic_rating(session, item)
    if critic is not None:
        sources["critic_rating"] = "imdb"

    content_rating = None
    if item.kind in ("movie", "show"):
        try:
            content_rating = await mdblist.content_rating(
                tmdb_id=item.tmdb_id, tvdb_id=item.tvdb_id, is_movie=item.kind == "movie"
            )
        except MDBListLimitReached:
            # Budget spent for today. Everything else we gathered is still
            # good; the content rating fills in on a later pass.
            logger.warning("mdblist daily limit reached; skipping content rating")
        except httpx.HTTPError as exc:
            # A 429/500/502 or connection error from MDBListClient's
            # raise_for_status(). Same reasoning as MDBListLimitReached above:
            # everything else we gathered is still good and must not be
            # thrown away over one provider's transient failure.
            logger.warning("mdblist request failed; skipping content rating: %s", exc)
        if content_rating:
            sources["content_rating"] = "mdb_commonsense"

    return replace(
        facts, critic_rating=critic, content_rating=content_rating, sources=sources
    )


async def persist_facts(
    session: AsyncSession, media_item_id: int, facts: GatheredFacts
) -> ItemFacts | None:
    """Upsert the row, so concurrent workers on one item cannot collide.

    Finding 4: never overwrite a stored value with an absent one. Chosen
    approach is a combination of the two offered: "skip the upsert entirely"
    for a totally empty gather, plus a per-field version of "COALESCE" for a
    partial one, since a plain SQL ``COALESCE(new, old)`` cannot tell "this
    round found nothing for this field" apart from "this round found an
    honestly-empty value" when the column type has no ``NULL`` at all
    (``genres``/``sources`` are ``NOT NULL`` JSONB, defaulting to ``[]``/``{}``).
    Building the ``values``/``SET`` clauses only from fields the gather
    actually populated sidesteps that ambiguity entirely.

    A gather that found nothing at all (``facts.is_empty()``) is skipped
    entirely rather than upserted — most commonly a season (which never
    carries its own facts) or a fully failed/negative-cached gather, and
    writing an all-NULL row for either would be pure noise. Returns the
    existing row unchanged in that case, or ``None`` if there isn't one yet.

    When there IS something new, only the fields this round actually
    populated are written — a field is included only when non-``None`` (or
    non-empty, for ``genres``/``sources``) — so e.g. a gather that found only
    a critic rating (no ``tmdb_id`` this pass) cannot blank the genres/studio
    a previous, more complete gather already stored. ``sources`` is merged
    with whatever is already stored (via Postgres's jsonb ``||``) rather than
    replaced outright, for the same reason: a partial gather's provenance map
    must not erase the entries a previous pass recorded for fields it did not
    touch this time. A field the new gather DID populate always overwrites
    the old value, per-field, via ``ON CONFLICT``.
    """
    if facts.is_empty():
        return (
            await session.execute(
                select(ItemFacts).where(ItemFacts.item_id == media_item_id)
            )
        ).scalar_one_or_none()

    values: dict[str, object] = {"item_id": media_item_id}
    if facts.critic_rating is not None:
        values["critic_rating"] = facts.critic_rating
    if facts.audience_rating is not None:
        values["audience_rating"] = facts.audience_rating
    if facts.content_rating:
        values["content_rating"] = facts.content_rating
    if facts.genres:
        values["genres"] = facts.genres
    if facts.studio:
        values["studio"] = facts.studio
    if facts.originally_available:
        values["originally_available"] = facts.originally_available
    if facts.sources:
        values["sources"] = facts.sources

    stmt = insert(ItemFacts).values(**values)
    set_ = {
        k: stmt.excluded[k] for k in values if k not in ("item_id", "sources")
    }
    if "sources" in values:
        set_["sources"] = ItemFacts.sources.op("||")(stmt.excluded.sources)
    # Database clock, like every other timestamp in this project.
    set_["fetched_at"] = func.now()
    set_["updated_at"] = func.now()
    await session.execute(
        stmt.on_conflict_do_update(index_elements=["item_id"], set_=set_)
    )
    await session.commit()
    return (
        await session.execute(select(ItemFacts).where(ItemFacts.item_id == media_item_id))
    ).scalar_one()
