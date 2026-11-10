#!/usr/bin/env bash
# Offline only. Synthetic execution/phone-note receipts and host-JVM lifecycle.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHONPATH=prototypes/voice-phone-demo python3 -m unittest rose.tests.test_life rose.tests.test_life_integration
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
SRC=workspace/rose-agent/app/src/dev/pixel/voice
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$OUT" \
 "$SRC/BackgroundActivity.java" "$SRC/WorkspaceOverview.java" "$SRC/MiniJson.java" "$SRC/WorkspaceOverviewFeed.java" \
 workspace/rose-agent/app/tests/BackgroundActivityTest.java
java -cp "$OUT" dev.pixel.voice.BackgroundActivityTest
