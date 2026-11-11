"""Pinned Phonon 31M catalog. Metadata is explicit; voice names imply no traits.

New ordinary voices are candidates pending listening acceptance. Character voices
are deliberately not admitted. No alternative model or hosted provider is used.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

CATALOG_FILE = Path(__file__).with_name('voice_catalog.json')


def load_catalog(path: Path = CATALOG_FILE) -> dict:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    source, voices = data['source'], data['voices']
    if (source['repo'] != 'gradium/phonon-voices'
            or source['model'] != 'gradium/phonon-31m-en'
            or source['license'] != 'Apache-2.0'
            or not re.fullmatch(r'[0-9a-f]{40}', source['revision'])):
        raise ValueError('expected a pinned, Apache-2.0 Phonon 31M catalog')
    if not voices:
        raise ValueError('empty voice catalog')
    ids, hashes = set(), set()
    for name, entry in voices.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
            raise ValueError('invalid stable voice name')
        vid, digest = entry['voice_id'], entry['sha256']
        if not re.fullmatch(r'[A-Za-z0-9_-]{16}', vid) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('invalid voice asset identity')
        if vid in ids or digest in hashes:
            raise ValueError('voice assets must be unique')
        ids.add(vid)
        hashes.add(digest)
        if entry['category'] not in ('ordinary', 'character'):
            raise ValueError('voice category must be explicit')
        if entry['language'] != 'en' or entry['locale'] not in ('en-us', 'en-gb'):
            raise ValueError('unsupported catalog locale')
        if entry.get('gender') not in ('female', 'male', None):
            raise ValueError('invalid gender metadata')
        if not entry['sample_dir'].startswith('samples/31m/en/') or '..' in entry['sample_dir'].split('/'):
            raise ValueError('invalid reference sample directory')
    return data


CATALOG = load_catalog()
VOICE_REPO = CATALOG['source']['repo']
VOICE_REVISION = CATALOG['source']['revision']
VOICES = {name: entry['voice_id'] for name, entry in CATALOG['voices'].items()}


def verify_voice_file(voice_id: str, path: str | Path) -> str:
    """Reject changed/corrupt bytes before the model can consume them."""
    expected = next((v['sha256'] for v in CATALOG['voices'].values() if v['voice_id'] == voice_id), None)
    if expected is None:
        raise ValueError('voice asset is not in the pinned catalog')
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('voice asset does not match the pinned catalog')
    return str(path)
