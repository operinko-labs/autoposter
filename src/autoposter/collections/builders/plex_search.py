"""``plex_search``: a query language against the library, answered by Plex.

The one builder whose membership the SERVER decides. Every other builder in
this package fetches a list from somewhere and hands the engine ids to resolve;
this one asks the library a question and hands back the rating keys of whatever
answered -- ``("plex", ratingKey)`` ids, which the engine resolves through its
own owned index exactly like any other builder's, so the engine keeps one shape
and this builder gets the ownership, dry-run and reconcile behaviour for free.

**The server narrows, the client refines.** A ``filters:`` block on a
``plex_search`` definition stays CLIENT-side and runs after resolution, exactly
as it does on any other definition (``engine.py:457-474``). The two are not
folded into one query, deliberately: query-folding is an optimisation with a
very large correctness surface -- the two vocabularies are not the same set,
the two evaluate their dates in different clocks, and a fold that got either
wrong would produce a full, plausible, wrong collection. So a definition may
carry both, and each does its own job.

**The two vocabularies share one table and are not the same set.** Which
attributes a search may name is ``FILTER_ATTRIBUTES``'s ``searchable`` column
and which a ``filters:`` block may name is its ``filterable`` column
(``collections/filters.py``). Kometa's own two vocabularies are not nested
either -- 44 of its filter names have no Plex search field and 29 of its search
names have no filter -- so every refusal on either side cross-references the
other rather than reading as a gap. Both refusals live in the PARSER
(``filters._split_key``), reached from here by passing ``searching=True``;
nothing in this module restates them, because two copies of a vocabulary rule
are two things to keep in step.

**Dates are answered in the PLEX SERVER's clock, not the runner's** (roadmap
row 154, and D6: document the divergence, do not reconcile it). ``added.after:
2026-06-01`` in a ``plex_search`` is decided by the server; the same line in a
``filters:`` block is decided by the process running this service, and 9a
established that the value it compares is already in the runner's local clock
because plexapi converts Plex's epoch with a bare ``datetime.fromtimestamp``.
The two can disagree about an item near a boundary. The relative-window forms
(``added: 30``, ``last_played.not: 6o``) are day-granular or coarser and are
therefore insensitive to the offset, which is why the params docstring
recommends them.

**Tag values are validated against the library's own vocabulary at BUILD time**
(roadmap row 158). A search sends Plex a KEY, never a written word, so the
lookup is not an extra check bolted on -- it is how the query gets built at all.
One ``listFilterChoices`` per (library, field, libtype) per pass, memoised in
``ctx.run_cache``, failures included. Load time cannot do it: the library is
not known until the pass, which is the same reason ``require_library_type``
is a build-time check (``builders/base.py:246-269``).
"""
import logging
from typing import Any

import langcodes
import requests
from plexapi.exceptions import BadRequest, NotFound, PlexApiException
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    SmartContext,
    require_library_type,
)
from autoposter.collections.filters import BY_NAME, parse_filters
from autoposter.collections.search_sorts import KNOWN_SORT_NAMES
from autoposter.collections.search_url import build_search_url

logger = logging.getLogger(__name__)

__all__ = [
    "LibraryTagResolver",
    "PlexSearchBuilder",
    "PlexSearchParams",
    "PlexSearchUnavailable",
]

# The keys Kometa accepts and this builder refuses, each with the reason. Held
# as data so the refusals cannot drift apart in wording, and so adding one is
# a row rather than a branch.
_REFUSED_KEYS: dict[str, str] = {
    "validate": (
        "`validate: false` tells Kometa to log a per-attribute error and carry on "
        "(modules/builder.py:4160-4167), which builds the query WITHOUT the clause "
        "it could not resolve -- a narrower collection than the config asks for, "
        "with nothing failing anywhere. This service refuses silently-wrong "
        "membership: fix the value, or remove the clause"
    ),
    "type": (
        "`type:` selects the season, episode, album or track libtype "
        "(modules/builder.py:4109-4121). This builder searches movie and show "
        "libraries; accepting the key and ignoring it would be a setting that "
        "reads as applied and is not"
    ),
}


class PlexSearchUnavailable(Exception):
    """The context carries no library accessor, or Plex would not answer.

    Its own class so the engine's log line -- which carries the exception class
    name and nothing else -- says which client was missing. Never carries a
    Plex exception's message: those can contain a tokenised URL.
    """


