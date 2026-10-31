#!/usr/bin/env bash
# ZXing core (Apache-2.0) for Rose Agent's pairing QR scanner, pinned by version and SHA-256.
# Already-present jars are only verified, never re-downloaded.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/.tools/zxing"
JAR="$DIR/core-3.5.4.jar"
URL="https://repo1.maven.org/maven2/com/google/zxing/core/3.5.4/core-3.5.4.jar"
SHA256=71de5d89341b5fcf5dd89da7f44e84d825d0e084cdf3ec77c9abe26b0f0ceb13
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi; }
mkdir -p "$DIR"
if [[ ! -f "$JAR" ]]; then
  curl --fail --location --retry 2 "$URL" --output "$JAR.part"
  actual="$(sha "$JAR.part")"
  if [[ "${actual%% *}" != "$SHA256" ]]; then rm -f "$JAR.part"; echo "ZXing download checksum mismatch" >&2; exit 1; fi
  mv "$JAR.part" "$JAR"
fi
actual="$(sha "$JAR")"
[[ "${actual%% *}" == "$SHA256" ]] || { echo "ZXing checksum mismatch: $JAR (delete it and rerun)" >&2; exit 1; }
printf 'ZXing ready: %s\n' "$JAR"
