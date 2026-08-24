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
    Namespace,
    PlexIdBuilder,
    PlexSectionAccess,
    SmartContext,
    SourceClients,
    register,
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
from autoposter.collections.builders.simple_ids import (
    ImdbIdBuilder,
    PlexRatingKeyBuilder,
    TmdbMovieBuilder,
    TmdbShowBuilder,
)
from autoposter.collections.builders.text_file import TextFileBuilder

register(PlexIdBuilder())
# Kometa's second name for the same builder -- see ``PlexRatingKeyBuilder``
# for why an alias is a registration rather than a second dict key.
register(PlexRatingKeyBuilder())
register(ImdbIdBuilder())
register(TmdbMovieBuilder())
register(TmdbShowBuilder())
register(TextFileBuilder())
register(RadarrAllBuilder())
register(RadarrTagListBuilder())
register(SonarrAllBuilder())
register(SonarrTagListBuilder())
register(ImdbChartBuilder())
register(ImdbAwardBuilder())
register(ImdbAwardYearsBuilder())
register(CsBucketBuilder())

__all__ = [
    "NAMESPACES",
    "REGISTRY",
    "Builder",
    "BuilderContext",
    "BuilderResult",
    "ExternalId",
    "Namespace",
    "PlexSectionAccess",
    "SmartContext",
    "SourceClients",
    "register",
]
