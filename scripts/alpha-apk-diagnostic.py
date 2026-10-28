#!/usr/bin/env python3
"""INFRA-ALPHA-116-DIAG: read-only aapt evidence for three platform APKs in the retained Cuttlefish alpha super.img.

Run 40765631053 compiled the alpha, then failed its image-content gate: alpha_image.badging could not read
system/priv-app/Shell, system/priv-app/IntentResolver and system_ext/priv-app/Settings, and the gate discards aapt's
stdout and stderr. This collects what the gate threw away, for those three fixed paths only, without rebuilding.

  run   the os-release.yml emulator-alpha-apk-diagnostic job, on the builder:
        --candidate-sha/--apps must be the bound base (as for the alpha build) and --build-timestamp the
        BUILD_DATETIME of run 40765631053 taken from that run's log. Under the shared builder lock it checks the
        pinned preparation/successor tree, then that the retained build.props name build rose-cf-alpha-40765631053
        and that timestamp, re-extracts system and system_ext from the existing super.img with the configured,
        pin-checked tools into a task-owned temporary directory (always removed), and records per APK: link/regular
        status, size and sha256, ZIP integrity and AndroidManifest.xml member, the aapt path and sha256, aapt dump
        badging return code with the package line and bounded sanitized stderr, and the compiled manifest's package
        and versionCode read through aapt dump xmltree. --evidence-dir receives diagnostic.json and SHA256SUMS.

Never runs lunch, make, signing, an installer or a publisher, writes no image receipt, and does not change the
image-content gate (scripts/alpha_image.py): an unreadable, malformed or version-less APK is reported as exactly that,
never as an accepted package. Hashes measured here are NEW observations of the retained output, not values from the
original run. Collection can pass while image verification stays failed (imageVerified is always false).
Exit status: 0 collected, 1 check failed, 2 invalid input, 4 setup missing.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha_image  # noqa: E402
from release_receipts import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_RUN = '40765631053'
# The exact entries alpha_image reported as unreadable in run 40765631053; nothing else is read.
TARGETS = ('system:system/priv-app/Shell/Shell.apk', 'system:system/priv-app/IntentResolver/IntentResolver.apk',
    'system_ext:priv-app/Settings/Settings.apk')
PARTITIONS = ('system', 'system_ext')
STDERR_CAP, LINE_CAP = 4096, 512
MANIFEST = 'AndroidManifest.xml'

def emulator_alpha():
    spec = importlib.util.spec_from_file_location('emulator_alpha', ROOT/'scripts/emulator-alpha.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sanitize(text, cap, workdir=None):
    """Printable ASCII only, temporary paths replaced, at most cap characters (marked when truncated)."""
    text = text or ''
    if workdir:
        text = text.replace(str(workdir), '<extract>')
    text = ''.join(c if c == '\n' or 32 <= ord(c) < 127 else '?' for c in text)
    return text if len(text) <= cap else text[:cap] + f'...[truncated {len(text) - cap} chars]'

def badging_outcome(returncode, stdout):
    """alpha_image.badging needs exit 0 and package:name plus a numeric versionCode; say which part is missing."""
    line = next((l for l in (stdout or '').splitlines() if l.startswith('package:')), None)
    name = re.search(r"\bname='([^']*)'", line or '')
    version = re.search(r"\bversionCode='([^']*)'", line or '')
    if returncode != 0:
        outcome = 'aapt-exit-nonzero'
    elif line is None or not name or not name[1]:
        outcome = 'no-package-name'
    elif not version or version[1] == '':
        outcome = 'version-absent'
    elif not re.fullmatch(r'\d+', version[1]):
        outcome = 'version-non-numeric'
    else:
        outcome = 'package-and-version'
    return {'outcome': outcome, 'packageName': name[1] if name else None, 'versionCode': version[1] if version else None}, line

def manifest_root(xmltree):
    """package and versionCode attributes of the compiled manifest element in aapt dump xmltree output."""
    found, depth = {}, None
    for line in (xmltree or '').splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped.startswith('E: '):
            if depth is not None:
                break  # first child element: the manifest's own attributes are done
            if stripped.split()[1] == 'manifest':
                depth = indent
            continue
        if depth is None or not stripped.startswith('A: '):
            continue
        attribute = re.match(r'A: (?:android:)?(package|versionCode)(?:\(0x[0-9a-f]+\))?=(.*)$', stripped)
        if attribute:
            name, value = attribute[1], attribute[2]
            if name == 'package':
                text = re.match(r'"([^"]*)"', value)
                found['package'] = text[1] if text else None
            else:
                number = re.match(r'\(type 0x10\)0x([0-9a-f]+)$', value)
                found['versionCode'] = int(number[1], 16) if number else sanitize(value, 64)
    if depth is None:
        return {'outcome': 'no-manifest-element'}
    version = found.get('versionCode')
    return {'outcome': 'version-absent' if 'versionCode' not in found else
        'package-and-version' if isinstance(version, int) and found.get('package') else 'unexpected-values',
        'package': found.get('package'), 'versionCode': version}

def zip_facts(path):
    if not zipfile.is_zipfile(path):
        return {'zip': 'not-a-zip'}
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            names = set(archive.namelist())
            manifest = archive.getinfo(MANIFEST) if MANIFEST in names else None
    except (zipfile.BadZipFile, OSError, RuntimeError) as error:
        return {'zip': 'unreadable', 'error': error.__class__.__name__}
    facts = {'zip': 'crc-ok' if bad is None else 'crc-failed', 'entries': len(names), 'manifestMember': manifest is not None}
    if bad is not None:
        facts['firstBadMember'] = sanitize(bad, 200)
    if manifest is not None:
        facts['manifestMemberSize'] = manifest.file_size
    return facts

def inspect(location, root, aapt, workdir, runner=subprocess.run):
    """Evidence for one fixed APK; never raises for APK content, and never calls a reader on a link or non-file."""
    partition, inside = location.split(':', 1)
    path = Path(root)/inside
    row = {'location': location}
    if os.path.islink(path):
        return {**row, 'status': 'symlink'}
    if not path.exists():
        return {**row, 'status': 'absent'}
    if not path.is_file():
        return {**row, 'status': 'not-a-regular-file'}
    row.update(status='regular', size=path.stat().st_size, sha256=sha256_file(path), **zip_facts(path))
    result = runner([aapt, 'dump', 'badging', str(path)], capture_output=True, text=True)
    outcome, line = badging_outcome(result.returncode, result.stdout)
    row['badging'] = {'returncode': result.returncode, **outcome,
        'packageLine': sanitize(line, LINE_CAP, workdir) if line else None,
        'stderr': sanitize(result.stderr, STDERR_CAP, workdir), 'stdoutBytes': len(result.stdout or '')}
    if row.get('manifestMember'):
        tree = runner([aapt, 'dump', 'xmltree', str(path), MANIFEST], capture_output=True, text=True)
        row['compiledManifest'] = {'reader': 'aapt dump xmltree (same pinned aapt; no resource-table resolution)',
            'returncode': tree.returncode, **(manifest_root(tree.stdout) if tree.returncode == 0 else {'outcome': 'reader-failed'}),
            'stderr': sanitize(tree.stderr, STDERR_CAP, workdir)}
    else:
        row['compiledManifest'] = {'outcome': 'no-manifest-member'}
    return row

def collect(product_out, spec, filesystem, workdir, used):
    """Re-extract the two partitions from super.img and inspect the fixed APKs; NEW hashes of what was measured."""
    super_image = Path(product_out)/'super.img'
    if os.path.islink(super_image) or not super_image.is_file():
        raise alpha_image.ImageCheckFailed(f'{super_image.name} is not a regular file in PRODUCT_OUT')
    tools = spec['tools']
    for name, pin in spec.get('toolSha256', {}).items():
        if tools.get(name) and Path(tools[name]).is_file() and sha256_file(tools[name]) != pin:
            raise alpha_image.ImageCheckFailed(f'imageVerification.tools.{name} {tools[name]} differs from its pinned sha256')
    aapt = alpha_image.tool(tools, 'aapt', used)
    observed = {'superImage': {'size': super_image.stat().st_size, 'sha256': sha256_file(super_image)}, 'partitions': {}}
    unpacked = alpha_image.unpack_super(super_image, workdir, tools, used)
    roots = {}
    for name in PARTITIONS:
        image = alpha_image.partition_image(unpacked, name)
        observed['partitions'][name] = {'image': image.name, 'size': image.stat().st_size, 'sha256': sha256_file(image)}
        roots[name] = alpha_image.extract(image, Path(workdir)/f'fs-{name}', filesystem, tools, used)
    apks = [inspect(location, roots[location.split(':', 1)[0]], aapt, workdir) for location in TARGETS]
    return observed, apks

def recipe():
    result = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None

def write_evidence(evidence_dir, document):
    evidence_dir.mkdir(parents=True)
    (evidence_dir/'diagnostic.json').write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
    (evidence_dir/'SHA256SUMS').write_text(f'{sha256_file(evidence_dir/"diagnostic.json")}  diagnostic.json\n')

def run(config, ea, sha, apps, run_id, timestamp, evidence_dir, collector=collect, now=time.time):
    if not re.fullmatch(r'[0-9a-f]{40}', sha or '') or not re.fullmatch(r'\d{10}', timestamp or ''):
        raise ea.InvalidInput('a full candidate SHA and the ten-digit BUILD_DATETIME of run ' + ORIGINAL_RUN + ' are required')
    if not re.fullmatch(r'[1-9][0-9]*', str(run_id or '')):
        raise ea.InvalidInput('--run-id must be the numeric Actions run id')
    evidence_dir = Path(evidence_dir)
    if evidence_dir.exists():
        raise ea.CheckFailed(f'{evidence_dir} already exists; use a new evidence directory')
    ea.guard(config)
    ea.check_dispatch(config, sha, apps)
    original = ea.build_id(config, ORIGINAL_RUN)
    lunch = config['hostEvidence']['lunch']
    document = {'receiptType': 'emulator-alpha-apk-diagnostic', 'diagnosticRun': str(run_id), 'currentRecipe': recipe(),
        'originalRun': {'runId': ORIGINAL_RUN, 'emulatorBuildId': original, 'buildDatetime': timestamp,
            'buildDatetimeSource': 'dispatch input taken from the original run log; checked against the retained build.props'},
        'candidate': sha, 'sourceBinding': {k: config['sourceBinding'][k] for k in ('candidate', 'reconcileReceipt', 'reconcileReceiptSha256', 'apps')},
        'prepared': {'preparationId': config['prepared']['preparationId'], 'receiptSha256': config['prepared']['receiptSha256'],
            'successor': {k: (config['prepared'].get('successor') or {}).get(k) for k in ('successorId', 'receiptSha256')}},
        'targets': list(TARGETS), 'executedMake': False, 'imageVerified': False, 'signed': False, 'published': False,
        'secondIndependentReader': 'unavailable: no aapt2 or other manifest tool is configured in imageVerification.tools',
        'newObservations': None, 'apks': None, 'tools': {}, 'verdict': 'FAIL', 'error': None}
    lock = ea.locked(config)
    try:
        _, tree, failures = ea.pinned_failures(config)
        if failures:
            raise ea.CheckFailed('; '.join(failures))
        product_out = tree/lunch['PRODUCT_OUT']
        props = ea.read_props(product_out)
        failures = ea.identity_failures(props, config, original, timestamp, lunch['PRODUCT_DEVICE'])
        if failures:
            raise ea.CheckFailed('the retained output is not run ' + ORIGINAL_RUN + "'s build: " + '; '.join(failures))
        system = props['system/build.prop']
        document['originalRun']['buildProps'] = {k: system.get(k) for k in
            ('ro.build.fingerprint', 'ro.build.type', 'ro.build.version.incremental', 'ro.build.date.utc')}
        with tempfile.TemporaryDirectory(prefix='apk-diagnostic-', dir=ea.preparation_dir(config, config['prepared']['preparationId'])) as workdir:
            observed, apks = collector(product_out, config['imageVerification'], lunch['BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE'],
                workdir, document['tools'])
        document.update(newObservations={**observed, 'note': 'measured by this diagnostic run, not taken from run ' + ORIGINAL_RUN},
            apks=apks, verdict='COLLECTED')
    except (ea.CheckFailed, alpha_image.ImageCheckFailed) as error:
        document['error'] = sanitize(str(error), STDERR_CAP)
        raise ea.CheckFailed(document['error'])
    except alpha_image.ToolMissing as error:
        document['error'] = 'tools missing: ' + ', '.join(error.missing)
        raise ea.SetupMissing(error.missing)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        document['error'] = error.__class__.__name__
        raise
    finally:
        lock.close()
        document['at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now()))
        write_evidence(evidence_dir, document)
    return document

def main(argv=None, ea=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['run'])
    p.add_argument('--config')
    p.add_argument('--candidate-sha')
    p.add_argument('--apps', default='[]')
    p.add_argument('--run-id')
    p.add_argument('--build-timestamp')
    p.add_argument('--evidence-dir', required=True)
    a = p.parse_args(argv)
    ea = ea or emulator_alpha()
    try:
        config = ea.load(a.config or ea.CONFIG)
        result = run(config, ea, a.candidate_sha, json.loads(a.apps), a.run_id, a.build_timestamp, a.evidence_dir)
    except ea.SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except ea.InvalidInput as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    except ea.CheckFailed as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)}), file=sys.stderr)
        return 1
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': error.__class__.__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
