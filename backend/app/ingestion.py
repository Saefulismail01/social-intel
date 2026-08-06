from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .intelligence import normalize_text
from .models import IngestionRun, PostMention, SocialPost, SquareAuthor, TrackedToken
from .schemas import SanitizedIngestRequest

X_SOURCE = "x-grok-cli"
SNOWFLAKE_EPOCH_MS = 1_288_834_974_657
MAX_TIMESTAMP_DRIFT = timedelta(minutes=5)


def snowflake_time(post_id: str) -> datetime | None:
    """Creation time encoded in an X post ID, or None if it is not a snowflake."""
    if not post_id.isdigit():
        return None
    value = int(post_id)
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(((value >> 22) + SNOWFLAKE_EPOCH_MS) / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def timestamp_disagrees(source: str, post_id: str, observed_at: datetime) -> bool:
    """True when a claimed X timestamp contradicts the post's own ID.

    Search results have arrived carrying posts from the same date a year
    earlier, restated as the requested year. The ID is self-verifying, so a
    disagreement means the timestamp is wrong and the row must not be stored.
    """
    if source != X_SOURCE:
        return False
    real = snowflake_time(post_id)
    if real is None:
        return True
    claimed = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=timezone.utc)
    return abs(real - claimed) > MAX_TIMESTAMP_DRIFT


def derive_run_status(received: int, accepted: int, rejected: int) -> str:
    """Honest IngestionRun status from the actual counts.

    The run used to read SUCCESS no matter how many posts were dropped; that hid
    silent data loss behind a green light. A batch where every post was rejected
    is not a success, and a batch that lost some is only a partial one.
    """
    if received == 0:
        return "EMPTY"
    if rejected == 0:
        return "SUCCESS"
    if accepted == 0:
        return "REJECTED"
    return "PARTIAL"


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _record_author(db: Session, record, detected_at: datetime) -> None:
    """Passively populate the Square author registry from observed posts.

    No polling, no session farms — this only mirrors authors we have already
    seen through ingest. The registry is a denormalized index, not a control
    plane. Idempotent upsert keyed on author_id.
    """
    if not record.author_id:
        return
    row = db.scalar(select(SquareAuthor).where(SquareAuthor.author_id == record.author_id))
    if row is None:
        row = SquareAuthor(
            author_id=record.author_id,
            author_name=record.author_name,
            verification_type=record.verification_type,
            post_count=1,
            first_seen_at=detected_at,
            last_seen_at=detected_at,
            last_post_id=record.source_post_id,
        )
        db.add(row)
    else:
        row.author_name = record.author_name or row.author_name
        if record.verification_type is not None:
            row.verification_type = record.verification_type
        row.post_count = (row.post_count or 0) + 1
        # first_seen_at is immutable — keep the earliest observation.
        row.last_seen_at = max(_as_utc(row.last_seen_at) or detected_at, detected_at)
        row.last_post_id = record.source_post_id


