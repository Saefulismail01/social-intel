from fastapi.testclient import TestClient

from app.main import app


def test_health_and_fixture_radar():
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        sync = client.post("/api/universe/sync")
        assert sync.status_code == 200
        assert sync.json()["synced"] >= 1

        radar = client.get("/api/radar")
        assert radar.status_code == 200
        assert any(row["symbol"] == "HOME" for row in radar.json())
