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
    version: str = ""
