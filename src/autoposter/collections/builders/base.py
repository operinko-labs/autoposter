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

Since 8b that model is also the *config's* contract: ``CollectionDefinition``
validates a definition's ``params`` against the declared ``params_model`` at
config load (``config/schema.py``), so the mis-spelled key is caught at the
moment of the edit rather than mid-pass hours later. The builders keep
validating ``ctx.config`` themselves regardless -- a builder is also called
directly, by tests and by an expanding builder's constructed definitions --
so the two are defence in depth, not one replacing the other.
"""
from collections.abc import Callable
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
    "PREFERENCE",
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
    "best_external_id",
    "register",
    "require_library_type",
]

# ``(field on the entry, namespace)`` in preference order, per library type:
# which guid the library's items are most likely to carry, and every fallback is
# a member that would otherwise be dropped.
#
# One table, not one per builder. It is a single semantic rule -- "which guid
# does this library type prefer" -- and two copies of it drift: the same movie
# built through ``mdblist_list`` and through ``tracearr_most_watched`` would
# resolve under different namespaces, which is a difference in *membership* that
# nothing downstream could report. Also doubles as the "which library types does
# this builder serve" table both of them pass to ``require_library_type``.
PREFERENCE: dict[str, tuple[tuple[str, str], ...]] = {
    "Movie": (("tmdb_id", "tmdb"), ("imdb_id", "imdb")),
    "Show": (("tvdb_id", "tvdb"), ("tmdb_id", "tmdb"), ("imdb_id", "imdb")),
}


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

    ``session`` is this service's own database, read-only by convention and by
    every use of it: the facts builders' membership IS a query over
    ``item_facts`` joined back to ``media_items``, because the values they build
    on are TMDb's and Plex holds none of them (roadmap rows 189/192). A smart
    builder has had one since 9c (``SmartContext.session``); the asymmetry was a
    gap rather than a rule, and the engine has the session in hand at the
    construction site. None for a direct caller with no database, and a builder
    that needs one says so by raising -- ``build`` raises on failure, so a
    missing session must never be answered with an empty membership.

    ``definition`` is the definition this context was built for, and only a
    FAMILY builder needs it: an expanding builder returns whole
    ``CollectionDefinition``s, and a family's units have to carry both the
    operator's own labels and the family label the delete sweep finds them by.
    ``engine._completed`` fills an unset field from the placeholder, so a
    builder that sets ``labels`` at all has to set both halves -- and it cannot
    read the first half from anywhere else. None for a direct caller, and for
    every builder that does not expand.

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
    session: AsyncSession | None = None
    definition: Any = None


@dataclass(frozen=True)
class SmartContext:
    """What a *smart* builder gets instead of ``BuilderContext``.

    The deliberate exception to "builders never touch Plex". A Plex-native smart
    collection has no membership to produce -- Plex evaluates its filter live --
    so there is no id list for the engine to resolve and apply, and the whole
    reconcile is the builder's.

    Two shapes now use it, and the difference between them is the only thing a
    reader has to hold: ``cs_bucket`` manages a FAMILY of collections whose
    titles it derives itself, and ``smart_filter`` (9c) manages exactly ONE, the
    collection its definition names. That is why ``titles`` below is an optional
    extra rather than a protocol member.

    A smart builder returns action strings from ``apply`` and ignores the
    membership knobs -- which of them are rejected on its definitions at config
    load is the builder's own ``refused_definition_fields`` table
    (``config/schema.py``), because the two shapes cannot apply the same set:
    a family has no single summary, a single collection does.

    ``definition`` is the definition itself, which a smart builder needs (and a
    list builder does not) because it applies its own collections: the
    per-definition collection settings the engine hands to
    ``reconcile_list_collection`` have to reach the smart reconciler the same
    way. None for a direct caller that has no definition.

    ``run_cache`` is the pass's scratch, the same dict ``BuilderContext`` gets
    and for the same reason: ``smart_filter`` resolves the library's tag
    vocabulary through ``plex_search.LibraryTagResolver``, which memoises one
    ``listFilterChoices`` per (library, libtype-scope, field) per pass --
    failures included. Sharing the dict with the list builders is the point: two
    definitions naming ``genre: Horror`` cost one round trip whichever builders
    they use. ``cs_bucket`` shares it too since phase 10a-2: its resolver
    answers both what content ratings the library holds and which key each one
    resolves to, out of the same memo.

    ``listing`` is the pass's ``{title: collection}`` map of the section, as a
    CALLABLE so it is fetched only if a smart builder asks for it. Every list
    definition in a pass already shares one -- ``section.collections()`` returns
    every collection in the library, 305 of them on the production Movies
    section -- and a smart builder that did not take it would pay for that
    listing once per definition, on every pass, unchanged definitions included.
    Calling it is what puts a smart definition on the same one-listing budget.
    Optional, and its absence is a fallback rather than an error: a direct
    caller with no pass around it has nothing to share. ``cs_bucket`` ignores
    it.
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
    # The collection group's sort-title prefix for this definition, resolved by
    # the engine (``collections/groups.py``, roadmap row 49). Handed to a smart
    # builder for exactly the reason ``definition`` is: a smart builder applies
    # its own collections, so everything the engine passes to
    # ``reconcile_list_collection`` has to reach the smart reconcilers the same
    # way. None for a direct caller that has no pass around it.
    sort_prefix: str | None = None
    run_cache: dict[str, Any] = field(default_factory=dict)
    listing: Callable[[], dict] | None = None


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
    context instead and returns the action strings itself.

    Two optional extras, left off the protocol itself for the reason
    ``params_model`` is left off ``Builder``: not every implementation has one.

    - ``titles(library_type, config) -> set[str]`` -- for a builder that manages
      a FAMILY of collections (``cs_bucket``), so the leftovers report and the
      delete sweep can enumerate what it owns. A builder that manages exactly
      the collection its definition names declares none, and
      ``engine.definition_titles`` falls through to ``{definition.title}``.
    - ``refused_definition_fields: dict[str, str]`` -- which
      ``CollectionDefinition`` fields this builder cannot apply, mapped to the
      reason. **Required in practice**: ``config/schema.py`` refuses to validate
      a smart definition whose builder declares none, because silently accepting
      a field that never applies is the failure the table exists to prevent.
    """

    type_name: str
    smart: bool

    async def apply(self, ctx: SmartContext) -> list[str]: ...


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


def best_external_id(
    entry: dict, preference: tuple[tuple[str, str], ...]
) -> ExternalId | None:
    """The best namespaced id for one entry, or None if it carries none.

    ``preference`` is a ``PREFERENCE`` row -- passed in rather than looked up
    here so a caller that already resolved the library type does not resolve it
    twice, and so a caller with an order of its own is not blocked.

    Absence is ``None``, ``""`` and ``0``: the three shapes a source uses for
    "this entry has no such id". Note that the *string* ``"0"`` is not absence
    here -- a caller reading a raw document that might carry one coerces it
    before this sees it (``activity.external_id_or_none`` is that coercion),
    because tightening it here would silently change what ``mdblist_list``
    emits.

    Logging a skip is the CALLER's, deliberately: ``mdblist_list`` names the
    list and the entry title it dropped, and a shared helper could name neither.
    """
    for name, namespace in preference:
        value = entry.get(name)
        if value is None or value == "" or value == 0:
            continue
        return (namespace, str(value))
    return None


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
