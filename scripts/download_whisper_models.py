#!/usr/bin/env python3
"""Download WhisperKit CoreML models for the Mac Helper, so speech input works offline.

    python3 scripts/download_whisper_models.py                 # small.en (the default)
    python3 scripts/download_whisper_models.py base.en tiny.en
    python3 scripts/download_whisper_models.py --all           # small.en, base.en, tiny.en

Each model lands in models/whisperkit-coreml/openai_whisper-<name>/ (BEACON_MODELS_DIR is honoured),
from Hugging Face argmaxinc/whisperkit-coreml, with tokenizer.json from openai/whisper-<name> copied
inside it: the Helper refuses to load a model without a local tokenizer rather than fetch one.
Already-downloaded files of the right size are skipped. Stdlib only.
"""

from __future__ import annotations  # macOS's /usr/bin/python3 is 3.9

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

CHOICES = ["small.en", "base.en", "tiny.en"]  # keep in step with Config.sttModelChoices
REPO = "argmaxinc/whisperkit-coreml"
HF = "https://huggingface.co"
ROOT = Path(__file__).resolve().parent.parent


def models_dir() -> Path:
    raw = os.environ.get("BEACON_MODELS_DIR", "models")
    path = Path(raw)
    return (path if path.is_absolute() else ROOT / path) / "whisperkit-coreml"


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def download(url: str, dest: Path, size: int | None = None) -> None:
    if dest.exists() and (size is None or dest.stat().st_size == size):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as response, open(partial, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    partial.replace(dest)


def fetch(name: str, base: Path) -> None:
    folder = f"openai_whisper-{name}"
    listing = get_json(f"{HF}/api/models/{REPO}/tree/main/{folder}?recursive=1")
    # The compiled .mlmodelc folders are what WhisperKit loads; the .mlpackage sources would double the size.
    files = [f for f in listing if f["type"] == "file" and ".mlpackage/" not in f["path"]]
    if not files:
        sys.exit(f"{folder} not found in {REPO}")
    total = sum(f.get("size", 0) for f in files)
    print(f"{name}: {len(files)} files, {total / 1e6:.0f} MB -> {base / folder}")
    for f in files:
        download(f"{HF}/{REPO}/resolve/main/{f['path']}", base / f["path"], f.get("size"))
    download(f"{HF}/openai/whisper-{name}/resolve/main/tokenizer.json", base / folder / "tokenizer.json")
    print(f"{name}: ready")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="*", help=f"model names (default: {CHOICES[0]})")
    parser.add_argument("--all", action="store_true", help="download " + ", ".join(CHOICES))
    args = parser.parse_args()
    names = CHOICES if args.all else (args.models or CHOICES[:1])
    base = models_dir()
    for name in names:
        fetch(name, base)
    if not args.all and not args.models:
        print("Speech input now uses small.en. Pass base.en or tiny.en (or --all) for the faster options.")


if __name__ == "__main__":
    main()
