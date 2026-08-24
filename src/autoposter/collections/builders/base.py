"""The builder contract: what produces a collection's membership.

A builder answers one question -- "which titles belong in this collection?" --
and answers it as an ordered list of namespaced external ids. Turning those ids
into owned items is the engine's job, so a builder is a fetch-and-translate
step that can be tested without a server -- the few builders whose source *is*
the library read it through ``SourceClients.plex``, read-only and through the
engine's own index, and still hand back ids for the engine to resolve.

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

from autoposter.collections.builders.sources_bundle import PlexSectionAccess, SourceClients
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
    "LibraryTypeMismatch",
    "Namespace",
    "PlexIdBuilder",
    "PlexIdParams",
    "PlexSectionAccess",
    "REGISTRY",
    "SmartBuilder",
    "SmartContext",
    "SourceClients",
    "register",
    "require_library_type",
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
    site. ``cache`` is the process's ``ProviderCache``, the one
    ``providers.fetch.fetch_json`` takes alongside ``http`` -- so a list fetch
    written through ``fetch_json`` is cached and credential-stripped for free.
    None means "do not cache", which is what a direct caller gets and what the
    process itself has when ``providers.cache_ttl_seconds`` is 0.

    ``sources`` is the bundle of constructed clients (``SourceClients``),
    never None: a builder checks the one client it needs, not the bundle and
    then the client. This is the deliberate relaxation of the rule the rest of
    this docstring keeps -- what a builder reaches is a client that already
    holds its credential, never the credential, never the application config.

    ``run_cache`` is scratch shared by every builder in one pass over one
    library, and it exists for exactly one reason: the seven Oscars collections
    all read the same ceremony dataset, which is one file and must stay one
    fetch. A builder that memoises there must memoise the *failure* too, or a
    dead source is re-fetched once per collection.

    Still absent: application config, raw secrets, and any way to *write*.
    Builders do not resolve and do not apply. The library itself is reachable
    only through ``sources.plex`` (``PlexSectionAccess``), read-only and
    sharing the engine's one index, for the handful of builders whose source
    *is* the library.
    """

    library: str
    library_type: str
    http: httpx.AsyncClient | None = None
    config: dict[str, Any] = field(default_factory=dict)
    cache: ProviderCache | None = None
    run_cache: dict[str, Any] = field(default_factory=dict)
    sources: SourceClients = field(default_factory=SourceClients)


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


class LibraryTypeMismatch(Exception):
    """This builder cannot mean anything for the library it is being run on.

    Its own class rather than a ``ValueError`` so the engine's log line -- which
    carries the exception class name and nothing else -- says what kind of
    failure this was.
    """


def require_library_type(subject: str, library_type: str, allowed) -> None:
    """Refuse a source whose media type is not this library's.

    A build-time check rather than a load-time one, and that is forced rather
    than chosen: a definition with no ``libraries:`` key applies to *every*
    library in the pass, so the library type is only known here. The refusal
    matters because the alternative is invisible -- a TV chart resolved against
    a Movie library does not error, it simply matches nothing, and "matched
    nothing" is what a correct collection of titles the library does not own
    looks like too.

    ``subject`` is the phrase that goes in front of the message ("the TMDb
    'airing_today' chart"), and ``allowed`` is any collection of library types
    -- including a ``CHART_ENDPOINTS`` row, whose keys *are* the library types
    that chart has an endpoint for.
    """
    if library_type in allowed:
        return
    kinds = " or ".join(sorted(allowed))
    raise LibraryTypeMismatch(
        f"{subject} builds {kinds} collections, but this pass is running against "
        f"a {library_type} library, where it would match nothing at all. Narrow "
        f"the definition with `libraries:` so it only targets {kinds} libraries."
    )


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
