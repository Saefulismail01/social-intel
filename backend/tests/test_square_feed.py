"""Tests for the raw Square feed endpoint, validated SearchCoverage, honest
IngestionRun statuses, and the heartbeat-not-clobbered contract.

These cover the Binance Square ingestion improvements as one coherent
increment. Every endpoint test runs against a fresh isolated SQLite file in a
tmp_path so the shared dev database is never touched.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import db as db_module
from app.ingestion import derive_run_status, ingest_sanitized
from app.main import app
from app.models import (
    Base,
    CollectorHeartbeat,
    IngestionRun,
    PostMention,
    SearchCoverage,
    SocialPost,
    TrackedToken,
)
from app.schemas import (
    RawSquareFeedRequest,
    SanitizedIngestRequest,
    SearchCoverageReport,
)
from app.square_normalizer import normalize_feed_response


def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def feed_payload(post_id="post-1", symbol_pair="ZFEEDUSDT", text="$ZFEED moving"):
    return {
        "code": "000000",
        "data": {"vos": [{
            "id": post_id, "squareAuthorId": "author-1", "authorName": "Public Author",
            "title": text, "subTitle": "crowd discussion", "date": now_ms(),
            "webLink": "https://www.binance.com/en/square/post/1",
            "cardType": "POST", "contentType": 1,
            "tradingPairsV2": [{"symbol": symbol_pair}],
            "likeCount": 5, "commentCount": 2, "shareCount": 1, "viewCount": 100,
        }]},
    }


def isolate(tmp_path, monkeypatch, *symbols):
    """Point the app at a fresh SQLite file and seed the given tracked tokens.

    `get_db` looks up `SessionLocal` in `app.db`, so both `app.db.SessionLocal`
    and `app.main.SessionLocal` are patched, and `init_db` is a no-op so the
    lifespan never touches the real dev database.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    if symbols:
        with Session(engine) as db:
            for symbol in symbols:
                db.add(TrackedToken(symbol=symbol, canonical_pair=f"{symbol}USDT"))
            db.commit()
    monkeypatch.setattr(db_module, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(db_module, "init_db", lambda: None)
    monkeypatch.setattr("app.main.SessionLocal", lambda: Session(engine))
    monkeypatch.setattr("app.main.init_db", lambda: None)
    return engine


# ---------------------------------------------------------------------------
# Raw square feed endpoint
# ---------------------------------------------------------------------------

def test_square_feed_normalizes_server_side_and_persists(tmp_path, monkeypatch):
    """The raw feed is normalized against tracked symbols on the server, and
    only sanitized posts are stored — the raw payload never lands in the DB."""
    engine = isolate(tmp_path, monkeypatch, "ZFEED")
    client = TestClient(app)

    response = client.post("/api/square/feed", json={
        "source": "binance-square-browser",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "feed": feed_payload(),
    })
    assert response.status_code == 200
    body = response.json()
    assert body["inserted"] == 1
    assert body["normalized"] == 1
    assert body["affected_symbols"] == ["ZFEED"]

    with Session(engine) as db:
        post = db.scalar(select(SocialPost))
        assert post is not None
        assert post.source == "binance-square-browser"
        # A sanitized row is stored, never the raw payload verbatim. The
        # normalizer joins title + subTitle into the stored text.
        assert post.text.startswith("$ZFEED moving")
        assert post.author_name == "Public Author"


def test_square_feed_drops_untracked_symbols(tmp_path, monkeypatch):
    """A post whose only symbol is not tracked yields zero normalized posts."""
    isolate(tmp_path, monkeypatch, "ZFEED")
    client = TestClient(app)

    response = client.post("/api/square/feed", json={
        "source": "binance-square-browser",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "feed": feed_payload(symbol_pair="UNTRACKEDUSDT", text="quiet day"),
    })
    assert response.status_code == 200
    body = response.json()
    assert body["normalized"] == 0
    assert body["inserted"] == 0
    assert body["affected_symbols"] == []
    assert body["status"] == "EMPTY"


def test_square_feed_rejects_sensitive_keys():
    """The raw feed is request metadata; a cookie smuggled in is rejected."""
    payload = {
        "source": "binance-square-browser",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "feed": {"cookie": "steal-me", "data": {"vos": []}},
    }
    with pytest.raises(ValidationError):
        RawSquareFeedRequest.model_validate(payload)


def test_square_feed_rejects_request_headers_inside_feed():
    """Authorization headers nested inside the feed object are rejected too."""
    payload = {
        "source": "binance-square-browser",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "feed": {"data": {"vos": [{"authorization": "Bearer x"}]}},
    }
    with pytest.raises(ValidationError):
        RawSquareFeedRequest.model_validate(payload)


def test_square_feed_is_idempotent(tmp_path, monkeypatch):
    engine = isolate(tmp_path, monkeypatch, "ZFEED")
    client = TestClient(app)

    body = {
        "source": "binance-square-browser",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "feed": feed_payload(),
    }
    first = client.post("/api/square/feed", json=body).json()
    second = client.post("/api/square/feed", json=body).json()
    assert first["inserted"] == 1
    assert second["updated"] == 1
    assert second["inserted"] == 0
    with Session(engine) as db:
        assert db.query(SocialPost).count() == 1


# ---------------------------------------------------------------------------
# Honest IngestionRun statuses
# ---------------------------------------------------------------------------

def test_derive_run_status_covers_every_outcome():
    assert derive_run_status(0, 0, 0) == "EMPTY"
    assert derive_run_status(2, 2, 0) == "SUCCESS"
    assert derive_run_status(2, 0, 2) == "REJECTED"
    assert derive_run_status(3, 2, 1) == "PARTIAL"


def test_ingest_records_honest_status_and_error_category():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(TrackedToken(symbol="ZFEED", canonical_pair="ZFEEDUSDT"))
        db.commit()
        # One accepted, two rejected for untracked symbols. (Timestamp drift only
        # applies to x-grok-cli, so it is exercised separately in test_x_signal.)
        honest = {
            "source_post_id": "honest-1", "published_at": datetime.now(timezone.utc).isoformat(),
            "author_id": "a", "text": "$ZFEED", "symbols": ["ZFEED"],
            "engagement": {"likes": 0, "comments": 0, "shares": 0, "views": 0},
        }
        untracked_a = dict(honest, source_post_id="untracked-1", symbols=["NOPE"])
        untracked_b = dict(honest, source_post_id="untracked-2", symbols=["ALSO"])
        request = SanitizedIngestRequest(
            source="binance-square-browser",
            collected_at=datetime.now(timezone.utc),
            posts=[honest, untracked_a, untracked_b],  # type: ignore[arg-type]
        )
        result = ingest_sanitized(db, request)
        assert result["status"] == "PARTIAL"
        assert result["rejected"] == 2
        assert result["error_category"] == "untracked_symbols"

        run = db.scalar(select(IngestionRun).where(IngestionRun.source == "binance-square-browser"))
        assert run.status == "PARTIAL"
        assert run.rejected_count == 2
        assert run.error_category == "untracked_symbols"


def test_ingest_all_rejected_is_marked_rejected():
    """A batch where every post was an untracked symbol is not a success."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(TrackedToken(symbol="ZFEED", canonical_pair="ZFEEDUSDT"))
        db.commit()
        result = ingest_sanitized(db, SanitizedIngestRequest(
            source="binance-square-browser",
            collected_at=datetime.now(timezone.utc),
            posts=[{
                "source_post_id": "no-1", "published_at": datetime.now(timezone.utc).isoformat(),
                "author_id": "a", "text": "$NOPE", "symbols": ["NOPE"],
                "engagement": {"likes": 0, "comments": 0, "shares": 0, "views": 0},
            }],  # type: ignore[arg-type]
        ))
        assert result["status"] == "REJECTED"
        assert result["error_category"] == "untracked_symbols"


def test_ingest_empty_batch_is_marked_empty_not_success():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(TrackedToken(symbol="ZFEED", canonical_pair="ZFEEDUSDT"))
        db.commit()
        result = ingest_sanitized(db, SanitizedIngestRequest(
            source="binance-square-browser",
            collected_at=datetime.now(timezone.utc),
            posts=[],
        ))
        assert result["status"] == "EMPTY"
        run = db.scalar(select(IngestionRun))
        assert run.status == "EMPTY"
        assert run.error_category is None


# ---------------------------------------------------------------------------
# Heartbeat must not be clobbered by ingestion
# ---------------------------------------------------------------------------

def test_ingest_does_not_clobber_collector_heartbeat(tmp_path, monkeypatch):
    """The collector writes its own heartbeat; a feed ingest must not overwrite
    its status or running batch/matched counters."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(TrackedToken(symbol="ZFEED", canonical_pair="ZFEEDUSDT"))
        db.add(CollectorHeartbeat(
            source="binance-square-browser", status="FEED_OBSERVED",
            batches_observed=42, matched_posts=99, unmatched_posts=3,
        ))
        db.commit()
    monkeypatch.setattr(db_module, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(db_module, "init_db", lambda: None)
    monkeypatch.setattr("app.main.SessionLocal", lambda: Session(engine))
    monkeypatch.setattr("app.main.init_db", lambda: None)
    client = TestClient(app)

    client.post("/api/square/feed", json={
        "source": "binance-square-browser",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "feed": feed_payload(),
    })

    with Session(engine) as db:
        hb = db.scalar(select(CollectorHeartbeat).where(
            CollectorHeartbeat.source == "binance-square-browser"))
        # The collector's status and counters survive the ingest call.
        assert hb.status == "FEED_OBSERVED"
        assert hb.batches_observed == 42
        assert hb.matched_posts == 99
        assert hb.unmatched_posts == 3


# ---------------------------------------------------------------------------
# Validated SearchCoverage request
# ---------------------------------------------------------------------------

def test_search_coverage_report_rejects_unknown_status():
    with pytest.raises(ValidationError):
        SearchCoverageReport.model_validate({
            "symbol": "ZFEED", "status": "GREAT", "started_at": datetime.now(timezone.utc).isoformat(),
        })


def test_search_coverage_report_normalizes_symbol():
    report = SearchCoverageReport.model_validate({
        "symbol": "zfeedusdt", "started_at": datetime.now(timezone.utc).isoformat(),
    })
    assert report.symbol == "ZFEED"


def test_search_coverage_report_rejects_backwards_timestamps():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        SearchCoverageReport.model_validate({
            "symbol": "ZFEED", "started_at": now.isoformat(),
            "oldest_post_at": (now - timedelta(hours=1)).isoformat(),
            "newest_post_at": (now - timedelta(hours=2)).isoformat(),
        })


def test_search_coverage_endpoint_stores_timestamps(tmp_path, monkeypatch):
    engine = isolate(tmp_path, monkeypatch, "ZCOV")
    client = TestClient(app)

    started = datetime.now(timezone.utc) - timedelta(minutes=5)
    oldest = started + timedelta(minutes=1)
    newest = started + timedelta(minutes=4)
    cutoff = started - timedelta(days=7)
    response = client.post("/api/search-coverage", json={
        "symbol": "ZCOV", "status": "SUCCESS", "pages_scanned": 3,
        "responses_observed": 3, "matched_posts": 7, "started_at": started.isoformat(),
        "oldest_post_at": oldest.isoformat(), "newest_post_at": newest.isoformat(),
        "cutoff_at": cutoff.isoformat(), "message": "reached cutoff",
    })
    assert response.status_code == 200
    coverage_id = response.json()["id"]

    with Session(engine) as db:
        row = db.get(SearchCoverage, coverage_id)
        assert row.status == "SUCCESS"
        assert row.oldest_post_at.replace(tzinfo=timezone.utc) == oldest
        assert row.newest_post_at.replace(tzinfo=timezone.utc) == newest
        assert row.cutoff_at.replace(tzinfo=timezone.utc) == cutoff

    rows = client.get("/api/search-coverage/ZCOV").json()
    assert rows[0]["oldest_post_at"] is not None
    assert rows[0]["cutoff_at"] is not None


def test_search_coverage_endpoint_404s_untracked_symbol(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)  # no symbols tracked
    client = TestClient(app)
    response = client.post("/api/search-coverage", json={
        "symbol": "NOPE", "started_at": datetime.now(timezone.utc).isoformat(),
    })
    assert response.status_code == 404


def test_normalizer_and_endpoint_agree_on_tracked_symbols(tmp_path, monkeypatch):
    """The endpoint uses the same normalizer the unit tests assert against, so
    server-side tracking cannot diverge from the documented behaviour."""
    isolate(tmp_path, monkeypatch, "ZFEED", "ZALT")
    client = TestClient(app)

    feed = {
        "data": {"vos": [{
            "id": "multi-1", "squareAuthorId": "a", "authorName": "A",
            "title": "$ZFEED and $ZALT", "date": now_ms(),
            "tradingPairsV2": [{"symbol": "ZFEEDUSDT"}],
            "likeCount": 1,
        }]},
    }
    # Unit-level normalizer
    posts = normalize_feed_response(feed, {"ZFEED", "ZALT"})
    assert posts[0].symbols == ["ZFEED", "ZALT"]
    # Endpoint-level normalization
    response = client.post("/api/square/feed", json={
        "source": "binance-square-browser",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "feed": feed,
    }).json()
    assert response["normalized"] == 1
    assert sorted(response["affected_symbols"]) == ["ZALT", "ZFEED"]
