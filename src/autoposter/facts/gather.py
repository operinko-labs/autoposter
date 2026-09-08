import logging
from dataclasses import replace

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemFacts, MediaItem
from autoposter.facts import imdb
from autoposter.facts.mdblist import MDBListLimitReached
from autoposter.facts.models import GatheredFacts
from autoposter.facts.tmdb_budget import TmdbRateLimited
from autoposter.plex.client import ResolvedItem

logger = logging.getLogger(__name__)

# Render jobs are queue-drained per item, not swept in a discrete "pass" the
# way the collections engine is -- there is no boundary to reset this on. A
# process-wide latch is the simplest thing that still stops a misconfigured
# deployment from spamming one warning line per item across a whole library.
_tvdb_source_unconfigured_warned = False

# Roadmap row 227, and the same latch for the same reason one line up: this
# config document holds library NAMES and nothing that says whether a name is
# a movie library, so "movie libraries only" cannot be a config-load refusal
# and is enforced here instead. One warning per PROCESS, not one per item: a
# library-wide misconfiguration must not cost 13,841 warning lines a pass.
_added_at_non_movie_warned = False


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
    """The stored IMDb rating, triggering one miss-refresh attempt if absent.

    A miss is the normal case for a same-day release or an unaired episode,
    so ``imdb.note_rating_miss`` rate-limits itself (see ``ImdbMissRefresh``)
    rather than downloading on every call. The lookup is retried afterwards
    so a *successful* refresh fills in the rating on this same pass; a
    disabled/rate-limited/failed attempt just leaves the second lookup
    returning ``None`` again, same as today.
    """
    if not item.imdb_id:
        return None
    if item.kind == "episode":
        if item.season_number is None or item.episode_number is None:
            return None
        rating = await imdb.get_episode_rating(
            session, item.imdb_id, item.season_number, item.episode_number
        )
        if rating is None:
            await imdb.note_rating_miss(session, item.imdb_id, is_episode=True)
            rating = await imdb.get_episode_rating(
                session, item.imdb_id, item.season_number, item.episode_number
            )
        return rating
    rating = await imdb.get_rating(session, item.imdb_id)
    if rating is None:
        await imdb.note_rating_miss(session, item.imdb_id, is_episode=False)
        rating = await imdb.get_rating(session, item.imdb_id)
    return rating


def _user_rating(source: str | None, critic: float | None, audience: float | None) -> float | None:
    """The value the named source supplies for Plex's user rating (row 32).

    The explicit-source model, in three lines: a source is named or it is not,
    and there is no fallback between them. Naming ``imdb`` when this item has
    no stored IMDb rating yields nothing -- it does NOT quietly become TMDb's
    audience rating, which would make the setting a suggestion.
    """
    if source == "imdb":
        return critic
    if source == "tmdb":
        return audience
    return None


