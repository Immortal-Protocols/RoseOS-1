#!/bin/bash
# One-job runner for the dedicated Mustang VM. No registration token is stored
# in this file. The JIT config is written straight to a 0600 file and removed
# on every exit. After that write, the guest marks the key fetched so the
# hosted job can delete the instance metadata.
set -euo pipefail
umask 077
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
find "$install" -maxdepth 1 -name 'jit.*' -type f -delete
file=$(mktemp "$install/jit.XXXXXX")
cleanup() {
  rm -f "$file" "$install/.runner" "$install/.credentials" "$install/.credentials_rsaparams" "$install/.path"
}
trap cleanup EXIT
attribute=http://metadata.google.internal/computeMetadata/v1/instance/attributes/mustang-jitconfig
deadline=$((SECONDS + 720))
while [[ ! -s "$file" && SECONDS -lt deadline ]]; do
  if ! curl --fail --silent --show-error --max-time 5 -H 'Metadata-Flavor: Google' --output "$file" "$attribute"; then
    : > "$file"
    sleep 10
  fi
done
if [[ ! -s "$file" ]]; then
  exit 0
fi
chmod 600 "$file"
curl --fail --silent --show-error --max-time 5 -X PUT -H 'Metadata-Flavor: Google' --data '1' \
  http://metadata.google.internal/computeMetadata/v1/instance/guest-attributes/mustang/jit-fetched
cd "$install"
status=0
./run.sh --jitconfig "$file" || status=$?
exit "$status"
