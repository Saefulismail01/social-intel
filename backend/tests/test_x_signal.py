from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.ingestion import snowflake_time, timestamp_disagrees
from app.main import app


SNOWFLAKE_EPOCH_MS = 1_288_834_974_657


def make_post_id(when: datetime) -> str:
    """Build an X-style snowflake ID encoding the given creation time."""
    milliseconds = int(when.timestamp() * 1000) - SNOWFLAKE_EPOCH_MS
    return str(milliseconds << 22)


def ingest(client, posts, source="x-grok-cli"):
    return client.post("/api/ingest", json={
        "source": source,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "posts": posts,
    })


def x_post(symbol, when, author="alice"):
    post_id = make_post_id(when)
    return {
        "source_post_id": post_id,
        "observed_at": when.isoformat(),
        "author_id": author,
        "author_name": author,
        "text": f"${symbol} looking strong",
        "public_url": f"https://x.com/{author}/status/{post_id}",
        "symbols": [symbol],
        "engagement": {"likes": 1, "comments": 0, "shares": 0, "views": 10},
    }


def radar_row(client, symbol):
    return next(row for row in client.get("/api/radar").json() if row["symbol"] == symbol)


def test_snowflake_guard_rejects_year_drifted_posts():
    """A post ID that decodes to a different year than the claimed timestamp is a lie."""
    real = datetime(2025, 8, 4, 17, 44, tzinfo=timezone.utc)
    post_id = make_post_id(real)
    assert snowflake_time(post_id).year == 2025
    assert not timestamp_disagrees("x-grok-cli", post_id, real)
    assert timestamp_disagrees("x-grok-cli", post_id, real.replace(year=2026))
    # Non-X sources use their own ID schemes and must not be snowflake-checked.
    assert not timestamp_disagrees("binance-square-browser", "not-a-snowflake", real)
    assert timestamp_disagrees("x-grok-cli", "not-a-snowflake", real)


def test_ingest_rejects_timestamp_that_contradicts_post_id():
    with TestClient(app) as client:
        client.post("/api/universe/track", json={"symbol": "ZTEST", "priority": 0})
        honest_time = datetime.now(timezone.utc) - timedelta(hours=2)
        honest = x_post("ZTEST", honest_time)

        liar = dict(honest)
        liar["source_post_id"] = make_post_id(honest_time - timedelta(days=365))
        liar["observed_at"] = honest_time.isoformat()

        result = ingest(client, [honest, liar]).json()
        assert result["inserted"] == 1
        assert result["rejected"] == 1


def test_radar_separates_unscanned_hours_from_quiet_hours():
    """An hour nobody queried is not an hour with zero posts."""
    with TestClient(app) as client:
        client.post("/api/universe/track", json={"symbol": "ZCOV", "priority": 0})

        row = radar_row(client, "ZCOV")
        assert row["x_signal"]["state"] == "NOT_SCANNED"
        assert row["x_signal"]["coverage"]["scanned_hours"] == 0

        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=3)
        client.post("/api/x-coverage", json=[{
            "symbol": "ZCOV",
            "slice_start": start.isoformat(),
            "slice_end": (start + timedelta(hours=1)).isoformat(),
            "status": "SUCCESS",
            "posts_found": 0,
        }])

        row = radar_row(client, "ZCOV")
        assert row["x_signal"]["state"] == "NO_DATA"
        assert row["x_signal"]["coverage"]["scanned_hours"] >= 1
        assert row["x_signal"]["coverage"]["window_hours"] == 168


def test_radar_counts_full_week_not_just_the_last_hour():
    """Headline counts describe one fixed window, and are not capped."""
    with TestClient(app) as client:
        client.post("/api/universe/track", json={"symbol": "ZWEEK", "priority": 0})
        now = datetime.now(timezone.utc)
        posts = [
            x_post("ZWEEK", now - timedelta(days=days, minutes=minutes), author=f"user{days}{minutes}")
            for days in range(1, 6)
            for minutes in range(0, 120, 10)
        ]
        posts.append(x_post("ZWEEK", now - timedelta(minutes=5), author="recent"))
        for index in range(0, len(posts), 100):
            assert ingest(client, posts[index:index + 100]).status_code == 200

        signal = radar_row(client, "ZWEEK")["x_signal"]
        assert signal["posts"] == len(posts)
        assert signal["posts_1h"] == 1
        # The old logic reported the 1-hour count here whenever anything was live.
        assert signal["posts"] > signal["posts_1h"]
        assert sum(day["posts"] for day in signal["history"]) == len(posts)


def test_radar_baseline_becomes_the_authoritative_count():
    """When X supplies official counts, the radar reports those, not the harvest."""
    with TestClient(app) as client:
        client.post("/api/universe/track", json={"symbol": "ZRADAR", "priority": 0})
        now = datetime.now(timezone.utc)

        # Harvest finds far fewer posts than X says exist.
        harvested = [
            x_post("ZRADAR", now - timedelta(days=2, hours=hour), author=f"u{hour}")
            for hour in range(1, 6)
        ]
        assert ingest(client, harvested).status_code == 200

        days = [
            {"day": (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
             "post_count": count}
            for offset, count in enumerate([24, 51, 22, 81, 103, 171, 47])
        ]
        client.post("/api/x-baseline", json=[{"symbol": "ZRADAR", "query": "$ZRADAR", "days": days}])

        signal = radar_row(client, "ZRADAR")["x_signal"]
        assert signal["source"] == "x-radar"
        assert signal["posts"] == sum(day["post_count"] for day in days)
        assert signal["posts"] > len(harvested)
        # Fields X does not expose must be null, never a zero that reads as data.
        assert signal["unique_authors"] is None
        assert signal["views"] is None
        assert signal["author_concentration"] is None
        assert signal["granularity"] == "day"
        # The shortfall stays visible rather than being papered over.
        assert signal["capture"]["captured_posts"] == len(harvested)
        assert signal["capture"]["capture_ratio"] < 0.2


def test_radar_state_reflects_daily_volume_against_the_median():
    with TestClient(app) as client:
        client.post("/api/universe/track", json={"symbol": "ZSURGE", "priority": 0})
        now = datetime.now(timezone.utc)
        # Six quiet days, then a spike today.
        counts = [400, 10, 10, 10, 10, 10, 10]
        days = [
            {"day": (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
             "post_count": count}
            for offset, count in enumerate(counts)
        ]
        client.post("/api/x-baseline", json=[{"symbol": "ZSURGE", "query": "$ZSURGE", "days": days}])

        signal = radar_row(client, "ZSURGE")["x_signal"]
        assert signal["state"] == "SURGING"
        assert signal["posts_today"] == 400
        assert signal["acceleration"] > 2


def test_x_coverage_upserts_and_is_readable_by_the_harvester():
    with TestClient(app) as client:
        client.post("/api/universe/track", json={"symbol": "ZSLICE", "priority": 0})
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start, end = now - timedelta(hours=5), now - timedelta(hours=4)
        body = {
            "symbol": "ZSLICE",
            "slice_start": start.isoformat(),
            "slice_end": end.isoformat(),
            "status": "SUCCESS",
            "posts_found": 3,
            "saturated": False,
        }
        assert client.post("/api/x-coverage", json=[body]).json()["recorded"] == 1
        assert client.post("/api/x-coverage", json=[body | {"posts_found": 7}]).json()["recorded"] == 1

        slices = client.get("/api/x-coverage/ZSLICE").json()["slices"]
        matching = [item for item in slices if item["posts_found"] == 7]
        assert len(matching) == 1, "re-reporting a slice must update it, not duplicate it"


def test_x_coverage_ignores_untracked_symbols():
    with TestClient(app) as client:
        now = datetime.now(timezone.utc)
        result = client.post("/api/x-coverage", json=[{
            "symbol": "ZNOPE",
            "slice_start": (now - timedelta(hours=1)).isoformat(),
            "slice_end": now.isoformat(),
        }])
        assert result.json()["recorded"] == 0


def teardown_module(_):
    """Drop the synthetic symbols so a shared dev database is left as found."""
    from sqlalchemy import delete
    from app.models import CrowdSnapshot, PostMention, SocialPost, TrackedToken, XSliceCoverage, XVolumeBaseline

    symbols = ["ZTEST", "ZCOV", "ZWEEK", "ZSLICE", "ZRADAR", "ZSURGE"]
    with SessionLocal() as db:
        post_ids = [row.post_id for row in db.query(PostMention).filter(PostMention.symbol.in_(symbols))]
        db.execute(delete(PostMention).where(PostMention.symbol.in_(symbols)))
        if post_ids:
            db.execute(delete(SocialPost).where(SocialPost.id.in_(post_ids)))
        db.execute(delete(XSliceCoverage).where(XSliceCoverage.symbol.in_(symbols)))
        db.execute(delete(XVolumeBaseline).where(XVolumeBaseline.symbol.in_(symbols)))
        db.execute(delete(CrowdSnapshot).where(CrowdSnapshot.symbol.in_(symbols)))
        db.execute(delete(TrackedToken).where(TrackedToken.symbol.in_(symbols)))
        db.commit()
