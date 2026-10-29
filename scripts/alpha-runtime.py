#!/usr/bin/env python3
"""INFRA-ALPHA-120: boot the exported, verified Cuttlefish alpha on the separate runtime host and capture evidence.

Used only by .github/workflows/alpha-runtime.yml (never os-release.yml, never the roseos-frankel-builder runner):

  guard         hosted, first job. ROSEOS_ALPHA_RUNTIME_READY must be 'true', and the new runtime identity variables
                must be set, or nothing starts (SETUP_MISSING). The run must be this repository's alpha-runtime.yml
                on main by workflow_dispatch, and the dispatch inputs well formed.
  source-check  hosted, same job (actions: read). The export run must be this repository's os-release.yml on main
                by workflow_dispatch, completed successfully, holding exactly one emulator-alpha-export-<run>
                artifact, unexpired, with the dispatched API digest. Before any VM starts.
  start-host /  hosted controller (the NEW runtime identity only): an async start or stop of the one runtime
  stop-host     instance, then `instances describe` polling; never the builder, never a resubmitted request.
  expectation   anywhere, read-only: verify a downloaded export like `run` does and write (--out, a new file) the
                rose-verify expectation generated from its original verification receipt, for a manual capture.
  run           self-hosted runtime job (label roseos-cuttlefish-runtime; contents/actions read only, no id-token,
                secrets or publisher credential). It
                - verifies the downloaded export: only regular files at safe relative paths, SHA256SUMS covering
                  exactly them, the export receipt's dispatched sha256, every image and the host package equal to the
                  export receipt AND to the embedded original verification receipt, itself checked again;
                - preflights without changing the host: /dev/kvm read/write for the runner user, free space, no
                  foreign Cuttlefish process or instance lock, and a fresh task root; anything absent is
                  SETUP_MISSING and nothing is installed, added to a group or granted;
                - extracts the host package (no absolute, parent, link-escaping, device or special entries) into a
                  new task-owned root with its own HOME, so historical cuttlefish directories and userdata in the
                  runner's real HOME are never touched (their listing is compared before and after);
                - launches one instance (fixed instance number, no WebRTC viewer), waits for sys.boot_completed
                  within the deadline, then runs scripts/rose-verify.py runtime --interactive with an expectation
                  generated from the original verification receipt (screenshots and UI dumps are the only viewer
                  evidence; no pairing, permission grant or provider contact);
                - always stops the guest, checks no process of the task root remains, removes the task root and
                  records each of these, also after a failure.
                --evidence-dir receives alpha-runtime-evidence.json, the expectation, rose-verify's output and
                SHA256SUMS. No serial, secret or personal data is written.

Never builds, signs, publishes, pairs or contacts a provider. Exit status: 0 ok, 1 check failed, 2 invalid input,
4 setup missing.
"""
import argparse
import glob
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_receipts  # noqa: E402
from release_receipts import sha256_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'os/release-pipeline/alpha-runtime.json'
BUILDER_INSTANCE = 'immortal-os-builder'
EVIDENCE = 'alpha-runtime-evidence.json'
EXPECTATION = 'alpha-expectation.json'
HEX64 = re.compile(r'[0-9a-f]{64}')
# action: (target state, state a request is submitted from, transitional states that need no new request)
STATES = {'start': ('RUNNING', 'TERMINATED', {'PROVISIONING', 'STAGING'}), 'stop': ('TERMINATED', 'RUNNING', {'STOPPING'})}

def load_module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

exporter = load_module('alpha_export', 'alpha-export.py')
CheckFailed, InvalidInput, SetupMissing = exporter.CheckFailed, exporter.InvalidInput, exporter.SetupMissing

def load(path=CONFIG):
    return json.loads(Path(path).read_text())

def check_inputs(export_run, digest, receipt_sha256):
    if not re.fullmatch(r'[1-9][0-9]*', str(export_run or '')):
        raise InvalidInput('export_run must be the numeric run id of the os-release emulator-alpha-export run')
    for name, value in (('export_artifact_digest', digest), ('export_receipt_sha256', receipt_sha256)):
        if not HEX64.fullmatch(str(value or '')):
            raise InvalidInput(f'{name} must be a lowercase sha256')

