#!/usr/bin/env python3
"""Firmware-carried partition observation from a hash-pinned signed target-files (FOLD-FIRMWARE-OBSERVE).

  python3 scripts/rango-firmware-observe.py --target-files TF --target-files-sha256 H --device rango \
      --build 2026103001 --source "os-release ... run R attempt A" --out FIRMWARE_OBSERVATION.json

Writes the roseos.factory-firmware-observation.v1 that scripts/factory-package.py classify reads (--firmware-
observation): for RADIO/bootloader.img and RADIO/radio.img, each container's sha256 and the A/B partitions it carries.

Format: FBPK (fastboot pack) v1/v2 exactly as GrapheneOS adevtool src/images/fastboot-pack.ts defines it, the same
git blob at the retained build's pinned adevtool 17df3b79 and at df29e3a (fixture
scripts/tests/fixtures/fbpack-adevtool-17df3b79). adevtool's firmware.ts parses both containers as FBPK and takes every
entry named like an A/B partition, which must be PartitionData (type 1), as that partition's image; this does the
same, read-only. Nothing is extracted to disk and nothing is executed.

A partition is listed as carried only when its single entry is PartitionData, lies wholly inside the container,
overlaps no other listed entry, and its bytes equal the target-files RADIO/<partition>.img exactly. Anything else
refuses, with no output file: a target-files that is not the pinned bytes; unsafe member names; a missing,
duplicated, linked or oversized container; a non-FBPK or unsupported-version container (there is no raw fallback);
header, size, count or range inconsistencies; a duplicated A/B entry name (product or slot variants are ambiguous);
a non-PartitionData A/B entry; a partition in both containers; or an entry whose bytes differ from, or lack, its
RADIO/<partition>.img. maybeCrc32 is recorded only: the source says it is unreliable, so no checksum meaning is
given to it (ZIP CRCs are checked when members are read).

Coverage is not decided here: the observation lists exactly what the containers carry, plus the A/B partitions the
target-files has no IMAGES/ entry for; classify decides whether everything it needs is carried.

Exit status: 0 written, 3 refused (one JSON line {"refused": "<category>: <detail>"}), 2 usage.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import zipfile

SCHEMA = 'roseos.factory-firmware-observation.v1'
FORMAT = {'source': 'GrapheneOS/adevtool src/images/fastboot-pack.ts', 'gitBlob': '3e1a92e6df3a2f7deb25a955d289c8f7d21f522a',
          'revisions': ['17df3b79a73e67ad68387b35c7d41c85e1d8103c', 'df29e3a20920914f8ec4cd29e41c4db85fd78c41']}
CONTAINERS = ('bootloader', 'radio')
HEX64 = re.compile(r'[0-9a-f]{64}')
LIMITS = {'containerBytes': 1 << 30, 'partitionBytes': 1 << 30, 'entries': 1024, 'metadataBytes': 1 << 20}
V1_HEADER, V1_ENTRY = 84, 56
V2_HEADER, V2_ENTRY = 112, 104
PARTITION_DATA = 1


class Refused(Exception):
    """A fixed category and nonsecret detail."""


def _str(raw):
    end = raw.find(b'\0')
    return (raw if end < 0 else raw[:end]).decode('ascii', 'replace')


def parse_fbpk(data, label):
    """FBPK header and entries ([{type, name, product, slotted, offset, size, maybeCrc32}]), every range checked."""
    if len(data) < 8 or data[:4] != b'FBPK':
        raise Refused(f'format_unsupported: {label} is not an FBPK container (no raw fallback)')
    version, = struct.unpack_from('<I', data, 4)
    entries = []
    if version == 2:
        if len(data) < V2_HEADER:
            raise Refused(f'format_invalid: {label} is shorter than the FBPK v2 header')
        header_size, entry_size = struct.unpack_from('<II', data, 8)
        slot_type, align, count, total = struct.unpack_from('<IIII', data, 96)
        if total != len(data):
            raise Refused(f'format_invalid: {label} totalSize {total} is not its length {len(data)}')
        if header_size != V2_HEADER or entry_size != V2_ENTRY:
            raise Refused(f'format_invalid: {label} header/entry sizes {header_size}/{entry_size} are not {V2_HEADER}/{V2_ENTRY}')
        if count > LIMITS['entries'] or V2_HEADER + count * V2_ENTRY > len(data):
            raise Refused(f'format_invalid: {label} entry table ({count} entries) exceeds the container')
        table_end = V2_HEADER + count * V2_ENTRY
        for i in range(count):
            at = V2_HEADER + i * V2_ENTRY
            kind, = struct.unpack_from('<I', data, at)
            name, product = _str(data[at + 4:at + 40]), _str(data[at + 40:at + 80])
            offset, size, slotted, crc = struct.unpack_from('<QQII', data, at + 80)
            if offset < table_end or offset + size > len(data):
                raise Refused(f'format_invalid: {label} entry {name!r} data [{offset}, {offset + size}) is outside the container')
            entries.append({'type': kind, 'name': name, 'product': product, 'slotted': slotted, 'offset': offset,
                            'size': size, 'maybeCrc32': crc})
        meta = {'version': 2, 'platform': _str(data[16:32]), 'packVersion': _str(data[32:96]), 'slotType': slot_type,
                'dataAlign': align, 'totalEntries': count}
    elif version == 1:
        if len(data) < V1_HEADER:
            raise Refused(f'format_invalid: {label} is shorter than the FBPK v1 header')
        slot_type, count, total = struct.unpack_from('<III', data, 72)
        if total != len(data):
            raise Refused(f'format_invalid: {label} totalSize {total} is not its length {len(data)}')
        if count > LIMITS['entries']:
            raise Refused(f'format_invalid: {label} has more than {LIMITS["entries"]} entries')
        at = V1_HEADER
        for i in range(count):
            if at + V1_ENTRY > len(data):
                raise Refused(f'format_invalid: {label} entry header {i} exceeds the container')
            kind, = struct.unpack_from('<I', data, at)
            name = _str(data[at + 4:at + 36])
            unknown1, size, unknown2, following, crc = struct.unpack_from('<IIIII', data, at + 36)
            start = at + V1_ENTRY
            if unknown1 or unknown2:
                raise Refused(f'format_invalid: {label} entry {name!r} has nonzero reserved fields')
            if start + size > len(data):
                raise Refused(f'format_invalid: {label} entry {name!r} data exceeds the container')
            entries.append({'type': kind, 'name': name, 'product': '', 'slotted': None, 'offset': start, 'size': size,
                            'maybeCrc32': crc})
            if following < len(data):
                if following < start + size:
                    raise Refused(f'format_invalid: {label} entry {name!r} next header overlaps its data')
                at = following
            elif i != count - 1:
                raise Refused(f'format_invalid: {label} entry {name!r} ends the chain before entry {count - 1}')
        meta = {'version': 1, 'packVersion': _str(data[8:72]), 'slotType': slot_type, 'totalEntries': count}
    else:
        raise Refused(f'format_unsupported: {label} FBPK version {version} is not 1 or 2')
    return meta, entries


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def read_member(archive, name, limit):
    infos = [i for i in archive.infolist() if i.filename == name]
    if len(infos) != 1:
        raise Refused(f'member_missing: the target-files has {len(infos)} {name} entries, expected exactly one')
    info = infos[0]
    if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
        raise Refused(f'member_invalid: {name} is not a regular member')
    if info.file_size > limit:
        raise Refused(f'member_invalid: {name} exceeds {limit} bytes')
    try:
        with archive.open(info) as member:
            data = member.read(limit + 1)
    except (zipfile.BadZipFile, OSError):
        raise Refused(f'member_invalid: {name} fails its CRC or cannot be read') from None
    if len(data) != info.file_size:
        raise Refused(f'member_invalid: {name} size differs from its header')
    return data


def observe(target_files, target_files_sha256, device, build, source):
    target_files = Path(target_files)
    if not target_files.is_absolute() or target_files.is_symlink() or not target_files.is_file():
        raise Refused('input_missing: --target-files must be an absolute regular file')
    if not HEX64.fullmatch(str(target_files_sha256)) or sha256_file(target_files) != target_files_sha256:
        raise Refused('hash_mismatch: target-files differs from its pin')
    if not re.fullmatch(r'[a-z][a-z0-9]{1,31}', device) or not re.fullmatch(r'[0-9]{10}', build) or not source.strip():
        raise Refused('usage_refused: device, ten-digit build and a source are required')
    try:
        archive = zipfile.ZipFile(target_files)
    except (OSError, zipfile.BadZipFile):
        raise Refused('target_files_invalid: not a readable zip') from None
    with archive:
        names = archive.namelist()
        if any(n.startswith('/') or '\\' in n or '..' in n.split('/') or '\0' in n for n in names):
            raise Refused('target_files_unsafe: a member name is absolute or escapes')
        ab = read_member(archive, 'META/ab_partitions.txt', LIMITS['metadataBytes']).decode('ascii', 'replace').split()
        if not ab or len(set(ab)) != len(ab):
            raise Refused('target_files_invalid: META/ab_partitions.txt is empty or repeats a partition')
        abset = set(ab)
        result = {'schema': SCHEMA, 'device': device, 'build': build, 'source': source, 'synthetic': False,
                  'targetFilesSha256': target_files_sha256, 'format': FORMAT,
                  'withoutImage': sorted(p for p in ab if f'IMAGES/{p}.img' not in names)}
        carried = {}
        for kind in CONTAINERS:
            container = read_member(archive, f'RADIO/{kind}.img', LIMITS['containerBytes'])
            meta, entries = parse_fbpk(container, f'RADIO/{kind}.img')
            named = [e for e in entries if e['name'] in abset]
            seen, spans, listed = set(), [], []
            for entry in named:
                part = entry['name']
                if part in seen:
                    raise Refused(f'entry_ambiguous: RADIO/{kind}.img has more than one {part} entry')
                seen.add(part)
                if entry['type'] != PARTITION_DATA:
                    raise Refused(f'entry_invalid: RADIO/{kind}.img {part} entry is type {entry["type"]}, not PartitionData')
                if part in carried:
                    raise Refused(f'entry_ambiguous: {part} is in both RADIO/{carried[part]}.img and RADIO/{kind}.img')
                if any(entry['offset'] < end and start < entry['offset'] + entry['size'] for start, end in spans):
                    raise Refused(f'format_invalid: RADIO/{kind}.img {part} entry overlaps another partition entry')
                spans.append((entry['offset'], entry['offset'] + entry['size']))
                payload = container[entry['offset']:entry['offset'] + entry['size']]
                if f'RADIO/{part}.img' not in names:
                    raise Refused(f'entry_unmatched: RADIO/{kind}.img carries {part} but the target-files has no RADIO/{part}.img')
                standalone = read_member(archive, f'RADIO/{part}.img', LIMITS['partitionBytes'])
                if payload != standalone:
                    raise Refused(f'entry_mismatch: RADIO/{kind}.img {part} bytes differ from RADIO/{part}.img')
                carried[part] = kind
                listed.append({'name': part, 'product': entry['product'], 'slotted': entry['slotted'], 'size': entry['size'],
                               'sha256': hashlib.sha256(payload).hexdigest(), 'maybeCrc32': entry['maybeCrc32']})
            result[kind] = dict({'sha256': hashlib.sha256(container).hexdigest(), 'size': len(container),
                                 'partitions': sorted(e['name'] for e in listed), 'entries': listed,
                                 'otherEntries': len(entries) - len(named)}, fbpk=meta)
        result['uncarried'] = sorted(p for p in result['withoutImage'] if p not in carried)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    for flag in ('--target-files', '--target-files-sha256', '--device', '--build', '--source', '--out'):
        p.add_argument(flag, required=True)
    args = p.parse_args(argv)
    try:
        out = Path(args.out)
        if not out.is_absolute() or out.parent.is_symlink() or not out.parent.is_dir() or os.path.lexists(out):
            raise Refused('output_refused: --out must be a new absolute path in an existing plain directory')
        document = observe(args.target_files, args.target_files_sha256, args.device, args.build, args.source)
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(document, indent=2, sort_keys=True) + '\n')
    except Refused as error:
        print(json.dumps({'refused': str(error)}))
        return 3
    print(json.dumps({'observed': str(out), 'carried': {k: len(document[k]['partitions']) for k in CONTAINERS},
                      'uncarried': document['uncarried']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
