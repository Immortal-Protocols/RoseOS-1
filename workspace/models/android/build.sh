#!/usr/bin/env bash
# Unsigned Rose Models APK (dev.pixel.models). Never creates, reads or uses a signing key; the
# output is not installable until Infra signs an admitted artifact.
#
#   bash workspace/models/android/build.sh                      # Java shell only (FAKE engine)
#   ROSE_MODELS_NATIVE_LIB=path/to/librose_chat_android.so bash workspace/models/android/build.sh
#
# A native library, when given, is stored uncompressed at lib/arm64-v8a/ and the APK is aligned
# for 16 KiB pages. Output goes to an ignored out/rose-models.* directory with a build receipt.
set -euo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$APP/../../.." && pwd)"
SDK="${ANDROID_SDK_ROOT:-$ROOT/.tools/android-sdk}"
BT="$SDK/build-tools/android-15"
ANDROID_JAR="$SDK/platforms/android-35/android.jar"
for f in "$BT/aapt" "$BT/d8" "$BT/zipalign" "$ANDROID_JAR"; do
  [[ -e "$f" ]] || { echo "Missing Android SDK file: $f (run scripts/setup-android-sdk.sh)" >&2; exit 1; }
done
mkdir -p "$ROOT/out"
OUT="$(mktemp -d "$ROOT/out/rose-models.XXXXXX")"
JAVA="${JAVA_HOME:+$JAVA_HOME/bin/}java"
mkdir -p "$OUT/classes" "$OUT/generated" "$OUT/dex"

"$BT/aapt" package -f -M "$APP/AndroidManifest.xml" -S "$APP/res" -I "$ANDROID_JAR" \
  -F "$OUT/resources.apk" -J "$OUT/generated"
mapfile -t SOURCES < <(find "$APP/src" "$OUT/generated" -name '*.java' -type f | sort)
"$JAVA" -m jdk.compiler/com.sun.tools.javac.Main -encoding UTF-8 -source 8 -target 8 \
  -Xlint:-options -bootclasspath "$ANDROID_JAR:$BT/core-lambda-stubs.jar" \
  -d "$OUT/classes" "${SOURCES[@]}"
mapfile -t CLASSES < <(find "$OUT/classes" -name '*.class' -type f | sort)
"$BT/d8" --min-api 29 --lib "$ANDROID_JAR" --output "$OUT/dex" "${CLASSES[@]}"

cp "$OUT/resources.apk" "$OUT/unaligned.apk"
(cd "$OUT/dex" && zip -q "$OUT/unaligned.apk" classes*.dex)
NATIVE="none"
if [[ -n "${ROSE_MODELS_NATIVE_LIB:-}" ]]; then
  mkdir -p "$OUT/native/lib/arm64-v8a"
  cp "$ROSE_MODELS_NATIVE_LIB" "$OUT/native/lib/arm64-v8a/librose_chat_android.so"
  (cd "$OUT/native" && zip -q -0 "$OUT/unaligned.apk" lib/arm64-v8a/librose_chat_android.so)
  NATIVE="$(sha256sum "$OUT/native/lib/arm64-v8a/librose_chat_android.so" | cut -d' ' -f1)"
fi
"$BT/zipalign" -P 16 -f 4 "$OUT/unaligned.apk" "$OUT/rose-models-unsigned.apk"
"$BT/zipalign" -c -P 16 4 "$OUT/rose-models-unsigned.apk"
"$BT/aapt" dump badging "$OUT/rose-models-unsigned.apk" > "$OUT/apk-metadata.txt"
(cd "$OUT" && sha256sum rose-models-unsigned.apk > SHA256SUMS)

REV="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
TREE="$(git -C "$ROOT" rev-parse --verify -q HEAD:workspace/models/android || echo "not committed")"
DIRTY="$(git -C "$ROOT" status --porcelain -- workspace/models/android | wc -l | tr -d ' ')"
{
  echo "artifact: rose-models-unsigned.apk (UNSIGNED, not installable, not shipped)"
  echo "apk_sha256: $(cut -d' ' -f1 "$OUT/SHA256SUMS")"
  echo "source_rev: $REV"
  echo "android_tree: $TREE (uncommitted files in workspace/models/android: $DIRTY)"
  echo "native_lib_arm64_sha256: $NATIVE"
  echo "engine: $([[ "$NATIVE" == none ]] && echo 'FAKE (local fixture), not a model' || echo 'native library present')"
  echo "build_tools: $(basename "$BT"); platform: android-35; d8 --min-api 29"
  echo "javac: $("$JAVA" -version 2>&1 | grep -v JAVA_TOOL_OPTIONS | head -1)"
  grep -E "^(package|sdkVersion|targetSdkVersion|uses-permission|native-code|application-label):" "$OUT/apk-metadata.txt"
} > "$OUT/build-receipt.txt"
cat "$OUT/build-receipt.txt"
printf 'Unsigned APK: %s\n' "$OUT/rose-models-unsigned.apk"
