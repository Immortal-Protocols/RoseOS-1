"""Image-content gate for the Cuttlefish emulator alpha (INFRA-ALPHA-087/088): the OS alpha contract pinned by
preparation.osDelta checked against the partitions actually packed into the built super.img, never against loose APKs
or source.

super.img (unsparsed with simg2img when sparse) must be an LP super image; lpunpack writes its partitions; each
inspected partition is extracted with the extractor for its detected file system (ext4: debugfs rdump; erofs:
fsck.erofs --extract) and must be the file system the probe recorded. Every tool is an explicit configured path;
an absent tool or an unknown format stops the gate and names it. Nothing is installed or guessed.

Checks, all from extracted partition content:
  * each contract app at its partition path: exact bytes (sha256), package and versionCode (aapt) and exactly the
    approved app signer (apksigner);
  * framework-res.apk's compiled computer-control Agents, Targets and PrototypeAgents arrays and LockedSessions bool,
    derived from the bound background-policy.json (whose app digests must equal the contract's);
  * the tray property's last assignment in the system build.prop;
  * the contract's Seedvault closure: each compiled OS module APK (Seedvault, LocalContactsBackup) at its system path,
    a non-empty regular file whose aapt package is the pinned one (no version, digest or signer is imposed on these
    compiled modules), and each required XML at its path, a non-empty regular file that parses with the pinned root
    element and names the pinned package;
  * no Updater: no app/ or priv-app/ Updater directory, no app.seamlessupdate.client.xml, and no APK of that package
    among every APK in the inspected partitions (an APK aapt cannot read fails the gate: absence must be proven).
The result says unsigned/test-only for the OS images; it is not a boot, UI or phone result.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import xml.etree.ElementTree as ET

SPARSE_MAGIC = b'\x3a\xff\x26\xed'
LP_GEOMETRY_MAGIC = struct.pack('<I', 0x616C4467)  # liblp LP_METADATA_GEOMETRY_MAGIC, at LP_PARTITION_RESERVED_BYTES
LP_RESERVED = 4096
EXT4_MAGIC, EXT4_OFFSET = b'\x53\xef', 1080
EROFS_MAGIC, EROFS_OFFSET = struct.pack('<I', 0xE0F5E1E2), 1024
EXTRACTORS = {'ext4': 'debugfs', 'erofs': 'fsckErofs'}

class ImageCheckFailed(Exception):
    pass

class ToolMissing(Exception):
    def __init__(self, missing):
        super().__init__('image tools missing: ' + ', '.join(missing))
        self.missing = missing

def sha256_path(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def detect(path):
    with open(path, 'rb') as f:
        head = f.read(LP_RESERVED + 4)
    if head[:4] == SPARSE_MAGIC:
        return 'sparse'
    if head[LP_RESERVED:LP_RESERVED + 4] == LP_GEOMETRY_MAGIC:
        return 'super'
    if head[EXT4_OFFSET:EXT4_OFFSET + 2] == EXT4_MAGIC:
        return 'ext4'
    if head[EROFS_OFFSET:EROFS_OFFSET + 4] == EROFS_MAGIC:
        return 'erofs'
    return 'unknown'

def tool(tools, name, used):
    path = tools.get(name)
    if not path:
        raise ToolMissing([f'imageVerification.tools.{name}'])
    if not Path(path).is_file() or not os.access(path, os.X_OK):
        raise ImageCheckFailed(f'imageVerification.tools.{name} {path} is not an executable file on this runner')
    used.setdefault(name, {'path': path, 'sha256': sha256_path(path)})
    return path

def run(argv, what):
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise ImageCheckFailed(f'{what} failed (exit {result.returncode})')
    return result.stdout

def unpack_super(super_image, workdir, tools, used):
    kind = detect(super_image)
    image = Path(super_image)
    if kind == 'sparse':
        image = Path(workdir)/'super.raw.img'
        run([tool(tools, 'simg2img', used), str(super_image), str(image)], 'simg2img of super.img')
        kind = detect(image)
    if kind != 'super':
        raise ImageCheckFailed(f'super.img is not an LP super image (detected {kind})')
    out = Path(workdir)/'super'
    out.mkdir()
    run([tool(tools, 'lpunpack', used), str(image), str(out)], 'lpunpack of super.img')
    if image != Path(super_image):
        image.unlink()
    return out

def partition_image(unpacked, name):
    """The one non-empty image of a partition: <name>.img or slot <name>_a.img; a non-empty slot b is ambiguous."""
    present = [p for p in (unpacked/f'{name}.img', unpacked/f'{name}_a.img') if p.is_file() and p.stat().st_size]
    other = unpacked/f'{name}_b.img'
    if other.is_file() and other.stat().st_size:
        raise ImageCheckFailed(f'super.img holds a non-empty {other.name}; the inspected slot is ambiguous')
    if len(present) != 1:
        raise ImageCheckFailed(f'super.img holds {len(present)} non-empty images for partition {name}')
    return present[0]

def extract(image, destination, filesystem, tools, used):
    kind = detect(image)
    if kind not in EXTRACTORS:
        raise ImageCheckFailed(f'{image.name} has an unsupported format ({kind})')
    if kind != filesystem:
        raise ImageCheckFailed(f'{image.name} is {kind}, not the recorded {filesystem}')
    destination.mkdir()
    if kind == 'ext4':
        run([tool(tools, 'debugfs', used), '-R', f'rdump / {destination}', str(image)], f'debugfs rdump of {image.name}')
    else:
        run([tool(tools, 'fsckErofs', used), f'--extract={destination}', str(image)], f'fsck.erofs extract of {image.name}')
    if not any(destination.iterdir()):
        raise ImageCheckFailed(f'nothing was extracted from {image.name}')
    return destination

def badging(aapt, apk):
    out = subprocess.run([aapt, 'dump', 'badging', str(apk)], capture_output=True, text=True)
    match = re.search(r"^package: name='([^']+)' versionCode='(\d+)'", out.stdout, re.M)
    return (match[1], int(match[2])) if out.returncode == 0 and match else None

def signers(apksigner, apk):
    out = subprocess.run([apksigner, 'verify', '--print-certs', str(apk)], capture_output=True, text=True)
    return re.findall(r'certificate SHA-256 digest: ([0-9a-f]{64})', out.stdout) if out.returncode == 0 else None

def last_property(path, key):
    value = None
    for line in Path(path).read_text(errors='replace').splitlines():
        if '=' in line and not line.lstrip().startswith('#') and line.split('=', 1)[0].strip() == key:
            value = line.split('=', 1)[1].strip()
    return value

def expected_framework(policy, locked):
    agent = [f'{policy["agent"]["packageName"]}:{c}' for c in policy['agent']['certificates']]
    return {'Agents': agent, 'Targets': [f'{t["packageName"]}:{c}' for t in policy['targets'] for c in t['certificates']],
        'PrototypeAgents': agent if policy.get('prototypeControl') else [], 'LockedSessions': locked}

def regular_file(roots, location):
    """(partition, path, failure): the file at partition:path if it is a non-empty regular file, not a link."""
    partition, inside = location.split(':', 1)
    path = roots[partition]/inside
    if os.path.islink(path):
        return partition, path, f'{location} is a symlink, not the module file'
    if not path.is_file():
        return partition, path, f'{location} is absent from the image'
    if path.stat().st_size == 0:
        return partition, path, f'{location} is empty'
    return partition, path, None

def closure_report(closure, roots, aapt, failures):
    """The contract's Seedvault closure from extracted partition content; failures are appended to the list."""
    report = {'source': closure['source'], 'apps': [], 'xml': []}
    for app in closure['apps']:
        partition, path, problem = regular_file(roots, app['path'])
        row = {'module': app['module'], 'path': app['path'], 'package': app['package']}
        if problem:
            failures.append(f'Seedvault closure: {problem}')
            report['apps'].append({**row, 'present': False})
            continue
        identity = badging(aapt, path)
        row.update(present=True, sha256=sha256_path(path), size=path.stat().st_size, identity=list(identity) if identity else None)
        if identity is None:
            failures.append(f'Seedvault closure: {app["path"]} could not be read as an APK')
        elif identity[0] != app['package']:
            failures.append(f'Seedvault closure: {app["path"]} is package {identity[0]}, not the pinned {app["package"]}')
        report['apps'].append(row)
    for xml in closure['xml']:
        partition, path, problem = regular_file(roots, xml['path'])
        row = {'module': xml['module'], 'path': xml['path'], 'root': xml['root'], 'package': xml['package']}
        if problem:
            failures.append(f'Seedvault closure: {problem}')
            report['xml'].append({**row, 'present': False})
            continue
        row.update(present=True, sha256=sha256_path(path))
        try:
            document = ET.parse(path).getroot()
        except ET.ParseError:
            failures.append(f'Seedvault closure: {xml["path"]} is not well-formed XML')
            report['xml'].append(row)
            continue
        if document.tag != xml['root']:
            failures.append(f'Seedvault closure: {xml["path"]} has root <{document.tag}>, not <{xml["root"]}>')
        elif not any(e.get('package') == xml['package'] for e in document.iter()):
            failures.append(f'Seedvault closure: {xml["path"]} does not name package {xml["package"]}')
        report['xml'].append(row)
    return report

