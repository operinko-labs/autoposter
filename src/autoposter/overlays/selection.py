"""The overlay engine's SELECTION half: which definitions apply to this item.

Until this module, `badges/compose.py::_draw_definitions` drew EVERY
configured definition on EVERY badged item -- `OverlayDefinition` had no
condition, filter or applicability field at all. Every family roadmap row 100
names is a *conditional* overlay (Kometa expresses each as a `plex_search:`
or `filters:` block), so none of them was expressible. That is the gap this
module closes, and it closes it by REUSING a filter engine rather than
writing one.

**The grammar is `collections/filters.py`'s, imported, not re-implemented**
(adjudication A3). That module's parse/evaluate pair was proved against
Kometa 2.4.8's own filter code -- 120 items, member list against member list,
the `SETTLED-BY-ORACLE` verdicts on its rows -- and a second filter dialect
would be exactly the "same name, different filter" class its own docstring
rules out. `parse_condition` below is a thin narrowing in front of
`parse_filters`, not a parser.

**The VIEW is ours, and deliberately not `filter_values.PlexItemView`.** That
view is bound by one hard rule -- "reading a filter value never costs a Plex
request" -- because the collections engine walks the library once with
`section.all()` and holds PARTIAL plexapi objects, whose attribute access
silently reloads. The badge path is a richer per-item context: by the time
this view is built, `render/pipeline.py::apply_badges` has already called
`badges/values.py::media_info_from_plex`, which reloads the item and walks
`part.streams`, and it is handed `facts` and `plex_item` directly. So the
collections table's per-attribute source tiers and `kinds` restrictions are
LISTING constraints, and they do not bind here (the recon's adjudication
A3b): a show simply has no `media`, which the tag missing-value rule already
answers correctly, rather than needing a `kinds` column to forbid it. **T1
review finding L-2, met here (sub-phase C2a):** that "do not bind" claim is
about the `kinds` COLUMN specifically -- `resolution`'s `kinds=("movie",)` is
the one restriction this view has ever genuinely relaxed, because
`parse_filters` never consults `kinds` at all (verified by reading the whole
parse path). It says nothing about which attributes have an ACCESSOR:
`duplicate` and `network` have none on this view and are refused by
`OVERLAY_ATTRIBUTES` membership, a wholly different mechanism, and stay
refused regardless of `kinds`. `versions` was the first attribute genuinely
newly unlocked since C1 -- a real `filters.py` row, a real accessor, both
dialects proven -- and sub-phase C2b added three more the same way: `aspect`
(its own row, its own shared accessor, both dialects proven) and the two
stream-language rows, which were already real `filters.py` rows and gained
an accessor HERE without gaining one on the collections view, because the
two views read them from different places.
`plex_item` is still very likely PARTIAL, though -- the badge path never
copies it -- so this view's own accessors read it the same load-bearing way
`filter_values.py`'s do (`object.__getattribute__`, imported directly rather
than re-implemented), even though the CLASS itself is not `PlexItemView`.

**The vocabulary is narrower than the collections block's, on purpose.**
`OVERLAY_ATTRIBUTES` is what THIS view can actually supply. An attribute that
parses as a collections filter but has no accessor here is refused at config
load naming what is available -- never answered as None, because None means
"this item has no value" and the table turns that into a defined match
result. A family that silently matches nothing is indistinguishable from a
family that is off.
"""
import json
from functools import lru_cache

from autoposter.collections.filter_values import (
    _aspect,
    _listing_value,
    _resolutions,
    _versions,
)
from autoposter.collections.filters import (
    FilterGroup,
    evaluate,
    parse_filters,
    predicates,
)

__all__ = [
    "OVERLAY_ATTRIBUTES",
    "AttributeNotOnItem",
    "OverlayItemView",
    "compiled_condition",
    "parse_condition",
    "select",
]

