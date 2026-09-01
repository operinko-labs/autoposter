"""Row 89: what one definition asks of the Radarr or Sonarr instance.

Two halves that fail in opposite directions, and the difference is the whole
design.

- ``restricted_members`` narrows a resolved membership to the items the ONE
  configured instance already holds. It is read-only. When it cannot be
  evaluated -- the service is not configured, or the listing failed -- it
  returns ``None``, which the engine treats exactly as a filter that could not
  run: the desired set is emptied, ``lists.py`` reads that as "make no
  changes", and the collection is left as it was. Not restricting would write
  precisely the members the operator asked to exclude, which is a full,
  plausible, wrong collection nobody can see is wrong.
- ``tag_members`` writes tags back. It is a side channel: the collection has
  already been applied to Plex, and a dead Arr must not turn a good pass into
  a failed one. So it never raises, and it reports what it could not do.

Opt-in twice, the ``sync_to_mdb_list`` precedent (row 31): the definition names
tags AND the deployment sets ``collections.arr_tag_apply``.

Kometa's ``add_missing``/``radarr_add_all``/``radarr_remove_by_tag`` family --
telling an instance to ACQUIRE or DROP content -- is a declared non-goal
(``docs/superpowers/specs/2026-08-22-full-parity-roadmap.md:1213-1215``).
Nothing here adds, removes or deletes anything on an arr instance; the only
write is an additive tag.
"""
import logging

from autoposter.arr.sync import GUID_KEY, external_id

logger = logging.getLogger(__name__)

# Which library type each service's ids can mean, the same rule
# ``builders/arr.py`` states: Radarr's tmdbId is only ever a movie and Sonarr's
# tvdbId only ever a show.
_LIBRARY_TYPE = {"radarr": "Movie", "sonarr": "Show"}


async def _listing(client, service: str, run_cache: dict) -> list[dict]:
    """The instance's listing, fetched at most once per service per pass.

    The memo idiom ``builders/arr.py::_tag_map`` uses, failures included: a
    config with a dozen restricted definitions costs one GET, and a dead
    service is asked once rather than a dozen times on the pass that can least
    afford it.
    """
    key = f"arr.listing.{service}"
    if key not in run_cache:
        try:
            run_cache[key] = await client.listing()
        except Exception as error:  # noqa: BLE001 - memoised and re-raised below
            run_cache[key] = error
    entries = run_cache[key]
    if isinstance(entries, BaseException):
        raise entries
    return entries


async def _tag_map(client, service: str, run_cache: dict) -> dict[str, int]:
    """The label-to-id vocabulary, memoised on the SAME key
    ``builders/arr.py::_tag_map`` uses, so a pass with both a tag-list builder
    and a tagging definition costs one ``/api/v3/tag`` round trip."""
    key = f"arr.tags.{service}"
    if key not in run_cache:
        try:
            run_cache[key] = await client.tags()
        except Exception as error:  # noqa: BLE001 - memoised and re-raised below
            run_cache[key] = error
    tags = run_cache[key]
    if isinstance(tags, BaseException):
        raise tags
    return tags


async def restricted_members(
    definition, items: list, *, library_type: str, radarr, sonarr, run_cache: dict
) -> tuple[list | None, list[str]]:
    """``(kept, actions)``. ``kept is None`` means "could not be evaluated".

    ``None`` is the refusal the engine contains, not an error: see the module
    docstring for why the alternative is worse.
    """
    asked = [
        (service, client)
        for service, client in (("radarr", radarr), ("sonarr", sonarr))
        if getattr(definition, f"{service}_restrict", False)
    ]
    if not asked:
        return items, []

    actions: list[str] = []
    kept = items
    for service, client in asked:
        field = f"{service}_restrict"
        wanted_type = _LIBRARY_TYPE[service]
        if library_type != wanted_type:
            return None, [
                "%r: %s only holds %s entries, and this pass is running against "
                "a %s library, where it would exclude every member; nothing was "
                "changed. Narrow the definition with `libraries:`"
                % (definition.title, field, wanted_type.lower(), library_type)
            ]
        if client is None:
            return None, [
                "%r: %s is not configured, so %s could not be evaluated and "
                "nothing was changed"
                % (definition.title, service.title(), field)
            ]
        try:
            entries = await _listing(client, service, run_cache)
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            logger.exception(
                "%r: listing %s for %s failed", definition.title, service, field
            )
            return None, [
                "%r: %s could not be listed (%s), so %s was not evaluated and "
                "nothing was changed"
                % (definition.title, service.title(), type(exc).__name__, field)
            ]
        held = client.ids_in(entries)
        guid_key = GUID_KEY[service]
        before = len(kept)
        kept = [
            item for item in kept
            if (value := external_id(item, guid_key)) is not None and value in held
        ]
        actions.append(
            "%r: %s kept %d of %d member(s) %s holds"
            % (definition.title, field, len(kept), before, service.title())
        )

    if items and not kept:
        actions.append(
            "%r: the Arr restriction excluded every member; leaving the "
            "collection untouched" % definition.title
        )
    return kept, actions


