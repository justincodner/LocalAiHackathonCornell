#!/usr/bin/env bash
# Owner: Mac owner
# Start PrismML's llama.cpp server (Bonsai-demo) for Beacon, loopback only, thinking off (spec 6.2).
#
#   scripts/start_bonsai.sh            start, wait until ready, stay in the foreground (Ctrl-C stops it)
#   scripts/start_bonsai.sh --detach   start, wait until ready, return (pid in logs/bonsai.pid)
#
# Env: BONSAI_MODEL (2B if installed, otherwise 4B), BONSAI_SLOTS (7), BONSAI_CTX (total, 6144 x slots),
# BONSAI_DIR (../Bonsai-demo), BEACON_VISION=1 keeps the vision projector loaded (P2 only; it costs ~0.4-0.6 GB). BONSAI_HOST is never set: the server binds 127.0.0.1.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BONSAI_DIR="${BONSAI_DIR:-$(cd "$ROOT/.." && pwd)/Bonsai-demo}"
LOG_DIR="$ROOT/logs"
LOG="$LOG_DIR/bonsai.log"
PID_FILE="$LOG_DIR/bonsai.pid"
URL="http://127.0.0.1:8080/v1/models"
DETACH=0
[[ "${1:-}" == "--detach" ]] && DETACH=1

mkdir -p "$LOG_DIR"
unset BONSAI_HOST

if curl -sf --max-time 2 "$URL" >/dev/null 2>&1; then
  echo "Bonsai is already running on 127.0.0.1:8080; leaving it alone."
  exit 0
fi
if [[ ! -x "$BONSAI_DIR/scripts/start_llama_server.sh" ]]; then
  echo "error: $BONSAI_DIR not set up; run scripts/setup_mac.sh first" >&2
  exit 1
fi

MODEL_SIZE="${BONSAI_MODEL:-}"
if [[ -z "$MODEL_SIZE" ]]; then
  MODEL_SIZE=4B
  if [[ -f "$BONSAI_DIR/models/bonsai2-gguf/2B/Ternary-Bonsai-2-2B-PQ2_0.gguf" ]]; then
    MODEL_SIZE=2B
  fi
fi
# Bonsai-demo auto-loads the vision projector when its file exists, and --no-mmproj does not override that.
# Without BEACON_VISION=1, point BONSAI_GGUF at the language model file so no projector is loaded.
GGUF_OVERRIDE=""
if [[ "${BEACON_VISION:-0}" != "1" && -z "${BONSAI_GGUF:-}" ]]; then
  for f in "$BONSAI_DIR/models/bonsai2-gguf/$MODEL_SIZE"/*PQ2_0.gguf; do
    [[ -f "$f" && "$f" != *mmproj* ]] && GGUF_OVERRIDE="$f" && break
  done
fi

# One slot per prompt family: the Brain prewarms six (loop.prompt_families) and pins each to its own
# slot (llm.py, id_slot). Bonsai 2 is a hybrid model, so families sharing a slot lost their cached
# prefixes (docs/bench.md). Separate KV per slot; the RAM prompt cache is no longer needed.
SLOTS="${BONSAI_SLOTS:-7}"
CTX="${BONSAI_CTX:-$((6144 * SLOTS))}"
echo "==> Starting Bonsai $MODEL_SIZE (vision: ${BEACON_VISION:-0}, ctx $CTX across $SLOTS slots, thinking off); log: $LOG"
(
  cd "$BONSAI_DIR"
  if [[ -n "$GGUF_OVERRIDE" ]]; then export BONSAI_GGUF="$GGUF_OVERRIDE"; unset BONSAI_MMPROJ; fi
  BONSAI_MODEL="$MODEL_SIZE" BONSAI_THINKING=0 BONSAI_CTX="$CTX" \
    exec ./scripts/start_llama_server.sh -np "$SLOTS" --no-kv-unified --cache-ram 0
) >"$LOG" 2>&1 &
PID=$!
echo "$PID" >"$PID_FILE"

stop() {
  if kill -0 "$PID" 2>/dev/null; then
    echo "==> Stopping Bonsai (pid $PID)"
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
}

started=$(date +%s)
until curl -sf --max-time 2 "$URL" >/dev/null 2>&1; do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "error: Bonsai exited during startup; last lines of $LOG:" >&2
    tail -20 "$LOG" >&2
    rm -f "$PID_FILE"
    exit 1
  fi
  if (( $(date +%s) - started > 180 )); then
    echo "error: Bonsai not ready after 180 s; see $LOG" >&2
    stop
    exit 1
  fi
  sleep 1
done
MODEL_ID="$(curl -s "$URL" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null || echo '?')"
echo "==> Bonsai ready in $(( $(date +%s) - started )) s: model '$MODEL_ID' at http://127.0.0.1:8080/v1 (pid $PID)"

if [[ "$DETACH" == "1" ]]; then exit 0; fi
trap 'stop; exit 0' INT TERM
wait "$PID"
