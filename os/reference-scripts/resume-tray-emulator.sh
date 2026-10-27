#!/usr/bin/env bash
# Resume emulator compilation only, preserving source and prior build output.
set -eo pipefail
ROOT=/root/pixel-os
exec 9>"$ROOT/tray-build.lock"
flock -n 9 || { echo 'Another tray build already owns the lock'; exit 1; }
printf '%s\n' "$$" > "$ROOT/tray-build.pid"
cat /proc/sys/kernel/random/boot_id > "$ROOT/tray-build.boot-id"
phase() { printf '%s\n' "$1" > "$ROOT/tray-build.phase"; printf '%s %s\n' "$(date -u +%FT%TZ)" "$1"; }
trap 'code=$?; if [ "$code" -ne 0 ]; then phase "failed with exit $code; see tray-build-resume.log"; fi; rm -f "$ROOT/tray-build.pid"' EXIT
cd "$ROOT/grapheneos-2026100600"
test -f frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/AgentTrayPrototype.java
phase 'selecting emulator target (resume; twelve CPU affinity, four build jobs)'
source build/envsetup.sh
lunch sdk_phone64_x86_64-cur-userdebug
phase 'compiling SystemUI (resume)'
m -j4 SystemUI
phase 'compiling emulator image (resume)'
m -j4
phase 'emulator image built; runtime validation pending'
