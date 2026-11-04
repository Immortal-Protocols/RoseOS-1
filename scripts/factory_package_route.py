"""Device-neutral factory-install packaging orchestration (Release-owned), shared by the per-device os-release wirings
scripts/rango-factory-package-wiring.py and scripts/frankel-factory-package-wiring.py.

A Route names everything one device's run uses:
- its device and build;
- the input pins handed to Signing's scripts/host-factory-package.py (signed target-files, otatools, release script,
  fastboot);
- its task-root and attempt-record names, and its receipt schema names;
- the private installer artifact caps;
- an optional label that every report and staged record carries.
Each function takes the route explicitly; nothing is taken from another device. The names and schemas default from
the device, so the Rango route reproduces the Rango wiring's names exactly.

The helpers called are unchanged and device-parameterized already:
- scripts/host-factory-package.py (Signing): it holds the shared builder lock for its whole run;
- scripts/factory-package.py observe and classify (Flavors);
- scripts/rango-firmware-observe.py (Signing; device-generic despite its name).
Nothing here signs, reads a secret, logs in, downloads, publishes or touches a phone.
"""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT/'scripts/host-factory-package.py'
INSPECTOR = ROOT/'scripts/factory-package.py'
FIRMWARE = ROOT/'scripts/rango-firmware-observe.py'
TASK_PARENT = '/home/pixelbuild/rose-actions'
RESULT_BYTES = 1 << 16  # the helper prints one short JSON line; anything longer is not trusted as a result
CATEGORY = re.compile(r'[a-z][a-z_]{0,39}')
UPLOAD = {'package/factory-package-receipt.json': 'factory-package-receipt.json',
          'inspection/target-files-observation.json': 'target-files-observation.json',
          'inspection/firmware-observation.json': 'firmware-observation.json',
          'inspection/firmware-attempt.json': 'firmware-attempt.json',
          'inspection/classify.json': 'classify.json', 'cleanup.json': 'cleanup.json'}


class Invalid(Exception):
    pass


@dataclass(frozen=True)
class Route:
    """One device's packaging run. pins: {'targetFiles': {'path', 'sha256'}, 'otatools': {'path', 'sha256'},
    'releaseScript': {'path', 'sha256'}, 'fastbootSha256'}. label, when set, is carried by every report and staged
    record (for example that the installer is an unsigned intermediate)."""
    device: str
    build: str
    pins: dict
    installer: dict = field(default_factory=lambda: {'capBytes': 8 << 30, 'retentionDays': 3, 'uploadMinutes': 45})
    task_parent: str = TASK_PARENT
    label: str = None

    @property
    def mode(self):
        return f'{self.device}-factory-package'

    @property
    def install(self):
        return f'{self.device}-install-{self.build}.zip'

    @property
    def task_name(self):
        return self.mode + '-{run_id}-{attempt}'

    @property
    def attempt_schema(self):
        return f'roseos.{self.mode}-attempt.v1'

    @property
    def attempt_name(self):
        return self.mode + '-attempt-{run_id}-{attempt}.json'

    @property
    def firmware_attempt_schema(self):
        return f'roseos.{self.device}-firmware-observe-attempt.v1'

    @property
    def cleanup_schema(self):
        return f'roseos.{self.mode}-cleanup.v1'

    def source(self, run_id, attempt):
        return f'os-release {self.mode} run {run_id} attempt {attempt}'


def task_root(route, run_id, attempt, parent=None):
    for label, value in (('run id', run_id), ('attempt', attempt)):
        if not (isinstance(value, str) and value.isdigit() and value[0] != '0' and len(value) <= 20):
            raise Invalid(f'a positive numeric {label} is required')
    return Path(parent or route.task_parent)/route.task_name.format(run_id=run_id, attempt=attempt)


def lock_path(root=ROOT):
    return json.loads((Path(root)/'os/release-pipeline/frankel.json').read_text())['host']['lockPath']


def package_argv(route, root_dir, root=ROOT):
    pins = route.pins
    return [sys.executable, str(HELPER), '--device', route.device, '--build', route.build,
            '--target-files', pins['targetFiles']['path'], '--target-files-sha256', pins['targetFiles']['sha256'],
            '--otatools', pins['otatools']['path'], '--otatools-sha256', pins['otatools']['sha256'],
            '--release-script', pins['releaseScript']['path'], '--release-script-sha256', pins['releaseScript']['sha256'],
            '--fastboot-sha256', pins['fastbootSha256'],
            '--lock', lock_path(root), '--out', str(root_dir/'package')]


def observe_argv(route, root_dir, run_id, attempt):
    return [sys.executable, str(INSPECTOR), 'observe', route.pins['targetFiles']['path'], '--flavor', route.device,
            '--build', route.build, '--source', route.source(run_id, attempt),
            '--out', str(root_dir/'inspection/target-files-observation.json')]


def firmware_argv(route, root_dir, run_id, attempt):
    return [sys.executable, str(FIRMWARE), '--target-files', route.pins['targetFiles']['path'],
            '--target-files-sha256', route.pins['targetFiles']['sha256'], '--device', route.device, '--build', route.build,
            '--source', route.source(run_id, attempt), '--out', str(root_dir/'inspection/firmware-observation.json')]


def classify_argv(route, root_dir, firmware=False):
    pins = route.pins
    return [sys.executable, str(INSPECTOR), 'classify', str(root_dir/'package'/route.install), '--flavor', route.device,
            '--target-files', pins['targetFiles']['path'],
            '--target-files-observation', str(root_dir/'inspection/target-files-observation.json'),
            *(['--firmware-observation', str(root_dir/'inspection/firmware-observation.json')] if firmware else []),
            '--otatools', pins['otatools']['path'], '--otatools-sha256', pins['otatools']['sha256'],
            '--work-dir', str(root_dir/'classify-work'), '--build', route.build]


def new_dir(path):
    path = Path(path)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise Invalid(f'{path.parent} must be an existing plain directory (not created here)')
    path.mkdir(mode=0o700)  # FileExistsError: never reused


def packaged(route, root_dir):
    receipt = root_dir/'package/factory-package-receipt.json'
    if receipt.is_symlink() or not receipt.is_file():
        raise Invalid('no package receipt: nothing to inspect')
    record = json.loads(receipt.read_text())
    archive = root_dir/'package'/route.install
    if record.get('status') != 'packaged' or archive.is_symlink() or not archive.is_file():
        raise Invalid('the package step did not produce the install archive: nothing to inspect')


def sanitized_result(stdout):
    """The helper's one-line JSON result reduced to fixed fields; anything else is not interpreted."""
    lines = [line for line in (stdout or '').splitlines() if line.strip()]
    if not lines or len(stdout) > RESULT_BYTES:
        return {'category': 'no_result'}
    try:
        result = json.loads(lines[-1])
    except ValueError:
        return {'category': 'no_result'}
    if isinstance(result, dict) and isinstance(result.get('refused'), str):
        head = result['refused'].split(':', 1)[0]
        return {'category': head if CATEGORY.fullmatch(head) else 'refused', 'detail': result['refused'][:500]}
    if isinstance(result, dict) and isinstance(result.get('packaged'), dict):
        output = result['packaged']
        return {'category': 'packaged', 'output': {k: output.get(k) for k in ('file', 'sha256', 'size')}}
    return {'category': 'no_result'}


