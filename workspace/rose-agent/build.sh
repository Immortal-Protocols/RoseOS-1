#!/usr/bin/env bash
# Build Rose Agent without reading a keystore or modifying its source tree.
set -euo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$APP/../.." && pwd)"
SDK="${ANDROID_SDK_ROOT:-$ROOT/.tools/android-sdk}"
BT="$SDK/build-tools/android-15"
JAVA="${JAVA_HOME:+$JAVA_HOME/bin/}java"
# ZXing core decodes the pairing QR code (LocalRosePairActivity); fetched by scripts/setup-zxing.sh.
ZXING="$ROOT/.tools/zxing/core-3.5.4.jar"
ZXING_SHA256=71de5d89341b5fcf5dd89da7f44e84d825d0e084cdf3ec77c9abe26b0f0ceb13
if [[ ! -f "$ZXING" ]]; then
  printf 'Missing build input: %s\nRun bash scripts/setup-zxing.sh (or scripts/bootstrap.sh).\n' "$ZXING" >&2; exit 1
fi
if command -v sha256sum >/dev/null; then ZXING_ACTUAL="$(sha256sum "$ZXING")"; else ZXING_ACTUAL="$(shasum -a 256 "$ZXING")"; fi
[[ "${ZXING_ACTUAL%% *}" == "$ZXING_SHA256" ]] || {
  printf 'ZXing jar checksum mismatch: %s\nDelete it and run bash scripts/setup-zxing.sh.\n' "$ZXING" >&2; exit 1; }
for file in "$BT/aapt" "$BT/d8" "$SDK/platforms/android-35/android.jar" \
  "$APP/app/assets/connection.json" \
  "$ROOT/workspace/pixel-clipboard/clipboard-connection.json" \
  "$APP/app/res/raw/mac_screen_cert.pem" "$APP/app/res/xml/mac_screen_network.xml" \
  "$APP/server/node_modules/.bin/esbuild"; do
  [[ -f "$file" ]] || { printf 'Missing build input: %s\nSee docs/APP-BUILD.md.\n' "$file" >&2; exit 1; }
done
mkdir -p "$ROOT/out"
OUT="$(mktemp -d "$ROOT/out/rose-agent.XXXXXX")"
cp -R "$APP/app" "$OUT/app"
STAGE="$OUT/app"
# aapt regenerates these; the upstream handoff included a stale Manifest.java.
rm -f "$STAGE/src/Manifest.java" "$STAGE/src/R.java"
# Retired input (host-app-build.RETIRED_ASSETS): nothing reads it, so a stale local copy is never packaged.
rm -f "$STAGE/assets/mac-screen-connection.json"
mkdir -p "$OUT/classes" "$OUT/generated" "$OUT/dex"
cp "$ROOT/workspace/pixel-clipboard/ClipboardSync.java" "$STAGE/src/dev/pixel/voice/"
cp "$ROOT/workspace/pixel-clipboard/clipboard-connection.json" "$STAGE/assets/"
node "$ROOT/scripts/check-public-assets.mjs" "$STAGE/assets"
cp "$ROOT/workspace/experiments/phone-command-push/delivery.js" "$STAGE/assets/phone-command-delivery.js"
cp "$APP/glasses-context/GlassesPhotoProvider.java" "$STAGE/src/dev/pixel/voice/"
cp "$APP/media-worker/src/dev/pixel/voice/"*.java "$STAGE/src/dev/pixel/voice/"
cp "$APP/clock-worker/src/ClockControlRoutine.java" "$STAGE/src/dev/pixel/voice/"
# Project 2: Models' on-phone composer core (workspace/models/local-composer/java), which the workspace page calls.
# Its scripted test double is not packaged; there is no model runtime or weights in this build.
mkdir -p "$STAGE/src/rose/models/composer"
cp "$ROOT/workspace/models/local-composer/java/src/rose/models/composer/"*.java "$STAGE/src/rose/models/composer/"
rm -f "$STAGE/src/rose/models/composer/ScriptedInference.java"
cp "$APP/messages-worker/messages-tool.js" "$APP/media-worker/media-tool.js" "$APP/clock-worker/clock-tool.js" "$STAGE/assets/"
cp "$ROOT/workspace/experiments/guardian-voice/client.js" "$STAGE/assets/guardian-voice.js"
for name in luna-first output-boost voice-providers voice-commands; do
  cp "$APP/voice-providers/$name.js" "$STAGE/assets/"
done
"$APP/server/node_modules/.bin/esbuild" "$APP/server/eleven-entry.js" \
  --bundle --format=iife --platform=browser --minify --outfile="$STAGE/assets/eleven-client.js"
"$BT/aapt" package -f -M "$STAGE/AndroidManifest.xml" -S "$STAGE/res" \
  -A "$STAGE/assets" -I "$SDK/platforms/android-35/android.jar" -F "$OUT/resources.apk" -J "$OUT/generated"
SOURCES=(); while IFS= read -r f; do SOURCES+=("$f"); done < <(find "$STAGE/src" "$OUT/generated" -name '*.java' -type f | sort)
"$JAVA" -m jdk.compiler/com.sun.tools.javac.Main -encoding UTF-8 -source 8 -target 8 \
  -Xlint:-options -bootclasspath "$SDK/platforms/android-35/android.jar:$BT/core-lambda-stubs.jar" \
  -classpath "$ZXING" -d "$OUT/classes" "${SOURCES[@]}"
CLASSES=(); while IFS= read -r f; do CLASSES+=("$f"); done < <(find "$OUT/classes" -name '*.class' -type f | sort)
"$BT/d8" --min-api 29 --lib "$SDK/platforms/android-35/android.jar" --output "$OUT/dex" "${CLASSES[@]}" "$ZXING"
cp "$OUT/resources.apk" "$OUT/unaligned.apk"
(cd "$OUT/dex" && zip -q "$OUT/unaligned.apk" classes*.dex)
"$BT/zipalign" -f 4 "$OUT/unaligned.apk" "$OUT/rose-agent-unsigned.apk"
"$BT/zipalign" -c 4 "$OUT/rose-agent-unsigned.apk"
unzip -t "$OUT/rose-agent-unsigned.apk" > "$OUT/zip-check.txt"
"$BT/aapt" dump badging "$OUT/rose-agent-unsigned.apk" > "$OUT/apk-metadata.txt"
(cd "$OUT" && sha256sum rose-agent-unsigned.apk > SHA256SUMS)
git -C "$ROOT" rev-parse HEAD > "$OUT/source-revision.txt"
git -C "$ROOT" status --porcelain > "$OUT/source-status.txt"
"$JAVA" -version 2> "$OUT/java-version.txt"
printf 'Unsigned APK: %s\n' "$OUT/rose-agent-unsigned.apk"
