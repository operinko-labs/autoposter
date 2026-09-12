from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class GatheredFacts:
    """What the providers said about one item, before it is written anywhere.

    Every field is optional: a provider may have nothing for this title, and a
    missing value must never be written to Plex as an empty one.
    """

    critic_rating: float | None = None
    audience_rating: float | None = None
    content_rating: str | None = None
    genres: list[str] = field(default_factory=list)
    studio: str | None = None
    originally_available: date | None = None
    # Roadmap rows 32 and 33a. NEITHER is persisted: there is no item_facts
    # column for either, because both are derived per pass from data already
    # fetched -- ``user_rating`` from the critic/audience values persist_facts
    # already stores, ``original_title`` from a TMDb payload the provider cache
    # already holds. A column would be a migration for a cache. They are
    # mass-op WRITE values, not badge inputs.
    user_rating: float | None = None
    original_title: str | None = None
    # Roadmap row 227, under the same rule the two lines above state: a
    # mass-op WRITE value, derived per pass from a TMDb response the provider
    # cache already holds (``/movie/{id}/release_dates``), so there is no
    # ``item_facts`` column for it and no migration. Kometa's
    # ``mass_added_at_update``, movie libraries only.
    added_at: date | None = None
    # Roadmap row 268, the same rule again: a mass-op WRITE value derived per
    # pass from ``/collection/{id}``, which the provider cache already holds
    # for ``tmdb_summary:`` definitions. No column, no migration.
    sort_title: str | None = None
    # The three prefetch fields (roadmap rows 189/192). Named OURS, never
    # Kometa's filter names, and enumeration-only: row 156 owns the question of
    # ever making a facts-backed value `filters:`-writable, and it needs a
    # `facts` source tier this project does not have. `tmdb_origin_country` is
    # a LIST because TMDb's is (a co-production carries several); the other two
    # are scalars.
    tmdb_origin_country: list[str] = field(default_factory=list)
    tmdb_original_language: str | None = None
    tmdb_collection_id: int | None = None
    # Roadmap row 100 sub-phase C2c, and the FIRST facts fields named for
    # Kometa's own FILTER vocabulary rather than "ours" (the three prefetch
    # fields above). That is not a break with row 156's law but its condition
    # being met: those three are enumeration-only because a facts-backed
    # filter needs a `facts` source tier, and C2c adds one
    # (`collections/filters.py::SOURCE_TIERS`) -- refusal-only on the
    # collections side, readable by an overlay `condition:`. Both carry
    # exactly the value space Kometa's filter compares in --
    # `tmdb_status` the `discover_status` TOKEN (`returning`, never
    # "Returning Series"; see `tmdb_facts.TMDB_SHOW_STATUS`) and
    # `last_episode_aired` TMDb's `last_air_date` -- so the name promises
    # what the field holds, which is the only thing row 156 ever asked for.
    tmdb_status: str | None = None
    last_episode_aired: date | None = None
    sources: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(
            (
                self.critic_rating is not None,
                self.audience_rating is not None,
                self.content_rating,
                self.genres,
                self.studio,
                self.originally_available,
                self.user_rating is not None,
                self.original_title,
                self.added_at,
                self.sort_title,
                self.tmdb_origin_country,
                self.tmdb_original_language,
                self.tmdb_collection_id is not None,
                self.tmdb_status,
                self.last_episode_aired,
            )
        )
