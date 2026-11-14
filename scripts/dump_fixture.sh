#!/usr/bin/env bash
# Owner: Mac owner
# Save a snapshot of the frontmost window as a fixture (spec 5.7).
#   scripts/dump_fixture.sh <name> [delay_seconds]   -> contracts/fixtures/<name>.json
# Uses the bundled, signed app binary so the Accessibility grant applies. Default delay 3 s to switch apps.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="${1:-}"
DELAY="${2:-3}"
if [[ -z "$NAME" ]]; then
  echo "usage: scripts/dump_fixture.sh <name> [delay_seconds]" >&2
  echo "  first five: mail_inbox mail_reply_draft notes_list safari_form safari_form_unlabeled" >&2
  exit 2
fi
NAME="${NAME%.json}"

BIN=""
for candidate in "/Applications/Beacon Helper.app/Contents/MacOS/beacon-helper" \
                 "$ROOT/build/Beacon Helper.app/Contents/MacOS/beacon-helper"; do
  if [[ -x "$candidate" ]]; then BIN="$candidate"; break; fi
done
if [[ -z "$BIN" ]]; then
  echo "error: no built Helper found; run scripts/build_helper.sh first" >&2
  exit 1
fi

mkdir -p "$ROOT/contracts/fixtures"
cd "$ROOT"
BEACON_ROOT="$ROOT" "$BIN" dump --out "$ROOT/contracts/fixtures/$NAME.json" --delay "$DELAY"
