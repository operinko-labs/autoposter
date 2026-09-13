"""What counts as an imperfect asset, expressed as SQL over the stored facts.

The Action Center stores FACTS on ``renders`` (written at
``render/pipeline.py``'s write-back) and derives JUDGEMENTS here, at query
time, against the LIVE config. Roadmap 11a's own risk note is the rule --
"Keep flags factual ('selected language = en, preferred = xx'), not
judgemental ('bad') -- the queue decides what's worth reviewing, data stays
stable". A stored ``language_miss`` boolean would be a lie the moment an
operator re-pointed ``language_order``, and re-backfilling fifteen thousand
rows on every config save is worse than a predicate.

Every predicate is a SQL expression rather than a Python function over fetched
rows, for two reasons that both matter:

 * filtering, counting and paging stay server-side, so the summary endpoint is
   one grouped query rather than a library walk; and
 * the one definition of a flag serves both the ``WHERE`` clause and the
   per-row labels -- the list endpoint selects each predicate a second time as
   a labelled boolean column, so what a row is *shown* as cannot drift from
   what it was *filtered* by.

Predicates may reference ``MediaItem`` as well as ``Render``: every caller
joins the two, and ``artwork.library_language_overrides`` (roadmap row 38)
makes the language preference a property of the library as well as the art
kind.

Nothing here writes anything. That is the property
``tests/test_action_flags.py`` pins twice over.
"""
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Text, and_, case, cast, func, literal, or_

from autoposter.config.schema import Config
from autoposter.db.models import MediaItem, Render
from autoposter.providers.ladder import UNRANKED

# The four values `renders.art_kind` holds, and the four
# `artwork.library_language_overrides` accepts. Spelled out rather than
# imported from render/pipeline.py's ART_KINDS_FOR: importing that module
# pulls plexapi, httpx and the badge compositor into every request that counts
# a flag. The agreement is pinned in tests/test_action_flags.py rather than
# assumed.
ART_KINDS = ("poster", "season_poster", "background", "title_card")


@dataclass(frozen=True)
class Flag:
    """One reason a render row might want an operator's attention."""

    code: str
    label: str
    #: One sentence the page shows beside the chip. Says what the fact is, not
    #: how bad it is.
    description: str
    #: Whether this flag is part of the queue's DEFAULT population. Off means
    #: the operator has to ask for it by name -- see `unknown_provenance`,
    #: whose population may be the whole adopted library.
    default_on: bool
    #: Whether this flag can fire on rows written before the Action Center
    #: shipped. False for the four that need the new bookkeeping, and the
    #: reason `unscored` exists at all.
    instant: bool
    predicate: Callable[[Config], ColumnElement[bool]]
    #: The factual sentence shown in the row's own detail cell. Reads only the
    #: row's stored columns, so it needs no config and no second query.
    detail: Callable[[Render], str]


def _language_order(config: Config, library: str | None, art_kind: str) -> list[str]:
    """The preference list this artifact is ranked against, live.

    The same rule as ``render/pipeline.py::language_order_for``, restated here
    rather than imported for the reason ART_KINDS is restated. Two statements
    of one rule drift, so the agreement is pinned by a test rather than left
    to good intentions.
    """
    if library is not None:
        override = config.artwork.library_language_overrides.get(library, {}).get(art_kind)
        if override:
            return override
    return getattr(config.artwork, art_kind).language_order


# --- the predicates ----------------------------------------------------------


def _missing(config: Config) -> ColumnElement[bool]:
    return Render.status == "no_art"


def _skipped(config: Config) -> ColumnElement[bool]:
    return Render.status == "skipped"


def _truncated(config: Config) -> ColumnElement[bool]:
    return Render.status == "truncated"


def _render_failed(config: Config) -> ColumnElement[bool]:
    return Render.status == "failed"


def _show_fallback(config: Config) -> ColumnElement[bool]:
    return Render.source_mode == "show_fallback"


def _plex_generated(config: Config) -> ColumnElement[bool]:
    return Render.source_mode == "plex_generated"


def _upload_failed(config: Config) -> ColumnElement[bool]:
    return Render.upload_status == "failed"


def _unknown_provenance(config: Config) -> ColumnElement[bool]:
    return Render.adopted.is_(True)


