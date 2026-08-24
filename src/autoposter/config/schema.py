import hashlib
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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
    # Soft secrets, same reasoning as mdblist_apikey: both radarr.enabled and
    # sonarr.enabled default to false, so a deployment that never configures
    # either service must still boot. Left empty, ArrClient's requests to
    # that service simply fail (and are caught and logged by the scheduled
    # job), rather than the whole process refusing to start.
    radarr_apikey: str = ""
    sonarr_apikey: str = ""
    # Soft secret, same reasoning as mdblist_apikey: a deployment without it
    # must still boot, just with every Web UI login attempt 401ing -- see
    # api/auth.py and api/routes.py. Never a plaintext password, always a
    # bcrypt hash produced by hash_password().
    admin_password_hash: str = ""
    # Soft secret, same reasoning as mdblist_apikey: the update check is one
    # line in the sidebar, so a deployment without a Harbor robot account must
    # still boot -- it simply reports what it is running and says nothing about
    # newer. Already base64 of `robot$name:secret`, ready to be the value of an
    # `Authorization: Basic` header; see api/version.py.
    harbor_token: str = ""

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
    adopt_from_plex: bool = True


class ScheduleGate(BaseModel):
    """When a definition is allowed to run, inside the one collections pass.

    Deliberately not a second scheduler: the reconcile job keeps its single
    cadence and a gated definition is simply skipped on the runs it does not
    match, leaving its collection untouched.
    """

    # Run on every Nth collections pass. 1 = every pass, the default.
    every_n_runs: int = Field(default=1, ge=1)
    # ...and only during these calendar months, for seasonal collections
    # (Kometa's date-window idiom). None = every month.
    months: list[int] | None = None

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


