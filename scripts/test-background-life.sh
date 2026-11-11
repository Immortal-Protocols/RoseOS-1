#!/usr/bin/env bash
# Offline only. Synthetic execution/server-note receipts and host-JVM lifecycle.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHONPATH=prototypes/voice-phone-demo python3 -m unittest rose.tests.test_life_world rose.tests.test_life_apps rose.tests.test_life rose.tests.test_life_integration rose.tests.test_life_executor rose.tests.test_life_notebook
node --test workspace/rose-agent/server/life-research.test.mjs
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
SRC=workspace/rose-agent/app/src/dev/pixel/voice
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$OUT" \
 "$SRC/BackgroundActivity.java" "$SRC/WorkspaceOverview.java" "$SRC/MiniJson.java" "$SRC/WorkspaceOverviewFeed.java" \
 workspace/rose-agent/app/tests/BackgroundActivityTest.java
java -cp "$OUT" dev.pixel.voice.BackgroundActivityTest
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$OUT/vision-input" \
 os/background-control/agent-pip/src/dev/pixel/backgroundcontrol/VisionInput.java os/background-control/tests/VisionInputTest.java
java -cp "$OUT/vision-input" dev.pixel.backgroundcontrol.VisionInputTest
python3 os/background-control/tests/test_vision_input_wiring.py
python3 os/background-control/tests/test_multi_session_wiring.py
