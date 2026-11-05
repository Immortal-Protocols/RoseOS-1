#!/usr/bin/env python3
"""OS Product's raised-edge artifact check (#410, 1C/2A item 4), as Release verification runs it on target-files.

One target-files zip must carry the raised edge: #548's staged gesture in Launcher3QuickStep, the WindowManager-Shell
resize and the Rose chin's microphone-input mute slot in SystemUI, and both build switches. The checks are the
roseEdge section of the pipeline config (os/release-pipeline/frankel.json), following OS Product's checker:

- every prop (key=value) is assigned in the listed build.prop files, and nowhere to another value;
- for every class entry, a classes*.dex of an APK whose target-files path fully matches the entry's pattern defines
  that class (and, when the entry names one, a direct or virtual method of that name). R8 may rename a class its
  keep rules do not cover (SystemUI keeps com.android.wm.shell.* names, not subpackages), so an entry may also name a
  string literal only that code holds: when no dex defines the class, the entry passes on a dex whose string table
  holds exactly that whole literal. An entry without a literal is checked by class only, so a rename fails closed;
- every literals entry is required as well, not as a fallback: a dex of a matching APK holds that whole literal. It
  proves code a class check cannot (a change inside classes that existed before it, such as #548's diagnostics switch).

Read-only and tool-free: the dex header, string, type and method ids, class_defs and class_data are read directly
(versions 035 to 041, including 041 containers of several dex sections), so the builder needs no dexdump. A dex that
cannot be read fails closed: its classes and strings count as absent and the failure names it.
Not proven: compile, behaviour on a device, publication.

usage: edge_artifact.py TARGET_FILES.zip [--config os/release-pipeline/frankel.json]
Prints one PASS/FAIL line per check and the JSON receipt; exit 0 when every check passes, 1 otherwise, 2 for an
unusable spec or input.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/release-pipeline/frankel.json'
REQUIRED = ('props', 'propFiles', 'classes')
CLASS_KEYS = {'apk', 'class', 'method', 'literal'}
LITERAL_KEYS = {'apk', 'literal'}
DEX_NAME = re.compile(r'classes\d*\.dex')
MAGIC = re.compile(rb'dex\n0(3[5-9]|4[01])\x00')
PROP = re.compile(r'[A-Za-z0-9_.-]+=\S+')
DESCRIPTOR = re.compile(r'L[A-Za-z0-9_$]+(/[A-Za-z0-9_$]+)*;')
ENDIAN = 0x12345678
# What reading one zip member can raise when its bytes are damaged.
MEMBER_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, ValueError)


class DexError(Exception):
    pass


class SpecError(Exception):
    pass


def missing(spec):
    """Names of the roseEdge keys that are absent or empty (for preflight and the verifier's setup check)."""
    spec = spec if isinstance(spec, dict) else {}
    return [f'roseEdge.{key}' for key in REQUIRED if not spec.get(key)]


def apk_pattern(value, where):
    if not isinstance(value, str) or not value.endswith(r'\.apk'):
        raise SpecError(f'{where} apk must be a pattern of .apk paths')
    try:
        re.compile(value)
    except re.error:
        raise SpecError(f'{where} apk is not a valid pattern') from None


def ascii_literal(value):
    return isinstance(value, str) and bool(value) and value.isascii() and '\x00' not in value


def validate(spec):
    """The spec, or SpecError: ASCII prop lines, build.prop paths, class entries with exactly apk/class/method/literal
    and, when present, literals entries with exactly apk/literal."""
    absent = missing(spec)
    if absent:
        raise SpecError('setup missing: ' + ', '.join(absent))
    if not all(isinstance(p, str) and PROP.fullmatch(p) and p.isascii() for p in spec['props']):
        raise SpecError('roseEdge.props must be key=value lines')
    if not all(isinstance(p, str) and p.endswith('build.prop') and '..' not in p.split('/') for p in spec['propFiles']):
        raise SpecError('roseEdge.propFiles must be target-files build.prop paths')
    for entry in spec['classes']:
        if not isinstance(entry, dict) or set(entry) != CLASS_KEYS:
            raise SpecError(f'roseEdge.classes entries need exactly {sorted(CLASS_KEYS)}')
        apk_pattern(entry['apk'], 'roseEdge.classes')
        if not isinstance(entry['class'], str) or not DESCRIPTOR.fullmatch(entry['class']):
            raise SpecError('roseEdge.classes class must be a dex class descriptor')
        if entry['method'] is not None and not (isinstance(entry['method'], str) and re.fullmatch(r'[A-Za-z_$][\w$]*', entry['method'])):
            raise SpecError('roseEdge.classes method must be a method name or null')
        if entry['literal'] is not None and not ascii_literal(entry['literal']):
            raise SpecError('roseEdge.classes literal must be a non-empty ASCII string or null')
    if not isinstance(spec.get('literals', []), list):
        raise SpecError('roseEdge.literals must be a list')
    for entry in spec.get('literals', []):
        if not isinstance(entry, dict) or set(entry) != LITERAL_KEYS:
            raise SpecError(f'roseEdge.literals entries need exactly {sorted(LITERAL_KEYS)}')
        apk_pattern(entry['apk'], 'roseEdge.literals')
        if not ascii_literal(entry['literal']):
            raise SpecError('roseEdge.literals literal must be a non-empty ASCII string')
    return spec


# ---------------------------------------------------------------- dex reading

def dex_string(literal):
    """OS Product's form of a literal as a whole dex string_data_item: uleb128 UTF-16 length, the MUTF-8 bytes (ASCII
    here) and the terminating NUL, after the previous item's NUL. Kept for reference; the check reads the string table."""
    size, out = len(literal), bytearray()
    while True:
        byte = size & 0x7f
        size >>= 7
        out.append(byte | (0x80 if size else 0))
        if not size:
            break
    return b'\x00' + bytes(out) + literal.encode('ascii') + b'\x00'


def u32(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise DexError(f'offset {offset} is outside the dex')
    return struct.unpack_from('<I', data, offset)[0]


def uleb128(data, pos):
    result = shift = 0
    while True:
        if pos >= len(data):
            raise DexError('truncated uleb128')
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7f) << shift
        if byte < 0x80:
            return result, pos
        shift += 7
        if shift > 28:
            raise DexError('uleb128 longer than five bytes')


class Section:
    """One dex: the whole file for versions 035-040, or one header's section of a 041 container. Every offset is from
    the start of the file (a 041 container's sections share its string table)."""

    def __init__(self, data, offset):
        self.data = data
        version = int(data[offset + 4:offset + 7])
        size, header, endian = struct.unpack_from('<3I', data, offset + 0x20)
        if endian != ENDIAN:
            raise DexError('not a little-endian dex')
        if header != (0x78 if version >= 41 else 0x70):
            raise DexError(f'dex {version:03d} header size {header}')
        if version >= 41:
            container, start = struct.unpack_from('<2I', data, offset + 0x70)
            if start != offset or container != len(data) or size < header or offset + size > len(data):
                raise DexError('dex container header does not describe this file')
        elif offset or size != len(data):
            raise DexError('dex file size differs from its header')
        self.size = size
        (self.strings, self.strings_off, self.types, self.types_off) = struct.unpack_from('<4I', data, offset + 0x38)
        (self.methods, self.methods_off, self.classes, self.classes_off) = struct.unpack_from('<4I', data, offset + 0x58)
        for count, start, width in ((self.strings, self.strings_off, 4), (self.types, self.types_off, 4),
                                    (self.methods, self.methods_off, 8), (self.classes, self.classes_off, 32)):
            if count and (start < header or start + count * width > len(data)):
                raise DexError('an id table lies outside the dex')

    def string(self, index):
        """The MUTF-8 bytes of string `index` (without its NUL) and its UTF-16 length."""
        if index >= self.strings:
            raise DexError(f'string index {index} out of range')
        pos = u32(self.data, self.strings_off + 4 * index)
        length, pos = uleb128(self.data, pos)
        end = self.data.find(b'\x00', pos)
        if end < 0:
            raise DexError('unterminated string')
        return self.data[pos:end], length

    def type_name(self, index):
        if index >= self.types:
            raise DexError(f'type index {index} out of range')
        return self.string(u32(self.data, self.types_off + 4 * index))[0]

    def method_name(self, index):
        if index >= self.methods:
            raise DexError(f'method index {index} out of range')
        return self.string(u32(self.data, self.methods_off + 8 * index + 4))[0]

    def has_string(self, literal):
        target = literal.encode('ascii')
        return any(self.string(i) == (target, len(literal)) for i in range(self.strings))

    def defined_methods(self, descriptor):
        """None when this section defines no class `descriptor`, else the names of the methods it defines."""
        target = descriptor.encode('ascii')
        for i in range(self.classes):
            item = self.classes_off + 32 * i
            if self.type_name(u32(self.data, item)) != target:
                continue
            data_off = u32(self.data, item + 24)
            names = set()
            if not data_off:
                return names
            pos, sizes = data_off, []
            for _ in range(4):
                value, pos = uleb128(self.data, pos)
                sizes.append(value)
            for _ in range(sizes[0] + sizes[1]):  # static and instance fields: field_idx_diff, access_flags
                pos = uleb128(self.data, uleb128(self.data, pos)[1])[1]
            for count in sizes[2:]:  # direct, then virtual methods; each list's indices restart from zero
                index = 0
                for _ in range(count):
                    diff, pos = uleb128(self.data, pos)
                    pos = uleb128(self.data, uleb128(self.data, pos)[1])[1]  # access_flags, code_off
                    index += diff
                    names.add(self.method_name(index).decode('utf-8', 'replace'))
            return names
        return None


def sections(data):
    """The dex sections of one classes*.dex, or DexError when it is not a readable dex."""
    found, offset = [], 0
    while True:
        if not MAGIC.fullmatch(data[offset:offset + 8]):
            raise DexError('no dex magic' + (f' at offset {offset}' if offset else ''))
        if len(data) < offset + (0x78 if int(data[offset + 4:offset + 7]) >= 41 else 0x70):
            raise DexError('truncated dex header')
        section = Section(data, offset)
        found.append(section)
        if int(data[offset + 4:offset + 7]) < 41:
            return found
        offset += section.size
        if offset == len(data):
            return found


# ---------------------------------------------------------------- the check

def check_props(archive, spec):
    names = set(archive.namelist())
    values = {}
    for name in spec['propFiles']:
        if name in names:
            for line in archive.read(name).decode('utf-8', 'replace').splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key, value = line.split('=', 1)
                    values.setdefault(key.strip(), []).append((name, value.strip()))
    results = []
    for prop in spec['props']:
        key, value = prop.split('=', 1)
        seen = values.get(key, [])
        files = sorted({name for name, v in seen if v == value})
        others = sorted({f'{name}: {v}' for name, v in seen if v != value})
        results.append({'prop': prop, 'pass': bool(files) and not others, 'files': files, 'conflicting': others})
    return results


def scan(dexes, unreadable, probe):
    """The first `apk!dex` whose sections satisfy probe(section), in APK then dex order. A dex whose ids point outside
    it is unreadable from then on: it counts as not holding anything, and is named."""
    for apk, dex, parsed in dexes:
        where = f'{apk}!{dex}'
        if where in unreadable:
            continue
        try:
            if any(probe(section) for section in parsed):
                return where
        except DexError as error:
            unreadable[where] = str(error)
    return None


def matching(archive, pattern, cache):
    """The APKs whose whole target-files path matches pattern, their readable dex, and those that could not be read."""
    apks = sorted(n for n in archive.namelist() if re.fullmatch(pattern, n))
    dexes, unreadable = [], {}
    for apk in apks:
        if apk not in cache:
            cache[apk] = read_apk(archive, apk)
        dexes += [(apk, dex, parsed) for dex, parsed in cache[apk]['dex']]
        unreadable.update({f'{apk}!{dex}': why for dex, why in cache[apk]['unreadable']})
    return apks, dexes, unreadable


def check_classes(archive, spec, cache):
    results = []
    for entry in spec['classes']:
        apks, dexes, unreadable = matching(archive, entry['apk'], cache)

        def defines(section, entry=entry):
            methods = section.defined_methods(entry['class'])
            return methods is not None and (entry['method'] is None or entry['method'] in methods)
        where, by = scan(dexes, unreadable, defines), 'class'
        if where is None and entry['literal']:
            where, by = scan(dexes, unreadable, lambda section, literal=entry['literal']: section.has_string(literal)), 'literal'
        results.append({'class': entry['class'], 'method': entry['method'], 'literal': entry['literal'],
                        'pass': where is not None, 'by': by if where else None, 'where': where, 'apks': apks,
                        'unreadable': [f'{k}: {v}' for k, v in sorted(unreadable.items())]})
    return results


def check_literals(archive, spec, cache):
    """Every required literal, as a whole string-table entry of a dex of a matching APK."""
    results = []
    for entry in spec.get('literals', []):
        apks, dexes, unreadable = matching(archive, entry['apk'], cache)
        where = scan(dexes, unreadable, lambda section, literal=entry['literal']: section.has_string(literal))
        results.append({'literal': entry['literal'], 'pass': where is not None, 'where': where, 'apks': apks,
                        'unreadable': [f'{k}: {v}' for k, v in sorted(unreadable.items())]})
    return results


def read_apk(archive, name):
    out = {'sha256': None, 'dex': [], 'unreadable': []}
    try:
        data = archive.read(name)
    except MEMBER_ERRORS as error:
        out['unreadable'].append(('(apk)', f'its target-files member is damaged ({error.__class__.__name__})'))
        return out
    out['sha256'] = hashlib.sha256(data).hexdigest()
    try:
        apk = zipfile.ZipFile(io.BytesIO(data))
        dexes = sorted((n for n in apk.namelist() if DEX_NAME.fullmatch(n)), key=lambda n: (len(n), n))
    except zipfile.BadZipFile:
        out['unreadable'].append(('(apk)', 'not a readable APK archive'))
        return out
    if not dexes:
        out['unreadable'].append(('(apk)', 'no classes*.dex'))
    for dex in dexes:
        try:
            out['dex'].append((dex, sections(apk.read(dex))))
        except DexError as error:
            out['unreadable'].append((dex, str(error)))
        except MEMBER_ERRORS as error:
            out['unreadable'].append((dex, f'damaged APK member ({error.__class__.__name__})'))
    return out


def check(target_files, spec):
    """(failures, receipt) for one target-files zip. Failures name each failed check; the receipt records what was read
    (APK sha256s, where each class or literal was found) and is evidence of code inclusion only."""
    validate(spec)
    cache = {}
    with zipfile.ZipFile(target_files) as archive:
        props = check_props(archive, spec)
        classes = check_classes(archive, spec, cache)
        literals = check_literals(archive, spec, cache)
    failures = []
    for p in props:
        if not p['pass']:
            failures.append(f'raised-edge prop {p["prop"]} ' + (f'conflicts with {p["conflicting"]}' if p['conflicting']
                                                               else f'is not set in {spec["propFiles"]}'))
    for c in classes:
        if not c['pass']:
            what = c['class'] + (f' with method {c["method"]}' if c['method'] else '')
            failures.append(f'raised-edge class {what} is not defined in the dex of the {len(c["apks"])} matching APK(s)'
                            + (f', nor is the literal "{c["literal"]}" there' if c['literal'] else '')
                            + (f'; unreadable: {c["unreadable"]}' if c['unreadable'] else ''))
    for item in literals:
        if not item['pass']:
            failures.append(f'raised-edge literal "{item["literal"]}" is not a whole dex string in the {len(item["apks"])} '
                            'matching APK(s)' + (f'; unreadable: {item["unreadable"]}' if item['unreadable'] else ''))
    return failures, {'props': props, 'classes': classes, 'literals': literals,
                      'apkSha256': {apk: cache[apk]['sha256'] for apk in sorted(cache)}}


def labels(receipt):
    out = [('PASS ' if p['pass'] else 'FAIL ') + f'prop {p["prop"]}' for p in receipt['props']]
    for c in receipt['classes']:
        what = c['class'].strip('L;').replace('/', '.') + (f'.{c["method"]}' if c['method'] else '')
        out.append(('PASS ' if c['pass'] else 'FAIL ') + f'class {what} in {len(c["apks"])} candidate apk(s)'
                   + (f' by {c["by"]}' if c['by'] else ''))
    for item in receipt['literals']:
        out.append(('PASS ' if item['pass'] else 'FAIL ') + f'literal "{item["literal"]}" in {len(item["apks"])} candidate apk(s)')
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('target_files', type=Path)
    p.add_argument('--config', type=Path, default=CONFIG, help='pipeline config holding the roseEdge section')
    a = p.parse_args(argv)
    try:
        spec = json.loads(a.config.read_text()).get('roseEdge')
        failures, receipt = check(a.target_files, spec)
    except (OSError, ValueError, SpecError, DexError, zipfile.BadZipFile) as error:
        print(json.dumps({'verdict': 'INVALID', 'error': f'{error.__class__.__name__}: {error}'}), file=sys.stderr)
        return 2
    for line in labels(receipt):
        print(line)
    print(json.dumps({'verdict': 'FAIL' if failures else 'PASS', 'failures': failures, **receipt}, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