# The attributes this view supplies, in `collections/filters.py`'s own
# spelling -- the key `evaluate` reads.
#
# - `content_rating`: PLEX's certification string (adjudication A5), which is
#   what the six content-rating regionals match. Deliberately NOT
#   `ItemFacts.content_rating`, which is MDBList's Common Sense AGE rating --
#   a different value space. Kometa's alias buckets carry Plex agent
#   spellings (`gb/U`, `no/A`, `TV-Y`) Common Sense never emits, and the
#   `content_rating` row in `collections/filters.py` says exactly this.
# - `resolution`: every version's `videoResolution`, which is what
#   `direct_play` regexes.
# - `versions` (sub-phase C2a): how many `<Media>` versions the item
#   carries -- adjudication A14's row, shared verbatim with
#   `filter_values.PlexItemView` via the same `_versions` function object,
#   so Global Constraint 9's agreement is structural rather than merely
#   tested.
# - `aspect` (sub-phase C2b): the `<Media aspectRatio=...>` float, which is
#   what the eight `aspect` bands compare. Adjudication A11's row, and
#   shared the same structural way `versions` is -- one `_aspect` function
#   object, imported rather than copied. Its multi-version rule is A-2's:
#   the same whole-list walk `resolution` makes, narrowed to the first
#   version carrying the attrib, because a `float` cannot answer a tuple.
# - `audio_language`, `subtitle_language` (sub-phase C2b): the item's
#   stream languages as Kometa itself reads them -- EVERY stream, across
#   every `<Media>`, NOT deduplicated (`modules/plex.py:2915-2922`) --
#   which is what `language_count`'s Dual/Multi bands count with Kometa's
#   own `.count_*` modifiers. These two are the FIRST attributes this view
#   answers from the `MediaInfo` rather than from `plex_item`, and
#   deliberately so: `media_info_from_plex` already walked `part.streams`
#   for this item before the view was built, so the values are in hand,
#   while `filter_values.PlexItemView` reads the same two from the
#   collections engine's batched metadata enrichment -- a different read,
#   which no shared function object could span. Their agreement is therefore
#   pinned at the VERDICT level (finding L-1), not on the raw values: this
#   view answers `()` for an item with no streams where `PlexItemView`
#   answers `None`, and the two are identical under every operator (the tag
#   missing-value rule for the value operators, and the `.count_*` reduction
#   to zero above it for the four count ones).
#
#   ONE DIVERGENCE, RECORDED RATHER THAN HIDDEN: on an item with REPEATED
#   stream languages the two views disagree under `.count_*`, because
#   `plex/client.py::_stream_languages` ends in `_uniq` while this view
#   carries Kometa's undeduplicated list. Kometa's answer is this view's; the
#   collections side's dedupe predates sub-phase C2b by two phases and lives
#   in a module C2b does not touch. It affects only the four operators C2b
#   introduces, it is pinned in `tests/test_overlay_selection.py` so it
#   cannot drift unnoticed, and closing it is a separate adjudication.
#
# - `tmdb_status`, `last_episode_aired` (sub-phase C2c): the show's TMDb
#   airing status as Kometa's own token, and the date its most recent
#   episode aired. **THE FIRST TWO ATTRIBUTES THIS VIEW ANSWERS FROM
#   `facts`** -- every one above comes from `plex_item` or from the
#   `MediaInfo`. `apply_badges` already loads the persisted `ItemFacts` row
#   (or a bare `GatheredFacts()`) and hands it to this constructor, which is
#   why a facts-backed attribute costs no `render/pipeline.py` edit at all.
#   Their `collections/filters.py` rows sit on the new `facts` source tier,
#   which is REFUSAL-ONLY for a collection (roadmap row 156's fence, named
#   rather than opened) and readable only here.
#
#   NO AGREEMENT PIN IS OWED, and that is stated rather than left unsaid:
#   `filter_values.PlexItemView` supplies NEITHER -- its accessors are
#   derived from the `listing` and `tier2-batched` tiers, so a `facts` row
#   reaches none of them and it refuses by naming the tier. What is pinned
#   instead is that refusal (`tests/test_collection_filter_values.py`).
#
#   The reads use `getattr` WITH A DEFAULT rather than attribute access, and
#   that is load-bearing: `facts` arrives here as an `ItemFacts` row, as a
#   bare `GatheredFacts()`, as None (several fingerprint pins build this view
#   by hand), and as small stand-in objects in tests that carry only the
#   three rating fields. Only the first two carry these attributes at all,
#   and a plain read would raise `AttributeError` out of `evaluate`,
#   mid-badge, for every item -- the same defensive shape `apply_badges`
#   already uses for `critic_rating`/`content_rating`.
#
# Later slices append; each addition owes a source note here and an agreement
# pin against `filter_values.PlexItemView` if that view supplies it too.
OVERLAY_ATTRIBUTES: tuple[str, ...] = (
    "content_rating", "resolution", "versions",
    "aspect", "audio_language", "subtitle_language",
    "tmdb_status", "last_episode_aired",
)


