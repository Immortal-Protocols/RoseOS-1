#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK="${ANDROID_SDK_ROOT:-$ROOT/.tools/android-sdk}"
mkdir -p "$SDK/downloads" "$SDK/platforms" "$SDK/build-tools"
fetch() {
  local name="$1" hash="$2" destination="$3"
  local archive="$SDK/downloads/$name"
  if [[ ! -f "$archive" ]]; then
    curl --fail --location --retry 2 "https://dl.google.com/android/repository/$name" --output "$archive"
  fi
  printf '%s  %s\n' "$hash" "$archive" | sha256sum --check --status
  unzip -qo "$archive" -d "$destination"
}
fetch platform-35_r02.zip 0988cacad01b38a18a47bac14a0695f246bc76c1b06c0eeb8eb0dc825ab0c8e0 "$SDK/platforms"
fetch build-tools_r35_linux.zip bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88 "$SDK/build-tools"
printf 'Android SDK ready: %s\n' "$SDK"
