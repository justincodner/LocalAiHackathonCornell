#!/usr/bin/env bash
# Owner: Mac owner (H3). Press Reply through the Helper and verify text in the demo draft.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/mail_reply_test.sh [delay_seconds]
Run the signed Helper with Accessibility granted. Select Sarah Chen's
"Lunch tomorrow?" in Mail's local Beacon Demo mailbox and close other drafts.
Keep Mail frontmost and leave the keyboard and mouse idle while the test runs.
The default delay is 3 seconds so you can switch to Mail from your terminal.
The test leaves the verified draft open for inspection.
EOF
}

fail() {
  printf 'FAIL  %s\n' "$*" >&2
  exit 1
}

case "${1:-}" in -h|--help) usage; exit 0 ;; esac
delay="${1:-3}"
if (( $# > 1 )) || [[ ! "$delay" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  usage >&2
  exit 2
fi
command -v jq >/dev/null || fail 'jq is required.'

url="${BEACON_HELPER_URL:-http://127.0.0.1:8765}"
[[ "$url" =~ ^http://(127\.0\.0\.1|localhost):[0-9]{1,5}$ ]] || fail 'Set BEACON_HELPER_URL to the local Helper address.'
headers=(-H 'Content-Type: application/json')
if [[ -n "${BEACON_TOKEN:-}" ]]; then headers+=(-H "X-Beacon-Token: $BEACON_TOKEN"); fi

post() {
  local path="$1" response code
  response="$(curl -q --noproxy '*' --silent --show-error --fail --max-time 5 \
    "${headers[@]}" --data "$2" "$url$path")" || fail "$path request failed."
  if ! jq -e '.ok == true' <<< "$response" >/dev/null; then
    code="$(jq -r '.error.code // "invalid response"' <<< "$response")" || code='invalid response'
    if [[ "$path" == /ax/snapshot && "$code" == timeout ]]; then return 75; fi
    fail "$path returned $code."
  fi
  printf '%s\n' "$response"
}

snapshot() {
  local response status
  for _ in {1..5}; do
    if response="$(post /ax/snapshot '{"scope":"window","max_elements":250}')"; then
      printf '%s\n' "$response"
      return 0
    else
      status=$?
      if (( status != 75 )); then return "$status"; fi
    fi
    sleep 0.25
  done
  fail 'Mail did not return a snapshot in time.'
}

element_ref() {
  jq -cn --arg sid "$1" --argjson eid "$2" '{snapshot_id:$sid,element_id:$eid}'
}

snapshot_id() {
  jq -er '.snapshot_id | select(test("^s_[0-9]{4,}$"))'
}

draft_body_id() {
  jq -er '
    select(.app == "Mail" and .window == "Re: Lunch tomorrow?" and .truncated == false)
    | select(any(.elements[]; .role == "button" and (.label | ascii_downcase) == "send"))
    | .focused_id as $focus
    | .elements[]
    | select(.id == $focus and (.role == "textarea" or .role == "group") and (.label | ascii_downcase) == "message body")
    | select((.states | index("focused")) != null and (.states | index("disabled")) == null)
    | .id'
}

printf 'Switch to the selected Beacon Demo message in Mail within %s seconds.\n' "$delay"
sleep "$delay"
inbox="$(snapshot)"
inbox_id="$(snapshot_id <<< "$inbox")" || fail 'The inbox snapshot has no valid ID.'
reply_id="$(jq -er '
  def selected: (.states | index("selected")) != null;
  select(.app == "Mail" and (.window | startswith("Beacon Demo — On My Mac")) and .truncated == false)
  | .elements as $elements
  | select(all($elements[] | select(.role == "row" and .label == "Beacon Demo"); selected))
  | select([$elements[] | select(.role == "row" and .label != "Beacon Demo" and selected)]
      | length == 1 and (.[0].label | test("^Sarah Chen - .* - Lunch tomorrow[?]( - |$)")))
  | [$elements[]
      | select(.role == "button" and .label == "Reply" and (.states | index("disabled")) == null)
      | . as $button
      | select(($elements | map(select(.id < $button.id and .depth < $button.depth)) | last | .role) == "toolbar")]
  | select(length == 1) | .[0].id' <<< "$inbox")" || fail 'Select Sarah Chen’s “Lunch tomorrow?” in Beacon Demo and make sure Mail’s Reply toolbar button is visible.'

reply="$(element_ref "$inbox_id" "$reply_id" | jq -c '. + {action:"press"}')"
pressed="$(post /ax/act "$reply")"
jq -e '.performed_via == "ax"' <<< "$pressed" >/dev/null || fail 'Reply did not use an accessibility action.'

for _ in {1..12}; do
  draft="$(snapshot)"
  if body_id="$(draft_body_id <<< "$draft")"; then break; fi
  sleep 0.25
done
[[ -n "$body_id" ]] || fail 'The Sarah Chen reply did not open with focus in the message body. The test typed no text.'

text='Beacon H3 test draft.'
quote='Sarah Chen <sarah.chen@example.com> wrote:'
draft_id="$(snapshot_id <<< "$draft")" || fail 'The draft snapshot has no valid ID.'
contents="$(post /ax/read "$(element_ref "$draft_id" "$body_id")")"
jq -e --arg quote "$quote" --arg text "$text" \
  '.text | contains($quote) and (contains($text) | not)' <<< "$contents" >/dev/null \
  || fail 'The draft body must quote the seeded Sarah Chen message and be free of existing H3 test text.'

# Typing goes to the globally focused control. Refresh the focus check after the body read.
draft="$(snapshot)"
draft_body_id <<< "$draft" >/dev/null || fail 'The reply lost message body focus before typing.'
typed="$(post /input/type "$(jq -cn --arg text "$text" '{text:$text}')")"
jq -e --argjson count "${#text}" '.chars == $count' <<< "$typed" >/dev/null || fail 'The Helper reported an unexpected character count.'

for _ in {1..12}; do
  draft="$(snapshot)"
  body_id="$(draft_body_id <<< "$draft")" || fail 'The reply lost message body focus after typing.'
  draft_id="$(snapshot_id <<< "$draft")" || fail 'The draft snapshot has no valid ID.'
  contents="$(post /ax/read "$(element_ref "$draft_id" "$body_id")")"
  if jq -e --arg quote "$quote" --arg text "$text" \
    '.text | index($text) as $typed | index($quote) as $quoted | $typed != null and $quoted != null and $typed < $quoted' \
    <<< "$contents" >/dev/null; then
    printf 'PASS  Pressed Reply via Accessibility and verified the text in the open draft.\n'
    exit 0
  fi
  sleep 0.25
done
fail 'The typed text could not be verified in the reply body.'
