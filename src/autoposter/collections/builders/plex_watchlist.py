"""The plex.tv watchlist, as a collection.

The only builder that talks to plex.tv rather than to the Plex server, and
the only one with a credential of its own. Both facts shape it:

- **The account token is not the server token.** ``AUTOPOSTER_PLEX_ACCOUNT_TOKEN``
  is a separate soft secret precisely because the configured ``plex_token``
  may be server-scoped (``plex/health.py`` carries the same warning), and
  assuming otherwise would turn a working deployment into a 401 mid-pass.
  Unset means ``SourceClients.plex_account`` is None and this builder raises,
  which the engine contains as one dead source.
- **The token never appears here.** ``sources.plex_account`` is a *factory*
  that closes over the token (``collections/service._plex_account_factory``),
  so this module holds a callable and never a credential -- there is nothing
  for a log line, an error message or a ``repr`` to leak. That is not an
  incidental property; it is why the bundle carries a factory rather than a
  string.

A watchlist is a plex.tv list, so its items are Plex Discover metadata rather
than anything this server owns. They are translated the way everything else
is: through their guids, into ``tmdb``/``tvdb``/``imdb`` ids the resolver can
match against the library's own index. The ``plex`` namespace is useless here
-- a Discover item's rating key belongs to plex.tv, not to this server.

Two judgements about missing data, and they are different on purpose. One
item with no usable guid is skipped with a debug line: an unresolvable entry
is the resolver's ordinary business, the same call ``mdblist_list`` and
``tvdb_list`` make. But *every* candidate having no usable guid is not an
unlucky watchlist, it is the guid children having stopped arriving on the
listing response -- which would empty a live collection -- so that raises.
"""
import asyncio
import logging

from pydantic import BaseModel, ConfigDict

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    require_library_type,
)
from autoposter.collections.ids import ExternalId
from autoposter.collections.resolve import GUID_PREFIXES

logger = logging.getLogger(__name__)

__all__ = [
    "PlexAccountNotConfigured",
    "PlexWatchlistBuilder",
    "PlexWatchlistDrift",
    "PlexWatchlistParams",
]

# The library type this pass runs against -> the plex.tv item type it can
# hold. A local mapping, the shape ``builders/tmdb.py``'s ``_DiscoverBuilder``
# uses, and it doubles as ``require_library_type``'s allowed set.
ITEM_TYPES: dict[str, str] = {"Movie": "movie", "Show": "show"}


class PlexAccountNotConfigured(Exception):
    """No plex.tv account token, so there is no watchlist to read.

    Its own class so the engine's class-name-only log line says which client
    was missing rather than "ValueError".
    """


class PlexWatchlistDrift(Exception):
    """Watchlist items arrived, and not one of them carried a usable guid.

    See the module docstring: individually that is ordinary, collectively it
    is the response shape having changed, and a builder that returned an
    empty list here would empty the collection on the next pass.
    """


class PlexWatchlistParams(BaseModel):
    """``plex_watchlist``'s params: none at all.

    Declared rather than omitted so a stray key is a config load error -- the
    same reason ``plex_all`` declares an empty model.
    """

    model_config = ConfigDict(extra="forbid")


def _fetch_watchlist(account_factory) -> list:
    """Construct the account and read its watchlist -- both plex.tv round
    trips, both synchronous. Run through ``asyncio.to_thread`` as one hop
    (``text_file``'s ``_read`` pattern) rather than on the event loop, where
    either would block every other task in the pass for their duration.
    """
    return account_factory().watchlist()


def _external_id(item: object) -> ExternalId | None:
    """The first usable namespaced id on a watchlist item, or None.

    Reads ``item.guids`` exactly the way ``resolve.build_owned_index`` reads
    an owned item's, through the same ``GUID_PREFIXES`` derived from
    ``NAMESPACES`` -- so the two sides of the match cannot drift apart into
    "the builder emits tvdb ids the index never records".
    """
    for guid in getattr(item, "guids", None) or []:
        value = getattr(guid, "id", "") or ""
        for namespace, prefix in GUID_PREFIXES.items():
            if value.startswith(prefix):
                return (namespace, value[len(prefix):])
    return None


class PlexWatchlistBuilder:
    """The account's plex.tv watchlist, in watchlist order."""

    type_name = "plex_watchlist"
    params_model = PlexWatchlistParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        PlexWatchlistParams.model_validate(ctx.config)
        require_library_type(
            "the 'plex_watchlist' builder", ctx.library_type, ITEM_TYPES
        )
        account_factory = ctx.sources.plex_account
        if account_factory is None:
            raise PlexAccountNotConfigured(
                "plex.tv account token not configured, so there is no watchlist "
                "to read. Set AUTOPOSTER_PLEX_ACCOUNT_TOKEN -- the configured "
                "Plex server token is not interchangeable with it; mint an "
                "account token with the PIN flow in `autoposter.plex.auth`."
            )
        wanted = ITEM_TYPES[ctx.library_type]

        ids: list[ExternalId] = []
        candidates = 0
        for item in await asyncio.to_thread(_fetch_watchlist, account_factory):
            if getattr(item, "type", None) != wanted:
                # A watchlist holds films and series together; the other half
                # of it belongs to the other library's pass.
                continue
            candidates += 1
            external = _external_id(item)
            if external is None:
                logger.debug(
                    "plex_watchlist: %r carries no tmdb/tvdb/imdb guid, skipping",
                    getattr(item, "title", item),
                )
                continue
            ids.append(external)

        if candidates and not ids:
            raise PlexWatchlistDrift(
                f"the plex.tv watchlist returned {candidates} {wanted} item(s) and "
                "not one of them carried a tmdb, tvdb or imdb guid; treating that "
                "as an empty collection would remove every member, so it is a "
                "failure instead"
            )
        return BuilderResult(ids=ids)
