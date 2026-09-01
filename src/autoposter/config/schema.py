import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

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

    database_url: str = Field(
        description=(
            "The connection string this service's database engine authenticates "
            "with. Set from AUTOPOSTER_DATABASE_URL; never read from the config file."
        ),
    )
    plex_token: str = Field(
        description=(
            "The Plex server token every request to Plex authenticates with. Set "
            "from AUTOPOSTER_PLEX_TOKEN; never read from the config file."
        ),
    )
    tmdb_token: str = Field(
        description=(
            "The TMDb API token metadata requests to TMDb authenticate with. Set "
            "from AUTOPOSTER_TMDB_TOKEN; never read from the config file."
        ),
    )
    tvdb_apikey: str = Field(
        description=(
            "The TVDB API key metadata requests to TVDB authenticate with. Set "
            "from AUTOPOSTER_TVDB_APIKEY; never read from the config file."
        ),
    )
    fanart_apikey: str = Field(
        description=(
            "The Fanart.tv API key art requests to Fanart.tv authenticate with. "
            "Set from AUTOPOSTER_FANART_APIKEY; never read from the config file."
        ),
    )
    webhook_secret: str = Field(
        description=(
            "The shared secret inbound webhook requests must present to be "
            "accepted. Set from AUTOPOSTER_WEBHOOK_SECRET; never read from the "
            "config file."
        ),
    )
    # Soft secret, unlike the rest of this class: MDBList content ratings are
    # one field among several metadata operations gathers, so a deployment
    # without a key yet still runs, just without that one field. Defaults to
    # "" rather than being in _SECRET_ENV, which would hard-fail every boot.
    mdblist_apikey: str = Field(
        default="",
        description=(
            "The MDBList API key content-rating lookups authenticate with. A "
            "deployment without one still runs, with content ratings unavailable."
        ),
    )
    # Soft secrets, same reasoning as mdblist_apikey: both radarr.enabled and
    # sonarr.enabled default to false, so a deployment that never configures
    # either service must still boot. Left empty, ArrClient's requests to
    # that service simply fail (and are caught and logged by the scheduled
    # job), rather than the whole process refusing to start.
    radarr_apikey: str = Field(
        default="",
        description=(
            "The Radarr API key Radarr requests authenticate with. A deployment "
            "without one still runs, with Radarr registration unavailable."
        ),
    )
    sonarr_apikey: str = Field(
        default="",
        description=(
            "The Sonarr API key Sonarr requests authenticate with. A deployment "
            "without one still runs, with Sonarr registration unavailable."
        ),
    )
    # Soft secret, same reasoning as mdblist_apikey: a deployment without it
    # must still boot, just with every Web UI login attempt 401ing -- see
    # api/auth.py and api/routes.py. Never a plaintext password, always a
    # bcrypt hash produced by hash_password().
    admin_password_hash: str = Field(
        default="",
        description=(
            "The bcrypt hash the Web UI's login checks against. A deployment "
            "without one still runs, with every login attempt rejected."
        ),
    )
    # Soft secret, same reasoning as mdblist_apikey, but for a different
    # reason than most of this class's other ones: the autoposter Harbor
    # project is public and internet-accessible (operator decision,
    # 2026-08-26), so the update check works with no credential at all --
    # an empty value means an anonymous request, not a disabled check. This
    # exists only for a deployment whose registry project is private, where
    # Harbor's artifact listing needs a robot account's credential. Already
    # base64 of `robot$name:secret`, ready to be the value of an
    # `Authorization: Basic` header when set; see api/version.py.
    harbor_token: str = Field(
        default="",
        description=(
            "The Harbor robot account credential the update check authenticates "
            "with. A deployment without one still runs the check anonymously; "
            "only needed when the registry project is private."
        ),
    )
    # Soft secret, same reasoning as mdblist_apikey: only the collection
    # builders that ask plex.tv about the *account* (the watchlist) need it,
    # and a deployment that builds no such collection must still boot.
    #
    # Deliberately a second token rather than reusing plex_token: that one is
    # the server's, and a server-scoped token is perfectly valid for every
    # other thing this service does while being rejected by plex.tv -- which
    # is exactly why plex/health.py's refresh is written never to let a
    # plex.tv failure touch anything. Minted by the PIN CLI
    # (``python -m autoposter.plex.auth``).
    plex_account_token: str = Field(
        default="",
        description=(
            "The plex.tv account token watchlist-based collection builders "
            "authenticate with. A deployment without one still runs, with those "
            "collections unavailable."
        ),
    )
    # Soft secret, same reasoning as mdblist_apikey: ``tracearr.enabled``
    # defaults to false, so a deployment that never configures Tracearr must
    # still boot. Left empty, ``build_source_clients`` builds no client at all
    # and every tracearr_most_watched definition reports itself failed while
    # the rest of the pass proceeds.
    tracearr_apikey: str = Field(
        default="",
        description=(
            "The Tracearr API key watch-history requests authenticate with. A "
            "deployment without one still runs, with Tracearr-sourced "
            "collections unavailable."
        ),
    )

    @classmethod
    def from_env(cls) -> "Secrets":
        values = {}
        for field, env_name in _SECRET_ENV.items():
            value = os.environ.get(env_name)
            if not value:
                raise RuntimeError(f"required environment variable {env_name} is not set")
            values[field] = value
        values["mdblist_apikey"] = os.environ.get("AUTOPOSTER_MDBLIST_APIKEY", "")
        values["radarr_apikey"] = os.environ.get("AUTOPOSTER_RADARR_APIKEY", "")
        values["sonarr_apikey"] = os.environ.get("AUTOPOSTER_SONARR_APIKEY", "")
        values["admin_password_hash"] = os.environ.get("AUTOPOSTER_ADMIN_PASSWORD_HASH", "")
        values["harbor_token"] = os.environ.get("AUTOPOSTER_HARBOR_TOKEN", "")
        values["plex_account_token"] = os.environ.get("AUTOPOSTER_PLEX_ACCOUNT_TOKEN", "")
        values["tracearr_apikey"] = os.environ.get("AUTOPOSTER_TRACEARR_APIKEY", "")
        return cls(**values)


class TextStyle(BaseModel):
    """One text block. Mirrors a Posterizarr *OverlayPart section."""

    font: str = Field(
        default="Comfortaa-Medium.ttf",
        description="The font file this text block is drawn with, found under fonts_root.",
    )
    all_caps: bool = Field(default=True, description="Upper-case the text before drawing it.")
    font_color: str = Field(
        default="white",
        description="The text fill color, an ImageMagick color name or hex value.",
    )
    min_point_size: int = Field(
        description=(
            "The floor the auto-fitter will shrink text to. Text that would need "
            "to go smaller reports truncation instead, and the render is "
            "abandoned unwritten -- Posterizarr never wrote a file in that case "
            "either."
        ),
    )
    max_point_size: int = Field(
        description="The largest point size the text is allowed to auto-fit to.",
    )
    max_width: int = Field(
        description="The text block's maximum width in pixels, the box auto-fitting sizes text within.",
    )
    max_height: int = Field(
        description="The text block's maximum height in pixels, the box auto-fitting sizes text within.",
    )
    text_offset: str = Field(
        description=(
            "The text block's vertical offset from gravity, an ImageMagick "
            "geometry value carrying an explicit sign, e.g. '+300' or '-50'. "
            "A negative value deliberately pushes the text block off-canvas "
            "to hide it entirely, which is how the title card's default "
            "hides its title line."
        ),
    )
    gravity: str = Field(
        default="south",
        description="The ImageMagick gravity the text block is anchored and offset from.",
    )
    line_spacing: int = Field(
        default=0,
        description="Extra spacing between lines of wrapped text, in pixels.",
    )
    add_text: bool = Field(default=True, description="Whether this text block is drawn at all.")
    add_stroke: bool = Field(
        default=False,
        description="Draw an outline behind the text fill, using stroke_color and stroke_width.",
    )
    stroke_color: str = Field(
        default="black",
        description="The text outline's color, an ImageMagick color name or hex value. Only used when add_stroke is on.",
    )
    stroke_width: int = Field(
        default=6,
        description="The text outline's width in pixels. Only used when add_stroke is on.",
    )
    # Roadmap row 42. Both empty by default, so prepare_text's output for an
    # untouched config is byte-identical to what it produced before.
    newline_on_symbols: list[str] = Field(
        default_factory=list,
        description=(
            "Characters that force the text onto a new line immediately after "
            "them, e.g. [':', '-']. A symbol at the very end of the text adds "
            "no trailing break."
        ),
    )
    newline_words: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "A manual line-break map applied before the text is measured: each "
            "key found in the text is replaced by its value, which may contain "
            "a newline. Matched before all_caps is applied, so the keys are "
            "written in the title's own casing."
        ),
    )

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

    enabled: bool = Field(default=True, description="Whether this artifact type is built at all.")
    language_order: list[str] = Field(
        default_factory=lambda: ["xx", "en", "fi"],
        description=(
            "The language preference order used when choosing this artifact's art "
            "and text, most preferred first. 'xx' means textless; only 'xx' or "
            "lowercase two-letter ISO-639-1 codes are accepted."
        ),
    )
    overlay_file: str = Field(
        description=(
            "The overlay image composited onto this artifact's art, found under "
            "overlays_root. Only used when add_overlay is on."
        ),
    )
    add_overlay: bool = Field(
        default=True,
        description="Whether the overlay image (overlay_file) is composited onto this artifact's base art.",
    )
    add_border: bool = Field(
        default=False,
        description="Whether a solid border is drawn around the finished image, using border_color and border_width.",
    )
    border_color: str = Field(
        default="white",
        description=(
            "The border's color, an ImageMagick color name or hex value. Only "
            "used when add_border is on."
        ),
    )
    border_width: int = Field(
        default=30,
        description=(
            "The border's width in pixels, shaved off the image before the "
            "frame is drawn back on. Only used when add_border is on."
        ),
    )
    min_width: int = Field(
        default=0,
        description=(
            "Accepted for Posterizarr-config compatibility (the minimum-width "
            "gate); not currently enforced by any code path."
        ),
    )
    min_height: int = Field(
        default=0,
        description=(
            "Accepted for Posterizarr-config compatibility (the minimum-height "
            "gate); not currently enforced by any code path."
        ),
    )
    text: TextStyle | None = Field(
        default=None,
        description=(
            "The text block drawn onto this artifact -- font, sizing and "
            "positioning. Unset means no text is drawn."
        ),
    )
    # Roadmap row 39. Off by default, so a manually supplied asset is still
    # styled exactly as it is today.
    skip_local_text_add: bool = Field(
        default=False,
        description=(
            "Do not draw text on this artifact when its base image came from a "
            "manually supplied local asset rather than a provider -- for assets "
            "that already carry their own title treatment."
        ),
    )
    # Roadmap row 47, the per-kind half. None -- not False -- so "this kind
    # says nothing" and "this kind says no" stay distinguishable and the
    # global switch can be overridden in both directions.
    disable_online_asset_fetch: bool | None = Field(
        default=None,
        description=(
            "Render this artifact from local assets only, making no provider "
            "request for it at all. Unset inherits artwork.disable_online_asset_fetch."
        ),
    )

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

    episode_text: TextStyle | None = Field(
        default=None,
        description=(
            "The title card's second text block, independent of text -- font, "
            "sizing and positioning. Unset means no text is drawn there."
        ),
    )
    season_label: str = Field(
        default="Season",
        description="The word printed before the season number on the title card, e.g. 'Season 1'.",
    )
    episode_label: str = Field(
        default="Episode",
        description="The word printed before the episode number on the title card, e.g. 'Episode 1'.",
    )
    skip_words: list[str] = Field(
        default_factory=lambda: ["TBA"],
        description=(
            "Episode titles that mean 'no title yet'. When skip_tba is on and an "
            "episode's title matches one of these (case-insensitive), no episode "
            "title text is drawn."
        ),
    )
    # Roadmap row 40. Independent of skip_tba: that switch owns the literal
    # skip_words list, this one owns a script test, and tying them together
    # would make one setting silently disable the other.
    skip_cjk_titles: bool = Field(
        default=False,
        description=(
            "Skip building a title card when the episode's title is written in "
            "Japanese or Chinese script -- Hiragana, Katakana or Han "
            "characters -- rather than transliterated."
        ),
    )
    # Roadmap row 43. One field, not two: "0" is the specials season, so the
    # specials wording is an entry here rather than a setting of its own.
    season_name_overrides: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "The text printed in place of 'Season N' on a title card, keyed by "
            "season number written as a string -- '0' is the specials season, "
            "e.g. {'0': 'Specials'}. A season not listed keeps season_label."
        ),
    )


class ArtworkConfig(BaseModel):
    poster: ArtKindConfig = Field(
        description="Movie/show poster art: whether it is built, its sources and its text block.",
    )
    season_poster: ArtKindConfig = Field(
        description="Season poster art: whether it is built, its sources and its text block.",
    )
    background: ArtKindConfig = Field(
        description="Background/fanart art: whether it is built, its sources and its text block.",
    )
    title_card: TitleCardConfig = Field(
        description=(
            "Episode title card art: whether it is built, its sources and its "
            "two text blocks (title and episode label)."
        ),
    )
    use_logo: bool = Field(
        default=True,
        description=(
            "Composite a clearlogo onto posters in place of the title text, "
            "when one is found. Off leaves posters textual."
        ),
    )
    logo_language_order: list[str] = Field(
        default_factory=lambda: ["en", "fi"],
        description=(
            "Language preference order used when selecting a clearlogo, the "
            "same rules as the artifact language_order fields."
        ),
    )
    logo_text_fallback: bool = Field(
        default=False,
        description=(
            "When no clearlogo is found for a poster, draw the title text "
            "instead. Off leaves the poster with neither logo nor text."
        ),
    )
    # Roadmap row 38. A dict of dicts, not a dict of models: the descriptions
    # walk cannot reach a model buried in a dict, and a field shaped that way
    # would be served as nothing (test_config_descriptions.py's container
    # guard).
    library_language_overrides: dict[str, dict[str, list[str]]] = Field(
        default_factory=dict,
        description=(
            "Per-library language preference orders, keyed by Plex library name "
            "and then by art kind ('poster', 'season_poster', 'background', "
            "'title_card'). An art kind not named keeps its own language_order, "
            "so a library can change its posters while its title cards keep "
            "leading with 'xx'."
        ),
    )
    # Roadmap row 47, the global half.
    disable_online_asset_fetch: bool = Field(
        default=False,
        description=(
            "Render every artifact from local assets only, making no provider "
            "request at all. An artifact with no local asset is skipped rather "
            "than fetched. Individual art kinds can override this either way."
        ),
    )
    # Roadmap row 48.
    season_episode_templates: bool = Field(
        default=False,
        description=(
            "Apply a show's SeasonTemplate and EpisodeTemplate manual assets to "
            "every season poster and title card beneath it that has no manual "
            "asset of its own. A season's or episode's own file always wins."
        ),
    )

    @field_validator("library_language_overrides")
    @classmethod
    def _valid_override_languages(
        cls, v: dict[str, dict[str, list[str]]]
    ) -> dict[str, dict[str, list[str]]]:
        for library, by_kind in v.items():
            for art_kind, codes in by_kind.items():
                if art_kind not in ("poster", "season_poster", "background", "title_card"):
                    raise ValueError(
                        f"library_language_overrides[{library!r}] names art kind "
                        f"{art_kind!r}, which is not one of poster, season_poster, "
                        "background, title_card"
                    )
                for code in codes:
                    if code != "xx" and not _LANG_RE.match(code):
                        raise ValueError(
                            f"language code {code!r} in "
                            f"library_language_overrides[{library!r}][{art_kind!r}] "
                            "is invalid: use 'xx' for textless or a lowercase "
                            "two-letter ISO-639-1 code"
                        )
        return v

    output_quality: str = Field(
        default="92%",
        description="The ImageMagick output quality (-quality) applied to every composite, e.g. '92%'.",
    )


class ProvidersConfig(BaseModel):
    order: list[str] = Field(
        default_factory=lambda: ["TMDB", "TVDB", "Fanart"],
        description=(
            "The providers tried, in order, when selecting artwork; the first "
            "to return usable art for the requested language wins. A name with "
            "no implementation is skipped."
        ),
    )
    favourite: str = Field(
        default="TMDB",
        description=(
            "Accepted for Posterizarr-config compatibility (the "
            "preferred-provider key); not currently read by any code path."
        ),
    )
    tmdb_vote_sorting: str = Field(
        default="vote_average",
        description=(
            "Accepted for Posterizarr-config compatibility (Posterizarr's TMDb "
            "vote-sorting key); not currently read by any code path."
        ),
    )
    cache_ttl_seconds: int = Field(
        default=24 * 3600,
        description=(
            'How long a provider\'s answer -- including "nothing found" -- stays in '
            "the provider cache. 0 disables provider caching entirely."
        ),
    )


class PlexConfig(BaseModel):
    url: str = Field(description="The Plex server's base URL this service manages.")
    excluded_libraries: list[str] = Field(
        default_factory=list,
        description="Plex libraries this service never touches -- skipped by every walk and sync.",
    )
    resolve_max_attempts: int = Field(
        default=10,
        description=(
            "How many times a job retries with backoff when Plex is "
            "unreachable, before it is parked."
        ),
    )
    liveness_interval_seconds: int = Field(
        default=60,
        description="How often the background health check pings the Plex server.",
    )
    token_refresh_interval_seconds: int = Field(
        default=12 * 3600,
        description="How often the Plex token is proactively refreshed.",
    )
    token_refresh_enabled: bool = Field(
        default=True,
        description="Whether the Plex token is proactively refreshed at all.",
    )


class OperationsConfig(BaseModel):
    """Per-item metadata operations, replacing Kometa's mass_*_update."""

    enabled: bool = Field(
        default=True,
        description=(
            "Whether per-item metadata operations run at all. Off skips gathering "
            "and storing facts entirely, and leaves Plex untouched."
        ),
    )
    write_to_plex: bool = Field(
        default=True,
        description=(
            "Write the gathered metadata to Plex. Off gathers and stores the facts "
            "but leaves Plex untouched -- the safe setting while another tool still "
            "owns these fields."
        ),
    )
    imdb_refresh_hours: int = Field(
        default=6,
        description=(
            "How often the IMDb ratings dataset is polled in the background, also "
            "run once at startup when the stored data is empty or older than this. "
            "IMDb rebuilds its datasets once a day, so this picks up each day's "
            "build within that many hours of publication."
        ),
    )
    imdb_refresh_enabled: bool = Field(
        default=True,
        description=(
            "Whether the background IMDb ratings refresh runs at all. Off disables "
            "it; the manual refresh entry point still works."
        ),
    )
    imdb_miss_refresh_minutes: int = Field(
        default=60,
        description=(
            "When a rating lookup finds nothing during fact gathering, attempt one "
            "extra refresh rather than waiting for the regular cadence, rate-limited "
            "to one attempt per this many minutes. 0 disables miss-triggered "
            "refreshes entirely."
        ),
    )
    # Roadmap row 35. Metadata WRITES only: an exempt item's facts are still
    # gathered and stored (badges read the stored row, not the write), and its
    # artwork is untouched. All three default empty, so an untouched config
    # exempts nothing.
    ignore_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Plex rating keys whose metadata this service never writes. Their "
            "facts are still gathered and stored; only the write to Plex is "
            "skipped."
        ),
    )
    ignore_imdb_ids: list[str] = Field(
        default_factory=list,
        description=(
            "IMDb ids whose metadata this service never writes, e.g. 'tt0133093'. "
            "Their facts are still gathered and stored; only the write to Plex is "
            "skipped."
        ),
    )
    ignore_labels: list[str] = Field(
        default_factory=list,
        description=(
            "Plex labels marking an item opted out of metadata writes -- the "
            "per-item escape hatch, e.g. 'skip_autoposter'. Matched "
            "case-insensitively, because Plex canonicalises label case."
        ),
    )
    # This section owns WHEN, not WHETHER, in the split ``SchedulerConfig``'s
    # docstring states: there is no ``tmdb_budget_enabled`` beside this,
    # because 0 already means that and two spellings of one setting is one
    # spelling too many.
    tmdb_backoff_seconds: int = Field(
        default=60,
        description=(
            "How long TMDb is left alone after it answers 429, when it gives no "
            "Retry-After of its own. Shared across every pod. 0 disables the "
            "shared cooldown entirely, so each 429 is simply that one request's "
            "failure."
        ),
    )