def _language_miss(config: Config) -> ColumnElement[bool]:
    """The ladder did not achieve this art kind's first-choice language.

    Derived, never stored. ``language_rank`` records the position achieved
    under the order in force when the row rendered; that is history. An
    operator who re-points ``language_order`` today must see the queue change
    today, with no backfill, so the comparison is made here against
    ``order[0]`` as it stands this request.

    ``language_rank IS NOT NULL`` is the "a ladder actually ran and ranked
    something" gate. It is NULL on a manual override, on a row that produced
    no art, and on every row written before this taxonomy existed -- all of
    which also have a NULL ``selected_language``, which compares unequal to
    every preference. Without the gate the whole pre-existing library would
    read as a language miss the pipeline never made.

    ``Render.textless`` is ``ArtCandidate.is_textless`` (``providers/base.py``),
    which is authoritative over the tag: a provider can (TVDB does, see
    ``providers/tvdb.py``) mark an image textless *and* still carry a language
    tag inherited from the entry. A textless row therefore achieves ``xx``
    here regardless of what tag rode along with it -- coalescing a NULL tag to
    ``xx`` is not enough on its own, because a textless row's tag need not be
    NULL.
    """
    achieved = case(
        (Render.textless.is_(True), literal("xx")),
        else_=func.coalesce(Render.selected_language, literal("xx")),
    )
    terms: list[ColumnElement[bool]] = []
    for art_kind in ART_KINDS:
        overridden_libraries = sorted(
            library
            for library, by_kind in config.artwork.library_language_overrides.items()
            if by_kind.get(art_kind)
        )
        for library in overridden_libraries:
            order = _language_order(config, library, art_kind)
            terms.append(
                and_(
                    Render.art_kind == art_kind,
                    MediaItem.library == library,
                    achieved != literal(order[0]),
                )
            )
        base = _language_order(config, None, art_kind)
        if base:
            clauses = [Render.art_kind == art_kind, achieved != literal(base[0])]
            if overridden_libraries:
                # The libraries above answer for themselves; this clause is
                # every other library, or a row whose library the override
                # does not name would be judged twice under two orders.
                clauses.append(MediaItem.library.notin_(overridden_libraries))
            terms.append(and_(*clauses))
    if not terms:
        return literal(False)
    return and_(Render.language_rank.isnot(None), or_(*terms))


def _provider_downgrade(config: Config) -> ColumnElement[bool]:
    """The winning provider is not the operator's first-choice provider.

    Live, like the language flag: ``provider_rank`` is the position in the
    runtime ladder at render time and is the evidence a row shows; the
    judgement is the comparison against ``config.providers.order[0]`` as it
    stands now, so re-ordering the providers re-shapes the queue with no row
    write.

    ``provider_rank IS NOT NULL`` gates it to rows a provider ladder actually
    chose. It is NULL for a manual override -- ``provider = 'manual'``, which
    no ladder position describes -- and for every pre-taxonomy row.
    """
    order = config.providers.order
    if not order:
        return literal(False)
    return and_(Render.provider_rank.isnot(None), Render.provider != literal(order[0]))


def _textless_miss(config: Config) -> ColumnElement[bool]:
    return Render.textless_fallback.is_(True)


def _logo_fallback(config: Config) -> ColumnElement[bool]:
    return Render.logo_text_fallback.is_(True)


def _unscored(config: Config) -> ColumnElement[bool]:
    """A rendered row this taxonomy has never scored.

    Scoped to ``rendered`` on purpose: a ``no_art`` row is already named by
    `missing`, and calling it unscored as well would count the same row under
    two chips and would make the backfill's population one it can never
    finish -- a row that produces no art never reaches the write-back that
    stamps ``quality_scored_at``.
    """
    return and_(Render.status == "rendered", Render.quality_scored_at.is_(None))


def _min_point_size(config: Config, art_kind: str) -> int | None:
    """This art kind's own auto-fit floor, live, or None if it draws no text.

    `ArtKindConfig.text` is `TextStyle | None` (`config/schema.py:456-462`):
    a kind with no text block has no floor, and no row of that kind can carry
    a `text_point_size` at all -- `render/pipeline.py:1046` only records the
    size when a primary text block was fitted.

    Read, never written. This is the whole of row 235's threshold story: the
    operator's existing `min_point_size` rather than a number this flag
    invents, so nothing new enters `config.artwork` and no render fingerprint
    moves (`config/loader.py:71`, `:188` -- both allow-lists over that
    subtree).
    """
    text = getattr(config.artwork, art_kind).text
    return None if text is None else text.min_point_size


