"""Read-modify-write metadata writes against a Jellyfin ``BaseItemDto``.

Both API calls are cited to docs/reference/2026-09-jellyfin-openapi-12.md:
``GET /Items/{itemId}`` and ``POST /Items/{itemId}`` (``UpdateItem`` -- a
full-body replace, 204 on success, no partial-field editor route the way
Plex's ``item.edit()`` has). That capture's "16 fields the client writes"
list (Name, SortName, ForcedSortName, Overview, Genres, Studios,
OfficialRating, Tags, LockedFields, ProviderIds, Path, IndexNumber,
ParentIndexNumber, SeriesId, Id, Type) was scoped to the resolve/write work
that preceded this module and does not individually verify ``CriticRating``,
``CommunityRating``, ``OriginalTitle`` or ``PremiereDate`` -- this module
writes them anyway, beyond that capture's verified list, and they should be
re-verified live before this write path is trusted in production.

``plex.writer.plan_edits`` is reused unmodified rather than
reimplementing the diff -- it already carries the provider-value diff, the
lock/unlock/remove verbs (``verb_edits``), the per-item override map
(``override_edits``) and the parental-guide labels, all gated by
``WRITABLE_BY_KIND``. ``_DtoView`` is the adapter that lets a Jellyfin dict
stand in for the plexapi object ``plan_edits`` (and the functions it calls)
read attributes off.
"""
from __future__ import annotations

import logging
from datetime import date

from autoposter.plex.writer import _PLEX_FIELD_NAMES, WRITABLE_BY_KIND, _item_label, plan_edits

logger = logging.getLogger(__name__)

KIND_TO_PLEX_TYPE = {"Movie": "movie", "Series": "show", "Season": "season", "Episode": "episode"}

# Plex edit key (what plan_edits' `put(field, value)` names -- the plexapi
# attribute name, identical to the lock-key name for every field except
# genres) -> the Jellyfin DTO property it is written to. The three
# override-only text fields (`title`, `summary`, `tagline`) are real DTO
# properties too -- WRITABLE_BY_KIND permits an override to set any of them,
# and dropping the value while still honouring its paired lock
# would lock Name/Overview to a STALE value, worse than not
# writing at all. `tagline` has no lock representation (not in LOCKABLE --
# not a captured MetadataField member) -- an override on it is value-only.
EDIT_TO_DTO = {
    "titleSort": "ForcedSortName",
    "contentRating": "OfficialRating",
    "studio": "Studios",
    "originalTitle": "OriginalTitle",
    "originallyAvailableAt": "PremiereDate",
    "rating": "CriticRating",
    "audienceRating": "CommunityRating",
    "title": "Name",
    "summary": "Overview",
    "tagline": "Taglines",
}

# Edit keys with no Jellyfin DTO home this task writes to, dropped with a
# debug log rather than raised: `userRating` (Jellyfin's BaseItemDto carries
# no per-server user rating -- _DtoView.userRating is always None, so a
# `user_rating` fact always looks like a change and would otherwise retry
# forever) and `addedAt` (DateCreated is read for comparison but this task
# does not write it back).
_NO_DTO_HOME = frozenset({"userRating", "addedAt"})

# Exactly the captured MetadataField members that map to a field this writer
# ever produces (spec / capture "MetadataField enum", cast/productionLocations/
# tags/runtime excluded -- this service never writes those). Keyed by OUR
# field name (the same vocabulary as plex.writer.WRITABLE_BY_KIND), matching
# what a `lock`/`unlock` verb or a per-item override names.
LOCKABLE = {
    "title": "Name",
    "summary": "Overview",
    "genres": "Genres",
    "studio": "Studios",
    "content_rating": "OfficialRating",
}
_DTO_TO_LOCKABLE_FIELD = {dto_name: field for field, dto_name in LOCKABLE.items()}

