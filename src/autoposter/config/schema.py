import os
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

_LANG_RE = re.compile(r"^[a-z]{2}$")

_SECRET_ENV = {
    "database_url": "AUTOPOSTER_DATABASE_URL",
    "plex_token": "AUTOPOSTER_PLEX_TOKEN",
    "tmdb_token": "AUTOPOSTER_TMDB_TOKEN",
    "tvdb_apikey": "AUTOPOSTER_TVDB_APIKEY",
    "fanart_apikey": "AUTOPOSTER_FANART_APIKEY",
    "webhook_secret": "AUTOPOSTER_WEBHOOK_SECRET",
}


class Secrets(BaseModel):
    """Runtime secrets. Never read from the YAML config file."""

    database_url: str
    plex_token: str
    tmdb_token: str
    tvdb_apikey: str
    fanart_apikey: str
    webhook_secret: str
    # Soft secret, unlike the rest of this class: MDBList content ratings are
    # one field among several metadata operations gathers, so a deployment
    # without a key yet still runs, just without that one field. Defaults to
    # "" rather than being in _SECRET_ENV, which would hard-fail every boot.
    mdblist_apikey: str = ""

    @classmethod
    def from_env(cls) -> "Secrets":
        values = {}
        for field, env_name in _SECRET_ENV.items():
            value = os.environ.get(env_name)
            if not value:
                raise RuntimeError(f"required environment variable {env_name} is not set")
            values[field] = value
        values["mdblist_apikey"] = os.environ.get("AUTOPOSTER_MDBLIST_APIKEY", "")
        return cls(**values)


class TextStyle(BaseModel):
    """One text block. Mirrors a Posterizarr *OverlayPart section."""

    font: str = "Comfortaa-Medium.ttf"
    all_caps: bool = True
    font_color: str = "white"
    min_point_size: int
    max_point_size: int
    max_width: int
    max_height: int
    text_offset: str
    gravity: str = "south"
    line_spacing: int = 0
    add_text: bool = True
    add_stroke: bool = False
    stroke_color: str = "black"
    stroke_width: int = 6

    @field_validator("text_offset")
    @classmethod
    def _must_carry_sign(cls, v: str) -> str:
        if not v.startswith(("+", "-")):
            raise ValueError(
                f"text_offset {v!r} must carry an explicit sign, e.g. '+300' — it is "
                "concatenated after '+0' to form an ImageMagick -geometry argument"
            )
        return v


class ArtKindConfig(BaseModel):
    """One artifact type: whether to build it, its art sources and its text block."""

    enabled: bool = True
    language_order: list[str] = Field(default_factory=lambda: ["xx", "en", "fi"])
    overlay_file: str
    add_overlay: bool = True
    add_border: bool = False
    border_color: str = "white"
    border_width: int = 30
    min_width: int = 0
    min_height: int = 0
    text: TextStyle | None = None

    @field_validator("language_order")
    @classmethod
    def _valid_languages(cls, v: list[str]) -> list[str]:
        for code in v:
            if code != "xx" and not _LANG_RE.match(code):
                raise ValueError(
                    f"language code {code!r} is invalid: use 'xx' for textless "
                    "or a lowercase two-letter ISO-639-1 code"
                )
        return v


class TitleCardConfig(ArtKindConfig):
    """Title cards carry two independent text blocks."""

    episode_text: TextStyle | None = None
    season_label: str = "Season"
    episode_label: str = "Episode"
    skip_words: list[str] = Field(default_factory=lambda: ["TBA"])


class ArtworkConfig(BaseModel):
    poster: ArtKindConfig
    season_poster: ArtKindConfig
    background: ArtKindConfig
    title_card: TitleCardConfig
    use_logo: bool = True
    logo_language_order: list[str] = Field(default_factory=lambda: ["en", "fi"])
    logo_text_fallback: bool = False
    output_quality: str = "92%"