class BadgesConfig(BaseModel):
    """Kometa-parity badge overlays, composited onto the base artwork."""

    enabled: bool = Field(
        default=True,
        description="Whether badge overlays are rendered at all. Off skips all badge compositing.",
    )
    # Dry run by default: compose and fingerprint, upload nothing. This is
    # the first thing in this project that writes images to the live Plex
    # server across ~16,000 items -- the operator should compose, inspect and
    # only then enable it, the same posture operations.write_to_plex takes.
    upload_to_plex: bool = Field(
        default=False,
        description=(
            "Upload the composed, badged artwork to Plex. Off composes and "
            "fingerprints but uploads nothing -- the safe setting until the "
            "operator has inspected the output."
        ),
    )
    lock_artwork: bool = Field(
        default=True,
        description="Lock the Plex artwork field after upload, so another agent cannot reclaim it.",
    )
    # Add the literal Plex label "Overlay", as the previous tool did. Off by
    # default: we track overlay state in Postgres so we do not need it, but
    # it is visible and filterable in Plex, so it is offered rather than
    # silently dropped.
    apply_overlay_label: bool = Field(
        default=False,
        description=(
            "Add the literal Plex label 'Overlay' to badged items. Off by default "
            "since overlay state is already tracked in Postgres; offered because "
            "the label is visible and filterable directly in Plex."
        ),
    )
    # Before uploading a render this service has no badge_fingerprint for --
    # after adoption, after a database restore, or for anything never badged
    # here -- read the provenance out of the artwork Plex is already serving
    # and skip the upload if it is already the exact image we were about to
    # send. Uploaded artwork carries its fingerprint in EXIF ImageDescription
    # (see plex/exif.py), which makes the artwork, not the database, the
    # source of truth about what is in Plex. On by default: at cutover this
    # is the difference between ~16,000 needless uploads and none. Entirely
    # best-effort -- any failure reading provenance falls through to the
    # normal upload path.
    adopt_from_plex: bool = Field(
        default=True,
        description=(
            "Before uploading a render this service has no fingerprint for, read the "
            "provenance out of the artwork Plex is already serving and skip the "
            "upload when it is already that exact image. Best-effort: any failure "
            "falls through to a normal upload."
        ),
    )


class MaintenanceConfig(BaseModel):
    """Plex's own housekeeping operations, run on a schedule (roadmap row 36).

    WHETHER lives here; WHEN lives in ``SchedulerConfig.maintenance_days`` --
    the same split ``CleanupConfig.apply``/``scheduler.cleanup_days`` already
    uses, and the one ``SchedulerConfig``'s docstring states.

    All three default off. ``empty_trash`` most of all: Plex's own
    auto-empty-trash is switched off on this deployment, which is what makes a
    mass item disappearance mean "somebody did that deliberately". A
    default-on toggle here would quietly take that signal away.
    """

    clean_bundles: bool = Field(
        default=False,
        description=(
            "Ask Plex to clean its media bundles on a schedule, reclaiming disk "
            "space from artwork and metadata no library item refers to any more."
        ),
    )
    empty_trash: bool = Field(
        default=False,
        description=(
            "Ask Plex to empty each library's trash on a schedule, permanently "
            "removing items whose files are gone. Off leaves deleted items "
            "recoverable in Plex."
        ),
    )
    optimize: bool = Field(
        default=False,
        description="Ask Plex to optimize its database on a schedule.",
    )


class ScheduleGate(BaseModel):
    """When a definition is allowed to run, inside the one collections pass.

    Deliberately not a second scheduler: the reconcile job keeps its single
    cadence and a gated definition is simply skipped on the runs it does not
    match, leaving its collection untouched.
    """

    every_n_runs: int = Field(
        default=1, ge=1,
        description="Run this definition on every Nth collections pass; 1 (the default) means every pass.",
    )
    months: list[int] | None = Field(
        default=None,
        description=(
            "Restrict this definition to these calendar months (1-12), for "
            "seasonal collections (Kometa's date-window idiom). None means "
            "every month."
        ),
    )

    @field_validator("months")
    @classmethod
    def _valid_months(cls, v: list[int] | None) -> list[int] | None:
        for month in v or []:
            if not 1 <= month <= 12:
                raise ValueError(
                    f"month {month} is not a calendar month: use 1-12 "
                    "(1 = January)"
                )
        return v


# What each refusable field looks like when the operator did NOT write it. One
# table rather than one per builder, because the check is "did they write it",
# which needs the default -- and a per-builder list carrying its own defaults
# would be seven chances for two builders to disagree about what `sync` means.
_SMART_REFUSABLE_DEFAULTS: dict[str, object] = {
    "summary": None,
    "sort": "custom",
    "limit": None,
    "sync_mode": "sync",
    "item_label": [],
    "tmdb_summary": None,
    "filters": None,
}

# "no entry", distinct from every default above -- ``None`` is four of them.
_UNTABLED = object()


