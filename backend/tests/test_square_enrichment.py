"""Tests for the enriched Square ingestion: native PGC fields, distinct
detection timestamps, merge across detection paths, author registry, and
feed coverage.

Scope is ingestion only — no crowd scoring, no external polling.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import _ensure_sqlite_columns
from app.ingestion import ingest_sanitized
from app.models import Base, PostMention, SocialPost, SquareAuthor, SquareFeedCoverage, TrackedToken
from app.schemas import PublicEngagement, SanitizedIngestRequest, SanitizedSquarePost
from app.square_normalizer import normalize_feed_response


def now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def make_card(**overrides):
    """A raw PGC card straight from /bapi, with native enrichment fields."""
    card = {
        "id": "post-1",
        "squareAuthorId": "author-1",
        "authorName": "CZ",
        "title": "$BULLA and $HOME are moving",
        "subTitle": "crowd discussion",
        "date": now_ms(),
        "webLink": "https://www.binance.com/en/square/post/1",
        "shareLink": "https://www.binance.com/en/square/post/1?share=1",
        "cardType": "POST",
        "contentType": 1,
        "tradingPairsV2": [{"symbol": "BULLAUSDT"}],
        "userInputTradingPairs": [{"symbol": "HOMEUSDT"}],
        "likeCount": 5,
        "commentCount": 2,
        "shareCount": 1,
        "viewCount": 100,
        "tendency": "bullish",
        "bullishRatio": 0.8,
        "bearishRatio": 0.2,
        "isReply": False,
        "isSticky": True,
        "media": [
            {"url": "https://public.bnbstatic.com/static/content/square/images/1.jpg"},
            {"url": "https://public.bnbstatic.com/video/pgc/ArticleContent/1.mp4"},
        ],
    }
    card.update(overrides)
    return card


def feed_payload(cards):
    return {"code": "000000", "data": {"vos": cards}}


def fresh_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            TrackedToken(symbol="BULLA", canonical_pair="BULLAUSDT"),
            TrackedToken(symbol="HOME", canonical_pair="HOMEUSDT"),
        ])
        db.commit()
    return engine


# --- Normalizer ---------------------------------------------------------


def test_normalizer_extracts_native_pgc_fields():
    posts = normalize_feed_response(feed_payload([make_card()]), {"BULLA", "HOME"})
    assert len(posts) == 1
    post = posts[0]
    assert post.coin_pairs == ["BULLAUSDT", "HOMEUSDT"]
    assert post.tendency == "bullish"
    assert post.bullish_ratio == 0.8
    assert post.bearish_ratio == 0.2
    assert post.is_sticky is True
    assert post.is_reply is False
    assert post.parent_id is None
    assert len(post.media_urls) == 2
    assert post.detection_path == "feed-recommend"
    assert post.share_url == "https://www.binance.com/en/square/post/1?share=1"
    # hashtags/mentions extracted from text
    assert "BULLA" not in post.hashtags  # $ is cashtag, not hashtag
    assert len(post.hashtags) == 0


def test_normalizer_detection_path_from_search():
    posts = normalize_feed_response(
        feed_payload([make_card()]), {"BULLA", "HOME"}, detection_path="feed-search"
    )
    assert posts[0].detection_path == "feed-search"


def test_normalizer_extracts_hashtags_and_mentions():
    card = make_card(title="Check #Bitcoin and @cz_binance $BULLA")
    posts = normalize_feed_response(feed_payload([card]), {"BULLA"})
    assert "Bitcoin" in posts[0].hashtags
    assert "cz_binance" in posts[0].mentions


def test_normalizer_tendency_bearish():
    card = make_card(tendency="bearish", bullishRatio=0.1, bearishRatio=0.9)
    posts = normalize_feed_response(feed_payload([card]), {"BULLA", "HOME"})
    assert posts[0].tendency == "bearish"


def test_normalizer_reply_chain():
    card = make_card(id="post-reply", isReply=True, parentId="post-1")
    posts = normalize_feed_response(feed_payload([card]), {"BULLA", "HOME"})
    assert posts[0].is_reply is True
    assert posts[0].parent_id == "post-1"


# --- Schema validation --------------------------------------------------


def test_published_at_required():
    """observed_at was renamed to published_at; it must be supplied."""
    with pytest.raises(ValidationError):
        SanitizedSquarePost(
            source_post_id="x", author_id="a",
        )


def test_detected_at_defaults_to_published_at():
    post = SanitizedSquarePost(
        source_post_id="x", published_at=datetime.now(timezone.utc), author_id="a",
    )
    assert post.detected_at is not None
    assert post.detected_at == post.published_at


def test_tendency_rejects_invalid():
    with pytest.raises(ValidationError):
        SanitizedSquarePost(
            source_post_id="x", published_at=datetime.now(timezone.utc),
            author_id="a", tendency="neutral",
        )


def test_ratio_out_of_range_rejected():
    with pytest.raises(ValidationError):
        SanitizedSquarePost(
            source_post_id="x", published_at=datetime.now(timezone.utc),
            author_id="a", bullish_ratio=1.5,
        )


# --- Ingest: enrichment + idempotency -----------------------------------


def test_ingest_stores_native_pgc_fields():
    engine = fresh_db()
    with Session(engine) as db:
        posts = normalize_feed_response(feed_payload([make_card()]), {"BULLA", "HOME"})
        result = ingest_sanitized(db, SanitizedIngestRequest(
            source="binance-square-browser",
            collected_at=datetime.now(timezone.utc),
            posts=posts,
        ))
        assert result["inserted"] == 1
        row = db.scalar(select(SocialPost))
        assert row.coin_pairs == "BULLAUSDT,HOMEUSDT"
        assert row.tendency == "bullish"
        assert row.bullish_ratio == 0.8
        assert row.bearish_ratio == 0.2
        assert row.is_sticky == 1
        assert row.is_reply == 0
        assert row.media_urls == (
            "https://public.bnbstatic.com/static/content/square/images/1.jpg\n"
            "https://public.bnbstatic.com/video/pgc/ArticleContent/1.mp4"
        )
        assert row.detection_path == "feed-recommend"
        assert row.share_url is not None
        # Distinct timestamps
        assert row.published_at is not None
        assert row.first_detected_at is not None
        assert row.last_observed_at is not None


def test_ingest_is_idempotent():
    engine = fresh_db()
    with Session(engine) as db:
        posts = normalize_feed_response(feed_payload([make_card()]), {"BULLA", "HOME"})
        request = SanitizedIngestRequest(
            source="binance-square-browser",
            collected_at=datetime.now(timezone.utc),
            posts=posts,
        )
        first = ingest_sanitized(db, request)
        second = ingest_sanitized(db, request)
        assert first["inserted"] == 1
        assert second["updated"] == 1
        assert len(list(db.scalars(select(SocialPost)))) == 1
        assert len(list(db.scalars(select(PostMention)))) == 2


def test_ingest_merges_across_detection_paths():
    """A post seen via recommend then search must merge, not duplicate.

    first_detected_at is immutable (earliest wins); last_observed_at
    advances to the newest observation.
    """
    engine = fresh_db()
    with Session(engine) as db:
        card = make_card()
        # First observation via recommend path, with an earlier detected_at.
        posts_reco = normalize_feed_response(
            feed_payload([card]), {"BULLA", "HOME"}, detection_path="feed-recommend"
        )
        earlier = datetime.now(timezone.utc) - timedelta(minutes=5)
        posts_reco[0].detected_at = earlier
        ingest_sanitized(db, SanitizedIngestRequest(
            source="binance-square-browser",
            collected_at=datetime.now(timezone.utc),
            posts=posts_reco,
        ))
        # Second observation via search path, later detected_at.
        posts_search = normalize_feed_response(
            feed_payload([card]), {"BULLA", "HOME"}, detection_path="feed-search"
        )
        later = datetime.now(timezone.utc)
        posts_search[0].detected_at = later
        result = ingest_sanitized(db, SanitizedIngestRequest(
            source="binance-square-browser",
            collected_at=datetime.now(timezone.utc),
            posts=posts_search,
        ))
        assert result["updated"] == 1
        assert len(list(db.scalars(select(SocialPost)))) == 1
        row = db.scalar(select(SocialPost))
        # first_detected_at stays at the earlier observation
        assert row.first_detected_at.replace(tzinfo=timezone.utc) == earlier.replace(tzinfo=timezone.utc)
        # last_observed_at advanced to the later observation
        assert row.last_observed_at.replace(tzinfo=timezone.utc) == later.replace(tzinfo=timezone.utc)
        # detection_path stays as the first path (first observation wins)
        assert row.detection_path == "feed-recommend"


def test_ingest_populates_author_registry():
    engine = fresh_db()
    with Session(engine) as db:
        posts = normalize_feed_response(feed_payload([make_card()]), {"BULLA", "HOME"})
        ingest_sanitized(db, SanitizedIngestRequest(
            source="binance-square-browser",
            collected_at=datetime.now(timezone.utc),
            posts=posts,
        ))
        authors = list(db.scalars(select(SquareAuthor)))
        assert len(authors) == 1
        author = authors[0]
        assert author.author_id == "author-1"
        assert author.author_name == "CZ"
        assert author.post_count == 1
        assert author.last_post_id == "post-1"


def test_author_registry_merges_on_reingest():
    engine = fresh_db()
    with Session(engine) as db:
        card = make_card()
        posts = normalize_feed_response(feed_payload([card]), {"BULLA", "HOME"})
        request = SanitizedIngestRequest(
            source="binance-square-browser",
            collected_at=datetime.now(timezone.utc),
            posts=posts,
        )
        ingest_sanitized(db, request)
        ingest_sanitized(db, request)
        authors = list(db.scalars(select(SquareAuthor)))
        assert len(authors) == 1
        # post_count increments per observation
        assert authors[0].post_count == 2


def test_x_source_does_not_populate_square_authors():
    """X posts must not leak into the Square author registry."""
    engine = fresh_db()
    with Session(engine) as db:
        post = SanitizedSquarePost(
            source_post_id="x-123",
            published_at=datetime.now(timezone.utc),
            author_id="x-author",
            author_name="X User",
            symbols=["BULLA"],
            engagement=PublicEngagement(likes=1),
            detection_path="fixture",
        )
        ingest_sanitized(db, SanitizedIngestRequest(
            source="x-grok-cli",
            collected_at=datetime.now(timezone.utc),
            posts=[post],
        ))
        authors = list(db.scalars(select(SquareAuthor)))
        assert len(authors) == 0


# --- SQLite additive migration ------------------------------------------


def test_sqlite_migration_adds_columns_to_existing_db():
    """An existing social_posts table must gain the new columns in place."""
    engine = create_engine("sqlite:///:memory:")
    # Simulate an old DB: create the table WITHOUT the new columns.
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("""
            CREATE TABLE social_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source VARCHAR(64) DEFAULT 'fixture',
                source_post_id VARCHAR(128),
                author_id VARCHAR(128),
                author_name VARCHAR(256) DEFAULT '',
                text TEXT,
                normalized_text TEXT,
                public_url VARCHAR(2000),
                verification_type INTEGER,
                card_type VARCHAR(64) DEFAULT '',
                content_type INTEGER,
                likes INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                shares INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                account_age_days INTEGER,
                observed_at DATETIME,
                ingested_at DATETIME
            )
        """))
        conn.execute(text("""
            CREATE TABLE tracked_tokens (
                symbol VARCHAR(32) PRIMARY KEY,
                canonical_pair VARCHAR(40) UNIQUE,
                lana_phase VARCHAR(32) DEFAULT 'NORMAL',
                source VARCHAR(64) DEFAULT 'fixture',
                priority INTEGER DEFAULT 3,
                metadata_json JSON DEFAULT '{}',
                effective_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source VARCHAR(64),
                status VARCHAR(32) DEFAULT 'SUCCESS',
                received_count INTEGER DEFAULT 0,
                inserted_count INTEGER DEFAULT 0,
                updated_count INTEGER DEFAULT 0,
                rejected_count INTEGER DEFAULT 0,
                collected_at DATETIME,
                completed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                error_category VARCHAR(64)
            )
        """))
    # Run the additive migration against this old schema.
    # We need to point _ensure_sqlite_columns at this engine; it reads the
    # module-level engine, so we monkeypatch it.
    import app.db as dbmod
    original_engine = dbmod.engine
    dbmod.engine = engine
    try:
        dbmod._ensure_sqlite_columns()
    finally:
        dbmod.engine = original_engine
    with engine.begin() as conn:
        from sqlalchemy import text
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(social_posts)")).fetchall()}
    assert "published_at" in cols
    assert "first_detected_at" in cols
    assert "last_observed_at" in cols
    assert "coin_pairs" in cols
    assert "tendency" in cols
    assert "bullish_ratio" in cols
    assert "bearish_ratio" in cols
    assert "hashtags" in cols
    assert "mentions" in cols
    assert "is_reply" in cols
    assert "parent_id" in cols
    assert "is_sticky" in cols
    assert "media_urls" in cols
    assert "detection_path" in cols
    assert "share_url" in cols


def test_sqlite_migration_is_idempotent():
    """Running the migration twice must not error."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("""
            CREATE TABLE social_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source VARCHAR(64),
                source_post_id VARCHAR(128),
                observed_at DATETIME,
                ingested_at DATETIME
            )
        """))
        conn.execute(text("""
            CREATE TABLE tracked_tokens (
                symbol VARCHAR(32) PRIMARY KEY
            )
        """))
        conn.execute(text("""
            CREATE TABLE ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source VARCHAR(64),
                collected_at DATETIME
            )
        """))
    import app.db as dbmod
    original_engine = dbmod.engine
    dbmod.engine = engine
    try:
        dbmod._ensure_sqlite_columns()
        dbmod._ensure_sqlite_columns()
    finally:
        dbmod.engine = original_engine


def test_sqlite_migration_handles_oldest_schema():
    """The oldest on-disk schema (symbol/engagement columns, no likes/
    public_url/etc.) must be brought forward in place."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        from sqlalchemy import text
        # Oldest social_posts schema: has `symbol` and `engagement` instead
        # of the split engagement columns and per-field enrichment.
        conn.execute(text("""
            CREATE TABLE social_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source VARCHAR(32),
                source_post_id VARCHAR(128),
                symbol VARCHAR(32),
                author_id VARCHAR(128),
                text TEXT,
                normalized_text TEXT,
                engagement INTEGER,
                account_age_days INTEGER,
                observed_at DATETIME,
                ingested_at DATETIME
            )
        """))
        conn.execute(text("""
            CREATE TABLE tracked_tokens (
                symbol VARCHAR(32) PRIMARY KEY,
                canonical_pair VARCHAR(40) UNIQUE,
                lana_phase VARCHAR(32) DEFAULT 'NORMAL',
                source VARCHAR(64) DEFAULT 'fixture',
                priority INTEGER DEFAULT 3,
                metadata_json JSON DEFAULT '{}',
                effective_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source VARCHAR(64),
                status VARCHAR(32) DEFAULT 'SUCCESS',
                received_count INTEGER DEFAULT 0,
                inserted_count INTEGER DEFAULT 0,
                updated_count INTEGER DEFAULT 0,
                rejected_count INTEGER DEFAULT 0,
                collected_at DATETIME,
                completed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                error_category VARCHAR(64)
            )
        """))
    import app.db as dbmod
    original_engine = dbmod.engine
    dbmod.engine = engine
    try:
        dbmod._ensure_sqlite_columns()
    finally:
        dbmod.engine = original_engine
    with engine.begin() as conn:
        from sqlalchemy import text
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(social_posts)")).fetchall()}
    # Catch-up columns
    assert "author_name" in cols
    assert "public_url" in cols
    assert "likes" in cols
    assert "comments" in cols
    assert "shares" in cols
    assert "views" in cols
    # Enrichment columns
    assert "published_at" in cols
    assert "first_detected_at" in cols
    assert "coin_pairs" in cols
    assert "detection_path" in cols
    # The old `symbol` and `engagement` columns are still there (SQLite
    # can't drop columns), but the model no longer references them.
    assert "symbol" in cols
    assert "engagement" in cols


# --- Feed coverage ------------------------------------------------------


def test_feed_coverage_upsert_is_idempotent():
    engine = fresh_db()
    with Session(engine) as db:
        batch_at = datetime.now(timezone.utc)
        row1 = SquareFeedCoverage(
            detection_path="feed-recommend", batch_at=batch_at,
            cards_observed=10, matched_posts=2, symbols_covered="BULLA,HOME",
        )
        db.add(row1)
        db.commit()
        # Re-report same batch: should update, not duplicate.
        row2 = db.scalar(select(SquareFeedCoverage).where(
            SquareFeedCoverage.detection_path == "feed-recommend",
            SquareFeedCoverage.batch_at == batch_at,
        ))
        assert row2 is not None
        row2.matched_posts = 3
        db.commit()
        assert len(list(db.scalars(select(SquareFeedCoverage)))) == 1
        assert db.scalar(select(SquareFeedCoverage)).matched_posts == 3
