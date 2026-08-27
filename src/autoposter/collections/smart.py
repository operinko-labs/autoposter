"""One Plex-native smart collection, reconciled from a built search URL.

The third reconciler in this package, and the one that owns the least. Its
siblings:

- ``reconcile.py`` -- the Common Sense age buckets, a FAMILY of smart
  collections whose filters are derived from the library's own ratings and
  written through plexapi's ``createCollection(smart=True)``.
- ``lists.py`` -- every collection with a membership this service maintains.
- here -- ONE smart collection per definition, whose filter is the operator's
  own ``smart_filter`` query, written as a RAW POST.

**Why a raw POST rather than plexapi's ``filters=`` (9c decision C1).** The
phase chain made exactly one grammar byte-provable: 9b's ``build_search_url``,
gated by an oracle against Kometa's own ``build_filter``, and 9c's
``tests/test_smart_collection_oracle.py`` extends that gate through the
envelope. Handing plexapi a ``filters`` dict instead would add a second
translation layer with its own correctness surface AND different semantics --
plexapi comma-joins a multi-value tag, which Plex reads as OR, where 9b's
grammar emits one term per value joined by the block's own conjunction, which
under ``all:`` is an AND. The same config would build a different collection.
So the URI this module sends is the oracle-proven string, and plexapi is used
for everything that is not the URI: the re-read, the labels, the sort title, the
display mode, the summary.

**What is deliberately NOT here.**

- No comparison against the ``content`` attribute Plex echoes back on a smart
  collection (C10) -- spelled without the leading dot here on purpose, because
  ``test_no_reconciler_reads_a_collections_content_echo`` greps this file for
  exactly that read. Drift is detected the way the Common Sense family does --
  a ``definition_hash`` over the DESIRED state, stored in
  ``managed_collections`` -- which means a Plex-side manual edit to a smart
  filter goes undetected, exactly as it does for that family today. That is a
  documented consequence, not an oversight: hashing the echo would make every
  pass depend on a byte-for-byte round trip nobody has verified.
- No ``ignore_blank_results`` (C8). Kometa's switch downgrades "this filter
  matches nothing" from an error to a log line; an error-downgrade switch is the
  ``validate:`` class 9b refused.
- No delete-and-recreate on a shape change (C11). See
  ``reconcile.shape_conflict``.
"""
import hashlib
import logging

import httpx
from plexapi.utils import joinArgs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.posters import apply_poster, posters_enabled
from autoposter.collections.reconcile import (
    LIBTYPES,
    _edit_collection_summary,
    apply_collection_settings,
    resolve_collision,
    shape_conflict,
)
from autoposter.db.models import ManagedCollection

logger = logging.getLogger(__name__)

__all__ = [
    "SmartCollectionUnavailable",
    "SmartFilterMatchedNothing",
    "count_matches",
    "create_smart_collection",
    "reconcile_smart_collection",
    "require_matches",
    "smart_definition_hash",
    "smart_filter_uri",
    "update_smart_collection",
]


class SmartFilterMatchedNothing(Exception):
    """The query answered with zero items, at create or at update (C8).

    Its own class so the caller can turn it into one definition's refusal rather
    than a dead pass -- and so the engine's class-name-only log line says which
    kind of failure this was.
    """


class SmartCollectionUnavailable(Exception):
    """Plex would not answer at all.

    Never carries a Plex exception's message: those can contain a tokenised URL.
    The same shape as ``plex_search.PlexSearchUnavailable``.
    """


def smart_filter_uri(server, section_key, url: str) -> str:
    """Kometa's ``build_smart_filter`` (modules/plex.py:1615-1616).

    Not a URL a client fetches -- a ``server://`` uri Plex STORES and evaluates
    itself, which is the whole difference between a smart collection and a list
    one. ``server._uriRoot()`` is private plexapi and is pinned in
    ``tests/test_plexapi_collection_contract.py`` for exactly that reason.
    """
    return "%s/library/sections/%s/all%s" % (server._uriRoot(), section_key, url)


def count_matches(section, url: str) -> int:
    """How many items ``url`` matches right now. Zero is an answer, not a fault.

    Split out of ``require_matches`` because the dynamic engine's
    ``minimum_items`` asks a different question of the same read: "how many, so
    I can compare" rather than "is this collection worth creating at all". Two
    functions rather than one with a flag, so a reader of either call site
    cannot mistake it for the other.

    The catch is blanket and class-name-only, which is ``plex_search.build``'s
    own reasoning (:342-359): any failure of this call ends the caller's
    decision the same way, and nothing is memoised here that a coding bug could
    be mistaken for a library fact. It lives on THIS half so both callers
    inherit it.
    """
    try:
        items = section.fetchItems("/library/sections/%s/all%s" % (section.key, url))
    except Exception as error:  # class name only, never the message
        raise SmartCollectionUnavailable(
            "Plex would not answer this smart filter: %s" % type(error).__name__
        ) from None
    return len(items)