def guard(config, env, export_run, digest, receipt_sha256):
    runtime, repo = config['runtime'], config['repository']
    missing = [] if env.get('ROSEOS_ALPHA_RUNTIME_READY') == 'true' else ['ROSEOS_ALPHA_RUNTIME_READY']
    missing += [n for n in ('ROSEOS_RUNTIME_WORKLOAD_IDENTITY_PROVIDER', 'ROSEOS_RUNTIME_CONTROLLER_SERVICE_ACCOUNT') if not env.get(n)]
    if missing:
        raise SetupMissing(missing)
    expected = {'GITHUB_REPOSITORY': repo, 'GITHUB_EVENT_NAME': runtime['event'],
        'GITHUB_WORKFLOW_REF': f'{repo}/{runtime["workflow"]}@{runtime["ref"]}'}
    wrong = [f'{k}={env.get(k)!r}' for k, v in expected.items() if env.get(k) != v]
    if wrong:
        raise CheckFailed('alpha runtime runs only as this repository\'s alpha-runtime.yml on main by workflow_dispatch: ' + ', '.join(wrong))
    check_inputs(export_run, digest, receipt_sha256)
    return {'ready': True, 'workflow': expected['GITHUB_WORKFLOW_REF']}

def source_check(config, api, export_run, digest):
    repo, export = config['repository'], config['export']
    run = api(f'/repos/{repo}/actions/runs/{export_run}')
    observed = {'repository': (run.get('repository') or {}).get('full_name'), 'path': run.get('path'), 'head_branch': run.get('head_branch'),
        'event': run.get('event'), 'status': run.get('status'), 'conclusion': run.get('conclusion')}
    expected = {'repository': repo, 'path': config['retainedVerification']['workflowPath'], 'head_branch': 'main',
        'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success'}
    wrong = [f'{k}={observed[k]!r}' for k in expected if observed[k] != expected[k]]
    if wrong or str(run.get('id')) != str(export_run):
        raise CheckFailed(f'run {export_run} is not a successful os-release.yml run on main: ' + ', '.join(wrong or ['id']))
    name = export['artifactName'].format(run_id=export_run)
    listed = api(f'/repos/{repo}/actions/runs/{export_run}/artifacts?name={name}').get('artifacts') or []
    if len(listed) != 1:
        raise CheckFailed(f'run {export_run} holds {len(listed)} artifacts named {name}, not exactly one')
    artifact = listed[0]
    if artifact.get('name') != name or artifact.get('expired') is not False or artifact.get('digest') != 'sha256:' + digest \
            or str((artifact.get('workflow_run') or {}).get('id')) != str(export_run):
        raise CheckFailed(f'artifact {name} is expired, from another run or not the dispatched digest')
    return {'run': observed | {'id': str(export_run), 'headSha': run.get('head_sha')},
        'artifact': {'id': artifact.get('id'), 'name': name, 'digest': artifact['digest'], 'size': artifact.get('size_in_bytes')}}

def control(config, action, gcloud='gcloud', timeout=420, interval=10, sleep=time.sleep, clock=time.monotonic):
    """Async start/stop of the runtime instance, then describe polling only; never the builder, never a resubmission."""
    host = config['runtime']['host']
    if host['instance'] == BUILDER_INSTANCE:
        raise CheckFailed('the runtime host may never be the builder')
    target, source, passing = STATES[action]
    where = [f'--project={host["project"]}', f'--zone={host["zone"]}']

    def describe():
        result = subprocess.run([gcloud, 'compute', 'instances', 'describe', host['instance'], *where, '--format=value(status)'],
            capture_output=True, text=True)
        if result.returncode:
            raise CheckFailed(f'runtime host describe failed (exit {result.returncode})')
        return result.stdout.strip()
    state = describe()
    if state == source:
        result = subprocess.run([gcloud, 'compute', 'instances', action, host['instance'], *where, '--async', '--quiet'],
            capture_output=True, text=True)
        if result.returncode:
            state = describe()
            if state not in passing | {target}:
                raise CheckFailed(f'runtime host {action} request failed (exit {result.returncode}); observed {state}')
    elif state not in passing | {target}:
        raise CheckFailed(f'runtime host is {state}; refusing to {action} it')
    deadline = clock() + timeout
    while state != target:
        if state not in passing | {source}:
            raise CheckFailed(f'runtime host moved to {state} while waiting for {target}')
        if clock() >= deadline:
            raise CheckFailed(f'runtime host did not reach {target} within {timeout}s; last observed {state}')
        sleep(interval)
        state = describe()
    return state