# Our field name -> the Plex LOCK key name plan_edits/verb_edits/override_edits
# emit for it (plex.writer._PLEX_FIELD_NAMES' second tuple element, restated
# here rather than imported since only these five entries are ever lockable
# on Jellyfin). "genre"/"genres" is the one asymmetric entry, same as Plex's.
_LOCK_KEY_NAME = {"title": "title", "summary": "summary", "genres": "genre",
                  "studio": "studio", "content_rating": "contentRating"}
_LOCK_KEY_TO_FIELD = {plex_name: field for field, plex_name in _LOCK_KEY_NAME.items()}

# Plex edit key -> our fact-field name (the inverse of plex.writer's own
# attribute map), so the value returned by apply_facts is named the way a
# caller reading GatheredFacts already thinks about fields, not Plex's names.
_ATTR_TO_FACT = {attribute: field for field, (attribute, _lock) in _PLEX_FIELD_NAMES.items()}


def _parse_date(value: str | None) -> date | None:
    """The DATE component of a Jellyfin ISO-8601 timestamp, or ``None``.

    Jellyfin's wire format carries up to 7 fractional-second digits (.NET
    ticks) and a trailing ``Z`` -- more precision than ``date.fromisoformat``
    needs or `datetime.fromisoformat` reliably accepts across versions. Every
    comparison plan_edits makes against this value is at day granularity
    (``.strftime("%Y-%m-%d")``), so only the first 10 characters ever matter.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class _Tag:
    """The ``.tag`` shape ``plan_edits``' ``_current_genres`` reads off
    ``item.genres`` -- plexapi's genre objects, minimally."""

    def __init__(self, name: str):
        self.tag = name


class _LockField:
    """The ``.name``/``.locked`` shape ``_locked_in_plex`` reads off
    ``item.fields``. Jellyfin's ``LockedFields`` is a flat "present" list with
    no separate unlocked entries, so every entry this class represents is
    locked; a field absent from ``LockedFields`` is simply never produced
    here, which is what makes ``_locked_in_plex`` correctly answer ``None``
    ("Jellyfin did not say") for it rather than ``False``."""

    def __init__(self, name: str):
        self.name = name
        self.locked = True