class CollectionDefinition(BaseModel):
    """One operator-configured collection: a builder plus how to apply it.

    ``builder`` is a registry key, validated against the live registry here --
    at config load -- rather than when the reconcile job eventually runs. A
    typo that only surfaced at run time would look like "that collection just
    stopped updating", hours later and in a log nobody is reading.
    """

    title: str = Field(description="The collection's title in Plex.")
    builder: str = Field(
        description="Which registered collection builder produces this collection's members.",
    )
    # The builder's own params. Untyped here on purpose: each builder validates
    # this through its own pydantic model (see collections/builders/base.py), so
    # the schema does not have to know every builder's shape.
    params: dict = Field(
        default_factory=dict,
        description="The parameters this collection's builder takes; each builder defines its own shape.",
    )
    # None = every library in collections.libraries. An explicit list narrows
    # this definition to those; [] would mean "no library at all", which is why
    # the default is None rather than [].
    libraries: list[str] | None = Field(
        default=None,
        description="Which libraries this definition applies to. None (the default) means every library in collections.libraries.",
    )
    summary: str | None = Field(
        default=None,
        description="A summary text that overrides what the builder would otherwise derive for this collection.",
    )
    sort: str = Field(
        default="custom",
        description="The order Plex applies to this collection's members, e.g. 'custom' (the default) or 'release'.",
    )
    sync_mode: Literal["sync", "append"] = Field(
        default="sync",
        description=(
            "'sync' (the default) makes the collection exactly the builder's "
            "output; 'append' only ever adds members, never removes them."
        ),
    )
    # Cap on members, applied after resolution. ge=1: a limit that could only
    # ever produce an empty collection is a mistake, and empty means "make no
    # changes" downstream, so it would not even fail visibly.
    limit: int | None = Field(
        default=None, ge=1,
        description="A cap on the collection's member count, applied after resolution.",
    )
    schedule: ScheduleGate | None = Field(
        default=None,
        description=(
            "Gates which collections passes this definition is allowed to "
            "run on; unset means every pass."
        ),
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Extra Plex labels applied to the collection, beyond collections.ownership_label.",
    )
    # Make ``labels`` (plus the ownership label) the authoritative set: any
    # other label on the collection is removed. Off by default because it is
    # the destructive reading of the same field, and it never touches a
    # protected or adopt_from label -- stripping a prior tool's marker is what
    # collections.adopt_removes_prior_label decides, deliberately and once.
    label_sync: bool = Field(
        default=False,
        description=(
            "Make labels (plus the ownership label) the authoritative set: "
            "any other label on the collection is removed."
        ),
    )
    # Labels applied to every RESOLVED MEMBER of the collection (Kometa's
    # item_label). Only ever added: a member the source stops naming is no
    # longer a member, and removing a label from it would be a write against
    # an item this definition no longer describes.
    item_label: list[str] = Field(
        default_factory=list,
        description="Plex labels applied to every resolved member of the collection. Only ever added, never removed.",
    )
    # The collection's Plex sort title, applied verbatim on create and kept in
    # sync afterwards. This is the whole string, not a prefix -- Kometa's
    # ``!110_<title>`` scheme is written out in full. A definition that names a
    # family of collections (a smart builder) gives all of them the same sort
    # title, which is exactly what that scheme is for: the family sorts as one
    # block, ordered by title inside it.
    sort_title: str | None = Field(
        default=None,
        description="The collection's Plex sort title, applied verbatim and kept in sync.",
    )
    # Plex's collection display mode. Named values only: the plexapi call
    # rejects anything else with a BadRequest mid-pass, which is a worse place
    # to learn about a typo than config load.
    collection_mode: Literal["default", "hide", "hideItems", "showItems"] | None = Field(
        default=None,
        description="Plex's collection display mode. Unset leaves Plex's current setting alone.",
    )
    # Row 68: pin the collection to a hub. None leaves Plex's current setting
    # alone -- these are Plex Pass features, and "off" is a different request
    # from "not managed by this definition".
    visible_library: bool | None = Field(
        default=None,
        description="Pin the collection to a hub on the library's recommendations. None leaves Plex's current setting alone.",
    )
    visible_home: bool | None = Field(
        default=None,
        description="Pin the collection to a hub on the home screen. None leaves Plex's current setting alone.",
    )
    visible_shared: bool | None = Field(
        default=None,
        description="Pin the collection to a hub for shared users. None leaves Plex's current setting alone.",
    )
    # Position among the library's managed recommendations, 0 = first. Only
    # meaningful once the collection is promoted to a hub by one of the
    # visible_* flags above.
    hub_priority: int | None = Field(
        default=None, ge=0,
        description="Position among the library's managed recommendations, 0 = first.",
    )
    # Row 30: take the summary from TMDB instead of writing one by hand -- the
    # id of the TMDB *collection* whose overview this collection borrows.
    # ``summary`` above still wins when both are set: a summary written out in
    # the config is an explicit choice, and a pull that silently overrode it
    # would be a setting that reads as applied and is not.
    tmdb_summary: int | None = Field(
        default=None, gt=0,
        description="The id of the TMDB collection whose overview this collection's summary is pulled from.",
    )
    # Row 96: post-builder filtering. A Kometa-shaped mapping of
    # ``attribute[.modifier]: value`` keys, plus nested ``any:``/``all:``
    # blocks -- ``{"year.gte": 2000, "content_rating": ["PG", "PG-13"]}``. The
    # engine evaluates it against the resolved items, between resolution and
    # ``limit``, so a cap counts the members that survived the filter.
    # Untyped here for the reason ``params`` is: the shape belongs to
    # ``collections.filters``, which validates it below. None rather than {},
    # because an empty mapping is a block an operator wrote and left empty and
    # the parser refuses that.
    filters: dict | None = Field(
        default=None,
        description=(
            "Post-builder filtering: an attribute[.modifier]: value mapping, "
            "plus nested any:/all: blocks, evaluated against the resolved "
            "members before limit is applied."
        ),
    )
    # Row 19 (Kometa's ``changes_webhooks``): a webhook this collection's
    # membership changes are POSTed to, in addition to whatever the global
    # ``notifications`` block does. A field on the definition rather than a
    # separate pattern list, for the reason ``labels`` and ``sync_mode`` are:
    # this is a property of one collection, and the operator already writes
    # that collection here. Deliberately NOT in ``config/live.FROZEN_SECTIONS``
    # -- the engine reads the definition off the live config on every pass, so
    # an edited URL applies at the next pass rather than the next restart. Only
    # ever sent to when ``notifications.enabled`` is true and a global
    # ``notifications.url`` is configured: the notifier is built once from it,
    # and serves every per-collection target. May embed a token in its path, so it is
    # never logged in full -- host only, the same rule the global URL has.
    # A smart-built collection never fires this: Plex owns smart membership,
    # so no pass ever computes a per-item delta for it. On a family
    # definition this rides along to every expanded member, so one pass POSTs
    # once per changed member -- worth knowing before pointing it at a
    # rate-limited target. The detail dict this event carries only reaches
    # the wire under ``notifications.mode: autoposter-v1``; ``apprise-json``
    # (the default) drops it.
    changes_webhook: str = Field(
        default="",
        description=(
            "A webhook this collection's membership changes are POSTed to, beside "
            "whatever the global notifications block does. Sent only while "
            "notifications.enabled is on and a global notifications.url is "
            "configured; a Plex-evaluated smart collection never fires it."
        ),
    )

    @field_validator("builder")
    @classmethod
    def _must_be_a_registered_builder(cls, v: str) -> str:
        # Imported here, not at module scope: the builders package reaches into
        # the rest of the collections package, which imports this module, so a
        # module-level import would be a cycle. Importing at validation time
        # also means the registry is populated by this very lookup -- no other
        # module has to have been imported first for `builder:` to resolve.
        from autoposter.collections.builders import REGISTRY

        if v not in REGISTRY:
            raise ValueError(
                f"unknown collection builder {v!r}: known builders are "
                + ", ".join(sorted(REGISTRY))
            )
        return v

    @model_validator(mode="after")
    def _params_must_satisfy_the_builders_own_model(self) -> "CollectionDefinition":
        """A builder that declares ``params_model`` is held to it here.

        Same argument as ``builder`` above, one level down: a mis-spelled
        param used to be caught by the builder itself, mid-pass, hours after
        the edit -- where the engine contains it as a dead source and the only
        symptom is one collection quietly not being built. The model is a
        statement of what the builder takes, so the config can be checked
        against it at the moment it loads.

        The builders keep validating ``ctx.config`` themselves. This is not a
        replacement for that: a builder is also called directly (tests, and
        the expanded definitions an expanding builder constructs), and
        defence in depth is cheap when the check is a model that already
        exists.

        Two things are deliberately not checked. A builder with no
        ``params_model`` has made no claim about its params, so there is
        nothing to hold it to. And an *expanding* builder's model describes
        the units it expands into, not the placeholder an operator writes --
        ``imdb_award_years`` takes a year, and no placeholder can name one,
        which is the whole reason it expands. That second exemption is
        narrowed to an **empty** ``params``, though: the engine never reads a
        placeholder's params at all (``expand`` builds its own), so anything
        an operator puts there is never even a runtime error -- it is
        silently ignored forever. Exempting the whole dict would let
        ``params: {garbage: 1}`` on ``imdb_award_years`` load clean and stay
        wrong indefinitely; the shipped placeholder ships with ``params: {}``,
        which still loads.
        """
        from autoposter.collections.builders import REGISTRY

        builder = REGISTRY.get(self.builder)
        model = getattr(builder, "params_model", None)
        if model is None or (hasattr(builder, "expand") and not self.params):
            return self
        try:
            model.model_validate(self.params)
        except ValidationError as error:
            # The title says which collection and the field says which key.
            # An operator with twenty definitions needs both to fix one.
            details = "; ".join(
                "%s: %s" % (
                    ".".join(str(part) for part in item["loc"]) or "params", item["msg"]
                )
                for item in error.errors()
            )
            raise ValueError(
                f"{self.title!r} does not configure the {self.builder!r} builder "
                f"correctly -- {details}"
            ) from error
        return self

    @model_validator(mode="after")
    def _smart_definitions_refuse_what_they_cannot_apply(self) -> "CollectionDefinition":
        """A smart builder is held to its OWN table of inapplicable fields.

        Until 9c this was one hard-coded list, which was right while
        ``cs_bucket`` was the only smart builder and wrong the moment a second
        one arrived with a different shape: ``cs_bucket`` names a FAMILY of
        collections, so it can apply no single ``summary``; ``smart_filter``
        names exactly one, so it can. A shared list would have to refuse the
        union (a setting that works, refused) or accept the intersection (a
        setting that reads as applied and is not) -- and the second is the
        failure roadmap row 140 filed.

        So the table moves onto the builder, as ``refused_definition_fields``,
        and this validator is the mechanism. A smart builder that declares none
        is refused outright rather than defaulted to empty: the default that
        matters here is "refuse nothing", and reaching it by forgetting a class
        attribute is exactly how a silently-ignored setting ships.

        ``sort_title``, ``collection_mode`` and the ``visible_*`` flags are in
        no builder's table, deliberately. They are properties of the collection
        OBJECT rather than of its membership, and every smart create path
        applies them (roadmap row 104).
        """
        from autoposter.collections.builders import REGISTRY

        builder = REGISTRY.get(self.builder)
        if not getattr(builder, "smart", False):
            return self
        refused = getattr(builder, "refused_definition_fields", None)
        if refused is None:
            raise ValueError(
                f"{self.builder!r} is registered as a smart builder but declares "
                "no 'refused_definition_fields'. Every smart builder has to say "
                "which definition fields it cannot apply, because the failure "
                "mode of not saying is a setting that reads as applied and "
                "never is"
            )
        for field_name, why in refused.items():
            default = _SMART_REFUSABLE_DEFAULTS.get(field_name, _UNTABLED)
            if default is _UNTABLED:
                # Unreachable today (both builders' key sets are subsets), and
                # spelled out anyway: the alternative is a bare KeyError at
                # config load, three lines after a branch that goes to real
                # trouble to explain the adjacent mistake.
                raise ValueError(
                    f"{self.builder!r} refuses {field_name!r}, which is not in "
                    "the refusable-field table. A smart builder can only refuse "
                    "a field whose unwritten value this validator knows, since "
                    "the check is 'did the operator write it'. Add it to "
                    "'_SMART_REFUSABLE_DEFAULTS' with the field's own default"
                )
            if getattr(self, field_name) != default:
                raise ValueError(
                    f"{field_name!r} does not apply to {self.builder!r}: {why}"
                )
        return self

    @model_validator(mode="after")
    def _hub_priority_needs_a_promotion(self) -> "CollectionDefinition":
        """``hub_priority`` is "only meaningful once the collection is
        promoted to a hub by one of the visible_* flags above" (see that
        field's comment) -- and that is not just documentation. plexapi's
        ``ManagedHub.move`` raises ``BadRequest`` on a hub that has never been
        promoted (pinned in
        ``test_managed_hub_move_requires_the_hub_to_be_promoted``), and
        ``visibility()`` synthesises exactly that unpromoted hub for a
        collection with none. A definition setting ``hub_priority`` alone
        would fail on its very first pass -- caught here, at config load,
        with the real cause, instead of surfacing as a misleading "Plex Pass"
        report mid-run.
        """
        if self.hub_priority is not None and all(
            flag is None
            for flag in (self.visible_library, self.visible_home, self.visible_shared)
        ):
            raise ValueError(
                "'hub_priority' requires at least one of 'visible_library', "
                "'visible_home' or 'visible_shared' to promote the collection "
                "to a managed hub first"
            )
        return self

    @model_validator(mode="after")
    def _filters_must_parse_and_be_readable(self) -> "CollectionDefinition":
        """A ``filters:`` block is parsed, typed and tier-checked here.

        Two refusals, both at load, both naming the definition and the key --
        the params discipline above, one field along:

        - the PARSE (``filters.parse_filters``) rejects an unknown attribute, a
          modifier the attribute's type does not take, an unparseable value and
          a broken regex. Its own message carries the dotted key; the title is
          added here, because an operator with twenty definitions needs both
          halves to fix one.
        - the SOURCE TIER. This half cannot be left to the parser and is the
          reason this validator is not three lines long. The parser's
          vocabulary is the attribute TABLE, and the table deliberately carries
          rows the item view will not read: ``network`` is one of the fifteen
          tier-1 names, it parses cleanly, and Phase 9a's probe found Plex
          1.43.4 does not emit the attrib at all -- so it has no accessor
          (``filter_values.SHIPPED_ATTRIBUTES``) and evaluating it raises
          ``AttributeNotInListing``. Without this check ``network: E4`` would
          load green and fail per item, mid-pass, inside a run nobody is
          watching -- the silent-until-it-runs failure that config validation
          exists to prevent, and the engine would contain it into a collection
          that quietly stopped updating.

        The source-tier half has THREE outcomes since phase B, not two: a
        ``listing`` row ships free, a ``tier2-batched`` row ships through the
        engine's enrichment pass (one batched
        ``/library/metadata/{k1,k2,...}`` read per definition, taken before
        evaluation), and a ``tier2-deferred`` or ``unprobed`` row still
        refuses. The batched rows load HERE without any further check because
        what makes them safe is not this validator but the engine's refusal
        law -- an item the batch did not answer for refuses the definition
        rather than evaluating with a silently-missing value.

        The refusal copy branches on the source tier, because the remaining
        tiers mean different things. ``tier2-deferred`` -- ``network`` alone
        now -- cites a probe verdict, and specifically the one the batched read
        cannot overturn: Plex emits the attrib nowhere. ``unprobed`` says there
        is no verdict at all, which is the honest answer for ``country`` and
        the four people rows, and points at the ``plex_search`` builder
        instead (``plays``/``last_played`` were the exemplars here until phase
        B's probe (d) measured them onto ``listing``). A
        ``search-only`` row never reaches this loop at all -- ``parse_filters``
        refuses it one layer up, with the message that names ``plex_search``.

        Checked against ``SHIPPED_ATTRIBUTES`` and ``BATCHED_ATTRIBUTES`` --
        the tier-derived sets (rows in ``FILTER_ATTRIBUTES`` whose ``source``
        is ``"listing"`` / ``"tier2-batched"``), not against the runtime
        accessor maps themselves. Those are pinned equal by separate tests,
        ``filter_values``'s own
        ``test_the_runtime_accessor_map_matches_the_listing_rows`` and
        ``test_the_runtime_batched_field_map_matches_the_batched_rows``, not by
        this check introspecting them directly -- so a row moved between tiers
        without the matching accessor work fails there, not here.
        """
        if self.filters is None:
            return self
        from autoposter.collections.filter_values import (
            BATCHED_ATTRIBUTES,
            SHIPPED_ATTRIBUTES,
        )
        from autoposter.collections.filters import parse_filters, predicates

        try:
            parsed = parse_filters(self.filters)
        except ValueError as error:
            raise ValueError(
                f"{self.title!r} does not configure 'filters' correctly -- {error}"
            ) from error

        for predicate in predicates(parsed):
            row = predicate.attribute
            if row.name in SHIPPED_ATTRIBUTES or row.source == "tier2-batched":
                continue
            if row.source == "tier2-deferred":
                # NOT the pre-phase-B sentence, which cited the listing's
                # incompleteness: the batched read answers that for the five
                # rows it moved, so repeating it here would send an operator
                # looking for a fix that already shipped. What is true of the
                # one row left is stronger and simpler.
                why = (
                    "Plex 1.43.4 emits it nowhere -- 9a's probe found 0/284 shows "
                    "carry it in the listing AND it is absent from "
                    "/library/metadata, so no batched read can answer it either "
                    "(the batched tier that phase B opened feeds genre/label/"
                    "collection/audio_language/subtitle_language, not this). A "
                    "TVDb/TMDb-sourced equivalent would be a different attribute "
                    "under a distinct name"
                )
            else:
                # ``unprobed``. Deliberately NOT the sentence above: that one
                # cites a probe verdict, and for these rows there is none. 9a
                # probed the seven attributes its own tier named and no others,
                # so claiming a finding here would be the same
                # confident-and-wrong failure the probe exists to prevent.
                why = (
                    "Phase 9a never probed whether the Plex section listing carries "
                    "it, so there is no verdict either way and this service will "
                    "not guess -- it is searchable today through the 'plex_search' "
                    "builder, which asks the server instead"
                )
            raise ValueError(
                f"{self.title!r} cannot filter on {row.name!r} at {predicate.field}: "
                f"that attribute's source tier is {row.source!r}. {why}. "
                "Filterable today: "
                + ", ".join(SHIPPED_ATTRIBUTES + BATCHED_ATTRIBUTES)
            )
        return self


