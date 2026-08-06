#!/usr/bin/env bash
# Serialize X Radar GraphQL pulls. Concurrent runs 429 each other and corrupt
# the 5-slot rotation. Callers (hourly timer + universe-sync) share this lock.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="${SI_RADAR_LOCK:-/var/lock/social-x-radar.lock}"

mkdir -p "$(dirname "$LOCK")" 2>/dev/null || true
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "{\"event\":\"x_radar_skipped\",\"reason\":\"lock_held\",\"lock\":\"$LOCK\"}"
  exit 0
fi

cd "$ROOT"
exec /usr/bin/node collector/x-radar-universe.mjs "$@"
