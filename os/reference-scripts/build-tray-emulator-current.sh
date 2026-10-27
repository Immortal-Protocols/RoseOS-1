#!/usr/bin/env bash
# Emulator-only combined SystemUI/image build. Never touches physical devices.
set -eo pipefail
ROOT=/home/pixelbuild/pixel-os
exec 9>"$ROOT/tray-build.lock"
flock -n 9 || { echo 'Another tray build owns the lock'; exit 1; }
printf '%s\n' "$$" > "$ROOT/tray-build.pid"
cat /proc/sys/kernel/random/boot_id > "$ROOT/tray-build.boot-id"
phase() { printf '%s\n' "$1" > "$ROOT/tray-build.phase"; printf '%s %s\n' "$(date -u +%FT%TZ)" "$1"; }
trap 'code=$?; if [ "$code" -ne 0 ]; then phase "emulator build failed with exit $code; see emulator-build.log"; fi; rm -f "$ROOT/tray-build.pid"' EXIT
cd "$ROOT/grapheneos-2026100600"
PRODUCT=device/generic/goldfish/64bitonly/product/sdk_phone64_x86_64.mk
# Generic 2800 MiB cap is too small for the signed-source product contents.
if grep -q 'BOARD_EMULATOR_DYNAMIC_PARTITIONS_SIZE.*2800' "$PRODUCT"; then
    sed -i '/BOARD_EMULATOR_DYNAMIC_PARTITIONS_SIZE/s/2800/4096/' "$PRODUCT"
fi
if grep -q '^PRODUCT_SYSTEM_PROPERTIES += persist.wm.debug.pixel_agent_tray=1$' "$PRODUCT"; then
    sed -i 's/^PRODUCT_SYSTEM_PROPERTIES += persist.wm.debug.pixel_agent_tray=1$/PRODUCT_PRODUCT_PROPERTIES += persist.wm.debug.pixel_agent_tray=1/' "$PRODUCT"
fi
if ! grep -q 'persist.wm.debug.pixel_agent_tray=1' "$PRODUCT"; then
    printf '\n# Custom agent tray emulator validation\nPRODUCT_PRODUCT_PROPERTIES += persist.wm.debug.pixel_agent_tray=1\n' >> "$PRODUCT"
fi
export OUT_DIR=out_adevtool_deps
export BUILD_NUMBER=2026100900 BUILD_DATETIME=1791536842
phase 'selecting x86_64 emulator target; physical Pixel untouched'
source build/envsetup.sh
lunch sdk_phone64_x86_64-cur-userdebug
phase 'compiling emulator SystemUI and full bootable image together'
m -j16 SystemUI droid
phase 'emulator image compiled; runtime tray validation pending'