class CollectionsConfig(BaseModel):
    """Every collection this service builds and owns in Plex.

    The Common Sense age-bucket smart collections that replace Kometa's are
    one source among several: the built-in IMDb charts and Oscars awards
    (``charts``/``awards``), the blank divider (``separators``), whichever
    catalog ``presets`` an operator has switched on, and any
    number of operator ``definitions`` built by a registered builder
    (``collections/builders``). All of them share the same ownership,
    adoption, protection, poster and delete-sweep rules configured below.
    """

    enabled: bool = Field(
        default=True,
        description="Whether this service builds and manages any collections at all.",
    )
    # Dry run by default, the same posture as operations.write_to_plex and
    # badges.upload_to_plex: reconciliation runs and reports, nothing is
    # written to Plex until the operator opts in.
    apply_to_plex: bool = Field(
        default=False,
        description="Actually write collection changes to Plex; off only reports what reconciliation would do.",
    )
    # The ownership boundary: only collections carrying this label are ever
    # created or modified. Must not be "Kometa" -- that is the label the tool
    # being replaced uses, and sharing it would make both tools claim the
    # same collections. Changing this after a run orphans every collection
    # created under the old label; they are left untouched, not renamed.
    ownership_label: str = Field(
        default="autoposter",
        description="The Plex label marking a collection as owned by this service; only labelled collections are ever created or modified.",
    )
    libraries: list[str] = Field(
        default_factory=lambda: ["Movies", "TV Shows"],
        description="Which Plex libraries this service builds and manages collections in.",
    )
    charts: bool = Field(
        default=True,
        description="Build the IMDb Popular, Top 250 and Lowest Rated chart collections.",
    )
    awards: bool = Field(
        default=True,
        description="Build the Oscars winners collection (movies only).",
    )
    # Blank "index card" divider collections -- one per group of collections
    # this service manages, each a permanently-empty collection whose sort
    # title floats it above its block in Plex's alphabetised collections tab
    # (roadmap row 49). Before row 49 this switch owned exactly one divider,
    # the Common Sense family's "Ratings Collections"; it now governs them all,
    # and that one is the content-ratings group's.
    separators: bool = Field(
        default=True,
        description="Build blank divider collections, one per group of collections this service manages.",
    )
    # Roadmap row 37. Off by default: a library's collections tab is full of
    # collections this service did not create, and reaching into them is an
    # opt-in, not a default.
    assets_for_all_collections: bool = Field(
        default=False,
        description=(
            "Apply an operator's local poster to collections this service does "
            "not manage, when one is filed under assets_root beside the "
            "collection's title. The ownership label is never applied and such "
            "a collection is never deleted. Only runs as part of a full sweep."
        ),
    )
    # Reorder the collection groups in the tab. None is the canonical order
    # (collections/groups.py: charts, awards, content ratings, content,
    # franchises, location, media, people, production, time, and the operator's
    # own definitions last -- eleven). A PARTIAL list is the expected use: the
    # groups it names lead, in that order, and the rest follow canonically.
    # Section numbers derive from position, so changing this re-writes the sort
    # title of every collection this service manages, once, on the next pass.
    group_order: list[str] | None = Field(
        default=None,
        description="Reorder the collection groups in the collections tab. None is the canonical order; a partial list leads with the named groups, in order, and the rest follow canonically.",
    )
    # Which of upstream's 22 separator colour styles the dividers wear --
    # "orig" is upstream's own default and the shipped value, so an untouched
    # config changes nothing on upgrade. Governs BOTH art kinds: the three
    # groups with matching upstream art fetch from this style's folder, and
    # every other divider is generated from this style's textless @base layer.
    # Changing it re-writes and re-posters every divider once on the next
    # pass, then settles (the key is part of the separator's definition hash).
    separator_style: str = Field(
        default="orig",
        description="Which of upstream's separator colour styles the divider collections wear.",
    )
    # Take over collections created by a tool this service replaces. Off by
    # default: it is a Plex write against collections we did not create, and
    # it should happen once, deliberately, as part of cutover.
    adopt: bool = Field(
        default=False,
        description="Take over collections created by a tool this service replaces, claiming them by their adopt_from label.",
    )
    # Labels belonging to tools being replaced. A collection carrying one of
    # these, whose title this service manages, is eligible to be claimed. A
    # collection with no label is never eligible -- those are the operator's.
    adopt_from: list[str] = Field(
        default_factory=lambda: ["Kometa"],
        description="Labels belonging to tools being replaced; a collection carrying one of these becomes eligible for adoption.",
    )
    # Strip the prior tool's label once claimed. Keeping it is reversible;
    # removing it is not, so it is opt-in.
    adopt_removes_prior_label: bool = Field(
        default=False,
        description="Strip the prior tool's label from a collection once this service has claimed it.",
    )
    # Labels belonging to other tools' collections that must never be
    # touched, no matter what -- this wins over ownership and adoption both,
    # even when the collection also carries an ``adopt_from`` label. Default
    # covers Maintainerr, whose "Deleted Soon" collections this service must
    # never claim.
    protect_labels: list[str] = Field(
        default_factory=lambda: ["Collection managed by Maintainerr"],
        description="Labels marking collections this service must never touch, no matter what -- wins over both ownership and adoption.",
    )
    # Give every collection this service manages a poster: a local override
    # under assets_root if the operator placed one, otherwise Kometa's hosted
    # default for that collection. Applied only after resolve_collision has
    # approved the collection, so a conflicting or protected one is never
    # reached.
    posters: bool = Field(
        default=True,
        description="Give every collection this service manages a poster: a local override if the operator placed one, otherwise a hosted default.",
    )
    # Preset collections switched on by key, from the catalog
    # (``collections/catalog.py``). A key rather than a copy of the
    # definitions it stands for: the expansion happens on the server, on every
    # pass, so a correction to the catalog reaches every deployment instead of
    # having to be migrated into each operator's file. Empty by default, and an
    # empty list expands to nothing at all -- which is what keeps an untouched
    # config building exactly what it built before the catalog existed.
    presets: list[str] = Field(
        default_factory=list,
        description="Preset collections switched on by key, from the built-in catalog. Empty (the default) builds none of them.",
    )
    # Operator-configured collections, each built by a registered builder. The
    # three shipped sources above (charts, awards, separators) are unaffected
    # by this list; it is additive. Live like the rest of this section, so a
    # definition added in Settings applies on the next reconcile.
    definitions: list[CollectionDefinition] = Field(
        default_factory=list,
        description="Operator-configured collections, each built by a registered builder; additive to the charts, awards and separators above.",
    )
    # Delete a collection this service owns once no definition builds it any
    # more -- a chart switched off, a definition removed, a title renamed.
    # Off by default and deliberately the only setting in this file that
    # authorises a delete: with it off the pass reports the orphan instead.
    # Even switched on it deletes only through every guard (the ownership
    # label AND a managed_collections row AND no protected label), and never
    # more than max_deletes in one pass.
    delete_unconfigured: bool = Field(
        default=False,
        description="Delete a collection this service owns once no definition builds it any more, instead of only reporting it as orphaned.",
    )
    # The per-pass cap on that sweep, the cleanup.max_orphans precedent: past
    # it the sweep refuses entirely and reports the numbers, so a config edit
    # that drops every definition cannot cascade into a wiped library. ge=0
    # because 0 is a meaningful setting -- opted in, but nothing this pass.
    max_deletes: int = Field(
        default=5,
        ge=0,
        description=(
            "The most collections one delete sweep may remove. Past this the sweep "
            "refuses entirely and reports the numbers instead; 0 means the sweep is "
            "opted in but deletes nothing."
        ),
    )

    @model_validator(mode="after")
    def _presets_must_be_known_and_ready(self) -> "CollectionsConfig":
        """Every key in ``presets`` names a READY row of the catalog.

        This is the *only* thing that makes a bad key an error. The collision
        validator below runs ``sources.default_definitions`` -- which expands
        these very keys -- during validation of this same model, and that
        expansion is written as a scan of the catalog rather than a lookup of
        this list precisely so it cannot raise on a key nobody knows (a
        ``KeyError`` from inside validation is a 500 on a settings save). So an
        unknown key does not fail there; it expands to nothing at all, and
        without the refusals below a mis-typed preset would be a checkbox an
        operator believed they had ticked.

        Three refusals:

        - **unknown**, with the catalog listed, exactly as ``builder:`` lists
          the registry one field along. A mis-typed key is otherwise a
          checkbox an operator believes they ticked.
        - **not ready**, naming the roadmap row the preset waits on. The
          catalog carries rows whose builders have not been written; the
          picker shows them disabled, and a key copied out of it by hand is
          refused here in the same words rather than accepted as a key that
          would quietly build nothing. (The Phase 9a deferred-attribute
          refusal above is the same shape, one section along.)
        - **duplicated**, because twice in the list is not twice the
          collections -- the expansion is a membership test -- so a repeated
          key is a config that does not mean what it reads as.
        """
        from autoposter.collections.catalog import BY_KEY, GATED

        seen: set[str] = set()
        for key in self.presets:
            if key in seen:
                raise ValueError(
                    f"collection preset {key!r} is listed twice in 'presets': a "
                    "preset is either switched on or it is not, so a repeated "
                    "key builds nothing extra and means less than it looks like"
                )
            seen.add(key)
            preset = BY_KEY.get(key)
            if preset is None:
                raise ValueError(
                    f"unknown collection preset {key!r}: the catalog's keys are "
                    + ", ".join(sorted(BY_KEY))
                )
            if preset.readiness == GATED:
                raise ValueError(
                    f"the {key!r} preset is in the catalog but is not ready to "
                    f"build: it needs roadmap row {preset.gated_row}. Refused "
                    "here rather than accepted as a key that would silently "
                    "build no collections at all"
                )
        return self

    @model_validator(mode="after")
    def _group_order_must_name_known_groups(self) -> "CollectionsConfig":
        """Every name in ``group_order`` is a group, and names it once.

        Refused here rather than discovered as a group whose collections
        quietly kept the canonical number: a mis-typed group is a reordering an
        operator believes they asked for, which is exactly the shape
        ``_presets_must_be_known_and_ready`` above refuses one field along.

        Imported at validation time, not module scope -- ``groups`` reaches back
        into this module through ``catalog``, the cycle every validator in this
        class documents.
        """
        from autoposter.collections.groups import CANONICAL_ORDER

        seen: set[str] = set()
        for name in self.group_order or []:
            if name in seen:
                raise ValueError(
                    f"collection group {name!r} is listed twice in "
                    "'group_order': a group has one position, so a repeated "
                    "name means less than it looks like"
                )
            seen.add(name)
            if name not in CANONICAL_ORDER:
                raise ValueError(
                    f"unknown collection group {name!r}: the groups are "
                    + ", ".join(CANONICAL_ORDER)
                )
        return self

    @model_validator(mode="after")
    def _separator_style_must_be_a_known_style(self) -> "CollectionsConfig":
        """Refused here rather than discovered as a divider that quietly kept
        its old artwork: ``hosted_poster_url`` answers None for an unknown
        style, which is the quiet belt -- this is the loud one. Imported at
        validation time for the cycle every validator in this class documents.
        """
        from autoposter.collections.groups import SEPARATOR_STYLES

        if self.separator_style not in SEPARATOR_STYLES:
            raise ValueError(
                f"unknown separator style {self.separator_style!r}: the styles "
                "are " + ", ".join(SEPARATOR_STYLES)
            )
        return self

    @model_validator(mode="after")
    def _titles_must_not_collide(self) -> "CollectionsConfig":
        """No two definitions may build the same title in the same library.

        Definition identity is ``(library, title)``: the managed row, the
        members hash and the collection in Plex are all keyed on it. Two
        definitions sharing one means the second overwrites the first on every
        pass and the hash flaps between them forever -- a collection that
        never settles, reported as changing every time. Every input to that
        judgement is in this document, so it is refused here rather than
        discovered as a collection that will not stop updating.

        The built-in titles are enumerated the way the engine enumerates them
        (``engine.definition_titles`` over ``sources.default_definitions``), so
        a toggle switched off frees its titles, and the age buckets contribute
        the titles they actually create rather than their definition's
        placeholder. The dynamic year titles are *not* enumerable without the
        ceremony dataset -- true of the Oscars and, now that the award
        presets ship, of all sixteen ceremonies alike -- so a definition
        titled "Oscars Winners 2026" is not caught here; the reconcile leaves
        whichever definition runs second in charge, and the roadmap has that
        as the known gap. The operator group's divider is reserved separately,
        below: that enumeration runs before operator config and so cannot see
        the one group these very definitions activate.
        """
        # Imported at validation time, not module scope: both reach back into
        # this module -- the same cycle CollectionDefinition's builder
        # validator documents.
        from autoposter.collections import groups
        from autoposter.collections.engine import definition_titles
        from autoposter.collections.service import LIBRARY_TYPES
        from autoposter.collections.sources import default_definitions

        # default_definitions and the smart builders' titles() read their
        # settings off ``config.collections``; this model IS that section, so
        # it stands in as the whole config for the enumeration.
        shim = SimpleNamespace(collections=self)
        built_in: set[str] = set()
        # Which of the built-in titles is a group separator's, so the refusal
        # below can name the group and the toggle that frees it instead of
        # sending the operator hunting for a built-in collection that does not
        # exist under that name. Read off ``separator_specs`` -- the same source
        # ``separator_titles`` folds into the set above -- so the reserving
        # enumeration and this explaining one cannot disagree about which titles
        # are separators. Built from the POSITIONED groups alone, the closing
        # fence ("Other Collections", which takes no position) fell through to
        # the generic message.
        separator_group_by_title: dict[str, str] = {}
        for library_type in LIBRARY_TYPES.values():
            defs = default_definitions(shim, library_type)
            built_in |= definition_titles(defs, [], library_type, shim)
            for spec in groups.separator_specs(defs, library_type, shim):
                separator_group_by_title[spec.title] = spec.group

        # The operator group's own divider, which that enumeration cannot see:
        # ``default_definitions`` is built "before operator config"
        # (``sources.py``), so the group that activates from the entries below
        # contributes no title to the set above. Its divider is titled
        # "Collections", and a definition claiming it would leave two writers on
        # one row -- the list reconciler storing a members hash and a member
        # sort title, ``reconcile_separator`` overwriting the summary, the sort
        # title and the hash, each reversing the other every pass. Reserved on
        # the same terms as every other divider: only while ``separators`` is on
        # and only when there is an operator definition to activate the group.
        if self.definitions and self.separators:
            operator_title = groups.separator_title(groups.OPERATOR_GROUP)
            built_in.add(operator_title)
            separator_group_by_title[operator_title] = groups.OPERATOR_GROUP

        seen: dict[str, CollectionDefinition] = {}
        for definition in self.definitions:
            if definition.title in built_in:
                group = separator_group_by_title.get(definition.title)
                if group is not None:
                    raise ValueError(
                        f"collection definition {definition.title!r} has the "
                        f"same title as a built-in collection this service "
                        f"already builds -- the {group!r} group's separator; "
                        "rename your definition, or set "
                        "collections.separators to false to free the title"
                    )
                raise ValueError(
                    f"collection definition {definition.title!r} has the same "
                    "title as a built-in collection this service already "
                    "builds; rename it, or switch off the setting that builds "
                    "the built-in one"
                )
            other = seen.get(definition.title)
            if other is not None and _libraries_overlap(self, definition, other):
                raise ValueError(
                    f"two collection definitions both build {definition.title!r} "
                    f"in the same library (builders {other.builder!r} and "
                    f"{definition.builder!r}): a collection is identified by "
                    "(library, title), so one would overwrite the other on "
                    "every pass"
                )
            seen[definition.title] = definition
        return self


