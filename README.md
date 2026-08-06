# Social Intelligence Desk

Crowd-intelligence companion for Lana-Migration. Lana chooses the market universe; this desk measures attention, breadth, authenticity, coordination, and crowd lifecycle.

## Current MVP

- Read-only Lana universe adapter: local fixture by default, optional SSH mode
- Binance Square JSON fixture/import endpoint
- Explainable Attention, Breadth, Authenticity, Coordination, and Data Confidence scores
- Crowd states: DORMANT, SEEDING, EMERGING, BROADENING
- FastAPI radar and token-detail endpoints
- Next.js terminal-style Crowd Radar
- PostgreSQL Docker deployment; SQLite-supported tests

No trading orders, publishing, impersonation, or platform-access bypasses are included.

## Run with Docker

```bash
docker compose up --build
```

Open:

- Desk: http://localhost:3000
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

Load the initial fixtures:

```bash
curl -X POST http://localhost:8000/api/fixtures/load
```

The supplied social-post fixture is intentionally empty. Import authorized observations through `POST /api/ingest`, then run `POST /api/recompute/{symbol}`.

## Local backend tests

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=. .venv/bin/pytest -q
```

## Lana universe sync

The desk mirrors **Lana's crime kanban only** — phases `IGNITION`, `SQUEEZE`,
`EXHAUSTION`, and `DUMP`. Tokens that leave those columns are **archived**
(`active=0`): X Radar baselines, posts, and coverage stay stored; they only
disappear from the live radar list. Returning to the kanban reactivates the row
and reuses prior history.

```bash
curl -X POST http://localhost:8000/api/universe/sync
# → { "synced": N, "activated": [...], "archived": [...], "mode": "lana_db" }
```

**Automation (VPS):**
- every **15 min** — `social-universe-sync.timer` runs
  `scripts/vps-universe-and-radar.sh` (sync kanban + X Radar for any *new*
  active coins still missing official counts)
- every **1 h** — `social-x-radar.timer` safety net (`SI_RADAR_ONLY_MISSING=1`)

Production uses TCP to Lana Postgres (`SI_LANA_DATABASE_URL` on `lana_lana_net`).
Fixture mode remains the safe local default when that URL is unset.

## Passive Binance Square collector

The collector connects to an already-open, user-authorized Chrome remote-debugging session. It observes only successful responses for the approved Square feed pathname and never reads or forwards request headers, cookies, CSRF values, or browser tokens.

Start Chrome with a dedicated profile:

```bash
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.chrome-social-intelligence"
```

Open Binance Square manually, then run:

```bash
cd collector
npm install
SI_CDP_URL=http://127.0.0.1:9222 \
SI_API_URL=http://127.0.0.1:8000 \
npm start
```

The collector is passive: reload or scroll Square manually. It does not post, like, follow, bypass access controls, or replay Binance requests. Stop it with `Ctrl+C` when collection is complete.

Check ingestion health:

```bash
curl http://127.0.0.1:8000/api/source-health
```

## X collector with local Grok CLI

The X adapter drives the Grok CLI's native `x_keyword_search` tool, which queries X's own search index and supports advanced operators (`since:`, `until:`, `min_faves:`). Results are imported as `x-grok-cli`. X evidence is displayed separately and does not alter the Binance Square crowd score.

### Why it scans in time slices

`x_keyword_search` returns **at most 10 posts per call, and only the newest ones inside the queried window**. One query per token therefore sees a sliver of a busy day. Measured against `$TAG` on 2026-08-01:

| query window | posts returned | span actually covered |
|---|---|---|
| whole day | 7 | 20:41–23:43 only |
| 00:00–06:00 | 3 | none of the above |
| 12:00–15:00 | 5 | none of the above |
| 01:00–02:00 | 2 | missed by the 6h slice |

So the collector walks hourly slices and merges them, splitting any slice that comes back capped at 10 until it stops capping. Every scanned slice is recorded via `/api/x-coverage`, which makes runs resumable and keeps *unscanned* hours distinguishable from *quiet* hours.

### Timestamps are verified, not trusted

A search for one hour has been observed returning ten posts from the same hour **a year earlier**, with the year restated as the requested one. X post IDs are snowflakes that encode creation time, so `observed_at` is derived from the ID, posts outside the queried window are dropped, and `/api/ingest` independently rejects any `x-grok-cli` post whose timestamp contradicts its ID.

### Running it

Incremental scan of the recent window (what the service does):

```bash
python3 scripts/collect-x.py --interval 900 --refresh-minutes 120
```

Backfill a token's full week — the only way to populate history for a newly tracked symbol:

```bash
python3 scripts/collect-x.py --symbols TAG --backfill-days 7
```

Re-query slices that previously came back empty, after a flaky search window:

```bash
python3 scripts/collect-x.py --symbols TAG --backfill-days 7 --rescan-empty
```

A symbol must be tracked before any of its posts can be stored — ingestion drops untracked symbols:

```bash
curl -X POST http://127.0.0.1:8100/api/universe/track \
  -H 'content-type: application/json' -d '{"symbol":"TAG","priority":0}'
```

Tuning: `SI_X_MAX_SYMBOLS` (rotation batch), `SI_X_LOOKBACK_MINUTES` (incremental window), `SI_X_SLICE_MINUTES` / `SI_X_MIN_SLICE_MINUTES` (slice granularity and split floor), `SI_X_WORKERS`, `SI_X_RETRIES`, `SI_X_MIN_CALL_GAP_SECONDS` (the search returns spurious empties when called in bursts).

### Reading the radar honestly

`x_signal.posts` is a **7-day** count, alongside `posts_1h` for the live hour; the two are no longer conflated. `x_signal.coverage` reports `scanned_hours / window_hours`, and `history[].scanned_hours` does the same per day — a day showing `0 posts` with `0 scanned_hours` has not been looked at, and must not be read as silence.

## Import format

```json
{
  "source": "authorized-import",
  "posts": [
    {
      "id": "square-123",
      "symbol": "BULLA",
      "author_id": "account-42",
      "text": "$BULLA discussion",
      "engagement": 12,
      "account_age_days": 180,
      "observed_at": "2026-08-04T10:00:00Z"
    }
  ]
}
```

See `social-intelligence-desk.md` for the complete design and roadmap.
