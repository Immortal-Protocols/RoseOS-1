"""Shared helpers for release receipts: streaming SHA-256 and atomic write-once JSON files."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

class ReceiptExists(Exception):
    pass

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def write_once(path, document):
    """Atomically create path with the JSON document; refuse to replace any existing file."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(document, indent=2, sort_keys=True) + '\n').encode()
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise ReceiptExists(f'{path.name} already exists; receipts are immutable, choose a new path')
    finally:
        os.unlink(temporary)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
