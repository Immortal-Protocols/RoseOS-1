#!/usr/bin/env python3
"""Install the task-owned stable Android SDK layout the os-release app build expects, from existing public tools.

Layout under ROOT (default /home/pixelbuild/rose-actions/tools/android-sdk):
  platforms/android-35/android.jar           -> <source>/prebuilts/sdk/35/public/android.jar
  build-tools/android-15/{aapt,aapt2,zipalign} -> <source>/prebuilts/sdk/tools/linux/bin/*
  build-tools/android-15/d8                  <- copy of <source>/prebuilts/sdk/tools/linux/bin/d8
  build-tools/android-15/lib/d8.jar          -> <source>/prebuilts/sdk/tools/linux/lib/d8.jar
  build-tools/android-15/core-lambda-stubs.jar   -> <source>/prebuilts/sdk/tools/core-lambda-stubs.jar
  build-tools/android-15/lib/apksigner.jar   <- copy of the retained public SDK apksigner.jar
  build-tools/android-15/apksigner           wrapper running that jar with <source>/prebuilts/jdk/jdk21
The d8 launcher resolves symlinks to itself and then looks for lib/d8.jar (or ../framework) next to the resolved
path; the prebuilt keeps the JAR in linux/lib, so a linked launcher cannot find it. The launcher is copied instead and
its JAR linked beside it (the original source is not modified). Every source must already exist (nothing is downloaded or invented); each installed tool is then run once and the
layout is recorded with hashes in ROOT/layout-receipt.json. Run during the coordinated builder window only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

TOOLS = ('aapt', 'aapt2', 'zipalign')

def plan(source, apksigner_jar):
    source = Path(source)
    bin_dir = source/'prebuilts/sdk/tools/linux/bin'
    links = {'platforms/android-35/android.jar': source/'prebuilts/sdk/35/public/android.jar',
        'build-tools/android-15/core-lambda-stubs.jar': source/'prebuilts/sdk/tools/core-lambda-stubs.jar'}
    links.update({f'build-tools/android-15/{t}': bin_dir/t for t in TOOLS})
    links['build-tools/android-15/lib/d8.jar'] = source/'prebuilts/sdk/tools/linux/lib/d8.jar'
    missing = [str(p) for p in [*links.values(), bin_dir/'d8', Path(apksigner_jar),
        source/'prebuilts/jdk/jdk21/linux-x86/bin/java'] if not p.is_file()]
    return links, missing

def install(root, source, apksigner_jar, run=subprocess.run):
    root, source = Path(root), Path(source)
    links, missing = plan(source, apksigner_jar)
    if missing:
        raise SystemExit('missing existing tools, nothing installed: ' + ', '.join(missing))
    if root.exists():
        raise SystemExit(f'{root} already exists; remove it deliberately before reinstalling')
    for relative, target in links.items():
        (root/relative).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, root/relative)
    tools = root/'build-tools/android-15'
    d8 = source/'prebuilts/sdk/tools/linux/bin/d8'
    (tools/'d8').write_bytes(d8.read_bytes())
    (tools/'d8').chmod(0o755)
    (tools/'lib/apksigner.jar').write_bytes(Path(apksigner_jar).read_bytes())
    java = source/'prebuilts/jdk/jdk21/linux-x86/bin/java'
    wrapper = tools/'apksigner'
    wrapper.write_text(f'#!/bin/sh\nexec "{java}" -jar "{tools}/lib/apksigner.jar" "$@"\n')
    wrapper.chmod(0o755)
    # Each tool must actually run from the new layout before it is recorded.
    for argv in ([str(tools/'aapt'), 'version'], [str(tools/'aapt2'), 'version'], [str(tools/'d8'), '--version'],
                 [str(wrapper), '--version']):
        run(argv, check=True, capture_output=True)
    receipt = {'receiptType': 'android-sdk-layout', 'root': str(root), 'source': str(source),
        'files': {r: {'target': str(t), 'sha256': hashlib.sha256(t.read_bytes()).hexdigest()} for r, t in sorted(links.items())},
        'd8Launcher': {'copiedFrom': str(d8), 'sha256': hashlib.sha256(d8.read_bytes()).hexdigest(),
            'reason': 'launcher resolves lib/d8.jar beside its resolved path; the prebuilt JAR is in linux/lib'},
        'apksignerJarSha256': hashlib.sha256((tools/'lib/apksigner.jar').read_bytes()).hexdigest()}
    (root/'layout-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--root', default='/home/pixelbuild/rose-actions/tools/android-sdk')
    p.add_argument('--source', default='/home/pixelbuild/grapheneos-2026100600')
    p.add_argument('--apksigner-jar', required=True, help='Retained public SDK apksigner.jar')
    a = p.parse_args()
    print(json.dumps(install(a.root, a.source, a.apksigner_jar), indent=2, sort_keys=True))
