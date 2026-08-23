from dataclasses import dataclass

RADARR_EVENTS = {"download", "rename", "movieadded"}
SONARR_EVENTS = {"download", "rename", "seriesadd"}


def _int_or_none(value: object) -> int | None:
    """Arr omits null fields but *emits* zeros. Treat 0 as absent."""
    if isinstance(value, int) and value > 0:
        return value
    return None


def _str_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


@dataclass(frozen=True)
class RenderIntent:
    """One item whose artwork may need rebuilding.

    ``rating_key`` is the item's Plex identity, and is set only by the paths
    that build an intent from a ``media_items`` row we already resolved once
    (the full pass and reprocess in ``api/routes.py``). The webhook paths below
    leave it None: Sonarr and Radarr know nothing about Plex.
    """

    kind: str  # movie | show | season | episode
    title: str
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    year: int | None = None
    season_number: int | None = None
    episode_number: int | None = None
    rating_key: str | None = None

    @property
    def dedupe_key(self) -> str:
        """Stable queue key. External ids are used because the Plex rating key is
        not known until the job runs -- and it stays out of the key even when it
        *is* known, so that a job queued without one still dedupes against a
        later enqueue of the same item."""
        ident = (
            f"tmdb{self.tmdb_id}" if self.tmdb_id
            else f"tvdb{self.tvdb_id}" if self.tvdb_id
            else f"imdb{self.imdb_id}" if self.imdb_id
            else f"title{self.title.lower()}"
        )
        suffix = ""
        if self.season_number is not None:
            suffix = f":s{self.season_number:02d}"
            if self.episode_number is not None:
                suffix += f"e{self.episode_number:02d}"
        return f"process_item:{self.kind}:{ident}{suffix}"


def parse_radarr(payload: dict) -> list[RenderIntent]:
    """Radarr events all map to a single movie intent."""
    event = str(payload.get("eventType", "")).lower()
    if event not in RADARR_EVENTS:
        return []
    movie = payload.get("movie") or {}
    title = _str_or_none(movie.get("title"))
    if not title:
        return []
    return [
        RenderIntent(
            kind="movie",
            title=title,
            tmdb_id=_int_or_none(movie.get("tmdbId")),
            imdb_id=_str_or_none(movie.get("imdbId")),
            year=_int_or_none(movie.get("year")),
        )
    ]


def parse_sonarr(payload: dict) -> list[RenderIntent]:
    """A Sonarr event fans out to the show, each affected season, and each episode.

    A new episode can change season- and show-level artwork inputs, so all three
    levels are refreshed. Rename and SeriesAdd carry no ``episodes`` array and
    therefore yield only the show.
    """
    event = str(payload.get("eventType", "")).lower()
    if event not in SONARR_EVENTS:
        return []
    series = payload.get("series") or {}
    title = _str_or_none(series.get("title"))
    if not title:
        return []

    tvdb_id = _int_or_none(series.get("tvdbId"))
    tmdb_id = _int_or_none(series.get("tmdbId"))
    imdb_id = _str_or_none(series.get("imdbId"))
    year = _int_or_none(series.get("year"))

    intents = [
        RenderIntent(
            kind="show", title=title, tvdb_id=tvdb_id,
            tmdb_id=tmdb_id, imdb_id=imdb_id, year=year,
        )
    ]

    episodes = payload.get("episodes") or []
    seen_seasons: set[int] = set()
    episode_intents: list[RenderIntent] = []
    for episode in episodes:
        season_number = episode.get("seasonNumber")
        episode_number = episode.get("episodeNumber")
        if season_number is None or episode_number is None:
            continue
        if season_number not in seen_seasons:
            seen_seasons.add(season_number)
            intents.append(
                RenderIntent(
                    kind="season", title=title, tvdb_id=tvdb_id, tmdb_id=tmdb_id,
                    imdb_id=imdb_id, year=year, season_number=season_number,
                )
            )
        episode_intents.append(
            RenderIntent(
                kind="episode",
                title=_str_or_none(episode.get("title")) or title,
                tvdb_id=tvdb_id, tmdb_id=tmdb_id, imdb_id=imdb_id, year=year,
                season_number=season_number, episode_number=episode_number,
            )
        )
    intents.extend(episode_intents)
    return intents
