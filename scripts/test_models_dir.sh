#!/usr/bin/env bash
# Tests for scripts/models_dir.sh: a worktree without models/ uses the main checkout's. Run: scripts/test_models_dir.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/models_dir.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TMP="$(cd "$TMP" && pwd -P)"
fail=0
check() {  # name expected actual
  if [[ "$2" == "$3" ]]; then echo "ok   $1"; else echo "FAIL $1: expected '$2', got '$3'"; fail=1; fi
}

git init -q "$TMP/main"
git -C "$TMP/main" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
mkdir -p "$TMP/main/models/whisperkit-coreml"
git -C "$TMP/main" worktree add -q "$TMP/wt" -b wt

check "worktree without models uses the main checkout's" "$TMP/main/models" "$(models_dir_for "$TMP/wt")"
check "main checkout uses its own" "" "$(models_dir_for "$TMP/main")"
mkdir -p "$TMP/wt/models/whisperkit-coreml"
check "worktree with its own models keeps them" "" "$(models_dir_for "$TMP/wt")"
rm -rf "$TMP/wt/models" "$TMP/main/models"
check "neither has models: nothing to override" "" "$(models_dir_for "$TMP/wt")"
mkdir -p "$TMP/plain"
check "not a git checkout" "" "$(models_dir_for "$TMP/plain")"
exit $fail
