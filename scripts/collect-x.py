#!/usr/bin/env python3
"""Harvest public X posts for tracked tokens using the Grok CLI's native X tools.

Why this is shaped the way it is
--------------------------------
Grok exposes `x_keyword_search`, which queries X's own search index and supports
advanced operators (`since:`, `until:`, `min_faves:`, ...). It has one hard
constraint that dictates the whole design: **it returns at most 10 posts per
call, and only the newest ones inside the queried window.**

Measured against $TAG on 2026-08-01:

    whole day            -> 7 posts, all between 20:41 and 23:43 UTC
    00:00-06:00 slice    -> 3 posts, none of which the daily query returned
    12:00-15:00 slice    -> 5 posts, none of which the daily query returned
    01:00-02:00 slice    -> 2 posts, which the 6h slice above missed

So a single query per token can never see more than a sliver of a busy day. The
timeline is reconstructed by walking narrow time slices and merging them, and by
splitting any slice that comes back saturated (== limit) until it stops
saturating. Every scanned slice is reported to the API so runs resume rather
than rescan, and so unscanned hours are never mistaken for quiet hours.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

API_URL = os.getenv("SI_API_URL", "http://127.0.0.1:8000").rstrip("/")
GROK_BIN = os.getenv("SI_GROK_BIN", str(Path.home() / ".grok/bin/grok"))
MAX_SYMBOLS = int(os.getenv("SI_X_MAX_SYMBOLS", "12"))
LOOKBACK_MINUTES = int(os.getenv("SI_X_LOOKBACK_MINUTES", "180"))
STATE_FILE = Path(os.getenv("SI_X_STATE_FILE", "/tmp/social-intelligence-x-offset"))

# Slice sizing. Base slices are the unit of coverage; a saturated slice is halved
# until MIN_SLICE_MINUTES, which is where the 10-post cap stops being the binding
# constraint even for a token trending hard.
SLICE_MINUTES = int(os.getenv("SI_X_SLICE_MINUTES", "60"))
MIN_SLICE_MINUTES = int(os.getenv("SI_X_MIN_SLICE_MINUTES", "5"))
SEARCH_LIMIT = int(os.getenv("SI_X_SEARCH_LIMIT", "10"))  # tool-enforced maximum
WORKERS = int(os.getenv("SI_X_WORKERS", "3"))
GROK_TIMEOUT = int(os.getenv("SI_X_GROK_TIMEOUT", "180"))
GROK_MODEL = os.getenv("SI_X_GROK_MODEL", "")
INGEST_CHUNK = 100  # SanitizedIngestRequest caps posts per batch

# The search backend is flaky under load: the same window has been observed
# returning 10 posts on one call and 0 on the next. An unretried empty result
# would be written into the coverage ledger as "this hour is scanned and quiet",
# permanently baking in a gap, so empties are retried like errors are.
RETRIES = int(os.getenv("SI_X_RETRIES", "2"))
RETRY_BACKOFF_SECONDS = float(os.getenv("SI_X_RETRY_BACKOFF_SECONDS", "20"))
MIN_CALL_GAP_SECONDS = float(os.getenv("SI_X_MIN_CALL_GAP_SECONDS", "2"))

_throttle = threading.Lock()
_last_call = 0.0


def throttle() -> None:
    """Space out search calls; bursts of parallel calls trigger empty responses."""
    global _last_call
    with _throttle:
        wait = MIN_CALL_GAP_SECONDS - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()

POST_SCHEMA = {
    "type": "object",
    "properties": {
        "posts": {
            "type": "array",
            "maxItems": 25,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "author_handle": {"type": "string"},
                    "author_name": {"type": "string"},
                    "text": {"type": "string"},
                    "observed_at": {"type": "string"},
                    "likes": {"type": "integer", "minimum": 0},
                    "replies": {"type": "integer", "minimum": 0},
                    "reposts": {"type": "integer", "minimum": 0},
                    "views": {"type": "integer", "minimum": 0},
                },
                "required": [
                    "id", "author_handle", "author_name", "text", "observed_at",
                    "likes", "replies", "reposts", "views",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["posts"],
    "additionalProperties": False,
}


class SliceResult:
    __slots__ = ("start", "end", "query", "records", "returned", "rejected", "status", "saturated", "depth", "message")

    def __init__(self, start: datetime, end: datetime, query: str, depth: int) -> None:
        self.start, self.end, self.query, self.depth = start, end, query, depth
        self.records: dict[str, dict[str, Any]] = {}  # post id -> normalized post
        self.returned = 0   # posts the search returned, before validation
        self.rejected = 0   # returned posts whose real timestamp fell outside the slice
        self.status = "SUCCESS"
        self.saturated = False
        self.message: str | None = None

    @property
    def posts(self) -> list[dict[str, Any]]:
        return list(self.records.values())


def request_json(path: str, payload: Any | None = None, method: str | None = None) -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{API_URL}{path}", data=body,
        headers={"content-type": "application/json"} if body else {},
        method=method or ("POST" if body else "GET"),
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def selected_symbols(explicit: list[str] | None) -> tuple[list[str], int, int]:
    """Rotate through the universe unless the caller named symbols explicitly."""
    if explicit:
        return [item.upper().replace("USDT", "") for item in explicit], 0, len(explicit)
    rows = request_json("/api/universe")
    rows.sort(key=lambda row: (row.get("priority", 3), row["symbol"]))
    symbols = [row["symbol"] for row in rows]
    if not symbols:
        return [], 0, 0
    try:
        offset = int(STATE_FILE.read_text().strip()) % len(symbols)
    except (OSError, ValueError):
        offset = 0
    count = min(MAX_SYMBOLS, len(symbols))
    selected = [symbols[(offset + index) % len(symbols)] for index in range(count)]
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(str((offset + count) % len(symbols)))
    return selected, offset, len(symbols)


def format_operator_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d_%H:%M:%S_UTC")


def build_query(symbol: str, start: datetime, end: datetime) -> str:
    # Cashtag keeps the crypto meaning of ambiguous tickers ($TAG, $M, $ON, $US)
    # without needing the model to judge relevance.
    return f"${symbol} since:{format_operator_time(start)} until:{format_operator_time(end)}"


PROMPT_TEMPLATE = """Call the tool x_keyword_search EXACTLY ONCE with exactly these arguments:
query: {query}
mode: Latest
limit: {limit}

