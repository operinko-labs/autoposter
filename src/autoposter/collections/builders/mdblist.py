"""``mdblist_list``: any MDBList list, and the daily budget that shapes it.

The transport is ``facts/mdblist.py``. What this builder adds is two decisions
MDBList does not make for us, and one rule about what happens when it stops
answering.

**Which id a member becomes.** MDBList hands over several ids per title --
``tmdb_id``, ``tvdb_id``, ``imdb_id`` -- and a ``mediatype``, and the engine
resolves one namespaced id against what the library's items actually carry. So
the builder picks per library type: a Movie library prefers TMDb and falls back
to IMDb; a Show library prefers TVDb, then TMDb, then IMDb. The order is which
guid the library's items are most likely to have, and every fallback is a
member that would otherwise be dropped. Only the *named* id fields are read: an
entry's bare ``id`` is MDBList's own and putting it in a namespace would be a
guess that resolves to the wrong title rather than to nothing.

**What happens to the other media type.** MDBList lists can hold both, and an
entry of the media type this library is not is dropped rather than emitted.
That is not tidiness: TMDb's movie and show ids share one namespace, so a
show's ``tmdb_id`` offered to a Movie library can match an unrelated film --
the same collision ``simple_ids`` refuses ``tmdb_movie`` on a Show library for.
An entry of the wrong media type could not correctly resolve here anyway.

**The budget.** MDBList answers ``200`` with ``{"error": "API Limit
Reached!"}`` when the daily allowance is spent, so nothing in the HTTP layer
slows down and N more definitions would each spend another call against an
allowance that is already gone. The first one memoises the exception on this
library's ``run_cache`` (``engine.run_library`` builds one per library, not
one per pass -- an N-library deployment spends the budget once per library,
which is bounded and acceptable, not once per definition) and every later
MDBList definition in that same library re-raises it without a request -- the
``imdb_award._event`` precedent (memoise the *failure* too, or a dead source is
re-fetched once per collection), with the roles reversed: there the memo saves
a shared fetch, here it saves the ones that would fail anyway. Each affected
definition still reports failed and nothing is touched, which is the engine's
containment doing its ordinary job.
"""
import logging
import re

from pydantic import BaseModel, ConfigDict, field_validator

from autoposter.collections.builders.base import (
    PREFERENCE,
    BuilderContext,
    BuilderResult,
    best_external_id,
    require_library_type,
)
from autoposter.collections.default_images import UNIVERSE_CODES
from autoposter.facts.mdblist import MDBListLimitReached

logger = logging.getLogger(__name__)

__all__ = ["MdblistBuilderRefused", "MdblistListBuilder", "MdblistListParams"]

# The two addressing forms, and nothing else. Unreserved characters only, so
# neither form needs escaping into the path.
_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._~-]*"
_LIST_REFERENCE = re.compile(rf"^(\d+|{_SEGMENT}/{_SEGMENT})$")

# MDBList's word for a media type, per library type.
_MEDIATYPES = {"Movie": "movie", "Show": "show"}

# The preference orders and the id-selection loop live in ``builders/base.py``
# as ``PREFERENCE``/``best_external_id`` -- ``tracearr_most_watched`` needs the
# same rule and two copies of it would drift. See the module docstring for why
# these orders and why no others, and ``best_external_id`` for why the skip is
# still logged here rather than there.

# Where this library's "MDBList has stopped answering" memo lives (in
# ``ctx.run_cache``, which ``engine.run_library`` builds one per library, not
# one per pass). Namespaced by module, like ``imdb_award``'s.
_LIMIT_REACHED = "mdblist.limit_reached"


class MdblistBuilderRefused(Exception):
    """This deployment cannot build from MDBList.

    Only ever "no API key was configured". Everything MDBList itself refuses is
    ``MDBListRefused`` or ``MDBListLimitReached``, raised by the client that
    knows what was asked.
    """


