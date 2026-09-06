"""``smart_filter``: a query the operator writes once and PLEX evaluates forever.

The same search 9b's ``plex_search`` takes, pointed at a different destination.
``plex_search`` asks Plex the question on every pass and hands the answers to the
engine, which resolves them, diffs them and writes the membership. ``smart_filter``
asks the question ONCE -- to check it matches something -- and then stores it on
the server, after which Plex answers it live and this service never touches the
membership again. A new item that matches appears in the collection with no pass
in between, which is the whole point and is also the acceptance criterion the
roadmap wrote for this phase.

**One builder, one behaviour (C5).** ``params_model`` is ``PlexSearchParams``
ITSELF -- not a copy, not a subclass -- so the vocabulary, the refusals and the
messages cannot drift between the two builders. Two deltas are parameterised
inside that shared model rather than duplicated beside it:

- the default sort is ``random`` when the config names none, which is Kometa's
  for this builder and only this builder (``default_sort="random"``,
  modules/builder.py:1478), against ``plex_search``'s ``title.asc``;
- the definition's own ``sort`` is refused (C7) -- see
  ``refused_definition_fields`` below.

A consequence, stated here because an operator will meet it: the shared model's
messages name ``plex_search`` ("a plex_search has one base", "the plex_search
vocabulary is ..."). That is accurate -- the vocabulary IS that one -- and the
alternative, a second set of messages differing only in a word, is exactly the
drift the shared model exists to prevent.

**Every refusal is contained to this definition.** ``engine.py:341-344``
deliberately does not wrap a smart builder's ``apply``, on the grounds that
anything it raises is a Plex WRITE failing, which belongs to the per-library
rollback. That reasoning is still right, and it is why this module catches its
own refusals: a filter matching nothing, a genre the library does not have, a
show-only sort against a movie library and a shape conflict are the OPERATOR's
configuration meeting this library, not a write failing, and each must cost this
one definition its pass and nothing else. What is not caught -- a failing label
write, a failing summary PUT -- still reaches the rollback, unchanged.
"""
import datetime as dt
import logging

from autoposter.collections.builders.base import (
    LibraryTypeMismatch,
    SmartContext,
    require_library_type,
)
from autoposter.collections.builders.plex_search import (
    LibraryTagResolver,
    PlexSearchParams,
    PlexSearchUnavailable,
)
from autoposter.collections.filters import predicates, resolve_search_values
from autoposter.collections.search_sorts import SortNotAvailable
from autoposter.collections.search_url import (
    SearchAttributeNotAvailable,
    SearchProducedNothing,
    TagValueNotFound,
    build_search_url,
)
from autoposter.collections.smart import (
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    reconcile_smart_collection,
)

logger = logging.getLogger(__name__)

__all__ = ["SmartFilterBuilder"]

# Kometa's default for this builder and no other (modules/builder.py:1478).
# Passed as a one-element ``sort_by`` rather than through a new argument to
# ``sort_argument``: the sort tables already hold ``random`` for both libtypes,
# so the delta is a value, not a signature.
DEFAULT_SORT = "random"

# Every refusal an operator's CONFIGURATION can cause once it meets a real
# library. Held as a tuple so ``apply`` has one catch rather than six, and named
# exhaustively rather than as ``Exception`` so a genuine bug -- a TypeError in
# this module -- still reaches the engine as a failure instead of being reported
# to the operator as something about their filter.
REFUSALS = (
    LibraryTypeMismatch,
    PlexSearchUnavailable,
    SearchAttributeNotAvailable,
    SearchProducedNothing,
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    SortNotAvailable,
    TagValueNotFound,
)