Do not call any other tool. Do not call x_keyword_search a second time. Do not use web search.

Then transcribe EVERY post the tool returned into the required JSON schema. Rules:
- Copy values verbatim from the tool output. Never invent, infer, estimate or fill in a missing value.
- id: the "ID" field exactly as given.
- author_handle: the @handle from the "Author" field, without the leading @.
- author_name: the display-name part of the "Author" field.
- observed_at: the "Timestamp" field converted to ISO-8601 UTC, format YYYY-MM-DDTHH:MM:SSZ.
- likes, replies, reposts, views: parse from the "Engagement" field (Likes, Replies, Reposts, Views). Use 0 for any counter the tool did not report.
- text: the "Content" field verbatim.
- Return every post the tool returned, including replies and reposts. Do not filter, rank, re-order, summarise or drop duplicates.
- If the tool returned no posts, return {{"posts": []}}.
"""


def grok_slice_once(symbol: str, start: datetime, end: datetime, depth: int) -> SliceResult:
    query = build_query(symbol, start, end)
    result = SliceResult(start, end, query, depth)
    command = [
        GROK_BIN, "--cwd", str(Path(__file__).resolve().parents[1]),
        "--single", PROMPT_TEMPLATE.format(query=query, limit=SEARCH_LIMIT),
        "--output-format", "json", "--json-schema", json.dumps(POST_SCHEMA),
        "--max-turns", "4", "--no-memory", "--no-subagents", "--permission-mode", "dontAsk",
    ]
    if GROK_MODEL:
        command += ["--model", GROK_MODEL]
    throttle()
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=GROK_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        result.status, result.message = "TIMEOUT", f"grok timed out after {GROK_TIMEOUT}s"
        return result
    if completed.returncode:
        result.status = "ERROR"
        result.message = (completed.stderr.strip() or f"grok exited {completed.returncode}")[:200]
        return result
    try:
        raw_posts = parse_posts(completed.stdout)
    except (ValueError, KeyError, TypeError) as error:
        result.status, result.message = "PARSE_ERROR", str(error)[:200]
        return result
    absorb(result, raw_posts, symbol)
    return result


def absorb(result: SliceResult, raw_posts: list[dict[str, Any]], symbol: str) -> None:
    """Validate returned posts into a slice result and recompute saturation."""
    result.returned += len(raw_posts)
    for raw in raw_posts:
        record = normalize(raw, symbol, result.start, result.end)
        if record is None:
            result.rejected += 1
            continue
        result.records.setdefault(record["source_post_id"], record)
    # Saturation is judged on what the search returned, not on what survived
    # validation: a full page still means the window was capped and needs
    # splitting, even if most of that page was out-of-window noise.
    result.saturated = result.returned >= SEARCH_LIMIT
    if result.rejected:
        result.message = f"{result.rejected}/{result.returned} returned posts fell outside the window"


def grok_slice(symbol: str, start: datetime, end: datetime, depth: int) -> SliceResult:
    """Query one window, retrying failures and unconfirmed empties.

    Attempts are merged rather than replaced: a retry that comes back thinner
    than the first attempt must not shrink the evidence already collected.
    """
    best = grok_slice_once(symbol, start, end, depth)
    for attempt in range(RETRIES):
        if best.status == "SUCCESS" and best.records:
            return best
        time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        retry = grok_slice_once(symbol, start, end, depth)
        if retry.status != "SUCCESS":
            if best.status != "SUCCESS":
                best = retry
            continue
        best.records.update(retry.records)
        best.returned = max(best.returned, retry.returned)
        best.rejected = max(best.rejected, retry.rejected)
        best.status, best.message = "SUCCESS", retry.message
        best.saturated = best.returned >= SEARCH_LIMIT
    if best.status == "SUCCESS" and not best.records:
        # Distinguishable from a confirmed-quiet hour, and re-runnable via
        # --rescan-empty once the search backend is behaving.
        best.status, best.message = "EMPTY", f"no posts after {RETRIES + 1} attempts"
    return best


def parse_posts(stdout: str) -> list[dict[str, Any]]:
    envelope = json.loads(stdout)
    text = envelope.get("text")
    if not isinstance(text, str):
        return envelope.get("posts", [])
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed, _ = json.JSONDecoder().raw_decode(text)
    return parsed.get("posts", [])


def harvest_slice(symbol: str, start: datetime, end: datetime, depth: int = 0) -> list[SliceResult]:
    """Scan one window, splitting it while the 10-post cap is still binding."""
    result = grok_slice(symbol, start, end, depth)
    duration_minutes = (end - start).total_seconds() / 60
    if not result.saturated or duration_minutes <= MIN_SLICE_MINUTES:
        return [result]
    midpoint = start + (end - start) / 2
    return [result] + harvest_slice(symbol, start, midpoint, depth + 1) + harvest_slice(symbol, midpoint, end, depth + 1)


def base_slices(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Tile the window with slices snapped to a fixed grid.

    Grid alignment is what makes coverage comparable between runs: a slice
    starting at whatever minute a run happened to launch would never match the
    slice a previous run recorded, so every run would rescan everything.
    """
    slices = []
    step = timedelta(minutes=SLICE_MINUTES)
    grid_start = start.replace(minute=0, second=0, microsecond=0)
    while (grid_start + step) <= start:
        grid_start += step
    cursor = grid_start
    while cursor < end:
        slices.append((cursor, cursor + step))
        cursor += step
    return slices


