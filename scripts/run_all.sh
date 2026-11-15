#!/usr/bin/env bash
# Owner: Mac owner
# Start the whole Beacon stack on the Mac: Bonsai (llama.cpp), the Helper app, then the Brain.
#
#   scripts/run_all.sh           use the newest signed app in build/ or /Applications
#   scripts/run_all.sh --debug   run helper/.build/debug/beacon-helper directly (swift build if needed)
#
# The HUD's ⋯ menu ("Use 4B model for questions") starts and stops the 4B at runtime, with no restart.
# BEACON_DUAL_MODEL=1 also starts Bonsai 4B on 127.0.0.1:8081 (ctx BEACON_QUESTION_CTX, default 8192) and sends
# questions to it while actions stay on the 2B. Off by default: on an 8 GB Mac both models together swap, and
# measured latency got worse for both (2B actions ~5.5 s -> 7-40 s, 4B questions 17-46 s).
#
# Logs go to logs/ (bonsai.log, helper.log, brain.log). Ctrl-C stops only what this script started.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
HELPER_URL="http://127.0.0.1:8765"
DEBUG=0
[[ "${1:-}" == "--debug" ]] && DEBUG=1
mkdir -p "$LOG_DIR"

# models/ is git-ignored, so a git worktree has none: use the main checkout's (see models_dir.sh).
# shellcheck source=scripts/models_dir.sh
source "$ROOT/scripts/models_dir.sh"
if [[ -z "${BEACON_MODELS_DIR:-}" ]]; then
  SHARED_MODELS="$(models_dir_for "$ROOT")"
  if [[ -n "$SHARED_MODELS" ]]; then
    export BEACON_MODELS_DIR="$SHARED_MODELS"
    echo "==> Worktree has no models/; using $BEACON_MODELS_DIR"
  fi
fi

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
STARTED_QUESTION_BONSAI=""
STARTED_HELPER=""
STARTED_BRAIN=""

cleanup() {
  trap - INT TERM EXIT
  echo
  echo "==> Stopping what run_all started"
  for pid in $STARTED_BRAIN $STARTED_HELPER $STARTED_BONSAI $STARTED_QUESTION_BONSAI; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  sleep 1
  for pid in $STARTED_BRAIN $STARTED_HELPER $STARTED_BONSAI $STARTED_QUESTION_BONSAI; do
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  done
  [[ -n "$STARTED_BONSAI" ]] && rm -f "$LOG_DIR/bonsai.pid"
  # The 4B can also be started from the HUD's ⋯ menu (by the Brain): stop it too.
  if [[ -f "$LOG_DIR/bonsai-8081.pid" ]]; then
    kill "$(cat "$LOG_DIR/bonsai-8081.pid")" 2>/dev/null || true
    rm -f "$LOG_DIR/bonsai-8081.pid"
  fi
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
if [[ "${BEACON_DUAL_MODEL:-0}" == "1" ]]; then
  # Questions go to the 4B, actions stay on the 2B (brain config: BEACON_LLM_QUESTION_URL).
  if curl -sf --max-time 2 http://127.0.0.1:8081/v1/models >/dev/null 2>&1; then
    echo "==> Question model already running on 8081; reusing it"
  else
    BONSAI_MODEL=4B BONSAI_PORT=8081 BONSAI_CTX="${BEACON_QUESTION_CTX:-8192}" "$ROOT/scripts/start_bonsai.sh" --detach
    STARTED_QUESTION_BONSAI="$(cat "$LOG_DIR/bonsai-8081.pid")"
  fi
  export BEACON_LLM_QUESTION_URL="http://127.0.0.1:8081/v1"
  echo "==> Questions -> Bonsai 4B (8081), actions -> Bonsai 2B (8080). Watch swap: sysctl vm.swapusage"
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
    if [[ -n "${BEACON_MODELS_DIR:-}" ]]; then HELPER_ENV+=(--env "BEACON_MODELS_DIR=$BEACON_MODELS_DIR"); fi
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
# Stay up until Ctrl-C, or until Bonsai or the Brain dies. The Helper quitting (the HUD's Turn off button) leaves
# Bonsai and the Brain running, so reopening the Helper app brings Beacon straight back.
while true; do
  if [[ -n "$STARTED_HELPER" ]] && ! kill -0 "$STARTED_HELPER" 2>/dev/null; then
    echo "==> Helper turned off; Bonsai and the Brain stay up. Reopen it with: open \"${APP:-$ROOT/build/Beacon Helper.app}\" (Ctrl-C here stops everything)"
    STARTED_HELPER=""
  fi
  # The 4B is optional: the HUD's ⋯ menu can turn it off, so it is not watched here.
  for pid in $STARTED_BONSAI $STARTED_BRAIN; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "warning: process $pid exited; check $LOG_DIR" >&2
      exit 1
    fi
  done
  sleep 2
done