class PlexSearchParams(BaseModel):
    """``plex_search``'s params: the base, the query, the order and the cap.

    ``all:`` or ``any:`` -- exactly one, written out. Kometa also accepts the
    base being OMITTED and then chooses one per key (a bare ``genre:`` becomes
    an OR block, ``genre.and:`` an AND one, modules/builder.py:4261-4276);
    refused here, because the same key would mean two different memberships
    depending on a three-character suffix.

    ``sort_by`` -- **not** ``sort``. Kometa's key is ``sort_by``
    (modules/builder.py:4130) and this definition already has a ``sort``, which
    is the *collection's* Plex display order and a completely different
    setting. All four narrowing knobs may coexist, and they compose in this
    order:

    1. ``params.sort_by`` decides the order Plex returns the search in;
    2. ``params.limit`` caps what Plex returns, so with a ``sort_by`` it
       decides WHICH items come back -- "the 50 highest-rated" is these two
       together and nothing else can express it;
    3. the definition's ``filters:`` block refines what came back, client-side;
    4. the definition's ``limit`` caps the members that survived, and the
       definition's ``sort`` sets how Plex displays them.

    So ``params.limit: 50`` with ``limit: 25`` means "ask Plex for its top 50,
    keep the first 25 of those this library still owns and the filter kept".

    Written here rather than only in ``search_sorts``' module docstring on
    purpose: the collision is between two keys an OPERATOR writes, one inside
    ``params`` and one beside it, so the place it has to be readable is the
    model that accepts them.

    **Dates:** prefer the relative windows (``added: 30``,
    ``last_played.not: 6o``) over the absolute ``.before``/``.after`` forms.
    A server-side date predicate is evaluated in the PLEX SERVER's clock and a
    ``filters:`` one in the runner's (roadmap row 154); a window measured in
    days or longer is insensitive to that offset and a same-day boundary is
    not. ``o`` is months and ``m`` is minutes -- the units are Kometa's
    (modules/plex.py:307).

    ``extra="forbid"`` so ``sort:`` for ``sort_by:`` is an error rather than a
    silently-ignored key -- the same failure ``PlexIdParams`` exists to catch
    (builders/base.py:299-312), one level down.
    """

    model_config = ConfigDict(extra="forbid")

    all: dict | None = None
    any: dict | None = None
    sort_by: list[str] | None = None
    limit: int | None = Field(default=None, ge=1)

    # Set by ``_the_block_must_parse_as_a_search``, the one place the block is
    # parsed. ``build()`` reads it through the ``group`` property rather than
    # calling ``parse_filters`` a second time on the same block and the same
    # arguments (Task 4 review, Minor 11).
    _group: Any = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _the_base_and_the_refused_keys(cls, data: Any) -> Any:
        """Everything that has to beat ``extra="forbid"``.

        A ``mode="before"`` validator, and that placement is the whole point:
        with no base written, EVERY key in the mapping is an extra field, so
        pydantic's own answer would be "Extra inputs are not permitted:
        genre, studio" -- which is true and tells an operator nothing about
        what a plex_search actually wants. The three keys Kometa does have get
        a reason here for the same reason.

        Every check below matches keys EXACTLY, not case-insensitively.
        Before Task 4's fix round this matched case-insensitively while field
        acceptance (``all``, ``any``, ``sort_by``, ``limit``, and
        ``extra="forbid"`` itself) always has not -- so ``All:`` satisfied
        "a base is present" here and was then rejected two validators later
        by pydantic's generic "Extra inputs are not permitted", losing the
        tailored message anyway (Task 4 review, Minor 12). Matching exactly
        is the stricter of the two and it is what makes this validator's
        verdict and pydantic's field lookup agree on every input.

        This is also the ONLY place the written base is known by name rather
        than inferred from which of ``self.all``/``self.any`` ended up
        non-``None`` -- which is why the two empty-base checks below live
        here rather than in an ``after`` validator (Task 4 review, Important
        1): ``{"all": None}`` and no base written at all are indistinguishable
        once pydantic has applied the field defaults, because both leave
        ``self.all`` and ``self.any`` at ``None``.
        """
        if not isinstance(data, dict):
            return data
        keys = {str(key) for key in data}
        for refused, why in _REFUSED_KEYS.items():
            if refused in keys:
                raise ValueError(f"{refused!r} is not accepted here. {why}")
        if "sort" in keys and "sort_by" not in keys:
            raise ValueError(
                "the search's order is `sort_by`, not `sort` -- `sort` on the "
                "definition itself is the collection's display order in Plex, "
                "which is a different setting and is still available"
            )
        bases = [name for name in ("all", "any") if name in keys]
        if len(bases) == 2:
            raise ValueError(
                "a plex_search has one base: write `all:` (every clause must "
                "match) or `any:` (one must), not both. Kometa refuses this too "
                "(modules/builder.py:4106-4107)"
            )
        if not bases:
            raise ValueError(
                "a plex_search needs a base. Write the clauses under `all:` if "
                "every one must match, or under `any:` if one is enough. Kometa "
                "lets you omit it and then picks per key -- a bare `genre:` "
                "becomes an OR and `genre.and:` an AND "
                "(modules/builder.py:4261-4276) -- which makes one spelling mean "
                "two memberships, so it is not accepted here"
            )
        # The two empty-base messages Kometa's own ``build_filter`` raises
        # (``{base} attribute is blank`` / ``{base} must be a dictionary``,
        # kometa_build_filter.py:951/:953) -- reproduced here because a bare
        # `all:` with nothing under it (YAML's ``{"all": None}``) is the most
        # common way to hit this, and naming ``any`` -- the base the operator
        # never wrote -- sends them looking for a block that does not exist.
        written = bases[0]
        value = data[written]
        if value is None:
            raise ValueError(
                f"`{written}:` is written but empty. Give it at least one "
                "clause, or remove the key"
            )
        if not isinstance(value, dict):
            raise ValueError(
                f"`{written}:` must be a mapping of attributes, not {value!r}. "
                "Write each clause as `attribute: value` beneath it"
            )
        if isinstance(data.get("sort_by"), str):
            data = {**data, "sort_by": [data["sort_by"]]}
        return data

    @model_validator(mode="after")
    def _the_block_must_parse_as_a_search(self) -> "PlexSearchParams":
        """The whole vocabulary check, at LOAD.

        Unknown attribute, a modifier the type does not take in a search, an
        unparseable value, a `.regex`, a `.and`, a bare `duration:` -- every one
        of them refuses here, naming the key, hours before the pass. What
        CANNOT be checked here is anything that needs the library: which
        libtype, and whether a tag value exists. Those are build-time, and the
        module docstring says why.

        ``field`` is ``params.<base>`` and not the parser's ``filters``
        default, so a refusal names the block an operator would go and edit.
        The rest of the sentence -- "the plex_search vocabulary is ...", "write
        it as a `filters:` block instead" -- is the parser's own, selected by
        ``searching=True``.

        The result is kept on ``self`` (the ``group`` property) rather than
        discarded: it is the same parse ``build()`` would otherwise redo on
        the same block with the same arguments.
        """
        self._group = parse_filters(
            self.block, field=f"params.{self.base}", searching=True, base=self.base
        )
        return self

    @model_validator(mode="after")
    def _sort_names_must_exist_somewhere(self) -> "PlexSearchParams":
        for name in self.sort_by or []:
            if name not in KNOWN_SORT_NAMES:
                raise ValueError(
                    f"sort_by {name!r} is not a Plex sort. Options: "
                    + ", ".join(sorted(KNOWN_SORT_NAMES))
                )
        return self

    @property
    def base(self) -> str:
        return "all" if self.all is not None else "any"

    @property
    def block(self) -> dict:
        return self.all if self.all is not None else self.any

    @property
    def group(self):
        """The block, already parsed and validated at load time."""
        return self._group


