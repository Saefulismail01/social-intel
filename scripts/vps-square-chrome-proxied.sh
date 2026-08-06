#!/usr/bin/env bash
set -euo pipefail
PROFILE="${SI_SQUARE_CHROME_PROFILE:-/opt/social-intelligence/chrome-profile-square}"
DISPLAY_NUMBER="${SI_SQUARE_DISPLAY:-:98}"
CDP_PORT="${SI_SQUARE_CDP_PORT:-9223}"
PROXY="${SI_SQUARE_PROXY:-socks5://172.17.0.1:1080}"
CHROME="${SI_SQUARE_CHROME_BIN:-/root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome}"
mkdir -p "$PROFILE"
chmod 700 "$PROFILE"
if ! pgrep -a Xvfb | grep -q " ${DISPLAY_NUMBER} "; then
  Xvfb "$DISPLAY_NUMBER" -screen 0 1440x900x24 -nolisten tcp &
  sleep 2
fi
export DISPLAY="$DISPLAY_NUMBER"
exec "$CHROME" \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$CDP_PORT" \
  --user-data-dir="$PROFILE" \
  --proxy-server="$PROXY" \
  --window-size=1440,900 \
  --password-store=basic \
  --no-first-run \
  --no-default-browser-check \
  "https://www.binance.com/en/square"
