#!/usr/bin/env bash
# 1) Sync desk to Lana kanban (archive leavers, activate joiners — keep history).
# 2) Pull X Radar official counts for active tokens still missing *today's* UTC day.
#    Historical baselines alone do not count — after midnight every row would
#    otherwise show TODAY=0 until a full re-pull.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="${SI_API_URL:-http://127.0.0.1:8100}"
CDP_URL="${SI_CDP_URL:-http://127.0.0.1:9222}"
LOG_DIR="${SI_LOG_DIR:-/var/log/social-intelligence}"
mkdir -p "$LOG_DIR" 2>/dev/null || LOG_DIR="/tmp"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

echo "[$STAMP] universe sync → $API_URL"
SYNC_JSON="$(curl -fsS -X POST "$API_URL/api/universe/sync")"
echo "$SYNC_JSON" | tee -a "$LOG_DIR/universe-sync.log" >/dev/null
echo "$SYNC_JSON"

# Active tokens lacking an official x-radar count for the current UTC day.
MISSING="$(
  curl -fsS "$API_URL/api/radar" | python3 -c '
import json, sys
from datetime import datetime, timezone
rows = json.load(sys.stdin)
today = datetime.now(timezone.utc).date().isoformat()
miss = []
for r in rows:
    hist = (r.get("x_signal") or {}).get("history") or []
    day = next((h for h in hist if h.get("date") == today), None)
    # expected_posts is null when we fell back to harvest; 0 is a valid quiet day.
    if day is None or day.get("posts_source") != "x-radar" or day.get("expected_posts") is None:
        miss.append(r["symbol"])
print(",".join(miss))
'
)"

if [[ -z "$MISSING" ]]; then
  echo "[$STAMP] x-radar: all active tokens already have today's official baseline"
  exit 0
fi

echo "[$STAMP] x-radar missing today: $MISSING"
export SI_API_URL="$API_URL"
export SI_CDP_URL="$CDP_URL"
export SI_RADAR_SYMBOLS="$MISSING"
export SI_RADAR_KEEP="${SI_RADAR_KEEP:-}"
# Explicit list already = missing set; do not re-filter against radar mid-run.
unset SI_RADAR_ONLY_MISSING || true
unset SI_RADAR_STALE_HOURS || true

cd "$ROOT"
# Serialized via scripts/run-x-radar-universe.sh (shared flock with hourly timer).
/bin/bash "$ROOT/scripts/run-x-radar-universe.sh" 2>&1 | tee -a "$LOG_DIR/x-radar-universe.log"
echo "[$STAMP] x-radar pass complete"
