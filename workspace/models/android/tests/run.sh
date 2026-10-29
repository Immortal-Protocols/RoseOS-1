#!/usr/bin/env bash
# JVM tests for the Rose Models shell policy. Compiles only the Android-free classes, so it needs
# a JDK but no Android SDK, device or emulator.
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
PURE=(Effect AgentEngine LaunchResult AppLauncher InferenceClient TurnView TurnController
      AppAllowlist EndpointPolicy FakeEngine FakeInferenceClient AllowlistLauncher)
SOURCES=()
for c in "${PURE[@]}"; do SOURCES+=("$APP/src/dev/pixel/models/$c.java"); done
# Fail if a class meant to be platform-free imports android.*.
if grep -l '^import android\.' "${SOURCES[@]}"; then
  echo "platform-free class imports android.*" >&2
  exit 1
fi
javac -encoding UTF-8 -source 8 -target 8 -Xlint:-options -d "$OUT" \
  "${SOURCES[@]}" "$APP"/tests/dev/pixel/models/*.java
java -cp "$OUT" dev.pixel.models.ShellPolicyTest
