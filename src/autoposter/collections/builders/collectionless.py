"""``plex_collectionless``: the items in no collection on the server (row 91).

Kometa's is a plain list builder that reads `item.collections` per item --
one forced reload per library item per pass (`modules/plex.py`, the
collectionless branch), the exact trap phase 9a exists to avoid. This one
reads the engine's owned index (no walk of its own, `plex_all`'s
discipline) and ONE batched enrichment for the library, shared through the
run cache with every tier-2 filter definition in the pass.

`exclude` / `exclude_prefix` are Kometa's collectionless knobs: a membership
named there does not count against an item, so "collectionless" can mean
"in nothing but the Decade: buckets". Matching is exact for `exclude` and
`str.startswith` for `exclude_prefix`, both case-sensitive -- Plex
collection titles are canonical strings, and a looser match would silently
widen what gets ignored. (Our reading of upstream's semantics; the
startswith half is upstream's own idiom, the case-sensitivity is stated
here rather than checked against a fetch -- NOT KOMETA-VERIFIED.)

**One config typo is maximally wrong and completely silent, and it is
written down here because nothing refuses it.** `exclude_prefix: [""]`
matches every title -- `"anything".startswith("")` is true -- so every
membership stops counting, every item reads as collectionless, and this
builder returns the WHOLE LIBRARY. That membership is non-empty, so it is
applied: no refusal, no empty result, no log line to read. Every other
failure path here refuses and leaves the collection byte-identical; this
one does not. Closing it is a `min_length=1` on the prefix items, turning
it into a config-load error naming the definition -- the treatment
`extra="forbid"` above already gives an unknown key -- and it is left
undone rather than slipped in unrequested. Noted in roadmap row 91's
closing text too, so it survives that row closing.
"""
import logging

from pydantic import BaseModel, ConfigDict, Field

from autoposter.collections.builders.base import BuilderContext, BuilderResult
from autoposter.collections.builders.plex_trivial import PlexLibraryUnavailable
from autoposter.collections.enrichment import ensure_tags

logger = logging.getLogger(__name__)

__all__ = ["CollectionlessRefused", "PlexCollectionlessBuilder", "PlexCollectionlessParams"]


class CollectionlessRefused(Exception):
    """The enrichment could not answer for every owned item.

    Raised, never degraded: an item whose collections we could not read
    would otherwise be indistinguishable from an item in none, and this
    builder exists to assert the difference. The engine contains it as one
    dead source (class-name-only in the report), the pass continues.
    """


class PlexCollectionlessParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Collection titles that do not count as "being in a collection".
    exclude: list[str] = Field(default_factory=list)
    # Title prefixes that do not count -- the family-bucket idiom
    # ("Decade: ", "autoposter-facts: ").
    exclude_prefix: list[str] = Field(default_factory=list)


class PlexCollectionlessBuilder:
    """Every owned item whose (unexcluded) collection list is empty."""

    type_name = "plex_collectionless"
    params_model = PlexCollectionlessParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = PlexCollectionlessParams.model_validate(ctx.config)
        access = ctx.sources.plex
        if access is None:
            raise PlexLibraryUnavailable(
                "the 'plex_collectionless' builder reads the library it is "
                "running against, and this context carries no library accessor"
            )
        rating_keys = list((await access.owned_index())["plex"])
        tags = await ensure_tags(access.section(), ctx.run_cache, rating_keys)
        missing = [key for key in rating_keys if key not in tags]
        if missing:
            raise CollectionlessRefused(
                "the batched read answered for %d of %d item(s); an item "
                "whose collections cannot be read is not known to be "
                "collectionless, so nothing was built. Re-run the pass; if "
                "this repeats, items may be leaving the library mid-pass"
                % (len(rating_keys) - len(missing), len(rating_keys))
            )
        excluded = set(params.exclude)
        prefixes = tuple(params.exclude_prefix)

        def counts(title: str) -> bool:
            """Whether this membership counts against the item."""
            if title in excluded:
                return False
            return not any(title.startswith(prefix) for prefix in prefixes)

        members = [
            key for key in rating_keys
            if not any(counts(title) for title in tags[key].collections)
        ]
        logger.debug(
            "plex_collectionless: %d of %d item(s) in no (counted) collection",
            len(members), len(rating_keys),
        )
        return BuilderResult(ids=[("plex", key) for key in members])