class AttributeNotOnItem(LookupError):
    """Asked for a value this view cannot supply.

    Raised, never returned as missing -- `parse_condition` refuses these at
    config load, so reaching this at render time means a condition got past
    that check.
    """


class OverlayItemView:
    """`filters.ItemView` over the badge pipeline's own per-item context.

    `media` is a `badges.values.MediaInfo`, `facts` an `ItemFacts`-shaped
    object (or None), `plex_item` the live plexapi item `apply_badges` was
    handed. All three are already in hand at the call site; this view makes
    no request of its own -- and reads `plex_item` the same load-bearing way
    `filter_values.py` does (see `get()` below) so that stays true even for
    an unrated movie whose `.media` is populated but whose `.contentRating`
    is unset.
    """

    def __init__(self, media, facts=None, plex_item=None) -> None:
        self._media = media
        self._facts = facts
        self._plex_item = plex_item

    def get(self, attribute: str, /) -> object | None:
        """Reads `plex_item` through `filter_values.py`'s own accessors, not
        a plain `getattr`, and that is load-bearing, not a style choice.

        `plex_item` is very likely a `PlexPartialObject`: `apply_badges`
        hands this view the SAME live item it uploads to, not a copy.
        `media_info_from_plex` reloading it beforehand only guarantees
        `.media` and its streams are populated -- it does nothing for
        `.contentRating`, and a plain `getattr` on an UNSET attribute of a
        partial object trips `PlexPartialObject.__getattribute__`'s reload
        branch: one synchronous blocking `requests` GET, inline, on the
        event loop (`select()` is called directly in `apply_badges`, not
        through `asyncio.to_thread`). `filter_values._listing_value` and
        `filter_values._resolutions` exist to read a partial object WITHOUT
        that risk (`object.__getattribute__`, which is exactly what
        `PlexPartialObject.__getattribute__` itself calls first, minus the
        reload branch that follows it) -- reusing them here closes that
        hazard and, for `resolution`, closes the duplication a straight
        `filter_values` import would otherwise leave (R2): one accessor,
        not two copies that can drift.
        """
        if attribute == "content_rating":
            return _listing_value(self._plex_item, "contentRating") or None
        if attribute == "resolution":
            return _resolutions(self._plex_item)
        if attribute == "versions":
            return _versions(self._plex_item)
        if attribute == "aspect":
            return _aspect(self._plex_item)
        # The two stream-language rows read the MediaInfo, not `plex_item`:
        # `media_info_from_plex` walked `part.streams` for this item before
        # this view existed, so the answer is already in hand and reading
        # `plex_item` again would be a second walk of the same data. `media`
        # is None only for a caller that built this view by hand (several
        # fingerprint pins do); answering None there is correct -- it means
        # "no value", which the tag missing-value rule handles.
        #
        # Handed on as the bare tuple, `()` included, rather than
        # `PlexItemView`'s `or None`: the two are verdict-identical
        # (`filters._is_missing` reads an empty sequence as missing for a
        # tag, and `.count_*` reduces both to zero above that rule), and
        # this view's contract is to hand on what `MediaInfo` carries
        # without reshaping it. See finding L-1 and the verdict-level
        # agreement pins.
        #
        # The `*_stream_languages` fields, NOT `audio_languages`: the former
        # are Kometa's own filter value (every stream, every `<Media>`, no
        # dedupe -- `modules/plex.py:2915-2922`), the latter is the distinct
        # set the flag badge draws from. Reading the wrong one here would
        # make `audio_language.count_gte: 2` miss a film with two English
        # tracks, which upstream badges as Dual.
        if attribute == "audio_language":
            return (
                self._media.audio_stream_languages
                if self._media is not None else None
            )
        if attribute == "subtitle_language":
            return (
                self._media.subtitle_stream_languages
                if self._media is not None else None
            )
        # The two facts-backed rows (sub-phase C2c). `getattr` with a default,
        # never attribute access -- see the source note on
        # `OVERLAY_ATTRIBUTES` above for the four shapes `facts` arrives in.
        #
        # `or None` on the status, matching `content_rating` above: an empty
        # or whitespace string is not a status, and the tag missing-value
        # rule must exclude the item rather than compare `""` against a band.
        # The date needs no such coercion -- a `date` is either present or
        # None, and `_MISSING_ALWAYS_EXCLUDES` handles the None.
        if attribute == "tmdb_status":
            value = getattr(self._facts, "tmdb_status", None)
            return value.strip() or None if isinstance(value, str) else None
        if attribute == "last_episode_aired":
            return getattr(self._facts, "last_episode_aired", None)
        raise AttributeNotOnItem(
            f"{attribute!r} is not an attribute an overlay condition can "
            "read on this service. Available: " + ", ".join(OVERLAY_ATTRIBUTES)
        )