def _base_language_code(value: str) -> str:
    """A language value in any common form, reduced to its base ISO 639-1 code.

    Transcribed from Kometa's ``base_language_code`` (modules/plex.py:141-151),
    including its fallback: a value that cannot be parsed comes back unchanged,
    so an unrecognised code targets itself rather than nothing. ``langcodes`` is
    the same library Kometa uses -- see the Task 4 Step 0 decision record for
    why it was added rather than transcribed. Its ``LanguageTagError`` is a
    ``ValueError`` subclass, which is what makes the fallback below catch it.
    """
    if not value:
        return value
    try:
        return langcodes.Language.get(str(value)).language or value
    except ValueError:
        return value


class PlexSearchBuilder:
    """The library, asked a question."""

    type_name = "plex_search"
    params_model = PlexSearchParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = PlexSearchParams.model_validate(ctx.config)
        require_library_type(
            "the 'plex_search' builder", ctx.library_type, ("Movie", "Show")
        )
        libtype = ctx.library_type.lower()
        access = ctx.sources.plex
        if access is None:
            raise PlexSearchUnavailable(
                "the 'plex_search' builder reads the library it is running "
                "against, and this context carries no library accessor"
            )
        section = access.section()

        # No separate ``require_sort_for_libtype`` call here any more: it is
        # the first statement of ``build_search_url`` itself now (Task 4
        # review, ruling on Minor 1), which gives every caller the message
        # AND -- because it runs ahead of ``_render_group`` -- still costs
        # this builder zero ``listFilterChoices`` round-trips before a
        # wrong-libtype sort refuses.
        url = build_search_url(
            params.group,
            libtype=libtype,
            sort_by=params.sort_by or (),
            limit=params.limit,
            resolve_tag=LibraryTagResolver(ctx, section, libtype),
        )
        logger.debug("plex_search: %s", url)
        # Blanket, deliberately, and NOT the three-clause shape
        # ``LibraryTagResolver._raw_choices`` uses below: this catch never memoises
        # anything (there is no ``run_cache`` entry a bug could be mistaken
        # for a library fact), and it is the request that actually returns
        # the collection's membership, so any failure here -- library bug,
        # Plex error, dropped connection -- ends the build the same way. The
        # resolver's finer split exists only because IT caches its verdict
        # for the rest of the pass and must not cache a coding bug as "Plex
        # has no such filter" (Task 4 review, Minor 3 / Fix-round Carry 1).
        try:
            items = section.fetchItems(
                f"/library/sections/{section.key}/all{url}"
            )
        except Exception as error:  # class name only, never the message
            raise PlexSearchUnavailable(
                "Plex would not answer this search: "
                f"{type(error).__name__}"
            ) from None
        ids = [("plex", str(item.ratingKey)) for item in items]
        logger.debug("plex_search: %d item(s)", len(ids))
        return BuilderResult(ids=ids)


