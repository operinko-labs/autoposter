import os
import re
from collections.abc import Collection, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlparse

from PIL import ImageColor
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from autoposter.config.live import FROZEN_SECTIONS
from autoposter.config.state import read_secrets_file, secrets_file_path
from autoposter.overlays.schema import OverlayDefinition

_LANG_RE = re.compile(r"^[a-z]{2}$")

_SECRET_ENV = {
    "database_url": "AUTOPOSTER_DATABASE_URL",
    "tmdb_token": "AUTOPOSTER_TMDB_TOKEN",
    "tvdb_apikey": "AUTOPOSTER_TVDB_APIKEY",
    "fanart_apikey": "AUTOPOSTER_FANART_APIKEY",
    "webhook_secret": "AUTOPOSTER_WEBHOOK_SECRET",
}

# The server credentials (spec §7.2): required iff that server is configured,
# and at least one server must be. Neither is "hard" in the old sense, so
# `missing_hard_secret_names` does not list them; `missing_server_setup`
# does, against the config document.
_SERVER_SECRET_ENV = {
    "plex_token": "AUTOPOSTER_PLEX_TOKEN",
    "jellyfin_api_key": "AUTOPOSTER_JELLYFIN_APIKEY",
}

NO_SERVER_CONFIGURED = "no media server is configured"


def missing_server_setup(document: dict | None, resolved: Mapping[str, str]) -> list[str]:
    """Why the deployment has no usable media server, as sentences naming
    variables and never values. Empty means at least one server has both an
    address and a credential."""
    document = document or {}
    configured = [name for name in ("plex", "jellyfin") if (document.get(name) or {}).get("url")]
    if not configured:
        return [NO_SERVER_CONFIGURED]
    problems = []
    for name in configured:
        env = _SERVER_SECRET_ENV["plex_token" if name == "plex" else "jellyfin_api_key"]
        if not resolved.get(env):
            problems.append(f"{name} is configured but {env} is not set")
    return problems

# The soft names, in the same shape as the hard ones above. Written out as a
# map rather than as eight `os.environ.get` lines inside `from_env` because
# two other callers now iterate it: `Secrets.load` below, and `boot`, which
# exports every name that resolved into the process environment so that
# alembic and the exec'd application read a file-configured deployment exactly
# as they read an env-configured one.
_SOFT_SECRET_ENV = {
    "mdblist_apikey": "AUTOPOSTER_MDBLIST_APIKEY",
    "radarr_apikey": "AUTOPOSTER_RADARR_APIKEY",
    "sonarr_apikey": "AUTOPOSTER_SONARR_APIKEY",
    "admin_password_hash": "AUTOPOSTER_ADMIN_PASSWORD_HASH",
    "plex_account_token": "AUTOPOSTER_PLEX_ACCOUNT_TOKEN",
    "tracearr_apikey": "AUTOPOSTER_TRACEARR_APIKEY",
    "api_key": "AUTOPOSTER_API_KEY",
}


#: Every secret NAME this service reads, in the maps' own order. One list,
#: because four callers iterate it: the resolver, the source map, ``boot``'s
#: export and the secrets route.
SECRET_NAMES: tuple[str, ...] = (
    *_SECRET_ENV.values(),
    *_SERVER_SECRET_ENV.values(),
    *_SOFT_SECRET_ENV.values(),
)


def resolve_secret_values(stored: Mapping[str, str] | None = None) -> dict[str, str]:
    """Every secret env NAME that resolves to a value.

    Precedence: the STORED row, then the STATE FILE, then the ENVIRONMENT
    (spec section 3, the operator's own order). The stored layer is new; the
    other two have SWAPPED, and that is deliberate rather than incidental --
    what an operator sets from the UI or the wizard must not be shadowed by a
    variable they cannot see from the page they set it on.

    "A deployment that never stores a secret sees no difference" still holds,
    because no existing deployment has a name with a value on both layers: the
    wizard writes to the state file only the names the environment did not
    resolve (``api/setup.py``'s staging), so for every real deployment exactly
    one of the two answers any given name and the order between them cannot be
    observed.

    An EMPTY value counts as absent at every layer, here and in every other
    reader of this map (``missing_hard_secret_names``, ``Secrets.load``, and
    ``boot._export``, which overwrites an empty value with the resolved one).
    ``AUTOPOSTER_DATABASE_URL=`` in a copied .env or a blanked GitOps secret
    is a name nobody supplied, and the one rule that must not vary between
    the reader that decides the boot mode and the writer that publishes the
    environment alembic then reads.

    ``stored`` is passed IN rather than read here, and that is not an accident:
    this function is synchronous and is called from inside running event loops
    (``api/setup.py``'s ``_effective``), where an ``asyncio.run`` bridge raises
    outright. The one caller that can read the table -- ``boot``, before any
    loop exists -- does so and hands the map down. A caller with no session
    passes nothing and gets the state file then the environment, which is the
    right answer when no store is reachable.

    Keyed by environment variable name rather than by model field because its
    callers speak in environment variables: ``boot`` exports these, and
    ``alembic/env.py`` reads one of them directly out of ``os.environ``.

    The env-complete short-circuit is kept for the case it was written for: a
    deployment whose environment carries every hard name and that stores
    nothing never opens the state file at all. Once opened, a state directory
    with no file in it reads as an empty mapping rather than failing. The
    corollary is that on an env-complete deployment the file is unreachable
    for the SOFT names too: a wizard-written ``AUTOPOSTER_API_KEY`` would be
    ignored there. That is harmless because such a deployment never runs the
    wizard, and it is said here so no later caller assumes otherwise.

    The short-circuit is gated on ``not stored``, and the consequence is sharp
    enough to state outright: the FIRST secret an env-complete deployment
    stores makes it read its state file again at the next boot, and the file
    then outranks the environment for every name it still holds. A deployment
    the wizard configured and an operator later handed to ExternalSecrets has
    exactly that leftover file. ``deploy/README.md`` carries the instruction
    that follows -- delete ``secrets.env`` once the environment takes over --
    because nothing here can tell a stale file from a deliberate one.
    """
    stored = stored or {}
    # A deployment whose environment carries every hard name never opens the
    # file at all -- not merely never uses its values. That is what makes the
    # GitOps/ExternalSecrets exemption true by construction: there is no file
    # read for that deployment to fail, race, or be denied by a mount that
    # is not there.
    if not stored and all(os.environ.get(name) for name in _SECRET_ENV.values()):
        return {name: os.environ[name] for name in SECRET_NAMES if os.environ.get(name)}
    from_file = read_secrets_file(secrets_file_path())
    resolved: dict[str, str] = {}
    for env_name in SECRET_NAMES:
        value = (
            stored.get(env_name)
            or from_file.get(env_name, "")
            or os.environ.get(env_name, "")
        )
        if value:
            resolved[env_name] = value
    return resolved


def _marker_names(variable: str) -> set[str]:
    """One of ``boot``'s comma-joined name markers, as a set.

    ``"".split(",")`` is ``[""]``, so the empty marker an env-configured boot
    publishes would otherwise become a name. Both markers are set
    unconditionally, and both may legitimately be empty.
    """
    return {name for name in os.environ.get(variable, "").split(",") if name}


def _state_file_names() -> set[str]:
    """The names the STATE FILE answers, as this process can best tell.

    ``boot``'s marker whenever there is one, and the file itself only when
    there is not. That order is the whole point and it is not the intuitive
    one: the file is what happens to be on the volume, the marker is what WON.
    An env-complete deployment with a leftover ``secrets.env`` never opens that
    file at boot -- ``resolve_secret_values`` short-circuits -- so not one of
    the names in it answers anything, and a label read off the file would tell
    that operator the opposite of what their deployment is running on. It would
    also let the webhook rotation write a file the next boot will not read,
    leaving both *arrs signing with a value this service has already forgotten.

    PRESENCE, not truthiness: both markers are set unconditionally, so
    ``STATE_FILE_NAMES_ENV in os.environ`` is what separates "this process
    booted and nothing came from the file" from "this process never booted at
    all". The second is the wizard before its first boot, the CLIs and the
    suite -- none of which has a marker, and all of which are right to read the
    file, because for them it is the only record there is.

    ``read_secrets_file`` raises rather than swallowing a file that exists and
    cannot be read, which is right for the boot path and wrong for a label
    lookup: a page render must not 500 over it. A file that is not UTF-8 is the
    same fault one decoding step later and is caught with it.
    """
    if STATE_FILE_NAMES_ENV in os.environ:
        return _marker_names(STATE_FILE_NAMES_ENV)
    try:
        return {name for name, value in read_secrets_file(secrets_file_path()).items() if value}
    except (OSError, UnicodeDecodeError):
        return set()


def secret_sources(stored_names: Collection[str] | None = None) -> dict[str, str]:
    """Where each secret's running value comes from. Names and sources only.

    One of ``stored``, ``state file``, ``environment``, ``unset`` (spec
    section 3). What the Settings page's secrets accordion renders beside each
    name, and the reason a cleared stored value can be described honestly: the
    next source down takes over and this map says which.

    NAMES ONLY, all the way down, and that is the whole design. Every caller
    of this function runs AFTER ``boot._export`` has published the winning
    values into ``os.environ``, at which point every name looks like an
    environment name and no comparison against ``os.environ`` can tell the
    layers apart. So a name's source is decided by which SET it belongs to --
    the stored set, then the state-file set -- and the environment is only
    what is left when neither claims it. Asking ``os.environ`` first, or
    short-circuiting on it, labels a whole wizard-configured deployment
    ``environment`` and sends its operator to change a variable nothing reads.

    ``stored_names`` is the LIVE table where a caller has a session
    (``secret_store.stored_secret_names``), so a secret cleared since boot
    stops being labelled ``stored`` immediately. ``None`` -- a caller with no
    session -- falls back to the marker ``boot`` published, which is that
    boot's own answer. An empty collection is not ``None``: it means the table
    was read and holds nothing. The state-file set follows the same rule one
    layer down, in ``_state_file_names``: what ``boot`` published, and the
    file only where no boot has published anything.

    The branches are in ``resolve_secret_values``' resolution order and must
    stay in it. A source label that disagreed with the value that function
    picks would send an operator to change a variable that is not the one in
    force, which is the single most expensive thing this map can be wrong
    about.
    """
    stored = _marker_names(STORED_SECRET_NAMES_ENV) if stored_names is None else set(stored_names)
    from_file = _state_file_names()
    sources: dict[str, str] = {}
    for name in SECRET_NAMES:
        if name in stored:
            sources[name] = "stored"
        elif name in from_file:
            sources[name] = "state file"
        elif os.environ.get(name):
            sources[name] = "environment"
        else:
            sources[name] = "unset"
    return sources


def missing_hard_secret_names(resolved: Mapping[str, str]) -> list[str]:
    """The hard names ``resolved`` does not carry, in ``_SECRET_ENV`` order.

    Names, never values: this list is logged at boot and reported by the setup
    wizard, and what an operator needs from it is which variable to set.
    """
    return [name for name in _SECRET_ENV.values() if not resolved.get(name)]


# The two markers `boot` publishes across its `os.execv` so the running
# application can still tell which layer answered each name after `_export`
# has made them all look alike. Comma-joined lists of environment-variable
# NAMES and nothing else: no value, ever. Both are set unconditionally, and
# an empty one is a real answer -- "no name came from there" -- rather than a
# missing one.
#
# They live here, beside `_SECRET_ENV` and `_SOFT_SECRET_ENV`, because this
# module owns every AUTOPOSTER_* secret name there is; `boot` sets them and
# `secret_sources` above reads them, and neither of those should be the place
# a third reader has to go looking.
STATE_FILE_NAMES_ENV = "AUTOPOSTER_STATE_FILE_SECRET_NAMES"
STORED_SECRET_NAMES_ENV = "AUTOPOSTER_STORED_SECRET_NAMES"


def state_file_secret_names(stored: Mapping[str, str] | None = None) -> list[str]:
    """The secret NAMES whose WINNING source is the STATE FILE, in
    ``_SECRET_ENV`` then ``_SERVER_SECRET_ENV`` then ``_SOFT_SECRET_ENV``
    order. Names, never values -- the same rule ``missing_hard_secret_names``
    above follows and for the same reason.

    This exists because ``boot._export`` erases the distinction on purpose:
    it publishes the file's values into ``os.environ`` before the exec, which
    is what makes a file-configured deployment indistinguishable from an
    env-configured one downstream. Downstream is right to be indifferent;
    a caller asking which layer answers a name is not.

    The predicate is ``resolve_secret_values``' precedence, name by name, and
    it moved with that precedence: with the file above the environment there
    is exactly one layer that can outrank it -- the store -- so that is the
    only thing which excludes a name here. The old rule also excluded a name
    the environment carried, which under this order would be wrong, because
    the file is what answers it.

    The env-complete short-circuit is repeated rather than shared so the
    GitOps exemption stays structural here too: such a deployment returns
    ``[]`` without the file being opened at all. That costs one extra read of
    one small file on the file-configured boot, once per process.
    """
    stored = stored or {}
    if not stored and all(os.environ.get(name) for name in _SECRET_ENV.values()):
        return []
    from_file = read_secrets_file(secrets_file_path())
    return [name for name in SECRET_NAMES if not stored.get(name) and from_file.get(name)]


