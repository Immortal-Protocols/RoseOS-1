#!/usr/bin/env bash
# The pinned Gradle stack ordinary Gradle app projects build with (docs/APP-BUILDER.md §2.4): JDK, Gradle
# distribution, SDK packages and the read-only offline dependency cache, as rose.app_builder.gradle_stack pins them.
# Every archive is SHA-256-checked before it is unpacked; the network is used only here, to provision. A stack that
# is already current is only checked. Prints the builder's `gradle` config object for it.
#   ROSE_GRADLE_STACK      where the stack goes (default .tools/gradle-stack)
#   ROSE_GRADLE_DOWNLOADS  where the archives are kept between runs (default .tools/gradle-downloads)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STACK="${ROSE_GRADLE_STACK:-$ROOT/.tools/gradle-stack}"
DOWNLOADS="${ROSE_GRADLE_DOWNLOADS:-$ROOT/.tools/gradle-downloads}"
case "$(uname -s)" in
  Linux|Darwin) ;;
  *) echo 'The Gradle stack supports Linux and macOS.' >&2; exit 1 ;;
esac
cd "$ROOT/prototypes/voice-phone-demo"
python3 -m rose.app_builder.gradle_prepare stack --out "$STACK" --downloads "$DOWNLOADS"
printf 'Gradle stack ready: %s\n' "$STACK" >&2
