#!/usr/bin/env python3
"""Build the startup-metadata bootstrap for the persistent Muse Glimmer VM (MODELS-ALWAYSON-SERVICE-001). Offline.

  python3 workspace/models/serving/service/build_bootstrap.py --out DIR [--attestation-roots ROOTS_DIR]

Writes three files:
  DIR/startup-script.sh         GCE `startup-script` metadata. It embeds the payload's sha256 and size, fetches
                                the payload from metadata, refuses any mismatch, then runs bootstrap.py.
  DIR/rose-models-payload.b64   GCE `rose-models-payload` metadata: base64(gzip(tar)) of the rendered /etc/rose-models
                                tree, bootstrap.py, the pinned NVIDIA repository key and any attestation roots.
  DIR/bootstrap-manifest.json   nonsecret identity: the tar sha256 and size, the metadata value size, the script
                                sha256, the source commit and the per-file sha256.
The tar is deterministic (sorted entries, fixed times and owners), so its sha256 identifies the source. gzip is only
transport.
"""
import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import render_service  # noqa: E402

METADATA_VALUE_LIMIT = 256 * 1024  # GCE: one metadata value
STARTUP = '''#!/bin/bash
# Rose Models bootstrap ({commit}). Rendered by workspace/models/serving/service/build_bootstrap.py; do not edit.
# Fetches the rose-models-payload metadata value, refuses unless its tar has exactly the digest and size below,
# then runs the bootstrap stages. Rerun-safe: a verified payload directory is reused on later boots.
set -uo pipefail
PAYLOAD_SHA256={sha256}
PAYLOAD_TAR_BYTES={size}
MD="${{ROSE_MODELS_METADATA_URL:-http://metadata.google.internal/computeMetadata/v1/instance/attributes}}"
OPT="${{ROSE_MODELS_OPT:-/opt/rose-models}}"
PY="${{ROSE_MODELS_PYTHON:-/usr/bin/python3}}"
dir="$OPT/payload-$PAYLOAD_SHA256"
fail() {{
  echo "ROSE-MODELS-BOOTSTRAP {{\\"result\\": \\"failed\\", \\"stage\\": \\"payload\\", \\"reason\\": \\"$1\\", \\"payloadSha256\\": \\"$PAYLOAD_SHA256\\"}}"
  exit 1
}}
if [ ! -f "$dir/.verified" ]; then
  mkdir -p -m 700 "$OPT" || fail opt_dir_failed
  tmp="$(mktemp -d "$OPT/.fetch.XXXXXX")" || fail tmp_dir_failed
  trap 'rm -rf "$tmp"' EXIT
  curl -fsS --noproxy '*' --max-time 60 --retry 5 --retry-delay 3 -H 'Metadata-Flavor: Google' \\
    -o "$tmp/payload.b64" "$MD/rose-models-payload" || fail metadata_fetch_failed
  base64 -d "$tmp/payload.b64" | gzip -dc > "$tmp/payload.tar" || fail payload_decode_failed
  [ "$(stat -c %s "$tmp/payload.tar")" = "$PAYLOAD_TAR_BYTES" ] || fail payload_size_mismatch
  [ "$(sha256sum "$tmp/payload.tar" | cut -d' ' -f1)" = "$PAYLOAD_SHA256" ] || fail payload_digest_mismatch
  mkdir "$tmp/x" && tar -xf "$tmp/payload.tar" -C "$tmp/x" --no-same-owner || fail payload_extract_failed
  touch "$tmp/x/.verified"
  rm -rf "$dir" && mv "$tmp/x" "$dir" || fail payload_install_failed
  rm -rf "$tmp"; trap - EXIT  # exec below would skip the trap
fi
find "$OPT" -maxdepth 1 -name 'payload-*' ! -name "payload-$PAYLOAD_SHA256" -exec rm -rf {{}} +
exec "$PY" -I "$dir/bootstrap.py" run --payload "$dir" --payload-sha256 "$PAYLOAD_SHA256"
'''


class Invalid(Exception):
    pass


