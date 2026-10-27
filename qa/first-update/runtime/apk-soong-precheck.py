#!/usr/bin/env python3
"""Advisory pre-build check (QA, read-only, offline): report the APK properties that decide whether
the pinned Soong android_app_import path can rewrite a presigned APK (INFRA-BUILDER-008):
native libraries (uncompressEmbeddedJniLibs), dex compression (conditional uncompress), and
zipalign -c -p 4 equivalence (stored entries 4-byte aligned, stored .so 4096-byte aligned).
A rewrite after signing would invalidate the APK signature. This script does not prove what
Soong did; built-artifact byte identity (verify-target-files.py) remains the evidence.
Usage: apk-soong-precheck.py APK... ; prints JSON, exit 1 if any APK has rewrite triggers."""
import sys, json, zipfile, struct

def data_offset(f, info):
    f.seek(info.header_offset)
    h = f.read(30)
    name_len, extra_len = struct.unpack('<HH', h[26:30])
    return info.header_offset + 30 + name_len + extra_len

def check(path):
    r = {'apk': path, 'nativeLibs': [], 'compressedDex': [], 'misaligned': []}
    with open(path, 'rb') as f, zipfile.ZipFile(f) as z:
        for i in z.infolist():
            n = i.filename
            if n.startswith('lib/') and n.endswith('.so'):
                r['nativeLibs'].append(n)
            if n.startswith('classes') and n.endswith('.dex') and i.compress_type != zipfile.ZIP_STORED:
                r['compressedDex'].append(n)
            if i.compress_type == zipfile.ZIP_STORED:
                align = 4096 if n.endswith('.so') else 4
                if data_offset(f, i) % align:
                    r['misaligned'].append(n)
    r['zipalignCheckP4'] = not r['misaligned']
    # Native libs or misalignment are rewrite triggers in the ordinary presigned path.
    # Compressed dex is a trigger only if Soong decides to uncompress dex (privileged, or
    # dexpreopt enabled); reported separately so the builder can confirm the decision.
    r['rewriteTriggers'] = bool(r['nativeLibs'] or r['misaligned'])
    return r

reports = [check(p) for p in sys.argv[1:]]
print(json.dumps(reports, indent=1))
sys.exit(1 if any(r['rewriteTriggers'] for r in reports) else 0)
