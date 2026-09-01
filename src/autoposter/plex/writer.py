import asyncio
import logging

from autoposter.facts.gather import format_audience, format_critic
from autoposter.facts.models import GatheredFacts

logger = logging.getLogger(__name__)

# plexapi raises AttributeError for fields a type does not carry: a season has
# no contentRating, studio, genres or originallyAvailableAt; an episode has no
# genres or studio.
#
# Finding 9: this phase's gather_facts never populates season's ratings or
# episode's content_rating/originally_available (a season returns no facts of
# its own at all, and TMDB's episode endpoint carries neither field) — those
# four entries are unreachable today. Left in deliberately, permissive for a
# later phase that fills them in, rather than removed: shrinking this set
# would mean re-adding entries later just to catch up with gather_facts,
# instead of gather_facts simply growing into a map that already allows it.
WRITABLE_BY_KIND: dict[str, set[str]] = {
    "movie": {
        "critic_rating", "audience_rating", "content_rating",
        "genres", "studio", "originally_available",
        # Roadmap rows 32 and 33a. ``original_title`` is movies only: Plex
        # carries originalTitle for movies and not for shows or episodes
        # (plex/client.py:59-61, row 44's finding).
        "user_rating", "original_title",
    },
    "show": {
        "critic_rating", "audience_rating", "content_rating",
        "genres", "studio", "originally_available",
        "user_rating",
    },
    "season": {"critic_rating", "audience_rating", "user_rating"},
    "episode": {"critic_rating", "audience_rating", "content_rating",
                "originally_available", "user_rating"},
}


def exemption_reason(
    operations, rating_key: str | None, imdb_id: str | None, labels
) -> str | None:
    """Why this item's metadata must not be written, or ``None`` to write it.

    Roadmap row 35, narrow reading: this gates the Plex WRITE only. The
    caller still gathers and persists the item's facts, because the badge
    stage reads the persisted row rather than the write, and an exempt item
    must keep its badges rather than silently lose them.

    A reason string rather than a bool: the operator's next question when an
    item is not being written is always "which setting did that", and a bare
    ``True`` cannot answer it.

    ``labels`` is whatever the plexapi object carries -- a list of tag objects
    on a real item, plain strings in a test -- so each entry is read through
    ``getattr(.., "tag", entry)``. Comparison is case-folded on both sides:
    Plex canonicalises label case, so an exact compare would silently miss the
    opt-out label an operator actually applied.
    """
    if rating_key is not None and rating_key in operations.ignore_ids:
        return f"operations.ignore_ids matched rating key {rating_key!r}"
    if imdb_id is not None and imdb_id in operations.ignore_imdb_ids:
        return f"operations.ignore_imdb_ids matched IMDb id {imdb_id!r}"
    wanted = {name.casefold() for name in operations.ignore_labels}
    for entry in labels or []:
        name = getattr(entry, "tag", entry)
        if isinstance(name, str) and name.casefold() in wanted:
            return f"operations.ignore_labels matched label {name!r}"
    return None


def _one_decimal(value: float) -> float:
    """Round to one decimal place, matching the tool being replaced.

    ``f"{value:.1f}"`` (the same string-based rounding ``format_critic``/
    ``format_audience`` already use for the comparison) avoids binary
    floating point rounding surprises like ``round(2.675, 1) == 2.67``.
    """
    return float(f"{float(value):.1f}")


def _current_genres(item) -> list[str]:
    return [g.tag for g in getattr(item, "genres", []) or []]


