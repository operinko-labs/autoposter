"""The clients a builder is allowed to reach, gathered into one object.

``BuilderContext`` deliberately carries no application config and no raw
secrets, and that does not change here: a builder still cannot read a token,
a base URL or a Plex password. What it can now reach is a *constructed client*
-- already holding its own credential, its own cache and its own base URL --
handed down by the layer that had the config and the secrets to build it.
The credential never enters the builder's world; only the ability to ask a
service a question does.

Two rules make the bundle safe to widen the context with:

- **Absent means None, and None means raise.** A service the deployment never
  configured -- no MDBList key, Radarr disabled -- is None here rather than a
  stand-in that answers nothing. A builder that needs it raises, which the
  engine contains as one dead source (``engine._run_one``), so the operator
  reads "this definition failed" instead of watching a collection quietly
  never fill. A degrading stand-in is right for ``facts`` (a missing content
  rating is one field of many) and wrong here, where the client *is* the
  membership.
- **Nothing in the bundle is built by asking Plex or a provider anything.**
  ``plex_account`` is a *factory* rather than an account because
  ``MyPlexAccount(token=...)`` calls plex.tv in its constructor: building the
  bundle for a pass must not make a network call for a builder that may not
  run.

The bundle is built once per pass, where config, secrets and the shared HTTP
client all exist (``collections.service.build_source_clients``). The engine
then binds ``plex`` per library, because that one accessor is the only part of
the bundle that means something different for each library in the pass.
"""
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # imported for annotations only -- see the module docstring
    from plexapi.myplex import MyPlexAccount

    from autoposter.arr.client import ArrClient
    from autoposter.facts.mdblist import MDBListClient
    from autoposter.providers.tmdb_lists import TmdbListClient
    from autoposter.providers.tracearr import TracearrClient
    from autoposter.providers.tvdb import TVDBClient

__all__ = ["PlexSectionAccess", "SourceClients"]


class PlexSectionAccess:
    """The library, for the builders that genuinely need to read it.

    A narrow accessor rather than the section itself, and narrow for one
    reason: ``owned_index()`` is the engine's *lazy, shared* index, so a
    builder listing everything the library owns costs no extra
    ``section.all()`` -- the single most expensive call in a pass, and one the
    engine is already paying to resolve every builder's ids.

    ``section()`` is the plexapi section, for the traversals an index cannot
    answer (episodes, say). It is still read-only by convention: writing is
    the apply layer's, and a builder that wrote here would bypass every
    ownership and dry-run guard the engine exists to keep.
    """

    def __init__(self, section: object, owned_index: Callable[[], dict]):
        self._section = section
        self._owned_index = owned_index

    def section(self) -> object:
        """The plexapi ``LibrarySection`` this pass is running against."""
        return self._section

    def owned_index(self) -> dict:
        """``{namespace: {value: plex_item}}`` for the whole library.

        The engine's own index, built at most once per library per pass.
        """
        return self._owned_index()


@dataclass(frozen=True)
class SourceClients:
    """Every client a builder may reach, each None when unconfigured.

    Constructing one with no arguments is the "nothing is configured" bundle
    -- what a direct caller and every test gets, and what makes
    ``BuilderContext.sources`` safe to require: it is never None, so a builder
    checks the one client it needs and nothing else.
    """

    # None when no TMDb read access token is configured. Unlike the artwork
    # and facts clients, which degrade to "no images"/"no rating", a list
    # client with no token could only produce an empty collection.
    tmdb: "TmdbListClient | None" = None
    # None -- not ``NullMDBListClient`` -- when no API key is configured. The
    # Null client exists so *one metadata field* can go missing quietly; a
    # list builder given one would fail on an attribute instead of saying
    # MDBList is not configured.
    mdblist: "MDBListClient | None" = None
    tvdb: "TVDBClient | None" = None
    # None when that service is disabled or has no base URL configured.
    radarr: "ArrClient | None" = None
    sonarr: "ArrClient | None" = None
    # None when Tracearr is disabled, has no base URL, or no
    # AUTOPOSTER_TRACEARR_APIKEY is set. Absent means None and None means the
    # builder raises: a watch-history ranking with no history to read could
    # only produce an empty collection, which one layer down means "remove
    # every member".
    tracearr: "TracearrClient | None" = None
    # A factory, not an account: see the module docstring. None when
    # ``AUTOPOSTER_PLEX_ACCOUNT_TOKEN`` is unset -- the configured
    # ``plex_token`` may be server-scoped and must never be assumed to work
    # against plex.tv (``plex/health.py``'s refresh carries the same warning).
    plex_account: "Callable[[], MyPlexAccount] | None" = None
    # Not a client, and the one thing here that is not: the root of the mount
    # operators put their own files on. ``text_file`` reads a list from there
    # and still may not read the application config to find out where "there"
    # is, so the root arrives the same way a client does -- handed down by the
    # layer that had the config. A path is not a credential; what it would be
    # unsafe to hand a builder is the freedom to read *outside* it, which is
    # the builder's own containment check, not this field's.
    manual_assets_root: Path | None = None
    # Bound by the engine per library, so it is None on the pass-level bundle
    # and on any bundle a direct caller builds. A builder that needs the
    # library treats that like every other absent client and raises.
    plex: PlexSectionAccess | None = None
