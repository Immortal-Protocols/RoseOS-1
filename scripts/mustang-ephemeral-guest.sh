#!/bin/bash
# One-job runner for the dedicated Mustang VM. No registration token is stored
# in this file. The JIT config is written straight to a 0600 file and removed
# on every exit, including when the guest-attribute acknowledgement fails.
# A metadata GET and the acknowledgement PUT are reported separately. Neither
# line includes the response body.
set -euo pipefail
umask 077
guest_root=${MUSTANG_GUEST_ROOT:-/mnt/disks/mustang-data/roseos}
install=$guest_root/runner
source=$guest_root/source
metadata=${MUSTANG_METADATA_URL:-http://metadata.google.internal/computeMetadata/v1}
attribute=$metadata/instance/attributes/mustang-jitconfig
ack=$metadata/instance/guest-attributes/mustang/jit-fetched
wait_seconds=${MUSTANG_GUEST_WAIT_SECONDS:-720}
poll_seconds=${MUSTANG_GUEST_POLL_SECONDS:-10}
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
ackfile=
cleanup() {
  rm -f "$file" "$ackfile" "$install/.runner" "$install/.credentials" \
    "$install/.credentials_rsaparams" "$install/.path"
}
trap cleanup EXIT

# Prints the HTTP status in REPLY_CODE. Network loss returns 2 and does not
# print the curl error text, which can include a URL but never a body here.
request() {
  local method=$1 url=$2 outfile=$3 data=${4-}
  local code status
  set +e
  if [[ -n $data ]]; then
    code=$(curl --silent --show-error --max-time 5 -H 'Metadata-Flavor: Google' \
      -X "$method" --data "$data" -o "$outfile" -w '%{http_code}' "$url" 2>/dev/null)
  else
    code=$(curl --silent --show-error --max-time 5 -H 'Metadata-Flavor: Google' \
      -X "$method" -o "$outfile" -w '%{http_code}' "$url" 2>/dev/null)
  fi
  status=$?
  set -e
  if [[ $status -ne 0 || $code == 000 || ! $code =~ ^[0-9]{3}$ ]]; then
    return 2
  fi
  REPLY_CODE=$code
  return 0
}

deadline=$((SECONDS + wait_seconds))
while [[ ! -s $file && SECONDS -lt deadline ]]; do
  if ! request GET "$attribute" "$file"; then
    : > "$file"
    echo 'metadata GET failed: network' >&2
    sleep "$poll_seconds"
    continue
  fi
  if [[ $REPLY_CODE == 404 ]]; then
    : > "$file"
    echo 'metadata GET failed: HTTP 404' >&2
    sleep "$poll_seconds"
    continue
  fi
  if [[ $REPLY_CODE != 200 ]]; then
    : > "$file"
    echo "metadata GET failed: HTTP $REPLY_CODE" >&2
    cleanup
    exit 4
  fi
done
if [[ ! -s $file ]]; then
  cleanup
  exit 0
fi
chmod 600 "$file"
ackfile=$(mktemp "$install/jit.ack.XXXXXX")
if ! request PUT "$ack" "$ackfile" 1; then
  rm -f "$ackfile"
  echo 'guest attribute ack failed: network' >&2
  cleanup
  exit 4
fi
rm -f "$ackfile"
if [[ $REPLY_CODE != 200 ]]; then
  echo "guest attribute ack failed: HTTP $REPLY_CODE" >&2
  cleanup
  exit 4
fi
cd "$install"
status=0
./run.sh --jitconfig "$file" || status=$?
exit "$status"
