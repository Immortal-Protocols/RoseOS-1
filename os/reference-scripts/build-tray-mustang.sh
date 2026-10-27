#!/usr/bin/env bash
# Compile the device SystemUI first. Does not sign, flash, unlock, or erase a phone.
set -eo pipefail
ROOT=/home/pixelbuild/pixel-os
exec 9>"$ROOT/tray-build.lock"
flock -n 9 || { echo 'Another tray build already owns the lock'; exit 1; }
printf '%s\n' "$$" > "$ROOT/tray-build.pid"
cat /proc/sys/kernel/random/boot_id > "$ROOT/tray-build.boot-id"
phase() { printf '%s\n' "$1" > "$ROOT/tray-build.phase"; printf '%s %s\n' "$(date -u +%FT%TZ)" "$1"; }
trap 'code=$?; if [ "$code" -ne 0 ]; then phase "mustang compile failed with exit $code; see mustang-build.log"; fi; rm -f "$ROOT/tray-build.pid"' EXIT
cd "$ROOT/grapheneos-2026100600"
test -f vendor/google_devices/mustang/mustang.mk
if ! grep -q "persist.wm.debug.pixel_agent_tray=1" vendor/google_devices/mustang/mustang.mk; then
    printf '\n# Custom reserved Rose Agent system tray\nPRODUCT_SYSTEM_PROPERTIES += persist.wm.debug.pixel_agent_tray=1\n' >> vendor/google_devices/mustang/mustang.mk
fi
export PATH="$ROOT/toolchain/node/bin:$PATH"
export OUT_DIR=out-mustang
export BUILD_NUMBER=2026100900 BUILD_DATETIME=1791537321
phase 'selecting mustang production target; SystemUI compile only'
source build/envsetup.sh
lunch mustang-cur-user
phase 'compiling mustang SystemUI; prototype runtime and integration still pending'
m -j16 SystemUI
phase 'mustang SystemUI compiled; full image and runtime validation pending'
