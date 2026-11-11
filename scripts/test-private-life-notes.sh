#!/usr/bin/env bash
# Source/host tests only: synthetic private files, no Android build, provider or phone.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
SRC=workspace/rosie-notes/src/dev/pixel/rosienotes
java -m jdk.compiler/com.sun.tools.javac.Main -source 8 -target 8 -Xlint:-options -d "$OUT" \
 "$SRC/LifeNoteStore.java" "$SRC/MiniJson.java" "$SRC/WorkspaceBinding.java" workspace/rosie-notes/tests/LifeNoteStoreTest.java workspace/rosie-notes/tests/WorkspaceBindingTest.java
java -cp "$OUT" dev.pixel.rosienotes.LifeNoteStoreTest
java -cp "$OUT" dev.pixel.rosienotes.WorkspaceBindingTest
python3 -I workspace/rosie-notes/tests/life_note_wire_test.py
