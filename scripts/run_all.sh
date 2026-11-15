#!/usr/bin/env bash
# Owner: Mac owner
# Start the whole Beacon stack on the Mac: Bonsai (llama.cpp), the Helper app, then the Brain.
#
#   scripts/run_all.sh           use the newest signed app in build/ or /Applications
#   scripts/run_all.sh --debug   run helper/.build/debug/beacon-helper directly (swift build if needed)
#
# Logs go to logs/ (bonsai.log, helper.log, brain.log). Ctrl-C stops only what this script started.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
HELPER_URL="http://127.0.0.1:8765"
DEBUG=0
[[ "${1:-}" == "--debug" ]] && DEBUG=1
mkdir -p "$LOG_DIR"

BRAIN_PYTHON="$ROOT/brain/.venv/bin/python"
HAS_BRAIN=0
if [[ -f "$ROOT/brain/pyproject.toml" ]]; then
  HAS_BRAIN=1
  if [[ ! -x "$BRAIN_PYTHON" ]]; then
    echo "error: Brain environment missing. Run scripts/setup_mac.sh while online." >&2
    exit 1
  fi
fi

STARTED_BONSAI=""
STARTED_HELPER=""
STARTED_BRAIN=""

cleanup() {
  trap - INT TERM EXIT
  echo
  echo "==> Stopping what run_all started"
  for pid in $STARTED_BRAIN $STARTED_HELPER $STARTED_BONSAI; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  sleep 1
  for pid in $STARTED_BRAIN $STARTED_HELPER $STARTED_BONSAI; do
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  done
  [[ -n "$STARTED_BONSAI" ]] && rm -f "$LOG_DIR/bonsai.pid"
  echo "Stopped."
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

# 1. Bonsai -----------------------------------------------------------------
if curl -sf --max-time 2 http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then
  echo "==> Bonsai already running; reusing it"
else
  "$ROOT/scripts/start_bonsai.sh" --detach
  STARTED_BONSAI="$(cat "$LOG_DIR/bonsai.pid")"
fi

# 2. Helper -----------------------------------------------------------------
if curl -sf --max-time 2 "$HELPER_URL/health" >/dev/null 2>&1; then
  echo "==> Helper already running; reusing it"
else
  : >"$LOG_DIR/helper.log"
  if [[ "$DEBUG" == "1" ]]; then
    BIN="$ROOT/helper/.build/debug/beacon-helper"
    if [[ ! -x "$BIN" ]]; then (cd "$ROOT/helper" && swift build); fi
    echo "==> Starting debug Helper: $BIN"
    (cd "$ROOT" && BEACON_ROOT="$ROOT" exec "$BIN") >>"$LOG_DIR/helper.log" 2>&1 &
    STARTED_HELPER=$!
  else
    INSTALLED_APP="/Applications/Beacon Helper.app"
    BUILT_APP="$ROOT/build/Beacon Helper.app"
    APP="$INSTALLED_APP"
    if [[ "$BUILT_APP/Contents/MacOS/beacon-helper" -nt "$INSTALLED_APP/Contents/MacOS/beacon-helper" ]]; then
      APP="$BUILT_APP"
    fi
    if [[ ! -x "$APP/Contents/MacOS/beacon-helper" ]]; then
      echo "error: no Helper app; run scripts/build_helper.sh (or use --debug)" >&2
      exit 1
    fi
    echo "==> Opening $APP"
    # `open` makes launchd the parent so the app's own Microphone/Accessibility grants apply (not Terminal's).
    HELPER_ENV=(--env "BEACON_ROOT=$ROOT")
    if [[ -n "${BEACON_TTS:-}" ]]; then HELPER_ENV+=(--env "BEACON_TTS=$BEACON_TTS"); fi
    if [[ -n "${BEACON_STT_MODEL:-}" ]]; then HELPER_ENV+=(--env "BEACON_STT_MODEL=$BEACON_STT_MODEL"); fi
    open -n -g "${HELPER_ENV[@]}" --stdout "$LOG_DIR/helper.log" --stderr "$LOG_DIR/helper.log" "$APP"
    for _ in $(seq 1 50); do
      STARTED_HELPER="$(pgrep -nf "$APP/Contents/MacOS/beacon-helper" || true)"
      [[ -n "$STARTED_HELPER" ]] && break
      sleep 0.1
    done
  fi
  echo -n "    waiting for $HELPER_URL/health "
  for i in $(seq 1 60); do
    if curl -sf --max-time 1 "$HELPER_URL/health" >/dev/null 2>&1; then break; fi
    if [[ -n "$STARTED_HELPER" ]] && ! kill -0 "$STARTED_HELPER" 2>/dev/null; then
      echo; echo "error: Helper exited; see $LOG_DIR/helper.log" >&2; tail -20 "$LOG_DIR/helper.log" >&2; exit 1
    fi
    if (( i == 60 )); then echo; echo "error: Helper /health not ready after 30 s" >&2; exit 1; fi
    echo -n "."; sleep 0.5
  done
  echo " ok"
fi
curl -s "$HELPER_URL/health" | (command -v jq >/dev/null && jq -c . || cat)

# 3. Brain ------------------------------------------------------------------
if (( HAS_BRAIN )); then
  echo "==> Starting Brain (log: $LOG_DIR/brain.log)"
  (cd "$ROOT/brain" && exec "$BRAIN_PYTHON" -m beacon_brain) >"$LOG_DIR/brain.log" 2>&1 &
  STARTED_BRAIN=$!
else
  echo "==> The Brain isn't in this checkout yet (no brain/); running Bonsai + Helper only."
fi

echo
echo "Beacon is running. Logs: $LOG_DIR  (tail -f $LOG_DIR/session.jsonl for latency). Ctrl-C to stop."
# Stay up until Ctrl-C, or until something we started dies.
while true; do
  for pid in $STARTED_BONSAI $STARTED_HELPER $STARTED_BRAIN; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "warning: process $pid exited; check $LOG_DIR" >&2
      exit 1
    fi
  done
  sleep 2
done