def verify_export(config, directory, receipt_sha256):
    """The downloaded export, checked completely before anything is extracted or launched."""
    directory, export = Path(directory), config['export']
    files = exporter.regular_files(directory)
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not re.fullmatch(r'[A-Za-z0-9_./-]+', name):
            raise CheckFailed(f'{name} is not a safe relative path')
    sums = exporter.read_sums(directory)
    if sorted(sums) != sorted(n for n in files if n != 'SHA256SUMS'):
        raise CheckFailed('SHA256SUMS does not cover exactly the exported files')
    for name, digest in sums.items():
        if sha256_file(directory/name) != digest:
            raise CheckFailed(f'{name} does not match SHA256SUMS')
    if sums.get(export['receipt']) != receipt_sha256:
        raise CheckFailed(f'{export["receipt"]} is not the dispatched export_receipt_sha256')
    receipt = json.loads((directory/export['receipt']).read_text())
    original_name = config['retainedVerification']['receipt']
    if (receipt.get('receiptType'), receipt.get('executedMake'), receipt.get('signed'), receipt.get('published')) \
            != ('emulator-alpha-export', False, False, False):
        raise CheckFailed('the export receipt is not an unsigned, unpublished emulator-alpha-export receipt')
    with tempfile.TemporaryDirectory() as scratch:
        original_dir = Path(scratch)
        shutil.copyfile(directory/'original'/original_name, original_dir/original_name)
        (original_dir/'SHA256SUMS').write_text(f'{sums["original/" + original_name]}  {original_name}\n')
        original, original_digest = exporter.check_received(config, original_dir, (receipt.get('originalVerification') or {}).get('receiptSha256'))
    expected_files = sorted([f'images/{n}' for n in original['images']] + [f'host/{export["hostPackage"]}',
        f'original/{original_name}', export['receipt'], 'SHA256SUMS'])
    if files != expected_files:
        raise CheckFailed(f'the export holds {files}, not exactly the measured images, host package and receipts')
    if receipt.get('images') != original['images'] or receipt.get('hostPackage') != {export['hostPackage']: original['hostOutputs'][export['hostPackage']]}:
        raise CheckFailed('the export receipt does not list exactly the images and host package of the original verification')
    for name, row in original['images'].items():
        path = directory/'images'/name
        if path.stat().st_size != row['size'] or sums[f'images/{name}'] != row['sha256']:
            raise CheckFailed(f'exported image {name} is not the verified bytes')
    host = original['hostOutputs'][export['hostPackage']]
    if (directory/'host'/export['hostPackage']).stat().st_size != host['size'] or sums[f'host/{export["hostPackage"]}'] != host['sha256']:
        raise CheckFailed('the exported host package is not the verified bytes')
    if (receipt.get('originalCompile') or {}).get('runId') != config['originalCompile']['runId']:
        raise CheckFailed('the export receipt names another original compile')
    return receipt, original, original_digest

def expectation(config, original, original_digest):
    """A rose-verify expectation generated from the original verification receipt alone (not from source or a plan)."""
    alpha = load(ROOT/config['alphaConfig'])
    signers = {s for a in original['imageContent']['apps'] for s in a['signers']}
    if len(signers) != 1:
        raise CheckFailed('the verified apps do not share exactly one signer')
    lunch = original.get('lunch') or {}
    return {'schemaVersion': 1, 'kind': 'rose-verify-expectation',
        'label': f'alpha compile {config["originalCompile"]["runId"]} verified by {config["retainedVerification"]["runId"]}: '
            f'product {original["candidate"][:8]}, apps ' + '/'.join(str(a['identity'][1]) for a in original['imageContent']['apps']),
        'channel': 'alpha', 'candidate': original['candidate'],
        'target': {'lunch': alpha['target']['lunch'], 'device': lunch.get('PRODUCT_DEVICE') or alpha['hostEvidence']['lunch']['PRODUCT_DEVICE'],
            'actionsRun': config['originalCompile']['runId'], 'verificationRun': config['retainedVerification']['runId'],
            'recipe': config['retainedVerification']['recipe']},
        'apps': [{'package': a['package'], 'version': a['identity'][1], 'sha256': a['sha256'], 'imagePath': f'{a["partition"]}/{a["path"]}'}
            for a in original['imageContent']['apps']],
        'appSignerSha256': signers.pop(),
        'basis': f'Generated from the original verification receipt of run {config["retainedVerification"]["runId"]} '
            f'(sha256 {original_digest}): imageContent apps (bytes, identity, signer, image path) and lunch. Historical alpha: Rose Agent '
            '302, Calculator 4 and controller 28 as of 2804; no Pixel, Agent 308, OS PR70 Home role or PR85 tray claim'}

