#!/usr/bin/env bash
# Owner: Mac owner
# Helper contract tests (spec 14.1): curl every endpoint of spec 4.5 against a running Helper and check
# `ok` plus the documented fields; validate snapshots and contracts/fixtures/*.json against
# contracts/snapshot.schema.json.
#
#   scripts/contract_test.sh                 safe endpoints only (nothing clicks or types)
#   scripts/contract_test.sh --live          also /ax/act, /input/*, /app/open (opens TextEdit, types, scrolls)
#   scripts/contract_test.sh --allow-errors  count a well-formed {"ok":false,"error":{...}} as a pass
#                                            (useful while service modules are still stubs)
#   scripts/contract_test.sh --fixtures-only validate fixtures against the schema; no Helper needed
#
# Env: BEACON_HELPER_URL (http://127.0.0.1:8765), BEACON_TOKEN (sent as X-Beacon-Token when set).
# Note: /debug/utterance emits a real stt.final event; a running Brain will act on it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL="${BEACON_HELPER_URL:-http://127.0.0.1:8765}"
SCHEMA="$ROOT/contracts/snapshot.schema.json"
LIVE=0; ALLOW_ERRORS=0; FIXTURES_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --live) LIVE=1 ;;
    --allow-errors) ALLOW_ERRORS=1 ;;
    --fixtures-only) FIXTURES_ONLY=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v jq >/dev/null 2>&1; then
  echo "jq not found; installing with brew"
  brew install jq
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0; SKIP=0
FAILED=()

if [[ -t 1 ]]; then G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; NC=$'\e[0m'; else G=""; R=""; Y=""; NC=""; fi
pass() { PASS=$((PASS+1)); printf '%sPASS%s  %-34s %s\n' "$G" "$NC" "$1" "${2:-}"; }
fail() { FAIL=$((FAIL+1)); FAILED+=("$1"); printf '%sFAIL%s  %-34s %s\n' "$R" "$NC" "$1" "${2:-}"; }
skip() { SKIP=$((SKIP+1)); printf '%sSKIP%s  %-34s %s\n' "$Y" "$NC" "$1" "${2:-}"; }

# ---------------------------------------------------------------------------------------------
# Schema validation (python jsonschema through uv; falls back to uvx check-jsonschema)
cat >"$TMP/validate.py" <<'PY'
import json, sys
from jsonschema import Draft202012Validator
schema = json.load(open(sys.argv[1]))
mode = sys.argv[2]            # "snapshot" or "elements"
if mode == "elements":
    schema = {"$schema": schema.get("$schema"), "type": "array",
              "items": {"$ref": "#/$defs/element"}, "$defs": schema.get("$defs", {})}
v = Draft202012Validator(schema)
bad = 0
for path in sys.argv[3:]:
    doc = json.load(open(path))
    if mode == "elements":
        doc = doc.get("matches", [])
    errs = sorted(v.iter_errors(doc), key=lambda e: list(e.path))
    if errs:
        bad += 1
        print(f"{path}: {len(errs)} error(s)")
        for e in errs[:5]:
            print(f"    at /{'/'.join(map(str, e.path))}: {e.message[:200]}")
sys.exit(1 if bad else 0)
PY

validate() {  # validate <snapshot|elements> <files...>; prints errors, returns nonzero on failure
  local mode="$1"; shift
  if [[ ! -f "$SCHEMA" ]]; then return 0; fi
  if command -v uv >/dev/null 2>&1; then
    uv run --quiet --no-project --with jsonschema python -I "$TMP/validate.py" "$SCHEMA" "$mode" "$@"
  elif command -v uvx >/dev/null 2>&1 && [[ "$mode" == "snapshot" ]]; then
    uvx check-jsonschema --schemafile "$SCHEMA" "$@"
  else
    echo "no uv; cannot validate against the schema" >&2; return 1
  fi
}