def _near_miss(config: Config) -> ColumnElement[bool]:
    """The title fitted only by dropping to this art kind's own floor.

    Live, like the language and provider flags: raising `min_point_size`
    re-shapes the queue on the next request with no row write and no
    backfill. A `near_miss` boolean on `FitResult` would be a stored verdict
    needing a whole-library re-render to change, which is what this module's
    docstring exists to refuse.

    `<=` rather than `==` so a floor an operator LATER RAISES retroactively
    catches every row that fitted below the new floor. Equality would make
    the flag silent on exactly the rows the raise was meant to surface.

    One term per art kind, OR-ed: text styles have no per-library override
    the way `language_order` does (`config/schema.py:689-695` is
    language-only), so this is strictly simpler than `_language_miss` above
    -- a kind and a number, nothing crossed with library.

    The population cannot overlap `truncated`: a truncated render returns at
    `render/pipeline.py:1471`, before the write-back at `:1503` that fills
    the column, so every truncated row's `text_point_size` is NULL. The
    explicit `IS NOT NULL` gate states that rather than leaning on SQL's
    NULL-is-not-TRUE, and is what keeps every logo poster, verbatim source
    and pre-capture row out of the queue.
    """
    terms: list[ColumnElement[bool]] = []
    for art_kind in ART_KINDS:
        floor = _min_point_size(config, art_kind)
        if floor is None:
            continue
        terms.append(
            and_(Render.art_kind == art_kind, Render.text_point_size <= literal(floor))
        )
    if not terms:
        return literal(False)
    return and_(Render.text_point_size.isnot(None), or_(*terms))


def excluded_library_predicate(config: Config) -> ColumnElement[bool]:
    """Rows whose Plex library this service is configured never to touch.

    Not a flag, and deliberately not in ``FLAGS``: it narrows the POPULATION
    every flag is asked about rather than naming a reason one row wants
    attention. It lives here because this is where the queue's SQL over
    ``MediaItem`` is written (see this module's docstring), and because the
    quality backfill's own queries need the SAME expression -- a second
    hand-written ``NOT IN`` in ``api/action_center.py`` would be one more
    place to forget it, and the counts and the listing would then disagree
    about the same row.

    ``plex.excluded_libraries`` gates the Plex WALK, not the database:
    ``PlexClient._sections`` (``plex/client.py:316-328``) drops those sections
    so nothing new is discovered there, but a row discovered BEFORE the
    exclusion stays in ``media_items`` forever. Its ``process_item`` job does
    not even park -- ``resolve()`` raises ``ItemNotFound`` and
    ``queue/worker.py``'s branch defers it on an unbounded horizon -- so such a
    row is never scored and never reads as ``blocked``, and without this
    predicate it sits in the queue, the chip counts and the backfill's
    denominator permanently. ``plex_prune`` retires the rows themselves; this
    keeps the Action Center's population one an operator can finish in the
    meantime.

    The comparison is EXACT, never case-folded, because every other reader of
    this setting is exact: ``plex/client.py:327`` tests
    ``s.title not in self._excluded`` and ``api/mismatches.py:249`` tests
    ``item.library in excluded_libraries``. A case-insensitive test here would
    hide rows the resolver would still walk, which is worse than showing one
    it will not.

    An empty list is ``literal(True)`` rather than ``NOT IN ()`` -- the
    ``_provider_miss`` precedent above: an empty configuration means the
    predicate has nothing to say, not that every row fails it.
    """
    excluded = config.plex.excluded_libraries if config.plex else []
    if not excluded:
        return literal(True)
    return MediaItem.library.notin_(excluded)


# --- the row details ---------------------------------------------------------


def _plain(text_value: str) -> Callable[[Render], str]:
    return lambda render: text_value


def _missing_detail(render: Render) -> str:
    return render.detail or "no art on any provider"


def _skipped_detail(render: Render) -> str:
    return render.detail or "skipped"


def _truncated_detail(render: Render) -> str:
    return render.detail or "the title does not fit at the minimum point size"


def _upload_detail(render: Render) -> str:
    return f"render {render.status}, upload {render.upload_status}"


def _render_failed_detail(render: Render) -> str:
    return render.detail or "the source was refused"


def _language_detail(render: Render) -> str:
    language = render.selected_language or "untagged"
    if render.language_rank == UNRANKED:
        return f"selected {language}; not in the preference list at all"
    return f"selected {language}; rank {render.language_rank} in the order that rendered it"


def _provider_detail(render: Render) -> str:
    return f"selected {render.provider}; rank {render.provider_rank} in the ladder that ran"