async def gather_facts(
    session: AsyncSession, item: ResolvedItem, tmdb, mdblist, *, operations=None, tvdb=None
) -> GatheredFacts:
    """Collect everything the providers know about one item.

    A season carries no facts of its own — its badge inherits the show's
    content rating — so it returns empty rather than making pointless calls.

    ``operations`` is the ``OperationsConfig`` naming each mass-op field's
    source (rows 32/33a/84). ``None`` -- what every direct caller and most
    tests pass -- names no source, so those fields are gathered as ``None``
    and nothing new is written. ``tvdb`` is the process's ``TVDBClient``;
    ``None`` -- what every direct caller and most tests pass -- means row 84's
    overlay below is never asked for, whatever ``operations`` names.
    """
    if item.kind == "season":
        return GatheredFacts()

    facts = GatheredFacts()
    sources: dict[str, str] = {}

    if item.kind == "episode":
        audience = None
        try:
            if item.tmdb_id and item.season_number is not None:
                ratings = await tmdb.season_episode_ratings(item.tmdb_id, item.season_number)
                audience = ratings.get(item.episode_number)
        except TmdbRateLimited as exc:
            # The MDBList precedent, one provider along: the budget is spent,
            # and everything else this pass gathers is still good. The item is
            # re-queued by the drift sweep like any other.
            logger.warning("tmdb rate budget reached; skipping tmdb facts: %s", exc)
        if audience is not None:
            sources["audience_rating"] = "tmdb"
        facts = GatheredFacts(audience_rating=audience)
    elif item.tmdb_id:
        try:
            facts = await (tmdb.movie(item.tmdb_id) if item.kind == "movie"
                           else tmdb.show(item.tmdb_id))
            sources.update(facts.sources)
        except TmdbRateLimited as exc:
            logger.warning("tmdb rate budget reached; skipping tmdb facts: %s", exc)

    # Row 84. Only asked for when config NAMES tvdb for at least one of the
    # three fields, so a deployment that names none pays no request and gets
    # today's behaviour exactly.
    wanted = {
        field
        for field in ("genres", "studio", "originally_available")
        if getattr(operations, f"{field}_source", None) == "tvdb"
    }
    if wanted and tvdb is None:
        global _tvdb_source_unconfigured_warned
        if not _tvdb_source_unconfigured_warned:
            logger.warning(
                "operations names tvdb as a source for %s, but no TVDb "
                "provider is configured (check providers.order)",
                ", ".join(sorted(wanted)),
            )
            _tvdb_source_unconfigured_warned = True
    if wanted and tvdb is not None and item.tvdb_id and item.kind in ("movie", "show"):
        try:
            tvdb_facts = await tvdb.extended_facts(item.tvdb_id, item.kind == "movie")
        except httpx.HTTPError as exc:
            # The MDBList precedent: one provider's transient failure must not
            # throw away everything else this pass gathered.
            logger.warning("tvdb request failed; skipping tvdb facts: %s", exc)
            tvdb_facts = None
        if tvdb_facts is not None:
            overlay = {
                field: getattr(tvdb_facts, field)
                for field in wanted
                if getattr(tvdb_facts, field)
            }
            if overlay:
                facts = replace(facts, **overlay)
                sources.update({field: "tvdb" for field in overlay})

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

    user_rating = _user_rating(
        getattr(operations, "user_rating_source", None), critic, facts.audience_rating
    )
    if user_rating is not None:
        sources["user_rating"] = operations.user_rating_source

    # Parsed unconditionally by tmdb_facts (the payload is already fetched, so
    # reading one more key is free); the source key is what decides whether it
    # survives the gather. Stripped rather than never-parsed so the parser
    # stays a pure function of the payload.
    original_title = facts.original_title
    if getattr(operations, "original_title_source", None) != "tmdb":
        original_title = None
    if original_title:
        sources["original_title"] = "tmdb"

    # Row 227. Only asked for when config NAMES a source, and only for a
    # MOVIE -- Kometa's own rule, whose helper's first act is to refuse a
    # non-movie library. A deployment that names no source pays no request and
    # gets today's behaviour exactly, which is the whole safety claim of this
    # row.
    added_at = None
    added_at_source = getattr(operations, "added_at_source", None)
    if added_at_source and item.kind != "movie":
        global _added_at_non_movie_warned
        if not _added_at_non_movie_warned:
            logger.warning(
                "operations.added_at_source names a TMDb release date, which "
                "is served for movies only; it is ignored on every non-movie "
                "item"
            )
            _added_at_non_movie_warned = True
    elif added_at_source and item.tmdb_id:
        try:
            added_at = await tmdb.release_date(item.tmdb_id, added_at_source)
        except TmdbRateLimited as exc:
            # One provider's spent budget must not throw away the pass -- the
            # shape :129-130 already uses.
            logger.warning("tmdb rate budget reached; skipping tmdb facts: %s", exc)
        except httpx.HTTPError as exc:
            # The tvdb precedent at :162-167, in its own words: "one
            # provider's transient failure must not throw away everything
            # else this pass gathered." release_dates is optional and
            # secondary -- a transient 5xx here must not discard a gather
            # that would otherwise complete entirely from cache plus
            # MDBList (review finding I1). Rating key and field only, never
            # the URL: an httpx error's str() carries it in full.
            logger.warning(
                "tmdb release_dates request failed for %s; skipping added_at: %s",
                item.rating_key, type(exc).__name__,
            )
    if added_at is not None:
        sources["added_at"] = added_at_source

    return replace(
        facts,
        critic_rating=critic,
        content_rating=content_rating,
        user_rating=user_rating,
        original_title=original_title,
        added_at=added_at,
        sources=sources,
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

    ``tmdb_origin_country`` joins ``genres``/``sources`` in the group a plain
    SQL ``COALESCE`` could not serve: it is ``NOT NULL`` JSONB defaulting to
    ``[]``, so "this round found no origin country" and "this item honestly has
    none" are the same value and only the populated-fields rule can tell them
    apart.
    """
    # C4, and the whole point of it: "we looked and found nothing" and "nobody
    # has looked" were the same two NULLs before this, and rows 189/192 need to
    # tell them apart -- an enumeration that cannot say how much of the library
    # it has actually visited cannot state its own coverage honestly.
    #
    # Unconditional, above the short-circuit, on the DATABASE clock like every
    # other timestamp here. It rides ``facts_attempted_at``'s existing shape:
    # the drift sweep already stamps it at selection time for exactly this
    # reason (``scheduler/jobs.py:142-151``), and stamping it again at persist
    # time is the same statement made by the path that actually did the work --
    # so an item reached by a webhook rather than by the sweep is recorded too.
    await session.execute(
        update(MediaItem)
        .where(MediaItem.id == media_item_id)
        .values(facts_attempted_at=func.now())
    )
    async def _stored() -> ItemFacts | None:
        await session.commit()
        row = (
            await session.execute(
                select(ItemFacts).where(ItemFacts.item_id == media_item_id)
            )
        ).scalar_one_or_none()
        # The read above autobegan a second transaction, and this function is
        # called from inside process_item -- so what it handed back was held
        # across the rest of that job's render. Nothing is pending; this ends
        # an empty read transaction. Same reasoning as the return below.
        await session.commit()
        return row

    if facts.is_empty():
        return await _stored()

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
    if facts.tmdb_origin_country:
        values["tmdb_origin_country"] = facts.tmdb_origin_country
    if facts.tmdb_original_language:
        values["tmdb_original_language"] = facts.tmdb_original_language
    if facts.tmdb_collection_id is not None:
        values["tmdb_collection_id"] = facts.tmdb_collection_id
    # Roadmap row 100 sub-phase C2c. Same never-blank shape as every branch
    # above: a field the gather did not populate is simply absent from
    # `values`, so the ON CONFLICT SET clause does not name it and whatever
    # the row already had survives. `last_episode_aired` is tested for
    # truthiness rather than `is not None` to match `originally_available`
    # two branches up; a `date` is always truthy when present, so the two
    # spellings agree and the file keeps one idiom for dates.
    if facts.tmdb_status:
        values["tmdb_status"] = facts.tmdb_status
    if facts.last_episode_aired:
        values["last_episode_aired"] = facts.last_episode_aired
    if facts.sources:
        values["sources"] = facts.sources

    if len(values) == 1:
        # ``item_id`` only. Rows 32/33a added GatheredFacts fields with no
        # item_facts column, so a gather whose sole result is one of those is
        # non-empty (the Plex write needs it) yet has nothing to store. Writing
        # a row of timestamps for it would be pure noise -- the same reasoning
        # as the is_empty short-circuit above, one field-set narrower.
        return await _stored()

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
    row = (
        await session.execute(select(ItemFacts).where(ItemFacts.item_id == media_item_id))
    ).scalar_one()
    # The commit above ended the upsert's transaction; this plain read then
    # autobegan another one that nothing closed, and persist_facts is called
    # from process_item, so the caller held it across the rest of the render.
    # expire_on_commit=False (db/base.py) keeps `row` usable after this.
    await session.commit()
    return row
