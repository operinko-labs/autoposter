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
            )
        )