class CollectionDefinition(BaseModel):
    """One operator-configured collection: a builder plus how to apply it.

    ``builder`` is a registry key, validated against the live registry here --
    at config load -- rather than when the reconcile job eventually runs. A
    typo that only surfaced at run time would look like "that collection just
    stopped updating", hours later and in a log nobody is reading.
    """

    title: str
    builder: str
    # The builder's own params. Untyped here on purpose: each builder validates
    # this through its own pydantic model (see collections/builders/base.py), so
    # the schema does not have to know every builder's shape.
    params: dict = Field(default_factory=dict)
    # None = every library in collections.libraries. An explicit list narrows
    # this definition to those; [] would mean "no library at all", which is why
    # the default is None rather than [].
    libraries: list[str] | None = None
    # Overrides the summary the builder derives, when set.
    summary: str | None = None
    sort: str = "custom"
    # sync = the collection is exactly the builder's output; append only ever
    # adds, never removes. sync is the default, matching the shipped sources.
    sync_mode: Literal["sync", "append"] = "sync"
    # Cap on members, applied after resolution. ge=1: a limit that could only
    # ever produce an empty collection is a mistake, and empty means "make no
    # changes" downstream, so it would not even fail visibly.
    limit: int | None = Field(default=None, ge=1)
    schedule: ScheduleGate | None = None
    # Extra Plex labels, beyond collections.ownership_label.
    labels: list[str] = Field(default_factory=list)
    # Make ``labels`` (plus the ownership label) the authoritative set: any
    # other label on the collection is removed. Off by default because it is
    # the destructive reading of the same field, and it never touches a
    # protected or adopt_from label -- stripping a prior tool's marker is what
    # collections.adopt_removes_prior_label decides, deliberately and once.
    label_sync: bool = False
    # Labels applied to every RESOLVED MEMBER of the collection (Kometa's
    # item_label). Only ever added: a member the source stops naming is no
    # longer a member, and removing a label from it would be a write against
    # an item this definition no longer describes.
    item_label: list[str] = Field(default_factory=list)
    # The collection's Plex sort title, applied verbatim on create and kept in
    # sync afterwards. This is the whole string, not a prefix -- Kometa's
    # ``!110_<title>`` scheme is written out in full. A definition that names a
    # family of collections (a smart builder) gives all of them the same sort
    # title, which is exactly what that scheme is for: the family sorts as one
    # block, ordered by title inside it.
    sort_title: str | None = None
    # Plex's collection display mode. Named values only: the plexapi call
    # rejects anything else with a BadRequest mid-pass, which is a worse place
    # to learn about a typo than config load.
    collection_mode: Literal["default", "hide", "hideItems", "showItems"] | None = None
    # Row 68: pin the collection to a hub. None leaves Plex's current setting
    # alone -- these are Plex Pass features, and "off" is a different request
    # from "not managed by this definition".
    visible_library: bool | None = None
    visible_home: bool | None = None
    visible_shared: bool | None = None
    # Position among the library's managed recommendations, 0 = first. Only
    # meaningful once the collection is promoted to a hub by one of the
    # visible_* flags above.
    hub_priority: int | None = Field(default=None, ge=0)
    # Row 30: take the summary from TMDB instead of writing one by hand -- the
    # id of the TMDB *collection* whose overview this collection borrows.
    # ``summary`` above still wins when both are set: a summary written out in
    # the config is an explicit choice, and a pull that silently overrode it
    # would be a setting that reads as applied and is not.
    tmdb_summary: int | None = Field(default=None, gt=0)

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
    def _membership_knobs_need_a_membership(self) -> "CollectionDefinition":
        """A smart builder has no membership to cap, append to or label.

        Plex evaluates a smart collection's filter live, so ``limit`` and
        ``sync_mode`` have nothing to act on there, and neither has
        ``item_label``: this service never resolves the members, so there is no
        list of items to label. ``tmdb_summary`` goes the same way -- a smart
        definition names a *family* of collections whose summaries the builder
        derives per collection, so one borrowed overview could not be the
        summary of any particular one of them. Accepting any of these silently
        would be the worst outcome: the operator would see a setting that reads
        as applied and never is.

        ``sort_title`` and ``collection_mode`` are deliberately NOT here. They
        are properties of the collection object rather than of its membership,
        and the smart create path applies them (roadmap row 104).
        """
        from autoposter.collections.builders import REGISTRY

        builder = REGISTRY.get(self.builder)
        if not getattr(builder, "smart", False):
            return self
        for field, value, default in (
            ("limit", self.limit, None),
            ("sync_mode", self.sync_mode, "sync"),
            ("item_label", self.item_label, []),
            ("tmdb_summary", self.tmdb_summary, None),
        ):
            if value != default:
                raise ValueError(
                    f"{field!r} does not apply to {self.builder!r}: it builds smart "
                    "collections, whose membership Plex evaluates from a filter"
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


def definition_config_hash(definition: CollectionDefinition) -> str:
    """A content hash of one definition, for detecting edits.

    Distinct from the members hash ``lists.py`` stores: this one changes when
    the *definition* changes (a new limit, a different param, a switch to
    append), not when the underlying list does. Taken over the validated
    model's canonical dump with sorted keys, so re-ordering keys in the YAML --
    which is not an edit -- does not read as one.
    """
    payload = json.dumps(
        definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CollectionsConfig(BaseModel):
    """Every collection this service builds and owns in Plex.

    The Common Sense age-bucket smart collections that replace Kometa's are
    one source among several: the built-in IMDb charts and Oscars awards
    (``charts``/``awards``), the blank divider (``separators``), and any
    number of operator ``definitions`` built by a registered builder
    (``collections/builders``). All of them share the same ownership,
    adoption, protection, poster and delete-sweep rules configured below.
    """

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
    # IMDb Popular / Top 250 / Lowest Rated list collections.
    charts: bool = True
    # Oscars winner list collections (movies only).
    awards: bool = True
    # The blank "Ratings Collections" divider, part of the Common Sense
    # family: a permanently-empty collection whose sort title makes it act
    # as a visual separator in Plex's alphabetised collection list.
    separators: bool = True
    # Take over collections created by a tool this service replaces. Off by
    # default: it is a Plex write against collections we did not create, and
    # it should happen once, deliberately, as part of cutover.
    adopt: bool = False
    # Labels belonging to tools being replaced. A collection carrying one of
    # these, whose title this service manages, is eligible to be claimed. A
    # collection with no label is never eligible -- those are the operator's.
    adopt_from: list[str] = Field(default_factory=lambda: ["Kometa"])
    # Strip the prior tool's label once claimed. Keeping it is reversible;
    # removing it is not, so it is opt-in.
    adopt_removes_prior_label: bool = False
    # Labels belonging to other tools' collections that must never be
    # touched, no matter what -- this wins over ownership and adoption both,
    # even when the collection also carries an ``adopt_from`` label. Default
    # covers Maintainerr, whose "Deleted Soon" collections this service must
    # never claim.
    protect_labels: list[str] = Field(
        default_factory=lambda: ["Collection managed by Maintainerr"]
    )
    # Give every collection this service manages a poster: a local override
    # under assets_root if the operator placed one, otherwise Kometa's hosted
    # default for that collection. Applied only after resolve_collision has
    # approved the collection, so a conflicting or protected one is never
    # reached.
    posters: bool = True
    # Operator-configured collections, each built by a registered builder. The
    # three shipped sources above (charts, awards, separators) are unaffected
    # by this list; it is additive. Live like the rest of this section, so a
    # definition added in Settings applies on the next reconcile.
    definitions: list[CollectionDefinition] = Field(default_factory=list)
    # Delete a collection this service owns once no definition builds it any
    # more -- a chart switched off, a definition removed, a title renamed.
    # Off by default and deliberately the only setting in this file that
    # authorises a delete: with it off the pass reports the orphan instead.
    # Even switched on it deletes only through every guard (the ownership
    # label AND a managed_collections row AND no protected label), and never
    # more than max_deletes in one pass.
    delete_unconfigured: bool = False
    # The per-pass cap on that sweep, the cleanup.max_orphans precedent: past
    # it the sweep refuses entirely and reports the numbers, so a config edit
    # that drops every definition cannot cascade into a wiped library. ge=0
    # because 0 is a meaningful setting -- opted in, but nothing this pass.
    max_deletes: int = Field(default=5, ge=0)

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
        placeholder. The dynamic Oscars year titles are *not* enumerable
        without the ceremony dataset, so a definition titled "Oscars Winners
        2026" is not caught here; the reconcile leaves whichever definition
        runs second in charge, and the roadmap has that as the known gap.
        """
        # Imported at validation time, not module scope: both reach back into
        # this module -- the same cycle CollectionDefinition's builder
        # validator documents.
        from autoposter.collections.engine import definition_titles
        from autoposter.collections.service import LIBRARY_TYPES
        from autoposter.collections.sources import default_definitions

        # default_definitions and the smart builders' titles() read their
        # settings off ``config.collections``; this model IS that section, so
        # it stands in as the whole config for the enumeration.
        shim = SimpleNamespace(collections=self)
        built_in: set[str] = set()
        for library_type in LIBRARY_TYPES.values():
            built_in |= definition_titles(
                default_definitions(shim, library_type), [], library_type, shim
            )

        seen: dict[str, CollectionDefinition] = {}
        for definition in self.definitions:
            if definition.title in built_in:
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
    apply: bool = False
    # Sanity caps on the result of a sweep. If assets_root is repointed, a
    # volume is remounted, library_folders is toggled (which changes the whole
    # naming scheme) or renders is only partly restored, then *every*
    # directory looks orphaned and the non-empty-renders guard still passes.
    # Past either cap the pass refuses and reports the numbers instead of
    # relocating the library. 500 sits far above real weekly churn on a
    # ~16,000-item library (dozens of directories) yet far below any plausible
    # "the tree moved" figure; the share cap catches the same failure on a
    # small tree, where no useful absolute cap would ever fire.
    max_orphans: int = 500
    max_orphan_share: float = 0.25


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
    apply: bool = False
    libraries: list[str] = Field(default_factory=lambda: ["Movies", "TV Shows"])


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
    plex_backup_root: Path = Path("/plexbackup")
    # Dry run by default, per Plex-writing mode -- see the class docstring.
    # Backup is Plex-read-only (it writes to disk) and so carries no apply flag.
    restore_apply: bool = False
    reset_apply: bool = False
    revert_apply: bool = False
    logo_apply: bool = False
    logo_revert_apply: bool = False
    # Shared plausibility caps. 500 sits far above any real operator run on a
    # ~16,000-item library yet far below any "the filter or the mount moved"
    # figure; the share cap catches the same failure on a small library, where
    # no useful absolute cap would ever fire. Mirrors ``cleanup.max_orphans`` /
    # ``max_orphan_share``.
    max_changes: int = 500
    max_change_share: float = 0.25


class SchedulerConfig(BaseModel):
    """Cadences for three of the periodic passes in ``scheduler/jobs.py`` --
    the collections reconcile, the ratings-drift sweep and the asset cleanup --
    and the master switch for all four: the Radarr/Sonarr sync safety net is
    also registered under ``enabled`` (its cadence lives in
    ``ArrSyncConfig.hours``), so disabling the scheduler stops it too and
    missed webhooks then never converge.

    There is deliberately no ``cleanup_apply`` field here even though it
    controls a scheduled job: ``CleanupConfig.apply`` above already means
    exactly that (dry run by default, moves rather than deletes) and
    ``make_cleanup_job`` reads it directly, so this section only owns *when*
    the cleanup pass runs, not whether it writes -- repeating it here would
    give the same behaviour two names.
    """

    enabled: bool = True
    poll_seconds: int = 60
    collections_hours: int = 24
    drift_days: int = 7
    # The safety valve: a sweep enqueues at most this many stale items, so a
    # library of ~16,000 items is worked through gradually rather than all at
    # once.
    drift_batch_size: int = 500
    drift_max_age_days: float = 7
    cleanup_days: int = 7


class RadarrConfig(BaseModel):
    """Registering Plex movies Radarr does not know about. See ``arr/sync.py``.

    ``api_key`` is deliberately not a field here -- it comes from
    ``Secrets.radarr_apikey`` (``AUTOPOSTER_RADARR_APIKEY``), the same
    pattern every other credential in this project follows. Nothing about
    the search flag is configurable: ``sync_section`` always pins
    ``addOptions.searchForMovie`` to ``False``.
    """

    enabled: bool = False
    base_url: str = ""
    # Dry run by default, the same posture as every other outward-facing
    # write in this project: register nothing until the operator opts in.
    add_existing: bool = False
    # Verified live: Plex mounts the library at /mnt/Media (capital M),
    # Radarr sees the same files at /mnt/media (lowercase). Case-sensitive
    # on purpose -- see arr/paths.py.
    plex_path: str = "/mnt/Media"
    arr_path: str = "/mnt/media"
    quality_profile: str = ""
    monitor: bool = True
    minimum_availability: str = "announced"


class SonarrConfig(BaseModel):
    """Registering Plex shows Sonarr does not know about. See ``arr/sync.py``.

    ``api_key`` is deliberately not a field here -- see ``RadarrConfig``'s
    docstring; the same reasoning applies, via
    ``Secrets.sonarr_apikey``/``AUTOPOSTER_SONARR_APIKEY``.
    """

    enabled: bool = False
    base_url: str = ""
    add_existing: bool = False
    plex_path: str = "/mnt/Media"
    arr_path: str = "/mnt/media"
    quality_profile: str = ""
    monitor: bool = True
    season_folder: bool = True
    series_type: str = "standard"


class ArrSyncConfig(BaseModel):
    """The safety net that catches any Plex item this service has never
    processed, plus the cadence for the Radarr/Sonarr registration pass.

    Runs whenever ``enabled`` is true, independently of whether either
    service is configured -- with both ``radarr.enabled`` and
    ``sonarr.enabled`` false, a pass still enqueues unknown items, it just
    registers nothing with either service.
    """

    enabled: bool = True
    hours: int = 24
    # The safety valve, same reasoning as scheduler.drift_batch_size: a first
    # run against a fresh database can find every item unknown.
    batch_size: int = 500


class NotificationsConfig(BaseModel):
    """Outbound run-completion webhooks: one POST to ``url`` per event.

    The two payload shapes live in ``notify/payload.py``. ``mode`` is a
    ``Literal`` on purpose -- an unknown mode must fail validation at config
    load, not fall back to some shape at send time. ``url`` is config, not a
    secret, but may embed a token in its path (Uptime-Kuma-style), so it is
    never logged in full -- host only.
    """

    enabled: bool = False
    url: str = ""
    # "apprise-json" (default): the body Apprise's json:// scheme POSTs --
    # what any Apprise-trained consumer or a `body.type === "success"` gate
    # expects. "autoposter-v1": this service's own versioned shape, carrying
    # the full detail dict.
    mode: Literal["apprise-json", "autoposter-v1"] = "apprise-json"
    # Per-attempt HTTP timeout and the number of attempts before giving up.
    # Notification failure never fails the work it reports on. Bounded at
    # load: retry_count of 0 would build a notifier that attempts nothing
    # and reports every send as failed.
    timeout_seconds: int = Field(10, gt=0)
    retry_count: int = Field(3, ge=1)


class VersionCheckConfig(BaseModel):
    """Where to ask what the newest deployable image is.

    The registry, not the git history: a commit whose image failed to build or
    failed the vulnerability scan was never pushed, so git would report an
    update to something nobody can deploy. Harbor holds exactly the images that
    exist.

    ``harbor_url`` is null by default, which switches the check off entirely --
    the endpoint then reports the running version and nothing about newer. It
    is operator config and is never logged, returned or echoed anywhere; see
    api/version.py for why that matters. The credential is not here: it is the
    ``AUTOPOSTER_HARBOR_TOKEN`` environment variable, like every other secret.
    """

    harbor_url: str | None = None
    project: str = ""
    repository: str = ""


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
    # /docs, /redoc and /openapi.json cannot be put behind the session
    # dependency (FastAPI mounts them itself), and they enumerate every
    # endpoint and its shape to anyone who can reach the port. Off unless a
    # deployment deliberately turns them on.
    api_docs_enabled: bool = False
    plex: PlexConfig
    providers: ProvidersConfig
    artwork: ArtworkConfig
    operations: OperationsConfig = Field(default_factory=OperationsConfig)
    badges: BadgesConfig = Field(default_factory=BadgesConfig)
    collections: CollectionsConfig = Field(default_factory=CollectionsConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    artwork_modes: ArtworkModesConfig = Field(default_factory=ArtworkModesConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    adopt: AdoptConfig = Field(default_factory=AdoptConfig)
    radarr: RadarrConfig = Field(default_factory=RadarrConfig)
    sonarr: SonarrConfig = Field(default_factory=SonarrConfig)
    arr_sync: ArrSyncConfig = Field(default_factory=ArrSyncConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    version_check: VersionCheckConfig = Field(default_factory=VersionCheckConfig)
    # Not a release number: the hash of every setting that changes what a
    # render produces, computed by config/loader.py's render_version and
    # stored on each Render row so a settings change can be detected as
    # staleness. Unrelated to `version_check` above, which is about the image.
    version: str = ""
