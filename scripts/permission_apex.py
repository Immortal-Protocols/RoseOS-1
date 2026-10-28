"""Packaged com.android.permission APEX gate (INFRA-HOME-098): the target-files must carry the APEX built from the
patched packages/modules/Permission source, not a prebuilt.

From the archive bytes alone: exactly one Permission APEX (com.android.permission.apex, or .capex whose original_apex
is checked the same way) and no Google prebuilt; apex_manifest.pb name and version; the payload key (apex_pubkey) and
the payload's AVB signature and hashtree (avbtool info_image / verify_image); the payload filesystem read by its
superblock (ext4 through debugfs, erofs through fsck.erofs; anything else fails); service-permission.jar from the
payload and the OS patch's markers in its dex; on signed target-files, the container signer through apksigner.
A missing tool or pin is a setup input, never a pass.
"""
import hashlib
import io
from pathlib import Path
import re
import subprocess
import sys
import zipfile

REQUIRED = ('module', 'paths', 'version', 'payloadJar', 'markers', 'sourcePayloadKeySha256', 'containerSigner', 'payloadKey')
ANY_PERMISSION_APEX = re.compile(r'(^|/)com\.(google\.)?android\.permission\.c?apex$')
EXT4 = (1080, b'\x53\xef')            # s_magic 0xEF53 in the superblock at 1024
EROFS = (1024, b'\xe2\xe1\xf5\xe0')   # EROFS_SUPER_MAGIC_V1 0xE0F5E1E2 at 1024
AVB_KEY_SHA1 = re.compile(r'Public key \(sha1\):\s*([0-9a-f]{40})')

def missing(spec):
    return [f'permissionApex.{key}' for key in REQUIRED if not (spec or {}).get(key)]

def varint(data, i):
    value = shift = 0
    while True:
        if i >= len(data):
            raise ValueError('truncated varint')
        byte = data[i]
        value |= (byte & 0x7f) << shift
        i += 1
        if byte < 0x80:
            return value, i
        shift += 7

def manifest(data):
    """(name, version) from apex_manifest.pb: ApexManifest field 1 (string name) and field 2 (int64 version)."""
    fields, i = {}, 0
    while i < len(data):
        key, i = varint(data, i)
        wire = key & 7
        if wire == 0:
            value, i = varint(data, i)
        elif wire == 2:
            size, i = varint(data, i)
            value, i = data[i:i + size], i + size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, i = data[i:i + size], i + size
        else:
            raise ValueError(f'unsupported wire type {wire}')
        if i > len(data):
            raise ValueError('truncated field')
        fields.setdefault(key >> 3, value)
    name = fields.get(1)
    return (name.decode() if isinstance(name, bytes) else None), (fields.get(2) if isinstance(fields.get(2), int) else None)

def payload_fs(path):
    with open(path, 'rb') as f:
        head = f.read(2048)
    for name, (offset, magic) in (('ext4', EXT4), ('erofs', EROFS)):
        if head[offset:offset + len(magic)] == magic:
            return name
    return None

def payload_file(image, fs, inside, tool, workdir):
    """Bytes of /inside from the payload image, or None when it is not there."""
    out = Path(workdir)/'payload-file'
    if fs == 'ext4':
        subprocess.run([tool('debugfs'), '-R', f'dump -p /{inside} {out}', str(image)], capture_output=True)
    else:
        extracted = Path(workdir)/'payload-tree'
        subprocess.run([tool('fsckErofs'), f'--extract={extracted}', str(image)], capture_output=True)
        out = extracted/inside
    return out.read_bytes() if out.is_file() and not out.is_symlink() else None

def container_signers(data, apksigner, workdir, label):
    apex = Path(workdir)/f'{label}.apex'
    apex.write_bytes(data)
    result = subprocess.run([apksigner, 'verify', '--print-certs', str(apex)], capture_output=True, text=True)
    apex.unlink()
    return None if result.returncode else re.findall(r'certificate SHA-256 digest: ([0-9a-f]{64})', result.stdout)

