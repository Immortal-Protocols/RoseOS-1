#!/usr/bin/env bash
# Repeatable local app tool setup. No cloud auth, key generation, or service startup.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
for tool in git python3 curl unzip zip sha256sum node npm java; do
 command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done
java -m jdk.compiler/com.sun.tools.javac.Main -version
node -e 'if(Number(process.versions.node.split(".")[0])<22)process.exit(1)'
bash scripts/setup-android-sdk.sh
bash scripts/setup-zxing.sh
npm ci --prefix workspace/rose-agent/server --ignore-scripts --cache "$ROOT/.tools/npm-cache" --no-audit --no-fund
printf 'Local tool setup complete. See docs/APP-BUILD.md for public endpoint configuration.\n'
