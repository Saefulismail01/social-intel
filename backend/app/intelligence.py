import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CrowdSnapshot, IngestionRun, PostMention, SocialPost, TrackedToken


SPACE_RE = re.compile(r"\s+")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def normalize_text(text: str) -> str:
    text = URL_RE.sub("<url>", text.lower())
    return SPACE_RE.sub(" ", text).strip()


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def compute_snapshot(db: Session, symbol: str, window_minutes: int = 60) -> CrowdSnapshot:
    token = db.get(TrackedToken, symbol.upper())
    if not token:
        raise ValueError(f"Unknown tracked token: {symbol}")

    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=window_minutes)
    posts = list(db.scalars(
        select(SocialPost).join(PostMention, PostMention.post_id == SocialPost.id).where(
            PostMention.symbol == token.symbol,
            SocialPost.source != "x-grok-cli",
            SocialPost.observed_at >= since,
        )
    ))
    baseline_since = now - timedelta(minutes=window_minutes * 4)
    baseline_posts = list(db.scalars(
        select(SocialPost).join(PostMention, PostMention.post_id == SocialPost.id).where(
            PostMention.symbol == token.symbol,
            SocialPost.source != "x-grok-cli",
            SocialPost.observed_at >= baseline_since,
            SocialPost.observed_at < since,
        )
    ))

    mentions = len(posts)
    authors = Counter(post.author_id for post in posts)
    unique_authors = len(authors)
    top5_share = sum(count for _, count in authors.most_common(5)) / mentions if mentions else 0.0
    duplicate_count = sum(count - 1 for count in Counter(post.normalized_text for post in posts).values() if count > 1)
    duplicate_ratio = duplicate_count / mentions if mentions else 0.0
    avg_engagement = sum(post.likes + post.comments + post.shares + math.log1p(post.views) for post in posts) / mentions if mentions else 0.0
    known_ages = [post.account_age_days for post in posts if post.account_age_days is not None]
    mature_ratio = sum(age >= 30 for age in known_ages) / len(known_ages) if known_ages else 0.5

    baseline_rate = len(baseline_posts) / 3 if baseline_posts else 0.0
    acceleration = mentions / max(1.0, baseline_rate)
    attention = clamp(mentions * 3.0 + min(acceleration, 5) * 9 + math.log1p(avg_engagement) * 4)
    breadth = clamp(unique_authors * 4.0 + (1 - top5_share) * 35)
    coordination = clamp(duplicate_ratio * 75 + top5_share * 25)
    authenticity = clamp(mature_ratio * 55 + (1 - duplicate_ratio) * 30 + (1 - top5_share) * 15)

    sample_confidence = min(1.0, mentions / 20)
    age_coverage = len(known_ages) / mentions if mentions else 0.0
    data_confidence = clamp(sample_confidence * 75 + age_coverage * 25)

    latest_run = db.scalar(select(IngestionRun).order_by(IngestionRun.completed_at.desc()))
    source_age_minutes = None
    if latest_run:
        completed_at = latest_run.completed_at.replace(tzinfo=latest_run.completed_at.tzinfo or timezone.utc)
        source_age_minutes = max(0.0, (now - completed_at).total_seconds() / 60)

    if latest_run is None:
        state = "NO_DATA"
        state_confidence = 1.0
    elif source_age_minutes is not None and source_age_minutes > 15:
        state = "STALE"
        state_confidence = 1.0
    elif mentions < 3:
        state = "INSUFFICIENT_DATA"
        state_confidence = 0.9
    elif attention < 20:
        state = "DORMANT"
        state_confidence = 0.65
    elif coordination >= 58 or breadth < 35:
        state = "SEEDING"
        state_confidence = 0.68
    elif breadth < 65 or unique_authors < 12:
        state = "EMERGING"
        state_confidence = 0.72
    else:
        state = "BROADENING"
        state_confidence = 0.8

    metrics = {
        "mentions": mentions,
        "unique_authors": unique_authors,
        "top5_author_share": round(top5_share, 4),
        "duplicate_ratio": round(duplicate_ratio, 4),
        "average_engagement": round(avg_engagement, 2),
        "mention_acceleration": round(acceleration, 2),
        "account_age_coverage": round(age_coverage, 4),
        "source_age_minutes": round(source_age_minutes, 2) if source_age_minutes is not None else None,
    }
    contributions = {
        "attention": {
            "mention_volume": round(min(mentions * 3.0, 45), 1),
            "acceleration": round(min(acceleration, 5) * 9, 1),
            "engagement": round(math.log1p(avg_engagement) * 4, 1),
        },
        "breadth": {
            "unique_authors": round(min(unique_authors * 4.0, 65), 1),
            "low_concentration": round((1 - top5_share) * 35, 1),
        },
        "coordination": {
            "duplicate_content": round(duplicate_ratio * 75, 1),
            "author_concentration": round(top5_share * 25, 1),
        },
    }

    snapshot = CrowdSnapshot(
        symbol=token.symbol,
        window_minutes=window_minutes,
        crowd_state=state,
        state_confidence=state_confidence,
        attention_score=attention,
        breadth_score=breadth,
        authenticity_score=authenticity,
        coordination_score=coordination,
        data_confidence=data_confidence,
        metrics_json=metrics,
        contributions_json=contributions,
        observed_at=now,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot
