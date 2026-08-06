from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CollectorHeartbeat


def update_heartbeat(
    db: Session,
    source: str,
    status: str,
    batches: int = 0,
    matched: int = 0,
    unmatched: int = 0,
    message: Optional[str] = None,
) -> None:
    row = db.scalar(select(CollectorHeartbeat).where(CollectorHeartbeat.source == source))
    if row is None:
        row = CollectorHeartbeat(source=source)
        db.add(row)
    row.status = status
    row.last_seen_at = datetime.now(timezone.utc)
    row.batches_observed = batches
    row.matched_posts = matched
    row.unmatched_posts = unmatched
    row.message = message
    db.commit()