def parse_api_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def already_scanned(symbol: str, days: int, rescan_empty: bool) -> set[tuple[int, int]]:
    """Slice boundaries already covered, keyed to whole seconds."""
    try:
        payload = request_json(f"/api/x-coverage/{symbol}?days={days}")
    except (urllib.error.URLError, OSError, ValueError):
        return set()
    accepted = {"SUCCESS"} if rescan_empty else {"SUCCESS", "EMPTY"}
    return {
        (int(parse_api_time(row["slice_start"]).timestamp()), int(parse_api_time(row["slice_end"]).timestamp()))
        for row in payload.get("slices", [])
        if row.get("status") in accepted
    }


def pending_slices(
    symbol: str, start: datetime, end: datetime, days: int,
    refresh_minutes: int, rescan_empty: bool,
) -> list[tuple[datetime, datetime]]:
    """Base slices still needing a scan.

    The most recent slices are always rescanned: they were first queried while
    the window was still filling, so their counts are incomplete by
    construction.
    """
    scanned = already_scanned(symbol, days, rescan_empty)
    fresh_boundary = datetime.now(timezone.utc) - timedelta(minutes=refresh_minutes)
    pending = []
    for slice_start, slice_end in base_slices(start, end):
        key = (int(slice_start.timestamp()), int(slice_end.timestamp()))
        if slice_end > fresh_boundary or key not in scanned:
            pending.append((slice_start, slice_end))
    return pending