class Secrets(BaseModel):
    """Runtime secrets. Never read from the YAML config file."""

    database_url: str = Field(
        description=(
            "The connection string this service's database engine authenticates "
            "with. Set from AUTOPOSTER_DATABASE_URL; never read from the config file."
        ),
    )
    # A server credential, not a hard secret (spec §7.2): required iff
    # `plex:` is configured, and enforced there by `missing_server_setup`
    # rather than here.
    plex_token: str = Field(
        default="",
        description=(
            "The Plex server token every request to Plex authenticates with. Set "
            "from AUTOPOSTER_PLEX_TOKEN; never read from the config file."
        ),
    )
    # Same posture as plex_token above: required iff `jellyfin:` is configured.
    jellyfin_api_key: str = Field(
        default="",
        description=(
            "The Jellyfin API key every request to Jellyfin authenticates with. "
            "Set from AUTOPOSTER_JELLYFIN_APIKEY; never read from the config file."
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
    # Soft secret, same reasoning as mdblist_apikey for booting and the same
    # posture as admin_password_hash for refusing: unset means the key path
    # is off and CLOSED -- every request presenting an X-API-Key is 401ed,
    # exactly as it was before the key existed -- never open. This is the
    # one INBOUND credential of the soft ones: it is compared against, not
    # sent anywhere. Read from the X-API-Key header only, on the GET routes
    # api.auth.ALLOWLIST names only; see api_key_or_session there.
    api_key: str = Field(
        default="",
        description=(
            "The read-only API key non-browser callers (a Homepage widget, a "
            "script) present as X-API-Key on the allowlisted GET routes. A "
            "deployment without one still runs, with every keyed request "
            "refused."
        ),
    )

    @classmethod
    def load(cls) -> "Secrets":
        """Every secret: the state file first, the environment second.

        No ``stored`` argument, and none is missing: this constructor has no
        database session, and the stored layer reaches it through ``boot``,
        which resolves the store before the exec and publishes the winning
        values into the environment this then reads.

        The ordering is the load-bearing rule of roadmap row 121. A deployment
        whose environment carries all five hard names -- every
        GitOps/ExternalSecrets deployment -- never takes a value from the file,
        never enters setup mode, and behaves exactly as it did before the file
        existed.

        The refusal still names the ENVIRONMENT variable, unchanged, because
        that is what an operator with a broken deployment should set: the state
        file is what the wizard writes, not what an operator is asked to edit.

        The server credentials (``plex_token``, ``jellyfin_api_key``) are read
        the same way the soft ones are: never required here, because whether
        either is required at all depends on which server is configured --
        that is ``missing_server_setup``'s question, not this constructor's.
        """
        resolved = resolve_secret_values()
        values = {}
        for field, env_name in _SECRET_ENV.items():
            value = resolved.get(env_name, "")
            if not value:
                raise RuntimeError(f"required environment variable {env_name} is not set")
            values[field] = value
        for field, env_name in _SERVER_SECRET_ENV.items():
            values[field] = resolved.get(env_name, "")
        for field, env_name in _SOFT_SECRET_ENV.items():
            values[field] = resolved.get(env_name, "")
        return cls(**values)

    @classmethod
    def from_env(cls) -> "Secrets":
        """The name every existing caller uses -- ``main.build()``, both CLIs
        and the suite -- kept so none of them has to change.

        It now delegates to ``load`` above, which reads the state file and the
        environment in that order. For a deployment whose environment is
        complete the two are indistinguishable, which is every deployment that
        existed before roadmap row 121.
        """
        return cls.load()


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
            "Make no online provider request for this artifact. A manually "
            "supplied local asset (including a picked logo) is unaffected and "
            "still used. Unset inherits artwork.disable_online_asset_fetch."
        ),
    )
    # Roadmap row 41. Fires only on a provider's explicit statement that the
    # image carries text -- never on the language-token inference, which
    # guesses and would strip an operator's styling on a guess.
    skip_add_text_when_with_text: bool = Field(
        default=False,
        description=(
            "Skip this artifact's text, overlay and border when the chosen "
            "provider artwork is known to already carry text. Only a provider "
            "that states this outright counts; artwork with no such statement "
            "is treated as unknown and styled normally."
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


# The three vertical anchors ``collections/poster_title.py`` can honour.
# ``TextStyle.gravity`` is an ImageMagick vocabulary shared with the render
# pipeline, which composites through ``magick`` and takes all nine; the
# collection-poster composite draws with Pillow and centres horizontally, so
# it anchors vertically only. Named here rather than in the drawing module so
# the refusal happens when the operator SAVES, not when a pass runs.
_COLLECTION_GRAVITIES = frozenset({"north", "center", "south"})


class CollectionPosterTitleConfig(BaseModel):
    """A styled title, plus a fixed second line, drawn onto a managed
    collection's poster before it is uploaded (roadmap row 105).

    **Where this lives is the whole safety argument.** ``config/loader.py``'s
    ``render_version`` hashes ``config.artwork.model_dump(mode="json")``
    wholesale and its docstring names ``collections`` among the sections it
    excludes, so a key here cannot move ``config.version`` and cannot strand a
    single stored render fingerprint -- while the same key under ``artwork``
    would strand roughly 16,000 of them at its own default value.

    **A sibling of ``ArtKindConfig``, not a subclass.** ``language_order``,
    ``skip_local_text_add``, ``disable_online_asset_fetch`` and
    ``skip_add_text_when_with_text`` mean nothing for a collection poster, and
    this model is walked by ``config/descriptions.py`` into the map the
    Settings page renders -- so subclassing would advertise four settings that
    do nothing.

    **Styled after Posterizarr's parts, not byte-matched to them.** The
    defaults are the operator's own Posterizarr values
    (``CollectionPosterOverlayPart`` for the title block,
    ``CollectionTitlePosterPart`` for the fixed line's colour, stroke, caps and
    wording), written against a 2000-pixel-wide poster and scaled at draw time
    to whatever poster is actually fetched. No captured Posterizarr output
    exists anywhere, so there is no oracle to prove parity against and none is
    claimed. Three defaults are OURS and are called out on their fields: the
    font name, and the fixed line's offset and box.

    ``AddBorder`` and ``AddOverlay`` from ``CollectionPosterOverlayPart`` are
    not implemented and deliberately have no key here.

    ``font_color``/``stroke_color`` are described on ``TextStyle`` as an
    ImageMagick vocabulary, which is accurate for the render pipeline's other
    callers; this module draws with Pillow's narrower ``ImageColor`` parser
    instead, and ``_colors_must_be_drawable`` below refuses a value Pillow
    cannot resolve at save time, the same treatment ``_gravities_must_be_drawable``
    already gives ``gravity``.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Draw the collection's title, plus the fixed collection_line_text, "
            "onto every managed collection poster this service fetches, before "
            "uploading it. Off is byte-identical to not having this feature. "
            "Turning it on re-uploads each affected collection's poster once, "
            "on the next pass, and then settles; a poster you placed yourself, "
            "a divider's art and any collection this service does not manage "
            "are never touched."
        ),
    )
    title: TextStyle = Field(
        default_factory=lambda: TextStyle(
            font="Inter-Medium.ttf",
            all_caps=True,
            font_color="white",
            min_point_size=100,
            max_point_size=250,
            max_width=1900,
            max_height=500,
            text_offset="+300",
            gravity="south",
            line_spacing=0,
            add_text=True,
            add_stroke=False,
            stroke_color="black",
            stroke_width=6,
        ),
        description=(
            "The collection's own title block. Its sizes and offsets are in "
            "pixels against a 2000-pixel-wide poster and are scaled to the "
            "poster actually fetched. The default font is a bundled face, not "
            "Posterizarr's Colus-Regular.ttf, which this service does not ship "
            "-- put your own copy under fonts_root and name it here to use it."
        ),
    )
    collection_line: TextStyle = Field(
        default_factory=lambda: TextStyle(
            font="Inter-Medium.ttf",
            all_caps=True,
            font_color="white",
            min_point_size=40,
            max_point_size=90,
            max_width=1200,
            max_height=150,
            text_offset="+120",
            gravity="south",
            line_spacing=0,
            add_text=True,
            add_stroke=False,
            stroke_color="black",
            stroke_width=6,
        ),
        description=(
            "The fixed second line's own block. Its colour, stroke, caps and "
            "line spacing are Posterizarr's; its offset and box are ours, "
            "because Posterizarr gives both of its parts the same offset -- "
            "they are two poster types there, not two blocks on one image, so "
            "copying both would draw this line through the title. Set add_text "
            "false to draw the title alone."
        ),
    )
    collection_line_text: str = Field(
        default="COLLECTION",
        description=(
            "The fixed second line printed with every collection title, "
            "Posterizarr's CollectionTitle. Empty draws no second line at all."
        ),
    )

    @model_validator(mode="after")
    def _gravities_must_be_drawable(self) -> "CollectionPosterTitleConfig":
        """Refuse a gravity the Pillow composite cannot anchor.

        A saved config with ``southeast`` here would otherwise draw every
        collection's title somewhere the operator did not ask for, on every
        managed collection, and be discovered by looking at Plex.
        """
        for name, style in (
            ("title", self.title),
            ("collection_line", self.collection_line),
        ):
            if style.gravity not in _COLLECTION_GRAVITIES:
                raise ValueError(
                    f"collections.poster_title.{name}.gravity {style.gravity!r} is "
                    "not one of 'north', 'center', 'south': the collection-poster "
                    "composite draws with Pillow and anchors its blocks "
                    "vertically only"
                )
        return self

    @model_validator(mode="after")
    def _colors_must_be_drawable(self) -> "CollectionPosterTitleConfig":
        """Refuse a color Pillow's ``ImageColor`` cannot parse.

        ``font_color``/``stroke_color`` are described as an ImageMagick
        vocabulary but this module draws with Pillow, which recognises a
        narrower set of names -- a value Pillow rejects would otherwise fail
        inside the compositing thread on every pass, for every managed
        collection, until the operator finds the traceback in the pod log.
        """
        for block_name, style in (
            ("title", self.title),
            ("collection_line", self.collection_line),
        ):
            for field in ("font_color", "stroke_color"):
                try:
                    ImageColor.getrgb(getattr(style, field))
                except ValueError as exc:
                    raise ValueError(
                        f"collections.poster_title.{block_name}.{field} is not "
                        f"a color Pillow can draw ({type(exc).__name__})"
                    ) from None
        return self


class SeasonPosterConfig(ArtKindConfig):
    """Season posters can carry the SHOW's own title above their season text."""

    # Roadmap row 78 -- Posterizarr's ShowTitleOnSeasonPosterPart. Shaped
    # exactly like TitleCardConfig.episode_text: a second, independent
    # TextStyle whose own add_text is the feature's gate (upstream's
    # AddShowTitletoSeason). None by default, so a config that never mentions
    # the key draws precisely what it drew before this row -- the same
    # off-by-construction the title card's second block has always had.
    #
    # This block's OWN text_offset AND gravity are IGNORED once the gate is
    # on: render/pipeline.py::stacked_above always uses the SEASON TEXT
    # block's text_offset as its base and raises it by one fitted line of the
    # season text plus a 10px gutter, and the caller draws the result at the
    # SEASON TEXT block's own gravity -- that is what "stacked above the
    # season text" means: the stacking rule owns the position AND the
    # anchor. Upstream gives this block the same "+300"/south as the season
    # text it is supposed to sit ABOVE, which would land the two on top of
    # each other; upstream's own toggle ships false, so those values were
    # never tuned against a real render, and no captured output of this
    # feature exists anywhere to match. The raise itself is OURS, adjudicated,
    # and said so wherever it is described. Both fields ARE live on the one
    # path where there is no season text to stack above -- a blanked
    # `artwork.title_card.season_name_overrides` entry for that season -- see
    # `compose_styled`'s guard.
    show_title: TextStyle | None = Field(
        default=None,
        description=(
            "The show's own title, drawn as a second text block above the "
            "season poster's season text -- font and sizing. This block's "
            "own text_offset and gravity are IGNORED here: position and "
            "anchor are decided entirely by the stacking rule, which draws "
            "above the season text block's own text_offset, at the season "
            "text block's own gravity. Both are live only when the season "
            "text is blank (an override in "
            "artwork.title_card.season_name_overrides), since then there is "
            "nothing to stack above. Unset means no show title is drawn. "
            "The season's own wording is renamed by "
            "artwork.title_card.season_name_overrides."
        ),
    )


class ArtworkConfig(BaseModel):
    poster: ArtKindConfig = Field(
        description="Movie/show poster art: whether it is built, its sources and its text block.",
    )
    season_poster: SeasonPosterConfig = Field(
        description=(
            "Season poster art: whether it is built, its sources, its season "
            "text block and the show title drawn above it."
        ),
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
            "Make no online provider request for any artifact. An artifact "
            "with no local base asset is skipped rather than fetched -- a "
            "picked logo cannot substitute for it, even for a poster. A "
            "manually supplied local asset is unaffected and still used. "
            "Individual art kinds can override this either way."
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
    # Roadmap row 44.
    use_original_title: bool = Field(
        default=False,
        description=(
            "Draw an item's original-language title instead of the localized "
            "one, where Plex carries an original title for it. An item with "
            "none keeps its localized title."
        ),
    )
    # Roadmap row 45.
    use_clearart: bool = Field(
        default=False,
        description=(
            "Prefer Fanart.tv clearart over clearlogo when compositing a logo "
            "onto a poster. Clearlogo is still used when no clearart exists."
        ),
    )
    # Roadmap row 46. One nullable colour, not a bool plus a colour: unset
    # means no recolour, and two spellings of one setting is one too many.
    logo_flat_color: str | None = Field(
        default=None,
        description=(
            "Flatten every composited clearlogo to this one colour -- an "
            "ImageMagick colour name or hex value, e.g. 'white' or '#ff0000'. "
            "Unset leaves each logo its own colours."
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


class JellyfinConfig(BaseModel):
    url: str = Field(description="The Jellyfin server's base URL this service manages.")
    excluded_libraries: list[str] = Field(
        default_factory=list,
        description="Jellyfin libraries this service never touches -- skipped by every walk and sync.",
    )
    library_map: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Plex library name -> Jellyfin library name, only for libraries the "
            "two servers name differently. A library not listed is matched by "
            "its own name."
        ),
    )
    replace_thumb_with_backdrop: bool = Field(
        default=False,
        description=(
            "Also upload the background into Jellyfin's Thumb slot, which some "
            "views show instead of the backdrop (Posterizarr's "
            "ReplaceThumbwithBackdrop)."
        ),
    )
    liveness_interval_seconds: int = Field(
        default=60,
        description="How often the background health check asks Jellyfin for /System/Info.",
    )


def _validate_field_verbs(value: dict[str, str]) -> dict[str, str]:
    """Refuse an unknown field name or verb at LOAD time.

    Extracted from ``OperationsConfig``'s own validator so the per-library
    partial model can reach the same rule without a second copy of it
    (roadmap row 92). A copy is the shape this particular bug takes: the
    global and the per-library halves would drift, and the half that drifted
    would accept a verb the writer never fires.

    The row-81 precedent, one vocabulary along: a typo'd field name would
    otherwise be a setting that silently never fires, which is
    indistinguishable from the feature not working.
    """
    from autoposter.plex.writer import FIELD_VERBS, WRITABLE_BY_KIND

    known = set().union(*WRITABLE_BY_KIND.values())
    for field, verb in value.items():
        if field not in known:
            raise ValueError(
                f"operations.field_verbs names {field!r}, which is not a "
                f"field this service writes; known fields are "
                f"{', '.join(sorted(known))}"
            )
        if verb not in FIELD_VERBS:
            raise ValueError(
                f"operations.field_verbs[{field!r}] is {verb!r}; the verbs "
                f"are {', '.join(sorted(FIELD_VERBS))}"
            )
    return value


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
    write_to_jellyfin: bool = Field(
        default=True,
        description=(
            "Write the gathered metadata to Jellyfin. Off gathers and stores the "
            "facts but leaves Jellyfin untouched."
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
    # Roadmap rows 32 and 33a, under the explicit-source model: a mass-op field
    # is written only when config NAMES the provider it comes from. There is no
    # default source and no fallback chain, so an untouched config writes
    # neither field. A ``Literal`` rather than a plain string: a source this
    # service cannot serve is a config load error, not a value that silently
    # never appears.
    user_rating_source: Literal["imdb", "tmdb"] | None = Field(
        default=None,
        description=(
            "Which provider's rating is written to Plex's user rating, "
            "library-wide: 'imdb' for the IMDb rating, 'tmdb' for the TMDb "
            "audience rating. Unset writes no user rating at all."
        ),
    )
    original_title_source: Literal["tmdb"] | None = Field(
        default=None,
        description=(
            "Which provider supplies the original-language title written to "
            "Plex, library-wide. Unset writes no original title at all."
        ),
    )
    # Roadmap row 34. Applied AHEAD of the diff, so the mapped value is both
    # what is compared and what is written -- mapping after the diff would
    # rewrite the same item every pass. Exact-key and case-sensitive: an
    # operator's hand-written table means the strings it holds.
    genre_mapper: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Genre names to rewrite before they are written to Plex, e.g. "
            "'Sci-Fi & Fantasy' to 'Sci-Fi'. A genre not named here is written "
            "unchanged; two genres mapped onto one are written once."
        ),
    )
    content_rating_mapper: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Content-rating values to rewrite before they are written to Plex, "
            "e.g. 'TV-MA' to '18'. A value not named here is written unchanged."
        ),
    )
    # Roadmap row 87. Keyed by this service's own field names
    # (plex/writer.py::WRITABLE_BY_KIND); the value is the verb, which
    # REPLACES that field's provider source. An empty map -- the default --
    # is exactly today's behaviour.
    field_verbs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "A verb to apply to a metadata field instead of writing a "
            "provider's value into it: 'lock', 'unlock' or 'remove'. Keyed by "
            "field name, e.g. 'studio'. A field not named here is written from "
            "its provider source as usual. 'remove' on 'genres' clears EVERY "
            "genre on the item and locks the field (roadmap row 229, Kometa's "
            "own semantics), and this service has no verb that puts them back."
        ),
    )
    lock_apply: bool = Field(
        default=False,
        description="Actually apply the 'lock' verb to Plex; off only reports which fields it would lock.",
    )
    unlock_apply: bool = Field(
        default=False,
        description="Actually apply the 'unlock' verb to Plex; off only reports which fields it would unlock.",
    )
    remove_apply: bool = Field(
        default=False,
        description="Actually apply the 'remove' verb to Plex; off only reports which fields it would clear.",
    )

    # Roadmap row 99. The ONE gate for per-item metadata overrides, and there
    # is deliberately no ``item_overrides_apply`` beside it -- see
    # ``plex/item_overrides.py``'s module docstring for the asymmetry with
    # row 87's three ``*_apply`` flags, which is that a verb is library-wide
    # and an override is one item typed into a panel with its own confirm.
    #
    # Off means: the panel is read-only with a banner naming this key, and
    # any existing rows are IGNORED by the writer rather than deleted -- so
    # switching it back on restores the operator's work instead of finding it
    # gone. Live: ``operations`` is absent from ``config/live.py``'s
    # FROZEN_SECTIONS, and ``config/loader.py::render_version`` excludes the
    # whole section, so this key moves no fingerprint and re-renders nothing.
    item_overrides_enabled: bool = Field(
        default=False,
        description=(
            "Whether per-item metadata overrides are written to Plex. Off "
            "leaves any stored overrides in place and ignored, and makes the "
            "item page's override panel read-only."
        ),
    )

    # Roadmap row 268. Row 227's two-key shape: naming the source gates the
    # fetch, ``sort_title_apply`` gates the write. A ``Literal`` with one value
    # today, so that ordering by some other collection's membership -- the
    # half of the idea this row does not ship -- is a second value here rather
    # than a second key. The apply flag is off by default for row 227's reason:
    # the write touches every franchise movie in a library, and turning the
    # flag back off leaves the sort titles it wrote (locked) in place.
    sort_title_source: Literal["tmdb_collection", "collections"] | None = Field(
        default=None,
        description=(
            "Where an item's sort title comes from, library-wide. "
            "'tmdb_collection' writes '<franchise> <NN>' -- the TMDb franchise "
            "collection's name and the film's position in it, in the order TMDb "
            "lists the parts -- so the library's title sort lists a franchise "
            "in that order; movie libraries only. 'collections' reads the "
            "positions your own collection definitions with member_sort: true "
            "recorded, in each list's own order (roadmap row 269). Unset writes "
            "no sort title at all."
        ),
    )
    sort_title_apply: bool = Field(
        default=False,
        description=(
            "Actually write the franchise sort title to Plex; off only reports "
            "which items it would set."
        ),
    )

    @field_validator("field_verbs")
    @classmethod
    def _known_fields_and_verbs(cls, value: dict[str, str]) -> dict[str, str]:
        """Refuse an unknown field name or verb at LOAD time.

        The rule itself lives in ``_validate_field_verbs`` above, so the
        per-library partial model (roadmap row 92) can reach it without a
        copy that would drift.
        """
        return _validate_field_verbs(value)

    # Roadmap row 86. Off by default: the mode reads Plex and writes a tree,
    # and a deployment that has not provided the mount must get a refusal
    # rather than a backup filling the container's own disk.
    metadata_backup_enabled: bool = Field(
        default=False,
        description=(
            "Whether the metadata backup export runs at all. Off refuses the "
            "trigger and writes nothing."
        ),
    )
    metadata_backup_root: Path = Field(
        default=Path("/metadatabackup"),
        description=(
            "Where the metadata backup writes one YAML file per Plex library, "
            "holding the current value and lock state of every field this "
            "service writes."
        ),
    )

    # Roadmap row 84. TVDb as a nameable source for the three fields its
    # extended record carries, alongside TMDb. The explicit-source model again:
    # exactly one source per field, no precedence and no tiebreak. Unset keeps
    # each field on the TMDb value gather_facts already produces, which is what
    # every config that does not set these does today.
    genres_source: Literal["tmdb", "tvdb"] | None = Field(
        default=None,
        description=(
            "Which provider supplies the genres written to Plex. Unset keeps "
            "the TMDb genres this service already gathers."
        ),
    )
    studio_source: Literal["tmdb", "tvdb"] | None = Field(
        default=None,
        description=(
            "Which provider supplies the studio written to Plex. Unset keeps "
            "the TMDb studio this service already gathers."
        ),
    )
    originally_available_source: Literal["tmdb", "tvdb"] | None = Field(
        default=None,
        description=(
            "Which provider supplies the release date written to Plex. Unset "
            "keeps the TMDb date this service already gathers."
        ),
    )

    # Roadmap row 227, Kometa's ``mass_added_at_update``. TWO knobs, not one,
    # and the split is row 85's: naming the source gates the FETCH (one cached
    # ``/movie/{id}/release_dates`` request per in-scope movie per TTL) and
    # ``added_at_apply`` gates the WRITE.
    #
    # The five other value-write sources (``user_rating_source``,
    # ``original_title_source``, ``genres_source``, ``studio_source``,
    # ``originally_available_source``) carry no apply flag -- naming them IS
    # their arming -- and this one diverges deliberately. The write rewrites
    # Plex's own "recently added" ordering for the whole library, this service
    # records no undo for it, and every ``*_apply`` flag already present in
    # this deployment's stored overrides is ``true``, so anything that reused
    # an existing flag would ship armed. A NEW flag ships off.
    #
    # NO region knob, and that is Kometa's own default path rather than a
    # simplification: ``modules/operations.py``'s ``tmdb_release_date`` uses
    # ``config.TMDb.region`` only when it is set AND present on the movie, and
    # otherwise takes ``min()`` across every region. This project has no TMDb
    # region setting anywhere, so the faithful port is the all-regions
    # ``min()``.
    #
    # Movie libraries only, per Kometa -- enforced by
    # ``plex/writer.WRITABLE_BY_KIND["movie"]`` and by ``gather_facts``' own
    # ``item.kind == "movie"`` term, because this document holds library NAMES
    # and nothing that says whether a name is a movie library
    # (``_library_names_must_be_configured`` below records the same limit).
    added_at_source: Literal["tmdb_digital", "tmdb_premiere"] | None = Field(
        default=None,
        description=(
            "Which TMDb release date is written to Plex's 'added at' date, "
            "library-wide: 'tmdb_digital' for the digital release, "
            "'tmdb_premiere' for the premiere. The earliest matching date "
            "across every region, as Kometa has it. Movie libraries only; "
            "unset makes no request and writes nothing."
        ),
    )
    added_at_apply: bool = Field(
        default=False,
        description=(
            "Actually write the fetched date to Plex's 'added at'; off only "
            "reports which items would change. Turning this on reorders the "
            "library's Recently Added shelf, and turning it back off does not "
            "put the old dates back."
        ),
    )

    # Roadmap row 85. IMDb's own parental-guide categories, written as Plex
    # labels. Default OFF -- the family posture rows 86 (backup) and 87
    # (verbs) already use. Two flags, not one, on purpose: `enabled` gates
    # the FETCH (one cached title(id:) GraphQL request per in-scope item --
    # a cost even in dry-run reporting mode), `apply` gates the WRITE, the
    # same split row 87's lock/unlock/remove already draws between "would
    # write" and "wrote."
    parental_labels_enabled: bool = Field(
        default=False,
        description=(
            "Fetch IMDb's parental-guide categories (violence, profanity, "
            "nudity, alcohol, frightening) for each in-scope item and report "
            "which labels would be added. Off makes no request."
        ),
    )
    parental_labels_apply: bool = Field(
        default=False,
        description=(
            "Actually write the fetched parental-guide labels to Plex; off "
            "only reports which labels it would add."
        ),
    )
    parental_labels_include_none: bool = Field(
        default=False,
        description=(
            "Also add a label for a category IMDb's consensus rates 'None', "
            "e.g. 'Alcohol, Drugs & Smoking: None'. Off -- the default -- "
            "labels only Mild/Moderate/Severe categories."
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
    upload_to_jellyfin: bool = Field(
        default=False,
        description=(
            "Upload the composed, badged artwork to Jellyfin. Off composes and "
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
    # Roadmap row 97. Operator-defined overlays, drawn after the nine built-in
    # badges. Empty by default, which is byte-identical to no feature at all
    # (tests/test_overlay_entrypoint.py pins that against a recorded hash).
    definitions: list[OverlayDefinition] = Field(
        default_factory=list,
        description=(
            "Operator-defined overlays composited on top of the built-in "
            "badges, each naming its own image or text, position, backdrop "
            "and optional group."
        ),
    )
    # Roadmap row 100. A family is a bundle of definitions this
    # service ships, transcribed from the pinned Kometa tree
    # (`overlays/families.py`) -- ~40 content-rating definitions is not
    # something an operator writes by hand, so naming the family is the
    # surface. Empty by default, which is byte-identical to no feature at
    # all: `all_definitions()` returns `definitions` unchanged.
    families: list[str] = Field(
        default_factory=list,
        description=(
            "Bundled overlay families to draw, by name -- each expands into "
            "this service's own transcription of Kometa's definitions for it, "
            "drawn before the operator's own definitions so those can suppress "
            "a family member."
        ),
    )
    definition_image_max_bytes: int = Field(
        default=5 * 1024 * 1024, gt=0,
        description=(
            "Largest image an overlay definition's url source may download; "
            "a larger body is refused mid-stream."
        ),
    )

    @model_validator(mode="after")
    def _check_families(self) -> "BadgesConfig":
        """Refuse an unknown or repeated family name at config load.

        Naming what exists rather than saying "unknown": the list is closed
        and short, and an operator who typed `ribbon` needs to be told it is
        roadmap row 8a's rather than left to guess at a spelling.

        The empty-`families` return is above the import, not just above the
        loop, and that ordering is load-bearing: this validator runs on
        EVERY `BadgesConfig` construction, including the ones `families`
        never touches, and `actions/flags.py` imports `config.schema` on
        every Action Center queue/summary request. Importing
        `overlays/families.py` constructs ~40 `OverlayDefinition`s at module
        scope, and each one's own `_validate` call-imports
        `overlays.selection`, which imports `collections.filters`, which
        imports `langcodes` -- exactly the transitive weight
        `overlays/schema.py::_as_rgba`'s docstring already keeps off this
        path. An operator who never names a family must not pay for one.
        """
        if not self.families:
            return self

        from autoposter.overlays.families import FAMILIES

        for name in self.families:
            if name not in FAMILIES:
                raise ValueError(
                    f"{name!r} is not a bundled overlay family. Available: "
                    + ", ".join(sorted(FAMILIES))
                )
        duplicates = sorted({n for n in self.families if self.families.count(n) > 1})
        if duplicates:
            raise ValueError(
                "each overlay family may be named once; repeated: "
                + ", ".join(duplicates)
            )
        return self

    def all_definitions(self) -> list["OverlayDefinition"]:
        """Every definition this config draws: the named families, then the
        operator's own.

        Computed rather than folded into ``definitions`` at load, and that is
        deliberate: the config editor round-trips config -> YAML -> config,
        so an expansion written back into ``definitions`` would be expanded
        again on the next load and every family member would draw twice.

        Families come FIRST so an operator's own definition can name a family
        member in ``suppress_overlays`` -- suppression is resolved before
        group weight (``badges/compose.py::_resolve_definitions``), so it
        wins regardless of order, but reading order matching drawing order is
        what an operator expects.
        """
        from autoposter.overlays.families import FAMILIES

        expanded: list[OverlayDefinition] = []
        for name in self.families:
            expanded.extend(FAMILIES[name])
        return expanded + list(self.definitions)


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


#: Roadmap row 92. Paths inside a ``libraries:`` block that are structurally
#: global -- refused at load rather than accepted and silently ignored, which
#: is the posture ``config/overrides.py``'s ``unknown_key_paths`` already
#: takes for a typo. Each value says WHY, because "not overridable" without
#: the why is a shrug.
#:
#: Keyed on the path INSIDE a library block, so one map serves the file loader
#: and the config editor and neither one holds a library name.
#:
#: The ``operations.*`` entries below are DERIVED from ``config/live.py``'s
#: ``FROZEN_SECTIONS`` rather than copied by hand (roadmap row 92 review,
#: Important 2): those four settings are frozen because a process-wide
#: object reads them once at startup and never again, which is exactly why a
#: per-library value could not be honoured either -- the object that would
#: have to vary per library does not exist. Deriving means a FIFTH
#: ``operations.*`` entry added to ``FROZEN_SECTIONS`` later (a new
#: startup-captured cadence, a new process-wide client) is excluded here
#: automatically, instead of staying overridable per library while the
#: object that reads it was built once and never rereads it -- the exact gap
#: a hand-copied literal would reopen silently.
_FROZEN_OPERATIONS_EXCLUSIONS: dict[str, str] = {
    path: reason for path, reason in FROZEN_SECTIONS.items()
    if path.startswith("operations.")
}

LIBRARY_OVERRIDE_EXCLUSIONS: dict[str, str] = {
    **_FROZEN_OPERATIONS_EXCLUSIONS,
    "operations.metadata_backup_enabled": (
        "the metadata backup writes one file tree for the whole server"
    ),
    "operations.metadata_backup_root": (
        "the metadata backup writes one file tree for the whole server"
    ),
    "maintenance.clean_bundles": (
        "cleanBundles is a server-wide Plex call with no per-section form, so "
        "a per-library value could only be ignored or applied everywhere"
    ),
    "maintenance.optimize": (
        "optimize is a server-wide Plex call with no per-section form, so a "
        "per-library value could only be ignored or applied everywhere"
    ),
    "badges.definitions": (
        "a list replaces wholesale, so a per-library definitions list would "
        "silently drop every global definition; a definition targets its own "
        "libraries instead"
    ),
    "badges.definition_image_max_bytes": (
        "a download safety bound rather than a preference"
    ),
}

#: Roadmap row 92 review, Important 1. ``LIBRARY_OVERRIDE_EXCLUSIONS`` above
#: is keyed ``section.name`` and only ever matches a LEAF of one of the three
#: whitelisted sections (``operations``, ``badges``, ``maintenance``) -- so a
#: whole SECTION under a library block, such as ``artwork`` or
#: ``scheduler``, could never be looked up in it and pydantic's default
#: ``extra="ignore"`` dropped it in silence. This map is keyed on the
#: section NAME alone, for a section that is a real ``Config`` field this
#: service deliberately does not let vary per library.
LIBRARY_OVERRIDE_SECTION_EXCLUSIONS: dict[str, str] = {
    "artwork": (
        "render_version hashes config.artwork wholesale into the one "
        "config.version every stored fingerprint carries, so a per-library "
        "artwork setting would strand fingerprints across libraries it "
        "never named; the correct shape is roadmap row 111's "
        "render_version_for(art_kind, config) taking a library argument, "
        "filed but not built"
    ),
    "collections": (
        "a collection definition already targets its own libraries "
        "(CollectionDefinition.libraries); a second per-library dimension "
        "over the same thing would be two ways to say one sentence"
    ),
    "playlists": (
        "a playlist definition already targets its own libraries "
        "(PlaylistDefinition.libraries); a second per-library dimension "
        "over the same thing would be two ways to say one sentence"
    ),
    "plex": (
        "one Plex server and one section list serve every library; there "
        "is no per-library Plex connection to have an opinion about"
    ),
    "scheduler": (
        "the scheduler's job set and cadences are registered once, "
        "process-wide, at startup; there is no per-library schedule"
    ),
}

#: A section under a library block that is neither one of the three
#: whitelisted sections nor one of the excluded ones above -- a typo, or a
#: name this config has never had. Refused with one fixed sentence rather
#: than a per-section reason, because there is no real setting to explain;
#: ``config/overrides.py``'s ``unknown_key_paths`` makes the same call for a
#: typo'd LEAF.
_UNKNOWN_LIBRARY_SECTION_REASON = (
    "not one of this library's overridable sections -- only operations, "
    "badges and maintenance can be set per library"
)

_LIBRARY_OVERRIDE_SECTIONS = frozenset({"operations", "badges", "maintenance"})


def library_override_refusals(document: dict) -> list[tuple[str, str]]:
    """Every ``libraries:`` path in ``document`` that is structurally global.

    Answers ``(dotted path, reason)`` pairs, dotted from the document ROOT so
    the config editor can report each one on the row it sits on.

    Public and shared by two callers, which is the point. ``Config``'s
    before-validator below refuses these for the mounted YAML -- where
    pydantic would otherwise drop them in silence, because the partial models
    do not declare them -- and ``api/routes.py`` calls it ahead of
    ``unknown_key_paths`` so the editor answers with the structural reason
    rather than the true-but-useless "unknown setting". One walk, one
    vocabulary, two entry points.

    A whole SECTION that is not one of the three whitelisted ones is refused
    here too (roadmap row 92 review, Important 1) -- whether it names a real
    ``Config`` field (``LIBRARY_OVERRIDE_SECTION_EXCLUSIONS``) or nothing at
    all (``_UNKNOWN_LIBRARY_SECTION_REASON``) -- so a section never reaches
    pydantic's default ``extra="ignore"`` and gets dropped without a trace.

    Sorted, so a document with several offenders reports them in a stable
    order rather than a dict's.
    """
    libraries = document.get("libraries")
    if not isinstance(libraries, dict):
        return []
    refusals: list[tuple[str, str]] = []
    for library, block in libraries.items():
        if not isinstance(block, dict):
            continue
        for section, settings in block.items():
            if section not in _LIBRARY_OVERRIDE_SECTIONS:
                reason = LIBRARY_OVERRIDE_SECTION_EXCLUSIONS.get(
                    section, _UNKNOWN_LIBRARY_SECTION_REASON
                )
                refusals.append((f"libraries.{library}.{section}", reason))
                continue
            if not isinstance(settings, dict):
                continue
            for name in settings:
                reason = LIBRARY_OVERRIDE_EXCLUSIONS.get(f"{section}.{name}")
                if reason is not None:
                    refusals.append((f"libraries.{library}.{section}.{name}", reason))
    return sorted(refusals)


def _per_library(section: str, name: str) -> str:
    """One override leaf's description: what setting it is, then the global
    field's own sentence.

    Composed rather than rewritten so the two can never drift: an operator
    reading the per-library row and the global row is reading one description
    of one setting, and a second hand-written copy is how they would stop
    agreeing. Says nothing about WHEN a change applies -- ``frozen_paths``
    owns that fact and the editor renders it separately.
    """
    source = {
        "operations": OperationsConfig,
        "badges": BadgesConfig,
        "maintenance": MaintenanceConfig,
    }[section]
    return (
        f"This library's value for {section}.{name}; unset inherits the "
        f"global setting. " + (source.model_fields[name].description or "")
    )


class OperationsOverride(BaseModel):
    """One library's metadata-operations settings (roadmap row 92).

    Every field of ``OperationsConfig`` except the six that are structurally
    global (``LIBRARY_OVERRIDE_EXCLUSIONS``), each optional so that ABSENT
    means "inherit the global" and a stated value means "this library instead".
    The distinction is pydantic's ``model_fields_set``, which is why
    ``config/loader.py``'s ``config_for_library`` dumps with
    ``exclude_unset=True``: without it every unstated field would arrive as an
    explicit ``None`` and blank the global.

    Kometa's own per-library merge is this, field by field
    (``modules/config.py``'s ``check_for_attribute``), with ONE exception this
    codebase deliberately does not copy: it UNIONs ``ignore_ids`` and
    ``ignore_imdb_ids`` with the global rather than replacing them. Here a
    list replaces, because every list in this config is a complete statement
    of intent (``config/overrides.py``'s ``merge_overrides``) -- an operator
    removing an id from a library's list must get a shorter list back, not
    the same one. One merge rule in this codebase is worth more than byte
    parity with Kometa on two keys.

    A dict-valued leaf (``genre_mapper``, ``content_rating_mapper``,
    ``field_verbs``) merges key by key rather than replacing, because that is
    what ``_merge`` does to a nested mapping -- and it is already what the
    stored overrides document does to those same three settings, so the two
    layers agree.
    """

    enabled: bool | None = Field(
        default=None, description=_per_library("operations", "enabled"))
    write_to_plex: bool | None = Field(
        default=None, description=_per_library("operations", "write_to_plex"))
    write_to_jellyfin: bool | None = Field(
        default=None, description=_per_library("operations", "write_to_jellyfin"))
    ignore_ids: list[str] | None = Field(
        default=None, description=_per_library("operations", "ignore_ids"))
    ignore_imdb_ids: list[str] | None = Field(
        default=None, description=_per_library("operations", "ignore_imdb_ids"))
    ignore_labels: list[str] | None = Field(
        default=None, description=_per_library("operations", "ignore_labels"))
    user_rating_source: Literal["imdb", "tmdb"] | None = Field(
        default=None, description=_per_library("operations", "user_rating_source"))
    original_title_source: Literal["tmdb"] | None = Field(
        default=None, description=_per_library("operations", "original_title_source"))
    genre_mapper: dict[str, str] | None = Field(
        default=None, description=_per_library("operations", "genre_mapper"))
    content_rating_mapper: dict[str, str] | None = Field(
        default=None, description=_per_library("operations", "content_rating_mapper"))
    field_verbs: dict[str, str] | None = Field(
        default=None, description=_per_library("operations", "field_verbs"))
    lock_apply: bool | None = Field(
        default=None, description=_per_library("operations", "lock_apply"))
    unlock_apply: bool | None = Field(
        default=None, description=_per_library("operations", "unlock_apply"))
    remove_apply: bool | None = Field(
        default=None, description=_per_library("operations", "remove_apply"))
    item_overrides_enabled: bool | None = Field(
        default=None, description=_per_library("operations", "item_overrides_enabled"))
    genres_source: Literal["tmdb", "tvdb"] | None = Field(
        default=None, description=_per_library("operations", "genres_source"))
    studio_source: Literal["tmdb", "tvdb"] | None = Field(
        default=None, description=_per_library("operations", "studio_source"))
    originally_available_source: Literal["tmdb", "tvdb"] | None = Field(
        default=None,
        description=_per_library("operations", "originally_available_source"))
    added_at_source: Literal["tmdb_digital", "tmdb_premiere"] | None = Field(
        default=None, description=_per_library("operations", "added_at_source"))
    added_at_apply: bool | None = Field(
        default=None, description=_per_library("operations", "added_at_apply"))
    sort_title_source: Literal["tmdb_collection", "collections"] | None = Field(
        default=None, description=_per_library("operations", "sort_title_source"))
    sort_title_apply: bool | None = Field(
        default=None, description=_per_library("operations", "sort_title_apply"))
    parental_labels_enabled: bool | None = Field(
        default=None, description=_per_library("operations", "parental_labels_enabled"))
    parental_labels_apply: bool | None = Field(
        default=None, description=_per_library("operations", "parental_labels_apply"))
    parental_labels_include_none: bool | None = Field(
        default=None,
        description=_per_library("operations", "parental_labels_include_none"))


class BadgesOverride(BaseModel):
    """One library's badge settings (roadmap row 92).

    ``definitions`` and ``definition_image_max_bytes`` are excluded and say
    why in ``LIBRARY_OVERRIDE_EXCLUSIONS``. ``families`` IS here and is a
    list, so it replaces the global list wholesale -- a library naming no
    family draws none, which is the statement an empty list makes everywhere
    else in this config.
    """

    enabled: bool | None = Field(
        default=None, description=_per_library("badges", "enabled"))
    upload_to_plex: bool | None = Field(
        default=None, description=_per_library("badges", "upload_to_plex"))
    upload_to_jellyfin: bool | None = Field(
        default=None, description=_per_library("badges", "upload_to_jellyfin"))
    lock_artwork: bool | None = Field(
        default=None, description=_per_library("badges", "lock_artwork"))
    apply_overlay_label: bool | None = Field(
        default=None, description=_per_library("badges", "apply_overlay_label"))
    adopt_from_plex: bool | None = Field(
        default=None, description=_per_library("badges", "adopt_from_plex"))
    families: list[str] | None = Field(
        default=None, description=_per_library("badges", "families"))


class MaintenanceOverride(BaseModel):
    """One library's Plex housekeeping (roadmap row 92).

    ONE field, and that is a fact about plexapi rather than a retreat.
    ``cleanBundles`` and ``optimize`` exist on ``Library`` only -- server-wide
    calls with no per-section form -- so a per-library value for either could
    be honoured in exactly two ways and both are wrong: ignore it, or run the
    server-wide call and apply one library's opinion to every library.
    ``emptyTrash`` exists on ``LibrarySection`` as well, so it is the one that
    can be scoped honestly, and ``scheduler/jobs.py``'s maintenance job scopes
    it. The other two are refused with that reason recorded.
    """

    empty_trash: bool | None = Field(
        default=None, description=_per_library("maintenance", "empty_trash"))


class LibraryOverride(BaseModel):
    """What one Plex library does differently (roadmap row 92).

    Three sections and no more. ``artwork`` is deliberately absent and the
    absence is load-bearing: ``config/loader.py``'s ``render_version`` hashes
    ``config.artwork`` WHOLESALE into the one ``config.version`` every stored
    fingerprint carries, so a per-library artwork setting inside that payload
    would strand ~16,000 fingerprints across libraries it never named, and one
    outside it would silently never invalidate anything. The correct shape is
    roadmap row 111's ``render_version_for(art_kind, config)`` taking a third
    ``library`` argument, so that a library's artwork projection moves that
    library's four versions and nobody else's. Filed, not forgotten.

    ``collections`` and ``playlists`` are absent too, and for a duller reason:
    a definition already targets its own libraries
    (``CollectionDefinition.libraries``, ``PlaylistDefinition.libraries``), so
    a second per-library dimension over the same thing would be two ways to
    say one sentence.
    """

    operations: OperationsOverride | None = Field(
        default=None,
        description=(
            "The metadata-operations settings this library uses instead of "
            "the global ones. Absent settings are the global ones."
        ),
    )
    badges: BadgesOverride | None = Field(
        default=None,
        description=(
            "The badge settings this library uses instead of the global ones. "
            "Absent settings are the global ones."
        ),
    )
    maintenance: MaintenanceOverride | None = Field(
        default=None,
        description=(
            "The Plex housekeeping this library asks for instead of the "
            "global setting. Absent settings are the global ones."
        ),
    )


def _merged_sections(config, override: LibraryOverride, merge) -> dict[str, BaseModel]:
    """One library's whitelisted sections, its stated leaves merged over the
    global ones.

    Returns only the sections the override actually names something in, so a
    caller can hand the result straight to ``model_copy(update=...)`` and
    every unnamed section is carried through by identity.

    ``merge`` is passed in rather than imported, because the one merge this
    codebase has lives in ``config/overrides.py``, which imports THIS module.
    Taking it as an argument keeps the dependency pointing one way and keeps
    the rule in one place: nested mappings merge key by key, everything else
    -- scalars and lists alike -- is replaced.

    Each section is rebuilt through its REAL model, not patched onto the
    existing instance, so every cross-field rule that model carries
    (``operations.field_verbs``' vocabulary, ``badges.families``' closed set)
    runs against the merged result. That is why the partial models above
    re-state none of them.
    """
    sections: dict[str, BaseModel] = {}
    for name, model in (
        ("operations", OperationsConfig),
        ("badges", BadgesConfig),
        ("maintenance", MaintenanceConfig),
    ):
        partial = getattr(override, name)
        if partial is None:
            continue
        delta = partial.model_dump(exclude_unset=True)
        if not delta:
            continue
        sections[name] = model(**merge(getattr(config, name).model_dump(), delta))
    return sections


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

# Roadmap row 222. A ceiling on the definition's own poster URL. Not a limit
# any real image address comes near -- it is a ceiling on what an accident or
# a paste can put into a value this service stores in the config document,
# serves from GET /api/config, carries in an overrides export and folds into a
# hash on every reconcile.
POSTER_URL_MAX_LENGTH = 2048


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
    # Roadmap row 31. Unset means this definition pushes nothing; the
    # deployment ALSO has to set collections.mdblist_sync_apply, so a
    # definition copied from someone else's config cannot start writing to a
    # third-party service on its own.
    sync_to_mdb_list: str | None = Field(
        default=None,
        description=(
            "An MDBList list -- '<user>/<slug>' or a numeric list id -- this "
            "collection's members are added to. Unset pushes nothing."
        ),
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
    # Roadmap row 88, on top of row 143's resolution contract. The default is
    # the library's own granularity, which is what every definition written
    # before this meant. LIST collections only: the smart/`plex_search` side of
    # season/episode collections is rows 173/179 and is refused below.
    builder_level: Literal["item", "season", "episode"] = Field(
        default="item",
        description=(
            "Whether this definition's members are the library's own items, "
            "their seasons, or their episodes."
        ),
    )
    # Roadmap row 89(a). Read-only: the membership is narrowed to what the one
    # configured instance already holds. Kometa's add_missing family -- telling
    # Radarr/Sonarr to ACQUIRE content -- is a declared non-goal.
    radarr_restrict: bool = Field(
        default=False,
        description="Keep only the members Radarr already holds; drop the rest from this collection.",
    )
    sonarr_restrict: bool = Field(
        default=False,
        description="Keep only the members Sonarr already holds; drop the rest from this collection.",
    )
    # Roadmap row 89(b). Unset writes nothing; the deployment ALSO has to set
    # collections.arr_tag_apply, so a definition copied from someone else's
    # config cannot start writing to their Radarr on its own.
    item_radarr_tag: list[str] = Field(
        default_factory=list,
        description="Radarr tags added to every member of this collection that Radarr holds.",
    )
    item_sonarr_tag: list[str] = Field(
        default_factory=list,
        description="Sonarr tags added to every member of this collection that Sonarr holds.",
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
    # Roadmap row 269. Record every resolved member's position in THIS
    # definition's order -- the builder's output after filters and limit,
    # never re-sorted -- for the render pipeline to write as the member's
    # sort title ("<base> <NN>", base = this title through
    # ``facts/franchise_sort.sort_base``). A member that leaves the list is
    # RELEASED: its sort title is blanked and unlocked so Plex derives it
    # again. Read by the pipeline only under ``operations.sort_title_source:
    # collections``, written only under ``operations.sort_title_apply``.
    member_sort: bool = Field(
        default=False,
        description=(
            "Write each member's Plex sort title as '<this title> <NN>' in the "
            "order this definition lists them, so the library's title sort keeps "
            "them together and in order. Needs operations.sort_title_source: "
            "collections and operations.sort_title_apply to reach Plex."
        ),
    )
    # Row 269's sort-only mode: the definition runs for its member effects and
    # creates no Plex collection. NOT Kometa's ``build_collection: false``
    # feeder (that family is a non-goal); it shares only the idea of a
    # definition that creates nothing. Refused without ``member_sort`` by
    # ``_member_sort_needs_an_ordered_builder``: such a definition would do
    # nothing and read as configured.
    create_collection: bool = Field(
        default=True,
        description=(
            "Create and reconcile the Plex collection. False runs the definition "
            "for member_sort only; a collection that already exists under this "
            "title is left alone."
        ),
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
    # Roadmap row 222 (Kometa's ``url_poster``). Ranked BETWEEN the operator's
    # own file under ``assets_root`` and every default this service can find
    # for itself: a file on disk still wins, because an operator who put one
    # there meant it, and a URL still beats a generic default. Fetched through
    # ``net/guard.guarded_download`` -- never ``posters.fetch_poster``, whose
    # lack of a scheme allowlist, address check and redirect control is safe
    # only while every URL it sees was built by this repository.
    poster_url: str | None = Field(
        default=None,
        description=(
            "An http or https address this collection's poster is downloaded "
            "from, through this service's SSRF guard. A poster file under "
            "assets_root still wins; every cached or hosted default is "
            "outranked."
        ),
    )
    # Row 30: take the summary from TMDB instead of writing one by hand -- the
    # id of the TMDB *collection* whose overview this collection borrows.
    # ``summary`` above and a builder's own derived summary (charts, awards,
    # tracearr, the person builders) both still win when set, matching
    # Kometa's own precedence -- on such a definition ``tmdb_summary`` is
    # accepted but does nothing, since the pull never runs.
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

    @field_validator("poster_url")
    @classmethod
    def _poster_url_must_be_a_plain_http_address(cls, value: str | None) -> str | None:
        """Refuse at config LOAD, and say only which field and what shape.

        Row 213. This is an operator's own string, and the class of value
        ``net/guard.py:79-84`` already writes the rule for: it can carry
        ``user:password@`` userinfo, a signed query parameter, or the name of
        an internal host that is itself worth not writing into a line that
        gets pasted into a ticket. What a refusal here reaches is
        ``errors()[...]["msg"]`` -- ``api/routes.py``'s ValidationError seam
        serves exactly those on five config endpoints -- so no message below
        interpolates the value. (pydantic-core also appends its own
        ``input_value=`` tail to ``str(ValidationError)``; nothing serves that
        tail, which is the finding
        ``tests/test_collection_config.py``'s credential-bearing-param test
        records for the eight validators before this one.)

        The userinfo check is this validator's own and not the guard's: the
        guard refuses on the ADDRESS, and ``https://operator:secret@`` resolves
        to a perfectly public host. Refusing it at load is what keeps a
        credential out of the stored config document in the first place -- the
        same argument ``builders/smart_url.py`` makes for refusing a
        token-bearing paste rather than stripping it.
        """
        if value is None:
            return value
        if len(value) > POSTER_URL_MAX_LENGTH:
            raise ValueError(
                f"'poster_url' is longer than {POSTER_URL_MAX_LENGTH} characters"
            )
        if re.search(r"[\s\x00-\x1f\x7f]", value):
            raise ValueError(
                "'poster_url' contains whitespace or control characters; fix "
                "the pasted value"
            )
        try:
            parsed = urlparse(value)
        except ValueError as exc:
            raise ValueError("'poster_url' is not a parsable URL") from exc
        if parsed.scheme not in ("http", "https"):
            raise ValueError("'poster_url' must be an http:// or https:// address")
        if not parsed.hostname:
            raise ValueError("'poster_url' names no host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "'poster_url' carries user:password@ userinfo; remove the "
                "credential from the address"
            )
        return value

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
    def _builder_level_needs_a_builder_that_reads_it(self) -> "CollectionDefinition":
        """Row 88 shipped the LIST half of season/episode collections and
        search-tail E-2 (rows 173/179) shipped the SEARCH half.

        ``plex_search`` and ``smart_filter`` now read ``builder_level`` and
        search at that level -- it becomes the ``type=`` byte of the query
        (``collections/search_url.build_search_url``'s ``search_type``) and,
        for the smart one, of the collection Plex creates. So the blanket
        "a smart collection's members are chosen by Plex" refusal this used to
        be is simply wrong for ``smart_filter``.

        The four smart builders that remain refused derive their query
        themselves and never pass a level to ``build_search_url``: ``dynamic``
        and ``credits_family`` build one per unit, ``cs_bucket`` one per
        content-rating bucket, and ``smart_url`` accepts a pasted Plex URL
        whose ``type=`` the operator has already written. A ``builder_level``
        on any of those would load clean, apply nothing, and read as
        configured -- which is the failure this validator exists for.
        """
        from autoposter.collections.builders import REGISTRY

        if self.builder_level == "item":
            return self
        if getattr(REGISTRY.get(self.builder), "smart", False) and (
            self.builder != "smart_filter"
        ):
            raise ValueError(
                f"'builder_level' does not apply to {self.builder!r}: this "
                "builder derives its own Plex search and never reads the "
                "level, so a season or an episode here would load clean and "
                "apply nothing. 'plex_search' and 'smart_filter' both honour "
                "'builder_level'; use one of those, or a list builder"
            )
        # A non-item builder_level resolves against a season/episode index,
        # so this definition's members are seasons or episodes -- and
        # sync_to_mdb_list pushes THOSE ids (mdblist_sync.push_payload always
        # reports is_movie=ctx.library_type == "Movie", never per-item), which
        # is a season/episode id reported to MDBList as a show. MDBList lists
        # hold movies and shows; there is no season/episode list kind to push
        # to instead, so the only safe reading is to refuse the combination
        # here rather than push the wrong id shape to a list the operator does
        # not own.
        if self.sync_to_mdb_list is not None:
            raise ValueError(
                f"'builder_level' cannot be combined with 'sync_to_mdb_list' "
                f"on {self.title!r}: MDBList lists hold movies and shows, not "
                "seasons or episodes, and pushing this definition's "
                f"{self.builder_level}-level ids there would report them as "
                "show-level ids on a list the operator does not own. Remove "
                "sync_to_mdb_list, or drop builder_level and point this "
                "definition at an item-level collection"
            )
        return self

    @model_validator(mode="after")
    def _arr_overrides_need_a_list_builder_at_item_level(self) -> "CollectionDefinition":
        """Row 89's fields describe MEMBERS an arr instance could know.

        Two combinations cannot mean anything, and both would load clean and do
        nothing visible: a smart collection's members are chosen by Plex, so
        there is no resolved membership to restrict or tag; and a season or an
        episode carries no tmdbId or tvdbId an arr instance holds, so the
        restriction could only ever exclude everything.
        """
        from autoposter.collections.builders import REGISTRY

        named = [
            name for name in
            ("radarr_restrict", "sonarr_restrict", "item_radarr_tag", "item_sonarr_tag")
            if getattr(self, name)
        ]
        if not named:
            return self
        listed = ", ".join(repr(name) for name in named)
        if getattr(REGISTRY.get(self.builder), "smart", False):
            raise ValueError(
                f"{listed} does not apply to {self.builder!r}: a smart "
                "collection's members are chosen by a filter Plex evaluates "
                "itself, so this definition has no resolved membership to "
                "restrict or tag"
            )
        if self.builder_level != "item":
            raise ValueError(
                f"{listed} cannot be combined with builder_level "
                f"{self.builder_level!r}: a season or an episode carries no "
                "tmdb or tvdb id Radarr or Sonarr would know it by, so the "
                "restriction could only ever exclude every member"
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
    def _member_sort_needs_an_ordered_builder(self) -> "CollectionDefinition":
        """Roadmap row 269. A smart builder hands Plex a filter and never holds
        an ordered member list, so there is no order to record: structural for
        every smart builder, which is why this is one validator rather than an
        entry in each builder's ``refused_definition_fields`` table (that
        table is for fields a smart builder COULD apply and chooses not to).
        ``create_collection: false`` on a smart builder is refused for the
        same reason: the smart collection is the definition's only effect.
        And ``create_collection: false`` without ``member_sort`` is a
        definition with no effect at all, refused so it cannot read as
        configured.
        """
        from autoposter.collections.builders import REGISTRY

        if getattr(REGISTRY.get(self.builder), "smart", False):
            if self.member_sort:
                raise ValueError(
                    f"'member_sort' does not apply to {self.builder!r}: a smart "
                    "builder holds no ordered member list to record positions from"
                )
            if not self.create_collection:
                raise ValueError(
                    f"'create_collection: false' does not apply to {self.builder!r}: "
                    "the smart collection is the definition's only effect"
                )
        if not self.create_collection and not self.member_sort:
            raise ValueError(
                "'create_collection: false' needs 'member_sort: true'; a definition "
                "that creates no collection and sorts nothing would do nothing"
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

        Sub-phase C2c added a fourth outcome without adding a collections
        capability: a ``facts`` row -- a value this service holds in its own
        ``item_facts`` table -- refuses HERE, naming row 156, and is readable
        only by an overlay ``condition:``. The tier exists so the badge path
        can read ``item_facts``; it deliberately does not make a collection
        filterable on it.

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
        from autoposter.collections.filters import (
            FACTS_FILTER_ROWS,
            parse_filters,
            predicates,
        )

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
            if row.source == "facts":
                if row.name in FACTS_FILTER_ROWS:
                    # ROADMAP ROW 156, and this is the fence A-2 named being
                    # opened rather than moved. The sparsity story A-2 was
                    # waiting for is now written down: the value is read once
                    # per pass through an OUTER join (collections/
                    # facts_read.py), and a NULL column or a missing facts row
                    # EXCLUDES the item under every operator including `.not`
                    # -- so a partially-gathered library builds a
                    # correct-but-incomplete collection rather than a wrong
                    # one. Coverage and the convergence rate are disclosed in
                    # deploy/README.md. Only the COINED names pass: see below.
                    continue
                # ADJUDICATION A-2 (roadmap row 100, sub-phase C2c), as row
                # 156 leaves it. These two carry KOMETA'S own names -- C2c's
                # condition, the column holding exactly the value space
                # Kometa's filter compares in -- and row 156 opened the
                # collections fence only for facts values under names this
                # service COINED, each carrying its own sparsity note. Neither
                # of these was re-adjudicated for collections, so both stay
                # refused, and the refusal still says where the value is and
                # what CAN read it.
                why = (
                    "this service holds that value in its own item_facts row "
                    "rather than reading it from Plex, and a facts-backed "
                    "COLLECTION filter under KOMETA'S OWN name is not "
                    "shipped: an item_facts column is NULL both for 'the "
                    "provider has nothing for this title' and for 'this item "
                    "has not been gathered yet'. Roadmap row 156 opened this "
                    "gate for the values this service names itself -- "
                    + ", ".join(FACTS_FILTER_ROWS)
                    + " -- each of which declares that sparsity in its own "
                    "note; this attribute was not part of that ruling. The "
                    "same attribute IS available to an overlay `condition:` "
                    "(badges.definitions, badges.families), where the verdict "
                    "is per item and a missing value simply draws no badge"
                )
            elif row.source == "tier2-deferred":
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
    # Roadmap row 31, the deployment-level half of the two gates. Off reports
    # what each definition would push and sends nothing, the same posture
    # apply_to_plex takes for Plex itself.
    mdblist_sync_apply: bool = Field(
        default=False,
        description=(
            "Actually add collection members to the MDBList lists definitions "
            "name; off only reports what would be pushed."
        ),
    )
    # Roadmap row 89(b), the deployment-level half of the two gates. Off
    # reports what each definition would tag and writes nothing, the same
    # posture apply_to_plex takes for Plex itself.
    arr_tag_apply: bool = Field(
        default=False,
        description=(
            "Actually write definitions' item_radarr_tag/item_sonarr_tag tags "
            "to Radarr and Sonarr; off only reports what would be written."
        ),
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
    # Roadmap row 105. A sibling sub-model, and deliberately here rather than
    # under `artwork`: render_version hashes the artwork section wholesale
    # (config/loader.py:48-55) and excludes `collections`, so nothing in here
    # can move a stored render fingerprint. Off by default; see the model.
    poster_title: CollectionPosterTitleConfig = Field(
        default_factory=CollectionPosterTitleConfig,
        description=(
            "Draw a styled collection title, plus a fixed second line, onto "
            "every managed collection poster this service fetches, before "
            "uploading it. Off by default."
        ),
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


# Every ``CollectionDefinition`` field a playlist cannot apply, mapped to why.
# Kometa expresses the same refusal as an allowlist checked at parse time
# (``playlist_attributes``, modules/builder.py:656-679, enforced at :1569 with
# "attribute not compatible with playlists"); this is that list turned inside
# out and moved to config load, with the reason attached to each entry.
#
# It has to be an EXPLICIT refusal rather than "the field is simply not on the
# model". Nothing in this file sets ``model_config``, so pydantic's default
# applies and an unknown key is SILENTLY DROPPED -- which is precisely why
# ``Config._version_check_moved_to_an_env_var`` exists and why
# ``tests/test_example_config_matches_schema.py`` was written. A ``sort_title:``
# copied across from a collection definition would otherwise load clean, apply
# nothing, and read as configured forever.
_REFUSED_PLAYLIST_FIELDS: dict[str, str] = {
    "sort": (
        "a playlist's order is its builder's own output order; Plex has no "
        "per-playlist sort setting to write"
    ),
    "sort_title": "Plex sorts playlists by title alone; there is no sort title to set",
    "collection_mode": (
        "the display mode is a property of a collection's shelf, and a playlist "
        "has no shelf"
    ),
    "labels": (
        "plexapi's Playlist is not a LabelMixin -- a playlist cannot carry a Plex "
        "label at all (pinned in tests/test_plexapi_playlist_contract.py)"
    ),
    "label_sync": (
        "a playlist cannot carry a Plex label, so there is no label set to make "
        "authoritative"
    ),
    "item_label": (
        "member labelling belongs to collections (roadmap row 69); a playlist's "
        "members are not owned by it"
    ),
    "member_sort": (
        "a playlist's order is already Plex's own custom order; member sort "
        "titles (roadmap row 269) are a collection feature"
    ),
    "create_collection": "a playlist has no collection to not create (roadmap row 269)",
    "visible_library": "hub visibility is a collection setting; playlists are never promoted to hubs",
    "visible_home": "hub visibility is a collection setting; playlists are never promoted to hubs",
    "visible_shared": "hub visibility is a collection setting; playlists are never promoted to hubs",
    "hub_priority": "hub ordering is a collection setting; playlists are never promoted to hubs",
    "poster_url": (
        "collection artwork is a collection setting (roadmap row 222); this "
        "service applies no poster to a playlist"
    ),
    "sync_to_mdb_list": (
        "the MDBList push (roadmap row 31) is a collection feature and is not "
        "offered for playlists"
    ),
    "radarr_restrict": "the Radarr/Sonarr member overrides (roadmap row 89) are collection-only",
    "sonarr_restrict": "the Radarr/Sonarr member overrides (roadmap row 89) are collection-only",
    "item_radarr_tag": "the Radarr/Sonarr member overrides (roadmap row 89) are collection-only",
    "item_sonarr_tag": "the Radarr/Sonarr member overrides (roadmap row 89) are collection-only",
    "tmdb_summary": (
        "a playlist's summary is written verbatim; there is no TMDB overview for "
        "a playlist to borrow"
    ),
    "changes_webhook": (
        "the per-collection changes webhook (roadmap row 19) is not wired for "
        "playlists"
    ),
    "filters": (
        "the post-builder filter stage runs inside the collections engine "
        "(engine._run_one, with its tier-2 prefetch); giving playlists a second "
        "copy of it is a declared gap, recorded in roadmap row 98"
    ),
}


class PlaylistDefinition(BaseModel):
    """One operator-configured playlist: a builder plus how to apply it.

    ``CollectionDefinition``'s shape with everything a Plex playlist has
    nowhere to put taken away -- see ``_REFUSED_PLAYLIST_FIELDS`` for the list
    and for why each one is refused BY NAME rather than ignored.

    Two fields are kept that a reader coming from Kometa might not expect.
    ``builder_level`` is how this service says "the members are episodes"
    (roadmap row 143): Kometa flattens a Show or Season into its episodes
    silently, and this codebase already has an explicit way to ask for the same
    thing, so the definition says what it means and the resolution is against
    an index of that level. ``sync_mode: append`` keeps its collection meaning
    -- add only, never remove, never reorder.
    """

    title: str = Field(description="The playlist's title in Plex.")
    builder: str = Field(
        description="Which registered collection builder produces this playlist's members.",
    )
    params: dict = Field(
        default_factory=dict,
        description="The parameters this playlist's builder takes; each builder defines its own shape.",
    )
    # None = every library in the section's scope. An explicit list narrows it
    # AND fixes the search order: the libraries are walked in the order written
    # and the first one that owns an id claims it (see
    # collections/resolve.py::resolve_external_across for why they are never
    # merged into one index).
    libraries: list[str] | None = Field(
        default=None,
        description=(
            "Which libraries this playlist's members are resolved from, searched "
            "in the order given, first match winning. None (the default) means "
            "every library in the playlists section's own scope."
        ),
    )
    summary: str | None = Field(
        default=None,
        description="The playlist's Plex summary, written verbatim. The only metadata a playlist takes.",
    )
    sync_mode: Literal["sync", "append"] = Field(
        default="sync",
        description=(
            "'sync' (the default) makes the playlist exactly the builder's "
            "output, in that order; 'append' only ever adds members, never "
            "removes them and never reorders what is already there."
        ),
    )
    builder_level: Literal["item", "season", "episode"] = Field(
        default="item",
        description=(
            "Whether this playlist's members are the libraries' own items, "
            "their seasons, or their episodes."
        ),
    )
    # Named by TITLE, because it is the only field both kinds of user carry:
    # MyPlexAccount.user() matches a Home user on ``title`` alone -- its own
    # comment says "Home users don't have email, username etc." -- and only
    # the shared branch can use username/email/id. A title is renameable, so
    # the ROW stores MyPlexUser.id and this stores what the operator reads in
    # the Plex UI; the intended consequence is that a renamed user reads as
    # "gone from the configuration" and their copy becomes a REPORTED sweep
    # candidate rather than a silent deletion.
    #
    # "all" is behind playlists.sync_all_users, refused at config load rather
    # than skipped at pass time (PlaylistsConfig._all_users_needs_its_own_gate).
    sync_to_users: list[str] | Literal["all"] | None = Field(
        default=None,
        description=(
            "Which other Plex users this playlist is also copied to, named by "
            "the display title Plex shows for each of them; 'all' means every "
            "user this account shares with, and needs playlists.sync_all_users. "
            "Omitted, the default, copies it to nobody."
        ),
    )
    limit: int | None = Field(
        default=None, ge=1,
        description="A cap on the playlist's member count, applied after resolution.",
    )
    schedule: ScheduleGate | None = Field(
        default=None,
        description=(
            "Gates which reconcile passes this playlist is allowed to run on; "
            "unset means every pass."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _refuse_collection_only_fields(cls, data):
        """A collection-only key is an error, not a dropped key.

        ``mode="before"`` because by ``mode="after"`` pydantic has already
        discarded it -- the same reason ``Config._version_check_moved_to_an_env_var``
        is written that way. Guarded on ``isinstance(data, dict)`` so a
        ``model_copy``/``model_validate`` over an already-built instance (which
        the live-config swap and the tests both do) passes straight through.
        """
        if not isinstance(data, dict):
            return data
        for name, why in _REFUSED_PLAYLIST_FIELDS.items():
            if name in data:
                raise ValueError(
                    f"{name!r} does not apply to a playlist: {why}. Remove it "
                    "from this playlist definition"
                )
        return data

    @field_validator("builder")
    @classmethod
    def _must_be_a_registered_builder(cls, v: str) -> str:
        # Imported at validation time, not module scope, for the cycle
        # CollectionDefinition's own copy of this validator documents.
        from autoposter.collections.builders import REGISTRY

        if v not in REGISTRY:
            raise ValueError(
                f"unknown collection builder {v!r}: known builders are "
                + ", ".join(sorted(REGISTRY))
            )
        return v

    @model_validator(mode="after")
    def _needs_a_plain_list_builder(self) -> "PlaylistDefinition":
        """A playlist is one object with one ordered membership.

        The engine dispatches on exactly these two markers
        (``getattr(builder, "smart", False)`` and ``hasattr(builder,
        "expand")``, ``collections/engine.py``), so they are what this refuses
        on. Both would otherwise load clean and build nothing: a smart builder
        has no ids to hand over at all, and an expanding one hands back whole
        definitions for a family of collections that a single playlist has no
        way to become.
        """
        from autoposter.collections.builders import REGISTRY

        builder = REGISTRY.get(self.builder)
        if getattr(builder, "smart", False):
            raise ValueError(
                f"{self.builder!r} is a smart builder and cannot feed a "
                "playlist: Plex evaluates a smart collection's membership "
                "itself, so there is no ordered list to give one. Point "
                f"{self.title!r} at a list builder"
            )
        if hasattr(builder, "expand"):
            raise ValueError(
                f"{self.builder!r} expands into a FAMILY of collections, and a "
                "playlist is a single object with a single ordered membership. "
                f"Point {self.title!r} at a builder that produces one list"
            )
        return self

    @model_validator(mode="after")
    def _params_must_satisfy_the_builders_own_model(self) -> "PlaylistDefinition":
        """The same check ``CollectionDefinition`` makes, for the same reason:
        a mis-spelled param caught at the moment of the edit rather than
        mid-pass, where the engine contains it as a dead source and the only
        symptom is one playlist quietly not being built.

        No expanding-builder exemption here -- an expanding builder is refused
        outright above, so a placeholder's params can never reach this.
        """
        from autoposter.collections.builders import REGISTRY

        model = getattr(REGISTRY.get(self.builder), "params_model", None)
        if model is None:
            return self
        try:
            model.model_validate(self.params)
        except ValidationError as error:
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
    def _the_list_form_of_all_means_the_same_thing(self) -> "PlaylistDefinition":
        """``sync_to_users: [all]`` (the YAML list form) must mean the same
        thing as ``sync_to_users: all``.

        Without this, ``["all"]`` is a one-element ``list[str]`` as far as the
        union at this field's declaration is concerned -- it never reaches
        ``PlaylistsConfig._all_users_needs_its_own_gate``, and later resolves
        against a user literally titled "all", which does not exist, so the
        playlist silently reaches nobody. Matched case-sensitively, the same
        word the gate refuses.

        A list that mixes "all" with named users is neither meaning and is
        refused outright here, unconditionally: there is no gate that could
        make sense of it.
        """
        if isinstance(self.sync_to_users, list):
            if self.sync_to_users == ["all"]:
                self.sync_to_users = "all"
            elif "all" in self.sync_to_users:
                raise ValueError(
                    f"playlist {self.title!r} sync_to_users mixes 'all' with "
                    "named users: 'all' means every user this server shares "
                    "with and cannot be combined with names. List users "
                    "explicitly, or use 'all' alone"
                )
        return self

    @model_validator(mode="after")
    def _libraries_must_not_be_blank(self) -> "PlaylistDefinition":
        """Kometa's own rule, and its reasoning holds here unchanged
        (modules/builder.py:710-718): an OMITTED ``libraries`` means every
        configured library, but a PRESENT and empty one is an operator who
        wrote something that selects nothing at all.
        """
        if self.libraries is not None and not self.libraries:
            raise ValueError(
                f"playlist {self.title!r} names no library at all: remove "
                "'libraries' to use every configured library, or name the ones "
                "its members should be resolved from"
            )
        return self


class PlaylistsConfig(BaseModel):
    """Every playlist this service builds and owns in Plex.

    The collections section's posture, with one thing genuinely different.
    Collections are owned by a Plex LABEL plus a ``managed_collections`` row;
    a playlist has no labels (plexapi's ``Playlist`` is not a ``LabelMixin``),
    so a playlist is ours if and only if a ``managed_playlists`` row's
    ``plex_rating_key`` names a playlist currently on the server. There is
    consequently no ``ownership_label``, no ``adopt``/``adopt_from`` and no
    ``protect_labels`` here: a playlist this service holds no row for is simply
    never touched, which is what protection would have bought anyway.
    """

    enabled: bool = Field(
        default=True,
        description="Whether this service builds and manages any playlists at all.",
    )
    # Dry run by default, the same posture as collections.apply_to_plex,
    # operations.write_to_plex and badges.upload_to_plex.
    apply_to_plex: bool = Field(
        default=False,
        description="Actually write playlist changes to Plex; off only reports what reconciliation would do.",
    )
    # The SECOND gate, and it is second because it authorises a different
    # thing: apply_to_plex authorises writing to this account, and this
    # authorises writing into other people's. The combination
    # ``apply_to_plex: true`` + ``sync_to_users_apply: false`` -- admin
    # playlists live, user copies reported -- is the deliberate sequencing
    # state, so one switch could not have carried both. The precedents are
    # collections.mdblist_sync_apply and collections.arr_tag_apply, which
    # guard the same shape of second-party write.
    sync_to_users_apply: bool = Field(
        default=False,
        description=(
            "Actually copy playlists into the accounts of the users a "
            "definition's sync_to_users names; off only reports what each of "
            "them would receive."
        ),
    )
    # A gate on the MEANING OF A WORD, checked below at config load.
    sync_all_users: bool = Field(
        default=False,
        description=(
            "Allow a playlist definition to say sync_to_users: all, which "
            "resolves to every user this Plex account shares with."
        ),
    )
    exclude_users: list[str] = Field(
        default_factory=list,
        description=(
            "Users a playlist never syncs to, named by the display title "
            "Plex shows for each of them -- whether sync_to_users: all "
            "resolved to them or a definition named them explicitly."
        ),
    )
    # The fan-out's WIDTH. An `all` that suddenly resolves to two hundred
    # accounts should refuse rather than run.
    max_users: int = Field(
        default=25,
        ge=0,
        description=(
            "The most users one pass may copy playlists to. Past this the "
            "whole user fan-out refuses and reports the numbers instead; 0 "
            "means it is opted in and copies to nobody."
        ),
    )
    # The fan-out's DEPTH, counted over write operations across every
    # (user, playlist) pair in the pass. Past it the fan-out refuses ENTIRELY,
    # for cleanup.max_orphans' reason: writing "the first fifty" of four
    # hundred would be the same accident spread over eight passes.
    max_user_writes: int = Field(
        default=50,
        ge=0,
        description=(
            "The most write operations one pass may issue across every user "
            "copy it manages. Past this the whole user fan-out refuses and "
            "reports the numbers instead; 0 means it is opted in and writes "
            "nothing."
        ),
    )
    # None rather than a second copy of ["Movies", "TV Shows"]: two lists to
    # keep in step is two chances to disagree, and Kometa's own default for a
    # playlist's scope is "every library this run processed".
    libraries: list[str] | None = Field(
        default=None,
        description=(
            "Which Plex libraries a playlist's members may be resolved from, "
            "searched in the order given. None (the default) means every library "
            "in collections.libraries."
        ),
    )
    definitions: list[PlaylistDefinition] = Field(
        default_factory=list,
        description="Operator-configured playlists, each built by one registered list builder.",
    )
    presets: list[str] = Field(
        default_factory=list,
        description=(
            "Preset timeline playlists this service builds, named by key from "
            "the shipped table; an empty list builds none of them."
        ),
    )
    # The only setting here that authorises a delete, and off means REPORTED --
    # the posture collections.delete_unconfigured takes, for the same reasons.
    delete_unconfigured: bool = Field(
        default=False,
        description="Delete a playlist this service owns once no definition builds it any more, instead of only reporting it as orphaned.",
    )
    max_deletes: int = Field(
        default=5,
        ge=0,
        description=(
            "The most playlists one delete sweep may remove. Past this the sweep "
            "refuses entirely and reports the numbers instead; 0 means the sweep "
            "is opted in but deletes nothing."
        ),
    )

    @model_validator(mode="after")
    def _titles_must_not_collide(self) -> "PlaylistsConfig":
        """No two definitions may build the same playlist title.

        Simpler than the collections analogue and stricter for a reason: a
        collection is identified by ``(library, title)``, so two definitions
        aimed at different libraries may share a title. A playlist belongs to no
        library, so ``title`` alone is its identity -- it is
        ``managed_playlists``' unique key and the row the members hash is stored
        on. Two definitions sharing one would overwrite each other on every pass
        and the hash would flap between them forever.

        A definition sharing a PRESET's title is a different thing and is not
        refused here: it is the operator overriding that preset, so
        ``playlist_presets.preset_definitions`` drops the preset and
        ``preset_conflicts`` reports what was displaced.
        """
        seen: dict[str, PlaylistDefinition] = {}
        for definition in self.definitions:
            other = seen.get(definition.title)
            if other is not None:
                raise ValueError(
                    f"two playlist definitions both build {definition.title!r} "
                    f"(builders {other.builder!r} and {definition.builder!r}): a "
                    "playlist belongs to no library, so its title alone "
                    "identifies it and one would overwrite the other on every pass"
                )
            seen[definition.title] = definition
        return self

    @model_validator(mode="after")
    def _presets_must_be_known(self) -> "PlaylistsConfig":
        """Every key in ``presets`` names a row of the shipped table.

        This is the *only* thing that makes a bad key an error.
        ``playlist_presets.preset_definitions`` is written as a scan of the
        table rather than a lookup of this list precisely so it cannot raise
        during validation, so an unknown key does not fail there -- it expands
        to nothing at all, and without this refusal a mis-typed preset would be
        a switch an operator believed they had flipped.

        Two refusals, the two ``CollectionsConfig._presets_must_be_known_and_
        ready`` makes one section along, minus its third: there is no readiness
        tier here, because every playlist preset sits on a builder that
        shipped.

        Imported at validation time, not module scope: ``playlist_presets``
        imports ``PlaylistDefinition`` from this module, the cycle every
        validator in this file documents.
        """
        from autoposter.collections.playlist_presets import BY_KEY

        seen: set[str] = set()
        for key in self.presets:
            if key in seen:
                raise ValueError(
                    f"playlist preset {key!r} is listed twice in 'presets': a "
                    "preset is either switched on or it is not, so a repeated "
                    "key builds nothing extra and means less than it looks like"
                )
            seen.add(key)
            if key not in BY_KEY:
                raise ValueError(
                    f"unknown playlist preset {key!r}: the shipped keys are "
                    + ", ".join(sorted(BY_KEY))
                )
        return self

    @model_validator(mode="after")
    def _all_users_needs_its_own_gate(self) -> "PlaylistsConfig":
        """``sync_to_users: all`` needs ``sync_all_users`` switched on.

        Refused here rather than skipped at pass time, for
        ``_params_must_satisfy_the_builders_own_model``'s stated reason: the
        operator learns at the moment of the edit instead of finding a report
        line six hours later. This validator can make the check because the
        SECTION sees both the flag and every definition; neither half can see
        the other alone.

        The scan is complete over what can carry the word: a preset expands
        from a frozen table that sets no ``sync_to_users`` at all
        (``playlist_presets``), so ``self.definitions`` is the whole
        population, and ``test_a_preset_never_carries_sync_to_users`` pins
        that rather than leaving it to be believed.
        """
        if self.sync_all_users:
            return self
        for definition in self.definitions:
            if definition.sync_to_users == "all":
                raise ValueError(
                    f"playlist {definition.title!r} says sync_to_users: all, "
                    "which copies it into every account this server shares "
                    "with. Set playlists.sync_all_users to true to allow that "
                    "word, or name the users explicitly"
                )
        return self


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


class MergeConfig(BaseModel):
    """Merging the twin ``media_items`` rows a re-key was too late to prevent.
    See ``scheduler/merge.py``.

    Dry run by default, the same posture as ``prune.apply`` and
    ``cleanup.apply``, and for the stricter of the two reasons: this pass
    deletes a row. What makes it safer than the prune is that the row's
    contents are not lost -- renders, facts, credits, dismissals and children
    are repointed onto the surviving twin first -- but a wrong pair is still
    two items collapsed into one, so it stays off until a dry-run report reads
    the way an operator expects.

    The dry run is probe-free on purpose: it elects the survivor by newest
    rating key and touches no Plex at all, so the report can be read during an
    outage. The APPLIED pass probes both rows by key and merges only the pair
    where the survivor's key is accepted and the stale one's is not.
    """

    apply: bool = Field(
        default=False,
        description="Actually merge twin media_items rows; off only reports which pairs would merge.",
    )
    # Sanity caps mirroring prune.max_prunes / max_prune_share. The failure
    # they catch is different from the prune's, and worse: an identity
    # predicate that is wrong -- a library rename that makes two libraries
    # read as one, an import that duplicated external ids -- makes a large
    # part of the library look like twins at once, and every merge deletes a
    # row. Past either cap the pass refuses and reports the numbers.
    max_merges: int = Field(
        default=500,
        description="Refuse a pass whose twin-pair count exceeds this, and report the numbers instead of merging anything.",
    )
    max_merge_share: float = Field(
        default=0.25,
        description=(
            "Refuse a pass whose twin-pair count exceeds this share of the "
            "library, and report the numbers instead of merging anything."
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
    # Roadmap row 52's backfill. Weekly like every other maintenance pass:
    # renders.size_bytes is stamped at render time, so this pass only ever
    # has to catch up rows written before that column existed (and rows whose
    # stat failed at publish). Once the library is measured it finds nothing
    # and costs one indexed SELECT a week.
    asset_stats_days: int = Field(
        default=7,
        description="How often the asset-size backfill sweep runs.",
    )
    # The safety valve -- but unlike drift_batch_size, a batch here is
    # os.stat calls, not renders: a few thousand cost seconds on an NFS
    # mount, not the multi-minute storm a batch of full renders would be.
    # Sized so a ~16,000-artifact library finishes in a handful of weekly
    # passes rather than over a year.
    asset_stats_batch_size: int = Field(
        default=5000,
        description=(
            "The most render rows one asset-size sweep stats (an os.stat "
            "call apiece, not a render), so a large library is measured "
            "gradually rather than all at once."
        ),
    )
    cleanup_days: int = Field(
        default=7,
        description="How often the asset cleanup sweep runs.",
    )
    prune_days: int = Field(
        default=7,
        description="How often the media_items prune sweep runs.",
    )
    merge_days: int = Field(
        default=7,
        description="How often the media_items twin merge runs.",
    )
    pending_deliveries_minutes: int = Field(
        default=15,
        description=(
            "How often the pending-deliveries pass retries deliveries a "
            "server could not take yet."
        ),
    )
    delivery_attempts: int = Field(
        default=8,
        ge=1,
        description=(
            "How many times the pending-deliveries pass retries one row -- an "
            "artwork delivery or a metadata write -- before giving up on it. A "
            "row that runs out is marked failed and stays visible until the "
            "next full pass or catch-up re-arms it; nothing retries forever. A "
            "resolution miss does not count against this: a server that has "
            "not scanned the file yet is a wait, not a failure."
        ),
    )
    catch_up_poll_seconds: int = Field(
        default=60,
        ge=1,
        description=(
            "How often the catch-up drain LOOKS for work. Each catch-up run "
            "carries its own cadence -- the pending-deliveries cadence by "
            "default, shortened by the button that started it -- and this "
            "only bounds how finely that cadence can be honoured."
        ),
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

    The four payload shapes live in ``notify/payload.py``. ``mode`` is a
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
    mode: Literal["apprise-json", "autoposter-v1", "discord", "apprise-api"] = Field(
        default="apprise-json",
        description=(
            "The payload shape. 'apprise-json' (the default) sends the body "
            "Apprise's json:// scheme POSTs; 'autoposter-v1' sends this "
            "service's own versioned shape, carrying the full detail dict; "
            "'discord' sends a Discord webhook embed; 'apprise-api' sends the "
            "{title, body, type} body an Apprise API server accepts."
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
        description=(
            "Where the overlay images referenced by overlay_file are read "
            "from; also the mount badges.definitions' file and name-keyed "
            "sources resolve under, and where a definition's url source is "
            "cached, in a .cache/ subdirectory."
        ),
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
    # This deployment's own address, and the one config value that describes
    # where Autoposter *is* rather than what it manages. It is not
    # `notifications.url` -- that is the outbound target run-completion events
    # are POSTed TO -- and it is not a secret: config/overrides.py's
    # `_reject_secrets` makes a `secrets` key a hard error, and a URL is not
    # one. Deliberately NOT in config/live.py's FROZEN_SECTIONS: nothing built
    # at startup reads it. The setup wizard and the Settings rotation action
    # are its only readers.
    public_url: str = Field(
        default="",
        description=(
            "This deployment's own externally reachable base URL, for example "
            "https://autoposter.example.com. Radarr's and Sonarr's Webhook "
            "connections are pointed at this address plus /webhook/radarr or "
            "/webhook/sonarr. Not a credential; empty means nothing is "
            "registered automatically."
        ),
    )
    plex: PlexConfig | None = Field(
        default=None,
        description=(
            "How this service reaches the Plex server: its address, which libraries "
            "are left alone, and how often it re-checks the server and its token. "
            "Absent means no Plex."
        ),
    )
    jellyfin: JellyfinConfig | None = Field(
        default=None,
        description="How this service reaches a Jellyfin server; absent means no Jellyfin.",
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
    playlists: PlaylistsConfig = Field(
        default_factory=PlaylistsConfig,
        description=(
            "Every playlist this service builds and owns in Plex: the operator's "
            "definitions, the libraries their members are resolved from, and the "
            "ownership and delete-sweep rules they share."
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
    merge: MergeConfig = Field(
        default_factory=MergeConfig,
        description="Merging twin media_items rows left by re-matched items.",
    )
    maintenance: MaintenanceConfig = Field(
        default_factory=MaintenanceConfig,
        description="Plex's own housekeeping operations -- clean bundles, empty trash, optimize.",
    )
    libraries: dict[str, LibraryOverride] = Field(
        default_factory=dict,
        description=(
            "Per-library overrides, keyed by Plex library name: the metadata "
            "operations, badge and maintenance settings that library uses "
            "instead of the global ones. A setting absent from a library's "
            "block is the global setting."
        ),
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
    # Roadmap row 236's "newly actionable" digest: one POST when a full pass
    # closes, carrying the registry flag codes and integer counts of the rows
    # that pass scored. Deliberately NOT a field on NotificationsConfig even
    # though it enables a notification: config/live.py freezes the whole
    # `notifications` prefix because the notifier object is built once at
    # startup, so a knob there would be reported to the operator as "restart to
    # apply" while the emitter in fact re-reads it off the config holder on
    # every scheduler tick -- a false promise the settings editor would make on
    # its own. `public_url` above is the shipped precedent for a top-level
    # setting nothing built at startup reads. Off by default because row 19's
    # rule for `changes` applies here too: an event the shipped integration did
    # not sign up for is opt-in, never a volume change it discovers.
    actionable_digest_enabled: bool = Field(
        default=False,
        description=(
            "Send a notification when a full pass finishes, counting the "
            "renders that pass scored which the Action Center would flag, "
            "grouped by flag code. Off by default. Nothing is sent when the "
            "pass produced nothing actionable. Also requires "
            "notifications.enabled and a configured notifications.url -- "
            "this switch alone sends nothing."
        ),
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
        """``version_check:`` is no longer a config section, and there is
        nothing to migrate it to: the update check now compares this build's
        own release stamp against the newest published GitHub release and
        takes no configuration at all (``api/version.py``). Pydantic ignores
        unknown keys, so without this a deployment that forgot to remove the
        block from its YAML would have it silently dropped and believe it
        still did something, rather than being told it is inert.
        ``mode="before"`` is required to see it at all: by ``mode="after"``
        the key is already gone.
        """
        if isinstance(data, dict) and "version_check" in data:
            raise ValueError(
                "version_check: is no longer a config section -- the update "
                "check is automatic for released builds and takes no settings "
                '(see deploy/README.md\'s "The sidebar\'s update check"); '
                "remove this block"
            )
        return data

    @model_validator(mode="before")
    @classmethod
    def _libraries_refuse_structurally_global_keys(cls, data):
        """A ``libraries:`` block naming a structurally global setting is a
        refusal, not a silent drop.

        ``mode="before"`` is required to see them at all: the partial models
        do not declare these fields, so by ``mode="after"`` pydantic has
        already discarded them and the operator would have a line in their
        YAML that documents a choice this service never makes -- the exact
        failure ``tests/test_example_config_matches_schema.py`` exists for,
        arriving through a different door.

        The message names the path INSIDE a library block and the reason for
        a real ``Config`` section (both constants of this codebase), and
        never the library name the operator typed. For a section that is
        not a real ``Config`` field either -- a typo, or a name this config
        has never had -- naming it would be echoing arbitrary operator text
        rather than a constant of this codebase, so only the fixed reason is
        served, with no name at all (row 213). The document that failed is
        in the pod log, which is where a config-load refusal is read.
        """
        if not isinstance(data, dict):
            return data
        refusals = library_override_refusals(data)
        if not refusals:
            return data
        inside = sorted({
            (
                "" if reason == _UNKNOWN_LIBRARY_SECTION_REASON
                else path.split(".", 2)[2],
                reason,
            )
            for path, reason in refusals
        })
        raise ValueError(
            "libraries: %d setting(s) cannot be overridden per library -- %s"
            % (
                len(refusals),
                "; ".join(why if not name else f"{name} ({why})" for name, why in inside),
            )
        )

    @model_validator(mode="after")
    def _playlist_libraries_must_be_configured(self) -> "Config":
        """Every library a playlist scopes itself to is one this config knows.

        Cross-section, so it cannot live on ``PlaylistsConfig``: the default
        scope IS ``collections.libraries``, and a model validator has no way to
        reach a sibling section.

        This is the half of the media-type question config load can answer. The
        other half -- that a named section is a Movie or Show library and not a
        Music or Photo one, which plexapi refuses to mix in a playlist -- is not
        answerable here at all: this document holds library NAMES and nothing
        that says what type a name is. That refusal belongs to
        ``collections/playlists.py``, which resolves the section and checks its
        type BEFORE the builder runs and before any Plex write.
        """
        configured = self.playlists.libraries
        if configured is None:
            configured = self.collections.libraries
        else:
            unknown = [name for name in configured if name not in self.collections.libraries]
            if unknown:
                raise ValueError(
                    "playlists.libraries names %s, which is not one of "
                    "collections.libraries (%s)"
                    % (", ".join(repr(n) for n in unknown),
                       ", ".join(repr(n) for n in self.collections.libraries))
                )
        known = set(configured)
        for definition in self.playlists.definitions:
            unknown = [name for name in definition.libraries or [] if name not in known]
            if unknown:
                raise ValueError(
                    "playlist %r is scoped to %s, which is not in the playlists "
                    "section's library scope (%s)"
                    % (definition.title,
                       ", ".join(repr(n) for n in unknown),
                       ", ".join(repr(n) for n in sorted(known)))
                )
        return self

    @model_validator(mode="after")
    def _library_names_must_be_configured(self) -> "Config":
        """Every key of ``libraries:`` is a library this config knows.

        Cross-section, so it cannot live on the sub-model: the names come
        from ``collections.libraries``. Refused rather than ignored, and the
        limit is the one ``_playlist_libraries_must_be_configured`` records
        for itself -- this document holds library NAMES and nothing that says
        whether a name is real, so a name absent from ``collections.libraries``
        cannot be told apart from a typo. A typo'd block would otherwise sit
        there overriding nothing, forever, looking like it worked.

        A COUNT rather than the names: the served refusal must not echo what
        the operator typed, and the document is in the pod log for whoever
        needs to see which key it was.
        """
        unknown = [
            name for name in self.libraries
            if name not in self.collections.libraries
        ]
        if unknown:
            raise ValueError(
                "libraries: %d key(s) name a library that is not in "
                "collections.libraries; every key must be one of the "
                "configured library names" % len(unknown)
            )
        return self

    @model_validator(mode="after")
    def _every_library_block_validates(self) -> "Config":
        """Every library's merged sections are built once, here.

        So a bad value inside a ``libraries:`` block is a load-time refusal
        rather than an exception in the render loop an hour later. The merged
        models carry every cross-field rule the global sections have --
        ``operations.field_verbs``' vocabulary, ``badges.families``' closed
        set -- so nothing here re-states one and nothing can drift out of step
        with one. The result is discarded: this validator exists for its
        exceptions.

        The import is local for the reason ``BadgesConfig._check_families``'
        is: ``config/overrides.py`` imports this module, so a module-level
        import here would be a cycle.
        """
        if not self.libraries:
            return self

        from autoposter.config.overrides import _merge

        for override in self.libraries.values():
            _merged_sections(self, override, _merge)
        return self

    @model_validator(mode="after")
    def _at_least_one_media_server(self) -> "Config":
        """A document naming neither ``plex:`` nor ``jellyfin:`` manages
        nothing -- refused at load rather than booting into a service with no
        server to talk to."""
        if self.plex is None and self.jellyfin is None:
            raise ValueError(
                "at least one media server must be configured: a `plex:` or a "
                "`jellyfin:` block"
            )
        return self

    @property
    def configured_servers(self) -> list[str]:
        """Which media servers this document configures, ``plex`` before
        ``jellyfin``."""
        return [name for name in ("plex", "jellyfin") if getattr(self, name) is not None]