async def tag_members(
    definition, items: list, *, library_type: str, radarr, sonarr,
    run_cache: dict, apply: bool,
) -> list[str]:
    """Write this definition's Arr tags onto the members the instance holds.

    Never raises: the collection has already been applied to Plex, and this is
    a side channel. Every failure comes back as an action string carrying the
    exception's CLASS NAME only -- an httpx error's message carries the request
    URL, and the URL is where an api key would be if anyone put one there.
    """
    asked = [
        (service, client, list(dict.fromkeys(labels)))
        for service, client, labels in (
            ("radarr", radarr, definition.item_radarr_tag),
            ("sonarr", sonarr, definition.item_sonarr_tag),
        )
        if labels
    ]
    if not asked:
        return []

    actions: list[str] = []
    for service, client, labels in asked:
        field = f"item_{service}_tag"
        named = ", ".join(labels)
        if library_type != _LIBRARY_TYPE[service]:
            actions.append(
                "%r: %s only holds %s entries, and this pass is running against "
                "a %s library, so nothing was tagged"
                % (definition.title, field, _LIBRARY_TYPE[service].lower(), library_type)
            )
            continue
        if client is None:
            actions.append(
                "%r: %s is not configured, so no member was tagged %s"
                % (definition.title, service.title(), named)
            )
            continue
        if not apply:
            # Off means off: not even the listing GET runs. The gate mirrors
            # ``apply_to_plex``'s posture exactly -- reporting "would tag" from
            # a real listing would still be a live request to an instance the
            # deployment has not opted into writing to.
            actions.append(
                "%r: would tag member(s) in %s with %s "
                "(collections.arr_tag_apply is off)"
                % (definition.title, service.title(), named)
            )
            continue
        try:
            entries = await _listing(client, service, run_cache)
            mapping = client.entry_ids_by_external_id(entries)
            guid_key = GUID_KEY[service]
            entry_ids, missing = [], 0
            for item in items:
                value = external_id(item, guid_key)
                entry_id = mapping.get(value) if value else None
                if entry_id is None:
                    missing += 1
                else:
                    entry_ids.append(entry_id)
            if missing:
                actions.append(
                    "%r: %d member(s) %s does not hold were skipped by %s"
                    % (definition.title, missing, service.title(), field)
                )
            if not entry_ids:
                actions.append(
                    "%r: no member is held by %s, so nothing was tagged %s"
                    % (definition.title, service.title(), named)
                )
                continue
            vocabulary = await _tag_map(client, service, run_cache)
            tag_ids = []
            for label in labels:
                tag_id = vocabulary.get(label)
                if tag_id is None:
                    tag_id = await client.create_tag(label)
                    # The memo is the pass's, and a second definition naming the
                    # same new tag must not create it again.
                    vocabulary[label] = tag_id
                    actions.append(
                        "%r: created the %s tag %r"
                        % (definition.title, service.title(), label)
                    )
                tag_ids.append(tag_id)
            await client.apply_tags(entry_ids, tag_ids)
            actions.append(
                "%r: tagged %d member(s) in %s with %s"
                % (definition.title, len(entry_ids), service.title(), named)
            )
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.exception(
                "%r: tagging %s with %s failed", definition.title, service, named
            )
            actions.append(
                "%r: tagging %d member(s) in %s failed (%s); the collection "
                "itself was applied as usual"
                % (definition.title, len(items), service.title(), type(exc).__name__)
            )
    return actions