def _genre_plan(current: list[str], target: list[str]) -> dict[str, object]:
    """Report-only additions/removals that set the genre list to exactly
    `target`, using plexapi's documented `addGenre`/`removeGenre` mixin
    methods rather than a hand-built tag wire format.

    These keys are *not* valid `item.edit()` kwargs -- `apply_facts` reads
    them to decide what to pass to `addGenre`/`removeGenre` and strips them
    before building the plain-field edit call.

    Why not just call `item.addGenre(missing)`: verified offline against the
    real plexapi `Movie`/`GenreMixin` classes (constructing a `Movie` from a
    minimal XML element and running `batchEdits()`, which performs no
    network I/O), `addGenre`'s "merge with existing genres" reads the item's
    *current* `genres` property live off its original data -- unaffected by
    a `removeGenre` queued earlier in the same batch. Passed only the
    genuinely new tags, it still silently re-emits every currently-held
    genre as an explicit add, which either duplicates genres that needed no
    change (plexapi's indexed tag write does not dedupe) or, worse, directly
    contradicts a `removeGenre` call for the same tag in the same request.
    `apply_facts` avoids this by resetting the item's cached `genres` to
    `[]` immediately before calling `addGenre`, so its merge has nothing
    stale to reintroduce and only the genuinely new tags are queued -- the
    same payload shape already proven safe on the production server.
    """
    current_set = set(current)
    target_set = set(target)
    additions = [g for g in target if g not in current_set]
    removals = [g for g in current if g not in target_set]

    if not additions and not removals:
        return {}

    plan: dict[str, object] = {}
    if additions:
        plan["genres.added"] = additions
    if removals:
        plan["genres.removed"] = removals
    # Locked so Plex's own agent does not revert a value this tool owns.
    plan["genres.locked"] = 1
    return plan


def map_value(mapping: dict[str, str], value: str | None) -> str | None:
    """``value`` rewritten by ``mapping``, or unchanged (roadmap row 34).

    Exact-key and case-sensitive. The label case-folding rule this project
    holds elsewhere is about Plex LABELS, which Plex itself canonicalises;
    this is an operator's own table of genre and rating strings, and folding it
    would silently merge two keys they wrote separately.
    """
    if not mapping or value is None:
        return value
    return mapping.get(value, value)


def map_values(mapping: dict[str, str], values: list[str]) -> list[str]:
    """``values`` rewritten by ``mapping``, in order, each result once.

    Deduplicated because two source genres commonly map onto one target and
    the genre diff below compares sorted sets -- a duplicate would make an
    already-correct item look like it needed a write.
    """
    if not mapping:
        return values
    mapped: list[str] = []
    for value in values:
        rewritten = mapping.get(value, value)
        if rewritten not in mapped:
            mapped.append(rewritten)
    return mapped


def plan_edits(item, facts: GatheredFacts, operations=None) -> dict[str, object]:
    """Field/value pairs that differ from what Plex already holds.

    Ratings compare on their *formatted* value, because that is what a viewer
    sees: 8.65 and 8.6 both render "86%", so rewriting one as the other would
    churn Plex for no visible gain.

    ``operations`` is the ``OperationsConfig``; ``None`` -- what a direct
    caller and most tests pass -- means no mapper and no verb, which is
    byte-identical to the pre-row-34 behaviour.
    """
    writable = WRITABLE_BY_KIND.get(getattr(item, "type", "movie"), set())
    edits: dict[str, object] = {}

    # Row 34: normalise once, here, so the mapped value is what the diff below
    # compares AND what is written.
    genre_mapper = getattr(operations, "genre_mapper", None) or {}
    content_rating_mapper = getattr(operations, "content_rating_mapper", None) or {}
    content_rating = map_value(content_rating_mapper, facts.content_rating)
    genres = map_values(genre_mapper, facts.genres)

    def put(field: str, value: object) -> None:
        edits[f"{field}.value"] = value
        # Locked so Plex's own agent does not revert a value this tool owns.
        edits[f"{field}.locked"] = 1

    if "critic_rating" in writable and facts.critic_rating is not None:
        if format_critic(getattr(item, "rating", None)) != format_critic(facts.critic_rating):
            put("rating", _one_decimal(facts.critic_rating))

    if "audience_rating" in writable and facts.audience_rating is not None:
        current = format_audience(getattr(item, "audienceRating", None))
        if current != format_audience(facts.audience_rating):
            put("audienceRating", _one_decimal(facts.audience_rating))

    if "content_rating" in writable and content_rating:
        if getattr(item, "contentRating", None) != content_rating:
            put("contentRating", content_rating)

    if "studio" in writable and facts.studio:
        if getattr(item, "studio", None) != facts.studio:
            put("studio", facts.studio)

    if "originally_available" in writable and facts.originally_available:
        formatted = facts.originally_available.strftime("%Y-%m-%d")
        current = getattr(item, "originallyAvailableAt", None)
        current_str = current.strftime("%Y-%m-%d") if hasattr(current, "strftime") else current
        if current_str != formatted:
            put("originallyAvailableAt", formatted)

    if "user_rating" in writable and facts.user_rating is not None:
        # Compared on the formatted value for the same reason the critic
        # rating is: 8.65 and 8.7 both render "8.7" to a viewer.
        if format_critic(getattr(item, "userRating", None)) != format_critic(facts.user_rating):
            put("userRating", _one_decimal(facts.user_rating))

    if "original_title" in writable and facts.original_title:
        if getattr(item, "originalTitle", None) != facts.original_title:
            put("originalTitle", facts.original_title)

    if "genres" in writable and genres:
        current_genres = _current_genres(item)
        if sorted(current_genres) != sorted(genres):
            edits.update(_genre_plan(current_genres, genres))

    return edits


