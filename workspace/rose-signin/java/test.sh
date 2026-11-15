#!/usr/bin/env bash
# rose-signin-v1 Java client core: compile at the Java 8 source level (no android.*) and run the shared vectors.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$out" \
  "$here/src/dev/pixel/rosesignin/RoseSigninCore.java" "$here/tests/RoseSigninCoreTest.java"
if grep -n 'import android\.' "$here/src/dev/pixel/rosesignin/"*.java; then echo "android.* in the pure core" >&2; exit 1; fi
java -cp "$out" dev.pixel.rosesignin.RoseSigninCoreTest "$here/../vectors/rose-signin-v1.txt"
# Name guard: rose-signin-v1 (this protocol) never reuses rose-auth-v1, the installed phone<->server signature prefix,
# and that prefix stays exactly where it is defined (a blanket rename would break paired phones).
root="$here/../../.."
if grep -rn 'rose-auth-v1' "$here/.." --include='*.rs' --include='*.java' --include='*.toml' --include='*.txt'; then
  echo "rose-signin must not use the rose-auth-v1 wire prefix" >&2; exit 1
fi
grep -q 'AUTH_PREFIX = "rose-auth-v1"' "$root/prototypes/voice-phone-demo/rose/remote.py"
grep -q 'VERSION = "rose-auth-v1"' "$root/workspace/rose-agent/app/src/dev/pixel/voice/LocalRoseAuth.java"
grep -q "'AUTH_PREFIX': 'rose-auth-v1'" "$root/workspace/models/rose_cloud/rose_cloud/contract.py"
echo "name guard: rose-signin-v1 distinct; rose-auth-v1 transport prefix unchanged"
