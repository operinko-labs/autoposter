"""The Common Sense age buckets, as a smart builder.

These are Plex-native smart collections: Plex evaluates the filter live, so
there is no membership for the engine to resolve or diff. The whole reconcile is
``collections/reconcile.py``'s, and this builder is the definition surface over
it -- so that one enumeration of definitions covers every collection this
service manages, which is what the leftovers report depends on.

It is deliberately a thin wrapper. Nothing about the bucket derivation, the
ownership rules or the separator moved: ``reconcile_content_ratings`` is called
with exactly the arguments ``reconcile_libraries`` used to pass it, plus the
definition itself -- which carries the per-definition collection settings
(labels, sort title, display mode, hub visibility) the reconciler applies to
each bucket it creates or updates.
"""
from autoposter.collections.buckets import derive_buckets
from autoposter.collections.builders.base import SmartContext
from autoposter.collections.reconcile import SEPARATOR_TITLE, reconcile_content_ratings


class CsBucketBuilder:
    """Every age bucket for one library, plus the family's separator."""

    type_name = "cs_bucket"
    # The engine's marker for "this one applies itself" -- see SmartContext.
    smart = True

    # Which ``CollectionDefinition`` fields this builder cannot apply, and why
    # (read at config load by ``config/schema.py``). ``summary`` and ``sort``
    # close roadmap row 140: neither was ever read for this family -- the
    # reconciler applies each bucket's OWN derived summary, and a family of
    # smart collections has no single membership to order -- so both silently
    # no-opped. This is load-time breaking for a config that sets either today,
    # which is what row 140 says it is.
    refused_definition_fields = {
        "summary": (
            "this definition names a FAMILY of collections and each one derives "
            "its own summary from the ratings it covers, so a single summary "
            "could not be the summary of any particular one of them"
        ),
        "sort": (
            "Plex evaluates each bucket's membership live, so there is no "
            "resolved order to set. The family's own ordering is what "
            "`sort_title` is for"
        ),
        "limit": (
            "Plex evaluates each bucket's membership from a filter, so there is "
            "no resolved list to cap"
        ),
        "sync_mode": (
            "Plex owns these collections' membership; there is nothing for this "
            "service to sync or append to"
        ),
        "item_label": (
            "this service never resolves these collections' members -- Plex "
            "does -- so there is no list of items to label"
        ),
        "tmdb_summary": (
            "this definition names a family of collections whose summaries the "
            "builder derives per collection, so one borrowed overview could not "
            "be the summary of any particular one of them"
        ),
        "filters": (
            "a `filters:` block narrows a membership this service resolved, and "
            "these are never resolved here -- the items are chosen inside Plex, "
            "by the bucket's own filter"
        ),
    }

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
            settings=ctx.definition,
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
