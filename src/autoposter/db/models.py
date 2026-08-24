from datetime import datetime, date

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autoposter.db.base import Base


class MediaItem(Base):
    """One movie, show, season or episode, keyed by its Plex rating key."""

    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rating_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    library: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # movie|show|season|episode
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    tmdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    imdb_id: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    season_number: Mapped[int | None] = mapped_column(Integer)
    episode_number: Mapped[int | None] = mapped_column(Integer)
    root_folder: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    # When the ratings-drift sweep last *tried* this item, as opposed to
    # ItemFacts.fetched_at, which records only when a gather succeeded. An
    # item that can never be resolved would otherwise stay permanently at the
    # front of every sweep and starve everything behind it.
    facts_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The ``upload://`` rating key Plex filed THIS service's clearlogo upload
    # under (artwork_modes/logo.py). NULL means "we never set a logo here", and
    # that is the whole safety promise of the logo revert: it clears only an
    # item whose marker is set *and* whose currently-selected logo is still that
    # exact key, so a logo an operator uploaded (keyed ``upload://`` too) or one
    # they replaced ours with is never touched. Not a ``renders`` row: a logo
    # has none and never will -- it rides into the poster's fingerprint as
    # ``logo_sha`` (render/pipeline.py), and a renders row here would make the
    # revert mode push a logo as though it were a poster base.
    logo_upload_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Render(Base):
    """One artifact for one item. ``fingerprint`` decides whether to re-render."""

    __tablename__ = "renders"
    __table_args__ = (UniqueConstraint("item_id", "art_kind", name="uq_render_item_kind"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    # poster | season_poster | background | title_card
    art_kind: Mapped[str] = mapped_column(String(24))
    # 'generate' composites our own text and fade over textless art.
    # 'verbatim' applies supplied art untouched (the MediUX seam, spec section 11).
    source_mode: Mapped[str] = mapped_column(String(16), default="generate")
    provider: Mapped[str | None] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(Text)
    textless: Mapped[bool | None] = mapped_column(Boolean)
    base_sha256: Mapped[str | None] = mapped_column(String(64))
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    # Separate from `fingerprint`, which covers the base image only. A rating
    # changing must re-badge and re-upload without re-fetching or
    # re-compositing the base.
    badge_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # pending | uploaded | skipped | failed
    # server_default, not just default: `default` is Python-side only, so
    # ADD COLUMN NOT NULL would fail against the populated renders table a
    # deployed instance already has.
    upload_status: Mapped[str] = mapped_column(
        String(24), default="pending", server_default="pending"
    )
    asset_path: Mapped[str] = mapped_column(Text)
    # pending | rendered | truncated | no_art | failed
    status: Mapped[str] = mapped_column(String(24), default="pending")
    detail: Mapped[str | None] = mapped_column(Text)
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Set by the adoption run for artwork that already existed on disk when
    # this service took over. Such a render cannot know the source_url its
    # base came from, so the pipeline compares its fingerprint without that
    # input -- see render_artifact. Cleared as soon as anything genuinely
    # changes and a real render happens.
    adopted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )


class Job(Base):
    """Durable work queue, claimed with SELECT ... FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (
        # Coalescing: at most one *pending* job per dedupe key. Running, done, failed
        # and parked rows are excluded, so an event arriving after work has started
        # still queues a fresh pass.
        Index(
            "uq_jobs_pending_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        Index("ix_jobs_claimable", "state", "run_after"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    # pending | running | done | failed | parked | dismissed
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # An operator asked for this job to stop while it was already ``running``.
    # A claimed job is not interrupted -- a render that has happened cannot be
    # un-rendered, and killing a handler mid-upload would leave Plex holding
    # half the change. So this is a request the worker honours at the *end* of
    # the attempt: on failure the job is dismissed instead of rescheduled
    # (queue/jobs.py's fail()), and on success it completes normally.
    # server_default, not just default: ADD COLUMN NOT NULL would otherwise
    # fail against the populated jobs table a deployed instance already has.
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProviderCache(Base):
    """Keyed provider responses with a TTL (Kometa's cache_expiration semantics)."""

    __tablename__ = "provider_cache"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EventLog(Base):
    """Every webhook received, and what it resolved to."""

    __tablename__ = "events_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))  # radarr|sonarr|manual|notifier
    event_type: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    outcome: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ItemFacts(Base):
    """Current observed truth for one item.

    Separate from ``renders`` so metadata writes and image work are
    independently triggerable — a rating can change without the artwork
    changing, and vice versa.
    """

    __tablename__ = "item_facts"
    __table_args__ = (UniqueConstraint("item_id", name="uq_item_facts_item"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    critic_rating: Mapped[float | None] = mapped_column(Float)
    audience_rating: Mapped[float | None] = mapped_column(Float)
    content_rating: Mapped[str | None] = mapped_column(String(16))
    genres: Mapped[list] = mapped_column(JSONB, default=list)
    studio: Mapped[str | None] = mapped_column(Text)
    originally_available: Mapped[date | None] = mapped_column(Date)
    # Which provider supplied each field, so a later source change is traceable.
    sources: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImdbRating(Base):
    """One IMDb title rating, from the bulk dataset.

    Only ids the library actually contains are stored, so this stays in the
    thousands rather than the 1.7 million rows the dataset carries.
    """

    __tablename__ = "imdb_ratings"

    tconst: Mapped[str] = mapped_column(String(16), primary_key=True)
    rating: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImdbEpisode(Base):
    """Maps a show's IMDb id + season + episode to the episode's own IMDb id."""

    __tablename__ = "imdb_episodes"

    parent_tconst: Mapped[str] = mapped_column(String(16), primary_key=True)
    season_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    tconst: Mapped[str] = mapped_column(String(16), index=True)


class ImdbDatasetState(Base):
    """Per-dataset conditional-request state for the periodic IMDb poll.

    ``dataset`` is ``"ratings"`` or ``"episodes"`` (see facts/imdb.py's
    ``refresh``). ``last_modified``/``etag`` are stored verbatim as received
    from datasets.imdbws.com and sent back unchanged as
    ``If-Modified-Since``/``If-None-Match`` on the next poll -- never parsed
    or compared against local time. ``wanted_hash`` is a sha256 of the sorted
    set of tconsts this dataset was filtered to on the last successful
    refresh; a 304 only means "nothing to do" when this also still matches
    the current wanted set -- otherwise a title imported since that refresh
    was thrown away last time and must be re-extracted from the unchanged
    file.
    """

    __tablename__ = "imdb_dataset_state"

    dataset: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    wanted_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ManagedCollection(Base):
    """A collection this service owns.

    Ownership is the whole point of this table. The Movies library holds 305
    collections and only a small fraction are ours -- the rest are Plex's own
    franchise collections, another tool's, or hand-made by the operator.
    Nothing outside this table is ever modified.
    """

    __tablename__ = "managed_collections"
    __table_args__ = (
        UniqueConstraint("library", "title", name="uq_managed_collection_library_title"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    library: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(255))
    # smart | manual | separator | operator
    # "operator" is a row an operator created directly through a lifecycle
    # endpoint (``api/collections_builders.py::blank_collection``) rather than
    # a definition -- no definition enumerates its title, so it needs its own
    # durable marker to stay out of ``engine._sweep``'s "no definition builds
    # this any more" candidates. Never written except by that endpoint.
    kind: Mapped[str] = mapped_column(String(16), default="smart")
    plex_rating_key: Mapped[str | None] = mapped_column(String(32))
    # Hash of the desired filter and summary, so an unchanged pass writes nothing.
    definition_hash: Mapped[str] = mapped_column(String(64))
    # Hash of the poster we last set, so an unchanged pass uploads nothing.
    # NULL means we have never set this collection's poster -- true for every
    # row that existed before this column, and for one whose fetch failed.
    # Posters are applied to every managed collection, adopted ones included,
    # so a NULL here is exactly what makes the next pass set one even when the
    # definition is unchanged. An operator pins a different poster with a
    # local file, not by leaving this NULL.
    poster_sha256: Mapped[str | None] = mapped_column(String(64))
    # What the last reconcile pass saw. NULL means "no pass has stamped this
    # row yet" -- true for every row that predates these columns, and
    # permanently true for smart and separator rows: Plex evaluates a smart
    # filter live, so there is no membership we could count, and a separator
    # has no members at all. Only list collections
    # (``collections/lists.py``) stamp here.
    member_count: Mapped[int | None] = mapped_column(Integer)
    last_added: Mapped[int | None] = mapped_column(Integer)
    last_removed: Mapped[int | None] = mapped_column(Integer)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImdbMissRefreshState(Base):
    """Rate-limit state for the miss-triggered refresh (see ``facts/imdb.py``).

    A single row, pinned to ``id=1``, recording when a miss-triggered refresh
    was last *attempted* -- successful or not -- using the database clock.
    Held here rather than a process variable so every pod behind the same
    database shares one cooldown window instead of each downloading
    independently when a season pack lands.
    """

    __tablename__ = "imdb_miss_refresh_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConfigOverride(Base):
    """The operator's configuration deltas, deep-merged over the YAML on load.

    A single row, pinned to ``id=1``, holding one JSON document. The mounted
    ``autoposter.yaml`` stays git/Flux-owned and is never written by the app
    (it is read-only in the pod, and an in-app writer would diverge from the
    repository it is delivered from); what the UI edits lands here instead and
    is merged over the file by ``config/overrides.py``.

    One document rather than a key/value row per setting because the merge
    result has to be validated *whole* -- a half-applied config is the thing
    the reload path exists to prevent -- and because "what has the operator
    changed" is then a single readable value.

    ``secrets`` never appears in it: those come from the environment, and
    ``merge_overrides`` rejects the key outright.
    """

    __tablename__ = "config_overrides"
    # "A single row, pinned to id=1" enforced rather than merely documented.
    # Every reader selects id=1 and the writer upserts id=1, so a second row
    # would not be read by anything -- it would sit in the table looking like
    # configuration that is in force while having no effect whatsoever, which
    # is the most expensive kind of wrong a config store can be.
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_config_overrides_single_row"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Session(Base):
    """One logged-in Web UI session, keyed by the SHA-256 hash of its token.

    Only the hash is stored, exactly as for the password: a database dump
    must not hand someone a working session. SHA-256 needs no key stretching
    here -- the token is high-entropy random, unlike the password.
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScheduledRun(Base):
    """When each periodic job last ran.

    In the database rather than in process memory so that restarts do not
    re-run everything, and so two replicas coordinate rather than both firing
    the same pass.
    """

    __tablename__ = "scheduled_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ok | failed
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
