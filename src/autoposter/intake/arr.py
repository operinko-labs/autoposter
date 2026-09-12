import unicodedata
from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

# ``movieadded``/``seriesadd`` fire when the item is added to Radarr/Sonarr,
# which for an unreleased title is months before Plex can see anything. Those
# passes cannot succeed, and they are kept anyway: the queue defers such a job
# on an unbounded horizon rather than parking it (queue/jobs.py's ``fail()``),
# so the cost is a row that says "waiting for Plex" until the file lands. An
# operator who has turned the trigger off on the Arr side simply never sends
# one; dropping it here would instead break the operator who turns it back on.
RADARR_EVENTS = {"download", "rename", "movieadded"}
SONARR_EVENTS = {"download", "rename", "seriesadd"}


# ``events_log.event_type`` is String(64) (db/models.py:285) and the value is
# served verbatim by GET /api/events (api/snapshots.py:143,:152) and by the
# dashboard stream. An over-long or non-string ``eventType`` reached that
# column unchecked and the delivery died on the insert; so did a NUL, which
# the length and type checks alone let through. The cap below stops the
# first two; ``_reject_control_characters`` stops the third, but only for
# the six fields it is actually attached to below: ``eventType`` here, and
# ``_Movie``/``_Series`` ``title`` and ``imdbId``. Those six are refused
# before they can reach either this ``VARCHAR`` column or the ``JSONB`` one
# the accepted payload lands in (``tmdbId``/``tvdbId``/``year``/
# ``seasonNumber``/``episodeNumber`` need no such guard -- ``StrictInt``
# already refuses anything that is not an integer). Nothing else this module
# declares is sanitised, and no field of any other model is. Separately,
# ``routes.py``'s ``_without_nul`` strips a bare NUL -- not other control
# characters -- from the *stored* ``events_log.payload`` copy kept as
# evidence, keys and values alike, whatever the body's shape; that is a
# narrower, storage-only mechanism and has no bearing on what these
# validators let through to the parser.
EVENT_TYPE_MAX_CHARS = 64


def _reject_control_characters(value: str | None) -> str | None:
    """Refuse a Unicode control character (category ``Cc``, NUL included) or
    a line/paragraph separator, before the value can reach a column that
    cannot hold it.

    Length and type checks alone let a NUL through: ``Test\\x00`` is a valid
    5-character string. Postgres refuses NUL outright in both ``VARCHAR``
    (``events_log.event_type``) and ``JSONB`` (``events_log.payload``),
    which turns an otherwise-valid delivery into a 500 on the insert instead
    of a 400 at the gate. U+2028/U+2029 are not ``Cc`` but are refused for
    the same reason: neither has any business in an event name or a title.
    The raised message never names the value -- it is only ever logged via
    ``loc``, which carries field names, not the string that failed.
    """
    if value is None:
        return None
    if any(unicodedata.category(ch) == "Cc" or ch in (" ", " ") for ch in value):
        raise ValueError("control characters are not allowed")
    return value


class ArrEnvelope(BaseModel):
    """What every Radarr/Sonarr delivery carries, whatever the trigger fired.

    ``extra="ignore"`` rather than ``forbid``: Radarr 6 and Sonarr 4 add
    top-level keys across releases (``applicationUrl``, ``downloadClient``,
    ``customFormatInfo``), and a ``forbid`` here would turn the next point
    release of either into a 400 on every delivery. Only ``eventType`` is
    required, because only ``eventType`` is read before we know whether this
    delivery is work at all.

    ``extra="ignore"`` is also what makes the refusal log safe: with unknown
    keys dropped rather than reported, a ValidationError's ``loc`` can only
    ever name a field declared in this module -- never a key the sender chose.
    """

    model_config = ConfigDict(extra="ignore")

    eventType: Annotated[str, Field(min_length=1, max_length=EVENT_TYPE_MAX_CHARS)]

    _reject_control_event_type = field_validator("eventType")(_reject_control_characters)


