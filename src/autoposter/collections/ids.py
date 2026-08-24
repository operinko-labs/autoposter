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
