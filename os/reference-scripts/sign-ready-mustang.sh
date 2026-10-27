#!/usr/bin/env bash
# Continue only a successfully staged, emulator-validated build. Never flashes.
set -euo pipefail
ROOT=/home/pixelbuild/pixel-os
BUILD_NUMBER="${PIXEL_OS_BUILD_NUMBER:?Set the same PIXEL_OS_BUILD_NUMBER used for the validated build}"
[[ "$BUILD_NUMBER" =~ ^[0-9]{10}$ ]] || { echo 'Expected a ten-digit build number' >&2; exit 1; }
SOURCE=$ROOT/grapheneos-2026100600
VAULT=/home/pixelbuild/.local/share/pixel-os-signing
export JAVA_HOME="$SOURCE/prebuilts/jdk/jdk21/linux-x86"
export PATH="$JAVA_HOME/bin:$PATH"
exec 9>"$ROOT/tray-build.lock"
flock 9
phase() { printf '%s\n' "$1" > "$ROOT/tray-build.phase"; printf '%s %s\n' "$(date -u +%FT%TZ)" "$1"; }
expected="unsigned mustang $BUILD_NUMBER target files staged; signing and device validation pending"
[ "$(cat "$ROOT/tray-build.phase")" = "$expected" ] || {
  echo 'Build did not stage successfully; refusing signing.' >&2; exit 1;
}
cd "$SOURCE"
TARGET="releases/$BUILD_NUMBER/mustang-target_files.zip"
test -s "$TARGET"
test -s "releases/$BUILD_NUMBER/mustang-otatools.zip"
test ! -e "releases/$BUILD_NUMBER/release-mustang-$BUILD_NUMBER" || { echo 'Signed release already exists; refusing overwrite' >&2; exit 1; }
python3 - "$TARGET" "$BUILD_NUMBER" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    values = '\n'.join(z.read(n).decode(errors='replace') for n in z.namelist()
                       if n.endswith('/build.prop'))
    assert 'ro.product.product.device=mustang' in values or 'ro.product.device=mustang' in values, 'wrong device'
    assert 'persist.wm.debug.pixel_agent_tray=1' in values, 'tray disabled'
    assert 'ro.build.type=user\n' in values, 'not release user build'
    assert ('ro.build.version.incremental='+sys.argv[2]+'\n') in values, 'wrong build'
    for line in values.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key.endswith('.build.version.incremental') or key == 'ro.build.version.incremental':
            assert value == sys.argv[2], ('inconsistent build number', key, value)
        if key.endswith('.build.fingerprint') or key == 'ro.build.fingerprint':
            assert '/'+sys.argv[2]+':user/test-keys' in value, ('inconsistent fingerprint', key, value)
    for line in z.read('META/misc_info.txt').decode().splitlines():
        if '.fingerprint:' in line:
            assert '/'+sys.argv[2]+':user/test-keys' in line, ('inconsistent AVB fingerprint', line)
print('Unsigned target metadata checked: mustang, user, requested build, status feature enabled')
PY
phase 'signing validated mustang candidate; no physical install'
trap 'code=$?; if [ "$code" -ne 0 ]; then phase "mustang signing or verification failed with exit $code; see mustang-signing.log"; fi' EXIT
export PATH="$SOURCE/prebuilts/build-tools/linux-x86/bin:$PATH"
password=$(cat "$VAULT/passphrase")
export password
script/generate-release.sh mustang "$BUILD_NUMBER"
unset password
RELEASE="$SOURCE/releases/$BUILD_NUMBER/release-mustang-$BUILD_NUMBER"
cd "$RELEASE"
test -s "mustang-install-$BUILD_NUMBER.zip"
test -s "mustang-ota_update-$BUILD_NUMBER.zip"
sha256sum "mustang-install-$BUILD_NUMBER.zip" "mustang-ota_update-$BUILD_NUMBER.zip" mustang-target_files.zip > SHA256SUMS
phase 'signed mustang candidate packaged; final independent signature verification pending'
