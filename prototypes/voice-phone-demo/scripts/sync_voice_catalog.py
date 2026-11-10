#!/usr/bin/env python3
"""Mirror the shared catalog's pinned IDs/hashes into the existing Linux adapter.

This edits only source data; it provisions, downloads and activates nothing.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / 'prototypes/voice-phone-demo/rose/voice_catalog.json'
TARGET = ROOT / 'workspace/models/rose_cloud/rose_cloud/phonon.py'
START = '# BEGIN GENERATED VOICE CATALOG (scripts/sync_voice_catalog.py)'
END = '# END GENERATED VOICE CATALOG'


def render(data):
    lines = [START, 'VOICES = {']
    for name, entry in data['voices'].items():
        lines.append(f"    {name!r}: ({entry['voice_id']!r}, {entry['sha256']!r}),")
    return '\n'.join(lines + ['}', END])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    data = json.loads(CATALOG.read_text())
    text = TARGET.read_text()
    before, tail = text.split(START, 1)
    _, after = tail.split(END, 1)
    expected = before + render(data) + after
    if args.check:
        if expected != text:
            raise SystemExit('cloud voice catalog is stale; run scripts/sync_voice_catalog.py')
    else:
        TARGET.write_text(expected)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
