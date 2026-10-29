#!/usr/bin/env bash
# Negative controls for verify-artifact.sh: each case damages a temporary copy of a verified
# artifact directory and requires the verifier to reject it. The original directory is untouched.
#
#   artifact-negative.sh DIR VERIFY_ARGS...   (the same arguments that verified DIR)
set -euo pipefail
DIR="${1:?usage: artifact-negative.sh DIR VERIFY_ARGS...}"
shift
VERIFY="$(cd "$(dirname "$0")" && pwd)/verify-artifact.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Positive control first: an undamaged copy must pass with these exact arguments, so every
# rejection below comes from the damage, not from the arguments.
cp -a "$DIR" "$WORK/undamaged"
bash "$VERIFY" "$WORK/undamaged" "$@" >/dev/null || { echo "positive control failed" >&2; exit 1; }
echo "accepted as expected: undamaged copy"

expect_reject() {
  local name="$1" setup="$2" copy="$WORK/$1"
  shift 2
  cp -a "$DIR" "$copy"
  (cd "$copy" && eval "$setup")
  if bash "$VERIFY" "$copy" "$@" >/dev/null 2>"$WORK/$name.err"; then
    echo "negative control NOT rejected: $name" >&2
    exit 1
  fi
  echo "rejected as expected: $name ($(tail -1 "$WORK/$name.err"))"
}

rehash() { echo 'sha256sum rose-models-unsigned.apk librose_chat_android.so apk-metadata.txt build-receipt.txt artifact-receipt.txt > SHA256SUMS'; }
expect_reject apk-byte-flipped 'printf "\x00" | dd of=rose-models-unsigned.apk bs=1 seek=100 conv=notrunc status=none' "$@"
expect_reject library-missing 'rm librose_chat_android.so' "$@"
expect_reject extra-file 'echo x > notes.txt' "$@"
expect_reject library-swapped-and-rehashed "printf 'not the packaged library' > librose_chat_android.so; sed -i \"s/^native_sha256: .*/native_sha256: \$(sha256sum librose_chat_android.so | cut -d' ' -f1)/\" artifact-receipt.txt; $(rehash)" "$@"
expect_reject wrong-source "sed -i 's/^source_sha: .*/source_sha: 0000000000000000000000000000000000000000/' artifact-receipt.txt; $(rehash)" "$@"
expect_reject symlink 'rm apk-metadata.txt && ln -s build-receipt.txt apk-metadata.txt' "$@"
echo "negative controls: 6 of 6 rejected"
