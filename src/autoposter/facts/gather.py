import logging
from dataclasses import replace

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
        if content_rating:
            sources["content_rating"] = "mdb_commonsense"

    return replace(
        facts, critic_rating=critic, content_rating=content_rating, sources=sources
    )


async def persist_facts(
    session: AsyncSession, media_item_id: int, facts: GatheredFacts
) -> ItemFacts:
    """Upsert the row, so concurrent workers on one item cannot collide."""
    values = {
        "item_id": media_item_id,
        "critic_rating": facts.critic_rating,
        "audience_rating": facts.audience_rating,
        "content_rating": facts.content_rating,
        "genres": facts.genres,
        "studio": facts.studio,
        "originally_available": facts.originally_available,
        "sources": facts.sources,
    }
    mutable = {k: v for k, v in values.items() if k != "item_id"}
    # Database clock, like every other timestamp in this project.
    mutable["fetched_at"] = func.now()
    await session.execute(
        insert(ItemFacts).values(**values).on_conflict_do_update(
            index_elements=["item_id"], set_=mutable
        )
    )
    await session.commit()
    return (
        await session.execute(select(ItemFacts).where(ItemFacts.item_id == media_item_id))
    ).scalar_one()
