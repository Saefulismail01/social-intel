from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TrackedToken(Base):
    __tablename__ = "tracked_tokens"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    canonical_pair: Mapped[str] = mapped_column(String(40), unique=True)
    lana_phase: Mapped[str] = mapped_column(String(32), default="NORMAL")
    source: Mapped[str] = mapped_column(String(64), default="fixture")
    priority: Mapped[int] = mapped_column(Integer, default=3)
    # False = left Lana kanban; row + X baselines / posts are kept for history.
    active: Mapped[bool] = mapped_column(Integer, default=1)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SocialPost(Base):
    __tablename__ = "social_posts"
    __table_args__ = (UniqueConstraint("source", "source_post_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), default="fixture")
    source_post_id: Mapped[str] = mapped_column(String(128))
    author_id: Mapped[str] = mapped_column(String(128), index=True)
    author_name: Mapped[str] = mapped_column(String(256), default="")
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    public_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    verification_type: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    card_type: Mapped[str] = mapped_column(String(64), default="")
    content_type: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    account_age_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PostMention(Base):
    __tablename__ = "post_mentions"
    __table_args__ = (UniqueConstraint("post_id", "symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("social_posts.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("tracked_tokens.symbol"), index=True)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="SUCCESS")
    received_count: Mapped[int] = mapped_column(Integer, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    batches_observed: Mapped[int] = mapped_column(Integer, default=0)
    matched_post_count: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_post_count: Mapped[int] = mapped_column(Integer, default=0)


class CollectorHeartbeat(Base):
    __tablename__ = "collector_heartbeats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="STARTING")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    batches_observed: Mapped[int] = mapped_column(Integer, default=0)
    matched_posts: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_posts: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)


class SearchCoverage(Base):
    __tablename__ = "search_coverage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    query: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    pages_scanned: Mapped[int] = mapped_column(Integer, default=0)
    responses_observed: Mapped[int] = mapped_column(Integer, default=0)
    matched_posts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    message: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)


class XSliceCoverage(Base):
    """One harvested (symbol, time-slice) of X search.

    The X keyword-search tool returns at most 10 posts per call and only the
    newest ones inside the queried window, so a symbol's timeline is only ever
    reconstructed slice by slice. This table records which slices have actually
    been scanned so backfills resume instead of rescanning, gaps stay visible,
    and radar counts can be reported against real coverage.
    """

    __tablename__ = "x_slice_coverage"
    __table_args__ = (UniqueConstraint("symbol", "slice_start", "slice_end"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    slice_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    slice_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    query: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="SUCCESS")
    posts_found: Mapped[int] = mapped_column(Integer, default=0)
    saturated: Mapped[int] = mapped_column(Integer, default=0)
    split_depth: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class XVolumeBaseline(Base):
    """X Radar's own daily post count for a symbol — the authoritative denominator.

    Radar reports counts only: no authors, no engagement, no post bodies
    (`allow_unique_users` and `allow_impressions` are both false on this
    subscription). So it cannot replace harvesting; what it provides is the
    number harvested posts should be measured against, turning "we collected
    340" into "we collected 340 of 498".
    """

    __tablename__ = "x_volume_baseline"
    __table_args__ = (UniqueConstraint("symbol", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    day: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    query: Mapped[str] = mapped_column(String(256), default="")
    source: Mapped[str] = mapped_column(String(64), default="x-radar")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CrowdSnapshot(Base):
    __tablename__ = "crowd_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("tracked_tokens.symbol"), index=True)
    window_minutes: Mapped[int] = mapped_column(Integer, default=60)
    crowd_state: Mapped[str] = mapped_column(String(32))
    state_confidence: Mapped[float] = mapped_column(Float)
    attention_score: Mapped[float] = mapped_column(Float)
    breadth_score: Mapped[float] = mapped_column(Float)
    authenticity_score: Mapped[float] = mapped_column(Float)
    coordination_score: Mapped[float] = mapped_column(Float)
    data_confidence: Mapped[float] = mapped_column(Float)
    metrics_json: Mapped[dict] = mapped_column(JSON)
    contributions_json: Mapped[dict] = mapped_column(JSON)
    score_version: Mapped[str] = mapped_column(String(32), default="crowd-v0.1.0")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
