from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal, get_db, init_db
from .fixtures import ingest_posts, load_universe
from .health import update_heartbeat
from .ingestion import ingest_sanitized
from .intelligence import compute_snapshot
from .lana_adapter import LanaUniverseAdapter
from .models import CollectorHeartbeat, CrowdSnapshot, IngestionRun, PostMention, SearchCoverage, SocialPost, SquareAuthor, SquareFeedCoverage, TrackedToken, XSliceCoverage, XVolumeBaseline
from .schemas import RawSquareFeedRequest, SanitizedIngestRequest, SearchCoverageReport
from .square_normalizer import normalize_feed_response


def is_active(token: TrackedToken) -> bool:
    """SQLite stores active as 0/1; treat missing/NULL as active for old rows."""
    value = getattr(token, "active", 1)
    return value is None or bool(value)


def active_tokens(db: Session):
    return [
        token for token in db.scalars(select(TrackedToken).order_by(TrackedToken.priority, TrackedToken.symbol))
        if is_active(token)
    ]


def serialize(token: TrackedToken, snapshot: CrowdSnapshot | None) -> dict:
    data = {
        "symbol": token.symbol,
        "canonical_pair": token.canonical_pair,
        "lana_phase": token.lana_phase,
        "priority": token.priority,
        "source": token.source,
        "active": is_active(token),

        "universe_updated_at": token.updated_at,
    }
    if not snapshot:
        return data | {"crowd_state": "NO_DATA", "data_confidence": 0, "observed_at": None}
    return data | {
        "crowd_state": snapshot.crowd_state,
        "state_confidence": snapshot.state_confidence,
        "attention_score": snapshot.attention_score,
        "breadth_score": snapshot.breadth_score,
        "authenticity_score": snapshot.authenticity_score,
        "coordination_score": snapshot.coordination_score,
        "data_confidence": snapshot.data_confidence,
        "metrics": snapshot.metrics_json,
        "contributions": snapshot.contributions_json,
        "score_version": snapshot.score_version,
        "observed_at": snapshot.observed_at,
    }


def latest_snapshot(db: Session, symbol: str) -> CrowdSnapshot | None:
    return db.scalar(select(CrowdSnapshot).where(CrowdSnapshot.symbol == symbol).order_by(CrowdSnapshot.observed_at.desc()))


X_SOURCE = "x-grok-cli"
X_HISTORY_DAYS = 7


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or timezone.utc)


