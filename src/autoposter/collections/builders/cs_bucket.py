"""The Common Sense age buckets, as a smart builder.

These are Plex-native smart collections: Plex evaluates the filter live, so
there is no membership for the engine to resolve or diff. The whole reconcile is
``collections/reconcile.py``'s, and this builder is the definition surface over
it -- so that one enumeration of definitions covers every collection this
service manages, which is what the leftovers report depends on.

It is deliberately a thin wrapper. Nothing about the bucket derivation, the
ownership rules or the separator moved: ``reconcile_content_ratings`` is called
with exactly the arguments ``reconcile_libraries`` used to pass it.
"""
from autoposter.collections.buckets import derive_buckets
from autoposter.collections.builders.base import SmartContext
from autoposter.collections.reconcile import SEPARATOR_TITLE, reconcile_content_ratings


class CsBucketBuilder:
    """Every age bucket for one library, plus the family's separator."""

    type_name = "cs_bucket"
    # The engine's marker for "this one applies itself" -- see SmartContext.
    smart = True

    async def apply(self, ctx: SmartContext) -> list[str]:
        return await reconcile_content_ratings(
            ctx.session,
            ctx.section,
            ctx.library,
            ctx.library_type,
            ctx.label,
            dry_run=ctx.dry_run,
            adopt=ctx.config.collections.adopt,
            adopt_from=ctx.config.collections.adopt_from,
            adopt_removes_prior_label=ctx.config.collections.adopt_removes_prior_label,
            separators=ctx.config.collections.separators,
            protect_labels=ctx.config.collections.protect_labels,
            http=ctx.http,
            config=ctx.config,
        )

    def titles(self, library_type: str, config) -> set[str]:
        """Every title this builder manages for one library.

        ``derive_buckets`` only varies a bucket's *filter values* by the ratings
        actually present -- its titles come from the key/library-type pair alone
        -- so an empty ``present`` set recovers every bucket title without a
        Plex round trip.
        """
        titles = {bucket.title for bucket in derive_buckets(set(), library_type)}
        if config.collections.separators:
            titles.add(SEPARATOR_TITLE)
        return titles
