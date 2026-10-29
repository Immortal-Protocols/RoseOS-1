#!/usr/bin/env bash
# Checks the arm64 JNI library: AArch64 ELF shared object, every LOAD segment aligned to at least
# 16 KiB, and exactly the three NativeAgent JNI exports.
set -euo pipefail
LIB="${1:?usage: check-native-lib.sh librose_chat_android.so NDK_LLVM_BIN}"
BIN="${2:?NDK llvm bin dir}"
fail() { echo "native library check failed: $*" >&2; exit 1; }
# Read the header whole before matching: `readelf | grep -q` under pipefail fails whenever grep exits
# before readelf has finished writing (SIGPIPE), even though the line matched.
HDR="$("$BIN/llvm-readelf" -h "$LIB")" || fail "llvm-readelf -h failed on $LIB"
SUMMARY="$(grep -E '^ *(Class|Type|Machine):' <<<"$HDR" | tr -s ' ' | tr '\n' ';')"
[[ "$HDR" =~ Machine:[[:space:]]+AArch64 ]] || fail "not AArch64 ($SUMMARY)"
[[ "$HDR" =~ Type:[[:space:]]+DYN ]] || fail "not a shared object ($SUMMARY)"
LOADS=0
while read -r align; do
  LOADS=$((LOADS + 1))
  (( $((align)) >= 16384 )) || fail "LOAD segment aligned to $align, need 0x4000"
done < <("$BIN/llvm-readelf" -lW "$LIB" | awk '$1=="LOAD"{print $NF}')
(( LOADS > 0 )) || fail "no LOAD segments found"
EXPORTS="$("$BIN/llvm-nm" -D --defined-only "$LIB" | awk '{print $3}' | { grep '^Java_' || true; } | sort | tr '\n' ' ')"
[[ "$EXPORTS" == "Java_dev_pixel_models_NativeAgent_nativeCreate Java_dev_pixel_models_NativeAgent_nativeDestroy Java_dev_pixel_models_NativeAgent_nativeStep " ]] \
  || fail "unexpected JNI exports: $EXPORTS"
echo "native library OK: AArch64 .so, 16 KiB LOAD alignment, 3 NativeAgent exports, sha256 $(sha256sum "$LIB" | cut -d' ' -f1)"
