"""``smart_url``: the query an operator already built, in Plex's own UI.

Kometa's no-DSL convenience form (``builder.py:1462-1474``). Build the search in
Plex Web, copy the URL out of the address bar, paste it into ``params.url`` --
and this builder extracts the query Plex itself put in that URL and stores it as
a smart collection's filter, through the same reconciler ``smart_filter`` uses.

**It is a spelling of 9c's builder, not a second one.** The extracted string is
exactly what ``build_search_url`` would have produced for the equivalent
``smart_filter`` definition, and ``tests/test_builder_smart_url.py`` proves the
two byte-identical through the real reconcile. So there is one query grammar on
the wire, one envelope, one drift hash -- the paste only replaces the part an
operator would otherwise write by hand.

**The URL is a query nobody validated** (roadmap row 184's own words), which is
why this module has its own refusal surface: ``SmartUrlNotAFilter`` for a paste
that is not a Plex smart-filter URL at all, and ``LibraryTypeMismatch`` for one
that is, but describes the other kind of library. Kometa validates neither --
``get_smart_filter_from_uri`` raises ``ValueError``/``KeyError``/``IndexError``
out of ``parse_qs`` and ``str.index`` and Kometa catches only ``ValueError``
(builder.py:1472-1474) -- so a missing ``key`` is an unhandled ``KeyError``
there. Named refusals here, because "the config is wrong" and "the service
crashed" have to look different to an operator.

**The pasted value is token-stripped before anything reads it.** This is the
first config value in the collections package that an operator pastes from a
BROWSER, and a Plex Web URL can carry ``X-Plex-Token``. ``strip_plex_token``
runs ahead of the parse, so the token cannot reach a parse error, a log line,
an action string, or the query stored on the server -- compare
``smart_filter.py:182``'s ``logger.debug("smart_filter: %s -> %s", ...)``, which
is safe only because that URL was BUILT rather than pasted.
"""
import logging
import re
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoposter.collections.builders.base import (
    LibraryTypeMismatch,
    SmartContext,
    require_library_type,
)
from autoposter.collections.search_sorts import SORT_TYPES
from autoposter.collections.smart import (
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    reconcile_smart_collection,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SmartUrlBuilder",
    "SmartUrlNotAFilter",
    "SmartUrlParams",
    "smart_query_from_uri",
    "strip_plex_token",
]


class SmartUrlNotAFilter(Exception):
    """The pasted value is not a Plex smart-filter URL.

    Its own class so the engine's class-name-only log line says which kind of
    failure this was, and so ``apply``'s catch below can name it exhaustively
    rather than swallowing a genuine ``TypeError`` from this module.
    """


# Any ``…token=…`` query term, in either encoding. Plex Web percent-encodes the
# whole ``key`` parameter, so a token that rides inside it appears as
# ``%26X-Plex-Token%3D…`` and a ``parse_qs``-based strip would walk straight
# past it -- which is why this is a regex over the raw string and not a
# parse-and-rebuild. The value character class is Plex's own token alphabet
# plus the URL-unreserved set, so the match stops at the next separator in
# either encoding.
_TOKEN_TERM = re.compile(
    r"(?i)(?P<lead>[?&]|%3F|%26)[A-Za-z0-9_.-]*token(?:=|%3D)[A-Za-z0-9._~-]*"
)
# ``?`` is structural and ``&`` is not, so a stripped FIRST term leaves the
# ``?`` behind and then an empty term behind it. Collapsed here rather than by
# a smarter substitution, so both encodings are handled by one rule.
_EMPTY_FIRST_TERM = re.compile(r"(?i)(\?|%3F)(?:&|%26)")


def strip_plex_token(value: str) -> str:
    """``value`` with every ``…token=…`` term removed, raw or percent-encoded.

    Removed rather than redacted: a redaction would leave
    ``X-Plex-Token=REDACTED`` inside the query this service STORES on the
    server, which is a filter term Plex was never sent before. Nothing
    downstream needs to know a token was there -- only that it is not.
    """
    def _drop(match: re.Match) -> str:
        lead = match.group("lead")
        return lead if lead.lower() in ("?", "%3f") else ""

    return _EMPTY_FIRST_TERM.sub(r"\1", _TOKEN_TERM.sub(_drop, value))


