#!/usr/bin/env bash
# Stages the Rose Models signing inputs from one finished build into a new, empty directory:
# exactly rose-models-unsigned.apk, librose_chat_android.so, apk-metadata.txt, build-receipt.txt,
# artifact-receipt.txt and SHA256SUMS. Nothing else from out/, the workspace or the environment
# is copied. Fails on a missing, extra or ambiguous build, on symlinks, or when the native library
# inside the APK is not byte-identical to the staged one. Signs nothing.
#
#   stage-artifact.sh STAGE_DIR BUILD_OUT_DIR NATIVE_LIB [NDK_LLVM_BIN]
set -euo pipefail
STAGE="${1:?usage: stage-artifact.sh STAGE_DIR BUILD_OUT_DIR NATIVE_LIB [NDK_LLVM_BIN]}"
BUILD="${2:?build output directory (one out/rose-models.* directory)}"
NATIVE="${3:?native library that build.sh packaged}"
LLVM_BIN="${4:-}"
APP="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$APP/../../.." && pwd)"
fail() { echo "stage failed: $*" >&2; exit 1; }

[[ ! -e "$STAGE" ]] || fail "$STAGE already exists; staging needs a new directory"
[[ -d "$BUILD" && ! -L "$BUILD" ]] || fail "$BUILD is not a build directory"
for f in rose-models-unsigned.apk apk-metadata.txt build-receipt.txt; do
  [[ -f "$BUILD/$f" && ! -L "$BUILD/$f" ]] || fail "$BUILD/$f missing or not a regular file"
done
[[ -f "$NATIVE" && ! -L "$NATIVE" ]] || fail "$NATIVE missing or not a regular file"

APK="$BUILD/rose-models-unsigned.apk"
NATIVE_SHA="$(sha256sum "$NATIVE" | cut -d' ' -f1)"
LIBS="$(unzip -Z1 "$APK" | grep -E '^lib/' || true)"
[[ "$LIBS" == "lib/arm64-v8a/librose_chat_android.so" ]] || fail "APK native entries are not exactly the arm64 library: ${LIBS:-none}"
EMBEDDED_SHA="$(unzip -p "$APK" lib/arm64-v8a/librose_chat_android.so | sha256sum | cut -d' ' -f1)"
[[ "$EMBEDDED_SHA" == "$NATIVE_SHA" ]] || fail "library in APK ($EMBEDDED_SHA) differs from $NATIVE ($NATIVE_SHA)"
if unzip -Z1 "$APK" | grep -qiE '^META-INF/.*\.(RSA|DSA|EC|SF)$'; then
  fail "APK carries signature files; this stage takes unsigned inputs only"
fi
NATIVE_CHECK="not re-run (no NDK llvm bin given)"
if [[ -n "$LLVM_BIN" ]]; then
  NATIVE_CHECK="$(bash "$APP/tests/check-native-lib.sh" "$NATIVE" "$LLVM_BIN")"
fi

mkdir -p "$STAGE"
cp "$APK" "$BUILD/apk-metadata.txt" "$BUILD/build-receipt.txt" "$STAGE/"
cp "$NATIVE" "$STAGE/librose_chat_android.so"

tree() { git -C "$ROOT" rev-parse --verify -q "HEAD:workspace/models/$1" || echo "not committed"; }
lock() { sha256sum "$ROOT/workspace/models/$1/Cargo.lock" | cut -d' ' -f1; }
ndk_rev() {
  local props="${ANDROID_NDK_HOME:-${ANDROID_NDK_LATEST_HOME:-}}/source.properties"
  [[ -f "$props" ]] && sed -n 's/^Pkg.Revision *= *//p' "$props" || echo "unknown"
}
{
  echo "schema: rose-models-unsigned-artifact/1"
  echo "status: UNSIGNED signing input; not installable, not shipped"
  echo "source_sha: $(git -C "$ROOT" rev-parse HEAD)"
  echo "source_dirty_models_files: $(git -C "$ROOT" status --porcelain -- workspace/models | wc -l | tr -d ' ')"
  echo "github_event: ${GITHUB_EVENT_NAME:-local}"
  echo "github_ref: ${GITHUB_REF:-local}"
  echo "github_sha: ${GITHUB_SHA:-local}"
  echo "github_workflow: ${GITHUB_WORKFLOW:-local}"
  echo "github_run_id: ${GITHUB_RUN_ID:-local}"
  echo "github_run_attempt: ${GITHUB_RUN_ATTEMPT:-local}"
  echo "github_job: ${GITHUB_JOB:-local}"
  echo "runner_image: ${ImageOS:-unknown} ${ImageVersion:-unknown}"
  echo "tree_android: $(tree android)"
  echo "tree_rose_chat_android: $(tree rose-chat-android)"
  echo "tree_rose_chat_core: $(tree rose-chat-core)"
  echo "tree_contracts: $(tree contracts)"
  echo "cargo_lock_rose_chat_android_sha256: $(lock rose-chat-android)"
  echo "cargo_lock_rose_chat_core_sha256: $(lock rose-chat-core)"
  echo "rustc: $(rustc --version 2>/dev/null || echo unknown)"
  echo "cargo: $(cargo --version 2>/dev/null || echo unknown)"
  echo "java: $(java -version 2>&1 | grep -v JAVA_TOOL_OPTIONS | head -1)"
  echo "ndk_revision: $(ndk_rev)"
  echo "android_sdk: $(grep -oE 'fetch [^ ]+ [0-9a-f]{64}' "$ROOT/scripts/setup-android-sdk.sh" | awk '{printf "%s sha256 %s; ", $2, $3}')"
  grep -E "^(package|sdkVersion|targetSdkVersion|uses-permission|native-code):" "$STAGE/apk-metadata.txt" \
    | sed 's/^/badging /'
  echo "native_check: $NATIVE_CHECK"
  echo "apk_sha256: $(sha256sum "$STAGE/rose-models-unsigned.apk" | cut -d' ' -f1)"
  echo "native_sha256: $NATIVE_SHA"
  echo "native_in_apk_sha256: $EMBEDDED_SHA"
} > "$STAGE/artifact-receipt.txt"
(cd "$STAGE" && sha256sum rose-models-unsigned.apk librose_chat_android.so apk-metadata.txt \
  build-receipt.txt artifact-receipt.txt > SHA256SUMS)
echo "staged $(wc -l < "$STAGE/SHA256SUMS") files in $STAGE"
cat "$STAGE/SHA256SUMS"