def verify(product_out, spec, contract, policy, signer, filesystem, locked, default_value, workdir):
    """Returns the content report; raises ImageCheckFailed (or ToolMissing) on the first structural problem and
    ImageCheckFailed listing every content failure otherwise. default_value is release-pipeline.py's parser."""
    tools, used = spec['tools'], {}
    for name, pin in spec.get('toolSha256', {}).items():  # measured on the builder; a different file is not the verified tool
        if tools.get(name) and Path(tools[name]).is_file() and sha256_path(tools[name]) != pin:
            raise ImageCheckFailed(f'imageVerification.tools.{name} {tools[name]} differs from its pinned sha256')
    if filesystem not in EXTRACTORS:
        raise ImageCheckFailed(f'recorded system file system {filesystem!r} has no supported extractor')
    aapt, apksigner = tool(tools, 'aapt', used), tool(tools, 'apksigner', used)
    unpacked = unpack_super(Path(product_out)/'super.img', workdir, tools, used)
    roots, partitions = {}, {}
    for name in spec['partitions']:
        image = partition_image(unpacked, name)
        partitions[name] = {'image': image.name, 'size': image.stat().st_size, 'sha256': sha256_path(image)}
        roots[name] = extract(image, Path(workdir)/f'fs-{name}', filesystem, tools, used)
    failures, apps = [], []
    contract_apps = {a['package']: a for a in contract['apps']}
    pinned = {policy['agent']['packageName']: policy['agent']['apkSha256'], policy['voice']['packageName']: policy['voice']['apkSha256'],
        **{t['packageName']: t['apkSha256'] for t in policy['targets']}}
    if pinned != {p: a['sha256'] for p, a in contract_apps.items()}:
        failures.append('the bound policy app digests differ from the contract apps')
    for package, location in spec['apps'].items():
        partition, inside = location.split(':', 1)
        apk, want = roots[partition]/inside, contract_apps[package]
        row = {'package': package, 'partition': partition, 'path': inside}
        if not apk.is_file() or apk.is_symlink():
            failures.append(f'{package} APK {partition}:{inside} is absent from the image')
            apps.append({**row, 'present': False})
            continue
        row.update(sha256=sha256_path(apk), identity=badging(aapt, apk), signers=signers(apksigner, apk))
        if row['sha256'] != want['sha256']:
            failures.append(f'{package} image bytes differ from the contract {want["sha256"][:12]}')
        if row['identity'] != (package, want['version']):
            failures.append(f'{package} image APK identifies as {row["identity"]}, expected {package} {want["version"]}')
        if row['signers'] != [signer]:
            failures.append(f'{package} image APK is not signed by exactly the approved app signer')
        apps.append({**row, 'present': True, 'identity': list(row['identity']) if row['identity'] else None})
    framework_partition, framework_inside = spec['framework'].split(':', 1)
    framework = roots[framework_partition]/framework_inside
    compiled, expected = {}, expected_framework(policy, locked)
    if not framework.is_file():
        failures.append(f'{spec["framework"]} is absent from the image')
    else:
        dump = run([aapt, 'dump', '--values', 'resources', str(framework)], 'aapt dump of framework-res.apk')
        for name, want in expected.items():
            value, problem = default_value(dump, 'bool' if name == 'LockedSessions' else 'array', f'config_pixelComputerControl{name}')
            compiled[name] = value
            if problem:
                failures.append(f'framework-res {problem}')
            elif value != want:
                failures.append(f'framework-res config_pixelComputerControl{name} is {value!r}, expected {want!r} from the bound policy')
    closure = closure_report(spec['closure'], roots, aapt, failures)
    prop_partition, prop_inside = spec['buildProp'].split(':', 1)
    prop = roots[prop_partition]/prop_inside
    tray = last_property(prop, spec['trayProperty']) if prop.is_file() else None
    if tray != spec['trayValue']:
        failures.append(f'{spec["trayProperty"]} in {spec["buildProp"]} is {tray!r}, expected {spec["trayValue"]!r}')
    updater, inspected = spec['updater'], 0
    for name, root in roots.items():
        for directory, dirs, files in os.walk(root):
            rel = os.path.relpath(directory, root)
            for d in dirs:
                if d in updater['dirNames'] and os.path.basename(rel) in ('app', 'priv-app'):
                    failures.append(f'Updater directory {name}:{os.path.normpath(os.path.join(rel, d))} is in the image')
            for f in files:
                path = Path(directory)/f
                if f == updater['xml']:
                    failures.append(f'{updater["xml"]} is in the image at {name}:{os.path.normpath(os.path.join(rel, f))}')
                if f.endswith('.apk') and not path.is_symlink():
                    inspected += 1
                    identity = badging(aapt, path)
                    if identity is None:
                        failures.append(f'{name}:{os.path.normpath(os.path.join(rel, f))} could not be read; Updater absence is unproven')
                    elif identity[0] == updater['package']:
                        failures.append(f'an APK of {updater["package"]} is in the image at {name}:{os.path.normpath(os.path.join(rel, f))}')
    if failures:
        raise ImageCheckFailed('; '.join(failures))
    return {'verified': True, 'source': 'partitions unpacked from the built super.img', 'filesystem': filesystem,
        'partitions': partitions, 'apps': apps, 'framework': compiled, 'tray': {spec['trayProperty']: tray}, 'closure': closure,
        'updaterAbsent': {'package': updater['package'], 'apksInspected': inspected}, 'tools': used,
        'osImages': 'unsigned, test-only (AOSP test keys)', 'notProven': 'boot, UI, viewer, input, Stop or phone behaviour'}
