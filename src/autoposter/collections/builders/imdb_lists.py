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

Still no library-type *guard*: an IMDb list may hold films and series at once,
and refusing to run it against either library would be wrong. What these
builders do instead is filter by each entry's IMDb title type, and that is not a
refinement of "the library decides which half resolves" -- it is the correction
of it. That reasoning holds only while every entry belongs to one library's
guid space or the other's, and a public list is under no such obligation:
Kometa's Star Trek universe list is ~13 films and ~950 ``tvEpisode`` entries
with no series on it at all, and its Arrowverse list is 977 episodes and one
``video``. An episode's ``tt`` id is in NEITHER namespace -- Plex files episodes
under their show's guid, never their own -- so emitting one cannot resolve
against any library. Unfiltered, those three definitions reported 500-978
"unresolved" ids per pass and their Show-library rows resolved nothing at all
while claiming the source had returned no items.

So the filter is about what an id *can* mean, not about what the operator
meant. A type this repository does not recognise is dropped rather than
emitted, because an unrecognised type is far likelier to be a new episode-like
bulk type than a film IMDb has re-labelled, and one dropped list member is
cheaper than a row of unresolvable ids. The debug line below names what was
dropped so the drift is visible rather than silent.

``imdb_watchlist`` now takes the same filter (roadmap row 168). It was first
left alone on the grounds that a watchlist is one person's own shortlist rather
than a curated bulk list -- but that was a claim about whose data it is, not
about what an id *can* mean, and the filter's principle is the latter: an
episode's ``tt`` id is unresolvable everywhere Plex looks whoever listed it, so
a watchlist holding one reproduces exactly the unresolved-ids shape the
``imdb_list`` half of this module already refuses to emit.
"""
import logging
import re

from pydantic import BaseModel, ConfigDict, field_validator

from autoposter.collections.builders.base import BuilderContext, BuilderResult
from autoposter.collections.imdb_lists import fetch_list, fetch_watchlist

logger = logging.getLogger(__name__)

__all__ = [
    "ImdbListBuilder",
    "ImdbListParams",
    "ImdbWatchlistBuilder",
    "ImdbWatchlistParams",
]

_LIST_ID = re.compile(r"^ls\d+$")
_USER_ID = re.compile(r"^ur\d+$")

# Plex library type -> the IMDb title types whose ids that library can own.
#
# Wider on the Movie side than ``imdb_search.TITLE_TYPE_IDS``, and for the
# opposite reason: a *search* constraint narrows what IMDb is asked for, so
# asking only for ``movie`` costs nothing, while a *list* is already written
# and the question here is which of its entries a Movies section could hold.
# Plex files a TV movie, a short and a standalone video as movies, each under
# its own ``imdb://`` guid, so all four resolve. The Show side is identical to
# the search module's, which is the corroboration: series and mini-series only,
# ``tvMovie`` excluded because Plex does not file one as a show.
_KEEP: dict[str, frozenset[str]] = {
    "Movie": frozenset({"movie", "tvMovie", "short", "video"}),
    "Show": frozenset({"tvSeries", "tvMiniSeries"}),
}

# What a dropped entry with no title type at all is called in the debug line.
# Not a type id IMDb has, which is the point -- it must not be mistaken for one.
_UNKNOWN = "unknown"


def _library_owned(entries, ctx: BuilderContext, source_description: str) -> list:
    """The ``("imdb", id)`` pairs a library of ``ctx``'s type can own; every
    other entry is dropped and the drop logged with its distinct types -- a
    source that starts losing entries to a type id this repository has never
    seen is IMDb drift, and this line is the only place it can show up."""
    keep = _KEEP[ctx.library_type]
    ids = []
    dropped: list[str] = []
    for value, title_type in entries:
        if title_type in keep:
            ids.append(("imdb", value))
        else:
            dropped.append(title_type or _UNKNOWN)
    if dropped:
        logger.debug(
            "%s: %s: dropped %d entr%s a %s library cannot own (%s)",
            ctx.library, source_description, len(dropped),
            "y" if len(dropped) == 1 else "ies", ctx.library_type,
            ", ".join(sorted(set(dropped))),
        )
    return ids


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
    """One public IMDb list, in list order, minus what this library cannot own.

    See the module docstring for why the title-type filter is not optional:
    three of the shipped universe lists are episode dumps, and an episode id is
    unresolvable everywhere.
    """

    type_name = "imdb_list"
    params_model = ImdbListParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbListParams.model_validate(ctx.config)
        entries = await fetch_list(ctx.http, params.list)
        return BuilderResult(
            ids=_library_owned(entries, ctx, "IMDb list %r" % params.list)
        )


class ImdbWatchlistBuilder:
    """One IMDb user's public watchlist, in watchlist order, minus what this
    library cannot own (roadmap row 168).

    Public watchlists only -- there is no anonymous route to a private one,
    and IMDb refuses rather than answering an empty list, which
    ``fetch_watchlist`` turns into a raise naming the watchlist.
    """

    type_name = "imdb_watchlist"
    params_model = ImdbWatchlistParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbWatchlistParams.model_validate(ctx.config)
        entries = await fetch_watchlist(ctx.http, params.user)
        return BuilderResult(
            ids=_library_owned(
                entries, ctx, "the IMDb watchlist of %r" % params.user
            )
        )
