"""The builder whose source is the library itself.

``plex_all`` is the whole library as a collection, and its interest is that it
costs nothing. The engine builds an owned index for every pass anyway -- one
``section.all()``, the single most expensive call there is, measured at 1,954
movies in 2.8 seconds -- and ``SourceClients.plex`` hands a builder that same
lazy index rather than the section. So the "list every item" builder makes no
Plex request of its own, and the test that says so counts calls rather than
trusting the reading.

Order is the index's insertion order, which is ``section.all()``'s order,
which is the order Plex lists the library in. It is preserved because it
becomes the collection's custom order.

``plex_pilots`` (S1E1 of every show) is deliberately **not** here. It is the
one builder in this family whose ids are episodes, and the resolver cannot
see an episode: ``resolve.build_owned_index`` indexes ``section.all()``,
which on a Show library returns shows, so an episode rating key resolves to
nothing and the collection would be built empty every pass. Making it work is
an engine change (episode-aware resolution), not a builder, and it is filed
rather than improvised -- see the phase's roadmap row 56.
"""
import logging

from pydantic import BaseModel, ConfigDict

from autoposter.collections.builders.base import BuilderContext, BuilderResult
from autoposter.collections.default_images import RESOLUTION_KEYS

logger = logging.getLogger(__name__)

__all__ = ["PlexAllBuilder", "PlexAllParams", "PlexLibraryUnavailable"]


class PlexLibraryUnavailable(Exception):
    """The context carries no library accessor.

    Only a direct caller that built its own bundle sees this -- the engine
    binds ``SourceClients.plex`` for every library in the pass. Its own class
    so the engine's class-name-only log line says which client was missing,
    the same shape ``TmdbBuilderRefused`` has.
    """


class PlexAllParams(BaseModel):
    """``plex_all``'s params: none at all.

    An empty model rather than no model: declaring it is what makes
    ``params: {limit: 50}`` a config *load* error naming the definition,
    instead of a key that is silently ignored forever. The membership knobs
    live on the definition (``limit:``, ``sync_mode:``), not in ``params``.
    """

    model_config = ConfigDict(extra="forbid")


class PlexAllBuilder:
    """Every item the library owns, in the order Plex lists them."""

    type_name = "plex_all"
    params_model = PlexAllParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        PlexAllParams.model_validate(ctx.config)
        access = ctx.sources.plex
        if access is None:
            raise PlexLibraryUnavailable(
                "the 'plex_all' builder reads the library it is running "
                "against, and this context carries no library accessor"
            )
        # ``owned_index()`` and nothing else: no ``section()`` call, because
        # any request here would be a second walk of a library the engine has
        # already walked. ``test_plex_all_makes_no_plex_call_of_its_own``
        # counts on that.
        rating_keys = list((await access.owned_index())["plex"])
        logger.debug("plex_all: %d owned item(s)", len(rating_keys))
        # ``media_resolution`` is four definitions of THIS builder distinguished
        # only by their ``resolution`` filter, so the filter is where the
        # family identity lives and there is nowhere else to read it from. The
        # first value is the bucket's own key -- ``("4k", "8k")`` is "4k, with
        # 8k folded in", upstream's own addon merge -- and it is one of the
        # four labels ``Default-Images/resolution/`` names its files by
        # (measured against that repository). Any other filter, and a
        # bare ``plex_all``, keep no default artwork: this builder is "every
        # item the library owns", which upstream has no picture of.
        values = (getattr(ctx.definition, "filters", None) or {}).get("resolution")
        bucket = values[0] if isinstance(values, list) and values else None
        matched = bucket if bucket in RESOLUTION_KEYS else None
        return BuilderResult(
            ids=[("plex", key) for key in rating_keys],
            poster_kind="resolution" if matched else None,
            poster_key=matched,
        )
