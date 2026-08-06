from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .config import settings
from .models import Base


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _ensure_sqlite_columns() -> None:
    """Additive migrations for long-lived SQLite desk DBs."""
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(tracked_tokens)")).fetchall()
        }
        if "active" not in cols:
            # 1 = on Lana kanban (shown on radar); 0 = archived, data retained
            conn.execute(text(
                "ALTER TABLE tracked_tokens ADD COLUMN active INTEGER NOT NULL DEFAULT 1"
            ))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_sqlite_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
