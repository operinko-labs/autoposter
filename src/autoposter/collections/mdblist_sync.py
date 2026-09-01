"""Roadmap row 31 -- push a collection's resolved membership to an MDBList list.

Opt-in twice. A definition has to name a list (``sync_to_mdb_list``, unset by
default) AND the deployment has to switch ``collections.mdblist_sync_apply``
on. One gate would be enough to be safe; two is what makes an outbound write to
a third-party service both unsurprising and dry-runnable -- with the definition
set and the switch off, the pass reports what it WOULD push, exactly as
``collections.apply_to_plex`` does for Plex. A ``dry_run`` pass never pushes
regardless of either.

What is pushed is the definition's OWN external ids for the members that
survived resolution, the filter and the limit -- not the Plex rating keys,
which mean nothing to MDBList, and not the raw source ids, which include
titles this library does not own.

**Additions only** (see ``MDBListClient.add_list_items``): this row is "push
membership out", not "make a third-party list a mirror this can empty".
"""
import logging

from autoposter.collections.resolve import _identity

logger = logging.getLogger(__name__)

# The namespaces MDBList understands. ``plex`` is deliberately absent: a rating
# key is this server's identity for an item and names nothing MDBList could
# resolve.
PUSHABLE_NAMESPACES = ("imdb", "tmdb", "tvdb")


def pushable_ids(index, ordered_ids, members) -> list[tuple[str, str]]:
    """The external ids naming ``members``, in the definition's own order.

    Derived by walking the source's ids and keeping those whose owned item
    survived to the final membership -- rather than reading ids back off the
    Plex objects, which would need a second guid pass over every item. One
    item is named once even when the source listed both its imdb and its tmdb
    id, matching ``resolve_external``'s own dedupe-by-item rule.
    """
    identities = {_identity(item) for item in members}
    out: list[tuple[str, str]] = []
    seen_ids: set[tuple[str, str]] = set()
    seen_items: set[object] = set()
    for external in ordered_ids:
        namespace, value = external
        if namespace not in PUSHABLE_NAMESPACES or external in seen_ids:
            continue
        seen_ids.add(external)
        item = index.get(namespace, {}).get(value)
        if item is None:
            continue
        identity = _identity(item)
        if identity not in identities or identity in seen_items:
            continue
        seen_items.add(identity)
        out.append(external)
    return out


def push_payload(ids: list[tuple[str, str]], is_movie: bool) -> dict:
    """MDBList's add-items body: one namespace-keyed object per member.

    Grouped under ``movies`` or ``shows`` because that is the shape MDBList's
    own list responses come back in (``parse_list_items``' two shapes), so the
    write mirrors the read rather than inventing a third.
    """
    return {
        "movies" if is_movie else "shows": [
            {namespace: value} for namespace, value in ids
        ]
    }


async def sync_membership(
    definition, members, index, ordered_ids, *, is_movie, client, apply
) -> list[str]:
    """Push this definition's membership, or report why nothing was pushed.

    Returns action strings for ``DefinitionResult.actions``. Never raises: the
    push is a side channel, and a third-party service being down must not fail
    a collection that applied to Plex perfectly well.
    """
    reference = getattr(definition, "sync_to_mdb_list", None)
    if not reference:
        return []
    if client is None:
        return [
            "%r: MDBList is not configured, so nothing was pushed to list %r"
            % (definition.title, reference)
        ]

    ids = pushable_ids(index, ordered_ids, members)
    if not ids:
        return [
            "%r: no member carries an id MDBList can resolve, so nothing was "
            "pushed to list %r" % (definition.title, reference)
        ]
    if not apply:
        return [
            "%r: would push %d member(s) to MDBList list %r "
            "(collections.mdblist_sync_apply is off)"
            % (definition.title, len(ids), reference)
        ]

    try:
        await client.add_list_items(reference, push_payload(ids, is_movie))
    except Exception as exc:  # noqa: BLE001 - see the docstring
        # CLASS NAME only on the served surface. An httpx error's message
        # carries the request URL and the URL carries apikey= as a query
        # parameter (facts C1.4). The full detail, with traceback, goes to the
        # pod log where credentials are already expected to appear.
        logger.exception(
            "%r: pushing %d member(s) to MDBList list %r failed",
            definition.title, len(ids), reference,
        )
        return [
            "%r: the push to MDBList list %r failed (%s); the collection "
            "itself was applied as usual"
            % (definition.title, reference, type(exc).__name__)
        ]
    return [
        "%r: pushed %d member(s) to MDBList list %r"
        % (definition.title, len(ids), reference)
    ]
