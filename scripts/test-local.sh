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
 workspace/rose-agent/server/owner-stop.test.mjs \
 workspace/rose-agent/server/server-stop-gate.test.mjs \
 workspace/rose-agent/server/phone-task-generation.test.mjs \
 workspace/experiments/phone-command-push/push.test.mjs \
 workspace/experiments/phone-command-push/delivery.test.cjs \
 workspace/experiments/phone-control-metrics/metrics.test.mjs \
 workspace/experiments/guardian-voice/bridge.test.mjs
python3 os/background-control/tests/test_policy.py
