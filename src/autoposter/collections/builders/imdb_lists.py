"""The two public-IMDb-list builders: a list by id, and a watchlist by user.

The transport is ``collections/imdb_lists.py``, which raises on anything it
did not expect -- see its docstring for why a list builder that degrades to
fewer ids is worse than one that fails.

What these add is the id format check. ``list: ls055350410`` and
``user: ur000000001`` are the two shapes IMDb uses, and getting them the wrong
way round (or pasting the whole URL) is the mistake an operator actually
makes. Caught here, at config load, it names the field; sent to IMDb it comes
back as a null and reads like "your list was deleted".

No summary and no poster, for the reason ``builders/tmdb.py`` gives at
length: ``imdb_chart`` can set both only because Kometa's translation strings
for the charts are transcribed in ``docs/research/kometa-collections.md`` §5.
A list is the operator's own, so its title and summary are the definition's.

No library-type guard either. An IMDb list may hold films and series at once
and both land in the ``imdb`` namespace; the library the pass runs against
already decides which half of it can resolve. That is the same call
``tmdb_list`` makes.
"""
import re

from pydantic import BaseModel, ConfigDict, field_validator

from autoposter.collections.builders.base import BuilderContext, BuilderResult
from autoposter.collections.imdb_lists import fetch_list, fetch_watchlist

__all__ = [
    "ImdbListBuilder",
    "ImdbListParams",
    "ImdbWatchlistBuilder",
    "ImdbWatchlistParams",
]

_LIST_ID = re.compile(r"^ls\d+$")
_USER_ID = re.compile(r"^ur\d+$")


class ImdbListParams(BaseModel):
    """``imdb_list``'s params: which list."""

    model_config = ConfigDict(extra="forbid")

    # Shadows the builtin as an attribute name only, and it is the name the
    # operator writes -- ``list:`` is what the IMDb URL calls this thing.
    list: str

    @field_validator("list")
    @classmethod
    def _must_be_a_list_id(cls, value: str) -> str:
        if not _LIST_ID.match(value):
            raise ValueError(
                f"{value!r} is not an IMDb list id: they look like 'ls055350410' "
                "and are the 'ls…' part of an imdb.com/list/ URL. A 'ur…' value "
                "is a user id -- use the `imdb_watchlist` builder for that."
            )
        return value


class ImdbWatchlistParams(BaseModel):
    """``imdb_watchlist``'s params: whose watchlist."""

    model_config = ConfigDict(extra="forbid")

    user: str

    @field_validator("user")
    @classmethod
    def _must_be_a_user_id(cls, value: str) -> str:
        if not _USER_ID.match(value):
            raise ValueError(
                f"{value!r} is not an IMDb user id: they look like 'ur000000001' "
                "and are the 'ur…' part of an imdb.com/user/ URL. An 'ls…' value "
                "is a list id -- use the `imdb_list` builder for that."
            )
        return value


class ImdbListBuilder:
    """One public IMDb list, in list order."""

    type_name = "imdb_list"
    params_model = ImdbListParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbListParams.model_validate(ctx.config)
        ids = await fetch_list(ctx.http, params.list)
        return BuilderResult(ids=[("imdb", value) for value in ids])


class ImdbWatchlistBuilder:
    """One IMDb user's public watchlist, in watchlist order.

    Public watchlists only -- there is no anonymous route to a private one,
    and IMDb refuses rather than answering an empty list, which
    ``fetch_watchlist`` turns into a raise naming the watchlist.
    """

    type_name = "imdb_watchlist"
    params_model = ImdbWatchlistParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbWatchlistParams.model_validate(ctx.config)
        ids = await fetch_watchlist(ctx.http, params.user)
        return BuilderResult(ids=[("imdb", value) for value in ids])
