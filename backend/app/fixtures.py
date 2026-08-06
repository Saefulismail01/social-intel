import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from .ingestion import ingest_sanitized
from .models import TrackedToken
from .schemas import PublicEngagement, SanitizedIngestRequest, SanitizedSquarePost


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_universe(db: Session, path: Path) -> int:
    records = json.loads(path.read_text())
    for record in records:
        symbol = record["symbol"].upper().replace("USDT", "")
        token = db.get(TrackedToken, symbol) or TrackedToken(symbol=symbol, canonical_pair=f"{symbol}USDT")
        token.lana_phase = record.get("lana_phase", "NORMAL")
        token.source = record.get("source", "fixture")
        token.priority = record.get("priority", 3)
        token.active = 1
        token.metadata_json = record.get("metadata", {})
        db.add(token)
    db.commit()
    return len(records)


def ingest_posts(db: Session, records: list[dict], source: str = "fixture") -> int:
    posts = [SanitizedSquarePost(
        source_post_id=str(record["id"]),
        published_at=parse_time(record["observed_at"]),
        author_id=str(record["author_id"]),
        author_name=str(record.get("author_name", "")),
        text=record["text"],
        public_url=record.get("public_url"),
        symbols=[record["symbol"]],
        engagement=PublicEngagement(likes=int(record.get("engagement", 0))),
        detection_path="fixture",
    ) for record in records]
    result = ingest_sanitized(db, SanitizedIngestRequest(
        source=source,
        collected_at=datetime.now(timezone.utc),
        posts=posts,
    ))
    return result["inserted"]