def require_matches(section, url: str) -> int:
    """Kometa's ``test_smart_filter`` (modules/plex.py:1580-1584), C8's half.

    Returns how many items the filter matches right now, and refuses at zero.
    The count is not stored anywhere -- a smart collection's membership is
    Plex's and changes without us -- it exists only so the action string can say
    what the operator's filter actually found.
    """
    matched = count_matches(section, url)
    if not matched:
        raise SmartFilterMatchedNothing(
            "this search matches nothing in this library right now, and a smart "
            "collection built from it would be permanently empty. Widen the "
            "filter, or narrow the definition with `libraries:` so it only "
            "targets libraries that can answer it. (Kometa refuses here too -- "
            "modules/plex.py:1580-1584 -- behind an `ignore_blank_results` "
            "switch this service deliberately does not offer.)"
        )
    return matched


def create_smart_collection(section, libtype: str, title: str, url: str):
    """Kometa's ``create_smart_collection`` (modules/plex.py:1592-1600).

    The same raw-POST idiom ``reconcile.create_blank_collection`` needs and for
    an adjacent reason: plexapi's ``createCollection(smart=True)`` takes a
    ``filters`` dict, and there is no entry point that accepts a uri. The three
    plexapi internals this depends on -- ``PlexServer._uriRoot``,
    ``PlexServer.query``'s ``method`` override and ``server._session.post`` --
    are pinned in ``tests/test_plexapi_collection_contract.py``.

    Returns the created collection, re-read through plexapi: the POST answers
    with the collection's XML but not through a route plexapi will build an
    object from, and every step after this one is an ordinary plexapi edit.
    """
    server = section._server
    args = {
        "type": 1 if libtype == "movie" else 2,
        "title": title,
        "smart": 1,
        "sectionId": section.key,
        "uri": smart_filter_uri(server, section.key, url),
    }
    server.query("/library/collections%s" % joinArgs(args), method=server._session.post)
    return section.collection(title)


def update_smart_collection(section, collection, url: str) -> None:
    """Kometa's ``update_smart_collection`` (modules/plex.py:1618-1620).

    ``PUT {collection}/items?uri=...`` -- the same uri value the create POST
    carries, on the collection's own items route. Replacing the stored filter is
    the only edit a smart collection has; there is no partial one.
    """
    server = section._server
    args = {"uri": smart_filter_uri(server, section.key, url)}
    server.query(
        "/library/collections/%s/items%s" % (collection.ratingKey, joinArgs(args)),
        method=server._session.put,
    )


def smart_definition_hash(url: str, summary: str | None, settings=None) -> str:
    """The desired state, hashed -- what an unchanged pass short-circuits on.

    Over the BUILT URI (C10), never over the ``content`` attribute Plex echoes
    back -- spelled without the leading dot for the reason the module docstring
    above gives, since the same grep reaches here too. Three parts,
    and each is in it because a pass that skipped on this hash would otherwise
    silently drop an edit: the uri decides membership, the summary is written by
    this module, and ``settings`` folds in exactly the way
    ``lists._settings_parts`` folds it into the members hash -- a definition
    whose only edit was a new label or sort title would otherwise be recognised
    as already current.

    ``_settings_parts`` is imported locally for the reason
    ``reconcile.definition_hash`` imports it locally: ``lists.py`` imports from
    ``reconcile.py`` at load time, and a module-level import here would build a
    second edge into that same cycle for no gain. Settings contribute nothing at
    their defaults, so a definition that sets none hashes to the same string it
    would have without this term.
    """
    from autoposter.collections.lists import _settings_parts

    payload = "\x1f".join([url, summary or "", *_settings_parts(settings)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def reconcile_smart_collection(
    session: AsyncSession,
    section,
    library: str,
    library_type: str,
    title: str,
    url: str,
    label: str,
    summary: str | None = None,
    dry_run: bool = True,
    existing: dict | None = None,
    adopt: bool = False,
    adopt_from: list[str] | None = None,
    adopt_removes_prior_label: bool = False,
    protect_labels: list[str] | None = None,
    http: httpx.AsyncClient | None = None,
    config=None,
    settings=None,
) -> list[str]:
    """Bring one smart collection in line with ``url``.

    ``url`` is a query string from ``?`` onward -- ``build_search_url``'s
    output, unmodified. This module never builds one; taking it as a string is
    what lets the whole reconcile be tested without a builder, a registry entry
    or a params model.

    ``existing`` is the caller's ``{title: collection}`` map of the section,
    shared across the pass for the reason ``lists.py`` gives: one
    ``section.collections()`` returns every collection in the library, so a
    caller reconciling several in a row pays for it once. A collection created
    here is written back into it. Omitting it falls back to listing here, and
    the write-back then lands in a map nobody else reads -- which is what a
    direct caller with no pass around it should get.

    ``settings`` is the ``CollectionDefinition``, supplying the per-definition
    collection settings (labels, sort title, display mode, hub visibility) the
    shared ``apply_collection_settings`` applies. ``summary`` is the definition's
    own; there is no builder-derived one here, because the builder derives no
    membership either.

    Returns a description of every action taken -- or, under ``dry_run``, every
    action that would be taken. ``flush``, never ``commit``: the commit belongs
    to the caller, so a per-library rollback can take these rows with it.
    """
    libtype = LIBTYPES[library_type]
    listing = existing if existing is not None else {
        collection.title: collection for collection in section.collections()
    }
    collection = listing.get(title)
    actions: list[str] = []

    if collection is not None:
        # C11 first: a list collection under a smart definition must never reach
        # the ownership check, because passing it would send the pass on to an
        # update route that cannot mean anything for that collection.
        conflict = shape_conflict(collection, title, want_smart=True)
        if conflict is not None:
            logger.warning("%s: %s", library, conflict)
            return [conflict]

        ok, message = resolve_collision(
            collection, label, adopt, adopt_from or [], adopt_removes_prior_label,
            dry_run, protect_labels or [],
        )
        if message:
            actions.append(message)
        if not ok:
            return actions

    record = (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.library == library, ManagedCollection.title == title
            )
        )
    ).scalar_one_or_none()

    wanted = smart_definition_hash(url, summary, settings)
    posters_on = posters_enabled(config, http)
    definition_current = (
        collection is not None and record is not None and record.definition_hash == wanted
    )
    # The same exception the Common Sense family makes: an unchanged definition
    # is not on its own a reason to skip, because a collection whose poster fetch
    # failed on the pass that created it still carries a NULL ``poster_sha256``
    # and would otherwise never be revisited.
    if definition_current and not (posters_on and record.poster_sha256 is None):
        return actions

    if not definition_current:
        # C8, before anything is written, on BOTH paths and under dry_run too:
        # the probe is a read, and an operator previewing a pass should learn
        # that their filter matches nothing then rather than on the first
        # applied one.
        matched = require_matches(section, url)

        if dry_run:
            actions.append(
                "%s %r from a smart filter matching %d item(s)"
                % ("would update" if collection is not None else "would create", title, matched)
            )
        else:
            if collection is None:
                collection = create_smart_collection(section, libtype, title, url)
                # Back into the shared listing, exactly as ``lists.py:283``
                # does it: the map is the pass's, so a later definition
                # reading it has to see a collection this pass created rather
                # than a listing taken before it existed.
                listing[title] = collection
                collection.addLabel(label)
                actions.append(
                    "created %r as a smart collection (%d item(s) match now)"
                    % (title, matched)
                )
            else:
                update_smart_collection(section, collection, url)
                actions.append(
                    "updated the smart filter of %r (%d item(s) match now)"
                    % (title, matched)
                )

            if summary is not None:
                _edit_collection_summary(collection, summary)
            actions += apply_collection_settings(
                section, collection, settings, label, config
            )

            if record is None:
                record = ManagedCollection(
                    library=library, title=title, kind="smart",
                    plex_rating_key=str(getattr(collection, "ratingKey", "") or ""),
                    definition_hash=wanted,
                )
                session.add(record)
            else:
                record.definition_hash = wanted
                record.plex_rating_key = str(getattr(collection, "ratingKey", "") or "")
                # The row can predate this definition's SHAPE. ``shape_conflict``
                # tells an operator switching a definition from a list builder to
                # this one to delete the collection in Plex and let the next pass
                # create it -- and following that advice leaves the LIST
                # definition's row behind for this create to find, carrying
                # ``kind="manual"`` and the membership stamps that definition
                # wrote. Nothing below ever revisits them, so the row would
                # report a stale count as if a pass had just confirmed it, for a
                # collection whose membership is now Plex's. Stamping the kind
                # and clearing the stamps is what
                # keeps ``ManagedCollection``'s own promise about a smart row
                # ("both leave member_count and the reconcile stamps NULL") true
                # through the one path that can break it.
                record.kind = "smart"
                record.member_count = None
                record.last_added = None
                record.last_removed = None
                record.last_reconciled_at = None

    if posters_on and collection is not None and record is not None:
        # ``kind``/``key`` are None: a smart_filter definition has no builder to
        # derive default artwork from, so ``hosted_poster_url`` has nothing to
        # offer and only the operator's LOCAL override (keyed on library+title)
        # can supply one. That is the same shape a ``plex_id`` collection
        # already has -- ``BuilderResult.poster_kind`` is None there too -- and
        # it means the action string says "no poster source" on every pass until
        # a local file exists, which is the honest report.
        message = await apply_poster(
            session, http, config, collection, record, library, None, None,
            dry_run=dry_run,
        )
        if message:
            actions.append(message)

    await session.flush()
    return actions
