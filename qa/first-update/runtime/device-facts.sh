#!/usr/bin/env bash
# READ-ONLY device/VM facts for RT-4, RT-7, RT-9, RT-10 (and C3a). Run only at an authorized
# checkpoint by the operator with ADB access (Infra for the VM; Nathan's checkpoint for the phone).
# Usage: device-facts.sh <adb-serial> <output-dir>. Never installs, writes settings or reboots.
set -euo pipefail
S="$1";OUT="$2";mkdir -p "$OUT";A(){ adb -s "$S" "$@"; }
date -u +%FT%TZ > "$OUT/captured-at.txt"
for p in ro.build.fingerprint ro.build.version.incremental ro.build.id ro.product.device ro.boot.verifiedbootstate ro.boot.vbmeta.digest persist.wm.debug.pixel_agent_tray; do echo "$p=$(A shell getprop $p)"; done > "$OUT/props.txt"
for pkg in dev.pixel.voice dev.pixel.backgroundcontrol dev.pixel.calculator.agent app.grapheneos.updater; do
 A shell dumpsys package "$pkg" | grep -E 'versionCode|versionName|codePath|signatures|pkgFlags|installerPackageName|firstInstallTime|lastUpdateTime' > "$OUT/package-$pkg.txt" || echo missing > "$OUT/package-$pkg.txt"
 path=$(A shell pm path "$pkg" 2>/dev/null | head -1 | sed 's/^package://' | tr -d '\r');[ -n "$path" ] && A pull "$path" "$OUT/$pkg.apk" >/dev/null 2>&1 || true
done
A shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME > "$OUT/home-holder.txt" || true
A shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp' > "$OUT/focus.txt" || true
A shell dumpsys display | grep -E 'mDisplayId|mDisplayInfo|ownerPackageName' > "$OUT/displays.txt" || true
A exec-out screencap -p > "$OUT/screen.png" || true
# Signer digests of pulled APKs (compare with 650d47f9...0bfb and the candidate table):
for apk in "$OUT"/*.apk; do [ -f "$apk" ] && apksigner verify --print-certs "$apk" > "$apk.certs.txt" 2>&1 || true; done
# Updater channel URL from the installed Updater resources:
[ -f "$OUT/app.grapheneos.updater.apk" ] && aapt2 dump resources "$OUT/app.grapheneos.updater.apk" 2>/dev/null | grep -A1 'string/url' > "$OUT/updater-url.txt" || true
(cd "$OUT" && sha256sum * > SHA256SUMS)
echo "Device facts written to $OUT"
