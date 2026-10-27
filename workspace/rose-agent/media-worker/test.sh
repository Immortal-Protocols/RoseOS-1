#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
node --test "$ROOT/rose-agent/media-worker/tests/media.test.mjs"
TEST_DIR=$(mktemp -d /tmp/pixel-media-test.XXXXXX)
trap 'rm -rf "$TEST_DIR"' EXIT
"$ROOT/tools/jdk/Contents/Home/bin/javac" -d "$TEST_DIR" "$ROOT/rose-agent/media-worker/src/dev/pixel/voice/MediaSnapshot.java" "$ROOT/rose-agent/media-worker/tests/MediaSnapshotTest.java"
"$ROOT/tools/jdk/Contents/Home/bin/java" -cp "$TEST_DIR" dev.pixel.voice.MediaSnapshotTest