class _DtoView:
    """Exposes a Jellyfin ``BaseItemDto`` through the plexapi attribute names
    ``plan_edits`` (and the functions it calls internally -- ``verb_edits``,
    ``override_edits``, ``parental_label_edits``, ``_item_label``) read off an
    item, so that whole diff can be reused unmodified.

    Every property reads the dto fresh rather than caching -- ``apply_facts``
    mutates the same dict in place while building the write payload, and nothing
    here needs a value frozen at construction time.
    """

    def __init__(self, dto: dict):
        self._dto = dto

    @property
    def type(self) -> str:
        return KIND_TO_PLEX_TYPE.get(self._dto.get("Type", ""), "")

    @property
    def rating(self) -> float | None:
        # Jellyfin's CriticRating is 0-100; Plex's `rating` -- and the
        # format_critic comparison plan_edits runs both sides through -- is
        # 0-10, so this divides by 10 and the two compare like with like.
        value = self._dto.get("CriticRating")
        return None if value is None else value / 10

    @property
    def audienceRating(self) -> float | None:  # noqa: N802 -- plexapi attribute name
        return self._dto.get("CommunityRating")  # already 0-10 on both sides

    @property
    def userRating(self) -> None:  # noqa: N802
        return None  # Jellyfin's BaseItemDto has no per-server user rating

    @property
    def contentRating(self) -> str | None:  # noqa: N802
        return self._dto.get("OfficialRating")

    @property
    def studio(self) -> str | None:
        studios = self._dto.get("Studios") or []
        return studios[0].get("Name") if studios else None

    @property
    def titleSort(self) -> str | None:  # noqa: N802
        return self._dto.get("ForcedSortName") or self._dto.get("SortName")

    @property
    def originalTitle(self) -> str | None:  # noqa: N802
        return self._dto.get("OriginalTitle")

    @property
    def originallyAvailableAt(self) -> date | None:  # noqa: N802
        return _parse_date(self._dto.get("PremiereDate"))

    @property
    def addedAt(self) -> date | None:  # noqa: N802
        return _parse_date(self._dto.get("DateCreated"))

    @property
    def genres(self) -> list[_Tag]:
        return [_Tag(name) for name in self._dto.get("Genres") or []]

    @property
    def title(self) -> str | None:
        return self._dto.get("Name")

    @property
    def summary(self) -> str | None:
        return self._dto.get("Overview")

    @property
    def tagline(self) -> str | None:
        taglines = self._dto.get("Taglines") or []
        return taglines[0] if taglines else None

    @property
    def fields(self):
        # _locked_in_plex reads `.name`/`.locked` off each entry, comparing
        # `.name` against a PLEX lock-key name ("studio", "contentRating",
        # "genre", ...) -- so each of Jellyfin's LockedFields entries (a
        # MetadataField name: "Studios", "OfficialRating", "Genres", ...) is
        # translated back through LOCKABLE/_LOCK_KEY_NAME first. A locked
        # name this module does not track (e.g. "Tags") is simply skipped --
        # plan_edits never asks about it.
        out = []
        for dto_name in self._dto.get("LockedFields") or []:
            field = _DTO_TO_LOCKABLE_FIELD.get(dto_name)
            if field is not None:
                out.append(_LockField(_LOCK_KEY_NAME[field]))
        return out

    # -- read only for _item_label's log-line formatting and verb_edits'
    # per-item collision log; never written.
    @property
    def year(self):
        return self._dto.get("ProductionYear")

    @property
    def ratingKey(self):  # noqa: N802
        return self._dto.get("Id")

    @property
    def grandparentTitle(self):  # noqa: N802
        return self._dto.get("SeriesName")

    @property
    def parentIndex(self):  # noqa: N802
        return self._dto.get("ParentIndexNumber")

    @property
    def index(self):
        return self._dto.get("IndexNumber")


def _log_non_lockable_verbs(view: _DtoView, operations) -> None:
    """One INFO line per field named in ``operations.field_verbs`` with a
    ``lock``/``unlock`` verb that Jellyfin's captured ``MetadataField`` enum
    cannot represent: the verb is accepted by
    config (``operations`` already loaded), ``verb_edits`` computes an edit
    for it same as any other field, and that edit is silently unwritable on
    this server -- silence here would read as "it worked."
    """
    verbs = getattr(operations, "field_verbs", None) or {}
    if not verbs:
        return
    writable = WRITABLE_BY_KIND.get(view.type, set())
    for field, verb in verbs.items():
        if verb in ("lock", "unlock") and field in writable and field in _PLEX_FIELD_NAMES and field not in LOCKABLE:
            logger.info(
                "jellyfin: %r has no MetadataField on this server; the %r verb is a no-op for it",
                field, verb,
            )


