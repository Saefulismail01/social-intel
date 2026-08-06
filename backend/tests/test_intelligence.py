from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.intelligence import compute_snapshot, normalize_text
from app.models import Base, IngestionRun, PostMention, SocialPost, TrackedToken


def test_normalize_text_removes_url_and_case():
    assert normalize_text("  BULLA Pump https://example.com/x ") == "bulla pump <url>"


def add_post(db, symbol, index, text, author, engagement, age, observed_at):
    post = SocialPost(
        source="test", source_post_id=str(index), author_id=author,
        text=text, normalized_text=normalize_text(text), likes=engagement,
        account_age_days=age, observed_at=observed_at,
    )
    db.add(post)
    db.flush()
    db.add(PostMention(post_id=post.id, symbol=symbol))


def test_broad_crowd_becomes_broadening():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        db.add(TrackedToken(symbol="BULLA", canonical_pair="BULLAUSDT", lana_phase="IGNITION"))
        db.commit()
        for index in range(20):
            add_post(db, "BULLA", index, f"BULLA discussion angle {index}", f"author-{index}", 10 + index, 100, now - timedelta(minutes=5))
        db.add(IngestionRun(source="test", collected_at=now, completed_at=now))
        db.commit()
        snapshot = compute_snapshot(db, "BULLA")
        assert snapshot.crowd_state == "BROADENING"
        assert snapshot.breadth_score >= 65
        assert snapshot.coordination_score < 20
        assert snapshot.data_confidence == 100


def test_duplicate_cluster_is_seeding():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        db.add(TrackedToken(symbol="MYX", canonical_pair="MYXUSDT"))
        db.commit()
        for index in range(12):
            add_post(db, "MYX", index, "MYX will moon now", f"bot-{index % 3}", 1, 2, now - timedelta(minutes=3))
        db.add(IngestionRun(source="test", collected_at=now, completed_at=now))
        db.commit()
        snapshot = compute_snapshot(db, "MYX")
        assert snapshot.crowd_state == "SEEDING"
        assert snapshot.coordination_score >= 58
