"""The builder registry, assembled here.

Importing this package is the *only* thing required to make every builder
reachable by name: the config validator resolves ``builder:`` through
``REGISTRY`` while validating, so registration cannot depend on some other
module having been imported first. Registration lives in this file rather than
in each builder module for the same reason -- one place to read, and no
ordering to get wrong.
"""
from autoposter.collections.builders.base import (
    NAMESPACES,
    REGISTRY,
    Builder,
    BuilderContext,
    BuilderResult,
    ExternalId,
    LibraryTypeMismatch,
    Namespace,
    PlexIdBuilder,
    PlexSectionAccess,
    SmartContext,
    SourceClients,
    register,
    require_library_type,
)
from autoposter.collections.builders.arr import (
    RadarrAllBuilder,
    RadarrTagListBuilder,
    SonarrAllBuilder,
    SonarrTagListBuilder,
)
from autoposter.collections.builders.cs_bucket import CsBucketBuilder
from autoposter.collections.builders.imdb_award import (
    ImdbAwardBuilder,
    ImdbAwardYearsBuilder,
)
from autoposter.collections.builders.imdb_chart import ImdbChartBuilder
from autoposter.collections.builders.imdb_lists import (
    ImdbListBuilder,
    ImdbWatchlistBuilder,
)
from autoposter.collections.builders.imdb_search import ImdbSearchBuilder
from autoposter.collections.builders.mdblist import MdblistListBuilder
from autoposter.collections.builders.plex_trivial import PlexAllBuilder
from autoposter.collections.builders.plex_watchlist import PlexWatchlistBuilder
from autoposter.collections.builders.simple_ids import (
    ImdbIdBuilder,
    PlexRatingKeyBuilder,
    TmdbMovieBuilder,
    TmdbShowBuilder,
)
from autoposter.collections.builders.text_file import TextFileBuilder
from autoposter.collections.builders.tmdb import (
    TmdbChartBuilder,
    TmdbCollectionBuilder,
    TmdbCompanyBuilder,
    TmdbKeywordBuilder,
    TmdbListBuilder,
    TmdbNetworkBuilder,
)
from autoposter.collections.builders.tmdb_discover import TmdbDiscoverBuilder
from autoposter.collections.builders.tmdb_person import (
    TmdbActorBuilder,
    TmdbCrewBuilder,
    TmdbDirectorBuilder,
    TmdbProducerBuilder,
    TmdbWriterBuilder,
)
from autoposter.collections.builders.tvdb import (
    TvdbListBuilder,
    TvdbMovieBuilder,
    TvdbShowBuilder,
)

register(PlexIdBuilder())
# Kometa's second name for the same builder -- see ``PlexRatingKeyBuilder``
# for why an alias is a registration rather than a second dict key.
register(PlexRatingKeyBuilder())
register(ImdbIdBuilder())
register(PlexAllBuilder())
register(TmdbMovieBuilder())
register(TmdbShowBuilder())
register(TextFileBuilder())
register(RadarrAllBuilder())
register(RadarrTagListBuilder())
register(SonarrAllBuilder())
register(SonarrTagListBuilder())
register(ImdbChartBuilder())
register(ImdbListBuilder())
register(ImdbWatchlistBuilder())
register(ImdbSearchBuilder())
register(PlexWatchlistBuilder())
register(TmdbChartBuilder())
register(TmdbListBuilder())
register(TmdbCollectionBuilder())
register(TmdbCompanyBuilder())
register(TmdbNetworkBuilder())
register(TmdbKeywordBuilder())
register(TmdbDiscoverBuilder())
# Kometa's five names for one filmography, five registrations of one build
# path -- see ``tmdb_person.ROLES``, the only thing that differs between them.
register(TmdbActorBuilder())
register(TmdbDirectorBuilder())
register(TmdbWriterBuilder())
register(TmdbProducerBuilder())
register(TmdbCrewBuilder())
register(MdblistListBuilder())
register(TvdbListBuilder())
register(TvdbMovieBuilder())
register(TvdbShowBuilder())
register(ImdbAwardBuilder())
# One registration per ceremony, because ``engine.definition_titles`` reads a
# year builder's ``TITLE_PATTERN`` off the registry entry with no definition in
# hand -- see ``imdb_award``'s module docstring. The static builder needs no
# such split: it takes its event as a param.
register(ImdbAwardYearsBuilder("oscars"))
register(ImdbAwardYearsBuilder("golden_globes"))
register(CsBucketBuilder())

__all__ = [
    "NAMESPACES",
    "REGISTRY",
    "Builder",
    "BuilderContext",
    "BuilderResult",
    "ExternalId",
    "LibraryTypeMismatch",
    "Namespace",
    "PlexSectionAccess",
    "SmartContext",
    "SourceClients",
    "register",
    "require_library_type",
]
