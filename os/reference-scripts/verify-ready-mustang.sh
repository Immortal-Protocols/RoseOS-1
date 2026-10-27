#!/usr/bin/env bash
# Verify generated release artifacts only. Never contacts or flashes a device.
set -euo pipefail
ROOT="${PIXEL_OS_ROOT:?Set your own OS build root}"
BUILD_NUMBER="${PIXEL_OS_BUILD_NUMBER:?Set the same PIXEL_OS_BUILD_NUMBER used for signing}"
[[ "$BUILD_NUMBER" =~ ^[0-9]{10}$ ]] || { echo 'Expected a ten-digit build number' >&2; exit 1; }
SOURCE=$ROOT/grapheneos-2026100600
RELEASE="$SOURCE/releases/$BUILD_NUMBER/release-mustang-$BUILD_NUMBER"
VAULT="${PIXEL_OS_SIGNING_VAULT:?Set your own OS signing directory}"
export JAVA_HOME="$SOURCE/prebuilts/jdk/jdk21/linux-x86"
export PATH="$JAVA_HOME/bin:$PATH"
EVIDENCE_ROOT="$ROOT/mustang-release-verification/$BUILD_NUMBER"
mkdir -p "$EVIDENCE_ROOT"
EVIDENCE=$(mktemp -d "$EVIDENCE_ROOT/run-XXXXXX")
printf '%s\n' "$EVIDENCE" > "$EVIDENCE_ROOT/latest-run.txt"
export PATH="$RELEASE/bin:$SOURCE/prebuilts/build-tools/linux-x86/bin:$PATH"
printf '%s  %s\n' "${PIXEL_AVB_PKMD_SHA256:?Set your expected AVB public metadata hash}" "$VAULT/mustang/avb_pkmd.bin" | sha256sum -c -
mkdir -p "$EVIDENCE/images"
cd "$RELEASE"
sha256sum -c SHA256SUMS
unzip -oq "mustang-img-$BUILD_NUMBER.zip" -d "$EVIDENCE/images"
openssl pkey -in "$VAULT/mustang/avb.pem" -passin "file:$VAULT/passphrase" \
    -pubout -out "$EVIDENCE/avb-public.pem"
(
  cd "$EVIDENCE/images"
  python3 "$SOURCE/external/avb/avbtool.py" verify_image --image vbmeta.img \
      --key "$EVIDENCE/avb-public.pem" --follow_chain_partitions
) | tee "$EVIDENCE/avb-verification.txt"
python3 "$SOURCE/external/avb/avbtool.py" info_image \
    --image "$EVIDENCE/images/vbmeta.img" > "$EVIDENCE/vbmeta-info.txt"
python3 - "$RELEASE" "$EVIDENCE" "$BUILD_NUMBER" <<'PY'
import pathlib, sys, zipfile
r, e = map(pathlib.Path, sys.argv[1:3])
build = sys.argv[3]
with zipfile.ZipFile(r/'mustang-target_files.zip') as z:
    props = '\n'.join(z.read(n).decode(errors='replace') for n in z.namelist()
                      if n.endswith('/build.prop'))
    for line in props.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key.endswith('.build.version.incremental') or key == 'ro.build.version.incremental':
            assert value == build, ('inconsistent build number', key, value)
        if key.endswith('.build.fingerprint') or key == 'ro.build.fingerprint':
            assert '/'+build+':user/release-keys' in value, ('inconsistent fingerprint', key, value)
    for line in z.read('META/misc_info.txt').decode().splitlines():
        if '.fingerprint:' in line:
            assert '/'+build+':user/release-keys' in line, ('inconsistent AVB fingerprint', line)
    assert 'ro.product.product.device=mustang' in props or 'ro.product.device=mustang' in props
    assert 'ro.build.type=user\n' in props
    assert ('ro.build.version.incremental='+build+'\n') in props
    assert 'persist.wm.debug.pixel_agent_tray=1' in props
    assert 'test-keys' not in '\n'.join(x for x in props.splitlines() if x.startswith('ro.build.tags='))
    (e/'build-properties.txt').write_text(props)
    (e/'android-info.txt').write_bytes(z.read('OTA/android-info.txt'))
    for suffix, name in [('/SystemUI.apk','SystemUI.apk'),('/framework-res.apk','framework-res.apk')]:
        matches=[n for n in z.namelist() if n.endswith(suffix)]
        assert len(matches)==1, matches
        (e/name).write_bytes(z.read(matches[0]))
with zipfile.ZipFile(r/('mustang-ota_update-'+build+'.zip')) as z:
    (e/'ota-metadata.txt').write_bytes(z.read('META-INF/com/android/metadata'))
    (e/'ota-certificate.pem').write_bytes(z.read('META-INF/com/android/otacert'))
with zipfile.ZipFile(e/'SystemUI.apk') as z:
    assert any(b'Pixel agent status waves' in z.read(n) for n in z.namelist() if n.endswith('.dex')), 'current top status-bar implementation absent'
print('Signed target metadata, APK presence, and current top status-bar marker verified')
PY
for apk in SystemUI framework-res; do
    "$RELEASE/bin/apksigner" verify --verbose --print-certs "$EVIDENCE/$apk.apk" \
        > "$EVIDENCE/$apk-signature.txt"
done
python3 - "$EVIDENCE" "${PIXEL_PLATFORM_CERT_SHA256:?Set your platform certificate fingerprint}" <<'PY'
import pathlib, re, sys
p=pathlib.Path(sys.argv[1]); expected=sys.argv[2].lower()
for name in ['SystemUI','framework-res']:
    text=(p/(name+'-signature.txt')).read_text()
    found=re.search(r'certificate SHA-256 digest: ([0-9a-f]+)', text)
    assert 'Number of signers: 1' in text and found and found.group(1)==expected, (name,'wrong platform certificate')
print('SystemUI and Android framework use expected custom platform certificate')
PY
openssl x509 -in "$EVIDENCE/ota-certificate.pem" -outform DER > "$EVIDENCE/ota-certificate.der"
openssl x509 -in "$VAULT/mustang/releasekey.x509.pem" -outform DER > "$EVIDENCE/expected-release-certificate.der"
cmp "$EVIDENCE/ota-certificate.der" "$EVIDENCE/expected-release-certificate.der"
"$RELEASE/bin/check_ota_package_signature" "$VAULT/mustang/releasekey.x509.pem" \
    "$RELEASE/mustang-ota_update-$BUILD_NUMBER.zip" | tee "$EVIDENCE/ota-signature-verification.txt"
python3 "$ROOT/verify-factory-mustang.py" "$RELEASE" "$EVIDENCE" "$VAULT" \
    "$ROOT/fallback" "$BUILD_NUMBER" | tee "$EVIDENCE/factory-verification.txt"
printf '%s\n' 'AVB chain, APK platform signatures, release metadata and OTA whole-file/payload signatures verified.' 
