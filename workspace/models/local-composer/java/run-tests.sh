#!/usr/bin/env bash
# Compile the Java composer core at the Rose Agent source level (Java 8) and run its offline tests against the
# shared vectors. Writes only java/out/ (ignored). No Android SDK, runtime, weights or device needed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/out/classes"
rm -rf "$HERE/out"
mkdir -p "$OUT"
javac --release 8 -encoding UTF-8 -Xlint:all,-options -Werror -d "$OUT" \
  $(find "$HERE/src" "$HERE/test" -name '*.java' | sort)
java -cp "$OUT" rose.models.composer.ComposerTests "$HERE/../contract/vectors/v1.json"