def inspect(data, spec, tool, workdir, payload_key, signer=None, apksigner=None, label='apex'):
    """(facts, failures) for one APEX's bytes. payload_key is the apex_pubkey sha256 it must carry; signer, when given,
    the container certificate sha256 apksigner must report."""
    failures, facts = [], {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return facts, [f'{label} is not a readable APEX archive']
    names = set(archive.namelist())
    if 'original_apex' in names:
        inner, failures = inspect(archive.read('original_apex'), spec, tool, workdir, payload_key, signer, apksigner,
            label=f'{label} original_apex')
        facts = {'container': 'capex', 'original': inner}
        for entry, value in (('apex_manifest.pb', (inner.get('name'), inner.get('version'))),
                ('apex_pubkey', inner.get('payloadKeySha256'))):
            if entry not in names:
                failures.append(f'{label} has no {entry}')
                continue
            outer = archive.read(entry)
            try:
                outer_value = manifest(outer) if entry == 'apex_manifest.pb' else hashlib.sha256(outer).hexdigest()
            except ValueError:
                outer_value = None
            if outer_value != value:
                failures.append(f'{label} {entry} differs from its original_apex')
    else:
        facts['container'] = 'apex'
        absent = [n for n in ('apex_manifest.pb', 'apex_payload.img', 'apex_pubkey') if n not in names]
        if absent:
            return facts, failures + [f'{label} has no {", ".join(absent)}']
        try:
            facts['name'], facts['version'] = manifest(archive.read('apex_manifest.pb'))
        except ValueError:
            failures.append(f'{label} apex_manifest.pb is malformed')
        if facts.get('name') != spec['module']:
            failures.append(f'{label} is {facts.get("name")}, expected {spec["module"]}')
        if facts.get('version') != spec['version']:
            failures.append(f'{label} version is {facts.get("version")}, expected {spec["version"]} (the pinned source default)')
        pubkey = archive.read('apex_pubkey')
        facts['payloadKeySha256'] = hashlib.sha256(pubkey).hexdigest()
        if facts['payloadKeySha256'] != payload_key:
            failures.append(f'{label} payload key is not the expected key')
        image = Path(workdir)/'apex_payload.img'
        with archive.open('apex_payload.img') as source, open(image, 'wb') as out:
            while block := source.read(1 << 20):
                out.write(block)
        avbtool = tool('avbtool')
        info = subprocess.run([sys.executable, avbtool, 'info_image', '--image', str(image)], capture_output=True, text=True)
        embedded = AVB_KEY_SHA1.search(info.stdout)
        if info.returncode or not embedded or embedded.group(1) != hashlib.sha1(pubkey).hexdigest():
            failures.append(f'{label} payload is not signed by its apex_pubkey')
        if subprocess.run([sys.executable, avbtool, 'verify_image', '--image', str(image)], capture_output=True).returncode:
            failures.append(f'{label} payload AVB signature or hashtree does not verify')
        else:
            facts['payloadSignature'] = 'verified'
        facts['payloadFs'] = payload_fs(image)
        if facts['payloadFs'] is None:
            failures.append(f'{label} payload filesystem is neither ext4 nor erofs; not read')
            jar = None
        else:
            jar = payload_file(image, facts['payloadFs'], spec['payloadJar'], tool, workdir)
        image.unlink()
        if facts['payloadFs'] and jar is None:
            failures.append(f'{label} payload has no {spec["payloadJar"]}')
        if jar is not None:
            facts['payloadJarSha256'] = hashlib.sha256(jar).hexdigest()
            try:
                with zipfile.ZipFile(io.BytesIO(jar)) as classes:
                    dex = [classes.read(n) for n in classes.namelist() if re.fullmatch(r'classes\d*\.dex', n)]
            except zipfile.BadZipFile:
                dex = None
                failures.append(f'{label} {spec["payloadJar"]} is not a readable jar')
            if dex is not None:
                facts['markers'] = [m for m in spec['markers'] if any(m.encode() in d for d in dex)]
                absent = [m for m in spec['markers'] if m not in facts['markers']]
                if absent:
                    failures.append(f'{label} {spec["payloadJar"]} dex lacks the RoleService migration markers {absent}; '
                        'the APEX is not built from the patched source')
    if signer:
        digests = container_signers(data, apksigner, workdir, label.replace(' ', '-'))
        facts['containerSigners'] = digests
        if digests != [signer]:
            failures.append(f'{label} container is not signed by the expected certificate')
    return facts, failures

def locate(names, spec):
    """(path, failures): exactly one of the configured Permission APEX paths and no other Permission APEX anywhere."""
    found = sorted(n for n in names if ANY_PERMISSION_APEX.search(n))
    present = [p for p in spec['paths'] if p in names]
    if not found:
        return None, [f'no Permission APEX in the target-files (expected one of {spec["paths"]})']
    if len(present) != 1 or found != present:
        return None, [f'Permission APEX must be exactly one of {spec["paths"]} with no prebuilt or second copy; found {found}']
    return present[0], []