# X post IDs are snowflakes: the creation time is encoded in the high bits, so
# a post's real timestamp can be derived from its ID without trusting anything
# the search backend or the model reported.
SNOWFLAKE_EPOCH_MS = 1_288_834_974_657
CLOCK_SKEW = timedelta(minutes=2)


def snowflake_time(post_id: str) -> datetime | None:
    try:
        value = int(post_id)
    except ValueError:
        return None
    if value <= 0:
        return None
    milliseconds = (value >> 22) + SNOWFLAKE_EPOCH_MS
    try:
        return datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def normalize(post: dict[str, Any], symbol: str, start: datetime, end: datetime) -> dict[str, Any] | None:
    """Convert one search result, rejecting anything outside the queried window.

    Both layers below have been observed lying: a search for a one-hour window
    returned ten posts from the same hour *a year earlier*, and the transcription
    restated their year as the queried one. The snowflake timestamp is derived
    from the post ID itself, so it settles both questions.
    """
    post_id = str(post.get("id", "")).strip()
    handle = str(post.get("author_handle", "")).lstrip("@").strip()
    if not post_id.isdigit() or not handle:
        return None
    observed_at = snowflake_time(post_id)
    if observed_at is None:
        return None
    if not (start - CLOCK_SKEW <= observed_at <= end + CLOCK_SKEW):
        return None
    return {
        "source_post_id": post_id[:128],
        "observed_at": observed_at.isoformat(),
        "author_id": handle[:128],
        "author_name": str(post.get("author_name", ""))[:256],
        "text": str(post.get("text", ""))[:20_000],
        "public_url": f"https://x.com/{handle}/status/{post_id}"[:2_000],
        "symbols": [symbol],
        "engagement": {
            "likes": counter(post, "likes"),
            "comments": counter(post, "replies"),
            "shares": counter(post, "reposts"),
            "views": counter(post, "views"),
        },
    }


