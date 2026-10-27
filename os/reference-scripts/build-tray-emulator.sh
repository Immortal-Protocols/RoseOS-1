#!/usr/bin/env bash
# Emulator only. No phone discovery, flashing, key generation, or vendor setup.
set -eo pipefail
BUILD_ROOT=/root/pixel-os/grapheneos-2026100600
PHASE_FILE=/root/pixel-os/tray-build.phase
SYNC_PID=${1:?Pass the PID of the existing source sync}
phase() { printf '%s\n' "$1" > "$PHASE_FILE"; printf '%s %s\n' "$(date -u +%FT%TZ)" "$1"; }
trap 'phase "failed at line $LINENO (see tray-build.log)"' ERR
phase 'waiting for existing source sync'
while [ -r "/proc/$SYNC_PID/cmdline" ] && tr '\0' ' ' < "/proc/$SYNC_PID/cmdline" | grep -q -- '-- sync'; do
    sleep 30
done
cd "$BUILD_ROOT"
phase 'checking complete local checkout'
# Local-only checkout verifies all required objects exist. No second network sync.
repo sync -l -j4 --fail-fast
phase 'applying pinned emulator prototype'
python3 /root/pixel-os/prototype/apply.py "$BUILD_ROOT"
phase 'selecting emulator target'
source build/envsetup.sh
lunch sdk_phone64_x86_64-cur-userdebug
phase 'compiling SystemUI'
m -j4 SystemUI
phase 'compiling emulator image'
m -j4
phase 'emulator image built; runtime validation pending'