# Above ``LibraryTagResolver``, its only user, rather than at the bottom of the
# file:
# a module-level sentinel read on every ``run_cache.get`` lookup, not a
# forward reference that happens to work because Python resolves names inside
# a method body at call time rather than at class-definition time.
_MISSING = object()


class LibraryTagResolver:
    """The library's tag vocabulary, cached per pass.

    Public (and exported) since 9c, because ``smart_filter`` needs the same
    vocabulary and the same per-pass cache -- and since 10a it also exposes the
    raw vocabulary through ``choices``, which is what the dynamic engine
    enumerates. Two copies of this would drift, and a drift here is the same
    written word resolving to two different Plex keys in two builders -- a
    difference in MEMBERSHIP that nothing downstream could report. It takes any
    context object carrying ``library`` and ``run_cache``, which is what
    ``SmartContext`` grew in 9c.

    One ``listFilterChoices`` per (library, libtype-scope, field) per pass, and
    the FAILURE is memoised too -- ``BuilderContext.run_cache``'s own docstring
    requires that, because a dead source re-fetched once per collection is the
    defect the cache exists to prevent.

    The lookup is keyed on **four** spellings per choice -- ``title``, ``key``,
    and both lowercased -- which is Kometa's (``get_search_choices``,
    modules/plex.py:1308-1315), and is where the case-insensitivity an operator
    sees actually lives. Later choices overwrite earlier ones on a collision,
    also Kometa's (plain assignment, not ``setdefault``).
    """

    def __init__(self, ctx: BuilderContext | SmartContext, section, libtype: str) -> None:
        self._ctx = ctx
        self._section = section
        self._libtype = libtype

    def __call__(self, attribute: str, value: str, /) -> tuple[str, ...]:
        scope, name = self._field_and_scope(attribute)
        if attribute in ("audio_language", "subtitle_language"):
            return self._language_keys(attribute, scope, name, value)
        choices = self._choices(attribute, scope, name)
        for spelling in (str(value), str(value).lower()):
            if spelling in choices:
                return (choices[spelling],)
        return ()

    def choices(self, attribute: str, /) -> tuple[tuple[str, str], ...]:
        """Every ``(key, title)`` this library reports for ``attribute``.

        The enumeration primitive, public since 10a. ``__call__`` above answers
        "which key does Plex know this written word by"; this answers "what does
        this library HAVE", which is the question one-collection-per-value asks
        and the only question ``_raw_choices`` was already able to answer
        without a second round trip.

        It is a method here rather than a function elsewhere because
        ``_raw_choices`` owns three things a second implementation would have to
        duplicate and could get wrong: the one-call-per
        ``(library, libtype-scope, field)`` memo, the narrow
        ``NotFound``/``BadRequest`` split against the blanket
        ``PlexApiException``/``RequestException`` one, and the class-name-only
        wrap that keeps a tokenised URL out of the message. Kometa's own
        enumeration is the same call through the same table
        (``get_tags``, modules/plex.py:1346-1364).

        Both members are ``str``: a caller comparing an operator's written
        ``include:`` entry against these must compare like with like, and Plex
        answers some keys as integers.
        """
        scope, name = self._field_and_scope(attribute)
        return tuple(
            (str(choice.key), str(choice.title))
            for choice in self._raw_choices(attribute, scope, name)
        )

    def _field_and_scope(self, attribute: str) -> tuple[str, str]:
        row = BY_NAME[attribute]
        field = row.field_for(self._libtype)
        scope, _, name = field.rpartition(".")
        return scope or self._libtype, name

    def _cache_key(self, scope: str, name: str) -> str:
        return f"plex_search:choices:{self._ctx.library}:{scope}:{name}"

    def _raw_choices(self, attribute: str, scope: str, name: str):
        key = self._cache_key(scope, name)
        cached = self._ctx.run_cache.get(key, _MISSING)
        if cached is not _MISSING:
            if isinstance(cached, Exception):
                raise cached
            return cached
        try:
            found = list(self._section.listFilterChoices(field=name, libtype=scope))
        # plexapi's own docstring for ``listFilterChoices`` names exactly these
        # two: ``NotFound`` for an unknown filter field, ``BadRequest`` for an
        # invalid one. Narrower than ``except Exception`` on purpose (Task 4
        # review, Minor 3) -- a blanket catch here does not just log a
        # failure, it MEMOISES one, for the rest of the pass, as a fact about
        # the LIBRARY ("Plex has no 'genre' filter..."). A ``TypeError`` from
        # this module's own code is not that fact, and telling the operator it
        # is would send them looking in the wrong place for the rest of the
        # run.
        except (NotFound, BadRequest) as error:
            failure = PlexSearchUnavailable(
                f"Plex has no {attribute!r} filter for this library "
                f"({type(error).__name__}), so its values cannot be resolved"
            )
            self._ctx.run_cache[key] = failure
            raise failure from None
        # A second, DIFFERENT kind of Plex-originating failure -- not "this
        # filter does not exist" but "Plex did not answer at all". ``query()``
        # hands the request straight to a bare ``requests`` call
        # (``self._session.get``), so a dropped connection or a timeout is a
        # ``requests.RequestException``, not a ``PlexApiException``, and
        # reaches here unwrapped; a malformed response or an auth failure is
        # ``PlexApiException`` itself, above ``NotFound``/``BadRequest`` in
        # its hierarchy. Caught here rather than folded into the clause above
        # because "Plex has no such filter" would be a FALSE claim about the
        # library for either one -- class-name-only, like the other Plex
        # exception this module wraps, because either can carry a tokenised
        # URL in its own message (Task 4 review, Fix-round Carry 1: the
        # narrowing above, alone, silently dropped this wrap and let that
        # message reach the engine's logger intact).
        except (PlexApiException, requests.RequestException) as error:
            failure = PlexSearchUnavailable(
                f"Plex would not answer the {attribute!r} filter lookup for "
                f"this library: {type(error).__name__}"
            )
            self._ctx.run_cache[key] = failure
            raise failure from None
        self._ctx.run_cache[key] = found
        return found

    def _choices(self, attribute: str, scope: str, name: str) -> dict[str, str]:
        # Every spelling maps to ``choice.key`` -- the KEY, never
        # ``choice.title`` -- which is what the query has to send Plex. Kometa
        # agrees: ``validate_attribute`` calls its own ``get_search_choices``
        # as ``title=not plex_search`` (modules/builder.py:4412, v2.4.8), so
        # under a plex_search that argument is ``False`` and Kometa's own
        # table is key-keyed too, not title-keyed (Task 4 review, closing
        # item 2).
        table: dict[str, str] = {}
        for choice in self._raw_choices(attribute, scope, name):
            for spelling in (
                str(choice.title), str(choice.key),
                str(choice.title).lower(), str(choice.key).lower(),
            ):
                table[spelling] = str(choice.key)
        return table

    def _language_keys(
        self, attribute: str, scope: str, name: str, value: str
    ) -> tuple[str, ...]:
        """Kometa's ``get_language_search_values`` (modules/plex.py:1321-1344).

        An EXACT value Plex reports (``es-419``, ``spa``) targets only itself;
        anything else expands to every library value that reduces to the same
        base code. Each becomes its own URL term -- and under an ``all:`` block
        those terms are ANDed, which is Kometa's behaviour and is Task 5's
        probe #2: an item is unlikely to carry three Spanish variants at once.
        """
        exact: dict[str, str] = {}
        by_base: dict[str, list[str]] = {}
        for choice in self._raw_choices(attribute, scope, name):
            key = str(choice.key)
            exact[key.lower()] = key
            by_base.setdefault(_base_language_code(key.lower()), []).append(key)
        code = str(value).lower()
        if code != _base_language_code(code) and code in exact:
            return (exact[code],)
        return tuple(by_base.get(code, ()))
