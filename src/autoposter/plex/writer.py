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


# Our field name -> (the plexapi attribute holding the value, the name Plex
# uses for that field's LOCK). The two differ for exactly one entry: the
# attribute is ``genres`` and the lock field is ``genre``, singular. Every
# other entry is the same string twice, and is written out anyway rather than
# special-cased -- a map with one exception in it is read wrong exactly once.
_PLEX_FIELD_NAMES: dict[str, tuple[str, str]] = {
    "critic_rating": ("rating", "rating"),
    "audience_rating": ("audienceRating", "audienceRating"),
    "user_rating": ("userRating", "userRating"),
    "content_rating": ("contentRating", "contentRating"),
    "studio": ("studio", "studio"),
    "originally_available": ("originallyAvailableAt", "originallyAvailableAt"),
    "original_title": ("originalTitle", "originalTitle"),
    "genres": ("genres", "genre"),
}

# Roadmap row 87's ``remove`` ships for these four and no others. A scalar's
# "remove" is unambiguous -- clear the value. ``genres`` is list-shaped and the
# row records no semantics for a verb used AS THE SOURCE with no items
# supplied, so it is STOP-and-filed rather than guessed. The three rating
# fields are excluded for a different reason: Plex has no empty rating, and
# writing "" into one is a shape this project has never sent.
_REMOVABLE_FIELDS = frozenset(
    {"content_rating", "studio", "originally_available", "original_title"}
)

# ``reset`` is absent on purpose -- see the STOP-and-file row. It would mean
# restoring the value Plex's own agent produces, and this project holds no
# agent value anywhere and has never called a Plex refresh. Absent here means
# absent from the config validator too (schema.py's ``_known_fields_and_verbs``
# checks membership in this set), so ``{field: reset}`` is a load-time error
# rather than a setting that loads and silently does nothing.
FIELD_VERBS = frozenset({"lock", "unlock", "remove"})


def _locked_in_plex(item, plex_field: str) -> bool | None:
    """Whether Plex reports this field locked, or ``None`` if it does not say.

    plexapi exposes per-field locks as ``item.fields``, each entry carrying a
    ``name`` and a ``locked`` flag; a field Plex has never written about is
    simply not in the list. ``None`` -- "it did not say" -- is treated by the
    verbs as "not yet in the wanted state", so a verb writes once rather than
    silently doing nothing on an item Plex is quiet about.
    """
    for field in getattr(item, "fields", None) or []:
        if getattr(field, "name", None) == plex_field:
            return bool(getattr(field, "locked", False))
    return None


def verb_edits(item, operations) -> dict[str, object]:
    """The lock/unlock/remove edits ``operations.field_verbs`` asks for (row 87).

    Each verb is dry-run-by-default behind its own apply flag: with the flag
    off the verb is LOGGED and nothing is written, and the field does NOT fall
    back to being written from its provider source -- the verb IS the source
    (the row's own phrasing), so an unapplied verb means "nothing happens to
    this field", never "do the old thing instead".

    Every verb is compared against what Plex currently reports, so a second
    pass over an item already in the wanted state writes nothing. That is what
    makes this steady-state rather than a rewrite every pass.
    """
    verbs = getattr(operations, "field_verbs", None) or {}
    if not verbs:
        return {}
    writable = WRITABLE_BY_KIND.get(getattr(item, "type", "movie"), set())
    applied = {
        "lock": getattr(operations, "lock_apply", False),
        "unlock": getattr(operations, "unlock_apply", False),
        "remove": getattr(operations, "remove_apply", False),
    }
    edits: dict[str, object] = {}
    for field, verb in verbs.items():
        if field not in writable or field not in _PLEX_FIELD_NAMES:
            continue
        if verb == "reset":
            # STOP-and-filed: see the module's _REMOVABLE_FIELDS comment.
            continue
        if verb == "remove" and field not in _REMOVABLE_FIELDS:
            continue
        if not applied.get(verb):
            logger.info(
                "plex: would %s %s on %s (operations.%s_apply is off)",
                verb, field, _item_label(item), verb,
            )
            continue
        attribute, plex_field = _PLEX_FIELD_NAMES[field]
        if verb == "lock":
            if _locked_in_plex(item, plex_field) is not True:
                edits[f"{plex_field}.locked"] = 1
        elif verb == "unlock":
            if _locked_in_plex(item, plex_field) is not False:
                edits[f"{plex_field}.locked"] = 0
        elif verb == "remove":
            if getattr(item, attribute, None) not in (None, ""):
                edits[f"{plex_field}.value"] = ""
                # Locked after clearing: an unlocked empty field is refilled by
                # Plex's own agent on its next refresh, which would make this
                # verb a no-op with extra requests.
                edits[f"{plex_field}.locked"] = 1
    return edits


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