def _libraries_overlap(
    config: "CollectionsConfig",
    one: CollectionDefinition,
    other: CollectionDefinition,
) -> bool:
    """Whether two definitions can ever target the same library.

    ``libraries: None`` means every library in ``collections.libraries``, so it
    is resolved to that list before the sets are compared -- otherwise a
    definition that names Movies explicitly and one that leaves it to the
    default would read as targeting different libraries when they do not.
    """
    default = set(config.libraries)
    return bool(
        (set(one.libraries) if one.libraries is not None else default)
        & (set(other.libraries) if other.libraries is not None else default)
    )


class CleanupConfig(BaseModel):
    """Periodic sweep for orphaned asset directories. Moves to backup_root, never deletes."""

    # Dry run by default, the same posture as badges.upload_to_plex and
    # collections.apply_to_plex: report what would move, change nothing until
    # the operator opts in.
    apply: bool = Field(
        default=False,
        description="Actually move orphaned asset directories to backup_root; off only reports what a sweep would move.",
    )
    # Sanity caps on the result of a sweep. If assets_root is repointed, a
    # volume is remounted, library_folders is toggled (which changes the whole
    # naming scheme) or renders is only partly restored, then *every*
    # directory looks orphaned and the non-empty-renders guard still passes.
    # Past either cap the pass refuses and reports the numbers instead of
    # relocating the library. 500 sits far above real weekly churn on a
    # ~16,000-item library (dozens of directories) yet far below any plausible
    # "the tree moved" figure; the share cap catches the same failure on a
    # small tree, where no useful absolute cap would ever fire.
    max_orphans: int = Field(
        default=500,
        description="Refuse a sweep whose orphan count exceeds this, and report the numbers instead of moving anything.",
    )
    max_orphan_share: float = Field(
        default=0.25,
        description=(
            "Refuse a sweep whose orphan count exceeds this share of the "
            "library, and report the numbers instead of moving anything."
        ),
    )


