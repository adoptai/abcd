#!/bin/bash
# =============================================================================
# AdoptAI Chrome Extension Test Browser
# =============================================================================
# Run this from YOUR terminal (not from Claude's sandbox).
# Launches Chrome with the Adopt extension in an isolated profile with
# remote debugging enabled so Claude can connect via CDP.
#
# Usage: ./start.sh [target-url]
# =============================================================================

# --- CONFIGURE THESE ---
EXTENSION_PATH="${ADOPT_EXTENSION_PATH:-/home/gabriel/Documents/adopt/adoptce/dist}"
TARGET_URL="${1:-https://www.google.com/travel/flights}"
APP_URL="https://app.adopt.ai"
DEBUG_PORT=9222
# --- END CONFIG ---

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)/user-profile"

if [ ! -f "$EXTENSION_PATH/manifest.json" ]; then
  echo "ERROR: Extension not found at $EXTENSION_PATH"
  echo "       Expected manifest.json in that directory."
  echo "       Set ADOPT_EXTENSION_PATH env var to override."
  exit 1
fi

mkdir -p "$PROFILE_DIR"

echo "=== AdoptAI Extension Test Browser ==="
echo "Extension: $EXTENSION_PATH"
echo "Profile:   $PROFILE_DIR  (isolated from your daily Chrome)"
echo "Debug:     http://localhost:$DEBUG_PORT"
echo "Auth:      $APP_URL"
echo "Target:    $TARGET_URL"
echo ""
echo "Close the browser window to stop (don't Ctrl+C — Chrome needs a clean exit to save state)."
echo "======================================="

# Trap SIGINT/SIGTERM so Chrome gets a clean shutdown signal instead of being killed
CHROME_PID=""
cleanup() {
  if [ -n "$CHROME_PID" ] && kill -0 "$CHROME_PID" 2>/dev/null; then
    echo ""
    echo "Shutting down Chrome gracefully..."
    kill -TERM "$CHROME_PID" 2>/dev/null
    wait "$CHROME_PID" 2>/dev/null
  fi
  exit 0
}
trap cleanup SIGINT SIGTERM

google-chrome \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --enable-extensions \
  --load-extension="$EXTENSION_PATH" \
  --remote-debugging-port=$DEBUG_PORT \
  --new-window \
  "$APP_URL" "$TARGET_URL" &

CHROME_PID=$!

# Run boot sequencer in background: waits for app.adopt.ai, then opens extension
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
node "$SCRIPT_DIR/boot.mjs" "$TARGET_URL" &

wait $CHROME_PID