def check_root(path):
    """An attestation root must be one X.509 certificate (PEM or DER); Infra installs Google's published roots."""
    from cryptography import x509
    data = path.read_bytes()
    try:
        if path.suffix == '.pem':
            x509.load_pem_x509_certificate(data)
        elif path.suffix == '.der':
            x509.load_der_x509_certificate(data)
        else:
            raise ValueError
    except ValueError:
        raise Invalid(f'attestation root {path.name} is not a .pem/.der X.509 certificate') from None
    return data


def payload_files(attestation_roots=None, model='muse', qwen_settings='proven'):
    """{archive path: bytes} of everything the VM needs. ``model`` selects what the persistent slot serves (default
    muse; qwen only when asked, MODELS-QWEN-SERVE-001)."""
    files = {}
    with tempfile.TemporaryDirectory() as tmp:
        render_service.render(Path(tmp) / 'tree', model=model, qwen_settings=qwen_settings)
        for p in sorted((Path(tmp) / 'tree').rglob('*')):
            if p.is_file():
                files['etc-rose-models/' + p.relative_to(Path(tmp) / 'tree').as_posix()] = p.read_bytes()
    files['bootstrap.py'] = (HERE / 'bootstrap.py').read_bytes()
    files['apt/nvidia-container-toolkit.asc'] = (HERE / 'apt' / 'nvidia-container-toolkit.asc').read_bytes()
    for p in sorted(Path(attestation_roots).iterdir()) if attestation_roots else []:
        if p.is_file():
            files['attestation-roots/' + p.name] = check_root(p)
    return files


def tar_bytes(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.uid, info.gid, info.uname, info.gname = len(files[name]), 0, 0, 0, '', ''
            info.mode = 0o755 if name == 'bootstrap.py' or name.startswith('etc-rose-models/bin/') else 0o644
            tar.addfile(info, io.BytesIO(files[name]))
    return out.getvalue()


def commit():
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=HERE, capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', str(HERE.parent)], cwd=HERE, capture_output=True,
                               text=True, timeout=10)
        return head.stdout.strip() + ('+uncommitted' if dirty.stdout.strip() else '') if head.returncode == 0 else 'unknown'
    except OSError:
        return 'unknown'


def build(out_dir, attestation_roots=None, source=None, model='muse', qwen_settings='proven'):
    files = payload_files(attestation_roots, model, qwen_settings)
    tar = tar_bytes(files)
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode='wb', mtime=0, compresslevel=9) as z:
        z.write(tar)
    value = base64.b64encode(gz.getvalue())
    if len(value) > METADATA_VALUE_LIMIT:
        raise Invalid(f'payload metadata value is {len(value)} bytes; the limit is {METADATA_VALUE_LIMIT}')
    sha = hashlib.sha256(tar).hexdigest()
    source = source or commit()
    script = STARTUP.format(commit=source, sha256=sha, size=len(tar)).encode()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'rose-models-payload.b64').write_bytes(value)
    (out_dir / 'startup-script.sh').write_bytes(script)
    manifest = {'schema': 'rose-models-bootstrap-payload/1', 'sourceCommit': source,
                'payloadTarSha256': sha, 'payloadTarBytes': len(tar), 'metadataValueBytes': len(value),
                'startupScriptSha256': hashlib.sha256(script).hexdigest(), 'startupScriptBytes': len(script),
                'files': {n: hashlib.sha256(d).hexdigest() for n, d in sorted(files.items())}}
    (out_dir / 'bootstrap-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--attestation-roots', type=Path)
    p.add_argument('--model', choices=render_service.MODELS, default='muse',
                   help='what the persistent model slot serves (default muse; qwen only when selected)')
    p.add_argument('--qwen-settings', choices=('proven', 'proposed'), default='proven')
    a = p.parse_args(argv)
    try:
        m = build(a.out, a.attestation_roots, model=a.model, qwen_settings=a.qwen_settings)
    except Invalid as e:
        print(f'rejected: {e}', file=sys.stderr)
        return 2
    print(json.dumps({k: m[k] for k in ('sourceCommit', 'payloadTarSha256', 'payloadTarBytes', 'metadataValueBytes',
                                        'startupScriptSha256')}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
