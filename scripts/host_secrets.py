"""Builder-side handling of the existing GCP Secret Manager signing inputs for the os-release adapters.

Secrets are fetched by pinned version with the job's short-lived gcloud identity into an owner-only
directory, preferably RAM-backed (/dev/shm), and are overwritten and removed on success or failure.
Values are never printed, put in argv or copied into the release directory.
"""
import base64
import contextlib
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

class AdapterSetupMissing(Exception):
    def __init__(self, missing):
        super().__init__('setup missing: ' + ', '.join(missing))
        self.missing = missing

class AdapterFailed(Exception):
    pass

SECRET_NAME = re.compile(r'[A-Za-z0-9_-]{1,255}')

@contextlib.contextmanager
def private_directory(prefix):
    base = '/dev/shm' if os.path.isdir('/dev/shm') and os.access('/dev/shm', os.W_OK) else None
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    os.chmod(path, 0o700)
    try:
        yield path
    finally:
        for file in path.rglob('*'):
            if file.is_file() and not file.is_symlink():
                with contextlib.suppress(OSError):
                    file.write_bytes(b'\0' * file.stat().st_size)
        shutil.rmtree(path, ignore_errors=True)

def fetch_secret(project, name, version, destination, gcloud='gcloud', timeout=None):
    """gcloud writes the secret straight to an owner-only file; nothing passes through argv or stdout. With timeout, a
    gcloud that runs longer is killed and refused (timeout=None keeps the historical unbounded behaviour)."""
    if not SECRET_NAME.fullmatch(name) or not re.fullmatch(r'[1-9][0-9]*', str(version)):
        raise AdapterFailed('secret names and pinned numeric versions only')
    destination = Path(destination)
    old = os.umask(0o077)
    try:
        result = subprocess.run([gcloud, 'secrets', 'versions', 'access', str(version), '--secret', name,
            '--project', project, '--out-file', str(destination), '--quiet'], capture_output=True, text=True,
            timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AdapterFailed(f'timed out reading secret {name} version {version} after {timeout} s') from None
    finally:
        os.umask(old)
    if result.returncode or not destination.is_file():
        # gcloud's own message can name resources but never contains the payload.
        raise AdapterFailed(f'could not read secret {name} version {version}')
    os.chmod(destination, 0o600)
    return destination

def extract_key_archive(archive, destination):
    """Extract a gzip tar of signing keys after refusing any member that is not a plain relative file/dir."""
    names = set()
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()) or member.name in names:
                raise AdapterFailed('signing key archive contains an unexpected member')
            names.add(member.name)
        for member in members:
            target = Path(destination)/member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tar.extractfile(member) as source, open(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as out:
                shutil.copyfileobj(source, out)
    return Path(destination)

def certificate_sha256(pem_path):
    pem = Path(pem_path).read_text()
    body = pem.split('-----BEGIN CERTIFICATE-----')[1].split('-----END CERTIFICATE-----')[0]
    return hashlib.sha256(base64.b64decode(''.join(body.split()))).hexdigest()

def check_public_pins(key_dir, identities):
    """The key set's public halves must be the established ones; nothing is generated or substituted."""
    failures = []
    cert = Path(key_dir)/'releasekey.x509.pem'
    avb = Path(key_dir)/'avb_pkmd.bin'
    if not cert.is_file() or certificate_sha256(cert) != identities['otaCertificateSha256']:
        failures.append('release certificate does not match the established pin')
    if not avb.is_file() or hashlib.sha256(avb.read_bytes()).hexdigest() != identities['avbPublicKeySha256']:
        failures.append('AVB public key does not match the established pin')
    if failures:
        raise AdapterFailed('; '.join(failures))

def discover_key_directory(root, identities):
    """The archive's nesting was never recorded: require exactly one releasekey.x509.pem and exactly one avb_pkmd.bin,
    in the same directory, matching the established pins. Zero or several of either fails."""
    certs = [p for p in Path(root).rglob('releasekey.x509.pem') if p.is_file() and not p.is_symlink()]
    avbs = [p for p in Path(root).rglob('avb_pkmd.bin') if p.is_file() and not p.is_symlink()]
    if len(certs) != 1 or len(avbs) != 1 or certs[0].parent != avbs[0].parent:
        raise AdapterFailed('key archive must hold exactly one releasekey.x509.pem and avb_pkmd.bin in one directory')
    check_public_pins(certs[0].parent, identities)
    return certs[0].parent
