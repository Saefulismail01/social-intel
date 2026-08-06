from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ingestion import ingest_sanitized
from app.models import Base, PostMention, SocialPost, TrackedToken
from app.schemas import SanitizedIngestRequest
from app.square_normalizer import normalize_feed_response


def payload():
    return {
        "code": "000000",
        "data": {"vos": [{
            "id": "post-1", "squareAuthorId": "author-1", "authorName": "Public Author",
            "title": "$BULLA and $HOME are moving", "subTitle": "crowd discussion",
            "date": int(datetime.now(timezone.utc).timestamp() * 1000),
            "webLink": "https://www.binance.com/en/square/post/1",
            "cardType": "POST", "contentType": 1,
            "tradingPairsV2": [{"symbol": "BULLAUSDT"}],
            "userInputTradingPairs": [{"symbol": "HOMEUSDT"}],
            "likeCount": 5, "commentCount": 2, "shareCount": 1, "viewCount": 100,
        }]}
    }


def test_normalizer_retains_only_tracked_symbols():
    posts = normalize_feed_response(payload(), {"BULLA", "HOME"})
    assert len(posts) == 1
    assert posts[0].symbols == ["BULLA", "HOME"]
    assert posts[0].engagement.views == 100


def test_multi_token_ingest_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            TrackedToken(symbol="BULLA", canonical_pair="BULLAUSDT"),
            TrackedToken(symbol="HOME", canonical_pair="HOMEUSDT"),
        ])
        db.commit()
        posts = normalize_feed_response(payload(), {"BULLA", "HOME"})
        request = SanitizedIngestRequest(source="binance-square-browser", collected_at=datetime.now(timezone.utc), posts=posts)
        first = ingest_sanitized(db, request)
        second = ingest_sanitized(db, request)
        assert first["inserted"] == 1
        assert second["updated"] == 1
        assert len(list(db.scalars(select(SocialPost)))) == 1
        assert len(list(db.scalars(select(PostMention)))) == 2


def test_sensitive_fields_are_rejected():
    with pytest.raises(ValidationError):
        SanitizedIngestRequest.model_validate({
            "source": "binance-square-browser",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "cookie": "must-not-pass",
            "posts": [],
        })
