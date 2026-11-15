#!/usr/bin/env bash
# Owner: Mac owner. Sourced by run_all.sh: where the Helper should look for models/ when started from a git worktree.
#
# models/ is git-ignored, so a git worktree has none. A Helper started from a worktree (BEACON_ROOT=<worktree>) then
# found no Whisper model and cancelled every talk-key recording with stt_model_missing, before transcribing anything.

# Prints the main checkout's models folder when <root> is a worktree without a Whisper model folder and the main
# checkout has one. Prints nothing when <root>'s own models/ should be used.
models_dir_for() {
  local root="$1" common main
  [[ -d "$root/models/whisperkit-coreml" ]] && return 0
  common="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 0
  main="$(dirname "$common")"
  if [[ "$main" != "$root" && -d "$main/models/whisperkit-coreml" ]]; then
    echo "$main/models"
  fi
}
