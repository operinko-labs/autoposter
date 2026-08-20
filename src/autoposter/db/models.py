from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autoposter.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
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
    asset_path: Mapped[str] = mapped_column(Text)
    # pending | rendered | truncated | no_art | failed
    status: Mapped[str] = mapped_column(String(24), default="pending")
    detail: Mapped[str | None] = mapped_column(Text)
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
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
    # pending | running | done | failed | parked
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
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
    source: Mapped[str] = mapped_column(String(32))  # radarr|sonarr|tautulli|manual
    event_type: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    outcome: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
