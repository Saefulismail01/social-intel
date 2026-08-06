"""Tests for the slicing logic in scripts/collect-x.py.

The harvester lives outside the package, so it is loaded by path.
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "collect-x.py"
SNOWFLAKE_EPOCH_MS = 1_288_834_974_657


def load_harvester():
    spec = importlib.util.spec_from_file_location("collect_x", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvester = pytest.importorskip("importlib") and load_harvester()


def make_post_id(when: datetime) -> str:
    return str((int(when.timestamp() * 1000) - SNOWFLAKE_EPOCH_MS) << 22)


def raw_post(when: datetime, handle="alice", claimed=None):
    return {
        "id": make_post_id(when),
        "author_handle": handle,
        "author_name": handle,
        "text": "$TAG going up",
        "observed_at": (claimed or when).isoformat(),
        "likes": 1, "replies": 0, "reposts": 0, "views": 5,
    }


def test_base_slices_tile_the_window_without_gaps_or_overlap():
    start = datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc)
    end = datetime(2026, 8, 1, 4, 0, tzinfo=timezone.utc)
    slices = harvester.base_slices(start, end)

    assert slices[0][0] <= start, "the first slice must cover the requested start"
    assert slices[-1][1] >= end, "the last slice must cover the requested end"
    for (_, previous_end), (next_start, _) in zip(slices, slices[1:]):
        assert previous_end == next_start


def test_base_slices_land_on_the_same_grid_regardless_of_launch_time():
    """Boundaries must be reproducible, or resuming rescans the whole window."""
    end = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    first = harvester.base_slices(datetime(2026, 8, 1, 6, 3, tzinfo=timezone.utc), end)
    second = harvester.base_slices(datetime(2026, 8, 1, 6, 47, tzinfo=timezone.utc), end)

    assert first == second
    assert all(item[0].minute == 0 and item[0].second == 0 for item in first)


def test_query_carries_the_time_window_as_search_operators():
    """Without since/until the search only ever returns the newest 10 posts."""
    start = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    query = harvester.build_query("TAG", start, start + timedelta(hours=1))
    assert query == "$TAG since:2026-08-01_12:00:00_UTC until:2026-08-01_13:00:00_UTC"


def test_observed_at_comes_from_the_post_id_not_the_transcription():
    real = datetime(2025, 8, 4, 17, 44, tzinfo=timezone.utc)
    window_start = real - timedelta(minutes=30)
    window_end = real + timedelta(minutes=30)

    lying = raw_post(real, claimed=real.replace(year=2026))
    record = harvester.normalize(lying, "TAG", window_start, window_end)

    assert record is not None
    assert record["observed_at"].startswith("2025-08-04T17:44")


def test_posts_outside_the_queried_window_are_dropped():
    window_start = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
    window_end = window_start + timedelta(hours=1)
    a_year_early = raw_post(window_start.replace(year=2025) + timedelta(minutes=10))

    assert harvester.normalize(a_year_early, "TAG", window_start, window_end) is None
    inside = raw_post(window_start + timedelta(minutes=10))
    assert harvester.normalize(inside, "TAG", window_start, window_end) is not None


def test_public_url_is_built_from_handle_and_id():
    when = datetime(2026, 8, 4, 18, 10, tzinfo=timezone.utc)
    record = harvester.normalize(raw_post(when, handle="bob"), "TAG", when - timedelta(minutes=1), when + timedelta(minutes=1))
    assert record["public_url"] == f"https://x.com/bob/status/{record['source_post_id']}"
    assert record["author_id"] == "bob"


def test_saturated_slice_is_split_until_it_stops_capping(monkeypatch):
    """A full page means the window was capped, so it must be subdivided."""
    start = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    calls = []

    def fake_query(symbol, slice_start, slice_end, depth):
        calls.append((slice_start, slice_end))
        result = harvester.SliceResult(slice_start, slice_end, "q", depth)
        # Only the full hour saturates; each half returns a distinct partial page.
        span_minutes = (slice_end - slice_start).total_seconds() / 60
        count = harvester.SEARCH_LIMIT if span_minutes > 30 else 4
        posts = [
            raw_post(slice_start + timedelta(minutes=index), handle=f"user{index}")
            for index in range(count)
        ]
        harvester.absorb(result, posts, symbol)
        return result

    monkeypatch.setattr(harvester, "grok_slice_once", fake_query)
    monkeypatch.setattr(harvester, "RETRIES", 0)
    results = harvester.harvest_slice("TAG", start, end)

    assert len(calls) == 3, "the capped hour plus its two halves"
    assert (start, start + timedelta(minutes=30)) in calls
    assert (start + timedelta(minutes=30), end) in calls
    # Merging the halves recovers posts the single capped query never returned.
    merged = {post_id for result in results for post_id in result.records}
    assert len(merged) > harvester.SEARCH_LIMIT


def test_unsaturated_slice_is_not_split(monkeypatch):
    start = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
    calls = []

    def fake_query(symbol, slice_start, slice_end, depth):
        calls.append((slice_start, slice_end))
        result = harvester.SliceResult(slice_start, slice_end, "q", depth)
        harvester.absorb(result, [raw_post(slice_start + timedelta(minutes=1))], symbol)
        return result

    monkeypatch.setattr(harvester, "grok_slice_once", fake_query)
    monkeypatch.setattr(harvester, "RETRIES", 0)
    harvester.harvest_slice("TAG", start, start + timedelta(hours=1))
    assert len(calls) == 1


def test_empty_result_is_marked_unconfirmed_after_retries(monkeypatch):
    """A transient empty must not be recorded as a confirmed quiet hour."""
    start = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)

    def always_empty(symbol, slice_start, slice_end, depth):
        return harvester.SliceResult(slice_start, slice_end, "q", depth)

    monkeypatch.setattr(harvester, "grok_slice_once", always_empty)
    monkeypatch.setattr(harvester, "RETRIES", 1)
    monkeypatch.setattr(harvester, "RETRY_BACKOFF_SECONDS", 0)
    result = harvester.grok_slice("TAG", start, start + timedelta(hours=1), 0)
    assert result.status == "EMPTY"


def test_retry_merges_with_the_first_attempt_rather_than_replacing_it(monkeypatch):
    """The search is flaky; a thin retry must never shrink collected evidence."""
    start = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
    attempts = {"n": 0}

    def flaky(symbol, slice_start, slice_end, depth):
        attempts["n"] += 1
        result = harvester.SliceResult(slice_start, slice_end, "q", depth)
        if attempts["n"] == 1:
            result.status, result.message = "ERROR", "boom"
            return result
        harvester.absorb(result, [raw_post(slice_start + timedelta(minutes=2))], symbol)
        return result

    monkeypatch.setattr(harvester, "grok_slice_once", flaky)
    monkeypatch.setattr(harvester, "RETRIES", 2)
    monkeypatch.setattr(harvester, "RETRY_BACKOFF_SECONDS", 0)
    result = harvester.grok_slice("TAG", start, start + timedelta(hours=1), 0)

    assert result.status == "SUCCESS"
    assert len(result.records) == 1
