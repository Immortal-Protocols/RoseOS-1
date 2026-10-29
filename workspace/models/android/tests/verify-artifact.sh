#!/usr/bin/env bash
# Verifies a downloaded Rose Models signing-input artifact. The files are treated as data only:
# they are hashed, unzipped to stdout and parsed with aapt, never executed or sourced.
# Fails unless the directory holds exactly the six expected regular files, every byte matches
# SHA256SUMS and artifact-receipt.txt, the library inside the APK is the staged library, the APK
# is unsigned dev.pixel.models for arm64-v8a with INTERNET only, and the receipt names the
# expected source commit and workflow run.
#
#   verify-artifact.sh DIR --source SHA --run RUN_ID --attempt N [--aapt PATH]
set -euo pipefail
DIR="${1:?usage: verify-artifact.sh DIR --source SHA --run RUN_ID --attempt N [--aapt PATH]}"
shift
SOURCE="" RUN="" ATTEMPT="" AAPT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --run) RUN="$2"; shift 2 ;;
    --attempt) ATTEMPT="$2"; shift 2 ;;
    --aapt) AAPT="$2"; shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
[[ -n "$SOURCE" && -n "$RUN" && -n "$ATTEMPT" ]] || { echo "--source, --run and --attempt are required" >&2; exit 2; }
fail() { echo "artifact verification FAILED: $*" >&2; exit 1; }

EXPECTED="SHA256SUMS apk-metadata.txt artifact-receipt.txt build-receipt.txt librose_chat_android.so rose-models-unsigned.apk"
[[ -d "$DIR" && ! -L "$DIR" ]] || fail "$DIR is not a directory"
ACTUAL="$(cd "$DIR" && find . -mindepth 1 -printf '%P\n' | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"
[[ "$ACTUAL" == "$EXPECTED" ]] || fail "inventory is [$ACTUAL], expected [$EXPECTED]"
for f in $EXPECTED; do
  [[ -f "$DIR/$f" && ! -L "$DIR/$f" ]] || fail "$f is not a regular file"
done

LISTED="$(awk '{print $2}' "$DIR/SHA256SUMS" | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')"
[[ "$LISTED" == "apk-metadata.txt artifact-receipt.txt build-receipt.txt librose_chat_android.so rose-models-unsigned.apk" ]] \
  || fail "SHA256SUMS lists [$LISTED]"
(cd "$DIR" && sha256sum --check --strict --quiet SHA256SUMS) || fail "SHA256SUMS mismatch"

field() { sed -n "s/^$1: //p" "$DIR/artifact-receipt.txt"; }
[[ "$(field schema)" == "rose-models-unsigned-artifact/1" ]] || fail "unknown receipt schema"
[[ "$(field source_sha)" == "$SOURCE" ]] || fail "receipt source $(field source_sha), expected $SOURCE"
[[ "$(field github_sha)" == "$SOURCE" ]] || fail "receipt github_sha $(field github_sha), expected $SOURCE"
[[ "$(field github_run_id)" == "$RUN" ]] || fail "receipt run $(field github_run_id), expected $RUN"
[[ "$(field github_run_attempt)" == "$ATTEMPT" ]] || fail "receipt attempt $(field github_run_attempt), expected $ATTEMPT"
[[ "$(field source_dirty_models_files)" == "0" ]] || fail "artifact was built from uncommitted Models files"

APK_SHA="$(sha256sum "$DIR/rose-models-unsigned.apk" | cut -d' ' -f1)"
SO_SHA="$(sha256sum "$DIR/librose_chat_android.so" | cut -d' ' -f1)"
[[ "$(field apk_sha256)" == "$APK_SHA" ]] || fail "receipt apk_sha256 differs from the APK bytes"
[[ "$(field native_sha256)" == "$SO_SHA" ]] || fail "receipt native_sha256 differs from the library bytes"
LIBS="$(unzip -Z1 "$DIR/rose-models-unsigned.apk" | grep -E '^lib/' || true)"
[[ "$LIBS" == "lib/arm64-v8a/librose_chat_android.so" ]] || fail "APK native entries: ${LIBS:-none}"
IN_APK="$(unzip -p "$DIR/rose-models-unsigned.apk" lib/arm64-v8a/librose_chat_android.so | sha256sum | cut -d' ' -f1)"
[[ "$IN_APK" == "$SO_SHA" ]] || fail "library inside the APK ($IN_APK) is not the staged library ($SO_SHA)"
if unzip -Z1 "$DIR/rose-models-unsigned.apk" | grep -qiE '^META-INF/.*\.(RSA|DSA|EC|SF)$'; then
  fail "APK carries signature files"
fi
grep -q "apk_sha256: $APK_SHA" "$DIR/build-receipt.txt" || fail "build-receipt.txt names another APK"

if [[ -n "$AAPT" ]]; then
  BADGING="$("$AAPT" dump badging "$DIR/rose-models-unsigned.apk")" || fail "aapt could not parse the APK"
  [[ "$BADGING" == "$(cat "$DIR/apk-metadata.txt")" ]] || fail "apk-metadata.txt is not the APK's badging"
fi
grep -q "^package: name='dev.pixel.models' " "$DIR/apk-metadata.txt" || fail "package is not dev.pixel.models"
[[ "$(grep -c '^uses-permission:' "$DIR/apk-metadata.txt")" == 1 ]] \
  && grep -q "^uses-permission: name='android.permission.INTERNET'$" "$DIR/apk-metadata.txt" \
  || fail "permissions are not exactly INTERNET"
grep -q "^native-code: 'arm64-v8a'$" "$DIR/apk-metadata.txt" || fail "native-code is not exactly arm64-v8a"

echo "artifact verified: 6 files; apk $APK_SHA; native $SO_SHA; source $SOURCE; run $RUN/$ATTEMPT"
