#!/usr/bin/env bash
# rose-auth-v1 Java client core: compile at the Java 8 source level (no android.*) and run the shared vectors.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$out" \
  "$here/src/dev/pixel/roseauth/RoseAuthCore.java" "$here/tests/RoseAuthCoreTest.java"
if grep -n 'import android\.' "$here/src/dev/pixel/roseauth/"*.java; then echo "android.* in the pure core" >&2; exit 1; fi
java -cp "$out" dev.pixel.roseauth.RoseAuthCoreTest "$here/../vectors/rose-auth-v1.txt"