def x_coverage(db: Session, symbol: str, since: datetime, now: datetime) -> dict:
    """How much of the history window has actually been scanned.

    Counts are only meaningful next to this. An hour that was never queried is
    not an hour with zero posts, and the two must never be shown as the same
    thing.
    """
    # EMPTY counts as scanned — the hour was genuinely queried — but is tracked
    # apart from SUCCESS because the search backend intermittently returns
    # nothing for a window that does have posts.
    slices = list(db.scalars(
        select(XSliceCoverage).where(
            XSliceCoverage.symbol == symbol,
            XSliceCoverage.slice_end > since,
            XSliceCoverage.status.in_(("SUCCESS", "EMPTY")),
        )
    ))
    total_hours = max(1, int((now - since).total_seconds() // 3600))
    scanned = [False] * total_hours
    for row in slices:
        start = max(as_utc(row.slice_start), since)
        end = min(as_utc(row.slice_end), now)
        if end <= start:
            continue
        # Index the hours a slice touches directly; radar renders every tracked
        # token, so walking all 168 hours per slice row does not scale.
        first = int((start - since).total_seconds() // 3600)
        last = int((end - since - timedelta(microseconds=1)).total_seconds() // 3600)
        for hour in range(max(0, first), min(total_hours - 1, last) + 1):
            scanned[hour] = True
    scanned_hours = sum(scanned)
    last_scan = max((as_utc(row.completed_at) for row in slices), default=None)
    return {
        "scanned_hours": scanned_hours,
        "window_hours": total_hours,
        "ratio": round(scanned_hours / total_hours, 3),
        "saturated_slices": sum(1 for row in slices if row.saturated),
        "empty_slices": sum(1 for row in slices if row.status == "EMPTY"),
        "last_scan_at": last_scan,
        "scanned_by_hour": scanned,
    }


def x_baseline(db: Session, symbol: str, since: datetime) -> dict[str, int]:
    """X Radar's daily post counts, keyed by ISO date."""
    rows = db.scalars(select(XVolumeBaseline).where(
        XVolumeBaseline.symbol == symbol,
        XVolumeBaseline.day >= since - timedelta(days=1),
    ))
    return {as_utc(row.day).date().isoformat(): row.post_count for row in rows}


def radar_signal(
    symbol: str, history: list[dict], capture: dict, coverage: dict,
    window_posts: list[SocialPost], baseline: dict[str, int],
) -> dict:
    """Build the X panel from X Radar's own daily counts.

    Radar is authoritative on volume and silent on everything else: this
    subscription reports `allow_unique_users: false` and
    `allow_impressions: false`, and the series is daily-only. Per-post fields
    are therefore returned as None rather than 0 — the desk must be able to
    tell "X does not expose this" from "this is zero".
    """
    counts = [day["posts"] for day in history]
    total = sum(counts)
    # The current day is still filling, so it is compared against the median of
    # the completed days rather than treated as a finished bar.
    completed = sorted(counts[:-1])
    middle = len(completed) // 2
    median = (
        float(completed[middle]) if len(completed) % 2
        else (sum(completed[middle - 1:middle + 1]) / 2 if completed else 0.0)
    )
    today = float(counts[-1]) if counts else 0.0
    acceleration = round(today / median, 2) if median > 0 else None
    yesterday = counts[-2] if len(counts) > 1 else 0

    if acceleration is None:
        state = "NO_DATA" if total == 0 else "STEADY"
    elif acceleration >= 2:
        state = "SURGING"
    elif acceleration >= 1.3:
        state = "ELEVATED"
    elif acceleration <= 0.5:
        state = "QUIET"
    else:
        state = "STEADY"

    return {
        "source": "x-radar",
        "state": state,
        "posts": total,
        "posts_today": int(today),
        "posts_yesterday": int(yesterday),
        "median_daily": round(median, 1),
        "acceleration": acceleration,
        "granularity": "day",
        "history_days": X_HISTORY_DAYS,
        "history": history,
        "capture": capture,
        "coverage": {key: value for key, value in coverage.items() if key != "scanned_by_hour"},
        "observed_at": max(
            (as_utc(post.observed_at) for post in window_posts),
            default=None,
        ),
        # X Radar does not expose these; None keeps them from reading as zero.
        "unique_authors": None,
        "engagement": None,
        "likes": None,
        "replies": None,
        "reposts": None,
        "views": None,
        "author_concentration": None,
        "posts_1h": None,
        "stale": False,
        "velocity": None,
        "metrics_note": "X Radar reports daily post counts only — no authors, engagement, or post bodies.",
        # Harvested posts remain as sample evidence, clearly a subset.
        "evidence_is_sample": True,
        "evidence": [{
            "id": post.source_post_id,
            "author": post.author_name or post.author_id,
            "text": post.text[:180],
            "url": post.public_url,
            "observed_at": post.observed_at,
            "likes": post.likes,
            "replies": post.comments,
            "reposts": post.shares,
            "views": post.views,
        } for post in window_posts[:5]],
    }


def x_signal(db: Session, symbol: str) -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=X_HISTORY_DAYS)
    base = select(SocialPost).join(PostMention, PostMention.post_id == SocialPost.id).where(
        PostMention.symbol == symbol,
        SocialPost.source == X_SOURCE,
        SocialPost.observed_at >= since,
    )
    # No LIMIT: a busy token legitimately produces hundreds of posts a week and
    # a truncated fetch silently understates every total on the radar.
    window_posts = list(db.scalars(base.order_by(SocialPost.observed_at.desc())))
    coverage = x_coverage(db, symbol, since, now)
    baseline = x_baseline(db, symbol, since)
    live_posts = [post for post in window_posts if as_utc(post.observed_at) >= now - timedelta(hours=1)]
    stale = not live_posts and bool(window_posts)

    history = []
    for days_ago in range(X_HISTORY_DAYS - 1, -1, -1):
        day = (now - timedelta(days=days_ago)).date()
        posts = [post for post in window_posts if as_utc(post.observed_at).date() == day]
        day_hours = [
            index for index in range(coverage["window_hours"])
            if (since + timedelta(hours=index)).date() == day
        ]
        scanned = sum(coverage["scanned_by_hour"][index] for index in day_hours)
        expected = baseline.get(day.isoformat())
        history.append({
            # `posts` is what the UI charts, so it carries the authoritative
            # number: X Radar's own count when we have it, harvested otherwise.
            "date": day.isoformat(),
            "posts": expected if expected is not None else len(posts),
            "posts_source": "x-radar" if expected is not None else "x-grok-cli",
            "harvested_posts": len(posts),
            "unique_authors": len({post.author_id for post in posts}),
            "views": sum(post.views for post in posts),
            "scanned_hours": scanned,
            "expected_hours": len(day_hours),
            "expected_posts": expected,
            "capture_ratio": round(len(posts) / expected, 3) if expected else None,
        })

    # Only days X actually reported can be compared; summing over days with no
    # baseline would understate the shortfall rather than admit it is unknown.
    comparable = [day for day in history if day["expected_posts"]]
    expected_total = sum(day["expected_posts"] for day in comparable)
    captured_total = sum(day["harvested_posts"] for day in comparable)
    capture = {
        "source": "x-radar",
        "expected_posts": expected_total or None,
        "captured_posts": captured_total if comparable else None,
        "capture_ratio": round(captured_total / expected_total, 3) if expected_total else None,
        "days_compared": len(comparable),
        "days_in_window": len(history),
    }

    if baseline:
        return radar_signal(symbol, history, capture, coverage, window_posts, baseline)

    velocity_defaults = {
        "posts_per_hour": 0, "authors_per_hour": 0, "baseline_per_hour": 0,
        "acceleration": None, "percentile": None, "history_days": 0,
    }
    summary = {
        "coverage": {key: value for key, value in coverage.items() if key != "scanned_by_hour"},
        "capture": capture,
        "history": history,
        "history_days": X_HISTORY_DAYS,
    }
    if not window_posts:
        return summary | {
            "state": "NO_DATA" if coverage["scanned_hours"] else "NOT_SCANNED",
            "posts": 0, "posts_1h": 0, "unique_authors": 0, "engagement": 0,
            "likes": 0, "replies": 0, "reposts": 0, "views": 0,
            "author_concentration": 0, "observed_at": None, "evidence": [], "stale": False,
            "velocity": velocity_defaults,
        }

    # Headline metrics always describe the same window (7d), never a window that
    # silently flips between one hour and one week depending on recency.
    author_counts: dict[str, int] = {}
    for post in window_posts:
        author_counts[post.author_id] = author_counts.get(post.author_id, 0) + 1
    likes = sum(post.likes for post in window_posts)
    replies = sum(post.comments for post in window_posts)
    reposts = sum(post.shares for post in window_posts)
    views = sum(post.views for post in window_posts)
    top_author_share = max(author_counts.values()) / len(window_posts)
    state = "BROADENING" if len(author_counts) >= 12 else "EMERGING" if len(author_counts) >= 4 else "SEEDING"

    # Baseline is drawn only from hours that were actually scanned, so unscanned
    # hours cannot masquerade as quiet ones and deflate the baseline.
    total_hours = coverage["window_hours"]
    hourly_counts = [0] * total_hours
    for post in window_posts:
        age_hours = int((now - as_utc(post.observed_at)).total_seconds() // 3600)
        if 0 <= age_hours < total_hours:
            hourly_counts[age_hours] += 1
    baseline_sample = sorted(
        hourly_counts[index]
        for index in range(total_hours)
        if coverage["scanned_by_hour"][total_hours - 1 - index]
    ) or sorted(hourly_counts)
    middle = len(baseline_sample) // 2
    baseline = float(baseline_sample[middle]) if len(baseline_sample) % 2 else sum(baseline_sample[middle - 1:middle + 1]) / 2
    current_rate = float(len(live_posts))
    acceleration = round(current_rate / baseline, 2) if baseline > 0 else None
    percentile = round(100 * sum(count <= current_rate for count in baseline_sample) / len(baseline_sample)) if baseline_sample else None

    return summary | {
        "state": "STALE" if stale else state,
        "posts": len(window_posts),
        "posts_1h": len(live_posts),
        "unique_authors": len(author_counts),
        "engagement": likes + replies + reposts,
        "likes": likes,
        "replies": replies,
        "reposts": reposts,
        "views": views,
        "author_concentration": round(top_author_share, 4),
        "observed_at": window_posts[0].observed_at,
        "stale": stale,
        "velocity": {
            "posts_per_hour": round(current_rate, 2),
            "authors_per_hour": len({post.author_id for post in live_posts}),
            "baseline_per_hour": round(baseline, 2),
            "acceleration": acceleration,
            "percentile": percentile,
            "history_days": round(coverage["scanned_hours"] / 24, 1),
        },
        "evidence": [{
            "id": post.source_post_id,
            "author": post.author_name or post.author_id,
            "text": post.text[:180],
            "url": post.public_url,
            "observed_at": post.observed_at,
            "likes": post.likes,
            "replies": post.comments,
            "reposts": post.shares,
            "views": post.views,
        } for post in window_posts[:5]],
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as db:
        if db.query(TrackedToken).count() == 0:
            universe = settings.fixture_dir / "lana-universe.json"
            if universe.exists():
                load_universe(db, universe)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    freshest = db.scalar(select(CrowdSnapshot).order_by(CrowdSnapshot.observed_at.desc()))
    tokens = list(db.scalars(select(TrackedToken)))
    active = sum(1 for token in tokens if is_active(token))
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc),
        "tracked_tokens": active,
        "archived_tokens": len(tokens) - active,
        "freshest_snapshot": freshest.observed_at if freshest else None,
    }


@app.get("/api/universe")
def universe(db: Session = Depends(get_db)):
    return [serialize(token, latest_snapshot(db, token.symbol)) for token in active_tokens(db)]


@app.post("/api/universe/sync")
def sync_universe(db: Session = Depends(get_db)):
    """Mirror Lana's crime kanban (IGNITION/SQUEEZE/EXHAUSTION/DUMP).

    Tokens that leave the board are *archived* (active=0): X Radar baselines,
    harvested posts, and coverage stay on disk for history. The live radar only
    lists active kanban members.
    """
    adapter = LanaUniverseAdapter(
        fixture_path=settings.fixture_dir / "lana-universe.json",
        ssh_host=settings.lana_ssh_host,
        container=settings.lana_postgres_container,
        database_url=settings.lana_database_url,
    )
    try:
        records = adapter.fetch()
    except Exception as error:
        raise HTTPException(502, f"Lana universe sync failed: {error}") from error

    keep: set[str] = set()
    activated: list[str] = []
    for record in records:
        symbol = record["symbol"].upper().replace("USDT", "")
        keep.add(symbol)
        token = db.get(TrackedToken, symbol)
        was_inactive = token is not None and not is_active(token)
        if token is None:
            token = TrackedToken(symbol=symbol, canonical_pair=f"{symbol}USDT")
            activated.append(symbol)
        elif was_inactive:
            activated.append(symbol)
        token.lana_phase = record.get("lana_phase", "NORMAL")
        token.priority = int(record.get("priority", 3))
        token.source = record.get("source", "lana")
        token.active = 1
        metadata = dict(record.get("metadata") or {})
        if record.get("effective_at"):
            metadata["lana_effective_at"] = record["effective_at"]
        metadata.pop("archived_at", None)
        token.metadata_json = metadata
        token.updated_at = datetime.now(timezone.utc)
        db.add(token)

    # Soft-archive: keep baselines / posts, hide from live radar.
    archived: list[str] = []
    for token in list(db.scalars(select(TrackedToken))):
        if token.symbol in keep:
            continue
        if not is_active(token):
            continue
        token.active = 0
        metadata = dict(token.metadata_json or {})
        metadata["archived_at"] = datetime.now(timezone.utc).isoformat()
        token.metadata_json = metadata
        token.updated_at = datetime.now(timezone.utc)
        db.add(token)
        archived.append(token.symbol)

    db.commit()
    if settings.lana_database_url:
        mode = "lana_db"
    elif settings.lana_ssh_host:
        mode = "ssh"
    else:
        mode = "fixture"
    return {
        "synced": len(records),
        "activated": sorted(activated),
        "archived": sorted(archived),
        "archived_count": len(archived),
        # Back-compat for older operators/scripts that still read "removed".
        "removed": sorted(archived),
        "removed_count": len(archived),
        "mode": mode,
    }


@app.get("/api/radar")
def radar(db: Session = Depends(get_db)):
    rows = [
        serialize(token, latest_snapshot(db, token.symbol)) | {"x_signal": x_signal(db, token.symbol)}
        for token in active_tokens(db)
    ]
    return sorted(rows, key=lambda row: (row.get("priority", 3), -(row.get("attention_score") or 0)))


@app.get("/api/tokens/{symbol}")
def token_detail(symbol: str, db: Session = Depends(get_db)):
    token = db.get(TrackedToken, symbol.upper().replace("USDT", ""))
    if not token:
        raise HTTPException(404, "Token is not tracked")
    snapshots = list(db.scalars(select(CrowdSnapshot).where(CrowdSnapshot.symbol == token.symbol).order_by(CrowdSnapshot.observed_at.desc()).limit(50)))
    return {"current": serialize(token, snapshots[0] if snapshots else None), "history": [serialize(token, item) for item in snapshots]}


@app.post("/api/ingest")
def ingest(payload: SanitizedIngestRequest, db: Session = Depends(get_db)):
    result = ingest_sanitized(db, payload)
    for symbol in result["affected_symbols"]:
        compute_snapshot(db, symbol)
    # Ingestion must not overwrite the collector's own heartbeat: the collector
    # is the source of truth for whether it is live, and a single ingest call
    # does not change its connection state or running batch counts. Heartbeats
    # are written only by /api/collector/heartbeat.
    return result


def tracked_symbol_set(db: Session) -> set[str]:
    """Symbols the desk currently tracks, for server-side normalization."""
    return set(db.scalars(select(TrackedToken.symbol)))


@app.post("/api/square/feed")
def ingest_square_feed(payload: RawSquareFeedRequest, db: Session = Depends(get_db)):
    """Ingest a raw Binance Square feed/search response, normalized server-side.

    The collector forwards the JSON it observed verbatim; this endpoint is the
    only place that decides which symbols are tracked, so normalization cannot
    drift between collector and server. The raw payload is never persisted —
    only the sanitized posts derived from it are. `detection_path` is passed
    through to the normalizer so each derived post carries its provenance.
    """
    tracked = tracked_symbol_set(db)
    posts = normalize_feed_response(payload.feed, tracked, payload.detection_path)
    result = ingest_sanitized(db, SanitizedIngestRequest(
        source=payload.source,
        collected_at=payload.collected_at,
        posts=posts,
    ))
    for symbol in result["affected_symbols"]:
        compute_snapshot(db, symbol)
    return result | {"normalized": len(posts)}


@app.post("/api/search-coverage")
def record_search_coverage(payload: SearchCoverageReport, db: Session = Depends(get_db)):
    symbol = payload.symbol
    if not db.get(TrackedToken, symbol):
        raise HTTPException(404, "Token is not tracked")
    coverage = SearchCoverage(
        source="binance-square-browser",
        symbol=symbol,
        query=payload.query or symbol,
        status=payload.status,
        pages_scanned=payload.pages_scanned,
        responses_observed=payload.responses_observed,
        matched_posts=payload.matched_posts,
        started_at=payload.started_at,
        oldest_post_at=payload.oldest_post_at,
        newest_post_at=payload.newest_post_at,
        cutoff_at=payload.cutoff_at,
        message=payload.message,
    )
    db.add(coverage)
    db.commit()
    return {"status": "ok", "id": coverage.id}


@app.get("/api/search-coverage/{symbol}")
def search_coverage(symbol: str, db: Session = Depends(get_db)):
    rows = list(db.scalars(select(SearchCoverage).where(
        SearchCoverage.symbol == symbol.upper().replace("USDT", "")
    ).order_by(SearchCoverage.completed_at.desc()).limit(50)))
    return [{
        "status": row.status, "query": row.query, "pages_scanned": row.pages_scanned,
        "responses_observed": row.responses_observed, "matched_posts": row.matched_posts,
        "started_at": row.started_at, "completed_at": row.completed_at,
        "oldest_post_at": row.oldest_post_at, "newest_post_at": row.newest_post_at,
        "cutoff_at": row.cutoff_at, "message": row.message,
    } for row in rows]


class SquareFeedCoverageReport(BaseModel):
    """One observed Square /bapi feed batch, per detection path (teardown §16)."""

    detection_path: str
    batch_at: datetime
    cards_observed: int = 0
    matched_posts: int = 0
    symbols_covered: list[str] = []


@app.post("/api/square-feed-coverage")
def record_square_feed_coverage(payload: list[SquareFeedCoverageReport], db: Session = Depends(get_db)):
    """Record which Square feed batches were observed, per path.

    Idempotent upsert keyed on (detection_path, batch_at): re-reporting the
    same batch updates counts rather than duplicating rows. This keeps feed
    coverage gaps visible rather than reading as zero.
    """
    written = 0
    for item in payload:
        path = item.detection_path[:32]
        batch_at = as_utc(item.batch_at)
        row = db.scalar(select(SquareFeedCoverage).where(
            SquareFeedCoverage.detection_path == path,
            SquareFeedCoverage.batch_at == batch_at,
        ))
        if row is None:
            row = SquareFeedCoverage(detection_path=path, batch_at=batch_at)
            db.add(row)
        row.cards_observed = max(0, item.cards_observed)
        row.matched_posts = max(0, item.matched_posts)
        row.symbols_covered = ",".join(sorted(set(s.upper() for s in item.symbols_covered)))[:1024]
        row.detected_at = datetime.now(timezone.utc)
        written += 1
    db.commit()
    return {"recorded": written}


@app.get("/api/square-feed-coverage")
def get_square_feed_coverage(path: Optional[str] = None, limit: int = 50, db: Session = Depends(get_db)):
    """Recent Square feed batches observed, optionally filtered by path."""
    stmt = select(SquareFeedCoverage).order_by(SquareFeedCoverage.batch_at.desc()).limit(max(1, min(limit, 200)))
    if path:
        stmt = stmt.where(SquareFeedCoverage.detection_path == path[:32])
    rows = list(db.scalars(stmt))
    return [{
        "detection_path": row.detection_path,
        "batch_at": row.batch_at,
        "cards_observed": row.cards_observed,
        "matched_posts": row.matched_posts,
        "symbols_covered": [s for s in row.symbols_covered.split(",") if s],
        "detected_at": row.detected_at,
    } for row in rows]


@app.get("/api/square-authors")
def square_authors(limit: int = 100, db: Session = Depends(get_db)):
    """Square author registry populated passively from observed posts.

    This is not a tracked-author control plane — it is a denormalized index
    of authors we have actually seen through ingest (teardown §22.3). No
    aggressive polling is performed; the registry only grows when a post is
    ingested.
    """
    rows = list(db.scalars(select(SquareAuthor).order_by(SquareAuthor.last_seen_at.desc()).limit(max(1, min(limit, 500)))))
    return [{
        "author_id": row.author_id,
        "author_name": row.author_name,
        "verification_type": row.verification_type,
        "post_count": row.post_count,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "last_post_id": row.last_post_id,
    } for row in rows]


class XSliceReport(BaseModel):
    symbol: str
    slice_start: datetime
    slice_end: datetime
    query: str = ""
    status: str = "SUCCESS"
    posts_found: int = 0
    saturated: bool = False
    split_depth: int = 0
    message: Optional[str] = None


@app.post("/api/x-coverage")
def record_x_coverage(payload: list[XSliceReport], db: Session = Depends(get_db)):
    """Record which (symbol, time-slice) windows the X harvester actually scanned."""
    written = 0
    for item in payload:
        symbol = item.symbol.upper().replace("USDT", "")
        if not db.get(TrackedToken, symbol):
            continue
        start, end = as_utc(item.slice_start), as_utc(item.slice_end)
        if end <= start:
            continue
        row = db.scalar(select(XSliceCoverage).where(
            XSliceCoverage.symbol == symbol,
            XSliceCoverage.slice_start == start,
            XSliceCoverage.slice_end == end,
        ))
        if row is None:
            row = XSliceCoverage(symbol=symbol, slice_start=start, slice_end=end)
            db.add(row)
        row.query = item.query[:256]
        row.status = item.status[:32]
        row.posts_found = max(0, item.posts_found)
        row.saturated = 1 if item.saturated else 0
        row.split_depth = max(0, item.split_depth)
        row.message = (item.message or "")[:256] or None
        row.completed_at = datetime.now(timezone.utc)
        written += 1
    db.commit()
    return {"recorded": written}


@app.get("/api/x-coverage/{symbol}")
def get_x_coverage(symbol: str, days: int = X_HISTORY_DAYS, db: Session = Depends(get_db)):
    """Slices already scanned, so a harvest run can resume instead of rescanning."""
    resolved = symbol.upper().replace("USDT", "")
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=max(1, min(days, 30)))
    rows = list(db.scalars(select(XSliceCoverage).where(
        XSliceCoverage.symbol == resolved,
        XSliceCoverage.slice_end > since,
    ).order_by(XSliceCoverage.slice_start)))
    return {
        "symbol": resolved,
        "since": since,
        "slices": [{
            "slice_start": row.slice_start,
            "slice_end": row.slice_end,
            "status": row.status,
            "posts_found": row.posts_found,
            "saturated": bool(row.saturated),
            "split_depth": row.split_depth,
            "completed_at": row.completed_at,
        } for row in rows],
    }


class XBaselineDay(BaseModel):
    day: datetime
    post_count: int


class XBaselineReport(BaseModel):
    symbol: str
    query: str = ""
    source: str = "x-radar"
    days: list[XBaselineDay]


@app.post("/api/x-baseline")
def record_x_baseline(payload: list[XBaselineReport], db: Session = Depends(get_db)):
    """Store X Radar's authoritative daily post counts for tracked symbols."""
    written, skipped = 0, []
    for report in payload:
        symbol = report.symbol.upper().replace("USDT", "")
        if not db.get(TrackedToken, symbol):
            skipped.append(symbol)
            continue
        for entry in report.days:
            day = as_utc(entry.day).replace(hour=0, minute=0, second=0, microsecond=0)
            row = db.scalar(select(XVolumeBaseline).where(
                XVolumeBaseline.symbol == symbol,
                XVolumeBaseline.day == day,
            ))
            if row is None:
                row = XVolumeBaseline(symbol=symbol, day=day)
                db.add(row)
            row.post_count = max(0, entry.post_count)
            row.query = report.query[:256]
            row.source = report.source[:64]
            row.fetched_at = datetime.now(timezone.utc)
            written += 1
    db.commit()
    return {"recorded": written, "skipped_untracked": sorted(set(skipped))}


@app.get("/api/x-baseline/{symbol}")
def get_x_baseline(symbol: str, days: int = X_HISTORY_DAYS, db: Session = Depends(get_db)):
    resolved = symbol.upper().replace("USDT", "")
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 30)))
    rows = list(db.scalars(select(XVolumeBaseline).where(
        XVolumeBaseline.symbol == resolved,
        XVolumeBaseline.day >= since,
    ).order_by(XVolumeBaseline.day)))
    return {
        "symbol": resolved,
        "total": sum(row.post_count for row in rows),
        "days": [{
            "date": as_utc(row.day).date().isoformat(),
            "post_count": row.post_count,
            "source": row.source,
            "fetched_at": row.fetched_at,
        } for row in rows],
    }


