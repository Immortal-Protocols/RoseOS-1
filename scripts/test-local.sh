#!/usr/bin/env bash
# Offline tests only: no live phone, provider, VM, or signing access.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
node --test \
 workspace/rose-agent/security/device-auth.test.mjs \
 workspace/rose-agent/security/recovery-download.test.mjs \
 workspace/rose-agent/conversation/mode.test.mjs \
 workspace/rose-agent/server/session-lease.test.mjs \
 workspace/rose-agent/server/background-app-policy.test.mjs \
 workspace/experiments/phone-command-push/push.test.mjs \
 workspace/experiments/phone-command-push/delivery.test.cjs \
 workspace/experiments/phone-control-metrics/metrics.test.mjs \
 workspace/experiments/guardian-voice/bridge.test.mjs
python3 os/background-control/tests/test_policy.py
python3 os/background-control/tests/test_stop_wiring.py
python3 os/background-control/tests/test_preinstall.py
# Pure controller logic; each test compiles with only the class it covers.
JAVA_TESTS="$(mktemp -d)"
trap 'rm -rf "$JAVA_TESTS"' EXIT
for pair in LabelTargets:agent-pip MirrorWindowFrame:agent-pip StopLedger:agent-pip \
 PanePolicy:agent-split InstallerRecoveryPolicy:agent-split; do
 name="${pair%%:*}" variant="${pair##*:}"
 java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$JAVA_TESTS/$name" \
  "os/background-control/$variant/src/dev/pixel/backgroundcontrol/$name.java" \
  "os/background-control/tests/${name}Test.java"
 java -cp "$JAVA_TESTS/$name" "dev.pixel.backgroundcontrol.${name}Test"
done
