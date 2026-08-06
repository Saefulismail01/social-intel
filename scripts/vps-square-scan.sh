#!/usr/bin/env bash
set -euo pipefail
exec 9>/run/social-square-scan.lock
flock -n 9 || exit 0
cd /opt/social-intelligence/collector
export SI_API_URL=http://127.0.0.1:8100
# Square-only browser (SG SOCKS); do not use :9222 (X session).
export SI_CDP_URL=http://127.0.0.1:9223
export SI_SQUARE_SEARCH_DAYS=7
export SI_SQUARE_SEARCH_PAGES=3
export SI_SQUARE_SEARCH_WAIT_MS=2500
exec /usr/bin/npm run search
