#!/usr/bin/env python3
"""Scratch-disk inventory and blank-device-only preparation for the retained H100 guest.

Default mode is read-only. It runs lsblk, findmnt and `wipefs --no-act` and resolves
/dev/disk/by-id links, then prints JSON. Each disk is classified as eligible scratch only when ALL hold:
- the provider names it a local SSD (GCE guest by-id link google-local-nvme-ssd-N or google-local-ssd-N);
- it backs no mounted path (/, /boot, /boot/efi or any other), as a device or as an ancestor of one;
- it has no partitions, filesystem, partition table or wipefs signature, and is not mounted.

--prepare DEVICE --confirm DEVICE formats and mounts exactly that one device, and only when it
re-classifies as eligible at that moment: mkfs.ext4 with label models-scratch, then mount on
/mnt/models-scratch. No wildcard, no fstab edit, no other disk touched. Exit 3 on refusal.
"""
import argparse
import json
import os
import subprocess
import sys

LOCAL_SSD_PREFIXES = ('google-local-nvme-ssd-', 'google-local-ssd-')
MOUNT = '/mnt/models-scratch'
LABEL = 'models-scratch'
EXIT_REFUSED = 3


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _mounts(node):
    # util-linux >= 2.37 reports a mountpoints list; older lsblk reports a single mountpoint.
    return [m for m in (node.get('mountpoints') or [node.get('mountpoint')]) if m]


def _walk(nodes, parent=None):
    for node in nodes or []:
        yield node, parent
        yield from _walk(node.get('children'), node)


def classify(lsblk, by_id, signatures):
    """Pure classification. lsblk: `lsblk -J -b -o ...` output; by_id: {link name: /dev path};
    signatures: {/dev path: [wipefs signature dicts]}."""
    nodes = list(_walk(lsblk.get('blockdevices')))
    parents = {n['path']: (p['path'] if p else None) for n, p in nodes}
    protected = set()
    for node, _ in nodes:
        if _mounts(node):
            path = node['path']
            while path:  # the mounted device and every ancestor
                protected.add(path)
                path = parents.get(path)
    names = {}
    for link, dev in by_id.items():
        names.setdefault(dev, []).append(link)
    report = []
    for node, parent in nodes:
        if node.get('type') != 'disk' or parent is not None:
            continue
        path = node['path']
        links = sorted(names.get(path, []))
        provider = [l for l in links if l.startswith(LOCAL_SSD_PREFIXES) and '-part' not in l]
        reasons = []
        if not provider:
            reasons.append('not identified by the provider as a local SSD')
        if path in protected:
            reasons.append('backs a mounted path (boot disk or ancestor)')
        if node.get('children'):
            reasons.append('has partitions')
        if node.get('fstype'):
            reasons.append(f"has filesystem {node['fstype']}")
        if node.get('pttype'):
            reasons.append(f"has partition table {node['pttype']}")
        if _mounts(node):
            reasons.append('is mounted')
        if signatures.get(path):
            reasons.append('wipefs reports signatures')
        report.append({'path': path, 'size': node.get('size'), 'model': node.get('model'), 'serial': node.get('serial'),
                       'by_id': links, 'provider_local_ssd': provider, 'eligible': not reasons, 'reasons': reasons})
    return report


def collect(run=_run):
    columns = 'NAME,PATH,TYPE,SIZE,MODEL,SERIAL,FSTYPE,PTTYPE,'
    result = run(['lsblk', '-J', '-b', '-o', columns + 'MOUNTPOINTS'])
    if result.returncode != 0:
        result = run(['lsblk', '-J', '-b', '-o', columns + 'MOUNTPOINT'])
    lsblk = json.loads(result.stdout)
    by_id = {}
    base = '/dev/disk/by-id'
    if os.path.isdir(base):
        for link in os.listdir(base):
            by_id[link] = os.path.realpath(os.path.join(base, link))
    signatures = {}
    for node, parent in _walk(lsblk.get('blockdevices')):
        if node.get('type') == 'disk' and parent is None:
            out = run(['wipefs', '--no-act', '--json', node['path']]).stdout.strip()
            signatures[node['path']] = json.loads(out).get('signatures', []) if out else []
    return lsblk, by_id, signatures


def prepare(device, confirm, run=_run):
    if device != confirm:
        return False, 'refused: --confirm must repeat the exact device path'
    report = classify(*collect(run))
    match = [d for d in report if d['path'] == device]
    if not match:
        return False, f'refused: {device} is not a whole disk in the inventory'
    if not match[0]['eligible']:
        return False, f"refused: {device} is not eligible: {'; '.join(match[0]['reasons'])}"
    for cmd in (['mkfs.ext4', '-L', LABEL, device], ['mkdir', '-p', MOUNT], ['mount', device, MOUNT]):
        result = run(cmd)
        if result.returncode != 0:
            return False, f"failed: {' '.join(cmd)} exited {result.returncode}"
    return True, f'prepared {device} as ext4 {LABEL} on {MOUNT}'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--prepare', metavar='DEVICE')
    p.add_argument('--confirm', metavar='DEVICE')
    a = p.parse_args(argv)
    if a.prepare:
        ok, message = prepare(a.prepare, a.confirm)
        print(json.dumps({'prepare': a.prepare, 'ok': ok, 'message': message}))
        return 0 if ok else EXIT_REFUSED
    print(json.dumps({'disks': classify(*collect())}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
