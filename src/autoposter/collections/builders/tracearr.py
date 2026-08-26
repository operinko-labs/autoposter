"""``tracearr_most_watched``: the titles this household actually played.

The transport is ``providers/tracearr.py`` and the ranking is
``collections/activity.py``. What this builder adds is the three decisions
neither of them can make -- how a ranked bucket becomes a namespaced id, what
that costs, and what happens when Tracearr is not configured at all.

**How a bucket becomes an id, and why it differs by library type.**

- *Movies* short-circuit. A movie history record's ``imdb_id``/``tmdb_id``/
  ``tvdb_id`` are the MOVIE's own, verified against the same movie's media
  document, so a Movie collection costs exactly the history pages and nothing
  else.
- *Shows* do not. On an episode record those three ids are the EPISODE's, and
  ``GET /media/show:tvdb:<episode id>`` is a live-verified 404 -- so a show's
  ids come from one ``GET /media/{show_media_id}`` per bucket. That call is
  made AFTER ranking and truncating, so a collection spends at most its own
  ``limit`` calls, and it is preferred over the ``grandparent_rating_key``
  Tracearr also hands over because this codebase runs a pruner precisely
  because rating keys die.
- *A bucket with no canonical id anywhere* -- formed from identity-less plays
  alone -- emits ``("plex", rating_key)`` as the documented last resort. That
  resolves free against the engine's own index and is dropped, not guessed,
  when it is stale.

**Known limitation: the Plex last resort is not available to an identified
bucket.** A Movie bucket that HAS a ``media_id`` but no usable external id is
dropped rather than falling back to its Plex rating key, even though the
identity-less path has that fallback -- because ``collections/activity.py``
sets ``rating_key=None`` on every identified bucket, so there is no key on the
``Bucket`` to fall back to. That is A-resolution's letter ("identity-less-ONLY
buckets"), recorded here so the asymmetry is a decision rather than an
accident.

**The budget.** 240 requests/minute shared across the whole v2 tree. One
collection is one page per 100 plays in its window, plus at most ``limit``
media documents on a Show library. The documents are memoised on
``ctx.run_cache`` -- one library's pass -- so two definitions ranking the same
show spend one call; the FAILURE is memoised too, because otherwise a dead id
costs one request per definition (``BuilderContext``'s own rule, the
``imdb_award``/``mdblist`` precedent). That applies to both kinds of failure:
a uuid that 404s once is not asked about again in this pass, it is dropped
again from the memo.

**A missing media document drops one title; a broken Tracearr fails the
definition.** The split is the ``mdblist_list`` one, and it turns on whether
the failure is about the item or about the source. A ``TracearrNotFound`` --
one uuid the history just handed us that no longer has a media document, which
is what a title deleted between the history read and the lookup looks like --
drops that bucket, counts it, and lets the other nineteen build; that is the
resolver's ordinary "the library does not have this" one step earlier, and a
transient deletion must not empty a collection. Anything else --
``TracearrRefused``: no connection, the SPA fallback, a mangled envelope,
authentication gone -- raises, because the source itself is the thing that
failed and every remaining bucket would fail the same way. The engine contains
that as one dead source and the operator reads "this definition failed".

The dropped-title count is logged once per build, with the exception's CLASS
name and no message, the way ``mdblist_list`` reports the entries it skipped:
it is the same fact ``DefinitionResult.unresolved`` reports one layer down --
"the source named things this collection could not use" -- and a silent drop is
the one outcome that looks identical to a correct, smaller collection.

**No ``server_id`` filter and no per-user variant.** Both are deliberate. The
banked instance runs one server, and the ``user`` block is the PII-bearing part
of a history record -- a username in a collection title would be a new class of
exposure, not a new feature.
"""
import logging
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoposter.collections.activity import (
    HISTORY_MEDIA_TYPE,
    MEDIA_KINDS,
    Bucket,
    external_id_or_none,
    rank,
    since_instant,
)
from autoposter.collections.builders.base import (
    PREFERENCE,
    BuilderContext,
    BuilderResult,
    ExternalId,
    best_external_id,
    require_library_type,
)
from autoposter.providers.tracearr import (
    TracearrClient,
    TracearrNotFound,
    TracearrRefused,
)

logger = logging.getLogger(__name__)

__all__ = [
    "TracearrBuilderRefused",
    "TracearrMostWatchedBuilder",
    "TracearrMostWatchedParams",
]

# The preference orders and the id-selection loop are ``builders/base.py``'s
# ``PREFERENCE``/``best_external_id``, shared with ``builders/mdblist.py``: it
# is one semantic rule and two copies of it would let the same movie resolve
# differently depending on which builder found it.

# Where one show's media document is memoised, namespaced by module like
# ``mdblist``'s and ``imdb_award``'s.
_MEDIA_MEMO = "tracearr.media.%s"

# What the collection says about itself when the definition sets no summary.
# Ours to write: no Kometa defaults file describes these collections.
_SUMMARY = {
    "plays": "The %ss played most often on this server over the past %d days.",
    "watch_time": "The %ss watched for the longest on this server over the past %d days.",
}


class TracearrBuilderRefused(Exception):
    """This deployment cannot build from Tracearr.

    Only ever "Tracearr is not configured". Everything Tracearr itself refuses
    is ``TracearrRefused``, raised by the client that knows what was asked.
    """