@app.post("/api/universe/track")
def track_symbol(payload: dict, db: Session = Depends(get_db)):
    """Track a symbol the Lana sync has not supplied yet.

    Ingestion drops any post whose symbol is not tracked, so a token has to
    exist here before the radar can hold a single observation for it.
    """
    symbol = str(payload.get("symbol", "")).upper().replace("USDT", "").strip()
    if not symbol or not symbol.isalnum():
        raise HTTPException(422, "symbol must be alphanumeric")
    token = db.get(TrackedToken, symbol)
    created = token is None
    if token is None:
        token = TrackedToken(symbol=symbol, canonical_pair=f"{symbol}USDT")
    token.lana_phase = str(payload.get("lana_phase", token.lana_phase if not created else "NORMAL"))
    token.priority = int(payload.get("priority", token.priority if not created else 3))
    token.source = str(payload.get("source", "manual"))
    token.active = 1
    token.updated_at = datetime.now(timezone.utc)
    db.add(token)
    db.commit()
    return {"symbol": symbol, "created": created, "priority": token.priority}


@app.post("/api/collector/heartbeat")
def collector_heartbeat(payload: dict, db: Session = Depends(get_db)):
    update_heartbeat(
        db,
        str(payload.get("source", "binance-square-browser")),
        str(payload.get("status", "CONNECTED")),
        int(payload.get("batches", 0)),
        int(payload.get("matched", 0)),
        int(payload.get("unmatched", 0)),
        str(payload.get("message", ""))[:256],
    )
    return {"status": "ok"}


