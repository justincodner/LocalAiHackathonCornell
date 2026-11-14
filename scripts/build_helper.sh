#!/usr/bin/env bash
# Owner: Mac owner
# Build the Helper in release mode and assemble a signed "build/Beacon Helper.app".
#
#   scripts/build_helper.sh             build + sign build/Beacon Helper.app
#   scripts/build_helper.sh --install   also copy it to /Applications/Beacon Helper.app (use this copy for demos)
#   scripts/build_helper.sh --no-runtime  sign without the hardened runtime (fallback if the mic misbehaves)
#
# Signing identity: $BEACON_SIGN_IDENTITY, else the first "Apple Development" identity in the keychain.
# A stable identity + fixed bundle ID (dev.beacon.helper) keeps Microphone/Accessibility grants across rebuilds.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER="$ROOT/helper"
APP="$ROOT/build/Beacon Helper.app"
INSTALL=0
RUNTIME=1
for arg in "$@"; do
  case "$arg" in
    --install) INSTALL=1 ;;
    --no-runtime) RUNTIME=0 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "==> swift build -c release"
(cd "$HELPER" && swift build -c release)
BIN_DIR="$(cd "$HELPER" && swift build -c release --show-bin-path)"

echo "==> Assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/beacon-helper" "$APP/Contents/MacOS/beacon-helper"
cp "$HELPER/BeaconHelper/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
shopt -s nullglob
for bundle in "$BIN_DIR"/*.bundle; do
  cp -R "$bundle" "$APP/Contents/Resources/"
done
shopt -u nullglob

IDENTITY="${BEACON_SIGN_IDENTITY:-}"
if [[ -z "$IDENTITY" ]]; then
  IDENTITY="$(security find-identity -v -p codesigning | awk -F'"' '/Apple Development/ {print $2; exit}')"
fi
if [[ -z "$IDENTITY" ]]; then
  echo "error: no signing identity. Set BEACON_SIGN_IDENTITY or add an Apple Development certificate in Xcode." >&2
  echo "       (Ad-hoc signing would re-prompt for permissions on every build.)" >&2
  exit 1
fi

SIGN_OPTS=(--force --timestamp=none --sign "$IDENTITY" --entitlements "$HELPER/BeaconHelper/BeaconHelper.entitlements")
if [[ "$RUNTIME" == "1" ]]; then SIGN_OPTS+=(--options runtime); fi

echo "==> Signing with: $IDENTITY (hardened runtime: $RUNTIME)"
# Sign nested resource bundles first, then the app.
for bundle in "$APP/Contents/Resources"/*.bundle; do
  [[ -e "$bundle" ]] || continue
  codesign --force --timestamp=none --sign "$IDENTITY" "$bundle" 2>/dev/null || true
done
codesign "${SIGN_OPTS[@]}" "$APP"
codesign --verify --strict --verbose=1 "$APP"
codesign -dv "$APP" 2>&1 | grep -E 'Identifier|Authority=Apple Development|TeamIdentifier|flags' || true

if [[ "$INSTALL" == "1" ]]; then
  DEST="/Applications/Beacon Helper.app"
  echo "==> Installing to $DEST"
  pkill -x beacon-helper 2>/dev/null || true
  rm -rf "$DEST"
  cp -R "$APP" "$DEST"
  echo "Installed. First launch: grant Microphone and Accessibility to Beacon Helper."
fi

echo "==> Done: $APP"