check_fixtures() {
  shopt -s nullglob
  local files=("$ROOT"/contracts/fixtures/*.json)
  shopt -u nullglob
  if [[ ! -f "$SCHEMA" ]]; then skip "fixtures vs schema" "contracts/snapshot.schema.json missing"; return; fi
  if (( ${#files[@]} == 0 )); then skip "fixtures vs schema" "no files in contracts/fixtures/"; return; fi
  local f out
  for f in "${files[@]}"; do
    if out="$(validate snapshot "$f" 2>&1)"; then
      pass "fixture $(basename "$f")" "valid"
    else
      fail "fixture $(basename "$f")" ""; echo "$out" | sed 's/^/      /'
    fi
  done
}

if [[ "$FIXTURES_ONLY" == "1" ]]; then
  check_fixtures
  echo; echo "Summary: $PASS passed, $FAIL failed, $SKIP skipped"
  (( FAIL == 0 ))
  exit
fi

# ---------------------------------------------------------------------------------------------
# HTTP helpers
HDRS=(-H "Content-Type: application/json")
[[ -n "${BEACON_TOKEN:-}" ]] && HDRS+=(-H "X-Beacon-Token: $BEACON_TOKEN")

N=0
call() {  # call METHOD PATH [BODY] -> sets RESP to the response file path (no subshell, so N persists)
  local method="$1" path="$2" body="${3:-}"
  [[ -z "$body" ]] && body='{}'
  N=$((N+1))
  local out="$TMP/r$N.json"
  local code
  if [[ "$method" == "GET" ]]; then
    code="$(curl -s -o "$out" -w '%{http_code}' --max-time 15 "${HDRS[@]}" "$URL$path" || echo 000)"
  else
    code="$(curl -s -o "$out" -w '%{http_code}' --max-time 15 "${HDRS[@]}" -X POST "$URL$path" --data "$body" || echo 000)"
  fi
  echo "$code" >"$out.code"
  RESP="$out"
}
RESP=""

ERROR_CODES='["stale_snapshot","element_not_found","action_unsupported","action_failed","permission_denied","timeout","app_not_found","bad_request"]'
ERR_SHAPE='(.ok == false) and (.error|type=="object") and (.error.code|type=="string") and (.error.message|type=="string") and ([.error.code]|inside('"$ERROR_CODES"'))'

# expect NAME METHOD PATH BODY JQ_SUCCESS_FIELDS  -> PASS when ok:true and fields match
expect() {
  local name="$1" method="$2" path="$3" body="$4" fields="$5"
  call "$method" "$path" "$body"; local out="$RESP"
  local code; code="$(cat "$out.code")"
  if [[ "$code" != "200" ]]; then fail "$name" "HTTP $code (contract: always 200)"; return 1; fi
  if ! jq -e 'type=="object" and (.ok|type=="boolean")' "$out" >/dev/null 2>&1; then
    fail "$name" "no boolean 'ok': $(head -c 160 "$out")"; return 1
  fi
  if jq -e '.ok == true' "$out" >/dev/null; then
    if jq -e "$fields" "$out" >/dev/null 2>&1; then pass "$name"; return 0; fi
    fail "$name" "missing/wrong fields: $(head -c 200 "$out")"; return 1
  fi
  if jq -e "$ERR_SHAPE" "$out" >/dev/null 2>&1; then
    local ec; ec="$(jq -r '.error.code + ": " + .error.message' "$out" | head -c 140)"
    if [[ "$ALLOW_ERRORS" == "1" ]]; then pass "$name" "(error allowed) $ec"; else fail "$name" "ok:false $ec"; fi
    return 1
  fi
  fail "$name" "ok:false with malformed error: $(head -c 200 "$out")"; return 1
}

# expect_error NAME METHOD PATH BODY CODE_REGEX -> PASS when a well-formed error with a matching code
expect_error() {
  local name="$1" method="$2" path="$3" body="$4" want="$5"
  call "$method" "$path" "$body"; local out="$RESP"
  if jq -e "$ERR_SHAPE and (.error.code|test(\"^($want)\$\"))" "$out" >/dev/null 2>&1; then
    pass "$name" "-> $(jq -r .error.code "$out")"
  else
    fail "$name" "wanted error $want, got: $(head -c 200 "$out")"
  fi
}

LAST() { echo "$RESP"; }

# ---------------------------------------------------------------------------------------------
echo "Beacon Helper contract tests against $URL (live: $LIVE)"
if ! curl -sf --max-time 3 "${HDRS[@]}" "$URL/health" >/dev/null 2>&1; then
  echo "${R}Helper is not reachable at $URL/health${NC}; start it (scripts/run_all.sh) first." >&2
  check_fixtures
  exit 1
fi

CV="$(tr -dc '0-9' <"$ROOT/contracts/VERSION" 2>/dev/null || echo 1)"
expect "GET /health" GET /health "" '
  (.version|type=="string") and (.contract_version == '"${CV:-1}"') and
  (.permissions|type=="object") and
  ([.permissions.accessibility, .permissions.microphone, .permissions.screen_recording]|all(type=="boolean")) and
  (.stt_model|type=="string") and (.tts_engine|IN("ttskit","system")) and (.online|type=="boolean")' || true
jq -c '{online, permissions, stt_model, tts_engine}' "$(LAST)" 2>/dev/null | sed 's/^/      /' || true

# WebSocket upgrade
ws_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "${HDRS[@]}" \
  -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" "$URL/events" 2>/dev/null || true)"
if [[ "$ws_code" == "101" ]]; then pass "GET /events (WebSocket)" "101"; else fail "GET /events (WebSocket)" "HTTP $ws_code, wanted 101"; fi

# Snapshot + schema
SNAP_ID=""; FIRST_EL=""
if expect "POST /ax/snapshot" POST /ax/snapshot '{"scope":"window","max_elements":250}' '
    (.snapshot_id|type=="string") and (.app|type=="string") and (.window|type=="string") and
    (.truncated|type=="boolean") and (.total_elements|type=="number") and (.build_ms|type=="number") and
    (.elements|type=="array") and (.text|type=="string")'; then
  snap="$(LAST)"
  SNAP_ID="$(jq -r .snapshot_id "$snap")"; FIRST_EL="$(jq -r '.elements[0].id // empty' "$snap")"
  echo "      $(jq -r '"\(.app) / \(.window): \(.elements|length) elements, \(.build_ms) ms"' "$snap")"
  if [[ -f "$SCHEMA" ]]; then
    if out="$(validate snapshot "$snap" 2>&1)"; then pass "snapshot vs schema"; else fail "snapshot vs schema"; echo "$out" | sed 's/^/      /'; fi
    bms="$(jq -r .build_ms "$snap")"
    if (( bms <= 300 )); then pass "snapshot build_ms <= 300" "$bms ms"; else fail "snapshot build_ms <= 300" "$bms ms"; fi
  else
    skip "snapshot vs schema" "no contracts/snapshot.schema.json"
  fi
fi
expect_error "POST /ax/snapshot bad scope" POST /ax/snapshot '{"scope":"nope"}' 'bad_request'

if expect "POST /ax/find" POST /ax/find '{"query":"a","limit":5}' '(.matches|type=="array")'; then
  if [[ -f "$SCHEMA" ]]; then
    if out="$(validate elements "$(LAST)" 2>&1)"; then pass "find matches vs schema"; else fail "find matches vs schema"; echo "$out" | sed 's/^/      /'; fi
  fi
fi
expect_error "POST /ax/find no query" POST /ax/find '{}' 'bad_request'

if [[ -n "$SNAP_ID" && -n "$FIRST_EL" ]]; then
  expect "POST /ax/read" POST /ax/read "{\"snapshot_id\":\"$SNAP_ID\",\"element_id\":$FIRST_EL}" '(.text|type=="string")' || true
else
  skip "POST /ax/read" "no snapshot element to read"
fi
expect_error "POST /ax/read stale id" POST /ax/read '{"snapshot_id":"s_9999999","element_id":1}' 'stale_snapshot|element_not_found'

# Validation errors only: these never reach the screen.
expect_error "POST /ax/act no args" POST /ax/act '{}' 'bad_request'
expect_error "POST /ax/act bad action" POST /ax/act '{"snapshot_id":"s_0001","element_id":1,"action":"explode"}' 'bad_request'
expect_error "POST /input/key no keys" POST /input/key '{}' 'bad_request'
expect_error "POST /input/scroll bad dir" POST /input/scroll '{"direction":"sideways"}' 'bad_request'

expect "GET /app/list" GET /app/list "" '(.running|type=="array") and (.installed|type=="array")' || true

expect "POST /tts/speak" POST /tts/speak '{"text":"Beacon contract test.","priority":"normal","rate":1.0}' '(.speech_id|type=="string")' || true
expect_error "POST /tts/speak bad priority" POST /tts/speak '{"text":"x","priority":"loud"}' 'bad_request'
expect "POST /tts/stop" POST /tts/stop '{}' '.ok == true' || true

expect "POST /stt/listen start" POST /stt/listen '{"mode":"start"}' '.ok == true' || true
expect "POST /stt/listen stop" POST /stt/listen '{"mode":"stop"}' '.ok == true' || true
expect_error "POST /stt/listen bad mode" POST /stt/listen '{"mode":"maybe"}' 'bad_request'

for st in listening thinking acting speaking confirm idle; do
  expect "POST /hud $st" POST /hud "{\"state\":\"$st\",\"transcript\":\"contract test\",\"caption\":\"$st\",\"latency_ms\":123}" '.ok == true' || true
done
expect_error "POST /hud bad state" POST /hud '{"state":"dancing"}' 'bad_request'
if [[ -n "$SNAP_ID" && -n "$FIRST_EL" ]]; then
  expect "POST /hud highlight" POST /hud "{\"state\":\"idle\",\"highlight\":{\"snapshot_id\":\"$SNAP_ID\",\"element_id\":$FIRST_EL}}" '.ok == true' || true
fi

expect "POST /debug/utterance" POST /debug/utterance '{"text":"contract test, please ignore"}' '.ok == true' || true

# P2: screen capture. A permission error is acceptable (Screen Recording is only granted if P2 is built).
call POST /screen/capture '{}'; out="$RESP"
if jq -e '.ok == true and (.png_base64|type=="string" and length > 100)' "$out" >/dev/null 2>&1; then
  pass "POST /screen/capture (P2)"
elif jq -e "$ERR_SHAPE" "$out" >/dev/null 2>&1; then
  skip "POST /screen/capture (P2)" "$(jq -r .error.code "$out")"
else
  fail "POST /screen/capture (P2)" "$(head -c 200 "$out")"
fi

# ---------------------------------------------------------------------------------------------
if [[ "$LIVE" == "1" ]]; then
  echo "-- live: opening TextEdit, typing, pressing keys and scrolling --"
  expect "POST /app/open" POST /app/open '{"name":"TextEdit"}' '(.app|type=="string") and (.pid|type=="number")' || true
  expect_error "POST /app/open unknown" POST /app/open '{"name":"zz-no-such-app-zz"}' 'app_not_found'
  sleep 1.5
  expect "POST /input/key cmd+n" POST /input/key '{"keys":"cmd+n"}' '.ok == true' || true
  sleep 1
  expect "POST /input/type" POST /input/type '{"text":"Beacon contract test. "}' '(.chars|type=="number" and . > 0)' || true
  expect "POST /input/key tab" POST /input/key '{"keys":"tab"}' '.ok == true' || true
  expect "POST /input/scroll down" POST /input/scroll '{"direction":"down","amount":"page"}' '.ok == true' || true
  expect "POST /input/scroll px" POST /input/scroll '{"direction":"up","amount":200}' '.ok == true' || true
  if expect "POST /ax/snapshot (TextEdit)" POST /ax/snapshot '{}' '(.elements|type=="array")'; then
    s="$(LAST)"
    sid="$(jq -r .snapshot_id "$s")"
    tid="$(jq -r '[.elements[]|select(.role=="textarea" or .role=="textfield")][0].id // empty' "$s")"
    if [[ -n "$tid" ]]; then
      expect "POST /ax/act focus" POST /ax/act "{\"snapshot_id\":\"$sid\",\"element_id\":$tid,\"action\":\"focus\"}" '(.performed_via|IN("ax","click"))' || true
      expect "POST /ax/act set_value" POST /ax/act "{\"snapshot_id\":\"$sid\",\"element_id\":$tid,\"action\":\"set_value\",\"value\":\"Beacon set_value test\"}" '(.performed_via|IN("ax","click"))' || true
      expect "POST /input/scroll element" POST /input/scroll "{\"direction\":\"down\",\"amount\":100,\"snapshot_id\":\"$sid\",\"element_id\":$tid}" '.ok == true' || true
    else
      skip "POST /ax/act" "no text area in the TextEdit snapshot"
    fi
    expect_error "POST /ax/act missing element" POST /ax/act "{\"snapshot_id\":\"$sid\",\"element_id\":99999,\"action\":\"press\"}" 'element_not_found'
  fi
  expect "POST /input/key escape" POST /input/key '{"keys":"escape"}' '.ok == true' || true
  echo "      (TextEdit was left open with an unsaved document; close it without saving)"
else
  skip "POST /ax/act, /input/*, /app/open" "screen actions; run with --live"
fi

echo "-- fixtures --"
check_fixtures

echo
echo "Summary: $PASS passed, $FAIL failed, $SKIP skipped"
if (( FAIL > 0 )); then
  printf 'Failed: %s\n' "${FAILED[@]}"
  exit 1
fi
