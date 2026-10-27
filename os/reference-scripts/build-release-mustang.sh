#!/usr/bin/env bash
# Produce unsigned target files and release tooling. Signing and flashing are separate.
set -eo pipefail
ROOT=/home/pixelbuild/pixel-os
export BUILD_NUMBER="${PIXEL_OS_BUILD_NUMBER:?Set a new PIXEL_OS_BUILD_NUMBER explicitly; historical releases must not be overwritten}"
[[ "$BUILD_NUMBER" =~ ^[0-9]{10}$ ]] || { echo 'Expected a ten-digit build number' >&2; exit 1; }
export BUILD_DATETIME="${PIXEL_OS_BUILD_DATETIME:-$(date +%s)}"
exec 9>"$ROOT/tray-build.lock"
flock -n 9 || { echo 'Another tray build owns the lock'; exit 1; }
printf '%s\n' "$$" > "$ROOT/tray-build.pid"
cat /proc/sys/kernel/random/boot_id > "$ROOT/tray-build.boot-id"
phase() { printf '%s\n' "$1" > "$ROOT/tray-build.phase"; printf '%s %s\n' "$(date -u +%FT%TZ)" "$1"; }
trap 'code=$?; if [ "$code" -ne 0 ]; then phase "mustang release build failed with exit $code; see mustang-release-build.log"; fi; rm -f "$ROOT/tray-build.pid"' EXIT
cd "$ROOT/grapheneos-2026100600"
test -f vendor/google_devices/mustang/mustang.mk
export OUT_DIR=out-mustang
test ! -e "releases/$BUILD_NUMBER" || { echo 'Release directory already exists; choose a new build number' >&2; exit 1; }
export SYSTEMUI_OPTIMIZE_JAVA=true
source build/envsetup.sh
lunch mustang-cur-user
phase 'clearing generated installed outputs to refresh release version metadata'
m installclean
phase 'compiling complete mustang device target files and release tools'
m -j16 SystemUI vendorbootimage vendorkernelbootimage target-files-package otatools-package
phase 'staging unsigned mustang target files and release tools'
script/finalize.sh
phase "unsigned mustang $BUILD_NUMBER target files staged; signing and device validation pending"
