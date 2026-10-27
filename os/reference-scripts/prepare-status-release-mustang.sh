#!/usr/bin/env bash
# Build/sign/verify only. This script has no device connection or flashing step.
set -euo pipefail
ROOT=/home/pixelbuild/pixel-os
export PIXEL_OS_BUILD_NUMBER="${PIXEL_OS_BUILD_NUMBER:?Set the reviewed release build number}"
[[ "$PIXEL_OS_BUILD_NUMBER" =~ ^[0-9]{10}$ ]] || exit 1
exec 8>"$ROOT/status-release.lock"
flock -n 8 || { echo 'Release preparation is already running' >&2; exit 1; }
phase() { printf '%s\n' "$1" > "$ROOT/status-release.phase"; printf '%s %s\n' "$(date -u +%FT%TZ)" "$1"; }
trap 'result=$?; printf "%s\n" "$result" > "$ROOT/status-release.exit"; if [ "$result" -ne 0 ]; then phase "failed; inspect the release preparation logs"; fi' EXIT
validate_source() {
python3 - "$ROOT" <<'PY'
from pathlib import Path
import hashlib, json, sys
r=Path(sys.argv[1]); source=r/'grapheneos-2026100600'
manifest=json.loads((r/'status-v7-source-sha256.json').read_text())
for path, expected in manifest.items():
    assert hashlib.sha256((source/path).read_bytes()).hexdigest()==expected, path
print('Final two-line source manifest matches the reviewed emulator build')
PY
}
phase 'validating frozen two-line source'
validate_source
phase 'building optimized mustang release'
bash "$ROOT/build-release-mustang.sh" > "$ROOT/mustang-release-build.log" 2>&1
validate_source
phase 'signing mustang release'
bash "$ROOT/sign-ready-mustang.sh" > "$ROOT/mustang-signing.log" 2>&1
phase 'verifying signed archives, firmware, and factory partitions'
bash "$ROOT/verify-ready-mustang.sh" > "$ROOT/mustang-verification.log" 2>&1
validate_source
EVIDENCE="$ROOT/mustang-release-verification/$PIXEL_OS_BUILD_NUMBER"
cp "$ROOT/status-v7-source-sha256.json" "$EVIDENCE/reviewed-source-sha256.json"
phase 'signed mustang release verified; physical installation not performed'