class SmartFilterBuilder:
    """One Plex-native smart collection, from the operator's own query."""

    type_name = "smart_filter"
    # The engine's marker for "this one applies itself" -- see SmartContext.
    smart = True
    params_model = PlexSearchParams

    # C7, and the closing half of roadmap row 140: which definition fields this
    # builder cannot apply, and why. Read at config load
    # (``config/schema.py``), so an operator learns at the moment of the edit
    # rather than from a setting that reads as applied and never is.
    #
    # ``summary`` -- and since row 186, ``tmdb_summary`` -- are deliberately
    # ABSENT: a smart_filter definition names one collection and the reconciler
    # writes its summary (the tmdb pull resolved engine-side, through the same
    # ``_summary_for`` every list definition uses), exactly as the Common
    # Sense reconciler writes each bucket's. ``sort_title``, ``collection_mode``
    # and the ``visible_*`` flags are absent for the reason they always were:
    # they are properties of the collection OBJECT, not of its membership, and
    # the create path applies them.
    refused_definition_fields = {
        "sort": (
            "on a smart collection the search's own order IS the display order, "
            "so `sort` here and `params.sort_by` inside the search would be two "
            "knobs steering one behaviour. Write the order as `params.sort_by`"
        ),
        "limit": (
            "Plex evaluates this collection's membership live, so there is no "
            "resolved list to cap. Cap the SEARCH instead, with `params.limit` "
            "-- which, with a `params.sort_by`, is what 'the 50 highest-rated' "
            "means"
        ),
        "sync_mode": (
            "Plex owns this collection's membership; there is nothing for this "
            "service to sync or append to"
        ),
        "item_label": (
            "this service never resolves this collection's members -- Plex "
            "does -- so there is no list of items to label"
        ),
        "filters": (
            "a `filters:` block narrows a membership this service resolved, and "
            "this one is never resolved here. Put the narrowing INSIDE the "
            "search, where Plex will evaluate it live along with the rest"
        ),
    }

    def search_url(self, ctx: SmartContext) -> str:
        """The query string for this definition, from ``?`` onward.

        Separate from ``apply`` because it is the half that has an answer
        without a database, a collection or a write -- which is what makes the
        default sort and the tag resolution testable on their own, and what the
        oracle's ``our_query`` mirrors.
        """
        if ctx.definition is None:
            raise ValueError(
                "the 'smart_filter' builder builds the collection its definition "
                "names, so it cannot run without one"
            )
        params = PlexSearchParams.model_validate(ctx.definition.params)
        # Roadmap row 176, ruling C5. ``smart_definition_hash``
        # (collections/smart.py:225) hashes the URL this method returns, and for
        # every other attribute that URL is a pure function of the config.
        # ``folder_location``'s Plex field is discovered from the SERVER at run
        # time, so shipping it here would make a stored definition hash a
        # function of the server as well -- and a Plex-side rename of the filter
        # would re-PUT the filter of every smart collection naming it, with
        # nothing in the config having changed. Refused rather than disclosed:
        # a definition hash that is a function of the config alone is a property
        # worth keeping whole.
        if any(one.attribute.name == "folder_location" for one in predicates(params.group)):
            raise SearchAttributeNotAvailable(
                "folder_location: this attribute's Plex field is discovered "
                "from the server at run time, and a smart collection stores "
                "its query on the server forever -- so a Plex-side rename of "
                "that filter would silently rewrite every definition naming "
                "it. Use the `plex_search` builder, which asks the question on "
                "every pass, or remove the clause"
            )
        require_library_type(
            "the 'smart_filter' builder", ctx.library_type, ("Movie", "Show")
        )
        libtype = ctx.library_type.lower()
        # Search-tail E-2, the same read ``PlexSearchBuilder.build`` makes and
        # for the same reason: Kometa's ``sort_type`` under a collection IS
        # ``builder_level`` (modules/builder.py:4093-4121). Refused here rather
        # than left to Plex, because the URI a smart collection stores is
        # evaluated live and forever: a ``type=4`` filter written into a movie
        # library would keep matching nothing until someone noticed.
        level = getattr(ctx.definition, "builder_level", "item")
        if level != "item" and ctx.library_type != "Show":
            raise LibraryTypeMismatch(
                f"a 'builder_level: {level}' search asks a library for the "
                f"{level}s inside its shows, but this pass is running against "
                f"a {ctx.library_type} library, where it would match nothing at "
                "all. Narrow the definition with `libraries:` so it only "
                "targets Show libraries."
            )
        # ``resolve_search_values`` first, against ONE moment for this build --
        # the same fix ``PlexSearchBuilder.build`` applies, and needed here for
        # a stronger reason than there: this URI is not a transient query, it
        # is the string Plex STORES as the smart collection's own filter, so
        # an unresolved ``_CurrentYear``/``_Today`` sentinel would not just
        # answer one pass wrong -- it would be written into the operator's
        # library and keep mis-selecting until someone noticed.
        return build_search_url(
            resolve_search_values(params.group, now=dt.datetime.now()),
            libtype=libtype,
            search_type=libtype if level == "item" else level,
            sort_by=params.sort_by or (DEFAULT_SORT,),
            limit=params.limit,
            resolve_tag=LibraryTagResolver(ctx, ctx.section, libtype),
        )

    async def apply(self, ctx: SmartContext) -> list[str]:
        definition = ctx.definition
        if definition is None:
            raise ValueError(
                "the 'smart_filter' builder builds the collection its definition "
                "names, so it cannot run without one"
            )
        collections = ctx.config.collections
        try:
            url = self.search_url(ctx)
            logger.debug("smart_filter: %s -> %s", definition.title, url)
            return await reconcile_smart_collection(
                ctx.session,
                ctx.section,
                ctx.library,
                ctx.library_type,
                definition.title,
                url,
                ctx.label,
                summary=ctx.summary if ctx.summary is not None else definition.summary,
                # Carried, never inferred here: only the engine knows whether an
                # absent summary is this definition asserting it has none (which
                # licenses the clear) or a ``tmdb_summary:`` that could not be
                # resolved this pass (which must leave the summary alone).
                summary_asserted=ctx.summary_asserted,
                dry_run=ctx.dry_run,
                # The pass's one listing, not a second one per definition.
                # Called here rather than at context construction so a
                # definition that refuses in ``search_url`` above costs
                # nothing, and so a pass carrying no smart_filter definition
                # never fetches it at all.
                existing=ctx.listing() if ctx.listing is not None else None,
                adopt=collections.adopt,
                adopt_from=collections.adopt_from,
                adopt_removes_prior_label=collections.adopt_removes_prior_label,
                protect_labels=collections.protect_labels,
                http=ctx.http,
                config=ctx.config,
                settings=definition,
                sort_prefix=ctx.sort_prefix,
                level=getattr(definition, "builder_level", "item"),
            )
        except REFUSALS as refusal:
            # Contained deliberately -- see the module docstring. Logged as well
            # as reported, because the action string reaches a run report an
            # operator may not read and the logs page is where they look when a
            # collection stops updating.
            logger.warning(
                "%s: %r was not built: %s", ctx.library, definition.title, refusal
            )
            return ["refused %r: %s" % (definition.title, refusal)]
