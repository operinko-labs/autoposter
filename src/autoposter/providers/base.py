from dataclasses import dataclass

POSTER = "poster"
BACKGROUND = "background"
SEASON_POSTER = "season_poster"
TITLE_CARD = "title_card"
LOGO = "logo"

# Values that mean "this image carries no language tag" across providers.
TEXTLESS_LANGUAGE_TOKENS = {None, "", "xx", "00", "null"}


@dataclass(frozen=True)
class ArtRequest:
    """What artwork is wanted, for one item.

    Every provider takes this same object so the ladder can call them
    interchangeably; each picks the identifiers it understands and returns an
    empty list when the ones it needs are missing.
    """

    art_kind: str
    is_movie: bool
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    season_id: int | None = None
    # Roadmap row 45. Carried on the request rather than on the client so the
    # setting stays live: the Fanart response is cached whole and parsed
    # afterwards, so the same cached payload is simply read differently and no
    # cache key changes.
    prefer_clearart: bool = False


@dataclass(frozen=True)
class ArtCandidate:
    """One artwork option from one provider, before cross-provider ranking."""

    provider: str
    url: str
    language: str | None
    width: int | None
    height: int | None
    score: float
    includes_text: bool | None = None

    @property
    def is_textless(self) -> bool:
        """TVDB states textlessness outright; the others only imply it.

        ``includes_text`` is authoritative when present — an English-tagged TVDB
        artwork with ``includesText: false`` really is textless, and is a better
        pick than an untagged image that happens to have burned-in text.
        """
        if self.includes_text is not None:
            return not self.includes_text
        return self.language in TEXTLESS_LANGUAGE_TOKENS
