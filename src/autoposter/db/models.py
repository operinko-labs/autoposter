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
    SmallInteger,
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
    """One movie, show, season or episode, keyed by a server-neutral identity
    (servers/identity.py). Per-server ids live in media_item_server_refs."""

    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    identity_key: Mapped[str] = mapped_column(Text, unique=True, index=True)
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
    # The credits scan's "we looked" stamp (roadmap rows 197/194) -- the same
    # law facts_attempted_at carries: stamped on every VISITED item, found-
    # no-credits included; NULL means unvisited, and only unvisited.
    credits_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # 'plex_generated' composites over the frame Plex itself derived from the
    # media file, when no provider had a title_card (roadmap row 241).
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
    # pending | uploaded | skipped | failed  (the roll-up of render_deliveries, spec §5.2)
    # server_default, not just default: `default` is Python-side only, so
    # ADD COLUMN NOT NULL would fail against the populated renders table a
    # deployed instance already has.
    upload_status: Mapped[str] = mapped_column(
        String(24), default="pending", server_default="pending"
    )
    asset_path: Mapped[str] = mapped_column(Text)
    # Bytes on disk for the artifact ``asset_path`` names (roadmap row 52).
    # Stamped by render/pipeline.py the moment the file is published -- the
    # one place in this tree that writes a renders asset -- and NULL until
    # then. Nullable with no server_default on purpose: NULL means "never
    # measured", which is exactly what ADD COLUMN gives every row that
    # predates this column, and exactly what GET /api/stats/storage reports
    # as `unknown_size`. A default of 0 would be a lie about every
    # grandfathered row and would make the asset_stats backfill invisible.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # pending | rendered | truncated | no_art | failed | skipped
    #
    # `skipped` was missing from this list while render/pipeline.py wrote it
    # in four places (a disabled art kind, a skip-word title, an unnumbered
    # item, online fetch disabled). No CHECK constraint governs the column in
    # the model or in any migration, so nothing caught it, and the
    # /items/filters dropdown was right only because it reads DISTINCT from
    # the table. An Action Center flag registry written from this comment
    # would have silently dropped a whole status.
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

    # --- Action Center quality facts (roadmap 11a) --------------------------
    #
    # Facts, never verdicts. Which language the ladder took, which provider,
    # which fallbacks it made -- whether any of that is worth an operator's
    # attention is a SQL predicate in actions/flags.py, evaluated against the
    # config that is live when the queue is read. A stored `language_miss`
    # boolean would be a lie the moment an operator re-pointed
    # artwork.poster.language_order, and re-backfilling the library on every
    # config save is worse than a predicate. 11a's own risk note is the
    # naming law: factual ("selected language = en"), never judgemental.
    #
    # All nine are written at render/pipeline.py's write-back block, beside
    # source_mode and for source_mode's own stated reason: the "unchanged"
    # fingerprint short-circuit returns before that block, so a fact recorded
    # there survives a pass that changes nothing -- unlike `detail`, which is
    # unconditionally reset on every successful render.

    # The candidate's own language tag, normalised to the two-letter form the
    # config speaks (providers/ladder.py's normalise_language, so a TVDB
    # "eng" does not read as a miss against every "en"-preferring order).
    # NULL when the provider tagged it with nothing, which is what textless
    # art looks like.
    selected_language: Mapped[str | None] = mapped_column(String(16))
    # Where that language sat in the order in force AT RENDER TIME, from
    # ladder.language_rank; 99 is the ladder's UNRANKED. History, and
    # deliberately not what the flag reads: it is the evidence a row shows an
    # operator, while the judgement is recomputed live. NULL means no ladder
    # ever ranked this row.
    language_rank: Mapped[int | None] = mapped_column(SmallInteger)
    # Where the winning provider sat in the RUNTIME ladder -- the list the
    # pipeline was handed, not config.providers.order. app.py's
    # _build_providers warns and drops a configured provider with no
    # implementation, so a config-index rank can claim a position that never
    # existed on this deployment. NULL for a manual override, which no ladder
    # produced.
    provider_rank: Mapped[int | None] = mapped_column(SmallInteger)
    # The order preferred textless art, no provider had any, and the ladder
    # took a text-bearing image rather than nothing. This is
    # ladder.Selection.is_fallback, which was returned by the ladder from the
    # day it was written and read by nobody until now.
    textless_fallback: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # A poster that wanted a clearlogo, found none on any provider, and drew
    # its title text instead because artwork.logo_text_fallback allowed it.
    logo_text_fallback: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # The base image's pixel dimensions as the provider reported them.
    # CAPTURED, ENFORCED NOWHERE, and no longer waiting on a decision: roadmap
    # row 219 CLOSED on 2026-09-08 by REMOVING artwork.min_width/min_height
    # rather than wiring them, so there is no inert setting left to name here.
    # The columns stay because the Action Center reads them and because a
    # future resolution floor, if one is ever wanted, is then a small change
    # against data already on file instead of a re-backfill of the whole
    # library -- which is 11a's own named risk.
    base_width: Mapped[int | None] = mapped_column(SmallInteger)
    base_height: Mapped[int | None] = mapped_column(SmallInteger)
    # The point size the primary title block finally fitted at, from
    # textfit.FitResult. NULL when no text was drawn -- a logo poster, a
    # verbatim source, a suppressed title. Capture-only for the same reason as
    # the dimensions: the near-miss fit metric is filed, not built.
    text_point_size: Mapped[int | None] = mapped_column(SmallInteger)
    # When the eight facts above were last written. NULL means "never scored
    # under this taxonomy", which is every row that existed before this
    # migration. It is what makes the coverage gap visible instead of letting
    # an unscored row read as a clean one.
    quality_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaItemServerRef(Base):
    """One server's id for one item (spec §4.1). Unique per (server, native_id).

    At most ONE row per ``(item_id, server)`` too -- an invariant
    ``render/pipeline.py``'s ``upsert_server_ref`` enforces by deleting the
    item's other refs for that server, not a constraint. A UNIQUE
    ``(item_id, server)`` could not be one: re-pointing a moved native id
    onto an item that still holds its previous id for that server passes
    through exactly the state such a constraint forbids. ``db/refs.py``
    resolves to the newest ref should a database ever hold two anyway.
    """

    __tablename__ = "media_item_server_refs"
    __table_args__ = (UniqueConstraint("server", "native_id", name="uq_server_ref"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    server: Mapped[str] = mapped_column(String(16))
    native_id: Mapped[str] = mapped_column(String(128))
    library: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RenderDelivery(Base):
    """One server's outcome for one render (spec §5.2). `Render.upload_status`
    stays as the roll-up; this is the per-server truth and the retry queue."""

    __tablename__ = "render_deliveries"
    __table_args__ = (
        UniqueConstraint("render_id", "server", name="uq_delivery_render_server"),
        # PARTIAL, like the metadata table's due index: `run_id` is NULL for
        # every row the ordinary pipeline arms, and the only query that reads
        # the column asks `run_id = <id>`.
        Index("ix_render_deliveries_run_id", "run_id", postgresql_where=text("run_id IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    render_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("renders.id", ondelete="CASCADE"), index=True
    )
    server: Mapped[str] = mapped_column(String(16))
    # uploaded | skipped | failed | pending
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    # How many times this row has been ATTEMPTED and not succeeded. Reset to 0
    # by any terminal-for-now outcome (`uploaded`, `skipped`, `absent`), so the
    # budget is about the current streak of trouble and not about the row's
    # whole history. `scheduler.delivery_attempts` (Task 8) is the cap.
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # The badge fingerprint the last successful upload actually delivered.
    # NULL means "never uploaded, or uploaded before this column existed" --
    # which the catch-up (Task 11) treats as behind, costing one redundant
    # upload per pre-existing row on the first catch-up and nothing after.
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    # The catch-up run that marked this row due (spec §3). NULL for a row the
    # ordinary pipeline armed. ON DELETE SET NULL, not CASCADE: the run
    # history is trimmed at 500 rows per name, and an outcome row must outlive
    # the run that queued it.
    # No `index=True`: the index is the partial one in `__table_args__` above.
    run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runs.id", ondelete="SET NULL", name="fk_render_deliveries_run_id"),
    )
    # What this row said before that run marked it `pending`, so cancelling
    # the run puts it back (spec §3). NULL means the run CREATED the row, and
    # a cancel deletes it rather than inventing a status for it.
    previous_status: Mapped[str | None] = mapped_column(String(24))


class MetadataWrite(Base):
    """One server's metadata outcome for one item (spec §1) -- the sibling of
    ``render_deliveries``, keyed on the ITEM rather than on a render because a
    metadata write has no art kind.

    ``status``: ``written`` (the server took the edits), ``pending`` (refused,
    unreachable, or not resolved there yet -- retried), ``failed`` (the budget
    ran out), ``skipped`` (an exemption, or ``operations.write_to_<server>``
    off -- ``detail`` says which), ``absent`` (the item's library is not
    carried by this server; never resolved, never retried).

    ``detail`` is an exemption reason or a ``deliveries.failure_detail``
    class-name string. Never a URL (spec §1).
    """

    __tablename__ = "metadata_writes"
    __table_args__ = (
        UniqueConstraint("item_id", "server", name="uq_metadata_write_item_server"),
        # PARTIAL: the retry pass is the only reader of this column and it
        # asks one question -- which `pending` rows are due (deliveries.py's
        # `metadata_due`). Every other status leaves `next_attempt_at` NULL,
        # so a full index over ~32k rows would be mostly NULLs maintained on
        # every write for a query that can never want them.
        Index(
            "ix_metadata_writes_next_attempt_at",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
        # PARTIAL for the same reason as its `render_deliveries` twin.
        Index("ix_metadata_writes_run_id", "run_id", postgresql_where=text("run_id IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # No `index=True`: uq_metadata_write_item_server above already indexes
    # (item_id, server) with item_id leading, so every `WHERE item_id = ?`
    # lookup -- the item page's own query, and `apply_metadata`'s absent-set
    # read -- is served by it. A second index would be one more to maintain
    # on every write, over ~32k rows, for nothing.
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE")
    )
    server: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    detail: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    written_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # No `index=True`: the index is the partial one in `__table_args__` above.
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The catch-up run that marked this row due (spec §3). NULL for a row the
    # ordinary pipeline armed. ON DELETE SET NULL, not CASCADE: the run
    # history is trimmed at 500 rows per name, and an outcome row must outlive
    # the run that queued it.
    # No `index=True`: the index is the partial one in `__table_args__` above.
    run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runs.id", ondelete="SET NULL", name="fk_metadata_writes_run_id"),
    )
    # What this row said before that run marked it `pending`, so cancelling
    # the run puts it back (spec §3). NULL means the run CREATED the row, and
    # a cancel deletes it rather than inventing a status for it.
    previous_status: Mapped[str | None] = mapped_column(String(24))


class Job(Base):
    """Durable work queue, claimed with SELECT ... FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (
        # Coalescing: at most one *pending or deferred* job per dedupe key.
        # Running, done, failed and parked rows are excluded, so an event
        # arriving after work has started still queues a fresh pass. Deferred
        # is INCLUDED on purpose (production incident: excluding it let every
        # webhook/sweep event for an item stuck waiting on Plex mint another
        # independent deferred row, unboundedly) -- an item whose add-time job
        # is already waiting must not get a second, parallel wait. Instead
        # queue/jobs.py's enqueue() wakes the existing deferred row when a
        # fresh event names the same key, which is what makes the download
        # webhook still resolve it promptly rather than swallowing the event.
        Index(
            "uq_jobs_pending_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("state IN ('pending', 'deferred')"),
        ),
        Index("ix_jobs_claimable", "state", "run_after"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    # pending | running | deferred | done | done_with_warnings | failed |
    # parked | dismissed
    #
    # ``deferred`` is a wait, not a failure: Plex cannot see the item yet, so
    # the job comes back on a long horizon with no attempt cap at all
    # (queue/jobs.py's DEFER_INTERVAL_SECONDS). It is claimable exactly like
    # pending once ``run_after`` passes, and it is presented as waiting rather
    # than failed -- the Failures page never sees one. No CHECK constraint
    # governs this column, in the model or in any migration, so the vocabulary
    # widens here without one.
    #
    # ``done_with_warnings`` is FINISHED, not failed: the item was processed,
    # but a server it touched ended the pass still owed something, and
    # ``last_error`` holds the sentence naming which. The queue never retries
    # one -- the per-server rows in ``render_deliveries`` and
    # ``metadata_writes`` carry their own retry.
    #
    # The LENGTH is what the vocabulary widened past: ``done_with_warnings``
    # is eighteen characters and the column was ``String(16)``, which
    # PostgreSQL enforces even with no CHECK constraint in sight -- so this
    # one word did need a migration after all (``b3d91f7c05ea``). Twenty-four
    # matches the two outcome tables' own status columns, which is the width
    # this project already reaches for when a state name has to grow.
    state: Mapped[str] = mapped_column(String(24), default="pending", index=True)
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
    # The three prefetch fields (roadmap rows 189/192), named OURS rather than
    # Kometa's -- row 156's law. They are ENUMERATION-only: nothing in this
    # service makes them `filters:`-writable, because a facts-backed filter
    # needs the `facts` source tier and the sparsity story row 156 owns.
    #
    # NOT NULL with a server-side default for the array, exactly like
    # ``genres``: an item TMDb reports no origin country for is honestly `[]`,
    # and ``persist_facts`` never writes a field the gather did not populate,
    # so "found nothing" is a row that keeps whatever it had.
    tmdb_origin_country: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # ISO-639-1 is two characters; 16 leaves room for the locale variants TMDb
    # occasionally sends (``pt-BR``) without inviting a free-text column.
    tmdb_original_language: Mapped[str | None] = mapped_column(String(16))
    # ``belongs_to_collection.id``. The NAME is not stored: see
    # ``facts/tmdb_facts._collection_id``.
    tmdb_collection_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # Roadmap row 100 sub-phase C2c, and the first two columns here that ARE
    # `filters:`-vocabulary names. The three prefetch fields above are
    # enumeration-only because a facts-backed filter needs a `facts` source
    # tier; C2c adds one (`collections/filters.py::SOURCE_TIERS`) and it is
    # REFUSAL-ONLY for a collection, readable by an overlay `condition:`. So
    # row 156's fence is named, not opened, and these two columns carry
    # exactly the value spaces Kometa's own filters compare in.
    #
    # Nullable with NO server-side default, the `80f7d7e25a0c` rule: NULL
    # means "TMDb has not told us", a distinct statement from any value, and
    # `persist_facts` never writes a field the gather did not populate, so a
    # pass that found nothing leaves whatever the row had. It is also the
    # state every existing row is in on upgrade -- adjudication A-4 ships no
    # backfill job; the columns fill on the next facts refresh, and
    # `api/facts_backfill.py` (roadmap row 206) is the operator's fast path.
    #
    # String(32): the value is one of `discover_status`'s six tokens (longest
    # `production`, 10) or, for a status TMDb spells outside them, TMDb's own
    # string (longest of the six, "Returning Series", 16). 32 leaves room
    # without inviting a free-text column -- the same reasoning
    # `tmdb_original_language`'s 16 records.
    tmdb_status: Mapped[str | None] = mapped_column(String(32))
    # TMDb's `last_air_date` -- the show's MOST RECENT episode. Deliberately
    # NOT `originally_available` above, which for a show is TMDb's
    # `first_air_date` (`facts/tmdb_facts.py::parse_show_facts`): the two are
    # opposite ends of the same show and conflating them would badge a
    # long-ended series as AIRING for the fourteen days after its premiere
    # anniversary.
    last_episode_aired: Mapped[date | None] = mapped_column(Date)
    # Which provider supplied each field, so a later source change is traceable.
    sources: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ItemCredit(Base):
    """One (item, credit kind, person) fact from Plex's own credit tags.

    The dedicated many-to-many table roadmap row 197 exists to decide:
    credits cannot live on ``item_facts``, whose
    UNIQUE(item_id) scalar shape is wrong for one-item-many-people. Composite
    PK in the ``ImdbEpisode`` mold; rows exist only for items the library
    contains (FK CASCADE -- the ``ImdbRating`` size discipline). ``person``
    is the PLEX TAG NAME -- the library's own credit data, upstream's
    semantic for its person packs -- not a TMDb id; a TMDb-fed person
    collection is a DIFFERENT membership under the same name and says so
    where it ships.

    The missing-value rule, verbatim from ``collections/facts_enumeration``:
    no row means ABSENT from every query -- never a bucket, never a zero.
    "We looked and found none" is ``media_items.credits_attempted_at`` set
    with zero rows here; "unvisited" is the stamp being NULL.

    **These rows are not a complete cast and must never be read as one.** The
    phase-B probe measured a server-side cap of **200 ``Role`` children per
    item** (docs/research/plex-batch-probe/README.md, D1): 54 of 200 sampled
    shows and 2 of 200 movies return exactly 200 and none more, and a
    single-key fetch returns the same 200, so the truncation is Plex's and
    cannot be read around. An actor missing from a large cast is therefore
    indistinguishable here from an actor Plex never credited. Shows carry
    ACTOR credits only for a separate reason (series-level Plex metadata has
    no director/writer/producer at all), so a show's three other kinds are
    legitimately empty rather than truncated.
    """

    __tablename__ = "item_credits"
    __table_args__ = (
        Index("ix_item_credits_kind_person", "kind", "person"),
    )

    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # actor | director | writer | producer -- collections/credits.CREDIT_KINDS
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    person: Mapped[str] = mapped_column(String(255), primary_key=True)


class ItemMetadataOverride(Base):
    """One metadata field an operator declared the value of, for ONE item.

    Roadmap row 99. This is the operator's word, not a provider's: nothing in
    this service ever writes a row here from a gathered fact, a Plex value or
    a served response. That prohibition is the freezing hazard
    (``frontend/src/api/overrides.ts``) in this row's own vocabulary -- a
    "seed this item's overrides from what Plex currently says" convenience
    would store today's values as overrides and freeze them against every
    future provider change -- and it is pinned by a named test rather than
    left to discipline.

    **Keyed on ``media_items.id``, never on a server's own id.** A Plex
    rating key moves -- a re-match, a library rebuild -- and it is not even a
    column on ``media_items`` any more: it is a ``media_item_server_refs``
    row the pipeline re-points (``render/pipeline.py``'s
    ``upsert_server_ref``). An override table keyed on it would detach
    silently every time that happened, the exact defect the 2026-09-03 era
    spent three tasks closing. Keyed on ``id``, an override survives the move
    for free, and ``scheduler/merge.py`` carries one across a twin merge.

    ``ON DELETE CASCADE`` like every other child of ``media_items``:
    ``scheduler/prune.py`` hard-deletes item rows, and an override must not
    outlive the item it is about.

    ``field`` is OUR field name -- ``plex/writer.py``'s vocabulary
    (``critic_rating``, ``originally_available``), never Plex's attribute and
    never Kometa's spelling. ``value`` is TEXT holding the CANONICAL string
    form the writer compares against: a rating parses as a float in 0-10 and
    is stored rounded to the one decimal the writer compares on, a date as
    ``YYYY-MM-DD``, a list as JSON. One column rather than a typed column per
    shape, because the set of shapes is the writer's and would otherwise have
    to be mirrored here every time it grows.

    One row per ``(item_id, field)`` rather than one JSONB blob per item: the
    delete-is-revert contract then falls out of the row going away, and each
    field carries its own ``updated_at`` for the panel to show.
    """

    __tablename__ = "item_metadata_overrides"
    __table_args__ = (
        UniqueConstraint(
            "item_id", "field", name="uq_item_metadata_override_item_field"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ItemSortPosition(Base):
    """One item's position in the ordered list that owns its sort title.

    Roadmap row 269. Written by the collections pass
    (``collections/member_sort.py``), read by the render pipeline
    (``facts/sort_positions.py``). One row per item AT MOST -- the ownership
    rule (the first definition in config order wins) made structural.

    Keyed on ``media_items.id`` with ``ON DELETE CASCADE`` for
    ``ItemMetadataOverride``'s reason verbatim: a re-key mutates
    ``rating_key`` in place and a child keyed on ``id`` survives it.

    ``released_at`` NULL means the item holds ``position``; set means the
    item left its list and the pipeline owes it one blank-and-unlock write,
    after which it deletes the row. ``base``/``position``/``total`` are kept
    on a released row for the log only.
    """

    __tablename__ = "item_sort_positions"

    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    library: Mapped[str] = mapped_column(String(255), index=True)
    definition_title: Mapped[str] = mapped_column(String(255))
    base: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    # "smart" is a Plex-native smart collection -- Plex evaluates a stored
    # filter and owns the membership. Two builders write it and the row does
    # not distinguish them, deliberately: a Common Sense age bucket
    # (``collections/reconcile.py``) and a ``smart_filter`` definition's
    # collection (``collections/smart.py``) differ in who derived the filter,
    # not in what the row has to remember about the result. Both leave
    # ``member_count`` and the reconcile stamps NULL for the same reason.
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


class ManagedPlaylist(Base):
    """A playlist this service owns.

    ``ManagedCollection`` above is half of an ownership test -- the other half
    is a Plex label on the collection, and ``engine._sweep``'s docstring
    explains why neither alone is enough: "the row alone can name a collection
    somebody else recreated under that title, and the label alone is a
    collection an operator labelled by hand."

    A playlist has no second half. plexapi's ``Playlist`` is not a
    ``LabelMixin`` and carries no label surface at all (pinned in
    ``tests/test_plexapi_playlist_contract.py``), so this row has to carry
    Plex's own identity for the object instead: **a playlist is ours if and
    only if ``plex_rating_key`` names a playlist currently on the server.**
    That predicate is what makes the two hard cases come out right -- a
    same-title playlist somebody else created is not ours, because its rating
    key is not this one; and one of ours that an operator RENAMED still is,
    because a rename does not move the rating key. ``plex_rating_key`` is
    therefore NOT NULL, unlike the collection column of the same name.

    There is no ``library``: a playlist belongs to none. ``libraries`` records
    the definition's SCOPE, for the report and for ``GET /api/playlists``, and
    is not part of any key.

    ``kind`` is absent too, deliberately. Its four collection values all name
    ways a collection can come to exist that a playlist has none of -- there is
    no smart playlist here, no separator, and no operator-blank endpoint
    (``createPlaylist`` refuses an empty item list).
    """

    __tablename__ = "managed_playlists"
    __table_args__ = (
        UniqueConstraint("title", name="uq_managed_playlist_title"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    # Plex casts a playlist's ratingKey to an int; this stores the str() of it,
    # which is what every comparison in collections/playlists.py goes through.
    plex_rating_key: Mapped[str] = mapped_column(String(32), index=True)
    # Hash of the desired state -- the ordered member rating keys, the summary
    # and the sync mode -- so an unchanged pass writes nothing. Computed by
    # lists._members_hash, shared with the collections side rather than
    # reimplemented.
    definition_hash: Mapped[str] = mapped_column(String(64))
    # The definition's library scope when this row was last written, in the
    # order it names them. Recorded for the report; never a key.
    libraries: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # What the last reconcile pass saw. NULL means no pass has stamped this row
    # yet -- true for a row created by a dry run's own bookkeeping and for one
    # written before a pass ever completed.
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


class ManagedPlaylistUser(Base):
    """One person's copy of a playlist this service owns.

    ``ManagedPlaylist`` above answers "is this playlist on the server ours".
    This answers the same question one level down, and it has to be a separate
    row rather than a column on that one because **a copy is a new playlist
    object in a different account with a different rating key**:
    ``Playlist.create`` POSTs ``/playlists`` on whichever server session it is
    handed and returns a brand-new object, and ``server.playlists()`` lists
    only what the token holding that session can see. So the owner's listing
    contains none of these, each user's listing contains only their own, and
    the predicate is:

    **a user's copy is ours if and only if a row's ``plex_rating_key`` names a
    playlist currently in THAT USER'S ``server.playlists()``.**

    Title matching is refused here for the reason it is refused one level up
    (``engine._sweep``'s docstring), plus a sharper one: a playlist somebody
    made in their own account under one of our titles is *theirs*, in a place
    this service has no business tidying.

    ``definition_key`` holds the definition's ``title`` -- a playlist belongs
    to no library, so its title alone is its identity, which is why
    ``managed_playlists.title`` is that table's unique key. It is deliberately
    **not** a foreign key to that table: ``playlists.apply_to_plex`` off with
    ``playlists.sync_to_users_apply`` on is a configuration an operator can
    write, and in it user copies exist while no ``managed_playlists`` row does.
    A NOT NULL foreign key would make that state unrepresentable, and a
    cascading one would delete the only handle this service holds on a live
    playlist in somebody else's account.

    ``plex_user_id`` is ``MyPlexUser.id`` and is the stable identity;
    ``plex_user_title`` is what the operator reads in Plex and what the report
    names, and is never a key -- a title is renameable and an id is not. The
    intended consequence of that split is that a RENAMED user reads as "gone
    from the configuration", so their copy becomes a sweep candidate and is
    *reported* long before ``delete_unconfigured`` could act on it.

    ``definition_hash`` is the value the admin pass already computed for this
    definition (``lists._members_hash``), stored per copy rather than per
    definition: membership is per copy, so a user whose copy is current is
    skipped without a membership read while a user who is behind is not. A
    user newly added to ``sync_to_users`` has no row at all, so no hash gates
    them -- which is why the resolved user set does not need to enter the hash.
    """

    __tablename__ = "managed_playlist_users"
    __table_args__ = (
        UniqueConstraint(
            "definition_key", "plex_user_id", name="uq_managed_playlist_user"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # The definition's title. Indexed because the sweep and the delete
    # endpoint both ask "which copies does this definition have".
    definition_key: Mapped[str] = mapped_column(String(255), index=True)
    plex_user_id: Mapped[int] = mapped_column(Integer)
    # Report only, never a key -- see the class docstring.
    plex_user_title: Mapped[str] = mapped_column(String(255))
    # THE ownership predicate, one level down: str() of that user's playlist's
    # ratingKey, which is what every comparison in
    # collections/playlist_users.py goes through.
    plex_rating_key: Mapped[str] = mapped_column(String(32), index=True)
    definition_hash: Mapped[str] = mapped_column(String(64))
    # What the last pass saw for THIS copy. NULL means no pass has stamped it.
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
    """The configuration this deployment runs, as one JSON document.

    A single row, pinned to ``id=1``. The mounted ``autoposter.yaml`` stays
    git/Flux-owned and is never written by the app (it is read-only in the pod,
    and an in-app writer would diverge from the repository it is delivered
    from); it seeds this row once, on the first boot that finds the store
    empty, and is not read at boot again. What the UI edits lands here, and
    what this row holds is what runs.

    One document rather than a key/value row per setting because it has to be
    validated *whole* -- a half-applied config is the thing the reload path
    exists to prevent -- and because "what is this deployment set to" is then a
    single readable value.

    A row written before the store held whole documents holds a DELTA instead:
    a partial document that ``config/overrides.py`` merges over the mounted
    file. ``meta["format"]`` tells the two apart, and the delta is converted on
    the first load that finds one.

    ``secrets`` never appears in it: those come from the environment or the
    secrets table, and ``config/overrides.py`` refuses the key in every
    document it loads and in every edit it merges.
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
    #: What the store knows about ITSELF, never about the configuration.
    #:
    #: ``format`` is 2 once the row holds the whole document rather than a
    #: delta (config/overrides.py's ``STORE_FORMAT``); an empty object means
    #: the row predates that and is a delta. ``restart_paths`` is the list of
    #: frozen paths saved since the last restart -- kept here rather than in
    #: memory so it survives a reload and shows to a second admin (spec §4).
    #:
    #: Deliberately not inside ``document``: everything in that column is
    #: validated by ``build_config`` and an extra key there would be an
    #: "unknown setting" 422 on the next save.
    meta: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class ConfigOverrideSnapshot(Base):
    """What the overrides document was, immediately before a write replaced it.

    History for the one row this service lets an operator destroy from a web
    page. Captured pre-write inside the writing transaction, so a snapshot
    without its write -- or a write without its snapshot -- cannot exist.

    In the database rather than on a mount, unlike ``metadata_backup``: that
    module's payload is ~16,000 Plex records and belongs on a volume, while
    this is one small JSON object that already lives here. Putting the
    configuration's own recovery path behind a mount would make it depend on
    exactly the thing an operator cannot check while the pod will not start.

    ``path_count`` is stored rather than derived so the listing endpoint can
    answer "3 settings, 14:22 today" without shipping twenty full config
    documents to a page that only needs to label its rows.
    """

    __tablename__ = "config_override_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    path_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 1 for a delta taken before the store became the document, 2 for a
    #: document. Restoring a format-1 snapshot re-runs the merge the delta
    #: described (api/routes.py's restore), which is the whole of spec §8's
    #: "every existing snapshot stays restorable".
    format: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # save | apply | restore | import -- what the write that displaced this
    # document was doing. Not nullable: every writer knows its own reason, and
    # a nullable column would only ever record that somebody forgot.
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class StoredSecret(Base):
    """One secret the operator set from the UI, encrypted.

    The NAME is the environment variable name every other reader of this
    service speaks in (``AUTOPOSTER_TMDB_TOKEN``), so the store, the resolver
    and the UI all key on one vocabulary. The VALUE is a Fernet token under
    the key in the state directory (``config/secret_store.py``): the database
    alone cannot reveal it, which is the trade spec §3 records -- losing the
    volume loses the key and therefore every stored secret.

    No ``source``, no ``set_by``, no history. What the UI needs is the name
    and where the running value came from, and the second is computed by the
    resolver rather than stored.
    """

    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
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


class TmdbRateState(Base):
    """The shared TMDb backoff window (see ``facts/tmdb_budget.py``).

    A single row, pinned to ``id=1``, holding the moment past which TMDb may be
    asked again, on the database clock. Held here rather than in a process
    variable for ``ImdbMissRefreshState``'s reason: every pod behind one
    database then shares one window instead of each discovering the 429 for
    itself.

    ``blocked_until`` is NULL when nothing is known -- the state before the
    first refusal -- and is never cleared afterwards, only moved: a window in
    the past is the same statement as no window at all, and comparing against
    ``now()`` is cheaper than deleting a row on a schedule nobody runs.
    """

    __tablename__ = "tmdb_rate_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the last refusal was seen, for an operator reading the table. Never
    # compared against anything: the window is ``blocked_until``.
    refused_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FactsBackfillState(Base):
    """The one-shot facts backfill's cursor (roadmap row 206).

    A single row, pinned to ``id=1``, ``TmdbRateState``'s shape for
    ``TmdbRateState``'s reason: pods behind one database must share one walk.
    ``cursor_item_id`` is the highest ``media_items.id`` a trigger has
    stamped-and-enqueued; NULL means the backfill has never run. There is no
    "completed" flag on purpose -- completion is derived (no movie/show past
    the cursor), so a library that grows after the walk simply exposes a new
    tail rather than needing a reset nobody built.
    """

    __tablename__ = "facts_backfill_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # BigInteger, not Integer, and not by analogy to ``id`` above: ``id`` is
    # this row's own identity, pinned to a literal 1 forever, while
    # ``cursor_item_id`` COPIES ``media_items.id`` -- which is BigInteger, and
    # whose width is not this table's question to answer. Live id values also
    # track upsert ATTEMPTS rather than row count: the pipeline's
    # ``on_conflict_do_update`` (render/pipeline.py:309-312) burns a sequence
    # value on every re-processed item, because Postgres evaluates ``nextval``
    # before the conflict check. Any column that copies that value inherits
    # its width.
    cursor_item_id: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ActionDismissal(Base):
    """One render row an operator told the Action Center to stop showing.

    Keyed by ``evidence`` -- a SHA-256 over the fact columns the queue judges,
    computed IN SQL (``actions/flags.evidence_expression``) so the queue's
    join is a plain equality and paging and totals stay server-side. The
    dismissal holds only while that hash still matches: a re-render that finds
    Finnish where it found English moves the hash, the join stops matching,
    and the row comes back on its own. That is roadmap 11b's "dismissal that
    sticks until the underlying facts change" and its "dismissals need a
    fingerprint-style identity so a re-render doesn't resurrect dismissed
    rows" -- both, with no sweep and no invalidation job between them.

    The unit is the render row, not the flag. The queue's Dismiss control is
    per row, and hiding a row under one flag while it still showed under
    another would be a button that visibly does nothing. ``flag`` records
    which flag the operator was looking at, for the audit; it does not narrow
    what the dismissal covers.

    ``ON DELETE CASCADE`` like every other child of ``media_items``:
    scheduler/prune.py hard-deletes item rows, and a dismissal must not
    outlive the item it is about.
    """

    __tablename__ = "action_dismissals"
    __table_args__ = (
        UniqueConstraint("item_id", "art_kind", name="uq_action_dismissal_item_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    art_kind: Mapped[str] = mapped_column(String(24))
    flag: Mapped[str | None] = mapped_column(String(32))
    evidence: Mapped[str] = mapped_column(String(64))
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    note: Mapped[str | None] = mapped_column(Text)


class Run(Base):
    """One execution, with a start, an end and an outcome (roadmap row 53).

    Beside ``ScheduledRun``, never instead of it. ``scheduled_runs.name`` is
    UNIQUE and that uniqueness is a scheduler-correctness invariant --
    ``claim_due``'s ``INSERT ... ON CONFLICT DO NOTHING`` and the "at most one
    replica claims" guarantee both rest on it (``scheduler/core.py``) -- so
    that table holds the LAST run of each job and this one holds the history.
    Converting the former into the latter would be a scheduler change wearing
    a stats feature's clothes.

    ``kind`` is ``scheduled`` (one row per ``_maybe_run`` pass) or
    ``full_pass`` (one row per ``POST /api/full-pass``). ``name`` is the
    scheduled job's name, or the literal ``full_pass``; it is not unique and
    is not indexed -- the retention clause keeps the newest
    ``RUN_HISTORY_KEEP`` rows per recorded name, trimmed by two different
    callers for its two kinds of row. A scheduled row is
    bounded by ``scheduler/jobs.py``'s cleanup pass, which holds while
    ``scheduler.enabled``, because every job that records a row here other
    than ``stale_job_reclaim`` is registered behind that same switch
    alongside the trim -- ``stale_job_reclaim`` itself, registered
    unconditionally and five-minutely, records no row at all
    (``scheduler/run_history.py``'s ``UNRECORDED``). A full-pass row is
    bounded instead by the drain-watcher's own close
    (``close_drained_full_passes``), which runs regardless of that switch,
    scoped to ``kind='full_pass'``/``name='full_pass'`` -- because
    ``POST /api/full-pass`` is gated only by ``require_session`` and writes a
    row whether or not the scheduler is enabled. The only query worth an
    index is the endpoint's ``ORDER BY started_at DESC``.

    ``status`` is ``running`` until something closes the row, then ``ok`` or
    ``failed`` for a scheduled job (copied from ``_maybe_run``'s own status),
    or ``ok``/``timed_out`` for a full pass (drained, or past the 24-hour
    ceiling). A scheduled job's row can also read ``interrupted``: the status
    ``open_run`` stamps on a still-open row of the same name it finds when a
    new pass of that name starts, meaning a pod SIGKILL or crash left the
    prior row with nothing to close it. No CHECK constraint governs it,
    matching ``jobs.state`` and ``scheduled_runs.last_status``.

    ``detail`` is row 213 territory and is a COPY, never a re-derivation: for
    a scheduled run it is the string ``scheduler/core.py`` already narrowed to
    a class name (or a ``served_detail`` exception's own reviewed message);
    for a full pass it is a sentence made of counts. Never ``jobs.last_error``,
    never ``str(exc)``, never a path.

    The seven count columns are **window attribution and say so**. No run id
    threads through a ``process_item`` job -- ``app.py``'s handler decodes
    ``RenderIntent(**job.payload)`` and an extra key raises, and
    ``RenderIntent.dedupe_key`` cannot carry one without destroying the
    coalescing the full pass depends on -- so what these columns count is
    everything the worker pool FINISHED between ``started_at`` and
    ``finished_at``, and nothing else. They are stamped only when the run is a
    full pass, which is the only run whose window is its own work; a scheduled
    job's window overlaps whatever the pool happened to be doing, so its
    counts stay NULL. NULL means "not attributed", the ``renders.size_bytes``
    rule -- visible, rather than a zero that lies.

    ``rendered_*`` counts ``renders`` rows whose ``rendered_at`` fell in the
    window, per art kind. That is artifacts RE-COMPOSITED, which is not the
    same number as items visited: the pipeline's fingerprint short-circuit
    returns before the ``rendered_at`` write-back, so a settled library's full
    pass legitimately reports zero composites and tens of thousands
    ``processed``. Both numbers are kept, named apart, for exactly that
    reason.
    """

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # scheduled | full_pass
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    # server_default, not just default: the row is inserted with the database
    # clock in the same transaction as the work it describes, which is what
    # makes `jobs.created_at >= runs.started_at` true by construction for a
    # full pass (both resolve to the same transaction_timestamp()).
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # running | ok | failed | timed_out | interrupted
    status: Mapped[str] = mapped_column(
        String(16), default="running", server_default="running"
    )
    detail: Mapped[str | None] = mapped_column(Text)

    # --- window attribution (see the class docstring) -----------------------
    rendered_poster: Mapped[int | None] = mapped_column(Integer)
    rendered_season_poster: Mapped[int | None] = mapped_column(Integer)
    rendered_background: Mapped[int | None] = mapped_column(Integer)
    rendered_title_card: Mapped[int | None] = mapped_column(Integer)
    processed: Mapped[int | None] = mapped_column(Integer)
    failed: Mapped[int | None] = mapped_column(Integer)
    # A wait, not a failure (jobs.state's own comment). Counted
    # deliberately; `parked` is not -- a parked job is an operator
    # matter the Action Center owns, and a run's rollup is not where it
    # belongs.
    deferred: Mapped[int | None] = mapped_column(Integer)

    # --- the catch-up run's own three columns (spec §3) -------------------
    #
    # A catch-up is a run of kind `catch_up` FOR one media server, so the
    # server belongs on the row rather than being parsed back out of `name`
    # -- the runs list filters and groups by it, and a name is a label.
    # NULL for every other kind.
    server: Mapped[str | None] = mapped_column(String(16))
    # How often this run's backlog is drained. Defaults to the scheduler's
    # pending-deliveries cadence and can be SHORTENED by the button that
    # starts the run, because a catch-up over a large library is thousands of
    # rows drained in batches of 500 and an operator watching it should not
    # have to wait a quarter of an hour per batch.
    cadence_seconds: Mapped[int | None] = mapped_column(Integer)
    # When the drain job last took a batch of this run's rows. The per-run
    # cadence is enforced against this, which is why it is a column and not
    # process state: two replicas share the database and share nothing else.
    last_drained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How many CONSECUTIVE batches of this run have moved nothing. Any batch
    # that moves a row puts it back to 0, and the drain stops the run when it
    # reaches two (`catchup.drain_catch_ups`): a resolution miss is a wait
    # rather than a failure, so it changes neither status nor attempts, and a
    # row for a file the server will never scan would otherwise hold the run
    # -- and with it the in-flight guard that refuses the next catch-up for
    # that server -- open for ever.
    #
    # Its own column rather than a comparison against the three count columns
    # above: a snapshot of the counts cannot tell "this batch moved nothing"
    # from "two in a row moved nothing", so a run that moves rows on
    # alternating batches was stopped at its first idle one -- and one brief
    # outage mid-drain, which turns every row of a batch into a resolution
    # miss, is enough to cause that.
    idle_drains: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
