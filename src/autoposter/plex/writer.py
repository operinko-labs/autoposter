import asyncio
import logging
from urllib.parse import quote

from autoposter.facts.gather import format_audience, format_critic
from autoposter.facts.models import GatheredFacts

logger = logging.getLogger(__name__)

# plexapi raises AttributeError for fields a type does not carry: a season has
# no contentRating, studio, genres or originallyAvailableAt; an episode has no
# genres or studio.
WRITABLE_BY_KIND: dict[str, set[str]] = {
    "movie": {
        "critic_rating", "audience_rating", "content_rating",
        "genres", "studio", "originally_available",
    },
    "show": {
        "critic_rating", "audience_rating", "content_rating",
        "genres", "studio", "originally_available",
    },
    "season": {"critic_rating", "audience_rating"},
    "episode": {"critic_rating", "audience_rating", "content_rating",
                "originally_available"},
}


def _current_genres(item) -> list[str]:
    return [g.tag for g in getattr(item, "genres", []) or []]


def _genre_edits(current: list[str], target: list[str]) -> dict[str, object]:
    """Field/value pairs that set the genre list to exactly `target`.

    Verified empirically against the production server: a single-item
    indexed tag write (`genre[0].tag.tag=...`) does not replace the genre
    set, it merges with whatever genres the item already has -- the same
    merge behaviour previously found for label edits, now confirmed to
    also hold for genres on a single-item (non-batch) write. So setting
    the list exactly means adding what's missing and explicitly removing
    what's surplus, using the same indexed-tag wire format plexapi's own
    EditTagsMixin._tagHelper uses (also verified empirically to handle
    multi-word tag values correctly).
    """
    current_set = set(current)
    target_set = set(target)
    additions = [g for g in target if g not in current_set]
    removals = [g for g in current if g not in target_set]

    if not additions and not removals:
        return {}

    edits: dict[str, object] = {}
    for i, genre in enumerate(additions):
        edits[f"genre[{i}].tag.tag"] = genre
    if removals:
        edits["genre[].tag.tag-"] = ",".join(quote(str(g)) for g in removals)
    # Locked so Plex's own agent does not revert a value this tool owns.
    edits["genre.locked"] = 1
    return edits


def plan_edits(item, facts: GatheredFacts) -> dict[str, object]:
    """Field/value pairs that differ from what Plex already holds.

    Ratings compare on their *formatted* value, because that is what a viewer
    sees: 8.65 and 8.6 both render "86%", so rewriting one as the other would
    churn Plex for no visible gain.
    """
    writable = WRITABLE_BY_KIND.get(getattr(item, "type", "movie"), set())
    edits: dict[str, object] = {}

    def put(field: str, value: object) -> None:
        edits[f"{field}.value"] = value
        # Locked so Plex's own agent does not revert a value this tool owns.
        edits[f"{field}.locked"] = 1

    if "critic_rating" in writable and facts.critic_rating is not None:
        if format_critic(getattr(item, "rating", None)) != format_critic(facts.critic_rating):
            put("rating", facts.critic_rating)

    if "audience_rating" in writable and facts.audience_rating is not None:
        current = format_audience(getattr(item, "audienceRating", None))
        if current != format_audience(facts.audience_rating):
            put("audienceRating", facts.audience_rating)

    if "content_rating" in writable and facts.content_rating:
        if getattr(item, "contentRating", None) != facts.content_rating:
            put("contentRating", facts.content_rating)

    if "studio" in writable and facts.studio:
        if getattr(item, "studio", None) != facts.studio:
            put("studio", facts.studio)

    if "originally_available" in writable and facts.originally_available:
        formatted = facts.originally_available.strftime("%Y-%m-%d")
        current = getattr(item, "originallyAvailableAt", None)
        current_str = current.strftime("%Y-%m-%d") if hasattr(current, "strftime") else current
        if current_str != formatted:
            put("originallyAvailableAt", formatted)

    if "genres" in writable and facts.genres:
        current_genres = _current_genres(item)
        if sorted(current_genres) != sorted(facts.genres):
            edits.update(_genre_edits(current_genres, facts.genres))

    return edits


async def apply_facts(item, facts: GatheredFacts) -> dict[str, object]:
    """Write the changed fields in one HTTP call.

    plexapi routes even a single-item edit through the library section, so
    batching the fields together turns six writes into one.
    """
    edits = plan_edits(item, facts)
    if not edits:
        return {}

    def _write() -> None:
        item.batchEdits()
        item.edit(**edits)
        item.saveEdits()

    await asyncio.to_thread(_write)
    logger.info("plex: wrote %d field(s) to %s", len(edits), getattr(item, "type", "?"))
    return edits