_SEARCH_ONLY_MARKER = "Move it into the plex_search builder's"


def parse_condition(raw: object, *, field: str = "condition") -> FilterGroup:
    """Parse one definition's `condition:` block, or refuse naming the key.

    Two layers, in this order, because the messages are different and both
    are useful:

    1. `collections.filters.parse_filters` -- the shared grammar. It refuses
       an attribute no filter vocabulary has, an operator the attribute's
       type does not carry, an empty block, a `.and` suffix, a plex_search-
       only modifier, and a value that will not coerce. Those refusals are
       already written, already tested and already cite Kometa; none of them
       is re-implemented here.
    2. the overlay NARROWING -- an attribute that is a legitimate collections
       filter but that `OverlayItemView` cannot supply. That refusal has to
       be ours, because `filters.py` has no idea what this view reads.

    A third, narrower case rides inside layer 1 (T1 review finding L-4, met
    here): a SEARCH-ONLY attribute (`duplicate`, `unplayed`, ...) is refused
    by `filters.py` with "Move it into the plex_search builder's `params`" --
    correct advice for a COLLECTION, meaningless for an overlay `condition:`
    block, which has no plex_search builder to move anything into. The
    attribute is unavailable to an overlay either way; only the remedy
    sentence is wrong-audience, and `filters.py` may not be edited to fix it
    (Global Constraint 2), so the reword happens here, by catching that one
    specific message rather than re-implementing the check.

    `field` is the dotted path the refusal names, so an operator with twenty
    definitions can find the one that is wrong.
    """
    try:
        group = parse_filters(raw, field=field)
    except ValueError as exc:
        message = str(exc)
        if _SEARCH_ONLY_MARKER in message:
            name = message.split("'")[1] if "'" in message else "?"
            raise ValueError(
                f"{field}: {name!r} is a Plex smart-search-only attribute "
                "(Kometa has no `filters:` equivalent for it either), and an "
                "overlay condition cannot use it at all -- there is no "
                "builder for it to be moved into here. Available: "
                + ", ".join(OVERLAY_ATTRIBUTES)
            ) from None
        raise
    for predicate in predicates(group):
        if predicate.attribute.name not in OVERLAY_ATTRIBUTES:
            raise ValueError(
                f"{predicate.field}: {predicate.attribute.name!r} is a filter "
                "attribute this service can evaluate for a COLLECTION but not "
                "for an overlay -- an overlay condition is answered from what "
                "the badge pass already holds for the item, and there is no "
                "accessor for it there. An overlay condition can name: "
                + ", ".join(OVERLAY_ATTRIBUTES)
            )
    return group