def _lower_event_type(value: object) -> object:
    """Match ``eventType`` case-insensitively, exactly as the parsers below do.

    Non-strings pass through untouched so the ``Literal`` reports the type
    error itself rather than this helper raising AttributeError.
    """
    return value.lower() if isinstance(value, str) else value


class _Movie(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: Annotated[str, Field(min_length=1)]
    # StrictInt rather than int: pydantic's lax mode coerces "693134" to an
    # int, and a quoted id is precisely the shape a scanner produces and
    # Radarr never does. Optional, and 0 is legal -- the Test body sends
    # ``tmdbId: 0``, and ``_int_or_none`` below already reads a zero as absent.
    tmdbId: StrictInt | None = None
    imdbId: str | None = None
    year: StrictInt | None = None

    _reject_control_title = field_validator("title")(_reject_control_characters)
    _reject_control_imdb_id = field_validator("imdbId")(_reject_control_characters)


class RadarrPayload(ArrEnvelope):
    """A Radarr delivery this service will do work for.

    Applied only to an event in ``RADARR_EVENTS``: a Health or Grab body
    carries no ``movie`` key at all and has to stay a 200.
    """

    eventType: Literal["download", "rename", "movieadded"]
    movie: _Movie

    _casefold_event_type = field_validator("eventType", mode="before")(_lower_event_type)


class _Episode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    seasonNumber: StrictInt
    episodeNumber: StrictInt
    title: str | None = None

    _reject_control_title = field_validator("title")(_reject_control_characters)


class _Series(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: Annotated[str, Field(min_length=1)]
    tvdbId: StrictInt | None = None
    tmdbId: StrictInt | None = None
    imdbId: str | None = None
    year: StrictInt | None = None

    _reject_control_title = field_validator("title")(_reject_control_characters)
    _reject_control_imdb_id = field_validator("imdbId")(_reject_control_characters)


class SonarrPayload(ArrEnvelope):
    """A Sonarr delivery this service will do work for."""

    eventType: Literal["download", "rename", "seriesadd"]
    series: _Series
    # Absent on Rename and SeriesAdd, where the parser yields the show alone.
    # ``| None`` rather than a bare default: the Arr serialiser omits null
    # fields rather than emitting them, so no known build sends an explicit
    # ``"episodes": null`` -- but ``parse_sonarr`` already reads
    # ``payload.get("episodes") or []``, so tolerating one costs nothing here
    # and keeps a legitimate delivery from 400ing on a shape the model does
    # not actually need to forbid.
    episodes: list[_Episode] | None = None

    _casefold_event_type = field_validator("eventType", mode="before")(_lower_event_type)


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

    ``refs`` (server -> native id) is a hint, and is set only by the paths
    that already know one: the full pass and reprocess in ``api/routes.py``
    (from a ``media_items`` row we already resolved once), and discovery in
    ``arr/sync.py`` (from the Plex item it just listed). The webhook paths
    below leave it empty: Sonarr and Radarr know nothing about any server.
    """

    kind: str  # movie | show | season | episode
    title: str
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    year: int | None = None
    season_number: int | None = None
    episode_number: int | None = None
    refs: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict) -> "RenderIntent":
        """Decode a persisted job payload. Accepts the pre-refs shape, whose
        `rating_key` was the Plex id, so in-flight jobs survive the re-key."""
        data = dict(payload)
        legacy = data.pop("rating_key", None)
        refs = dict(data.pop("refs", None) or {})
        if legacy and "plex" not in refs:
            refs["plex"] = str(legacy)
        return cls(**data, refs=refs)

    def native_id_on(self, server: str) -> str | None:
        return self.refs.get(server)

    def __hash__(self) -> int:
        # ``refs`` is a dict -- unhashable -- so the auto-generated frozen-dataclass
        # hash (which would include it) is replaced with one over the other fields.
        return hash((self.kind, self.title, self.tmdb_id, self.tvdb_id, self.imdb_id,
                     self.year, self.season_number, self.episode_number))

    @property
    def dedupe_key(self) -> str:
        """Stable queue key. External ids are used because a server's native id is
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