def _near_miss_detail(render: Render) -> str:
    """The fitted size and nothing else -- `Flag.detail` stays unwidened.

    Roadmap row 213's rule: never `render.detail` (free text the pipeline
    resets on every pass) and never a path (a served column is not the place
    for the filesystem). The live floor this size is judged against is a
    config value `Flag.detail: Callable[[Render], str]` cannot reach without
    widening the contract, so the sentence names the one fact this callable
    does have.
    """
    return f"fitted at {render.text_point_size} pt, at or below this kind's floor"


# --- the registry ------------------------------------------------------------

_REGISTRY: tuple[Flag, ...] = (
    Flag(
        code="missing",
        label="No art found",
        description="No provider had artwork of this kind for this item.",
        default_on=True,
        instant=True,
        predicate=_missing,
        detail=_missing_detail,
    ),
    Flag(
        code="skipped",
        label="Skipped",
        description=(
            "The pipeline declined to render this artifact -- the art kind is "
            "disabled, the title matched a skip word, the item cannot be named, "
            "or online fetch is off and there is no local asset. Off by default: "
            "a deliberately disabled art kind would otherwise flood the queue "
            "with every row of that kind."
        ),
        default_on=False,
        instant=True,
        predicate=_skipped,
        detail=_skipped_detail,
    ),
    Flag(
        code="truncated",
        label="Text does not fit",
        description=(
            "The title would not fit its box at the minimum point size, so no "
            "file was written at all."
        ),
        default_on=True,
        instant=True,
        predicate=_truncated,
        detail=_truncated_detail,
    ),
    Flag(
        code="render_failed",
        label="Render refused",
        description=(
            "The last render attempt refused this artifact's source and wrote no "
            "file; the stored reason is in the detail below. On by default: the "
            "asset has no current art, which is a true defect, not noise -- a "
            "rerender retries the same source and recovers once it is fixed."
        ),
        default_on=True,
        instant=True,
        predicate=_render_failed,
        detail=_render_failed_detail,
    ),
    Flag(
        code="show_fallback",
        label="Styled from the show poster",
        description=(
            "No provider had art for this season, so the show's own poster was "
            "styled with the season text instead."
        ),
        default_on=True,
        instant=True,
        predicate=_show_fallback,
        detail=_plain("season art came from the show's poster"),
    ),
    Flag(
        code="plex_generated",
        label="Plex's own preview frame",
        description=(
            "No provider had a title card for this episode, so the frame Plex "
            "generated from the media file was used as the base instead. On "
            "by default: every row this fires on already fires missing "
            "today, so the queue's population does not grow, only its "
            "labelling improves."
        ),
        default_on=True,
        instant=True,
        predicate=_plex_generated,
        detail=_plain("no title card on any provider; Plex's generated frame was used"),
    ),
    Flag(
        code="upload_failed",
        label="Upload failed",
        description="The artwork was rendered but never reached Plex.",
        default_on=True,
        instant=True,
        predicate=_upload_failed,
        detail=_upload_detail,
    ),
    Flag(
        code="language_miss",
        label="Not the preferred language",
        description=(
            "The language the ladder achieved is not this art kind's first "
            "choice in the configuration as it stands now."
        ),
        default_on=True,
        instant=False,
        predicate=_language_miss,
        detail=_language_detail,
    ),
    Flag(
        code="provider_downgrade",
        label="Not the preferred provider",
        description=(
            "The provider that won is not the first in the configured order as "
            "it stands now."
        ),
        default_on=True,
        instant=False,
        predicate=_provider_downgrade,
        detail=_provider_detail,
    ),
    Flag(
        code="textless_miss",
        label="Text-bearing art taken",
        description=(
            "Textless art was preferred, no provider had any, and a "
            "text-bearing image was taken rather than nothing."
        ),
        default_on=True,
        instant=False,
        predicate=_textless_miss,
        detail=_plain("no textless art on any provider; a text-bearing image was used"),
    ),
    Flag(
        code="logo_fallback",
        label="Title text instead of a logo",
        description=(
            "This poster wanted a clearlogo, no provider had one, and the title "
            "text was drawn in its place."
        ),
        default_on=True,
        instant=False,
        predicate=_logo_fallback,
        detail=_plain("no clearlogo on any provider; the title text was drawn instead"),
    ),
    Flag(
        code="unknown_provenance",
        label="Adopted, provenance unknown",
        description=(
            "Artwork that was already on disk when this service took over. No "
            "provider, no source URL, no way to say what it is. Off by default: "
            "on a library that was adopted wholesale this is every row."
        ),
        default_on=False,
        instant=True,
        predicate=_unknown_provenance,
        detail=_plain("found on disk at adoption; no provider and no source URL"),
    ),
    Flag(
        code="unscored",
        label="Not yet scored",
        description=(
            "Rendered before the quality facts existed, so four of the flags "
            "cannot be evaluated for it until it re-renders."
        ),
        default_on=False,
        instant=True,
        predicate=_unscored,
        detail=_plain("rendered before the quality taxonomy; re-render to score it"),
    ),
    Flag(
        code="near_miss",
        label="Fitted only at the minimum size",
        description=(
            "The title fitted its box only by shrinking to this art kind's "
            "configured minimum point size. The file was written, unlike "
            "'Text does not fit', but there was no room left. Off by "
            "default: the size of this population depends entirely on the "
            "configured box and floor, and a library of long titles could "
            "put thousands of rows on it."
        ),
        default_on=False,
        instant=False,
        predicate=_near_miss,
        detail=_near_miss_detail,
    ),
)

