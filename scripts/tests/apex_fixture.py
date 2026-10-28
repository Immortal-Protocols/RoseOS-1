"""Synthetic Permission APEX archives for the gate tests (not built by Soong, not signed by releasetools).

Real formats where the gate parses them: the APEX/CAPEX zip layout, apex_manifest.pb protobuf fields and an ext4 payload
made by mke2fs and read by the real debugfs. Stand-ins where a signing tool would run: the payload's AVB footer is a
trailer naming the sha1 of its key (read by fake_avbtool), the container signature is a zip comment naming a certificate
digest (read by fake_apksigner), service-permission.jar's classes.dex is only a byte string holding the dex strings the
gate looks for, and an erofs payload is the erofs magic followed by a JSON tree (read by fake_fsck_erofs)."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import zipfile

AVB_TRAILER = b'FAKEAVB1'
MKE2FS = shutil.which('mke2fs') or '/usr/sbin/mke2fs'
DEBUGFS = shutil.which('debugfs') or '/usr/sbin/debugfs'
MARKERS = ('rose_home_role_selected', 'dev.pixel.voice')
SOURCE_KEY = (Path(__file__).resolve().parent/'fixtures/permission-apex/Permission/com.android.permission.avbpubkey').read_bytes()

def varint(value):
    out = b''
    while True:
        byte, value = value & 0x7f, value >> 7
        out += bytes([byte | (0x80 if value else 0)])
        if not value:
            return out

def apex_manifest(name, version):
    encoded = name.encode()
    return b'\x0a' + varint(len(encoded)) + encoded + b'\x10' + varint(version) + b'\x1a\x011'

def service_jar(markers=MARKERS, dex_name='classes.dex'):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as jar:
        jar.writestr(zipfile.ZipInfo(dex_name, (2009, 1, 1, 0, 0, 0)), b'dex\n039\0Lcom/android/role/RoleService;' + b''.join(m.encode() + b'\0' for m in markers))
    return buffer.getvalue()

def payload(files, key, fs='ext4', verified=True):
    """Payload image holding files (path -> bytes) plus the stand-in AVB trailer for key."""
    if fs == 'ext4':
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)/'tree'
            for relative, data in files.items():
                (tree/relative).parent.mkdir(parents=True, exist_ok=True)
                (tree/relative).write_bytes(data)
            tree.mkdir(exist_ok=True)
            image = Path(directory)/'payload.img'
            subprocess.run([MKE2FS, '-q', '-F', '-t', 'ext4', '-O', '^has_journal', '-d', str(tree), str(image), '2M'],
                check=True, capture_output=True)
            data = image.read_bytes()
    elif fs == 'erofs':
        data = b'\0' * 1024 + b'\xe2\xe1\xf5\xe0' + json.dumps({k: base64.b64encode(v).decode() for k, v in files.items()}).encode()
    else:
        data = b'\0' * 4096
    sha1 = hashlib.sha1(key).hexdigest().encode() if verified else b'0' * 40
    return data + AVB_TRAILER + sha1

def apex(name='com.android.permission', version=370399999, key=SOURCE_KEY, files=None, fs='ext4', verified=True,
         signer=None, omit=(), payload_key=None):
    """APEX bytes. payload_key is the key that signed the payload when it differs from apex_pubkey."""
    files = {'javalib/service-permission.jar': service_jar()} if files is None else files
    entries = {'apex_manifest.pb': apex_manifest(name, version), 'apex_pubkey': key,
        'apex_payload.img': payload(files, payload_key or key, fs, verified), 'AndroidManifest.xml': b'binary manifest'}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for entry, data in entries.items():
            if entry not in omit:
                archive.writestr(zipfile.ZipInfo(entry, (2009, 1, 1, 0, 0, 0)), data)
        if signer:
            archive.comment = b'signer=' + signer.encode()
    return buffer.getvalue()

def capex(original, name='com.android.permission', version=370399999, key=SOURCE_KEY, signer=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('original_apex', original, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr('apex_manifest.pb', apex_manifest(name, version))
        archive.writestr('apex_pubkey', key)
        archive.writestr('AndroidManifest.xml', b'binary manifest')
        if signer:
            archive.comment = b'signer=' + signer.encode()
    return buffer.getvalue()

def tool(path, body):
    path.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)

def fake_avbtool(path, passthrough=None):
    """avbtool contract for an APEX payload: info_image prints the key sha1, verify_image checks the footer.
    passthrough (a script path) handles every other image, e.g. the pipeline's vbmeta stand-in."""
    return tool(path, f'''
        import os, subprocess, sys
        image = sys.argv[sys.argv.index('--image') + 1]
        if os.path.basename(image) != 'apex_payload.img':
            sys.exit(subprocess.run([sys.executable, {passthrough!r}, *sys.argv[1:]]).returncode if {passthrough!r} else 2)
        data = open(image, 'rb').read()
        trailer = data[-48:]
        if trailer[:8] != {AVB_TRAILER!r}:
            sys.exit('no AVB footer')
        if sys.argv[1] == 'info_image':
            print('Footer version:           1.0')
            print('Public key (sha1):        ' + trailer[8:].decode())
        elif sys.argv[1] == 'verify_image':
            sys.exit(1 if trailer[8:] == b'0' * 40 else 0)''')

def fake_apksigner(path, passthrough_digest=None):
    """apksigner verify --print-certs: an APEX's zip comment names its certificate; no comment does not verify."""
    return tool(path, f'''
        import sys, zipfile
        target = sys.argv[-1]
        if not target.endswith('.apex'):
            print('Signer #1 certificate SHA-256 digest: {passthrough_digest}')
            sys.exit(0)
        comment = zipfile.ZipFile(target).comment.decode()
        if not comment.startswith('signer='):
            sys.exit('DOES NOT VERIFY')
        print('Signer #1 certificate SHA-256 digest: ' + comment[7:])''')

def fake_fsck_erofs(path):
    return tool(path, '''
        import base64, json, os, sys
        out = next(a for a in sys.argv if a.startswith('--extract=')).split('=', 1)[1]
        data = open(sys.argv[-1], 'rb').read()
        files = json.loads(data[1028:-48])
        for relative, value in files.items():
            os.makedirs(os.path.dirname(os.path.join(out, relative)), exist_ok=True)
            open(os.path.join(out, relative), 'wb').write(base64.b64decode(value))''')
