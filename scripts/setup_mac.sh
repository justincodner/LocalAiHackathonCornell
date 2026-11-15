#!/usr/bin/env bash
# Owner: Mac owner
# One-time Mac setup (run while online). Idempotent: safe to re-run.
#   - brew installs git-lfs, uv, jq
#   - clones PrismML's Bonsai-demo next to this repo (../Bonsai-demo) and sets up the 2B by default
#   - installs the Brain into its virtual environment when present
# Speech models (models/whisperkit-coreml, models/ttskit) are fetched by the Helper's speech setup, not here.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BONSAI_DIR="${BONSAI_DIR:-$(cd "$ROOT/.." && pwd)/Bonsai-demo}"
BONSAI_REPO="https://github.com/PrismML-Eng/Bonsai-demo"
PY311="/Library/Frameworks/Python.framework/Versions/3.11/bin"

say() { printf '\n==> %s\n' "$*"; }

# 1. Tools -------------------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh" >&2
  exit 1
fi
for pkg in git-lfs uv jq; do
  if command -v "$pkg" >/dev/null 2>&1; then
    echo "ok: $pkg already installed"
  else
    say "brew install $pkg"
    brew install "$pkg"
  fi
done
git lfs install --skip-repo >/dev/null

if [[ -x "$PY311/python3.11" ]]; then
  echo "ok: Python 3.11 at $PY311"
  export PATH="$PY311:$PATH"
elif command -v python3.11 >/dev/null 2>&1; then
  echo "ok: python3.11 on PATH"
else
  echo "warning: Python 3.11 not found; uv will download a managed interpreter when needed" >&2
fi

if ! xcrun --find swift >/dev/null 2>&1; then
  echo "warning: Swift toolchain not found; install Xcode 16+ to build the Helper" >&2
fi

# 2. Bonsai-demo -------------------------------------------------------------
if [[ -d "$BONSAI_DIR/.git" ]]; then
  echo "ok: Bonsai-demo already cloned at $BONSAI_DIR"
else
  say "Cloning Bonsai-demo into $BONSAI_DIR"
  git clone "$BONSAI_REPO" "$BONSAI_DIR"
fi

run_bonsai_setup() {
  local model="$1"
  say "Bonsai-demo setup for $model (downloads binaries and weights on first run)"
  # Beacon only uses the llama.cpp server; skip MLX, Open WebUI and the Jupyter venv by default to save disk
  # (the demo Mac has little free space). Override any of these by exporting them as 1.
  (cd "$BONSAI_DIR" && BONSAI_MODEL="$model" \
    BONSAI_SKIP_MLX="${BONSAI_SKIP_MLX:-1}" \
    BONSAI_OPENWEBUI="${BONSAI_OPENWEBUI:-0}" \
    BONSAI_CODE_INTERPRETER="${BONSAI_CODE_INTERPRETER:-0}" \
    ./setup.sh </dev/null)
}
MODEL_SIZE="${BONSAI_MODEL:-2B}"
run_bonsai_setup "$MODEL_SIZE"
if [[ "$MODEL_SIZE" != 2B && "${BONSAI_ALSO_2B:-0}" == "1" ]]; then
  run_bonsai_setup 2B
fi

# 3. Model folders for the Helper --------------------------------------------
mkdir -p "$ROOT/models/whisperkit-coreml" "$ROOT/models/ttskit" "$ROOT/logs"
say "Speech models"
echo "WhisperKit models go in: $ROOT/models/whisperkit-coreml  (python3 scripts/download_whisper_models.py: small.en by default; --all adds base.en and tiny.en)"
echo "TTSKit models go in:     $ROOT/models/ttskit             (Qwen3-TTS 0.6B, fetched on first online launch)"
for d in whisperkit-coreml ttskit; do
  if [[ -n "$(ls -A "$ROOT/models/$d" 2>/dev/null)" ]]; then
    echo "  $d: present"
  else
    echo "  $d: EMPTY - download before going offline"
  fi
done

# 4. Brain -------------------------------------------------------------------
if [[ -d "$ROOT/brain" && -f "$ROOT/brain/pyproject.toml" ]]; then
  say "Installing the Brain's local environment"
  (
    cd "$ROOT/brain"
    if [[ -f uv.lock ]]; then
      uv sync --locked --no-dev
    else
      [[ -x .venv/bin/python ]] || uv venv
      uv pip install --python .venv/bin/python -e .
    fi
  )
else
  echo "Brain is not in this checkout yet; skipping uv sync."
fi

say "Setup done. Next: scripts/build_helper.sh --install, then scripts/run_all.sh"