class TracearrMostWatchedParams(BaseModel):
    """``tracearr_most_watched``'s params: what "most watched" means here.

    ``metric`` is closed because there are exactly two answers and both come
    free from one page-through: ``plays`` counts records (which ARE plays --
    Tracearr groups resume chains and drops anything under two minutes before
    it answers) and ``watch_time`` sums ``duration_ms``.

    ``days`` is the window, converted to an ISO instant at build time. Kometa's
    Tautulli builders take the same knob under the same name. Bounded at both
    ends: 0 days is a collection that can only ever be empty, and a decade is a
    page-through nobody meant to ask for.

    ``limit`` is NOT ``CollectionDefinition.limit`` and the difference matters.
    The definition's cap is applied after resolution, to members; this one caps
    CANDIDATES, before the per-bucket ``/media/{ref}`` calls a Show library
    spends -- so it is what bounds this definition's share of the shared
    240/min budget, which is why it has a ceiling at all.
    """

    model_config = ConfigDict(extra="forbid")

    metric: Literal["plays", "watch_time"] = "plays"
    days: int = Field(30, ge=1, le=365)
    limit: int = Field(20, ge=1, le=100)


class TracearrMostWatchedBuilder:
    """The library's most-watched titles over a window, in rank order."""

    type_name = "tracearr_most_watched"
    params_model = TracearrMostWatchedParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TracearrMostWatchedParams.model_validate(ctx.config)
        require_library_type(
            "the 'tracearr_most_watched' builder", ctx.library_type, MEDIA_KINDS
        )
        client = ctx.sources.tracearr
        if client is None:
            raise TracearrBuilderRefused(
                "Tracearr is not configured for this deployment (tracearr.enabled is "
                "off, tracearr.base_url is blank, or AUTOPOSTER_TRACEARR_APIKEY is "
                "unset), so there is no watch history to rank"
            )

        kind = MEDIA_KINDS[ctx.library_type]
        records = await client.history(
            since=since_instant(params.days, datetime.now(UTC)),
            media_type=HISTORY_MEDIA_TYPE[kind],
        )
        ids: list[ExternalId] = []
        vanished = 0
        for bucket in rank(
            records, media_kind=kind, metric=params.metric, limit=params.limit
        ):
            try:
                external = await _external_id(ctx, client, ctx.library_type, bucket)
            except TracearrNotFound:
                # ONE title, not the source: a uuid the history just named has
                # no media document any more, which is what a title deleted
                # between the two calls looks like. Dropped and counted rather
                # than raised -- see the module docstring. Only this class is
                # caught; a plain TracearrRefused propagates and fails the
                # definition, because every remaining bucket would fail too.
                vanished += 1
                continue
            if external is None:
                # Skipped rather than raised, the ``mdblist_list`` judgement:
                # Tracearr knowing no id for one title is the resolver's
                # ordinary "the library does not have this" one step earlier.
                logger.debug(
                    "%s: no usable id for %r in the Tracearr ranking",
                    ctx.library, bucket.title,
                )
                continue
            ids.append(external)
        if vanished:
            # Class name and a count, no message: the message would carry a
            # path, and what an operator needs is "the ranking was N titles
            # shorter than it looks, and here is the kind of failure".
            logger.info(
                "%s: %d ranked title(s) dropped -- Tracearr has no media document "
                "for them any more (%s)",
                ctx.library, vanished, TracearrNotFound.__name__,
            )
        return BuilderResult(
            ids=ids,
            summary=_SUMMARY[params.metric] % (ctx.library_type.lower(), params.days),
        )


async def _external_id(
    ctx: BuilderContext, client: TracearrClient, library_type: str, bucket: Bucket
) -> ExternalId | None:
    """The best namespaced id for one bucket, or None if it has none."""
    if bucket.media_id is None:
        # An identity-less-only bucket: no canonical id exists anywhere, so the
        # Plex rating key it was formed under is the only thing there is.
        return ("plex", bucket.rating_key) if bucket.rating_key else None
    if library_type == "Movie":
        identity = {
            "imdb_id": bucket.imdb_id,
            "tmdb_id": bucket.tmdb_id,
            "tvdb_id": bucket.tvdb_id,
        }
    else:
        # The media document is read RAW off the wire, unlike a Movie bucket's
        # ids -- so ``activity``'s absence rule has to be applied here or a
        # document carrying ``"tvdb_id": "0"`` becomes ("tvdb", "0"), an id
        # that resolves to nothing anywhere and looks exactly like a member the
        # library happens not to own. ``external_id_or_none`` is that rule, in
        # the module that owns it: a second copy here would let the same "0" be
        # an id on this path and absence on the ranking's.
        document = await _media_document(ctx, client, bucket.media_id)
        identity = {
            name: external_id_or_none(document.get(name))
            for name in ("imdb_id", "tmdb_id", "tvdb_id")
        }
    return best_external_id(identity, PREFERENCE[library_type])


async def _media_document(
    ctx: BuilderContext, client: TracearrClient, media_id: str
) -> dict:
    """One show's media document, fetched at most once per library per pass.

    The failure is memoised as well as the answer -- see the module docstring.
    Both classes are memoised and both are re-raised unchanged, so which of
    them it was survives the memo: ``build`` still gets a ``TracearrNotFound``
    to drop the bucket on, or a ``TracearrRefused`` to fail the definition on,
    exactly as it would have on the first attempt.
    """
    memo = _MEDIA_MEMO % media_id
    remembered = ctx.run_cache.get(memo)
    if isinstance(remembered, Exception):
        raise remembered
    if remembered is not None:
        return remembered
    try:
        document = await client.media(media_id)
    except TracearrRefused as error:
        # TracearrNotFound is a subclass, so this arm memoises both.
        ctx.run_cache[memo] = error
        raise
    ctx.run_cache[memo] = document
    return document
