"""The per-run tier-2 enrichment cache (roadmap row 197).

One dict on the pass's ``run_cache`` (beside ``engine.run_library``'s other
scratch, engine.py:306), holding every ``ItemTags`` fetched so far this pass.
Scoped to the RESOLVED ITEM SETS callers actually ask for -- never the whole
library by default: a 40-item definition costs one request, and the
collectionless builder, whose resolved set IS the library, pays the measured
ceil(N/chunk) and no more. Three memos, per BuilderContext.run_cache's own
law that a failure must be memoised too:

- fetched tags (empty tag families included -- "found none" is an answer),
- keys Plex did not answer (so a gone item costs one fetch per pass),
- the pass-level failure (so a dead server costs one fetch per pass).
"""
import asyncio

from autoposter.plex.client import TAG_BATCH_CHUNK, ItemTags, fetch_tag_index

__all__ = ["EnrichmentUnavailable", "ensure_tags"]

_TAGS_KEY = "plex_enrichment:tags"
_MISSING_KEY = "plex_enrichment:missing"
_FAILED_KEY = "plex_enrichment:failed"


class EnrichmentUnavailable(Exception):
    """The batched read failed this pass. Class-name-only by construction:
    a plexapi failure's message can quote the tokenised URL."""


async def ensure_tags(
    section, run_cache: dict, rating_keys, chunk_size: int = TAG_BATCH_CHUNK
) -> dict[str, ItemTags]:
    """The run's tag cache, guaranteed to have been ASKED about every key in
    ``rating_keys``. A key still absent from the result after this returns is
    one Plex did not answer -- the caller's refusal to make, not this
    module's to hide.
    """
    failed = run_cache.get(_FAILED_KEY)
    if failed is not None:
        raise failed
    tags: dict[str, ItemTags] = run_cache.setdefault(_TAGS_KEY, {})
    missing: set[str] = run_cache.setdefault(_MISSING_KEY, set())
    wanted = [str(key) for key in rating_keys]
    to_fetch = [key for key in wanted if key not in tags and key not in missing]
    if not to_fetch:
        return tags
    try:
        fetched = await asyncio.to_thread(fetch_tag_index, section, to_fetch, chunk_size)
    except Exception as error:
        failure = EnrichmentUnavailable(
            "the batched Plex metadata read failed (%s); every definition "
            "needing tier-2 attributes is refused this pass. If Plex is up, "
            "check the chunk cap the phase-B probe measured (docs/research/"
            "plex-batch-probe/README.md)" % type(error).__name__
        )
        run_cache[_FAILED_KEY] = failure
        raise failure from None
    tags.update(fetched)
    missing.update(key for key in to_fetch if key not in fetched)
    return tags
