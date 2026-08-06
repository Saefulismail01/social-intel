#!/usr/bin/env bash
set -euo pipefail

PROFILE="${SI_CHROME_PROFILE:-/opt/social-intelligence/chrome-profile}"
DISPLAY_NUMBER="${DISPLAY:-:99}"
mkdir -p "$PROFILE"
chmod 700 "$PROFILE"

if ! pgrep -f "Xvfb ${DISPLAY_NUMBER}" >/dev/null; then
  Xvfb "$DISPLAY_NUMBER" -screen 0 1440x900x24 -nolisten tcp &
  sleep 2
fi

export DISPLAY="$DISPLAY_NUMBER"
exec /snap/bin/chromium \
  --no-sandbox \
  --disable-dev-shm-usage \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE" \
  --window-size=1440,900 \
  "https://www.binance.com/en/square"
