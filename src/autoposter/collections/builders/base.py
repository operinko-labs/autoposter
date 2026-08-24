"""The builder contract: what produces a collection's membership.

A builder answers one question -- "which titles belong in this collection?" --
and answers it as an ordered list of namespaced external ids. It never sees a
Plex section: turning ids into owned items is the engine's job, so a builder is
a pure fetch-and-translate step that can be tested without a server.

Two rules the rest of the engine depends on:

- ``build`` **raises** on failure. Returning an empty list would be read one
  layer down as "make no changes" (``lists.reconcile_list_collection``), so a
  dead chart or a mis-typed param would silently look like success. Failure
  containment -- one dead source must not take the run down -- belongs to the
  engine wrapping ``build``, not to the builders.
- ``type_name`` is the registry key, and it is also what an operator writes as
  ``builder:`` in the config. ``register`` refuses a duplicate rather than
  overwriting, because the loser of an overwrite would still be named in
  someone's config and its collections would simply stop being built.

Params are the builder's own business. A builder that takes params declares a
pydantic model for them (the ``params_model`` convention below) and validates
``ctx.config`` through it, so a mis-spelled key is an error instead of a
silently-applied default. ``plex_id`` below is the worked example.
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from autoposter.providers.cache import ProviderCache

# The id namespaces the resolver understands. "plex" is a rating key, which is
# already the identity of an owned item -- it needs no external lookup, which is
# what makes ``plex_id`` the trivial builder.
Namespace = Literal["imdb", "tmdb", "tvdb", "plex"]
NAMESPACES: frozenset[str] = frozenset({"imdb", "tmdb", "tvdb", "plex"})

ExternalId = tuple[Namespace, str]


@dataclass(frozen=True)
class BuilderResult:
    """One builder's answer.

    ``ids`` is ordered and may repeat: order becomes the collection's custom
    order, and deduplication is the resolver's (one item may carry several ids).
    ``summary`` is a summary the builder itself derives -- the chart and award
    sources take theirs from Kometa's translations -- and is only used when the
    definition does not set one.
    """

    ids: list[ExternalId]
    summary: str | None = None


@dataclass(frozen=True)
class BuilderContext:
    """Everything a builder is allowed to reach.

    ``config`` is the definition's ``params`` block, not the application config.
    ``http`` and ``cache`` are the shared client and the provider response cache
    (``providers.fetch.fetch_json`` takes exactly this pair); both default to
    None because a builder like ``plex_id`` needs neither, and one that does
    need them fails loudly rather than fetching un-cached.

    Deliberately absent: the Plex section. Builders do not resolve, do not read
    the library and do not write.
    """

    library: str
    library_type: str
    http: httpx.AsyncClient | None = None
    config: dict[str, Any] = field(default_factory=dict)
    cache: ProviderCache | None = None


@runtime_checkable
class Builder(Protocol):
    """What a registry entry has to provide.

    Optionally also ``params_model: type[BaseModel]`` -- the model ``build``
    validates ``ctx.config`` through. Left off the protocol itself so a builder
    that takes no params does not have to declare an empty one.
    """

    type_name: str

    async def build(self, ctx: BuilderContext) -> BuilderResult: ...


REGISTRY: dict[str, Builder] = {}


def register(builder: Builder) -> Builder:
    """Add a builder under its ``type_name``. Returns it, for use as a decorator
    on a class that is instantiated at registration.

    Refuses a name already taken -- see the module docstring.
    """
    if builder.type_name in REGISTRY:
        raise ValueError(
            "a builder is already registered as %r; type names must be unique"
            % builder.type_name
        )
    REGISTRY[builder.type_name] = builder
    return builder


class PlexIdParams(BaseModel):
    """``plex_id``'s params: the rating keys, in the order they should appear.

    ``extra="forbid"`` so ``id:`` for ``ids:`` is an error rather than an empty
    default -- the same failure ``test_example_config_matches_schema.py`` exists
    to catch, one level down.
    """

    # coerce_numbers_to_str, because rating keys look like integers and YAML
    # will hand over `ids: [12345]` as one -- an id value is a string in every
    # namespace, and rejecting the unquoted form would be a pointless failure.
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    ids: list[str] = Field(min_length=1)


class PlexIdBuilder:
    """A hand-picked collection: the rating keys the operator listed.

    No network, no provider, no translation -- the whole builder is the params
    model. It exists as the end-to-end proof of the interface, and as the thing
    an operator can point at a handful of items without any external list.
    """

    type_name = "plex_id"
    params_model = PlexIdParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = PlexIdParams.model_validate(ctx.config)
        return BuilderResult(ids=[("plex", value) for value in params.ids])
