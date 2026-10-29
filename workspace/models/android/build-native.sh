#!/usr/bin/env bash
# Builds the arm64-v8a JNI adapter (librose_chat_android.so) for Rose Models with the Android NDK.
# It never installs anything: it needs an existing NDK (ANDROID_NDK_HOME or ANDROID_NDK_LATEST_HOME)
# and the Rust target aarch64-linux-android, and says exactly which is missing.
# Prints the path of the built library on success.
set -euo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
ADAPTER="$(cd "$APP/../rose-chat-android" && pwd)"
API=29
NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_LATEST_HOME:-}}"
[[ -n "$NDK" && -d "$NDK" ]] || { echo "Blocked: no Android NDK (set ANDROID_NDK_HOME)" >&2; exit 3; }
BIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
CLANG="$BIN/aarch64-linux-android${API}-clang"
[[ -x "$CLANG" ]] || { echo "Blocked: NDK has no $CLANG" >&2; exit 3; }
rustup target list --installed | grep -qx aarch64-linux-android \
  || { echo "Blocked: Rust target aarch64-linux-android is not installed" >&2; exit 3; }
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$CLANG"
# 16 KiB page alignment for Android 15+ devices.
export CARGO_TARGET_AARCH64_LINUX_ANDROID_RUSTFLAGS="-C link-arg=-Wl,-z,max-page-size=16384"
(cd "$ADAPTER" && cargo build --locked --release --target aarch64-linux-android -q)
LIB="$ADAPTER/target/aarch64-linux-android/release/librose_chat_android.so"
bash "$APP/tests/check-native-lib.sh" "$LIB" "$BIN" >&2
echo "$LIB"
