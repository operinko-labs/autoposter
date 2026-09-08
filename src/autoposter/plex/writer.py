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
#
# Roadmap row 99 is that later phase, and it arrives from the other side: the
# four TEXT fields below (``title``, ``sort_title``, ``summary``, ``tagline``)
# have NO provider source in this service at all and are never written from
# ``GatheredFacts`` -- they exist here so that a per-item OVERRIDE can name
# them. Their per-libtype placement is plexapi's own capability matrix
# (``plexapi/mixins/__init__.py:35-70``), which is also what Kometa's
# ``add_edit`` writes through: a season carries no ``titleSort`` and no
# ``tagline``, and an episode carries no ``tagline``.
#
# TWO OTHER MODULES READ THIS MAP and both move when it grows, intentionally:
# ``config/schema.py``'s ``field_verbs`` validator (so ``lock``/``unlock`` now
# work on the four text fields), and ``metadata_backup.py::capture_item`` (so
# row 86's backup file carries them and their lock flags).
WRITABLE_BY_KIND: dict[str, set[str]] = {
    "movie": {
        "critic_rating", "audience_rating", "content_rating",
        "genres", "studio", "originally_available",
        # Roadmap rows 32 and 33a. ``original_title`` is movies only: Plex
        # carries originalTitle for movies and not for shows or episodes
        # (plex/client.py:59-61, row 44's finding).
        "user_rating", "original_title",
        # Roadmap row 99, override-only.
        "title", "sort_title", "summary", "tagline",
    },
    "show": {
        "critic_rating", "audience_rating", "content_rating",
        "genres", "studio", "originally_available",
        "user_rating",
        "title", "sort_title", "summary", "tagline",
    },
    "season": {
        "critic_rating", "audience_rating", "user_rating",
        "title", "summary",
    },
    "episode": {"critic_rating", "audience_rating", "content_rating",
                "originally_available", "user_rating",
                "title", "sort_title", "summary"},
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
    # Roadmap row 99. Each is the same string twice, written out rather than
    # special-cased for the reason the block above states. ``title``'s Plex
    # attribute is ``title`` even though Kometa reaches it through
    # ``editTitle``: that method is ``editField("title", ...)`` underneath and
    # emits the same ``title.value``/``title.locked`` pair ``put()`` builds.
    "title": ("title", "title"),
    "sort_title": ("titleSort", "titleSort"),
    "summary": ("summary", "summary"),
    "tagline": ("tagline", "tagline"),
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
#
# Roadmap row 230 CLOSED this as won't-do on 2026-09-08, and ``verb_edits``
# therefore carries no ``reset`` branch: the load-time refusal above is the
# whole implementation, and a defensive ``if verb == "reset": continue`` in the
# loop could only ever be reached by a config that had already been refused.
# "Restore from the metadata backup" is a DIFFERENT promise -- the value at
# backup time, not the agent's -- and is filed as its own row under row 86.
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


def _ensure_locked(edits: dict[str, object], item, plex_field: str) -> None:
    """Lock ``plex_field`` when an override's value already matches Plex's.

    Task-2 fix round 1, ruling on I-1: ``override_edits`` diffs on VALUE, but
    a field the operator pinned must end up locked even when there is
    nothing to write -- an unlocked field Plex agrees with today is exactly
    the one Plex's own agent is free to rewrite on its next refresh, before
    a later pass would notice the drift and write-and-lock it. Emits nothing
    when Plex already reports it locked, which is what keeps this a one-time
    cost rather than a write every pass.
    """
    if _locked_in_plex(item, plex_field) is not True:
        edits[f"{plex_field}.locked"] = 1


def verb_edits(item, operations, overridden=frozenset()) -> dict[str, object]:
    """The lock/unlock/remove edits ``operations.field_verbs`` asks for (row 87).

    Each verb is dry-run-by-default behind its own apply flag: with the flag
    off the verb is LOGGED and nothing is written, and the field does NOT fall
    back to being written from its provider source -- the verb IS the source
    (the row's own phrasing), so an unapplied verb means "nothing happens to
    this field", never "do the old thing instead".

    Every verb is compared against what Plex currently reports, so a second
    pass over an item already in the wanted state writes nothing. That is what
    makes this steady-state rather than a rewrite every pass.

    ``overridden`` is the set of fields this ITEM has a per-item override for
    (roadmap row 99). A field in it is skipped here and logged once: a
    library-wide verb says what happens to a field everywhere, a per-item
    override says what happens to it HERE, and here wins. The log names the
    rating key and the field and never the value, which is operator-typed
    free text (row 213).
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
        # Checked before the collision log below (task-2 fix round 1, m-1):
        # a field this KIND cannot carry, or with no Plex name at all, could
        # never have collided with anything, so the log must not claim one.
        if field not in writable or field not in _PLEX_FIELD_NAMES:
            continue
        if field in overridden:
            logger.info(
                "plex: rating key %s has a per-item override for %s; the %r "
                "verb is skipped for this item",
                getattr(item, "ratingKey", None), field, verb,
            )
            continue
        if verb == "remove" and field not in _REMOVABLE_FIELDS:
            # Row 87's I1, applied to a set that row 99 just widened: an
            # accepted-but-ignored verb is indistinguishable from a working
            # one that has been switched off, so say so rather than pass
            # silently. Not a config-load refusal: ``{critic_rating: remove}``
            # has loaded and quietly done nothing since row 87 shipped, and
            # turning a config that boots today into one that refuses to is a
            # deployment risk this row has no mandate to take. The question is
            # filed beside row 229, which already owns that family; row 230
            # (the sibling question, for ``reset``) closed as won't-do, and
            # restore-from-backup is refiled as roadmap row 256 under row 86.
            #
            # INFO, not WARNING (task-2 fix round 1, I-2): this fires once
            # PER ITEM per pass, the same per-item volume as the sibling
            # "would %s %s on %s" line ten lines below, which is INFO for the
            # identical reason. A single library-wide config mistake would
            # otherwise cost one WARNING line per item, every pass, forever.
            logger.info(
                "plex: operations.field_verbs asks to remove %r, which this "
                "service does not clear; the verb is skipped (removable "
                "fields are %s)",
                field, ", ".join(sorted(_REMOVABLE_FIELDS)),
            )
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

    There is no lock key here, and that is roadmap row 246's other half: a
    `genres.locked` entry lived in this dict until then and never once reached
    Plex, because `apply_facts` strips exactly the PLURAL `"genres."` prefix it
    carried. The lock a real change needs rides `locked=True` on the
    `addGenre`/`removeGenre` calls `_apply_genre_edits` makes. The equal-list
    case never reaches this function at all, and locks through the SINGULAR
    `genre.locked` key in `plan_edits`/`override_edits` instead.

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


def override_edits(item, overrides: dict) -> dict[str, object]:
    """The edits this item's per-item metadata overrides ask for (row 99).

    An override is a SOURCE, in exactly the sense row 87's comment in
    ``plan_edits`` gives the word: a field an override names drops out of the
    provider-value path entirely, so the two can never both touch it in one
    payload. What that buys is inherited rather than rebuilt -- the diff
    against what Plex currently reports (so a second pass writes nothing), the
    lock on write (so Plex's own agent does not revert a value this tool
    owns), and a place inside ``apply_facts``' single batched HTTP call.

    ``overrides`` is ``{our field name: typed value}``, as
    ``plex/item_overrides.load_overrides`` returns it -- already parsed, and
    already in the canonical form the comparisons below are made in.

    A field the item's kind cannot carry is SKIPPED rather than written. The
    endpoint already refuses one with a 422, so this is belt and braces
    against a hand-inserted row or an item whose kind changed under a stored
    override; a plexapi object raises ``AttributeError`` for a field its type
    does not have, and losing an item's whole write to that would be a poor
    trade for a row nobody can see.
    """
    if not overrides:
        return {}
    writable = WRITABLE_BY_KIND.get(getattr(item, "type", "movie"), set())
    edits: dict[str, object] = {}

    def put(field: str, value: object) -> None:
        edits[f"{field}.value"] = value
        edits[f"{field}.locked"] = 1

    # Sorted so the payload -- and any log or test reading it -- is stable
    # across passes rather than dict-insertion ordered.
    for field in sorted(overrides):
        if field not in writable or field not in _PLEX_FIELD_NAMES:
            continue
        value = overrides[field]
        attribute, plex_field = _PLEX_FIELD_NAMES[field]
        if field == "genres":
            # SYNC semantics: the override IS the list, so the plan is
            # whatever additions and removals make Plex's genres exactly this.
            #
            # Roadmap row 246: the equal-list case locks, like every scalar
            # branch below. ``plex_field`` here is ``genre``, SINGULAR --
            # ``_PLEX_FIELD_NAMES``' one asymmetric entry -- and that is what
            # makes this work where the old ``genres.locked`` key never did:
            # ``apply_facts`` filters the PLURAL ``"genres."`` prefix out of
            # the payload, so the singular key flows into ``item.edit()``, the
            # exact wire shape ``operations.field_verbs: {genres: lock}`` has
            # sent since row 87. A real CHANGE needs no lock key of its own:
            # ``_apply_genre_edits`` sends ``locked=True`` on the mixin calls.
            current_genres = _current_genres(item)
            if sorted(current_genres) != sorted(value):
                edits.update(_genre_plan(current_genres, value))
            else:
                _ensure_locked(edits, item, plex_field)
            continue
        if field == "audience_rating":
            if format_audience(getattr(item, attribute, None)) != format_audience(value):
                put(plex_field, _one_decimal(value))
            else:
                _ensure_locked(edits, item, plex_field)
            continue
        if field in ("critic_rating", "user_rating"):
            # Compared on the FORMATTED value for the reason ``plan_edits``
            # gives: 8.65 and 8.7 both render the same thing to a viewer.
            if format_critic(getattr(item, attribute, None)) != format_critic(value):
                put(plex_field, _one_decimal(value))
            else:
                _ensure_locked(edits, item, plex_field)
            continue
        if field == "originally_available":
            formatted = value.strftime("%Y-%m-%d")
            current = getattr(item, attribute, None)
            current_str = (
                current.strftime("%Y-%m-%d") if hasattr(current, "strftime") else current
            )
            if current_str != formatted:
                put(plex_field, formatted)
            else:
                _ensure_locked(edits, item, plex_field)
            continue
        # The seven plain text fields.
        if getattr(item, attribute, None) != value:
            put(plex_field, value)
        else:
            _ensure_locked(edits, item, plex_field)
    return edits


def plan_edits(
    item, facts: GatheredFacts, operations=None, parental_categories=None,
    overrides=None,
) -> dict[str, object]:
    """Field/value pairs that differ from what Plex already holds.

    Ratings compare on their *formatted* value, because that is what a viewer
    sees: 8.65 and 8.6 both render "86%", so rewriting one as the other would
    churn Plex for no visible gain.

    ``operations`` is the ``OperationsConfig``; ``None`` -- what a direct
    caller and most tests pass -- means no mapper and no verb, which is
    byte-identical to the pre-row-34 behaviour. ``parental_categories`` is the
    row-85 fetch's result -- ``None``/most callers, in which case this folds
    in nothing new.

    ``overrides`` is roadmap row 99's per-item map -- ``None``/``{}`` for
    every caller that has none, which is byte-identical to the behaviour
    before that row. A field it names drops out of the value-write path for
    the same reason a verbed field does, and is written by ``override_edits``
    below instead.
    """
    verbs = getattr(operations, "field_verbs", None) or {}
    overrides = overrides or {}
    # A field named in field_verbs or in this item's overrides drops out of
    # the value-write path entirely: the verb, or the operator, replaces the
    # source for that field, so no two of them can ever touch it in one
    # payload. (Row 87 wrote the first half of this sentence; row 99 wrote
    # the second.)
    writable = (
        WRITABLE_BY_KIND.get(getattr(item, "type", "movie"), set())
        - set(verbs) - set(overrides)
    )
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
        else:
            # Roadmap row 246, the provider half of the same equal-value
            # guarantee ``override_edits`` gets above. The key is the SINGULAR
            # ``genre.locked``; ``apply_facts`` strips the PLURAL prefix
            # ``"genres."``, so this one reaches ``item.edit()``.
            #
            # INSIDE the ``and genres`` gate on purpose (row 246 C3): an item
            # no provider has genres for is not one this service owns the
            # genre list of, and it stays untouched exactly as it does today.
            _ensure_locked(edits, item, "genre")

    edits.update(verb_edits(item, operations, overridden=frozenset(overrides)))
    edits.update(override_edits(item, overrides))
    edits.update(parental_label_edits(item, parental_categories, operations))

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


def _apply_label_edits(item, additions: list[str]) -> None:
    """Queue label additions via plexapi's documented ``addLabel`` mixin
    method. Must be called after ``item.batchEdits()`` and before
    ``item.saveEdits()``, alongside ``_apply_genre_edits`` -- additions only,
    since ``parental_label_edits`` never produces a removal.
    """
    for tag in additions:
        item.addLabel(tag)


async def apply_facts(
    item, facts: GatheredFacts, operations=None, parental_categories=None,
    overrides=None,
) -> dict[str, object]:
    """Write the changed fields in one HTTP call.

    plexapi routes even a single-item edit through the library section, so
    batching the fields together turns several writes into one. Genres and
    parental-guide labels are both queued through their own mixin methods
    (``addGenre``/``removeGenre``, ``addLabel``) rather than ``item.edit()``,
    but still land inside the same ``batchEdits()``/``saveEdits()`` block, so
    it's still a single request.
    """
    edits = plan_edits(item, facts, operations, parental_categories, overrides)
    if not edits:
        return {}

    field_edits = {
        k: v for k, v in edits.items() if not k.startswith(("genres.", "labels."))
    }

    def _write() -> None:
        item.batchEdits()
        if field_edits:
            item.edit(**field_edits)
        _apply_genre_edits(item, edits.get("genres.added", []), edits.get("genres.removed", []))
        _apply_label_edits(item, edits.get("labels.added", []))
        item.saveEdits()

    await asyncio.to_thread(_write)
    logger.info("plex: wrote %d field(s) to %s", len(edits), _item_label(item))
    return edits
