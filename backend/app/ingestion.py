from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .intelligence import normalize_text
from .models import IngestionRun, PostMention, SocialPost, TrackedToken
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


def ingest_sanitized(db: Session, payload: SanitizedIngestRequest) -> dict:
    tracked = set(db.scalars(select(TrackedToken.symbol)))
    inserted = updated = rejected = 0
    affected: set[str] = set()

    for record in payload.posts:
        symbols = [symbol for symbol in record.symbols if symbol in tracked]
        if not symbols:
            rejected += 1
            continue
        if timestamp_disagrees(payload.source, record.source_post_id, record.observed_at):
            rejected += 1
            continue
        post = db.scalar(select(SocialPost).where(
            SocialPost.source == payload.source,
            SocialPost.source_post_id == record.source_post_id,
        ))
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
                observed_at=record.observed_at,
            )
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
        post.likes = record.engagement.likes
        post.comments = record.engagement.comments
        post.shares = record.engagement.shares
        post.views = record.engagement.views

        existing = set(db.scalars(select(PostMention.symbol).where(PostMention.post_id == post.id)))
        for symbol in symbols:
            affected.add(symbol)
            if symbol not in existing:
                db.add(PostMention(post_id=post.id, symbol=symbol))

    run = IngestionRun(
        source=payload.source,
        status="SUCCESS",
        received_count=len(payload.posts),
        inserted_count=inserted,
        updated_count=updated,
        rejected_count=rejected,
        collected_at=payload.collected_at,
    )
    db.add(run)
    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "rejected": rejected,
        "affected_symbols": sorted(affected),
        "ingestion_run_id": run.id,
    }
