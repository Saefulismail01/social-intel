from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .config import settings
from .models import Base


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing SQLite table, idempotently.

    Skips tables that do not exist yet (they will be created by create_all).
    """
    existing_tables = {
        row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }
    if table not in existing_tables:
        return
    cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
    if column not in cols:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def _ensure_sqlite_columns() -> None:
    """Additive migrations for long-lived SQLite desk DBs.

    SQLite ALTER TABLE only adds columns at the end (no drop, no reorder),
    which is exactly the additive contract we want: an existing on-disk DB
    is brought forward in place, never rebuilt. New tables come from
    create_all(); new columns on existing tables come from here.

    This function is idempotent: it checks PRAGMA table_info before each
    ALTER, so it is safe to run on a DB at any migration level (including
    a fresh one where create_all already supplied every column).
    """
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        # tracked_tokens.active (legacy migration, kept for older DBs).
        _add_column_if_missing(conn, "tracked_tokens", "active",
                               "active INTEGER NOT NULL DEFAULT 1")

        # search_coverage: oldest/newest discovered post timestamps and the
        # cutoff the search aimed for (first validated-coverage increment).
        _add_column_if_missing(conn, "search_coverage", "oldest_post_at",
                               "oldest_post_at DATETIME")
        _add_column_if_missing(conn, "search_coverage", "newest_post_at",
                               "newest_post_at DATETIME")
        _add_column_if_missing(conn, "search_coverage", "cutoff_at",
                               "cutoff_at DATETIME")

        # social_posts: catch-up columns for older schemas that predate the
        # current model. The oldest on-disk DBs still carry `symbol` and
        # `engagement` instead of the split engagement columns. These
        # additive ALTERs bring them forward without a rebuild; the old
        # `symbol`/`engagement` columns remain (SQLite cannot drop columns)
        # but are simply unused by the current model.
        _add_column_if_missing(conn, "social_posts", "author_name",
                               "author_name VARCHAR(256) NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "social_posts", "public_url",
                               "public_url VARCHAR(2000)")
        _add_column_if_missing(conn, "social_posts", "verification_type",
                               "verification_type INTEGER")
        _add_column_if_missing(conn, "social_posts", "card_type",
                               "card_type VARCHAR(64) NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "social_posts", "content_type",
                               "content_type INTEGER")
        _add_column_if_missing(conn, "social_posts", "likes",
                               "likes INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "social_posts", "comments",
                               "comments INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "social_posts", "shares",
                               "shares INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "social_posts", "views",
                               "views INTEGER NOT NULL DEFAULT 0")

        # ingestion_runs: catch-up columns for older schemas.
        _add_column_if_missing(conn, "ingestion_runs", "batches_observed",
                               "batches_observed INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "ingestion_runs", "matched_post_count",
                               "matched_post_count INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "ingestion_runs", "unmatched_post_count",
                               "unmatched_post_count INTEGER NOT NULL DEFAULT 0")

        # social_posts enrichment: native PGC fields + distinct detection
        # timestamps + provenance (teardown §15.3/§15.6/§16). All nullable
        # or defaulted so existing rows and X-source rows stay valid.
        _add_column_if_missing(conn, "social_posts", "share_url",
                               "share_url VARCHAR(2000)")
        _add_column_if_missing(conn, "social_posts", "published_at",
                               "published_at DATETIME")
        _add_column_if_missing(conn, "social_posts", "first_detected_at",
                               "first_detected_at DATETIME")
        _add_column_if_missing(conn, "social_posts", "last_observed_at",
                               "last_observed_at DATETIME")
        _add_column_if_missing(conn, "social_posts", "coin_pairs",
                               "coin_pairs VARCHAR(512) NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "social_posts", "tendency",
                               "tendency VARCHAR(16)")
        _add_column_if_missing(conn, "social_posts", "bullish_ratio",
                               "bullish_ratio FLOAT")
        _add_column_if_missing(conn, "social_posts", "bearish_ratio",
                               "bearish_ratio FLOAT")
        _add_column_if_missing(conn, "social_posts", "hashtags",
                               "hashtags TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "social_posts", "mentions",
                               "mentions TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "social_posts", "is_reply",
                               "is_reply INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "social_posts", "parent_id",
                               "parent_id VARCHAR(128)")
        _add_column_if_missing(conn, "social_posts", "is_sticky",
                               "is_sticky INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "social_posts", "media_urls",
                               "media_urls TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "social_posts", "detection_path",
                               "detection_path VARCHAR(32)")


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_sqlite_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