class PruneConfig(BaseModel):
    """Retiring ``media_items`` rows Plex can no longer resolve. See
    ``scheduler/prune.py``.

    Dry run by default, the same posture as ``cleanup.apply`` and
    ``collections.apply_to_plex``: report which rows would go, delete nothing
    until the operator has read a report and opted in. More strictly here than
    anywhere else, because a cleanup mistake is a folder that moved to
    ``backup_root`` and a prune mistake is a row that is gone.

    Note what "gone" means, because it is wider than "deleted from Plex": a row
    is prunable when the *pipeline* cannot resolve it, and the pipeline never
    looks inside ``plex.excluded_libraries``. Excluding a library therefore
    makes its rows prunable. That is the intended behaviour -- an operator
    excludes a library precisely to stop processing those items -- but it means
    an applied prune after an exclusion retires those rows. No files are
    touched, and re-including the library re-creates the rows on the next pass.
    """

    apply: bool = Field(
        default=False,
        description="Actually delete rows Plex can no longer resolve; off only reports which rows would go.",
    )
    # Sanity caps on the result of a sweep, mirroring cleanup.max_orphans /
    # max_orphan_share. A Plex rebuild, a renamed library, a newly excluded one
    # or a server answering from a partly-loaded state all make large numbers
    # of rows look unresolvable at once, and the unhealthy-Plex guard does not
    # catch a server that answers wrongly rather than not at all. Past either
    # cap the pass refuses and reports the numbers rather than treating them as
    # a work order. 500 sits far above real churn on a ~16,000-item library yet
    # far below any plausible "the server changed" figure; the share cap
    # catches the same failure on a small library, where no useful absolute cap
    # would ever fire.
    max_prunes: int = Field(
        default=500,
        description="Refuse a sweep whose unresolvable-row count exceeds this, and report the numbers instead of deleting anything.",
    )
    max_prune_share: float = Field(
        default=0.25,
        description=(
            "Refuse a sweep whose unresolvable-row count exceeds this share of "
            "the library, and report the numbers instead of deleting anything."
        ),
    )


class AdoptConfig(BaseModel):
    """One-time adoption of an existing library: ``python -m autoposter.adopt``.

    Hashes artwork already on disk instead of resolving providers, so a
    library taken over from the tool being replaced does not get re-rendered
    on its first real pass. See ``adopt/walk.py``.
    """

    # Dry run by default, the same posture as badges.upload_to_plex,
    # collections.apply_to_plex and cleanup.apply: the walk computes and
    # reports what it would adopt, but writes no rows until the operator has
    # read the report and opted in.
    apply: bool = Field(
        default=False,
        description="Actually write adopted rows and hashes; off only reports what adoption would do.",
    )
    libraries: list[str] = Field(
        default_factory=lambda: ["Movies", "TV Shows"],
        description="Which Plex libraries the one-time adoption walk scans.",
    )


class ArtworkModesConfig(BaseModel):
    """Operator-triggered bulk artwork operations (Phase 7b): backup, restore,
    poster reset, remove-overlays revert and the logo updater/revert.

    Every mode that writes to Plex is dry run by default -- the same posture as
    ``badges.upload_to_plex``, ``collections.apply_to_plex`` and
    ``cleanup.apply`` -- so each carries its own ``*_apply`` flag, read off the
    holder per run. The two caps below are shared by all of them: past
    ``max_changes`` items, or past ``max_change_share`` of the candidate set, a
    mode refuses with the real numbers rather than treating an implausibly
    large operation as a work order (the ``cleanup`` ``max_orphans`` /
    ``max_orphan_share`` precedent).
    """

    # Where backup writes and restore reads the Kometa-structured art tree. A
    # NEW root and mount, deliberately not the overloaded ``backup_root`` (which
    # holds asset directories the cleanup sweep relocates): the two must never
    # collide.
    plex_backup_root: Path = Field(
        default=Path("/plexbackup"),
        description="Where the backup mode writes and the restore mode reads the Kometa-structured art tree.",
    )
    # Dry run by default, per Plex-writing mode -- see the class docstring.
    # Backup is Plex-read-only (it writes to disk) and so carries no apply flag.
    restore_apply: bool = Field(
        default=False,
        description="Actually upload the backed-up artwork tree to Plex; off only reports what restore would do.",
    )
    reset_apply: bool = Field(
        default=False,
        description="Actually reset artwork to Plex's default posters; off only reports what reset would do.",
    )
    revert_apply: bool = Field(
        default=False,
        description="Actually revert overlays previously applied by this service; off only reports what the revert would do.",
    )
    logo_apply: bool = Field(
        default=False,
        description="Actually run the logo updater against Plex; off only reports what it would change.",
    )
    logo_revert_apply: bool = Field(
        default=False,
        description="Actually revert the logo updater's previous changes; off only reports what the revert would do.",
    )
    # Shared plausibility caps. 500 sits far above any real operator run on a
    # ~16,000-item library yet far below any "the filter or the mount moved"
    # figure; the share cap catches the same failure on a small library, where
    # no useful absolute cap would ever fire. Mirrors ``cleanup.max_orphans`` /
    # ``max_orphan_share``.
    max_changes: int = Field(
        default=500,
        description="Refuse a mode whose candidate count exceeds this, and report the numbers instead of changing anything.",
    )
    max_change_share: float = Field(
        default=0.25,
        description=(
            "Refuse a mode whose candidate count exceeds this share of the "
            "library, and report the numbers instead of changing anything."
        ),
    )


class SchedulerConfig(BaseModel):
    """Cadences for four of the periodic passes in ``scheduler/jobs.py`` and
    ``scheduler/prune.py`` -- the collections reconcile, the ratings-drift
    sweep, the asset cleanup and the media_items prune -- and the master switch
    for all five: the Radarr/Sonarr sync safety net is also registered under
    ``enabled`` (its cadence lives in ``ArrSyncConfig.hours``), so disabling the
    scheduler stops it too and missed webhooks then never converge.

    There is deliberately no ``cleanup_apply`` or ``prune_apply`` field here
    even though both control scheduled jobs: ``CleanupConfig.apply`` and
    ``PruneConfig.apply`` already mean exactly that (dry run by default) and
    the job factories read them directly, so this section only owns *when*
    those passes run, not whether they write -- repeating it here would give
    the same behaviour two names.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "The master switch for all five scheduled passes -- collections "
            "reconcile, ratings-drift sweep, asset cleanup, media_items prune "
            "and the Radarr/Sonarr sync safety net. Off stops all of them."
        ),
    )
    poll_seconds: int = Field(
        default=60,
        description="How often the scheduler checks whether any due job should run.",
    )
    collections_hours: int = Field(
        default=24,
        description="How often the collections reconcile pass runs.",
    )
    drift_days: int = Field(
        default=7,
        description="How often the ratings-drift sweep runs.",
    )
    # The safety valve: a sweep enqueues at most this many stale items, so a
    # library of ~16,000 items is worked through gradually rather than all at
    # once.
    drift_batch_size: int = Field(
        default=500,
        description=(
            "The most stale items one ratings-drift sweep enqueues, so a large "
            "library is worked through gradually rather than all at once."
        ),
    )
    drift_max_age_days: float = Field(
        default=7,
        description="How old a ratings-drift record must be before the sweep considers it stale.",
    )
    # The credits scan's cadence (roadmap rows 197/194). Whole-library per
    # run, deliberately unpaced: the batched read is ceil(N/chunk) requests
    # (~10 for the measured movie library), so there is nothing a 500-item
    # batch valve would be protecting. Weekly, like drift: credits change on
    # library edits, not on a clock.
    credits_scan_days: int = Field(
        default=7,
        description=(
            "How often the credits scan runs. Whole-library per run, "
            "deliberately unpaced -- credits change on library edits, not on a "
            "clock."
        ),
    )
    maintenance_days: int = Field(
        default=7,
        description="How often the Plex maintenance pass runs.",
    )
    cleanup_days: int = Field(
        default=7,
        description="How often the asset cleanup sweep runs.",
    )
    prune_days: int = Field(
        default=7,
        description="How often the media_items prune sweep runs.",
    )


class RadarrConfig(BaseModel):
    """Registering Plex movies Radarr does not know about. See ``arr/sync.py``.

    ``api_key`` is deliberately not a field here -- it comes from
    ``Secrets.radarr_apikey`` (``AUTOPOSTER_RADARR_APIKEY``), the same
    pattern every other credential in this project follows. Nothing about
    the search flag is configurable: ``sync_section`` always pins
    ``addOptions.searchForMovie`` to ``False``.
    """

    enabled: bool = Field(default=False, description="Whether the Radarr registration sync runs at all.")
    base_url: str = Field(default="", description="Radarr's base URL, e.g. 'http://radarr:7878'.")
    # Dry run by default, the same posture as every other outward-facing
    # write in this project: register nothing until the operator opts in.
    add_existing: bool = Field(
        default=False,
        description="Actually register unknown Plex movies with Radarr; off leaves Radarr untouched.",
    )
    # Verified live: Plex mounts the library at /mnt/Media (capital M),
    # Radarr sees the same files at /mnt/media (lowercase). Case-sensitive
    # on purpose -- see arr/paths.py.
    plex_path: str = Field(
        default="/mnt/Media",
        description="The library path prefix as Plex sees it, remapped to arr_path when registering with Radarr.",
    )
    arr_path: str = Field(
        default="/mnt/media",
        description="The library path prefix as Radarr sees it, the target of the plex_path remap.",
    )
    quality_profile: str = Field(
        default="",
        description="The Radarr quality profile name assigned to newly registered movies.",
    )
    monitor: bool = Field(
        default=True,
        description="Whether newly registered movies are marked monitored in Radarr.",
    )
    minimum_availability: str = Field(
        default="announced",
        description="The Radarr availability level assigned to newly registered movies, e.g. 'announced'.",
    )


class SonarrConfig(BaseModel):
    """Registering Plex shows Sonarr does not know about. See ``arr/sync.py``.

    ``api_key`` is deliberately not a field here -- see ``RadarrConfig``'s
    docstring; the same reasoning applies, via
    ``Secrets.sonarr_apikey``/``AUTOPOSTER_SONARR_APIKEY``.
    """

    enabled: bool = Field(default=False, description="Whether the Sonarr registration sync runs at all.")
    base_url: str = Field(default="", description="Sonarr's base URL, e.g. 'http://sonarr:8989'.")
    add_existing: bool = Field(
        default=False,
        description="Actually register unknown Plex shows with Sonarr; off leaves Sonarr untouched.",
    )
    plex_path: str = Field(
        default="/mnt/Media",
        description="The library path prefix as Plex sees it, remapped to arr_path when registering with Sonarr.",
    )
    arr_path: str = Field(
        default="/mnt/media",
        description="The library path prefix as Sonarr sees it, the target of the plex_path remap.",
    )
    quality_profile: str = Field(
        default="",
        description="The Sonarr quality profile name assigned to newly registered shows.",
    )
    monitor: bool = Field(
        default=True,
        description="Whether newly registered shows are marked monitored in Sonarr.",
    )
    season_folder: bool = Field(
        default=True,
        description="Whether newly registered shows are set to use season folders in Sonarr.",
    )
    series_type: str = Field(
        default="standard",
        description="The Sonarr series type assigned to newly registered shows, e.g. 'standard'.",
    )


class TracearrConfig(BaseModel):
    """Where the watch-history collections read their plays from.

    ``api_key`` is deliberately not a field here -- it comes from
    ``Secrets.tracearr_apikey`` (``AUTOPOSTER_TRACEARR_APIKEY``), the same
    pattern ``RadarrConfig`` records and every other credential in this project
    follows. ``base_url`` may be a cluster-internal hostname, which is why
    ``providers/tracearr.py`` keeps it out of every log line and every
    exception message.

    Not in ``config/live.FROZEN_SECTIONS``, for the radarr/sonarr reason: the
    client is built once per collections pass rather than once per process, so
    switching Tracearr on takes effect at the next pass rather than at the next
    restart.
    """

    enabled: bool = Field(default=False, description="Whether Tracearr is used as a watch-history source at all.")
    base_url: str = Field(
        default="",
        description=(
            "Tracearr's base URL. May be a cluster-internal hostname, so it is "
            "kept out of every log line and exception message."
        ),
    )


class ArrSyncConfig(BaseModel):
    """The safety net that catches any Plex item this service has never
    processed, plus the cadence for the Radarr/Sonarr registration pass.

    Runs whenever ``enabled`` is true, independently of whether either
    service is configured -- with both ``radarr.enabled`` and
    ``sonarr.enabled`` false, a pass still enqueues unknown items, it just
    registers nothing with either service.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Whether the Radarr/Sonarr sync safety net runs at all, catching any "
            "Plex item this service has never processed."
        ),
    )
    hours: int = Field(default=24, description="How often the safety-net pass runs.")
    # The safety valve, same reasoning as scheduler.drift_batch_size: a first
    # run against a fresh database can find every item unknown.
    batch_size: int = Field(
        default=500,
        description=(
            "The most unknown items one safety-net pass enqueues, so a first "
            "run against a fresh database is worked through gradually rather "
            "than all at once."
        ),
    )


