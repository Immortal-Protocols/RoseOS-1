#!/usr/bin/env bash
# Checks a built APK's badging against the Rose Models permission boundary:
# package dev.pixel.models, minSdk 29, targetSdk 35, INTERNET as the only permission and, when
# present, arm64-v8a as the only native ABI.
set -euo pipefail
META="${1:?usage: check-apk-policy.sh apk-metadata.txt [--require-native]}"
REQUIRE_NATIVE="${2:-}"
fail() { echo "APK policy violation: $*" >&2; exit 1; }
grep -q "^package: name='dev.pixel.models' " "$META" || fail "package is not dev.pixel.models"
grep -qx "sdkVersion:'29'" "$META" || fail "minSdk is not 29"
grep -qx "targetSdkVersion:'35'" "$META" || fail "targetSdk is not 35"
PERMS="$(grep '^uses-permission:' "$META" || true)"
[[ "$PERMS" == "uses-permission: name='android.permission.INTERNET'" ]] || fail "permissions are: $PERMS"
if [[ "$REQUIRE_NATIVE" == "--require-native" ]]; then
  grep -q "^native-code:" "$META" || fail "no native code, but --require-native was given"
fi
if grep -q "^native-code:" "$META"; then
  grep -qx "native-code: 'arm64-v8a'" "$META" || fail "native code is not arm64-v8a only"
fi
echo "APK policy OK: dev.pixel.models, sdk 29/35, INTERNET only"
