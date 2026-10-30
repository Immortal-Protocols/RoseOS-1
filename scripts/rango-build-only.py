#!/usr/bin/env python3
"""OS-RANGO-BUILD-001: unsigned build-only route for rango (Pixel 10 Pro Fold) on the retained GrapheneOS tree.

It reuses the existing device support; it is not a port. Inputs are bound by os/build-only/rango.json, which is
checked against the FAM-03a device contract (os/devices/pixel10.json). Two stages, each behind the same fail-closed
preflight and the shared builder lock:

  generate   cd SRC; ADEVTOOL_IMG_DOWNLOAD_DIR=DL vendor/adevtool/bin/run generate-all -d rango
             Never the all-device default, which would regenerate (rm -rf) Frankel's vendor tree.
  compile    cd SRC; source build/envsetup.sh; lunch rango-cur-user; m <goals> with OUT_DIR=out-rango-<build>

  plan       prints both stages' exact commands and the preflight report; runs nothing
  preflight  the report alone (exit 0 ready, 1 not ready)
  resume-check / resume   OS-RANGO-RESUME-005: compile only, after a proven natural cutoff (see resume below)

Preflight refuses, before any write or command, when:
  * the target, pin or factory hash differs from the contract;
  * a path is unsafe;
  * the adevtool host tools are not already built at the pinned revision (generate-all would otherwise start a Soong
    host build);
  * the stage's generated inputs are present or missing as that stage requires;
  * the shared lock is held;
  * compile only: the Rose integration is not proven for this tree (see compile_integration);
  * the stage's measured allowance is unknown (null), or the filesystem's available bytes minus it would fall below
    the 300 GiB reserve. A config with capacity.allowanceRequired false (the isolated host, OS-RANGO-BUILD-003) never
    invents an allowance: a null one is recorded as unmeasured and only available >= reserve is required.
Generation needs no Rose integration (it reads only the factory image and adevtool); compile needs
--inputs-receipt, the local-inputs.json of scripts/rango-local-inputs.py, so the reconcile receipt can be bound to
this tree, the exact signed APKs and policy, and this checkout's patcher.
--receipt-dir receives write-once preflight.json, then started.json just before the command runs (the start event),
then completed.json with its exit status. Unsigned test-keys output only: this module reaches no signer, secret or
publisher, and the unsigned build is not release-ready.

resume continues an interrupted compile in its own OUT_DIR. It is not an allow-existing switch: the prior attempt
(--prior-receipt-dir, then any PRIOR.resume-N after it) must have started and never completed, an operator cutoff
proof must bind that exact start to a supervisor deadline or VM stop, and supplemental provenance supplies what an
old-format started.json lacks. Under the same lock it re-checks everything, then writes PRIOR.resume-N (write-once)
and runs the same command. A completed prior attempt is a no-op; an ordinary failure is never resumed.
Exit status: 0 ready/success, 1 not ready or command failed, 2 invalid input.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from xml.etree import ElementTree
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/build-only/rango.json'
DEVICE = 'rango'
STAGES = ('generate', 'compile')
FORBIDDEN_TREE_PATHS = ('os/fold-port',)
PATCHER = ('apply-os-changes.py', 'background_apks.py', 'integration_copy.py')
# Where apply-os-changes.py installs each signed input in the source tree.
INSTALLED = {'agent': 'packages/apps/PixelBackgroundControl/Agent.apk',
    'preinstall': 'packages/apps/RosePreinstall/RosePreinstall_{module}/app.apk'}
POLICY_CONFIG = 'frameworks/base/core/res/res/values/config.xml'

class InvalidInput(Exception):
    pass

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()

def load(path=CONFIG, root=ROOT):
    """The config, checked against the FAM-03a device contract; InvalidInput on any mismatch."""
    config = json.loads(Path(path).read_text())
    problems = []
    if config.get('device') != DEVICE:
        problems.append(f'config device is {config.get("device")!r}; this route builds {DEVICE} only')
    contract = json.loads((root/config['deviceContract']).read_text())
    device = contract['devices'].get(DEVICE, {})
    if config.get('lunch') != device.get('lunch', {}).get('expected'):
        problems.append(f'lunch {config.get("lunch")!r} is not the contract\'s {device.get("lunch", {}).get("expected")!r}')
    image = device.get('vendorInputs', {}).get('factoryImage', {})
    if (config['factory']['file'], config['factory']['sha256']) != (image.get('file'), image.get('sha256')):
        problems.append('factory image name or sha256 differs from the device contract')
    if not config['factory']['url'].endswith('/' + config['factory']['file']) \
            or not config['factory']['url'].startswith('https://dl.google.com/dl/android/aosp/'):
        problems.append('factory URL is not the adevtool index URL for that file')
    if config['adevtool']['revision'] != contract['source']['projects']['vendor/adevtool']:
        problems.append('adevtool revision differs from the device contract pin')
    if any(p in json.dumps(config) for p in FORBIDDEN_TREE_PATHS):
        problems.append('config references os/fold-port, which never applies to rango')
    if config['capacity'].get('allowanceRequired', True) is not True:
        # rango.json binds the Frankel builder's own tree, lock and receipts; an isolated host shares none of them.
        frankel = json.loads((root/CONFIG.relative_to(ROOT)).read_text())['host']
        shared = [k for k in ('sourceRoot', 'lockPath', 'reconcileReceiptDir') if config['host'][k] == frankel[k]]
        if config['capacity']['allowanceRequired'] is not False or shared:
            problems.append(f'capacity.allowanceRequired false is only for an isolated host; shared with Frankel: {shared}')
    if problems:
        raise InvalidInput('; '.join(problems))
    return config

def out_dir_name(config, build):
    if not re.fullmatch(r'[1-9][0-9]{9}', str(build or '')):
        raise InvalidInput('--build-number must be a ten-digit build number')
    return config['host']['outDirPrefix'] + str(build)

def commands(config, build, download_dir):
    """The exact argv, cwd and extra environment of each stage."""
    src, host = config['host']['sourceRoot'], config['host']
    make = 'm -j{} {}'.format(host['makeJobs'], ' '.join(host['makeGoals']))
    return {
        'generate': {'cwd': src, 'env': {'ADEVTOOL_IMG_DOWNLOAD_DIR': str(download_dir)},
            'argv': ['vendor/adevtool/bin/run', 'generate-all', '-d', DEVICE]},
        'compile': {'cwd': src, 'env': {'OUT_DIR': out_dir_name(config, build), 'BUILD_NUMBER': str(build)},
            'argv': ['bash', '-c', f'source build/envsetup.sh && lunch {config["lunch"]} && {make}']},
    }

def lock_free(path):
    """True when nobody holds the shared builder lock (probed without keeping it)."""
    try:
        with open(path, 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False

def latest_reconcile(directory):
    receipts = sorted(Path(directory).glob('[0-9][0-9][0-9][0-9]-*.json')) if Path(directory).is_dir() else []
    return receipts[-1] if receipts else None

def tree_bytes(src, relative):
    path = Path(src)/relative
    return sha256_file(path) if path.is_file() and not path.is_symlink() else None

def compile_integration(config, src, inputs_receipt):
    """(problems, facts): the latest reconcile receipt in this config's own receipt dir must describe this tree as it
    is now, have installed the exact signed APKs and policy that inputs_receipt (rango-local-inputs.py) verified, and
    come from this checkout's patcher. A receipt copied from another builder (Frankel) names other APK bytes or other
    tree bytes and is refused."""
    problems, directory = [], Path(config['host']['reconcileReceiptDir'])
    reconcile = latest_reconcile(directory)
    if reconcile is None:
        return ['no Rose integration (reconcile) receipt: the tree has no recorded Rose app/controller inputs'], {}
    facts = {'receipt': reconcile.name, 'sha256': sha256_file(reconcile)}
    if reconcile.is_symlink() or directory.is_symlink() or reconcile.resolve().parent != directory.resolve():
        return [f'{reconcile.name} is a link out of {directory}, not a receipt of this builder'], facts
    record = json.loads(reconcile.read_text())
    if record.get('receiptType') != 'reconcile':
        problems.append(f'{reconcile.name} is a {record.get("receiptType")!r} receipt; run host-reconcile.py apply last')
    patcher = record.get('patcher', {})
    ours = {name: sha256_file(ROOT/'scripts'/name) for name in PATCHER}
    if patcher.get('patcher') != 'trusted' or patcher.get('sha256') != ours:
        problems.append('the reconcile patcher is not this checkout\'s trusted apply-os-changes.py (different recipe)')
    entries = record.get('entries', {})
    drifted = sorted(path for path, entry in entries.items() if tree_bytes(src, path) != entry.get('after'))
    if drifted:
        problems.append(f'the source tree no longer matches {reconcile.name}: {drifted[:5]}')
    if not inputs_receipt or not Path(inputs_receipt).is_file():
        problems.append('compile needs --inputs-receipt: the local-inputs.json of scripts/rango-local-inputs.py')
        return problems, facts
    inputs = json.loads(Path(inputs_receipt).read_text())
    facts['inputs'] = {'receipt': str(inputs_receipt), 'sha256': sha256_file(inputs_receipt),
        'release': inputs.get('release'), 'product': inputs.get('product')}
    if inputs.get('receiptType') != 'rango-local-inputs':
        problems.append('--inputs-receipt is not a rango-local-inputs receipt')
        return problems, facts
    policy = inputs['policy']
    if not Path(policy['path']).is_file() or sha256_file(policy['path']) != policy['sha256']:
        problems.append('the background policy differs from the one rango-local-inputs.py wrote')
    expected = {}
    for app in inputs['apps']:
        if not Path(app['signedPath']).is_file() or sha256_file(app['signedPath']) != app['sha256']:
            problems.append(f'{app["package"]}: signed APK bytes differ from the verified extraction')
        tree = INSTALLED['agent'] if app['role'] == 'agent' else \
            INSTALLED['preinstall'].format(module=app['package'].replace('.', '_'))
        expected[tree] = app['sha256']
    for tree, digest in sorted(expected.items()):
        if entries.get(tree, {}).get('after') != digest:
            problems.append(f'{reconcile.name} did not install the verified bytes at {tree}')
    xml = (Path(src)/POLICY_CONFIG).read_text(errors='replace') if (Path(src)/POLICY_CONFIG).is_file() else ''
    pinned = json.loads(Path(policy['path']).read_text()) if Path(policy['path']).is_file() else {}
    for row in [pinned.get('agent', {}), *pinned.get('targets', [])]:
        for cert in row.get('certificates', []):
            if f'<item>{row.get("packageName")}:{cert}</item>' not in xml:
                problems.append(f'{POLICY_CONFIG} does not carry the local policy pin for {row["packageName"]}')
    return problems, facts

def preflight(config, stage, build, download_dir, statvfs=os.statvfs, lock_probe=lock_free, inputs_receipt=None,
        existing_out=False):
    """{'ready': bool, 'problems': [...], 'facts': {...}} for one stage. Reads only; writes and runs nothing.
    existing_out is resume's alone: the existing OUT_DIR is then checked by resume_admission instead of refused."""
    if stage not in STAGES:
        raise InvalidInput(f'stage must be one of {STAGES}')
    out_name = out_dir_name(config, build)
    host, problems, facts = config['host'], [], {'stage': stage, 'device': DEVICE, 'outDir': out_name}
    src = Path(host['sourceRoot'])
    download = Path(download_dir) if download_dir else None
    if not src.is_absolute() or not src.is_dir():
        problems.append(f'source root {src} is not an existing absolute directory')
        return {'ready': False, 'problems': problems, 'facts': facts}
    if download is None or not download.is_absolute() or '..' in download.parts:
        raise InvalidInput('--download-dir must be an absolute path without ..')
    if download == src or src in download.parents:
        problems.append('the download dir must be outside the source tree (keeps the archive out of Soong and alpha copies)')
    if download.exists() and (not download.is_dir() or (stage == 'generate' and any(download.iterdir()))):
        problems.append(f'download dir {download} must be absent or an empty directory before generation')
    probe = download if download.exists() else download.parent
    if not probe.is_dir():
        problems.append(f'download dir parent {probe} does not exist')
    elif os.stat(probe).st_dev != os.stat(src).st_dev:
        problems.append('the download dir is not on the source tree\'s filesystem; capacity would be checked on the wrong one')
    for needed in ('vendor/adevtool/bin/run', 'prebuilts/build-tools/path/linux-x86', 'prebuilts/jdk/jdk21/linux-x86/bin'):
        if not (src/needed).exists():
            problems.append(f'{needed} is missing from the source tree')
    deps = src/config['adevtool']['depsDir']
    revision = (deps/'adevtool_revision').read_text().strip() if (deps/'adevtool_revision').is_file() else None
    if revision != config['adevtool']['revision']:
        problems.append(f'{config["adevtool"]["depsDir"]}/adevtool_revision is {revision!r}, not the pinned revision; '
            'generate-all would start a Soong host-tool build')
    missing_tools = [t for t in config['adevtool']['depsTools'] if not (deps/'host/linux-x86/bin'/t).is_file()]
    if missing_tools:
        problems.append(f'adevtool host tools missing: {missing_tools}')
    vendor = src/'vendor/google_devices'/DEVICE
    if stage == 'generate' and os.path.lexists(vendor):
        problems.append(f'{vendor} already exists; generate-all would rm -rf it')
    if stage == 'compile':
        product = vendor/f'{DEVICE}.mk'
        check = vendor/'adevtool-version-check.mk'
        if not product.is_file():
            problems.append(f'{product} is missing: run the generate stage first')
        if not check.is_file() or config['adevtool']['revision'] not in check.read_text():
            problems.append('the generated vendor module is not from the pinned adevtool revision')
    facts['frankelVendorPresent'] = (src/'vendor/google_devices/frankel').is_dir()
    if os.path.lexists(src/out_name) and not existing_out:
        problems.append(f'{out_name} already exists; each build needs a new OUT_DIR')
    if stage == 'compile':
        integration, facts['roseIntegration'] = compile_integration(config, src, inputs_receipt)
        problems += integration
    if not lock_probe(host['lockPath']):
        problems.append('the shared builder lock is held by another job')
    allowance = config['capacity'][f'{stage}AllowanceBytes']
    fs = statvfs(src)
    available = fs.f_bavail * fs.f_frsize
    required = config['capacity'].get('allowanceRequired', True)
    facts['capacity'] = {'availableBytes': available, 'reserveBytes': config['capacity']['reserveBytes'],
        'allowanceBytes': allowance, 'allowanceRequired': required}
    if allowance is None and required:
        problems.append(f'capacity.{stage}AllowanceBytes is unmeasured; a measured allowance is required, never an estimate')
    elif allowance is None:
        facts['capacity']['allowance'] = 'unmeasured; reserve-only observation on the isolated host'
        if available < config['capacity']['reserveBytes']:
            problems.append(f'available {available} is below the reserve {config["capacity"]["reserveBytes"]}')
    elif available - allowance < config['capacity']['reserveBytes']:
        problems.append(f'available {available} minus allowance {allowance} is below the reserve {config["capacity"]["reserveBytes"]}')
    return {'ready': not problems, 'problems': problems, 'facts': facts}