def _current_labels(item) -> dict[str, str]:
    """``{casefolded tag: the tag as the server spells it}`` for one item.

    Mirrors ``collections/reconcile.py``'s ``_folded_labels`` for the same
    reason: Plex canonicalises label case, so an exact compare would add a
    label already present under different casing every single pass.
    """
    return {
        tag.casefold(): tag
        for tag in (getattr(entry, "tag", entry) for entry in getattr(item, "labels", None) or [])
        if isinstance(tag, str)
    }


def _label_text(category_text: str, severity_text: str) -> str:
    """``"Violence & Gore: Severe"``. Not a Kometa-mandated string -- row 85's
    roadmap cell and its Kometa-inventory source name no label format at
    all -- so this is the build's own documented choice (see the row close),
    built only from ``category.text``/``severity.text``, never an id."""
    return f"{category_text}: {severity_text}"


def parental_label_edits(
    item, categories: list[tuple[str, str, str]] | None, operations
) -> dict[str, object]:
    """Row 85: the labels IMDb's parental-guide categories add to this item.

    ``categories`` is ``None`` or ``[]`` for "nothing to label" (see
    ``providers/imdb_parental_guide.py``'s module docstring for every reason)
    and produces no edits either way.

    Each entry is ``(category id, category text, severity text)`` --
    ``severity.text``, never ``severity.id``. A category whose severity is
    ``"None"`` is skipped unless ``operations.parental_labels_include_none``
    says otherwise.

    Additive only, like ``collections/reconcile.py``'s ``_apply_labels``
    without ``label_sync``: this op has no removal semantics stated anywhere
    in its row, so it never strips a label IMDb's guide no longer supports.

    Dry-run by default, the same split row 87's verbs draw: with
    ``parental_labels_apply`` off, a wanted-but-missing label is LOGGED and
    no edit is produced.
    """
    if not categories:
        return {}
    include_none = bool(getattr(operations, "parental_labels_include_none", False))
    wanted = [
        _label_text(category_text, severity_text)
        for _category_id, category_text, severity_text in categories
        if severity_text != "None" or include_none
    ]
    if not wanted:
        return {}
    stored = _current_labels(item)
    missing = [tag for tag in wanted if tag.casefold() not in stored]
    if not missing:
        return {}
    if not getattr(operations, "parental_labels_apply", False):
        logger.info(
            "plex: would add parental-guide label(s) %s to %s "
            "(operations.parental_labels_apply is off)",
            ", ".join(missing), _item_label(item),
        )
        return {}
    return {"labels.added": missing}


def plan_edits(item, facts: GatheredFacts, operations=None) -> dict[str, object]:
    """Field/value pairs that differ from what Plex already holds.

    Ratings compare on their *formatted* value, because that is what a viewer
    sees: 8.65 and 8.6 both render "86%", so rewriting one as the other would
    churn Plex for no visible gain.

    ``operations`` is the ``OperationsConfig``; ``None`` -- what a direct
    caller and most tests pass -- means no mapper and no verb, which is
    byte-identical to the pre-row-34 behaviour.
    """
    verbs = getattr(operations, "field_verbs", None) or {}
    # A field named in field_verbs drops out of the value-write path entirely:
    # the verb replaces the source for that field, so the two can never both
    # touch it in one payload.
    writable = WRITABLE_BY_KIND.get(getattr(item, "type", "movie"), set()) - set(verbs)
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

    edits.update(verb_edits(item, operations))

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