async def apply_facts(
    api, ref, facts, operations=None, parental_categories=None, overrides=None,
) -> dict[str, object]:
    """Write the changed fields to Jellyfin in one read-modify-write POST.

    Returns the fact-field names actually changed, mapped to the value
    written -- or the string ``"locked"``/``"unlocked"`` for a field whose
    only change was its ``LockedFields`` membership, with no value write of
    its own (e.g. a bare ``lock`` verb on an already-correct field). Returns
    ``{}`` and makes no POST when nothing would change -- mirroring
    ``plex.writer.apply_facts``.
    """
    dto = await api.item(ref.native_id)  # capture -> "/Items/{itemId}" -> get
    view = _DtoView(dto)
    edits = plan_edits(view, facts, operations, parental_categories, overrides)

    _log_non_lockable_verbs(view, operations)

    if not edits:
        return {}

    written: dict[str, object] = {}
    dto_changed = False

    # --- plain scalar/text value writes ---
    for key, value in edits.items():
        if not key.endswith(".value"):
            continue
        field = key[: -len(".value")]
        dto_prop = EDIT_TO_DTO.get(field)
        if field in _NO_DTO_HOME or dto_prop is None:
            logger.debug("jellyfin: %s has no writable DTO property on this server; dropping the write", field)
            continue
        if dto_prop == "CriticRating":
            dto[dto_prop] = round(value * 10, 1)
        elif dto_prop == "Studios":
            dto[dto_prop] = [{"Name": value}]
        elif dto_prop == "Taglines":
            dto[dto_prop] = [value]
        elif dto_prop == "PremiereDate":
            dto[dto_prop] = f"{value}T00:00:00.0000000Z"
        else:
            dto[dto_prop] = value
        written[_ATTR_TO_FACT.get(field, field)] = value
        dto_changed = True

    # --- genres: plan_edits/_genre_plan emit additions/removals, never a
    # plain "genres.value" -- Jellyfin's Genres is a full replaced list.
    additions = edits.get("genres.added") or []
    removals = edits.get("genres.removed") or []
    genres_changed = bool(additions or removals)
    if genres_changed:
        current = dto.get("Genres") or []
        removed = set(removals)
        new_genres = [g for g in current if g not in removed]
        for g in additions:
            if g not in new_genres:
                new_genres.append(g)
        dto["Genres"] = new_genres
        written["genres"] = new_genres
        dto_changed = True

    # Parental-guide labels (roadmap row 85) have no Jellyfin DTO home this
    # task writes to -- Tags is the nearest concept and is not what a Plex
    # label maps to; dropped rather than mis-written.
    label_changes = len(edits.get("labels.added", [])) + len(edits.get("labels.removed", []))
    if label_changes:
        logger.debug(
            "jellyfin: parental-guide labels have no writable DTO property on this server; "
            "dropping %d label change(s)", label_changes,
        )

    # --- LockedFields: existing membership, plus/minus what the edits ask for.
    existing_locked = set(dto.get("LockedFields") or [])
    new_locked = set(existing_locked)
    for key, value in edits.items():
        if not key.endswith(".locked"):
            continue
        field = _LOCK_KEY_TO_FIELD.get(key[: -len(".locked")])
        if field is None:
            continue  # not one of the five lockable fields -- no Jellyfin home for the bit
        dto_name = LOCKABLE[field]
        if value:
            new_locked.add(dto_name)
        else:
            new_locked.discard(dto_name)
    if genres_changed:
        # Plex's real lock for a genuine genre change rides `locked=True` on
        # the addGenre/removeGenre mixin calls, never a "genres.locked" edit
        # key (see plex/writer.py::_genre_plan's docstring) -- so the same
        # protection must be applied here explicitly, or a provider-driven
        # genre change would land unlocked and Jellyfin's own scanner could
        # revert it before the next pass ever notices.
        new_locked.add("Genres")

    if new_locked != existing_locked:
        for dto_name in new_locked - existing_locked:
            field = _DTO_TO_LOCKABLE_FIELD.get(dto_name)
            if field is not None and field not in written:
                written[field] = "locked"
        for dto_name in existing_locked - new_locked:
            field = _DTO_TO_LOCKABLE_FIELD.get(dto_name)
            if field is not None and field not in written:
                written[field] = "unlocked"
        dto["LockedFields"] = sorted(new_locked)
        dto_changed = True

    if not dto_changed:
        return {}

    await api.update_item(ref.native_id, dto)  # capture -> "/Items/{itemId}" -> post (UpdateItem)
    # Names, not a count, for the reason plex/writer.py's _edited_fields gives:
    # a count cannot say which field a full pass keeps rewriting. The item is
    # named the way the Plex line names it, so one item's two write lines read
    # alike; the native id stays for looking it up in Jellyfin.
    logger.info(
        "jellyfin: wrote %s to %s [%s]",
        ", ".join(
            f"{field} ({how})" if how in ("locked", "unlocked") else field
            for field, how in sorted(written.items())
        ),
        _item_label(view),
        ref.native_id,
    )
    return written