def ingest_sanitized(db: Session, payload: SanitizedIngestRequest) -> dict:
    tracked = set(db.scalars(select(TrackedToken.symbol)))
    inserted = updated = rejected = 0
    rejected_untracked = 0
    rejected_drift = 0
    affected: set[str] = set()
    detected_at_now = datetime.now(timezone.utc)

    for record in payload.posts:
        symbols = [symbol for symbol in record.symbols if symbol in tracked]
        if not symbols:
            rejected += 1
            rejected_untracked += 1
            continue
        # X posts still use the snowflake self-check against observed_at
        # (the publish time for X). Square posts carry published_at but no
        # snowflake, so the check is a no-op for them.
        observed_at = _as_utc(record.published_at) or _as_utc(record.detected_at)
        if observed_at is None:
            rejected += 1
            continue
        if timestamp_disagrees(payload.source, record.source_post_id, observed_at):
            rejected += 1
            rejected_drift += 1
            continue

        post = db.scalar(select(SocialPost).where(
            SocialPost.source == payload.source,
            SocialPost.source_post_id == record.source_post_id,
        ))
        detected_at = _as_utc(record.detected_at) or detected_at_now

        if post is None:
            post = SocialPost(
                source=payload.source,
                source_post_id=record.source_post_id,
                author_id=record.author_id,
                author_name=record.author_name,
                text=record.text,
                normalized_text=normalize_text(record.text),
                public_url=record.public_url,
                verification_type=record.verification_type,
                card_type=record.card_type,
                content_type=record.content_type,
                observed_at=observed_at,
            )
            # Square-specific enrichment fields (teardown §15.3). X rows
            # leave these at defaults; the model's defaults handle that.
            post.published_at = _as_utc(record.published_at)
            post.first_detected_at = detected_at
            post.last_observed_at = detected_at
            post.share_url = record.share_url
            post.coin_pairs = ",".join(record.coin_pairs)
            post.tendency = record.tendency
            post.bullish_ratio = record.bullish_ratio
            post.bearish_ratio = record.bearish_ratio
            post.hashtags = "\n".join(record.hashtags)
            post.mentions = "\n".join(record.mentions)
            post.is_reply = 1 if record.is_reply else 0
            post.parent_id = record.parent_id
            post.is_sticky = 1 if record.is_sticky else 0
            post.media_urls = "\n".join(record.media_urls)
            post.detection_path = record.detection_path
            db.add(post)
            db.flush()
            inserted += 1
        else:
            updated += 1
            post.author_name = record.author_name
            post.text = record.text
            post.normalized_text = normalize_text(record.text)
            post.public_url = record.public_url
            post.verification_type = record.verification_type
            # Merge observations across detection paths (teardown §16):
            # first_detected_at is immutable (earliest wins), last_observed_at
            # advances to the newest observation. published_at is set from
            # the first observation if it was missing (back-compat for rows
            # created before this enrichment).
            if post.first_detected_at is None:
                post.first_detected_at = detected_at
            else:
                existing = _as_utc(post.first_detected_at)
                if existing and detected_at < existing:
                    post.first_detected_at = detected_at
            if post.last_observed_at is None or detected_at > _as_utc(post.last_observed_at):
                post.last_observed_at = detected_at
            if post.published_at is None:
                post.published_at = _as_utc(record.published_at)
            # Enrichment fields are filled in on update if they were empty,
            # but never overwrite a value already set (first observation wins
            # for native PGC fields; engagement below is always latest).
            if record.share_url and not post.share_url:
                post.share_url = record.share_url
            if record.coin_pairs and not post.coin_pairs:
                post.coin_pairs = ",".join(record.coin_pairs)
            if record.tendency and not post.tendency:
                post.tendency = record.tendency
            if record.bullish_ratio is not None and post.bullish_ratio is None:
                post.bullish_ratio = record.bullish_ratio
            if record.bearish_ratio is not None and post.bearish_ratio is None:
                post.bearish_ratio = record.bearish_ratio
            if record.hashtags and not post.hashtags:
                post.hashtags = "\n".join(record.hashtags)
            if record.mentions and not post.mentions:
                post.mentions = "\n".join(record.mentions)
            if record.parent_id and not post.parent_id:
                post.parent_id = record.parent_id
            if record.media_urls and not post.media_urls:
                post.media_urls = "\n".join(record.media_urls)
            if record.detection_path and not post.detection_path:
                post.detection_path = record.detection_path
            # is_reply / is_sticky are monotonic: once true, stay true.
            if record.is_reply:
                post.is_reply = 1
            if record.is_sticky:
                post.is_sticky = 1
        post.likes = record.engagement.likes
        post.comments = record.engagement.comments
        post.shares = record.engagement.shares
        post.views = record.engagement.views

        existing = set(db.scalars(select(PostMention.symbol).where(PostMention.post_id == post.id)))
        for symbol in symbols:
            affected.add(symbol)
            if symbol not in existing:
                db.add(PostMention(post_id=post.id, symbol=symbol))

        # Passively register the author from observed posts (teardown §22.3).
        if payload.source != X_SOURCE:
            _record_author(db, record, detected_at)

    received = len(payload.posts)
    accepted = inserted + updated
    status = derive_run_status(received, accepted, rejected)
    error_category = None
    if status in ("REJECTED", "PARTIAL"):
        # Name the dominant cause so the desk can tell untracked-symbol batches
        # (a config gap) from timestamp-drift batches (a bad source).
        if rejected_untracked >= rejected_drift:
            error_category = "untracked_symbols"
        else:
            error_category = "timestamp_drift"

    run = IngestionRun(
        source=payload.source,
        status=status,
        received_count=received,
        inserted_count=inserted,
        updated_count=updated,
        rejected_count=rejected,
        collected_at=payload.collected_at,
        error_category=error_category,
    )
    db.add(run)
    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "rejected": rejected,
        "status": status,
        "error_category": error_category,
        "affected_symbols": sorted(affected),
        "ingestion_run_id": run.id,
    }