def firmware_observe(route, root_dir, run_id, attempt, runner=subprocess.run, require_package=True, argv=None):
    """Run the firmware observer once; keep its exit and sanitized one-line result write-once. Returns the step status:
    0 for an observation (exit 0 with a new regular output) or a refusal (exit 3 with none), otherwise failure. An
    inspect-only mode passes require_package=False: it verified a retained archive instead of packaging one."""
    if require_package:
        packaged(route, root_dir)
    inspection, output = root_dir/'inspection', root_dir/'inspection/firmware-observation.json'
    if inspection.is_symlink() or not inspection.is_dir():
        raise Invalid('the target-files observation step did not run: nothing to observe into')
    if os.path.lexists(output):
        raise Invalid('a firmware observation already exists here; it is never reused')
    record = {'schema': route.firmware_attempt_schema, 'step': 'firmware-observe'}
    fd = os.open(inspection/'firmware-attempt.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        try:
            result = runner(argv or firmware_argv(route, root_dir, run_id, attempt), stdout=subprocess.PIPE, text=True)
        except OSError as error:
            record.update(exit=1, category='spawn_failed', detail=type(error).__name__)
        else:
            record.update(exit=result.returncode, **sanitized_result(result.stdout))
            produced = output.is_file() and not output.is_symlink()
            if result.returncode == 0 and not produced:
                record.update(category='no_output', detail='exit 0 without a regular observation file')
            elif result.returncode != 0 and os.path.lexists(output):
                record.update(category='unexpected_output', detail='an output file exists after a non-zero exit')
            elif result.returncode == 0:
                record['category'] = 'observed'
                record.pop('detail', None)
        finally:
            record.setdefault('exit', 1)
            record.setdefault('category', 'wiring_interrupted')
            f.write(json.dumps(record, indent=2, sort_keys=True) + '\n')
    ok = (record['exit'], record['category']) == (0, 'observed') or (record['exit'] == 3 and record['category'] not in
                                                                      ('unexpected_output', 'no_result'))
    return 0 if ok else (record['exit'] or 1)


def firmware_observed(root_dir):
    """True only when the firmware step recorded an observation and the file is still the regular file it wrote."""
    attempt, output = root_dir/'inspection/firmware-attempt.json', root_dir/'inspection/firmware-observation.json'
    if attempt.is_symlink() or not attempt.is_file():
        return False
    record = json.loads(attempt.read_text())
    return (record.get('exit'), record.get('category')) == (0, 'observed') and output.is_file() and not output.is_symlink()


def classify(route, root_dir, runner=subprocess.run, argv=None):
    packaged(route, root_dir)
    firmware = firmware_observed(root_dir)
    new_dir(root_dir/'classify-work')
    out = os.open(root_dir/'inspection/classify.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(out, 'w') as f:
        code = runner(argv(firmware) if argv else classify_argv(route, root_dir, firmware=firmware), stdout=f).returncode
    return 0 if code in (0, 3) else (code or 1)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def stage_installer(route, root_dir, cap=None, hasher=sha256_file):
    """The ZIP the helper recorded, verified again before it may leave: regular file, recorded size and sha256, within
    the cap, and only after inspection wrote its report. Writes R/SHA256SUMS; returns the export record."""
    cap = route.installer['capBytes'] if cap is None else cap
    packaged(route, root_dir)
    report_file = root_dir/'inspection/classify.json'
    if report_file.is_symlink() or not report_file.is_file():
        raise Invalid('the inspection report is missing: the installer is not exported uninspected')
    output = json.loads((root_dir/'package/factory-package-receipt.json').read_text()).get('output') or {}
    if output.get('file') != route.install or not isinstance(output.get('size'), int) or not (
            isinstance(output.get('sha256'), str) and len(output['sha256']) == 64):
        raise Invalid('the package receipt does not record the install archive name, size and sha256')
    archive = root_dir/'package'/route.install
    size = os.lstat(archive).st_size
    if size != output['size']:
        raise Invalid(f'the archive is {size} bytes, not the recorded {output["size"]}: not exported')
    if size > cap:
        raise Invalid(f'the archive is {size} bytes, above the proposed {cap}-byte cap: not exported')
    digest = hasher(archive)
    if digest != output['sha256']:
        raise Invalid('the archive sha256 differs from the package receipt: not exported')
    fd = os.open(root_dir/'SHA256SUMS', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as f:
        f.write(f'{digest}  package/{route.install}\n')
    record = {'file': f'package/{route.install}', 'bytes': size, 'sha256': digest, 'capBytes': cap,
              'retentionDays': route.installer['retentionDays']}
    if route.label:
        record['label'] = route.label
    return record


def attempt_record_path(route, value, run_id, attempt):
    """The attempt record lives outside R (which may never be created): a new file with the fixed per-run name in an
    existing plain directory (the runner's temp directory in the workflow)."""
    path = Path(value or '')
    if (not value or not path.is_absolute() or os.path.normpath(value) != value
            or path.name != route.attempt_name.format(run_id=run_id, attempt=attempt)):
        raise Invalid(f'--attempt-record must be an absolute path named {route.attempt_name}')
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise Invalid('the attempt record directory must be an existing plain directory')
    return path


def package(route, root_dir, record_path, runner=subprocess.run, argv=None):
    """Create R, run the helper and keep its outcome in the write-once attempt record. Returns the exit status."""
    record = {'schema': route.attempt_schema, 'step': 'package', 'taskRoot': str(root_dir)}
    if route.label:
        record['label'] = route.label
    fd = os.open(record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)  # before anything else
    with os.fdopen(fd, 'w') as f:
        try:
            new_dir(root_dir)
        except (Invalid, OSError) as error:
            record.update(exit=2, category='task_root_refused', detail=str(error)[:500])
        else:
            try:
                result = runner(argv or package_argv(route, root_dir), stdout=subprocess.PIPE, text=True)
            except OSError as error:
                record.update(exit=1, category='spawn_failed', detail=type(error).__name__)
            else:
                record.update(exit=result.returncode, **sanitized_result(result.stdout))
                print((result.stdout or '').strip()[:RESULT_BYTES], flush=True)
        finally:
            record.setdefault('exit', 1)
            record.setdefault('category', 'wiring_interrupted')
            f.write(json.dumps(record, indent=2, sort_keys=True) + '\n')
    return record['exit']


def report(route, root_dir, upload_dir, installer_upload=None, record_path=None):
    """The observed scratch state, then the allowlisted copies. Works when the task root was never created."""
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(mode=0o700)
    state = {'schema': route.cleanup_schema, 'taskRoot': str(root_dir),
             'taskRootExists': root_dir.is_dir() and not root_dir.is_symlink()}
    if route.label:
        state['label'] = route.label
    record = Path(record_path) if record_path else None
    if record is not None and record.is_file() and not record.is_symlink():
        (upload_dir/'package-attempt.json').write_bytes(record.read_bytes())
        state['packageAttempt'] = 'recorded'
    else:
        state['packageAttempt'] = 'absent (the package step did not run or was stopped before recording)'

    if state['taskRootExists']:
        work, scratch = root_dir/'package/work', root_dir/'classify-work'
        state['packageWorkRemoved'] = not os.path.lexists(work)
        left = sorted(p.name for p in scratch.iterdir()) if scratch.is_dir() and not scratch.is_symlink() else []
        state['classifyWorkLeft'] = left
        if scratch.is_dir() and not scratch.is_symlink() and not left:
            scratch.rmdir()
        state['classifyWorkRemoved'] = not os.path.lexists(scratch)
        state['retained'] = {name: (root_dir/'package'/name).is_file()
                             for name in (route.install, 'factory-package-receipt.json', 'factory-package.log')}
        state['uploaded'] = sorted(UPLOAD.values())
        sums = root_dir/'SHA256SUMS'
        state['installer'] = {'staged': sums.is_file() and not sums.is_symlink(),
                              'sha256sums': sums.read_text().strip() if sums.is_file() and not sums.is_symlink() else None,
                              'uploadOutcome': installer_upload or 'not-run',
                              'retentionDays': route.installer['retentionDays'], 'capBytes': route.installer['capBytes']}
        fd = os.open(root_dir/'cleanup.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(state, indent=2, sort_keys=True) + '\n')
        for relative, name in UPLOAD.items():
            source = root_dir/relative
            if source.is_file() and not source.is_symlink():
                (upload_dir/name).write_bytes(source.read_bytes())
    else:
        (upload_dir/'cleanup.json').write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
    return state
