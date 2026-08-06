#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ROOT}/backend/.venv/bin/python"
UVICORN="${ROOT}/backend/.venv/bin/uvicorn"
LOG_DIR="${ROOT}/.runtime"
mkdir -p "$LOG_DIR"

if [[ ! -x "$PYTHON" ]]; then
  echo "Backend virtualenv missing. Run: cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

export SI_DATABASE_URL="${SI_DATABASE_URL:-sqlite:///${LOG_DIR}/social-intelligence-live.db}"
export SI_CORS_ORIGINS="${SI_CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}"
export SI_LANA_SSH_HOST="${SI_LANA_SSH_HOST:-contabo}"
export SI_LANA_POSTGRES_CONTAINER="${SI_LANA_POSTGRES_CONTAINER:-lana-postgres}"
export SI_CDP_URL="${SI_CDP_URL:-http://127.0.0.1:9222}"
export SI_API_URL="${SI_API_URL:-http://127.0.0.1:8000}"
export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-http://127.0.0.1:8000}"

cleanup() {
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "${COLLECTOR_PID:-}" ]] && kill "$COLLECTOR_PID" 2>/dev/null || true
  [[ -n "${X_COLLECTOR_PID:-}" ]] && kill "$X_COLLECTOR_PID" 2>/dev/null || true
  [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(cd "$ROOT/backend" && "$UVICORN" app.main:app --host 127.0.0.1 --port 8000) >"$LOG_DIR/api.log" 2>&1 & API_PID=$!
for _ in {1..30}; do curl -fsS "$SI_API_URL/health" >/dev/null && break; sleep 1; done
curl -fsS -X POST "$SI_API_URL/api/universe/sync" >"$LOG_DIR/universe-sync.json"

(cd "$ROOT/collector" && npm start) >"$LOG_DIR/collector.log" 2>&1 & COLLECTOR_PID=$!
"$PYTHON" "$ROOT/scripts/collect-x.py" --interval "${SI_X_INTERVAL_SECONDS:-900}" >"$LOG_DIR/x-collector.log" 2>&1 & X_COLLECTOR_PID=$!
(NEXT_PUBLIC_API_URL="$NEXT_PUBLIC_API_URL" npm --prefix "$ROOT/frontend" run dev -- --hostname 127.0.0.1 --port 3000) >"$LOG_DIR/frontend.log" 2>&1 & WEB_PID=$!

echo "Social Intelligence Desk running"
echo "Desk: http://127.0.0.1:3000"
echo "API:  http://127.0.0.1:8000/docs"
echo "Health: $SI_API_URL/api/source-health"
echo "Press Ctrl+C to stop all services"
wait "$API_PID" "$COLLECTOR_PID" "$WEB_PID"
