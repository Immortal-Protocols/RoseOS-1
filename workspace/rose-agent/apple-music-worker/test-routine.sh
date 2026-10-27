#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
JDK="$ROOT/tools/jdk/Contents/Home/bin"
TEST_DIR=$(mktemp -d /tmp/pixel-apple-routine.XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT
# Host-side org.json for exercising the real Android routine with fake UI/time.
curl -fsSL https://repo.maven.apache.org/maven2/org/json/json/20240303/json-20240303.jar -o "$TEST_DIR/json.jar"
printf '%s  %s\n' 3cf6cd6892e32e2b4c1c39e0f52f5248a2f5b37646fdfbb79a66b46b618414ed "$TEST_DIR/json.jar" | shasum -a 256 -c -
"$JDK/javac" -cp "$TEST_DIR/json.jar" -d "$TEST_DIR/classes" \
 "$ROOT/rose-agent/app/src/dev/pixel/voice/AppleMusicRoutine.java" \
 "$ROOT/rose-agent/app/src/dev/pixel/voice/AppleMusicMatch.java" \
 "$ROOT/rose-agent/app/src/dev/pixel/voice/AppleMusicRetry.java" \
 "$ROOT/rose-agent/apple-music-worker/AppleMusicRetryTest.java" \
 "$ROOT/rose-agent/apple-music-worker/routine-test/android/os/Handler.java" \
 "$ROOT/rose-agent/apple-music-worker/routine-test/android/os/Looper.java" \
 "$ROOT/rose-agent/apple-music-worker/routine-test/android/os/SystemClock.java" \
 "$ROOT/rose-agent/apple-music-worker/routine-test/dev/pixel/voice/VoiceService.java" \
 "$ROOT/rose-agent/apple-music-worker/routine-test/dev/pixel/voice/PhoneControl.java" \
 "$ROOT/rose-agent/apple-music-worker/routine-test/dev/pixel/voice/AppleMusicRoutineTest.java"
"$JDK/java" -cp "$TEST_DIR/classes:$TEST_DIR/json.jar" dev.pixel.voice.AppleMusicRetryTest
"$JDK/java" -cp "$TEST_DIR/classes:$TEST_DIR/json.jar" dev.pixel.voice.AppleMusicRoutineTest
