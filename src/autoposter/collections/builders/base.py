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
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.ids import NAMESPACES, ExternalId, Namespace
from autoposter.providers.cache import ProviderCache

# ``Namespace``/``NAMESPACES``/``ExternalId`` moved to ``collections/ids.py``:
# ``resolve.py`` needs the same vocabulary, and importing it from here made the
# resolver depend on the builder package it sits underneath. Re-exported so
# nothing that reads them from the builder contract has to care.
__all__ = [
    "NAMESPACES",
    "Builder",
    "BuilderContext",
    "BuilderResult",
    "ExternalId",
    "Namespace",
    "PlexIdBuilder",
    "PlexIdParams",
    "REGISTRY",
    "SmartBuilder",
    "SmartContext",
    "register",
]


@dataclass(frozen=True)
class BuilderResult:
    """One builder's answer.

    ``ids`` is ordered and may repeat: order becomes the collection's custom
    order, and deduplication is the resolver's (one item may carry several ids).
    ``summary`` is a summary the builder itself derives -- the chart and award
    sources take theirs from Kometa's translations -- and is only used when the
    definition does not set one.

    ``poster_kind``/``poster_key`` name the collection's default artwork
    (``collections/posters.py``). They belong to the builder because the hosted
    default is keyed by what the *source* calls the collection -- Kometa's chart
    name, the award, the ceremony year -- not by whatever an operator titled it.
    A builder that has no artwork to offer leaves both None and the collection
    simply keeps no poster.
    """

    ids: list[ExternalId]
    summary: str | None = None
    poster_kind: str | None = None
    poster_key: str | None = None


@dataclass(frozen=True)
class BuilderContext:
    """Everything a builder is allowed to reach.

    ``config`` is the definition's ``params`` block, not the application config.
    ``http`` is the shared client, passed through by every ``engine.py`` call
    site. ``cache`` names the provider response cache ``providers.fetch.fetch_json``
    takes alongside it -- but unlike ``http``, nothing populates it: the engine
    itself holds no ``ProviderCache``, only ``summaries``
    (a ``TMDBFactsClient`` used solely for ``tmdb_summary:``, not this cache),
    so every ``BuilderContext`` gets ``cache=None`` regardless of what a
    builder asks for, and no builder reads ``ctx.cache`` today. Wiring a real
    cache through here is future work, not a promise this field currently
    keeps.

    ``run_cache`` is scratch shared by every builder in one pass over one
    library, and it exists for exactly one reason: the seven Oscars collections
    all read the same ceremony dataset, which is one file and must stay one
    fetch. A builder that memoises there must memoise the *failure* too, or a
    dead source is re-fetched once per collection.

    Deliberately absent: the Plex section. Builders do not resolve, do not read
    the library and do not write.
    """

    library: str
    library_type: str
    http: httpx.AsyncClient | None = None
    config: dict[str, Any] = field(default_factory=dict)
    cache: ProviderCache | None = None
    run_cache: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SmartContext:
    """What a *smart* builder gets instead of ``BuilderContext``.

    The deliberate exception to "builders never touch Plex". A Plex-native smart
    collection has no membership to produce -- Plex evaluates its filter live --
    so there is no id list for the engine to resolve and apply, and the whole
    reconcile is the builder's. The Common Sense age buckets are the only such
    family (``collections/reconcile.py``), and 9c decides whether operators ever
    get to define more; until then this stays a two-implementation escape hatch
    rather than a second builder ecosystem.

    A smart builder returns action strings from ``apply`` and ignores the
    membership knobs -- ``limit``, ``sync_mode``, ``item_label`` and
    ``tmdb_summary`` are rejected on its definitions at config load rather than
    silently doing nothing.

    ``definition`` is the definition itself, which a smart builder needs (and a
    list builder does not) because it applies its own collections: the
    per-definition collection settings the engine hands to
    ``reconcile_list_collection`` have to reach the smart reconciler the same
    way. None for a direct caller that has no definition.
    """

    session: AsyncSession
    section: object
    library: str
    library_type: str
    label: str
    config: Any
    http: httpx.AsyncClient | None = None
    dry_run: bool = True
    definition: Any = None


@runtime_checkable
class Builder(Protocol):
    """What a registry entry has to provide.

    Optionally also ``params_model: type[BaseModel]`` -- the model ``build``
    validates ``ctx.config`` through. Left off the protocol itself so a builder
    that takes no params does not have to declare an empty one.
    """

    type_name: str

    async def build(self, ctx: BuilderContext) -> BuilderResult: ...


@runtime_checkable
class SmartBuilder(Protocol):
    """The other kind of registry entry: one that applies itself.

    Marked by ``smart = True``, which is what the engine dispatches on. It
    produces no ids -- see ``SmartContext`` for why -- so it gets the reconcile
    context instead and returns the action strings itself, and it lists the
    titles it manages so the leftovers report can enumerate definitions
    uniformly.
    """

    type_name: str
    smart: bool

    async def apply(self, ctx: SmartContext) -> list[str]: ...

    def titles(self, library_type: str, config: Any) -> set[str]: ...


REGISTRY: dict[str, Builder | SmartBuilder] = {}


def register(builder: Builder | SmartBuilder) -> Builder | SmartBuilder:
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
