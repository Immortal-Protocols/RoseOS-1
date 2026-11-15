#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK="${ANDROID_SDK_ROOT:-$ROOT/.tools/android-sdk}"
case "$(uname -s)" in
  Linux)
    BUILD_TOOLS_ARCHIVE=build-tools_r35_linux.zip
    BUILD_TOOLS_SHA256=bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88
    ;;
  Darwin)
    BUILD_TOOLS_ARCHIVE=build-tools_r35_macosx.zip
    BUILD_TOOLS_SHA256=530cdbd1ec315e1477624d7ed2f0f2962108d69f36eddba5894cef9ea2cedb48
    ;;
  *) echo 'Android SDK bootstrap supports Linux and macOS.' >&2; exit 1 ;;
esac
mkdir -p "$SDK/downloads" "$SDK/platforms" "$SDK/build-tools"
fetch() {
  local name="$1" hash="$2" destination="$3"
  local archive="$SDK/downloads/$name"
  if [[ ! -f "$archive" ]]; then
    curl --fail --location --retry 2 "https://dl.google.com/android/repository/$name" --output "$archive"
  fi
  # macOS's sha256sum has no GNU --status option. Compare the same digest
  # directly on both hosts, before any archive is unpacked.
  local actual
  actual="$(sha256sum "$archive")"
  [[ "${actual%% *}" == "$hash" ]] || { echo "SDK checksum mismatch: $name" >&2; exit 1; }
  unzip -qo "$archive" -d "$destination"
}
fetch platform-35_r02.zip 0988cacad01b38a18a47bac14a0695f246bc76c1b06c0eeb8eb0dc825ab0c8e0 "$SDK/platforms"
fetch "$BUILD_TOOLS_ARCHIVE" "$BUILD_TOOLS_SHA256" "$SDK/build-tools"
printf 'Android SDK ready: %s\n' "$SDK"