@app.post("/api/recompute/{symbol}")
def recompute(symbol: str, db: Session = Depends(get_db)):
    try:
        snapshot = compute_snapshot(db, symbol.upper().replace("USDT", ""))
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    token = db.get(TrackedToken, snapshot.symbol)
    return serialize(token, snapshot)


@app.post("/api/fixtures/load")
def load_fixture_data(db: Session = Depends(get_db)):
    universe_path = settings.fixture_dir / "lana-universe.json"
    posts_path = settings.fixture_dir / "square-posts.json"
    loaded_tokens = load_universe(db, universe_path)
    posts = json.loads(posts_path.read_text()) if posts_path.exists() else []
    loaded_posts = ingest_posts(db, posts)
    snapshots = [compute_snapshot(db, token.symbol) for token in db.scalars(select(TrackedToken))]
    return {"tokens": loaded_tokens, "posts": loaded_posts, "snapshots": len(snapshots)}


@app.get("/api/source-health")
def source_health(db: Session = Depends(get_db)):
    run = db.scalar(select(IngestionRun).order_by(IngestionRun.completed_at.desc()))
    if not run:
        return {"status": "NO_DATA", "last_success": None, "source": None}
    age_seconds = max(0, (datetime.now(timezone.utc) - run.completed_at.replace(tzinfo=run.completed_at.tzinfo or timezone.utc)).total_seconds())
    recent_runs = list(db.scalars(select(IngestionRun).order_by(IngestionRun.completed_at.desc()).limit(10)))
    observed_symbols = list(db.execute(
        select(PostMention.symbol, SocialPost.observed_at, SocialPost.author_name, SocialPost.text)
        .join(SocialPost, SocialPost.id == PostMention.post_id)
        .order_by(SocialPost.ingested_at.desc()).limit(20)
    ))
    heartbeat = db.scalar(select(CollectorHeartbeat).order_by(CollectorHeartbeat.last_seen_at.desc()))
    return {
        "status": "LIVE" if age_seconds <= 300 else "STALE",
        "collector": {
            "status": heartbeat.status if heartbeat else "NO_HEARTBEAT",
            "last_seen_at": heartbeat.last_seen_at if heartbeat else None,
            "batches_observed": heartbeat.batches_observed if heartbeat else 0,
            "matched_posts": heartbeat.matched_posts if heartbeat else 0,
            "unmatched_posts": heartbeat.unmatched_posts if heartbeat else 0,
            "message": heartbeat.message if heartbeat else None,
        },
        "source": run.source,
        "last_success": run.completed_at,
        "age_seconds": round(age_seconds),
        "received_count": run.received_count,
        "inserted_count": run.inserted_count,
        "updated_count": run.updated_count,
        "rejected_count": run.rejected_count,
        "runs": [{
            "completed_at": item.completed_at,
            "received": item.received_count,
            "inserted": item.inserted_count,
            "updated": item.updated_count,
            "rejected": item.rejected_count,
        } for item in recent_runs],
        "recent_evidence": [{
            "symbol": row.symbol,
            "observed_at": row.observed_at,
            "author": row.author_name,
            "text": row.text[:180],
        } for row in observed_symbols],
    }