@lru_cache(maxsize=512)
def _compiled(payload: str) -> FilterGroup:
    """Parse once per distinct condition, not once per item.

    Keyed on the condition's canonical JSON rather than on the definition,
    so two definitions writing the same condition share one tree and a
    reordered mapping is the same key. Bounded: an operator with more than
    512 distinct conditions pays a re-parse for the tail, which is a small
    cost with a hard ceiling instead of an unbounded one.

    Calls `parse_condition` with its default `field="condition"` -- a
    refusal raised THROUGH this cache (rather than at config load, where
    `OverlayDefinition._validate` already calls `parse_condition` with the
    same default and would have caught it first) therefore always names the
    literal field `"condition"`, never a per-overlay dotted path. Reaching
    this refusal at all means a condition got past load-time validation,
    which should not happen; it is not the path `parse_condition`'s `field`
    parameter is documented for.
    """
    return parse_condition(json.loads(payload))


def compiled_condition(definition) -> FilterGroup | None:
    """This definition's parsed condition, or None when it has none.

    `definition` is an `overlays.schema.OverlayDefinition`; typed loosely so
    this module does not import the schema that imports it back at validation
    time.

    **`default=str` is finding L-7, and it lands with sub-phase C2c because
    C2c brings the first date-typed attribute into `OVERLAY_ATTRIBUTES` --
    but NOT for the reason the roadmap gave.** The shipped `status` family
    writes `{"last_episode_aired": 14}`, an int, which dumps fine. The
    reachable break is an OPERATOR writing the absolute form,
    `last_episode_aired.after: 2026-01-01`, which YAML parses to a
    `datetime.date`; `json.dumps` refuses that with `TypeError`, raised from
    HERE, inside `apply_badges`, at RENDER time -- config validation would
    have passed, because `OverlayDefinition._validate` calls
    `parse_condition` on the raw mapping and `filters._as_date` accepts a
    `date` object directly.

    The keyword round-trips rather than merely silencing: `str(date)` is ISO,
    which `_as_date` also accepts, so the cache key is stable and the
    re-parsed predicate is the same one. It applies to any unserialisable
    value, not only dates, which is the right scope for a key-building dump.
    """
    if definition.condition is None:
        return None
    return _compiled(json.dumps(definition.condition, sort_keys=True, default=str))


def select(definitions, view) -> tuple[list, list[tuple[str, bool]]]:
    """Which definitions apply to this item, and the outcomes to fingerprint.

    Returns `(matched, outcomes)`:

    - `matched` is the subset handed on to image resolution and `compose` --
      in configured order, so suppression and group/weight resolution
      downstream see exactly what an operator wrote, minus what this item
      does not match. A definition with no condition is in it always, which
      is the pre-seam behaviour preserved exactly.
    - `outcomes` is `(name, matched)` for every definition that CARRIES a
      condition, and nothing else. That asymmetry is the whole storm guard:
      a config of unconditioned definitions produces an EMPTY outcomes list,
      which `badge_fingerprint` folds in under a non-empty guard, so every
      already-badged item under an existing row-97 config keeps its digest to
      the bit. It is the same shape, and the same reasoning, as the
      `if definitions:` guard that already sits beside it.

    Order is configured order, not sorted: definition order already decides
    group tie-breaks and draw order, and keying on the name alone would
    collide for two definitions sharing one.
    """
    matched: list = []
    outcomes: list[tuple[str, bool]] = []
    for definition in definitions:
        condition = compiled_condition(definition)
        if condition is None:
            matched.append(definition)
            continue
        passed = evaluate(condition, view)
        outcomes.append((definition.name, passed))
        if passed:
            matched.append(definition)
    return matched, outcomes