# Plex's own search types, for the two libtypes this service builds collections
# for. ``SORT_TYPES`` holds the same two keys and is the table the rest of the
# search half indexes; this maps the other way, from the digit a pasted URL
# carries to the name.
_LIBTYPE_BY_TYPE_KEY = {"1": "movie", "2": "show"}


def smart_query_from_uri(uri: str) -> tuple[str, str]:
    """Kometa's ``get_smart_filter_from_uri`` (modules/plex.py:1610-1613).

    Returns ``(args, libtype)`` -- the query string from ``?`` onward, which is
    exactly what ``reconcile_smart_collection`` takes, and the library type the
    URL describes.

    Kometa's three lines, kept step for step:

    1. rewrite ``/#!/`` to ``/`` -- without it everything after the ``#`` is a
       URL FRAGMENT and ``urlparse`` reports an empty query;
    2. take the ``key`` parameter, which is the section path plus its own,
       percent-encoded, query;
    3. keep that from its own ``?`` onward, and read the libtype as the SINGLE
       character after ``type=``.

    Step 3's single character is Kometa's slice (``args[i + 5:i + 6]``) and is
    transcribed rather than corrected: ``type=10`` therefore reads as ``1``.
    This service only builds Movie and Show collections, and a ``type=10``
    query stored against a movie section is caught downstream by
    ``smart.require_matches``, which refuses a filter matching nothing.
    Correcting it here would diverge from the oracle for no operator-visible
    gain.

    Every refusal is a ``SmartUrlNotAFilter`` naming the pasted value AFTER the
    token strip, because the operator has to recognise which of their
    definitions is wrong and the value carries no credential once stripped.
    """
    safe = strip_plex_token(uri)
    query = urlparse(safe.replace("/#!/", "/")).query
    keys = parse_qs(query).get("key") or []
    if not keys:
        raise SmartUrlNotAFilter(
            f"{safe!r} has no `key` parameter, so it is not a Plex smart-filter "
            "URL. Build the search in Plex Web and copy the whole address, "
            "which looks like `https://app.plex.tv/desktop/#!/server/.../"
            "com.plexapp.plugins.library?...&key=%2Flibrary%2Fsections%2F...` "
            "-- a `.../web/index.html#!/...` address keeps its query in the "
            "URL fragment, where nothing can read it"
        )
    key = keys[0]
    if "?" not in key:
        raise SmartUrlNotAFilter(
            f"{safe!r} carries a `key` with no query of its own "
            f"({key!r}), so there is no filter in it. This is what a plain "
            "library view looks like; switch a filter on in Plex Web first"
        )
    args = strip_plex_token(key[key.index("?"):])
    marker = args.find("type=")
    if marker < 0:
        raise SmartUrlNotAFilter(
            f"{safe!r} carries a query with no `type=` in it ({args!r}), so "
            "there is nothing to say what kind of items it searches"
        )
    libtype = _LIBTYPE_BY_TYPE_KEY.get(args[marker + 5:marker + 6])
    if libtype is None:
        raise SmartUrlNotAFilter(
            f"{safe!r} searches a kind of item this service does not build "
            f"collections for ({args[marker:marker + 6]!r}) -- only movie or "
            "show searches can become a managed collection"
        )
    return args, libtype


