#!/usr/bin/env bash
# 1) Sync desk to Lana kanban (archive leavers, activate joiners — keep history).
# 2) Pull X Radar official counts only for active tokens still missing a baseline.
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

# Active tokens without x-radar source.
MISSING="$(
  curl -fsS "$API_URL/api/radar" | python3 -c '
import json, sys
rows = json.load(sys.stdin)
miss = [
    r["symbol"] for r in rows
    if (r.get("x_signal") or {}).get("source") != "x-radar"
]
print(",".join(miss))
'
)"

if [[ -z "$MISSING" ]]; then
  echo "[$STAMP] x-radar: all active tokens already have official baselines"
  exit 0
fi

echo "[$STAMP] x-radar missing: $MISSING"
export SI_API_URL="$API_URL"
export SI_CDP_URL="$CDP_URL"
export SI_RADAR_SYMBOLS="$MISSING"
export SI_RADAR_KEEP="${SI_RADAR_KEEP:-}"
# Explicit list already = missing set; do not re-filter against radar mid-run.
unset SI_RADAR_ONLY_MISSING || true

cd "$ROOT"
/usr/bin/node collector/x-radar-universe.mjs 2>&1 | tee -a "$LOG_DIR/x-radar-universe.log"
echo "[$STAMP] x-radar pass complete"
