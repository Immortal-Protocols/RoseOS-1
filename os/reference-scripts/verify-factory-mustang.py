#!/usr/bin/env python3
"""Check an install archive against AVB-verified images. Never contacts a phone."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    release, evidence, vault, fallback = map(Path, sys.argv[1:5])
    build = sys.argv[5]
    install = release / f'mustang-install-{build}.zip'
    factory = Path(tempfile.mkdtemp(prefix='factory-', dir=evidence))
    with zipfile.ZipFile(install) as archive:
        for entry in archive.infolist():
            path = Path(entry.filename)
            assert not path.is_absolute() and '..' not in path.parts
        archive.extractall(factory)
    content = factory / f'mustang-install-{build}'
    shell = (content / 'flash-all.sh').read_text()
    script = (content / 'script.txt').read_text()
    info = (content / 'android-info.txt').read_text()
    assert 'require board=mustang' in info
    assert shell.count('flash --slot=other bootloader ') == 2
    assert script.count('flash bootloader ') == 2
    assert 'fastboot --set-active=a' in shell
    assert 'run-cmd set_active:a' in script
    assert 'erase userdata' in shell and 'erase metadata' in shell
    assert digest(content / 'avb_pkmd.bin') == digest(vault / 'mustang/avb_pkmd.bin')
    images = evidence / 'images'
    report = {'build': build, 'factory_images': {}, 'firmware': {}}
    for name in ('boot', 'init_boot', 'dtbo', 'vendor_kernel_boot', 'pvmfw', 'vendor_boot', 'vbmeta'):
        actual = digest(content / f'{name}.img')
        assert actual == digest(images / f'{name}.img'), name
        report['factory_images'][name] = actual
    segments = sorted(content.glob('super_*.img'), key=lambda p: int(re.search(r'_(\d+)\.img$', p.name)[1]))
    assert segments and [int(re.search(r'_(\d+)\.img$', p.name)[1]) for p in segments] == list(range(1, len(segments) + 1))
    raw = factory / 'factory-super.raw'
    subprocess.run([str(release / 'bin/simg2img'), *map(str, segments), str(raw)], check=True)
    partitions = factory / 'factory-partitions'
    partitions.mkdir()
    subprocess.run([str(release / 'bin/lpunpack'), str(raw), str(partitions)], check=True)
    for name in ('product', 'system', 'system_dlkm', 'system_ext', 'vendor', 'vendor_dlkm'):
        matches = list(partitions.glob(name + '.img')) + list(partitions.glob(name + '_a.img'))
        assert len(matches) == 1, (name, matches)
        actual = digest(matches[0])
        assert actual == digest(images / f'{name}.img'), name
        report['factory_images'][name] = actual
    # Compare firmware to an already signature-verified official baseline.
    official = list(fallback.glob('**/mustang-install-2026100601.zip'))
    assert len(official) == 1, 'Expected one authenticated official 0601 archive'
    with official[0].open('rb') as stream:
        verified = subprocess.run(
            ['ssh-keygen', '-Y', 'verify', '-f', str(fallback / 'allowed_signers'),
             '-I', 'contact@grapheneos.org', '-n', 'factory images',
             '-s', str(official[0]) + '.sig'],
            stdin=stream, capture_output=True, text=True, check=True)
    (evidence / 'official-baseline-signature.txt').write_text(verified.stdout + verified.stderr)
    with zipfile.ZipFile(official[0]) as archive:
        names = archive.namelist()
        is_firmware = lambda name: name.startswith(('bootloader-', 'radio-')) or name.endswith('.ec.bin')
        expected_firmware = {Path(name).name for name in names if is_firmware(Path(name).name)}
        actual_firmware = {item.name for item in content.iterdir() if is_firmware(item.name)}
        assert any(name.startswith('bootloader-') for name in expected_firmware)
        assert any(name.startswith('radio-') for name in expected_firmware)
        assert actual_firmware == expected_firmware, (actual_firmware, expected_firmware)
        for item in content.iterdir():
            if is_firmware(item.name):
                matches = [name for name in names if Path(name).name == item.name]
                assert len(matches) == 1, item.name
                expected = hashlib.sha256(archive.read(matches[0])).hexdigest()
                assert digest(item) == expected, item.name
                report['firmware'][item.name] = expected
    report['both_slot_bootloader_handling'] = True
    report['physical_device_tested'] = False
    (evidence / 'factory-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Factory partitions match AVB-verified images; firmware matches authenticated official baseline; both-slot install handling retained.')


if __name__ == '__main__':
    main()
