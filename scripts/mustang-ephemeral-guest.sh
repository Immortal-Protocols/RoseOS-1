#!/bin/bash
# One-job runner for the dedicated Mustang VM. Installed by the reviewed
# bootstrap; it contains no registration token. A missing metadata key exits
# without registering. The local copy of the config is removed on the way out.
set -euo pipefail
install=/mnt/disks/mustang-data/roseos/runner
source=/mnt/disks/mustang-data/roseos/source
work="$install/_work"
case "$source" in
  "$work"|"$work"/*) echo 'Mustang source cache is inside the runner work directory' >&2; exit 4;;
esac
if [[ "${MUSTANG_EPHEMERAL_CHECK:-}" == 1 ]]; then
  exit 0
fi
if [[ "$(id -u)" == 0 ]]; then
  echo 'Mustang runner must not be root' >&2
  exit 4
fi
attribute=http://metadata.google.internal/computeMetadata/v1/instance/attributes/mustang-jitconfig
deadline=$((SECONDS + 720))
config=
while [[ -z "$config" && SECONDS -lt deadline ]]; do
  config=$(curl --fail --silent --show-error --max-time 5 -H 'Metadata-Flavor: Google' "$attribute" || true)
  if [[ -z "$config" ]]; then
    sleep 10
  fi
done
if [[ -z "$config" ]]; then
  exit 0
fi
file=$(mktemp "$install/jit.XXXXXX")
chmod 600 "$file"
cleanup() { rm -f "$file"; }
trap cleanup EXIT
printf '%s' "$config" >"$file"
unset config
cd "$install"
exec ./run.sh --jitconfig "$file"