class MdblistListParams(BaseModel):
    """``mdblist_list``'s params: which list, and optionally in what order.

    ``list`` is MDBList's own two ways of naming one: ``"<user>/<slug>"`` as it
    appears in a list's URL, or the numeric id. Both are accepted because both
    are what an operator has to hand, and the pattern refuses anything else --
    including a pasted ``https://mdblist.com/lists/...`` URL, which would
    otherwise become a path with slashes in it and a 404 to read out of a log.

    ``order`` is closed (``asc``/``desc``) because those are the only two
    values there can be. ``sort`` deliberately is not: MDBList's sort
    vocabulary moves, an over-strict list here would refuse a valid config for
    no gain, and unlike a chart's ``region`` -- which silently changes
    *membership* -- a sort key MDBList does not recognise changes only the
    order the same members arrive in.
    """

    # coerce_numbers_to_str, because ``list: 14`` is how YAML hands over a
    # numeric list id and quoting is the operator's business, not ours.
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    list: str
    sort: str | None = None
    order: str | None = None

    @field_validator("list")
    @classmethod
    def _must_be_a_list_reference(cls, value: str) -> str:
        if not _LIST_REFERENCE.match(value):
            raise ValueError(
                "that is not an MDBList list: write either the numeric list "
                "id (list: 14) or the user and list name from its URL "
                "(list: someone/their-list)"
            )
        return value

    @field_validator("order")
    @classmethod
    def _must_be_a_direction(cls, value: str | None) -> str | None:
        if value is not None and value not in ("asc", "desc"):
            raise ValueError(f"order {value!r} is not 'asc' or 'desc'")
        return value


async def _list_items(ctx: BuilderContext, client, params: MdblistListParams):
    """This list's items, unless MDBList has already said the budget is spent.

    The memo is checked before the request and written from the failure, so the
    first definition to hit the limit is the only one that spends a call on it.
    The exception itself is memoised rather than a flag, so every later
    definition reports exactly what MDBList said. ``ctx.run_cache`` is one
    library's run, not the whole collections pass -- so "the first definition
    to hit the limit" and "every later definition" both mean within this
    library, and a second library still asks MDBList fresh.
    """
    reached = ctx.run_cache.get(_LIMIT_REACHED)
    if reached is not None:
        raise reached
    try:
        return await client.list_items(params.list, sort=params.sort, order=params.order)
    except MDBListLimitReached as error:
        ctx.run_cache[_LIMIT_REACHED] = error
        raise


class MdblistListBuilder:
    """One MDBList list, in MDBList's order, as this library's ids."""

    type_name = "mdblist_list"
    params_model = MdblistListParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = MdblistListParams.model_validate(ctx.config)
        require_library_type("the 'mdblist_list' builder", ctx.library_type, PREFERENCE)
        client = ctx.sources.mdblist
        if client is None:
            raise MdblistBuilderRefused(
                "MDBList is not configured for this deployment (no API key), so it "
                "has no list to build from"
            )

        wanted = _MEDIATYPES[ctx.library_type]
        preference = PREFERENCE[ctx.library_type]
        ids = []
        other_media = 0
        for mediatype, entry in await _list_items(ctx, client, params):
            if mediatype != wanted:
                other_media += 1
                continue
            external = best_external_id(entry, preference)
            if external is None:
                # Skipped rather than raised: MDBList knowing no id for one
                # title is the resolver's ordinary "the library does not have
                # this" one step earlier, and must not take the collection
                # down. Logged so it is not invisible.
                logger.debug(
                    "%s: MDBList list %r has no usable id for %r",
                    ctx.library, params.list, entry.get("title"),
                )
                continue
            ids.append(external)
        if other_media:
            logger.debug(
                "%s: MDBList list %r: skipped %d entr%s of the other media type",
                ctx.library, params.list, other_media, "y" if other_media == 1 else "ies",
            )
        # The DC split's two MDBList universes join the same table by their
        # list ref -- see ``ImdbListBuilder`` for why the ref rather than the
        # title. 'In Association With DC' is deliberately absent from that
        # table: upstream's ``dca`` is DC ANIMATED, a different continuity, and
        # lending it that art would be a plausible wrong poster rather than an
        # absent one.
        code = UNIVERSE_CODES.get(params.list)
        return BuilderResult(
            ids=ids,
            poster_kind="universe" if code else None,
            poster_key=code,
        )