def counter(post: dict[str, Any], key: str) -> int:
    """A malformed counter must not cost the whole slice its posts."""
    try:
        return max(0, int(post.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def harvest_symbol(
    symbol: str, start: datetime, end: datetime, days: int,
    refresh_minutes: int, rescan_empty: bool,
) -> tuple[int, list[SliceResult]]:
    """Scan every outstanding slice for one symbol, persisting as it goes.

    A multi-day backfill is hundreds of searches long. Results are flushed per
    slice group so an interruption costs one slice rather than the whole run,
    and so a resumed run skips what already landed.
    """
    targets = pending_slices(symbol, start, end, days, refresh_minutes, rescan_empty)
    if not targets:
        return 0, []
    all_results: list[SliceResult] = []
    # Slices overlap after splitting, so the same post arrives repeatedly; the
    # post ID is the only trustworthy identity here.
    seen: set[str] = set()
    ingested = 0
    with ThreadPoolExecutor(max_workers=max(1, WORKERS)) as pool:
        for group in pool.map(lambda window: harvest_slice(symbol, window[0], window[1]), targets):
            all_results.extend(group)
            fresh = {}
            for result in group:
                for post_id, record in result.records.items():
                    if post_id not in seen:
                        seen.add(post_id)
                        fresh[post_id] = record
            if fresh:
                ingest(list(fresh.values()))
                ingested += len(fresh)
            report_coverage(symbol, group)
    return ingested, all_results


def report_coverage(symbol: str, results: list[SliceResult]) -> None:
    payload = [{
        "symbol": symbol,
        "slice_start": item.start.isoformat(),
        "slice_end": item.end.isoformat(),
        "query": item.query,
        "status": item.status,
        "posts_found": len(item.records),
        "saturated": item.saturated,
        "split_depth": item.depth,
        "message": item.message,
    } for item in results]
    for index in range(0, len(payload), INGEST_CHUNK):
        request_json("/api/x-coverage", payload[index:index + INGEST_CHUNK])


def ingest(posts: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"inserted": 0, "updated": 0, "rejected": 0}
    for index in range(0, len(posts), INGEST_CHUNK):
        result = request_json("/api/ingest", {
            "source": "x-grok-cli",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "posts": posts[index:index + INGEST_CHUNK],
        })
        for key in totals:
            totals[key] += result.get(key, 0)
    return totals


def collect_once(explicit: list[str] | None, args: argparse.Namespace) -> dict[str, Any]:
    symbols, offset, universe_size = selected_symbols(explicit)
    if not symbols:
        raise RuntimeError("empty universe")
    now = datetime.now(timezone.utc)
    start = now - (timedelta(days=args.backfill_days) if args.backfill_days else timedelta(minutes=LOOKBACK_MINUTES))
    per_symbol, slices_scanned, failures, empties = {}, 0, 0, 0

    for symbol in symbols:
        harvested, results = harvest_symbol(
            symbol, start, now, max(1, args.backfill_days or 7),
            args.refresh_minutes, args.rescan_empty,
        )
        slices_scanned += len(results)
        failures += sum(1 for item in results if item.status not in ("SUCCESS", "EMPTY"))
        empties += sum(1 for item in results if item.status == "EMPTY")
        per_symbol[symbol] = harvested

    return {
        "event": "x_collection_complete",
        "symbols": len(symbols),
        "posts": sum(per_symbol.values()),
        "slices_scanned": slices_scanned,
        "slice_failures": failures,
        "slices_empty": empties,
        "window_start": start.isoformat(),
        "offset": offset,
        "universe_size": universe_size,
        "per_symbol": per_symbol,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Harvest public X evidence via the Grok CLI's x_keyword_search tool")
    parser.add_argument("--interval", type=int, default=0, help="repeat every N seconds")
    parser.add_argument("--backfill-days", type=int, default=0, help="scan this many days back instead of the incremental lookback")
    parser.add_argument("--symbols", default="", help="comma-separated symbols to scan instead of the universe rotation")
    parser.add_argument("--refresh-minutes", type=int, default=120, help="always rescan slices ending within this many minutes")
    parser.add_argument("--rescan-empty", action="store_true", help="re-query slices previously recorded as EMPTY")
    args = parser.parse_args()
    explicit = [item.strip() for item in args.symbols.split(",") if item.strip()] or None

    while True:
        try:
            print(json.dumps(collect_once(explicit, args)), flush=True)
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            print(json.dumps({"event": "x_collection_error", "category": str(error)[:300]}), file=sys.stderr, flush=True)
            if not args.interval:
                return 1
        if not args.interval:
            return 0
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