class System:
    """Everything that touches the runtime host; the tests replace it with a synthetic one."""
    proc = Path('/proc')
    kvm = '/dev/kvm'

    def access(self, path, mode):
        return os.access(path, mode)

    def exists(self, path):
        return os.path.exists(path)

    def free_gib(self, path):
        return shutil.disk_usage(path).free / 2**30

    def processes(self):
        found = []
        for entry in self.proc.glob('[0-9]*'):
            try:
                found.append((int(entry.name), (entry/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()))
            except OSError:
                continue
        return found

    def locks(self, patterns):
        return sorted(p for pattern in patterns for p in glob.glob(pattern))

    def run(self, argv, env, timeout, cwd=None):
        try:
            result = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            return result.returncode, (result.stdout + result.stderr)[-4000:]
        except subprocess.TimeoutExpired:
            return None, 'timed out'

    def sleep(self, seconds):
        time.sleep(seconds)

    def clock(self):
        return time.monotonic()

def foreign(config, system, root=None):
    names = config['runtime']['foreignProcesses']
    running = [(pid, cmd) for pid, cmd in system.processes() if any(os.path.basename(part) in names for part in cmd.split()[:1])]
    if root is not None:
        running = [(pid, cmd) for pid, cmd in running if str(root) not in cmd]
    return running

def preflight(config, system, home, root):
    runtime, missing = config['runtime'], []
    if not system.exists(system.kvm):
        missing.append('/dev/kvm (absent on this host)')
    elif not system.access(system.kvm, os.R_OK | os.W_OK):
        missing.append('/dev/kvm read/write access for the runner user')
    if system.free_gib(home) < runtime['minFreeGiB']:
        missing.append(f'{runtime["minFreeGiB"]} GiB free under the runner HOME')
    if missing:
        raise SetupMissing(missing)
    if system.exists(root):
        raise CheckFailed(f'task root {root} already exists; a runtime attempt never reuses another one\'s root or userdata')
    running, locks = foreign(config, system), system.locks(runtime['foreignLocks'])
    if running or locks:
        raise CheckFailed(f'a foreign Cuttlefish guest is present ({len(running)} processes, instance locks {locks}); nothing is launched')

def safe_extract(archive, destination):
    """The host package: regular files, directories and links that stay inside destination; nothing else."""
    destination = Path(destination).resolve()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not member.name:
                raise CheckFailed(f'host package entry {member.name!r} escapes the task root')
            if not (member.isfile() or member.isdir() or member.issym()):
                raise CheckFailed(f'host package entry {member.name!r} is not a regular file, directory or symlink')
            if member.issym():
                target = (destination/path.parent/member.linkname).resolve()
                if PurePosixPath(member.linkname).is_absolute() or not target.is_relative_to(destination):
                    raise CheckFailed(f'host package link {member.name!r} points outside the task root')
            if member.mode & (stat.S_ISUID | stat.S_ISGID):
                raise CheckFailed(f'host package entry {member.name!r} is setuid or setgid')
        try:
            tar.extractall(destination, members=members, filter='data')
        except TypeError:  # a Python without extraction filters: every member was checked above
            tar.extractall(destination, members=members)

def snapshot(home, names):
    """Listing (path, size, mtime) of the runner's historical Cuttlefish directories; contents are never read."""
    result = {}
    for name in names:
        base = Path(home)/name
        if not base.exists() and not base.is_symlink():
            result[name] = None
            continue
        rows = []
        for directory, dirs, files in os.walk(base, followlinks=False):
            for entry in sorted(dirs + files):
                path = Path(directory)/entry
                info = path.lstat()
                rows.append([str(path.relative_to(base)), info.st_size, info.st_mtime_ns])
        result[name] = sorted(rows)
    return result

def run(config, artifact_dir, receipt_sha256, run_id, attempt, evidence_dir, home, system=None, handback=None):
    system = system or System()
    runtime = config['runtime']
    if not re.fullmatch(r'[1-9][0-9]*', str(run_id or '')) or not re.fullmatch(r'[1-9][0-9]*', str(attempt or '')):
        raise InvalidInput('--run-id and --attempt must be the numeric Actions run id and attempt')
    evidence_dir, home = Path(evidence_dir), Path(home)
    if evidence_dir.exists():
        raise CheckFailed(f'{evidence_dir} already exists; use a new evidence directory')
    root = home/runtime['root']/f'{run_id}-{attempt}'
    evidence = {'receiptType': 'alpha-runtime-evidence', 'verdict': 'FAIL', 'error': None, 'runtimeRun': str(run_id),
        'attempt': str(attempt), 'instanceNumber': runtime['instanceNumber'], 'host': runtime['host']['instance'],
        'launched': False, 'bootCompleted': None, 'bootSeconds': None, 'roseVerify': None,
        'stopped': None, 'leftoverProcesses': None, 'taskRootRemoved': None, 'historicalUnchanged': None,
        'signed': False, 'published': False, 'paired': False, 'providerContacted': False, 'viewer': 'screenshots only (no WebRTC)',
        'notProven': ['Pixel hardware, firmware, radio or flashing', 'Agent 308, OS PR70 Home role or PR85 tray (not in this image)',
            'pairing, backend or provider behaviour (none attempted)']}
    before = snapshot(home, runtime['historicalPaths'])
    launched = created = False
    env = None
    try:
        receipt, original, original_digest = verify_export(config, artifact_dir, receipt_sha256)
        evidence['export'] = {'receiptSha256': receipt_sha256, 'exportRun': receipt.get('exportRun'),
            'originalVerification': receipt.get('originalVerification'), 'originalReceiptSha256': original_digest,
            'images': sorted(original['images']), 'hostPackage': config['export']['hostPackage']}
        preflight(config, system, home, root)
        root.mkdir(parents=True)
        created = True  # only a root this attempt created is ever removed; a pre-existing one is refused and left alone
        (root/'home').mkdir()
        safe_extract(Path(artifact_dir)/'host'/config['export']['hostPackage'], root/'host')
        tools = [root/'host'/t for t in runtime['hostTools']]
        absent = [str(t.relative_to(root/'host')) for t in tools if not t.is_file() or not os.access(t, os.X_OK)]
        if absent:
            raise SetupMissing([f'host package tool {t}' for t in absent])
        shutil.copytree(Path(artifact_dir)/'images', root/'images')
        document = expectation(config, original, original_digest)
        evidence_dir.mkdir(parents=True)
        release_receipts.write_once(evidence_dir/EXPECTATION, document)
        launch, stop, adb = (str(t) for t in tools)
        env = {'HOME': str(root/'home'), 'PATH': f'{root/"host/bin"}:/usr/bin:/bin', 'ANDROID_HOST_OUT': str(root/'host'),
            'LANG': 'C.UTF-8'}
        argv = [launch, *runtime['launchArgs'], f'--base_instance_num={runtime["instanceNumber"]}', '--num_instances=1',
            f'--system_image_dir={root/"images"}']
        started = system.clock()
        launched = True
        code, output = system.run(argv, env, runtime['bootTimeoutSeconds'], cwd=str(root/'home'))
        evidence['launched'] = True
        evidence['launchExit'] = code
        if code != 0:
            raise CheckFailed(f'launch_cvd exited {code} before boot completed')
        serial = f'0.0.0.0:{runtime["adbPortBase"] + runtime["instanceNumber"] - 1}'
        system.run([adb, 'connect', serial], env, 30)
        while True:
            code, output = system.run([adb, '-s', serial, 'shell', 'getprop', 'sys.boot_completed'], env, 30)
            if code == 0 and output.strip() == '1':
                break
            if system.clock() - started >= runtime['bootTimeoutSeconds']:
                evidence['bootCompleted'] = False
                raise CheckFailed(f'sys.boot_completed was not 1 within {runtime["bootTimeoutSeconds"]}s')
            system.sleep(5)
        evidence['bootCompleted'], evidence['bootSeconds'] = True, round(system.clock() - started)
        reference = handback or f'alpha-runtime.yml run {run_id} attempt {attempt}: task-owned instance {runtime["instanceNumber"]} on {runtime["host"]["instance"]}'
        code, output = system.run([sys.executable, str(ROOT/'scripts/rose-verify.py'), 'runtime', '--serial', serial, '--handback', reference,
            '--expect', str(evidence_dir/EXPECTATION), '--out', str(evidence_dir/'rose-verify'), '--interactive', '--adb', adb], env | {'PATH': os.environ.get('PATH', '')}, 600)
        evidence['roseVerify'] = {'exit': code, 'out': 'rose-verify'}
        if code != 0:
            raise CheckFailed(f'rose-verify runtime exited {code}')
        evidence['verdict'] = 'PASS'
    except (CheckFailed, SetupMissing, InvalidInput) as error:
        evidence['error'] = ('setup missing: ' + ', '.join(error.missing)) if isinstance(error, SetupMissing) else str(error)[:4096]
        raise
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        evidence['error'] = error.__class__.__name__
        raise
    finally:
        if launched:
            # stop_cvd finds the instance through the task root's own HOME; nothing outside it is addressed.
            code, _ = system.run([str(root/'host'/runtime['hostTools'][1])], env, 300)
            evidence['stopped'] = code == 0
            left = [pid for pid, cmd in system.processes() if str(root) in cmd]
            evidence['leftoverProcesses'] = len(left)
        if created and root.is_relative_to(home/runtime['root']):
            shutil.rmtree(root, ignore_errors=True)
            evidence['taskRootRemoved'] = not root.exists()
        evidence['historicalUnchanged'] = snapshot(home, runtime['historicalPaths']) == before
        if evidence['verdict'] == 'PASS' and (evidence['stopped'] is False or evidence['leftoverProcesses'] or not evidence['historicalUnchanged']):
            evidence['verdict'], evidence['error'] = 'FAIL', 'cleanup incomplete: guest not stopped, processes left or historical data changed'
        evidence['at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        evidence_dir.mkdir(parents=True, exist_ok=True)
        release_receipts.write_once(evidence_dir/EVIDENCE, evidence)
        names = exporter.regular_files(evidence_dir)
        (evidence_dir/'SHA256SUMS').write_text(''.join(f'{sha256_file(evidence_dir/n)}  {n}\n' for n in names if n != 'SHA256SUMS'))
    if evidence['verdict'] != 'PASS':
        raise CheckFailed(evidence['error'])
    return evidence

def main(argv=None, system=None, api=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['guard', 'source-check', 'start-host', 'stop-host', 'expectation', 'run'])
    p.add_argument('--config', default=str(CONFIG))
    p.add_argument('--export-run')
    p.add_argument('--export-artifact-digest')
    p.add_argument('--export-receipt-sha256')
    p.add_argument('--artifact-dir')
    p.add_argument('--evidence-dir')
    p.add_argument('--run-id')
    p.add_argument('--attempt')
    p.add_argument('--out')
    a = p.parse_args(argv)
    try:
        config = load(a.config)
        if a.command == 'guard':
            result = guard(config, os.environ, a.export_run, a.export_artifact_digest, a.export_receipt_sha256)
        elif a.command == 'source-check':
            check_inputs(a.export_run, a.export_artifact_digest, a.export_receipt_sha256)
            result = source_check(config, api or exporter.github_api(), a.export_run, a.export_artifact_digest)
        elif a.command in ('start-host', 'stop-host'):
            result = {'state': control(config, a.command.split('-')[0])}
        elif a.command == 'expectation':
            if not a.artifact_dir or not a.out or not HEX64.fullmatch(str(a.export_receipt_sha256 or '')):
                raise InvalidInput('expectation needs --artifact-dir, --export-receipt-sha256 and --out')
            _, original, digest = verify_export(config, a.artifact_dir, a.export_receipt_sha256)
            result = expectation(config, original, digest)
            release_receipts.write_once(Path(a.out), result)
        else:
            check_inputs(a.export_run, a.export_artifact_digest, a.export_receipt_sha256)
            if not a.artifact_dir or not a.evidence_dir:
                raise InvalidInput('--artifact-dir and --evidence-dir are required')
            result = run(config, a.artifact_dir, a.export_receipt_sha256, a.run_id, a.attempt, a.evidence_dir, Path.home(), system)
    except SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except InvalidInput as error:
        print(json.dumps({'verdict': 'INVALID', 'error': str(error)}), file=sys.stderr)
        return 2
    except (CheckFailed, release_receipts.ReceiptExists) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)[:4096]}), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': error.__class__.__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

if __name__ == '__main__':
    sys.exit(main())