class NotificationsConfig(BaseModel):
    """Outbound run-completion webhooks: one POST to ``url`` per event.

    The two payload shapes live in ``notify/payload.py``. ``mode`` is a
    ``Literal`` on purpose -- an unknown mode must fail validation at config
    load, not fall back to some shape at send time. ``url`` is config, not a
    secret, but may embed a token in its path (Uptime-Kuma-style), so it is
    never logged in full -- host only.
    """

    enabled: bool = Field(default=False, description="Whether run-completion webhooks are sent at all.")
    url: str = Field(
        default="",
        description="The webhook URL each run-completion event is POSTed to. May embed a token in its path; only its host is ever logged.",
    )
    mode: Literal["apprise-json", "autoposter-v1"] = Field(
        default="apprise-json",
        description=(
            "'apprise-json' (the default) sends the body Apprise's json:// scheme "
            "POSTs; 'autoposter-v1' sends this service's own versioned shape, "
            "carrying the full detail dict."
        ),
    )
    # Per-attempt HTTP timeout and the number of attempts before giving up.
    # Notification failure never fails the work it reports on. Bounded at
    # load: retry_count of 0 would build a notifier that attempts nothing
    # and reports every send as failed.
    timeout_seconds: int = Field(
        10, gt=0,
        description="The per-attempt HTTP timeout for a webhook send, in seconds.",
    )
    retry_count: int = Field(
        3, ge=1,
        description="How many attempts a webhook send makes before giving up.",
    )


class Config(BaseModel):
    assets_root: Path = Field(
        description="Where this service writes rendered posters, season posters, backgrounds and title cards.",
    )
    manual_assets_root: Path = Field(
        description="The mount an operator's manually-supplied artwork sources are picked from.",
    )
    backup_root: Path = Field(
        description="Where the cleanup sweep relocates orphaned asset directories, preserving their path under assets_root.",
    )
    fonts_root: Path = Field(
        description="Where the fonts referenced by text styles are read from.",
    )
    overlays_root: Path = Field(
        description="Where the overlay images referenced by overlay_file are read from.",
    )
    library_folders: bool = Field(
        default=True,
        description="Lay out assets_root in per-library, per-title folders; off uses a flat naming scheme instead.",
    )
    workers: int = Field(default=5, description="How many render workers run in parallel.")
    settle_seconds: int = Field(
        default=30,
        description=(
            "How long this service waits after a Radarr/Sonarr webhook before "
            "rendering, so a burst of events -- a season pack import -- "
            "collapses into one pass instead of one render per episode."
        ),
    )
    magick_binary: str = Field(
        default="magick",
        description="The ImageMagick executable this service invokes for every composite.",
    )
    skip_tba: bool = Field(
        default=True,
        description="Skip drawing episode title text on a title card when the episode's title matches one of title_card.skip_words.",
    )
    # /docs, /redoc and /openapi.json cannot be put behind the session
    # dependency (FastAPI mounts them itself), and they enumerate every
    # endpoint and its shape to anyone who can reach the port. Off unless a
    # deployment deliberately turns them on.
    api_docs_enabled: bool = Field(
        default=False,
        description="Serve the /docs, /redoc and /openapi.json endpoints, which enumerate every endpoint to anyone who can reach the port.",
    )
    plex: PlexConfig = Field(
        description=(
            "How this service reaches the Plex server: its address, which libraries "
            "are left alone, and how often it re-checks the server and its token."
        ),
    )
    providers: ProvidersConfig = Field(
        description="Which metadata/art providers this service queries, in what order, and how long their answers are cached.",
    )
    artwork: ArtworkConfig = Field(
        description=(
            "What artwork this service builds -- poster, season poster, "
            "background and title card -- their sources, borders and text, "
            "plus the shared logo and output-quality settings."
        ),
    )
    operations: OperationsConfig = Field(
        default_factory=OperationsConfig,
        description="Per-item metadata operations, replacing Kometa's mass_*_update.",
    )
    badges: BadgesConfig = Field(
        default_factory=BadgesConfig,
        description="Kometa-parity badge overlays, composited onto the base artwork.",
    )
    collections: CollectionsConfig = Field(
        default_factory=CollectionsConfig,
        description=(
            "Every collection this service builds and owns in Plex: the built-in "
            "charts and awards, the divider collections, catalog presets and "
            "operator-defined definitions, plus the ownership, adoption, "
            "protection, poster and delete-sweep rules they share."
        ),
    )
    cleanup: CleanupConfig = Field(
        default_factory=CleanupConfig,
        description="Periodic sweep for orphaned asset directories; moves them to backup_root, never deletes.",
    )
    prune: PruneConfig = Field(
        default_factory=PruneConfig,
        description="Retiring media_items rows Plex can no longer resolve.",
    )
    maintenance: MaintenanceConfig = Field(
        default_factory=MaintenanceConfig,
        description="Plex's own housekeeping operations -- clean bundles, empty trash, optimize.",
    )
    artwork_modes: ArtworkModesConfig = Field(
        default_factory=ArtworkModesConfig,
        description=(
            "Operator-triggered bulk artwork operations: backup, restore, "
            "poster reset, remove-overlays revert and the logo updater/revert."
        ),
    )
    scheduler: SchedulerConfig = Field(
        default_factory=SchedulerConfig,
        description=(
            "Cadences for the periodic passes -- the collections reconcile, the "
            "ratings-drift sweep, the asset cleanup and the media_items prune -- "
            "and the master switch for all of them."
        ),
    )
    adopt: AdoptConfig = Field(
        default_factory=AdoptConfig,
        description="One-time adoption of an existing library, hashing artwork already on disk instead of resolving providers.",
    )
    radarr: RadarrConfig = Field(
        default_factory=RadarrConfig,
        description="Registering Plex movies Radarr does not know about.",
    )
    sonarr: SonarrConfig = Field(
        default_factory=SonarrConfig,
        description="Registering Plex shows Sonarr does not know about.",
    )
    tracearr: TracearrConfig = Field(
        default_factory=TracearrConfig,
        description="Where the watch-history collections read their plays from.",
    )
    arr_sync: ArrSyncConfig = Field(
        default_factory=ArrSyncConfig,
        description=(
            "The safety net that catches any Plex item this service has never "
            "processed, plus the cadence for the Radarr/Sonarr registration pass."
        ),
    )
    notifications: NotificationsConfig = Field(
        default_factory=NotificationsConfig,
        description="Outbound run-completion webhooks: one POST to url per event.",
    )
    # Not a release number: the hash of every setting that changes what a
    # render produces, computed by config/loader.py's render_version and
    # stored on each Render row so a settings change can be detected as
    # staleness.
    version: str = Field(
        default="",
        description="A hash of every setting that changes what a render produces, used to detect stale renders. Not a release number.",
    )

    @model_validator(mode="before")
    @classmethod
    def _version_check_moved_to_an_env_var(cls, data):
        """``version_check:`` is no longer a config section -- the registry,
        project and repository it held are derived from ``AUTOPOSTER_IMAGE_REF``
        instead (``config/image_ref.py``). Pydantic ignores unknown keys, so
        without this a deployment that forgot to remove the block from its
        YAML would have it silently dropped and believe it still did
        something, rather than being told to migrate. ``mode="before"`` is
        required to see it at all: by ``mode="after"`` the key is already
        gone.
        """
        if isinstance(data, dict) and "version_check" in data:
            raise ValueError(
                "version_check: has moved out of the config file -- set "
                "AUTOPOSTER_IMAGE_REF instead (see deploy/README.md's "
                '"The sidebar\'s update check") and remove this block'
            )
        return data
