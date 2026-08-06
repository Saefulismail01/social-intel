import json

from app.lana_adapter import LanaUniverseAdapter


def test_fixture_adapter(tmp_path):
    fixture = tmp_path / "universe.json"
    fixture.write_text(json.dumps([{"symbol": "HOME", "lana_phase": "EXHAUSTION", "priority": 0}]))
    rows = LanaUniverseAdapter(fixture_path=fixture).fetch()
    assert rows[0]["symbol"] == "HOME"
    assert rows[0]["lana_phase"] == "EXHAUSTION"


def test_fixture_adapter_drops_non_kanban_phases(tmp_path):
    """Desk universe mirrors Lana kanban only — not NORMAL / ACCUMULATION."""
    fixture = tmp_path / "universe.json"
    fixture.write_text(json.dumps([
        {"symbol": "HOME", "lana_phase": "EXHAUSTION", "priority": 0},
        {"symbol": "NOISE", "lana_phase": "NORMAL", "priority": 1},
        {"symbol": "ACC", "lana_phase": "ACCUMULATION", "priority": 2},
        {"symbol": "HOT", "lana_phase": "IGNITION", "priority": 0},
    ]))
    rows = LanaUniverseAdapter(fixture_path=fixture).fetch()
    symbols = {row["symbol"] for row in rows}
    assert symbols == {"HOME", "HOT"}