class ProvidersConfig(BaseModel):
    order: list[str] = Field(default_factory=lambda: ["TMDB", "TVDB", "Fanart"])
    favourite: str = "TMDB"
    tmdb_vote_sorting: str = "vote_average"
    # How long a provider response (including "nothing found") stays cached in
    # provider_cache. 0 disables caching entirely.
    cache_ttl_seconds: int = 24 * 3600


class PlexConfig(BaseModel):
    url: str
    excluded_libraries: list[str] = Field(default_factory=list)
    resolve_max_attempts: int = 10
    liveness_interval_seconds: int = 60
    token_refresh_interval_seconds: int = 12 * 3600
    token_refresh_enabled: bool = True


class OperationsConfig(BaseModel):
    """Per-item metadata operations, replacing Kometa's mass_*_update."""

    enabled: bool = True
    # Off means gather and store facts but leave Plex untouched — the safe
    # setting while the tool being replaced still owns these fields.
    write_to_plex: bool = True
    # How often the IMDb ratings dataset is polled in the background (see
    # facts/imdb.py's ImdbAutoRefresh). Also runs once at startup when
    # imdb_ratings is empty or older than this. IMDb rebuilds its datasets
    # once a day; polling every 6h picks up each day's build within 6h of
    # publication. Conditional requests (If-Modified-Since) mean most polls
    # transfer nothing when the file hasn't changed.
    imdb_refresh_hours: int = 6
    # Off disables the automatic refresh entirely; the manual
    # `python -m autoposter.facts.imdb` entry point still works.
    imdb_refresh_enabled: bool = True
    # When a rating lookup finds nothing during fact gathering, attempt one
    # extra refresh rather than waiting up to imdb_refresh_hours. Rate-limited
    # to one attempt per this many minutes (see facts/imdb.py's
    # ImdbMissRefresh) so a season-pack import cannot trigger one download
    # per episode. 0 disables miss-triggered refreshes entirely.
    imdb_miss_refresh_minutes: int = 60


class BadgesConfig(BaseModel):
    """Kometa-parity badge overlays, composited onto the base artwork."""

    enabled: bool = True
    # Dry run by default: compose and fingerprint, upload nothing. This is
    # the first thing in this project that writes images to the live Plex
    # server across ~16,000 items -- the operator should compose, inspect and
    # only then enable it, the same posture operations.write_to_plex takes.
    upload_to_plex: bool = False
    # Lock the Plex field after upload so the agent cannot reclaim it.
    lock_artwork: bool = True
    # Add the literal Plex label "Overlay", as the previous tool did. Off by
    # default: we track overlay state in Postgres so we do not need it, but
    # it is visible and filterable in Plex, so it is offered rather than
    # silently dropped.
    apply_overlay_label: bool = False


class CollectionsConfig(BaseModel):
    """Common Sense age-bucket smart collections, replacing Kometa's."""

    enabled: bool = True
    # Dry run by default, the same posture as operations.write_to_plex and
    # badges.upload_to_plex: reconciliation runs and reports, nothing is
    # written to Plex until the operator opts in.
    apply_to_plex: bool = False
    # The ownership boundary: only collections carrying this label are ever
    # created or modified. Must not be "Kometa" -- that is the label the tool
    # being replaced uses, and sharing it would make both tools claim the
    # same collections. Changing this after a run orphans every collection
    # created under the old label; they are left untouched, not renamed.
    ownership_label: str = "autoposter"
    libraries: list[str] = Field(default_factory=lambda: ["Movies", "TV Shows"])


class Config(BaseModel):
    assets_root: Path
    manual_assets_root: Path
    backup_root: Path
    fonts_root: Path
    overlays_root: Path
    library_folders: bool = True
    workers: int = 5
    settle_seconds: int = 30
    magick_binary: str = "magick"
    skip_tba: bool = True
    plex: PlexConfig
    providers: ProvidersConfig
    artwork: ArtworkConfig
    operations: OperationsConfig = Field(default_factory=OperationsConfig)
    badges: BadgesConfig = Field(default_factory=BadgesConfig)
    collections: CollectionsConfig = Field(default_factory=CollectionsConfig)
    version: str = ""
