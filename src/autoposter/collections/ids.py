"""The id namespaces a collection's membership is expressed in.

This is the vocabulary two layers share: a builder produces external ids, and
the resolver turns them into owned Plex items. It lives in its own module so
that neither has to import the other -- the resolver knowing what a builder is
would be an upward dependency, and the engine sits on top of both.

Nothing here does any work. It is three names and the rule that they are the
only namespaces that exist.
"""
from typing import Literal

# "plex" is a rating key, which is already the identity of an owned item -- it
# needs no external lookup, which is what makes ``plex_id`` the trivial builder.
Namespace = Literal["imdb", "tmdb", "tvdb", "plex"]
NAMESPACES: frozenset[str] = frozenset({"imdb", "tmdb", "tvdb", "plex"})

ExternalId = tuple[Namespace, str]

# What a definition's members ARE, as opposed to what namespace names them.
# "item" is the library's own granularity -- a Movie in a Movie library, a Show
# in a Show library -- and is what every builder that predates roadmap row 143
# means. The other two exist because a Show library's own items carry no
# per-episode guid, so an episode-level membership has to be resolved against a
# SECOND traversal of the same library (``resolve.build_owned_index``).
MemberLevel = Literal["item", "season", "episode"]
MEMBER_LEVELS: frozenset[str] = frozenset({"item", "season", "episode"})

# The ``libtype`` each level is searched under. None is "do not search at all":
# ``section.all()`` is one request for the whole library and is what the item
# level has always used, so the item level must keep costing exactly that.
LIBTYPE_FOR_LEVEL: dict[str, str | None] = {
    "item": None,
    "season": "season",
    "episode": "episode",
}