#: Insertion-ordered, and that order is the order the page's chips appear in.
FLAGS: dict[str, Flag] = {flag.code: flag for flag in _REGISTRY}


def predicate_for(code: str, config: Config) -> ColumnElement[bool]:
    """The SQL this flag is, against the live config.

    Raises ``KeyError`` for an unknown code; the API turns that into a 400
    naming the codes it does have, rather than silently answering with an
    unfiltered queue.
    """
    return FLAGS[code].predicate(config)


def default_predicate(config: Config) -> ColumnElement[bool]:
    """The queue's default population: any default-on flag firing."""
    return or_(*[flag.predicate(config) for flag in FLAGS.values() if flag.default_on])


def detail_for(code: str, render: Render) -> str:
    """The factual sentence behind this flag, for this row."""
    return FLAGS[code].detail(render)


# The columns a dismissal is a dismissal *of*. `detail` is deliberately absent:
# it is free text the pipeline resets to None on every successful render, so
# folding it in would move the hash on every pass and resurrect every
# dismissal -- exactly the bug 11b names as its own risk.
#
# `quality_scored_at` itself is deliberately absent too, for the opposite
# reason: `unscored` rests on it, so leaving it out entirely would let a
# dismissal made while a row was unscored survive the very re-render that
# scores it. But the raw timestamp is not what is hashed -- it moves on
# every re-render even when scored-ness does not, which would resurrect that
# same dismissal on every later pass. What is hashed is scored-ness itself.
_EVIDENCE_COLUMNS = (
    Render.status,
    Render.source_mode,
    Render.upload_status,
    Render.adopted,
    Render.provider,
    Render.selected_language,
    Render.textless,
    Render.language_rank,
    Render.provider_rank,
    Render.textless_fallback,
    Render.logo_text_fallback,
    Render.quality_scored_at.isnot(None),
    # Roadmap row 235. `near_miss` rests on it, so a dismissal made while a
    # title sat on the floor must not survive the re-render that fitted it
    # comfortably -- the exact failure this tuple's comment above is written
    # against. Adding it moved `evidence_expression()` for every row and
    # returned every existing dismissal to the queue ONCE, which was the
    # accepted cost of the row.
    Render.text_point_size,
)


def evidence_expression() -> ColumnElement[str]:
    """This row's facts, hashed, as a SQL expression over ``renders``.

    In SQL rather than in Python so the dismissal join is a plain equality:
    that is what keeps filtering, paging and `total` server-side. Computing the
    hash in Python would mean fetching every candidate row to decide which ones
    to hide, which makes `total` a lie and pages arbitrarily short.

    ``sha256`` is a PostgreSQL built-in (11+) and needs no extension.
    ``coalesce`` to a sentinel rather than relying on ``concat_ws``, which
    skips NULLs -- without it ``(NULL, 'en')`` and ``('en', NULL)`` would hash
    identically and a fact moving between two columns would leave a dismissal
    standing.
    """
    parts = [
        func.coalesce(cast(column, Text), literal("~")) for column in _EVIDENCE_COLUMNS
    ]
    joined = func.concat_ws(literal("|"), *parts)
    # Not `cast(joined, BYTEA)`: Postgres has no text->bytea cast, so that
    # resolves to the I/O-conversion cast and runs `byteain`, which *parses*
    # the text as a bytea literal rather than encoding it -- a `\` in a
    # provider-supplied fact (e.g. `selected_language`) would raise
    # "invalid input syntax for type bytea" and take the whole query down.
    # `convert_to` just encodes bytes; it does not parse.
    encoded = func.convert_to(joined, literal("UTF8"))
    return func.encode(func.sha256(encoded), literal("hex"))
