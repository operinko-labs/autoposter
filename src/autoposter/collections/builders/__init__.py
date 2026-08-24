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
    SmartContext,
    register,
)
from autoposter.collections.builders.cs_bucket import CsBucketBuilder
from autoposter.collections.builders.imdb_award import (
    ImdbAwardBuilder,
    ImdbAwardYearsBuilder,
)
from autoposter.collections.builders.imdb_chart import ImdbChartBuilder

register(PlexIdBuilder())
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
    "SmartContext",
    "register",
]
