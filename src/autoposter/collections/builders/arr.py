"""The Radarr/Sonarr builders: the downloader's inventory, as a collection.

``radarr_all``/``sonarr_all`` are "everything the service manages", and the
tag-list forms are "everything it manages carrying these tags" -- which is
what makes them worth having, since a Radarr tag is a curation the operator
already maintains for the service's own reasons (a quality profile, a naming
scheme, a family shelf) and would otherwise have to maintain a second time in
this config.

No translation is needed: Radarr's ``tmdbId`` and Sonarr's ``tvdbId`` are
already the namespaces the resolver takes. What these builders add over
``client.listing()`` is three refusals.

- **An unconfigured service raises**, per ``SourceClients``: None means the
  deployment never set it up, and a builder that shrugged would leave a
  collection quietly empty rather than reporting a dead source.
- **An unknown tag name raises**, and names the tags that do exist. A tag that
  is not in the vocabulary matches nothing, and "matches nothing" is
  indistinguishable from a correct empty answer -- so it has to be caught
  where the name is, not where the result is. The available labels go in the
  message because they are the operator's own words and are what fixes the
  typo; nothing about a tag is a secret.
- **A non-2xx raises**, which is ``ArrClient``'s own rule and the reason the
  builders do nothing to soften it.

The tag vocabulary is fetched once per service per pass (``ctx.run_cache``).
A config can hold a dozen tag-list definitions and each one would otherwise
be its own ``/api/v3/tag`` round trip resolving the same handful of labels.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoposter.collections.builders.base import BuilderContext, BuilderResult

__all__ = [
    "ArrAllParams",
    "ArrBuilderRefused",
    "ArrTagListParams",
    "RadarrAllBuilder",
    "RadarrTagListBuilder",
    "SonarrAllBuilder",
    "SonarrTagListBuilder",
]


class ArrBuilderRefused(Exception):
    """This service could not answer, or was asked something it cannot mean.

    One class for both: the engine contains the definition and logs the class
    name with the traceback either way, and the message says which. Tag labels
    and service names are the only things it carries -- never a base URL,
    which is where an api key would be if anyone ever put one there.
    """


class ArrAllParams(BaseModel):
    """No params at all -- but declared, so a stray key is a load error.

    A model that accepts nothing is not the same as no model: a builder with
    no ``params_model`` has made no claim, and ``params: {tags: [kids]}`` on
    ``radarr_all`` would then load clean and be ignored forever.
    """

    model_config = ConfigDict(extra="forbid")


class ArrTagListParams(BaseModel):
    """Which tags, and whether an item needs one of them or all of them.

    ``any`` by default, which is the reading of a *list* of tags that surprises
    nobody -- and the one where adding a tag to the list can only grow the
    collection, never silently shrink it.
    """

    model_config = ConfigDict(extra="forbid")

    tags: list[str] = Field(min_length=1)
    match: Literal["any", "all"] = "any"


async def _tag_map(ctx: BuilderContext, client, service: str) -> dict[str, int]:
    """The service's label-to-id vocabulary, fetched at most once per pass.

    Keyed by service, because Radarr and Sonarr keep separate vocabularies in
    which the same id means different things.

    The failure is memoised as the exception and re-raised -- ``imdb_award``'s
    event memo's rule. Memoising only the success would mean a dead service is
    re-asked once per tag-list definition, which is the cost this memo exists
    to avoid, paid in full on exactly the pass that can least afford it.
    """
    key = f"arr.tags.{service}"
    if key not in ctx.run_cache:
        try:
            ctx.run_cache[key] = await client.tags()
        except Exception as error:  # noqa: BLE001 - memoised and re-raised below
            ctx.run_cache[key] = error
    tags = ctx.run_cache[key]
    if isinstance(tags, BaseException):
        raise tags
    return tags


class _ArrBuilder:
    """What the four builders share: the client, and how ids come out of it.

    ``service`` is the ``SourceClients`` field name and the memo key, and
    ``namespace`` is the id namespace that service's id field lives in.
    """

    service: str
    namespace: str

    def _client(self, ctx: BuilderContext):
        client = getattr(ctx.sources, self.service)
        if client is None:
            raise ArrBuilderRefused(
                f"{self.service} is not configured, so it has no list to build from"
            )
        return client

    def _result(self, client, entries: list[dict]) -> BuilderResult:
        # ``ordered_ids_in`` rather than ``ids_in``: the sync's set answers
        # "does the service hold this?", and a collection is an order.
        return BuilderResult(
            ids=[(self.namespace, value) for value in client.ordered_ids_in(entries)]
        )


class _ArrAllBuilder(_ArrBuilder):
    params_model = ArrAllParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        ArrAllParams.model_validate(ctx.config)
        client = self._client(ctx)
        return self._result(client, await client.listing())


class _ArrTagListBuilder(_ArrBuilder):
    params_model = ArrTagListParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ArrTagListParams.model_validate(ctx.config)
        client = self._client(ctx)
        wanted = await self._tag_ids(ctx, client, params.tags)
        entries = await client.listing()
        selected = [
            entry for entry in entries
            if _matches(client.tag_ids_in(entry), wanted, params.match)
        ]
        return self._result(client, selected)

    async def _tag_ids(self, ctx: BuilderContext, client, names: list[str]) -> set[int]:
        """The configured names as tag ids, or a refusal naming the miss.

        Matched case-insensitively: the label is typed twice, once in the
        service's own UI and once in this config, and neither end lower-cases
        anything for the operator.
        """
        labels = await _tag_map(ctx, client, self.service)
        folded = {label.casefold(): tag_id for label, tag_id in labels.items()}
        wanted = set()
        for name in names:
            tag_id = folded.get(name.strip().casefold())
            if tag_id is None:
                available = ", ".join(sorted(labels)) or "none at all"
                raise ArrBuilderRefused(
                    f"{self.service} has no tag named {name!r}; its tags are: "
                    f"{available}"
                )
            wanted.add(tag_id)
        return wanted


def _matches(entry_tags: set[int], wanted: set[int], match: str) -> bool:
    if match == "all":
        return wanted <= entry_tags
    return bool(entry_tags & wanted)


class RadarrAllBuilder(_ArrAllBuilder):
    """Every movie Radarr manages, in the order Radarr lists them."""

    type_name = "radarr_all"
    service = "radarr"
    namespace = "tmdb"


class SonarrAllBuilder(_ArrAllBuilder):
    """Every series Sonarr manages, in the order Sonarr lists them."""

    type_name = "sonarr_all"
    service = "sonarr"
    namespace = "tvdb"


class RadarrTagListBuilder(_ArrTagListBuilder):
    """The Radarr movies carrying the configured tags."""

    type_name = "radarr_taglist"
    service = "radarr"
    namespace = "tmdb"


class SonarrTagListBuilder(_ArrTagListBuilder):
    """The Sonarr series carrying the configured tags."""

    type_name = "sonarr_taglist"
    service = "sonarr"
    namespace = "tvdb"
