#!/usr/bin/env bash
# Linux/macOS unsigned build. Never creates or reads a signing key.
set -euo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$APP/../.." && pwd)"
SDK="${ANDROID_SDK_ROOT:-$ROOT/.tools/android-sdk}"
BT="$SDK/build-tools/android-15"
ANDROID_JAR="$SDK/platforms/android-35/android.jar"
mkdir -p "$ROOT/out"
OUT="$(mktemp -d "$ROOT/out/rose-builder.XXXXXX")"
JAVA="${JAVA_HOME:+$JAVA_HOME/bin/}java"
mkdir -p "$OUT/classes" "$OUT/generated" "$OUT/dex"
"$BT/aapt" package -f -M "$APP/AndroidManifest.xml" -S "$APP/res" \
  -I "$ANDROID_JAR" -F "$OUT/resources.apk" -J "$OUT/generated"
# Rose Agent's rose-auth-v1 signer and key code, compiled in unchanged so both apps sign the same bytes (RoseLink).
AGENT="$ROOT/workspace/rose-agent/app/src/dev/pixel/voice"
# The builder screens now live in Rose Agent (workspace/rose-agent/app/src/dev/pixel/builder); this standalone app,
# kept until the Agent's builder replaces it, compiles them from there with its own pairing (MainActivity, Pairing).
# Only the Agent's own entry (BuilderActivity, AgentEndpoint) is left out: it rides the Agent's pairing.
SCREENS="$ROOT/workspace/rose-agent/app/src/dev/pixel/builder"
SOURCES=()
while IFS= read -r source_path; do SOURCES+=("$source_path"); done < <({ find "$APP/src" "$OUT/generated" -name '*.java' -type f; find "$SCREENS" -name '*.java' -type f ! -name BuilderActivity.java ! -name AgentEndpoint.java; printf '%s\n' "$AGENT/LocalRoseAuth.java" "$AGENT/LocalRoseKey.java"; } | sort)
"$JAVA" -m jdk.compiler/com.sun.tools.javac.Main -encoding UTF-8 -source 8 -target 8 \
  -Xlint:-options -bootclasspath "$ANDROID_JAR:$BT/core-lambda-stubs.jar" \
  -d "$OUT/classes" "${SOURCES[@]}"
CLASSES=()
while IFS= read -r class_path; do CLASSES+=("$class_path"); done < <(find "$OUT/classes" -name '*.class' -type f | sort)
"$BT/d8" --min-api 26 --lib "$ANDROID_JAR" --output "$OUT/dex" "${CLASSES[@]}"
cp "$OUT/resources.apk" "$OUT/unaligned.apk"
(cd "$OUT/dex" && zip -q "$OUT/unaligned.apk" classes*.dex)
"$BT/zipalign" -f 4 "$OUT/unaligned.apk" "$OUT/rose-builder-unsigned.apk"
"$BT/zipalign" -c 4 "$OUT/rose-builder-unsigned.apk"
"$BT/aapt" dump badging "$OUT/rose-builder-unsigned.apk" > "$OUT/apk-metadata.txt"
sha256sum "$OUT/rose-builder-unsigned.apk" > "$OUT/SHA256SUMS"
"$JAVA" -version 2> "$OUT/java-version.txt"
printf 'Unsigned APK: %s\n' "$OUT/rose-builder-unsigned.apk"