class SmartUrlParams(BaseModel):
    """One key: the URL. ``extra="forbid"`` for the reason every params model
    here has it -- a mis-spelled key is an error rather than a silently ignored
    one."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1,
        description=(
            "A Plex Web smart-filter URL, copied whole from the address bar. "
            "Any X-Plex-Token in it is stripped before anything reads it."
        ),
    )

    @field_validator("url")
    @classmethod
    def _the_url_must_carry_a_filter(cls, value: str) -> str:
        """Refuse at config LOAD, which is what Kometa does too (``'smart_url'
        attribute is incorrectly formatted``, builder.py:1472-1474). The
        extraction is pure -- no Plex, no library -- so the only thing that
        cannot be checked here is whether the libtype suits the library the
        definition eventually runs against, which is ``search_url``'s check
        below and is build-time by necessity: a definition with no
        ``libraries:`` key runs against every library in the pass."""
        try:
            smart_query_from_uri(value)
        except SmartUrlNotAFilter as refusal:
            raise ValueError(str(refusal)) from None
        return value


class SmartUrlBuilder:
    """One Plex-native smart collection, from a URL the operator pasted."""

    type_name = "smart_url"
    # The engine's marker for "this one applies itself" -- see SmartContext.
    smart = True
    params_model = SmartUrlParams

    # Character for character ``SmartFilterBuilder``'s table, and deliberately
    # so: the two builders produce the SAME kind of collection by the same
    # route, so a field one cannot apply the other cannot either. Spelled out
    # rather than imported from ``smart_filter`` because
    # ``config/schema.py``'s validator reads this attribute off the registry
    # entry and a shared mutable dict between two builders would let one edit
    # move the other's refusals; the test asserts the two sets are equal, which
    # is the drift guard a shared object would only appear to give.
    refused_definition_fields = {
        "sort": (
            "on a smart collection the search's own order IS the display order, "
            "and the pasted URL already carries it as `sort=`. Change the sort "
            "in Plex Web and paste the URL again"
        ),
        "limit": (
            "Plex evaluates this collection's membership live, so there is no "
            "resolved list to cap. Cap the SEARCH instead -- Plex Web writes "
            "that into the URL as `limit=`"
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

    # Every refusal an operator's CONFIGURATION can cause once it meets a real
    # library. Named exhaustively rather than as ``Exception`` so a genuine bug
    # in this module still reaches the engine as a failure instead of being
    # reported to the operator as something about their URL. Shorter than
    # ``smart_filter``'s tuple by four classes, and that is the point of this
    # builder: no sort table, no tag vocabulary and no query grammar are
    # consulted, because Plex already built the query.
    REFUSALS = (
        LibraryTypeMismatch,
        SmartCollectionUnavailable,
        SmartFilterMatchedNothing,
        SmartUrlNotAFilter,
    )

    def search_url(self, ctx: SmartContext) -> str:
        """The query string for this definition, from ``?`` onward.

        Separate from ``apply`` for the reason ``smart_filter.search_url`` is:
        it is the half that has an answer without a database, a collection or a
        write.
        """
        if ctx.definition is None:
            raise ValueError(
                "the 'smart_url' builder builds the collection its definition "
                "names, so it cannot run without one"
            )
        params = SmartUrlParams.model_validate(ctx.definition.params)
        require_library_type(
            "the 'smart_url' builder", ctx.library_type, ("Movie", "Show")
        )
        url, libtype = smart_query_from_uri(params.url)
        if libtype != ctx.library_type.lower():
            raise LibraryTypeMismatch(
                "this smart_url is a %s search (`type=%d` in the pasted URL), "
                "but this pass is running against a %s library, where Plex "
                "would store a filter it cannot evaluate. Narrow the "
                "definition with `libraries:`, or paste the URL from the "
                "library you meant"
                % (libtype, SORT_TYPES[libtype].key, ctx.library_type)
            )
        return url

    async def apply(self, ctx: SmartContext) -> list[str]:
        definition = ctx.definition
        if definition is None:
            raise ValueError(
                "the 'smart_url' builder builds the collection its definition "
                "names, so it cannot run without one"
            )
        collections = ctx.config.collections
        try:
            url = self.search_url(ctx)
            # Safe to log in full: ``smart_query_from_uri`` stripped every
            # token term before this string existed.
            logger.debug("smart_url: %s -> %s", definition.title, url)
            return await reconcile_smart_collection(
                ctx.session,
                ctx.section,
                ctx.library,
                ctx.library_type,
                definition.title,
                url,
                ctx.label,
                summary=ctx.summary if ctx.summary is not None else definition.summary,
                summary_asserted=ctx.summary_asserted,
                dry_run=ctx.dry_run,
                existing=ctx.listing() if ctx.listing is not None else None,
                adopt=collections.adopt,
                adopt_from=collections.adopt_from,
                adopt_removes_prior_label=collections.adopt_removes_prior_label,
                protect_labels=collections.protect_labels,
                http=ctx.http,
                config=ctx.config,
                settings=definition,
                sort_prefix=ctx.sort_prefix,
            )
        except self.REFUSALS as refusal:
            # Contained deliberately, the same shape as
            # ``smart_filter.apply``'s: an operator's configuration meeting
            # this library must cost this one definition its pass and nothing
            # else. A failing label write or summary PUT still reaches the
            # per-library rollback, unchanged.
            logger.warning(
                "%s: %r was not built: %s", ctx.library, definition.title, refusal
            )
            return ["refused %r: %s" % (definition.title, refusal)]
