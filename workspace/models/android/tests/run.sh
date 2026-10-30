#!/usr/bin/env bash
# JVM tests for the Rose Models host. Builds the JNI adapter for THIS machine (not arm64) so the
# tests drive Core's real local-agent-v1 agent; needs a JDK and cargo, no Android SDK or device.
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
ADAPTER="$(cd "$APP/../rose-chat-android" && pwd)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
(cd "$ADAPTER" && cargo build --locked -q)
HOST_CLASSES=(Json AgentPort NativeAgent AgentConfigJson LocalAgentHost InferenceClient AppLauncher
              LaunchResult TurnView AppAllowlist EndpointPolicy AllowlistLauncher
              EndpointCredential HttpsInferenceClient ModelsService CredentialSource DeviceKey DeviceAuth
              AuthMessages InviteLink)
SOURCES=()
for c in "${HOST_CLASSES[@]}"; do SOURCES+=("$APP/src/dev/pixel/models/$c.java"); done
# These classes must stay free of android.* so they run on a plain JVM.
if grep -l '^import android\.' "${SOURCES[@]}"; then
  echo "host class imports android.*" >&2
  exit 1
fi
javac -encoding UTF-8 -source 8 -target 8 -Xlint:-options -d "$OUT" \
  "${SOURCES[@]}" "$APP"/tests/dev/pixel/models/*.java
java -Djava.library.path="$ADAPTER/target/debug" -cp "$OUT" dev.pixel.models.HostTest