def run(config, stage, build, download_dir, receipt_dir, runner=subprocess.run, now=time.time, **probes):
    """Preflight, then the stage's command under the shared lock, with write-once receipts."""
    receipt_dir = Path(receipt_dir)
    if receipt_dir.exists():
        raise InvalidInput(f'{receipt_dir} already exists; use a new receipt directory')
    report = preflight(config, stage, build, download_dir, **probes)
    receipt_dir.mkdir(parents=True)
    release_receipts.write_once(receipt_dir/'preflight.json', {**report, 'at': stamp(now)})
    if not report['ready']:
        return 1
    command = commands(config, build, download_dir)[stage]
    with open(config['host']['lockPath'], 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            release_receipts.write_once(receipt_dir/'completed.json', {'started': False, 'error': 'lock taken after preflight'})
            return 1
        release_receipts.write_once(receipt_dir/'started.json', {'stage': stage, 'device': DEVICE, 'command': command,
            'signed': False, 'at': stamp(now)})
        result = runner(command['argv'], cwd=command['cwd'], env={**os.environ, **command['env']})
        release_receipts.write_once(receipt_dir/'completed.json', {'stage': stage, 'returncode': result.returncode,
            'signed': False, 'releaseReady': False, 'at': stamp(now)})
    return 0 if result.returncode == 0 else 1

CUTOFF_CAUSES = ('supervisor-deadline', 'vm-stop')
SIGNALLED = (-15, -9, 143, 137)
BOOT_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
STAMP = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ')

def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None

def attempt_chain(prior):
    """[PRIOR, PRIOR.resume-1, ...]: the original attempt and every resume after it, in order. A gap is malformed."""
    prior = Path(prior)
    found = sorted(int(m[1]) for p in prior.parent.glob(prior.name + '.resume-*')
        for m in [re.fullmatch(re.escape(prior.name) + r'\.resume-([1-9][0-9]*)', p.name)] if m)
    if found != list(range(1, len(found) + 1)):
        raise InvalidInput(f'{prior}.resume-N attempts are not 1..{len(found)}: {found}')
    return [prior] + [Path(f'{prior}.resume-{n}') for n in found]

def classify(attempt):
    """(state, why) of one attempt directory from its own write-once receipts only."""
    attempt = Path(attempt)
    if attempt.is_symlink() or not attempt.is_dir():
        return 'malformed', f'{attempt} is not a receipt directory'
    files = {name: attempt/f'{name}.json' for name in ('preflight', 'started', 'completed')}
    docs = {name: read_json(path) if path.exists() else None for name, path in files.items()}
    broken = [name for name, path in files.items() if path.exists() and (path.is_symlink() or not isinstance(docs[name], dict))]
    if broken:
        return 'malformed', f'unreadable or linked receipts: {broken}'
    preflight_doc, started, completed = docs['preflight'], docs['started'], docs['completed']
    if preflight_doc is None:
        return 'malformed', 'no preflight.json'
    if started is None:
        if preflight_doc.get('ready') is not True or (completed or {}).get('started') is False:
            return 'never-started', 'preflight refused or the lock was taken; the command never ran'
        return 'never-started', 'preflight passed but no started.json: the command never ran'
    if preflight_doc.get('ready') is not True or started.get('stage') != 'compile' or started.get('device') != DEVICE \
            or started.get('signed') is not False or not isinstance(started.get('command'), dict):
        return 'malformed', 'started.json is not an unsigned rango compile after a ready preflight'
    if completed is None:
        return 'interrupted', 'started without completion; resumable only with a bound cutoff proof'
    if completed.get('stage') != 'compile' or not isinstance(completed.get('returncode'), int):
        return 'malformed', 'completed.json has no compile returncode'
    if completed['returncode'] == 0:
        return 'completed', 'the compile already completed with returncode 0'
    if completed['returncode'] in SIGNALLED:
        return 'interrupted', f'terminated by signal ({completed["returncode"]}); resumable only with a bound cutoff proof'
    return 'failed', f'ordinary failure, returncode {completed["returncode"]}; never resumed'

def check_cutoff(proof, attempt, started, build, out_name):
    """Problems with the operator's cutoff proof for exactly this attempt; [] when it binds."""
    if not isinstance(proof, dict) or proof.get('receiptType') != 'rango-compile-cutoff' or proof.get('schemaVersion') != 1:
        return ['the cutoff proof is not a rango-compile-cutoff schemaVersion 1 document']
    problems, prior = [], proof.get('prior') if isinstance(proof.get('prior'), dict) else {}
    if Path(str(prior.get('receiptDir'))) != Path(attempt) or prior.get('startedSha256') != sha256_file(Path(attempt)/'started.json') \
            or prior.get('preflightSha256') != sha256_file(Path(attempt)/'preflight.json'):
        problems.append(f'the cutoff proof is not bound to {attempt} (receiptDir, startedSha256, preflightSha256)')
    if proof.get('build') != {'buildNumber': str(build), 'outDir': out_name}:
        problems.append('the cutoff proof names another build or OUT_DIR')
    if proof.get('cause') not in CUTOFF_CAUSES:
        problems.append(f'cutoff cause {proof.get("cause")!r} is not one of {CUTOFF_CAUSES}; a failure is not a cutoff')
    stopped, observed, start = (str(x or '') for x in (proof.get('stoppedAt'), proof.get('observedAt'), started.get('at')))
    if not all(re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', t) for t in (stopped, observed, start)) \
            or not start < stopped <= observed:
        problems.append('the cutoff must be stamped after the attempt started and observed after it stopped')
    evidence = proof.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        problems.append('the cutoff proof carries no supervisor or VM stop evidence')
    for item in evidence if isinstance(evidence, list) else []:
        item = item if isinstance(item, dict) else {}
        path = Path(str(item.get('path')))
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or sha256_file(path) != item.get('sha256'):
            problems.append(f'stop evidence {path} is missing, linked or not the recorded bytes')
        elif not item.get('line') or item['line'] not in path.read_text(errors='replace').splitlines():
            problems.append(f'stop evidence {path} does not contain the quoted line')
    if not proof.get('observedBy'):
        problems.append('the cutoff proof names no observer')
    return problems

def check_provenance(provenance, original, config_path):
    """Problems with the supplemental provenance for fields an old-format started.json never recorded."""
    if not isinstance(provenance, dict) or provenance.get('receiptType') != 'rango-compile-provenance' \
            or provenance.get('schemaVersion') != 1:
        return ['the provenance is not a rango-compile-provenance schemaVersion 1 document'], {}
    problems, missing = [], [k for k in ('configSha256', 'recipe', 'projects', 'output', 'sources') if not provenance.get(k)
        or (k in ('recipe', 'projects', 'output', 'sources') and not isinstance(provenance[k], dict))]
    if missing:
        return [f'supplemental provenance is missing {missing}; resume refused rather than inventing them'], {}
    if not isinstance(provenance.get('prior'), dict) or provenance['prior'].get('startedSha256') != sha256_file(Path(original)/'started.json'):
        problems.append(f'the provenance is not bound to {original}/started.json')
    sources = provenance['sources']
    unsourced = [k for k in ('configSha256', 'projects', 'output') if not sources.get(k)]
    if unsourced:
        problems.append(f'provenance fields without a recorded source: {unsourced}')
    if provenance['configSha256'] != sha256_file(config_path):
        problems.append('the config differs from the one recorded at the original start')
    projects = provenance['projects']
    paths = [Path(str(projects.get(k))) for k in ('recordedPath', 'currentPath')]
    pins = None
    if any(not p.is_absolute() or p.is_symlink() or not p.is_file() for p in paths) or paths[0] == paths[1]:
        problems.append('the Android project maps (the retained one and one resolved now) must be two existing files')
    elif sha256_file(paths[0]) != projects.get('recordedSha256'):
        problems.append(f'{paths[0]} is not the retained project map with the recorded sha256')
    else:
        recorded, current = project_pins(paths[0]), project_pins(paths[1])
        if isinstance(recorded, str) or isinstance(current, str):
            problems.append(f'project map unusable: {recorded if isinstance(recorded, str) else current}')
        elif recorded != current:
            changed = sorted(set(recorded) ^ set(current))
            problems.append(f'the Android project pins differ from the retained map: {len(changed)} entries, e.g. {changed[:3]}')
        else:
            pins = len(recorded)
    uid = provenance['output'].get('uid')
    if not isinstance(uid, int) or isinstance(uid, bool):
        problems.append('provenance output.uid is not a recorded owner uid')
    return problems, {'uid': uid, 'projectsSha256': projects.get('recordedSha256'), 'projectPins': pins,
        'recipe': provenance['recipe'], 'sources': provenance['sources']}

def project_pins(path):
    """{(name, path, revision)} of a resolved manifest (repo manifest -r), compared as pins rather than as bytes;
    a reason string when a project has no pinned revision or appears twice."""
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, OSError) as error:
        return f'{path}: {error}'
    pins = set()
    for project in root.iter('project'):
        name, revision = project.get('name'), project.get('revision')
        if not name or not revision:
            return f'{path}: project {name!r} has no pinned revision'
        pin = (name, project.get('path') or name, revision)
        if pin in pins:
            return f'{path}: project {name!r} appears twice'
        pins.add(pin)
    return pins or f'{path}: no projects'

def check_output(src, out_name, uid, receipt_paths):
    """The existing OUT_DIR must be this build's own ordinary directory directly in SRC; its insides are not walked."""
    src, out = Path(src), Path(src)/out_name
    problems = []
    if any(Path(*src.parts[:i]).is_symlink() for i in range(2, len(src.parts) + 1)):
        problems.append(f'{src} has a linked component')
    if out.is_symlink() or not out.is_dir():
        return problems + [f'{out} is not an existing ordinary directory: nothing to resume'], {}
    if out.resolve().parent != src.resolve():
        problems.append(f'{out} is not directly inside the source root')
    stat = os.stat(out, follow_symlinks=False)
    if stat.st_uid != uid:
        problems.append(f'{out} is owned by uid {stat.st_uid}, not the recorded {uid}')
    for path in receipt_paths:
        resolved = Path(path).resolve()
        if resolved == out.resolve() or out.resolve() in resolved.parents or resolved in out.resolve().parents:
            problems.append(f'receipt path {path} collides with {out}')
    return problems, {'path': str(out), 'uid': stat.st_uid, 'device': stat.st_dev, 'inode': stat.st_ino}

def current_boot():
    """{'bootId', 'bootTime'} of this host from two host-wide kernel files, or None when either is unreadable or
    malformed (then nothing is admitted). No process, command line, environment or file descriptor is read."""
    try:
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        btime = [int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines() if line.startswith('btime ')]
    except (OSError, ValueError, IndexError):
        return None
    if not BOOT_ID.fullmatch(boot) or len(btime) != 1:
        return None
    return {'bootId': boot, 'bootTime': stamp(lambda: btime[0])}

def check_boot_change(witness, latest, started, stopped_at, build, now_boot):
    """Problems proving the latest attempt's whole process tree is gone: a boot id observed while that attempt existed
    (its own started.json boot for a resume attempt, else the operator's contemporaneous witness) differs from the
    current boot, and the current boot began after that observation and after the recorded stop. Nothing survives a
    reboot, so no process needs to be looked at. A same-boot stop is refused: proving termination there would need
    process inspection. This says nothing about writers started in the new boot that bypass the isolated lock."""
    if isinstance(started.get('boot'), dict):
        seen = {'bootId': started['boot'].get('bootId'), 'observedAt': started.get('at'), 'source': 'started.json'}
    elif witness is None:
        return ['the original boot id was not recorded at launch; a contemporaneous --boot-witness of this attempt is '
            'required (never a backdated one)'], {}
    elif not isinstance(witness, dict) or witness.get('receiptType') != 'rango-compile-boot-witness' \
            or witness.get('schemaVersion') != 1:
        return ['the boot witness is not a rango-compile-boot-witness schemaVersion 1 document'], {}
    else:
        attempt = witness.get('attempt') if isinstance(witness.get('attempt'), dict) else {}
        if Path(str(attempt.get('receiptDir'))) != Path(latest) \
                or attempt.get('startedSha256') != sha256_file(Path(latest)/'started.json') \
                or witness.get('buildNumber') != str(build):
            return [f'the boot witness is not bound to {latest} and build {build}'], {}
        if not witness.get('observedBy') or not witness.get('method'):
            return ['the boot witness names no observer or observation method'], {}
        seen = {'bootId': witness.get('bootId'), 'observedAt': witness.get('observedAt'), 'source': 'witness'}
    problems, start = [], str(started.get('at'))
    if not BOOT_ID.fullmatch(str(seen['bootId'])):
        problems.append('the witnessed boot id is not a kernel boot id')
    if not STAMP.fullmatch(str(seen['observedAt'])) or not start <= seen['observedAt'] or \
            (stopped_at and not seen['observedAt'] <= stopped_at):
        problems.append('the boot was not observed between the attempt start and the recorded stop')
    if now_boot is None:
        problems.append('the current boot id or boot time is unreadable; termination is unknown, not proven')
    elif not problems:
        if now_boot['bootId'] == seen['bootId']:
            problems.append('same boot as the live attempt: its process tree is not proven gone, and no process is '
                'inspected to prove it; a same-boot resume is unsupported')
        elif not (now_boot['bootTime'] > seen['observedAt'] and (not stopped_at or now_boot['bootTime'] >= stopped_at)):
            problems.append('the current boot did not begin after the witnessed boot and the recorded stop')
    return problems, {'witnessed': seen, 'current': now_boot}

def resume_admission(config, config_path, build, download_dir, prior, cutoff, provenance, inputs_receipt,
        boot_witness=None, boot=current_boot, **probes):
    """{'state', 'admitted', 'problems', 'facts'}: reads only. The caller holds the lock when it acts on the result."""
    out_name = out_dir_name(config, build)
    chain = attempt_chain(prior)
    states = [classify(a) for a in chain]
    latest, (state, why) = chain[-1], states[-1]
    facts = {'attempts': [{'receiptDir': str(a), 'state': s, 'why': w} for a, (s, w) in zip(chain, states)],
        'latest': str(latest), 'outDir': out_name, 'buildNumber': str(build)}
    report = {'state': state, 'admitted': False, 'problems': [], 'facts': facts}
    earlier = [s for s, _ in states[:-1]]
    if any(s != 'interrupted' for s in earlier):
        report['state'], report['problems'] = 'malformed', ['an earlier attempt in the chain was not interrupted']
        return report
    if state != 'interrupted':
        report['problems'] = [why]
        return report
    started = read_json(latest/'started.json')
    original_started, original_preflight = read_json(chain[0]/'started.json'), read_json(chain[0]/'preflight.json')
    problems = check_cutoff(read_json(cutoff), latest, started, build, out_name)
    if problems:
        report['state'] = 'interrupted-unproven'
    prov_problems, prov = check_provenance(read_json(provenance), chain[0], config_path)
    problems += prov_problems
    command = commands(config, build, download_dir)['compile']
    for attempt in chain:
        if read_json(attempt/'started.json').get('command') != command:
            problems.append(f'{attempt}/started.json ran another command, cwd or env than this config gives now')
    ready = preflight(config, 'compile', build, download_dir, inputs_receipt=inputs_receipt, existing_out=True,
        lock_probe=lambda path: True, **probes)
    problems += ready['problems']
    then = (original_preflight.get('facts') or {}).get('roseIntegration') or {}
    now = ready['facts'].get('roseIntegration') or {}
    if (original_preflight.get('facts') or {}).get('outDir') != out_name:
        problems.append('the original preflight recorded another OUT_DIR')
    for key in ('receipt', 'sha256'):
        if then.get(key) != now.get(key):
            problems.append(f'the reconcile receipt {key} differs from the original start')
    for key in ('receipt', 'sha256', 'release', 'product'):
        if (then.get('inputs') or {}).get(key) != (now.get('inputs') or {}).get(key):
            problems.append(f'the local-inputs {key} differs from the original start')
    out_problems, facts['output'] = check_output(config['host']['sourceRoot'], out_name, prov.get('uid'),
        [prior, f'{prior}.resume-{len(chain)}', cutoff, provenance] + ([boot_witness] if boot_witness else []))
    problems += out_problems
    proof = read_json(cutoff) or {}
    stopped_at = proof.get('stoppedAt') if STAMP.fullmatch(str(proof.get('stoppedAt'))) else None
    witness = read_json(boot_witness) if boot_witness else None
    if boot_witness and witness is None:
        witness = {}
    boot_problems, facts['boot'] = check_boot_change(witness, latest, started, stopped_at, build, boot())
    problems += boot_problems
    facts.update(preflight=ready, provenance=prov,
        bootWitnessSha256=sha256_file(boot_witness) if boot_witness and Path(boot_witness).is_file() else None,
        cutoffSha256=sha256_file(cutoff) if Path(cutoff).is_file() else None,
        provenanceSha256=sha256_file(provenance) if Path(provenance).is_file() else None,
        helperSha256=sha256_file(Path(__file__).resolve()))
    report['problems'] = problems
    if not problems:
        report['state'], report['admitted'] = 'resumable', True
    return report

def resume(config, config_path, build, download_dir, prior, cutoff, provenance, inputs_receipt, boot_witness=None,
        runner=subprocess.run, now=time.time, boot=current_boot, **probes):
    """Under the same nonblocking lock: re-admit, write PRIOR.resume-N, run the unchanged compile in the same OUT_DIR."""
    with open(config['host']['lockPath'], 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'verdict': 'NOT-ADMITTED', 'problems': ['the isolated builder lock is held']}), file=sys.stderr)
            return 1
        report = resume_admission(config, config_path, build, download_dir, prior, cutoff, provenance,
            inputs_receipt, boot_witness, boot=boot, **probes)
        if report['state'] == 'completed':
            print(json.dumps({'verdict': 'ALREADY-COMPLETED', 'latest': report['facts']['latest'], 'executed': False}))
            return 0
        if not report['admitted']:
            print(json.dumps({'verdict': 'NOT-ADMITTED', 'state': report['state'], 'problems': report['problems']},
                indent=2), file=sys.stderr)
            return 1
        attempt = Path(f'{prior}.resume-{len(report["facts"]["attempts"])}')
        attempt.mkdir()  # never exists: attempt_chain counted every resume-N before it
        latest = Path(report['facts']['latest'])
        resume_of = {'receiptDir': str(latest), 'original': str(prior),
            'receipts': {p.name: sha256_file(p) for p in sorted(latest.glob('*.json'))},
            'cutoffSha256': report['facts']['cutoffSha256'], 'provenanceSha256': report['facts']['provenanceSha256'],
            'bootWitnessSha256': report['facts']['bootWitnessSha256'], 'boot': report['facts']['boot'],
            'helperSha256': report['facts']['helperSha256']}
        release_receipts.write_once(attempt/'preflight.json', {**report['facts']['preflight'], 'ready': True,
            'resume': {k: report['facts'][k] for k in ('attempts', 'output', 'provenance', 'boot')},
            'at': stamp(now)})
        command = commands(config, build, download_dir)['compile']
        release_receipts.write_once(attempt/'started.json', {'stage': 'compile', 'device': DEVICE, 'command': command,
            'signed': False, 'resumeOf': resume_of, 'boot': report['facts']['boot']['current'], 'at': stamp(now)})
        result = runner(command['argv'], cwd=command['cwd'], env={**os.environ, **command['env']})
        release_receipts.write_once(attempt/'completed.json', {'stage': 'compile', 'returncode': result.returncode,
            'incremental': True, 'signed': False, 'releaseReady': False, 'at': stamp(now)})
    return 0 if result.returncode == 0 else 1

