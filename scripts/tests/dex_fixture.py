"""Synthetic dex files for the raised-edge check tests (scripts/edge_artifact.py) and the pipeline's stand-in APKs.

Real format where the reader parses it: the header, string ids and string data (sorted, uleb128 UTF-16 length, NUL),
type, proto and method ids, class_defs and class_data (methods without code), for versions 035-040 and for 041
containers whose sections share one string table at the end, as d8 writes them. Not runnable code: no code items, no
map list, checksum or signature. The real d8 output the reader is also checked against is in fixtures/rose-edge-dex."""
import io
import struct
import zipfile

ENDIAN = 0x12345678


def uleb(value):
    out = bytearray()
    while True:
        byte, value = value & 0x7f, value >> 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def string_item(text):
    return uleb(len(text)) + text.encode('ascii') + b'\x00'


class _Layout:
    def __init__(self):
        self.out = bytearray()

    def put(self, data, align=4):
        while len(self.out) % align:
            self.out.append(0)
        offset = len(self.out)
        self.out += data
        return offset


def dex(classes, strings=(), version=39):
    """One dex: classes is [(descriptor, [method names])]; strings are extra string-table entries (literals)."""
    if version >= 41:
        return container([(classes, strings)])
    return _build([(classes, strings)], version)


def container(sections):
    """A dex 041 container of several sections, each (classes, strings) as dex() takes them."""
    return _build(sections, 41)


def _build(sections, version):
    header_size = 0x78 if version >= 41 else 0x70
    table = sorted({'V'} | {d for classes, extra in sections for d, _ in classes}
                   | {m for classes, extra in sections for _, methods in classes for m in methods}
                   | {s for _, extra in sections for s in extra})
    index = {s: i for i, s in enumerate(table)}
    layout = _Layout()
    headers = []
    for classes, _ in sections:
        start = layout.put(bytes(header_size))
        types = sorted({'V'} | {d for d, _ in classes}, key=index.get)
        type_index = {t: i for i, t in enumerate(types)}
        types_off = layout.put(b''.join(struct.pack('<I', index[t]) for t in types))
        proto_off = layout.put(struct.pack('<3I', index['V'], type_index['V'], 0))
        methods = sorted({(type_index[d], index[m], m) for d, names in classes for m in names})
        method_index = {(cls, name): i for i, (cls, _, name) in enumerate(methods)}
        methods_off = layout.put(b''.join(struct.pack('<HHI', cls, 0, name) for cls, name, _ in methods))
        defs_off = layout.put(bytes(32 * len(classes)))
        headers.append([start, types, types_off, proto_off, methods, methods_off, classes, defs_off])
        # Each section's class_data follows its ids; the shared string table comes last, as d8 places them.
        for n, (descriptor, names) in enumerate(classes):
            direct = sorted(method_index[(type_index[descriptor], m)] for m in names if m.startswith('<'))
            virtual = sorted(method_index[(type_index[descriptor], m)] for m in names if not m.startswith('<'))
            data = uleb(0) + uleb(0) + uleb(len(direct)) + uleb(len(virtual))
            for group in (direct, virtual):
                previous = 0
                for i, m in enumerate(group):
                    data += uleb(m - previous if i else m) + uleb(0x1) + uleb(0)
                    previous = m
            data_off = layout.put(data, align=1) if names else 0
            struct.pack_into('<8I', layout.out, defs_off + 32 * n, type_index[descriptor], 0x1, 0xffffffff, 0,
                             0xffffffff, 0, data_off, 0)
    strings_off = layout.put(bytes(4 * len(table)))
    for i, text in enumerate(table):
        struct.pack_into('<I', layout.out, strings_off + 4 * i, layout.put(string_item(text), align=1))
    layout.put(b'', align=4)
    total = len(layout.out)
    starts = [h[0] for h in headers] + [total]
    for n, (start, types, types_off, proto_off, methods, methods_off, classes, defs_off) in enumerate(headers):
        size = starts[n + 1] - start if version >= 41 else total
        layout.out[start:start + 8] = b'dex\n0%02d\x00' % version
        struct.pack_into('<3I', layout.out, start + 0x20, size, header_size, ENDIAN)
        struct.pack_into('<12I', layout.out, start + 0x38, len(table), strings_off, len(types), types_off, 1, proto_off,
                         0, 0, len(methods), methods_off, len(classes), defs_off)
        if version >= 41:
            struct.pack_into('<2I', layout.out, start + 0x70, total, start)
    return bytes(layout.out)


def apk(dexes, extra=None):
    """An APK-shaped archive: classes.dex, classes2.dex, ... from the given dex bytes, plus any extra members."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for n, data in enumerate(dexes):
            archive.writestr(zipfile.ZipInfo('classes.dex' if n == 0 else f'classes{n + 1}.dex', (2009, 1, 1, 0, 0, 0)), data)
        for name, data in (extra or {}).items():
            archive.writestr(zipfile.ZipInfo(name, (2009, 1, 1, 0, 0, 0)), data)
    return buffer.getvalue()