def _apply_genre_edits(item, additions: list[str], removals: list[str]) -> None:
    """Queue genre changes via the documented `addGenre`/`removeGenre` mixin
    methods. Must be called after `item.batchEdits()` and before
    `item.saveEdits()`; see `_genre_plan` for why the cache reset before
    `addGenre` matters. Split out from `apply_facts` so this exact code path
    can be exercised offline (batched, never saved) in tests against real
    plexapi classes.
    """
    if removals:
        item.removeGenre(removals, locked=True)
    if additions:
        # addGenre merges with item.genres, so a surplus tag still cached there
        # would be re-added in the same payload that removes it. Blank it for
        # the duration of the call, then put it back -- the caller's object must
        # not be left describing a state that was never true.
        original_genres = list(getattr(item, "genres", []))
        try:
            item.genres = []
            item.addGenre(additions, locked=True)
        finally:
            item.genres = original_genres


def _item_label(item) -> str:
    """`movie 'Heat' (1995)`, `show 'Dark' (2017)`, `episode 'Pilot' (Dark
    S01E01)` -- the operator reading a live log needs to know WHICH item was
    written, not just its type. Every attribute is optional because the
    plexapi object shapes differ per type (and the tests use bare fakes);
    whatever is missing is simply left out.
    """
    kind = getattr(item, "type", None) or "?"
    title = getattr(item, "title", None)
    if title is None:
        return kind
    label = "%s %r" % (kind, title)
    if kind == "episode":
        show = getattr(item, "grandparentTitle", None)
        season = getattr(item, "parentIndex", None)
        episode = getattr(item, "index", None)
        if show is not None and season is not None and episode is not None:
            return "%s (%s S%02dE%02d)" % (label, show, int(season), int(episode))
        return label
    year = getattr(item, "year", None)
    if year is not None:
        return "%s (%s)" % (label, year)
    return label


async def apply_facts(item, facts: GatheredFacts, operations=None) -> dict[str, object]:
    """Write the changed fields in one HTTP call.

    plexapi routes even a single-item edit through the library section, so
    batching the fields together turns six writes into one. Genres are
    queued through the documented `removeGenre`/`addGenre` mixin methods
    (see `_genre_plan`) rather than `item.edit()`, but still land inside the
    same `batchEdits()`/`saveEdits()` block, so it's still a single request.
    """
    edits = plan_edits(item, facts, operations)
    if not edits:
        return {}

    field_edits = {k: v for k, v in edits.items() if not k.startswith("genres.")}

    def _write() -> None:
        item.batchEdits()
        if field_edits:
            item.edit(**field_edits)
        _apply_genre_edits(item, edits.get("genres.added", []), edits.get("genres.removed", []))
        item.saveEdits()

    await asyncio.to_thread(_write)
    logger.info("plex: wrote %d field(s) to %s", len(edits), _item_label(item))
    return edits