def stamp(now):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now()))

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['plan', 'preflight', 'run', 'resume-check', 'resume'])
    p.add_argument('--stage', choices=STAGES)
    p.add_argument('--config', default=str(CONFIG))
    p.add_argument('--build-number', required=True)
    p.add_argument('--download-dir', required=True)
    p.add_argument('--receipt-dir')
    p.add_argument('--inputs-receipt', help='compile: local-inputs.json from scripts/rango-local-inputs.py')
    p.add_argument('--prior-receipt-dir', help='resume: the original compile --receipt-dir')
    p.add_argument('--cutoff-proof', help='resume: the operator rango-compile-cutoff document')
    p.add_argument('--provenance', help='resume: the operator rango-compile-provenance document')
    p.add_argument('--boot-witness', help='resume: the operator rango-compile-boot-witness of a run that recorded no boot')
    a = p.parse_args(argv)
    try:
        config = load(a.config)
        if a.command in ('resume-check', 'resume'):
            if a.stage not in (None, 'compile') or a.receipt_dir or not (a.prior_receipt_dir and a.cutoff_proof
                    and a.provenance and a.inputs_receipt) or not Path(a.prior_receipt_dir).is_absolute():
                raise InvalidInput(f'{a.command} is compile-only and needs an absolute --prior-receipt-dir, '
                    '--cutoff-proof, --provenance and --inputs-receipt (its attempt directory is derived, never given)')
            args = (config, a.config, a.build_number, a.download_dir, Path(a.prior_receipt_dir), a.cutoff_proof,
                a.provenance, a.inputs_receipt, a.boot_witness)
            if a.command == 'resume':
                return resume(*args)
            report = resume_admission(*args)
            try:  # an observation only; resume itself takes the lock and re-checks everything while holding it
                report['facts']['lockFreeNow'] = lock_free(config['host']['lockPath'])
            except OSError as error:
                report['facts']['lockFreeNow'] = f'unobserved: {error.strerror}'
            print(json.dumps({**report, 'executed': False}, indent=2, sort_keys=True))
            return 0 if report['admitted'] or report['state'] == 'completed' else 1
        if a.command == 'plan':
            plan = {'commands': commands(config, a.build_number, a.download_dir),
                'preflight': {s: preflight(config, s, a.build_number, a.download_dir, inputs_receipt=a.inputs_receipt)
                    for s in STAGES}, 'executed': False}
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        if not a.stage:
            raise InvalidInput('--stage is required')
        if a.command == 'preflight':
            report = preflight(config, a.stage, a.build_number, a.download_dir, inputs_receipt=a.inputs_receipt)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report['ready'] else 1
        if not a.receipt_dir:
            raise InvalidInput('run needs --receipt-dir')
        return run(config, a.stage, a.build_number, a.download_dir, a.receipt_dir, inputs_receipt=a.inputs_receipt)
    except InvalidInput as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())
