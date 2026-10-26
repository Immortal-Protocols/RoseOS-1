#!/usr/bin/env bash
# Linux unsigned build. Never creates or reads a signing key.
set -euo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$APP/../.." && pwd)"
SDK="${ANDROID_SDK_ROOT:-$ROOT/.tools/android-sdk}"
BT="$SDK/build-tools/android-15"
ANDROID_JAR="$SDK/platforms/android-35/android.jar"
mkdir -p "$ROOT/out"
OUT="$(mktemp -d "$ROOT/out/calculator.XXXXXX")"
JAVA="${JAVA_HOME:+$JAVA_HOME/bin/}java"
mkdir -p "$OUT/classes" "$OUT/generated" "$OUT/dex"
"$BT/aapt" package -f -M "$APP/AndroidManifest.xml" -S "$APP/res" \
  -A "$APP/src/main/assets" -I "$ANDROID_JAR" -F "$OUT/resources.apk" -J "$OUT/generated"
mapfile -t SOURCES < <(find "$APP/src" "$OUT/generated" -name '*.java' -type f | sort)
"$JAVA" -m jdk.compiler/com.sun.tools.javac.Main -encoding UTF-8 -source 8 -target 8 \
  -Xlint:-options -bootclasspath "$ANDROID_JAR:$BT/core-lambda-stubs.jar" \
  -d "$OUT/classes" "${SOURCES[@]}"
mapfile -t CLASSES < <(find "$OUT/classes" -name '*.class' -type f | sort)
"$BT/d8" --min-api 30 --lib "$ANDROID_JAR" --output "$OUT/dex" "${CLASSES[@]}"
cp "$OUT/resources.apk" "$OUT/unaligned.apk"
(cd "$OUT/dex" && zip -q "$OUT/unaligned.apk" classes*.dex)
"$BT/zipalign" -f 4 "$OUT/unaligned.apk" "$OUT/calculator-unsigned.apk"
"$BT/zipalign" -c 4 "$OUT/calculator-unsigned.apk"
"$BT/aapt" dump badging "$OUT/calculator-unsigned.apk" > "$OUT/apk-metadata.txt"
sha256sum "$OUT/calculator-unsigned.apk" > "$OUT/SHA256SUMS"
"$JAVA" -version 2> "$OUT/java-version.txt"
printf 'Unsigned APK: %s\n' "$OUT/calculator-unsigned.apk"
