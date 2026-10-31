"""FAM-HOST-READINESS-S002 (FIX001): the os-release device-readiness mode and scripts/device-readiness.py.

The workflow mode reuses the guard, controller, shared builder runner, global concurrency and cleanup; the inspection job
holds contents: read only and no build, sign, publish or alpha job can run in it; cleanup always waits for it and always
stops the builder. The probe runs here against a representative fixture tree (the pinned adevtool and platform_build
fixture files and the adevtool vendor skeletons), never against a fabricated host result. Every lunch-chain, vendorsetup
and cmds-for-envsetup script in the fixture is deliberately unsafe: it appends to and deletes files nested inside
existing vendor and output directories and leaves a sentinel. The metadata-only route must never run them (no process
is started at all), nested bytes must survive, and the receipt must stay PARTIAL with lunch and target unknown - even
when the chain bytes match the inspected revision. Stand-in scripts are never treated as proof of the real chain. Also
covered: lock conflict, invalid input, contract and pin refusal, symlink escape, missing generated vendor inputs, the
deadline, secret non-disclosure, scratch collision and foreign-file preservation, and cleanup after early and late
failure. Offline; no builder, network or cloud access."""
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
FIXTURES = ROOT/'os/tests/fixtures/adevtool-17df3b79'
CONTRACT = json.loads((ROOT/'os/devices/pixel10.json').read_text())

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

readiness = load('device_readiness', 'device-readiness.py')
pipeline = load('release_pipeline_readiness_test', 'release-pipeline.py')

def jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}

JOBS = jobs(WORKFLOW)
BUILD_JOBS = ('builder', 'app-builder', 'alpha-builder', 'alpha-successor', 'alpha-apk-diagnostic', 'alpha-verify-retained',
    'alpha-export', 'app-reuse')
READINESS_JOB = """    needs: controller
    if: inputs.mode == 'device-readiness'
    runs-on: [self-hosted, roseos-frankel-builder]
    timeout-minutes: 20
    permissions:
      contents: read
    steps:
"""

def wiring_failures(text):
    """What the readiness wiring must keep; used on the real workflow and on mutated copies."""
    failures, jobs_ = [], jobs(text)
    job = jobs_.get('device-readiness', '')
    if not job.startswith(READINESS_JOB):
        failures.append('readiness job header (needs, if, runner, 20 min, contents: read only) changed')
    for word in ('id-token', 'secrets.', 'vars.', 'actions-gcp-login', 'release-pipeline.py', 'publish', 'os-release-stage',
                 'host-app', 'emulator-alpha', 'alpha-', 'sign'):
        if word in job.replace('device-readiness-${{', '').replace('Sanitized', ''):
            failures.append(f'readiness job mentions {word}')
    cleanup = jobs_.get('cleanup', '')
    needs = re.search(r'    needs: \[([^\]]*)\]\n', cleanup)
    if not needs or 'device-readiness' not in [n.strip() for n in needs[1].split(',')]:
        failures.append('cleanup does not wait for the readiness job')
    if "    if: always() && needs.controller.result != 'skipped'\n" not in cleanup or 'release-pipeline.py stop-builder' not in cleanup:
        failures.append('cleanup does not always stop the builder')
    for name in BUILD_JOBS:
        conditions = re.findall(r"inputs\.mode == '([\w-]+)'", re.search(r'    if: (.*)\n', jobs_.get(name, '    if: none\n'))[1])
        if not conditions or 'device-readiness' in conditions:
            failures.append(f'{name} could run in device-readiness mode')
    if '    needs: [builder, signed-builder]\n' not in jobs_.get('publisher', '') or not re.search(r"inputs\.mode == 'app-only'", jobs_.get('app-publisher', '')):
        failures.append('a publisher is not bound to a build mode')
    if not re.search(r'\[\[ "\$MODE" != device-readiness \|\| "\$CANDIDATE_SHA" == "\$GITHUB_SHA" \]\]', text):
        failures.append('the guard does not bind the candidate to the workflow recipe')
    return failures

class WorkflowTest(unittest.TestCase):
    def test_the_mode_reuses_the_existing_trust_and_runs_nothing_else(self):
        self.assertEqual(wiring_failures(WORKFLOW), [])
        self.assertIn('        options: [latest-beta, os-release, device-readiness, app-only, emulator-alpha,', WORKFLOW)
        job = JOBS['device-readiness']
        self.assertIn('python3 scripts/device-readiness.py run --run-id "$GITHUB_RUN_ID" --run-attempt "$GITHUB_RUN_ATTEMPT" '
            '--recipe-sha "$GITHUB_SHA" --evidence-dir "$RUNNER_TEMP/device-readiness"', job)
        self.assertIn('          retention-days: 7\n', job)
        self.assertIn("      - name: Remove only this attempt's task root\n        if: always()\n", job)
        self.assertIn("      - name: Device-readiness contract check\n        if: inputs.mode == 'device-readiness'\n"
            '        run: python3 scripts/device-readiness.py check\n', JOBS['guard'])
        # The controller, runner label, group and lock are the existing ones; no new runner or workflow trust.
        self.assertIn('    needs: guard\n', JOBS['controller'])
        self.assertEqual(sorted(set(re.findall(r'runs-on: \[self-hosted, ([\w-]+)\]', WORKFLOW))), ['roseos-frankel-builder'])
        self.assertIn('  group: os-release-frankel\n', WORKFLOW)
        self.assertIn("    if: github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'\n", JOBS['guard'])
        self.assertEqual(readiness.check()['lock'], '/home/pixelbuild/frankel-build.lock')

    def test_mutations_of_the_guard_and_cleanup_wiring_are_caught(self):
        mutations = {
            'cleanup without the readiness job': WORKFLOW.replace('[controller, device-readiness, builder,', '[controller, builder,'),
            'cleanup only on success': WORKFLOW.replace("    if: always() && needs.controller.result != 'skipped'\n", '    if: success()\n'),
            'readiness job with an id token': WORKFLOW.replace(READINESS_JOB, READINESS_JOB.replace('      contents: read\n',
                '      contents: read\n      id-token: write\n')),
            'readiness job with more time': WORKFLOW.replace(READINESS_JOB, READINESS_JOB.replace('timeout-minutes: 20', 'timeout-minutes: 720')),
            'builder in readiness mode': WORKFLOW.replace("    if: inputs.mode == 'os-release' || inputs.mode == 'latest-beta'\n", "    if: inputs.mode == 'os-release' || inputs.mode == 'device-readiness'\n"),
            'unbound candidate': WORKFLOW.replace('[[ "$MODE" != device-readiness || "$CANDIDATE_SHA" == "$GITHUB_SHA" ]]', 'true'),
            'secret in the readiness job': WORKFLOW.replace("      - name: Inspect the retained tree", "      - env:\n          K: ${{ secrets.PIXEL_OTA_APP_PRIVATE_KEY }}\n        name: Inspect the retained tree"),
        }
        for name, text in mutations.items():
            self.assertNotEqual(text, WORKFLOW, name)
            self.assertTrue(wiring_failures(text), name)

    def guard(self):
        script = re.search(r'- name: Candidate is an exact commit on main\n(?:.*\n)*?        run: \|\n((?: {10}.*\n)+)', JOBS['guard'])[1]
        return '\n'.join(line for line in script.splitlines() if 'git merge-base' not in line)

    def test_the_guard_binds_readiness_to_the_recipe_and_frankel_and_admits_no_extras(self):
        base = dict(os.environ, MODE='device-readiness', ROSE_DEVICE='frankel', CANDIDATE_SHA='a' * 40, GITHUB_SHA='a' * 40,
            BUILD_NUMBER='2026102901', BUILD_TIMESTAMP='1793300000', APPS='[["dev.pixel.voice", 309]]', RESUME_STAGE='none',
            RESUME_UNSIGNED_SHA256='', RESUME_SOURCE_RUN='', RESUME_SIGNED_SHA256='', REUSE_BINDING='', PROMOTE='false',
            APP_PUBLISH='false', ALPHA_RECOVER='false')
        cases = [({}, True), ({'GITHUB_SHA': 'b' * 40}, False), ({'ROSE_DEVICE': 'blazer'}, False), ({'ROSE_DEVICE': 'rango'}, False),
            ({'PROMOTE': 'true'}, False), ({'APP_PUBLISH': 'true'}, False), ({'ALPHA_RECOVER': 'true'}, False),
            ({'RESUME_STAGE': 'sign', 'RESUME_UNSIGNED_SHA256': 'c' * 64}, False), ({'REUSE_BINDING': '{}'}, False),
            ({'MODE': 'os-release', 'GITHUB_SHA': 'b' * 40}, True)]
        for change, ok in cases:
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', self.guard()], cwd=ROOT, env={**base, **change},
                capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, ok, (change, result.stderr))

    def test_release_admission_stays_frankel_only(self):
        self.assertEqual((pipeline.SUPPORTED_DEVICES, pipeline.LATEST_DEVICES), (('frankel',), ('frankel',)))
        self.assertEqual(readiness.READINESS_DEVICES, ('frankel', 'blazer', 'rango'))
        self.assertEqual(sorted(p.name for p in (ROOT/'os/release-pipeline').glob('*.json')),
            ['alpha-runtime.json', 'cuttlefish-alpha.json', 'frankel.json'])

    def test_actual_ci_runs_the_probe_and_device_contract_tests(self):
        ci = (ROOT/'.github/workflows/release-verification.yml').read_text()
        for line in ('python3 scripts/tests/test_device_readiness.py', 'python3 scripts/tests/test_device_contract.py'):
            self.assertIn(f'          {line}\n', ci)
            self.assertIn(line + '\n', (ROOT/'scripts/test-local.sh').read_text())
        for path in ('scripts/device-readiness.py', 'scripts/tests/test_device_readiness.py', 'scripts/tests/test_device_contract.py',
                     'os/devices/**', 'os/tests/fixtures/adevtool-17df3b79/**'):
            self.assertEqual(ci.count(f"      - '{path}'\n"), 2, path)

# ---------------------------------------------------------------- fixture host

# Realistic hostile tree scripts: each would modify a nested vendor file, delete nested retained outputs and leave a
# sentinel if anything ever sourced or ran it.
UNSAFE_SCRIPT = """# {name}
echo injected >> "{source}/vendor/google_devices/frankel/proprietary/vendor/lib64/libexisting.so"
rm -f "{source}/out-rose-first-update-2026102701/target/product/frankel/vendor.img"
rm -rf "{source}/vendor/adevtool/dl/unpacked/frankel-CP3A.261005.005/vendor"
echo "{name}" >> "{sentinel}"
"""
UNSAFE = ('build/make/envsetup.sh', 'build/make/shell_utils.sh', 'build/soong/soong_ui.bash', 'build/soong/scripts/microfactory.bash',
    'device/google/laguna/vendorsetup.sh', 'vendor/google_devices/rango/cmds-for-envsetup.sh')

def snapshot(root):
    """Every path under root by lstat, with file bytes' digests and link targets: nested changes show up here."""
    state = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath)/name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                state[rel] = ('link', os.readlink(path))
            elif path.is_dir():
                state[rel] = ('dir',)
            else:
                state[rel] = ('file', hashlib.sha256(path.read_bytes()).hexdigest())
    return state

@contextlib.contextmanager
def no_processes():
    """Any process start (subprocess.run, Popen, os.system) fails the test and is recorded."""
    started, saved = [], (subprocess.Popen, os.system)
    class Refused(subprocess.Popen):
        def __init__(self, args, *a, **kw):
            started.append(args)
            raise AssertionError(f'process started: {args}')
    def system(command):
        started.append(command)
        raise AssertionError(f'process started: {command}')
    subprocess.Popen, os.system = Refused, system
    try:
        yield started
    finally:
        subprocess.Popen, os.system = saved

def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)

def git_init(path, message='pin'):
    path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
    for args in (['init', '-q'], ['commit', '-q', '--allow-empty', '-m', message]):
        subprocess.run(['git', '-C', str(path), *args], check=True, env=env, capture_output=True)
    return subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()

@contextlib.contextmanager
def watched():
    """Records the resolved path of every successful os.open, every directory listed with os.scandir (with how many
    entries the listing actually produced) and every content read through read_fd, so tests can prove what was never
    opened, enumerated or read - not only what the receipt shows."""
    log = {'opened': [], 'listed': [], 'read': []}
    real_open, real_scandir, real_read = os.open, os.scandir, readiness.read_fd
    def where(fd):
        return os.readlink(f'/proc/self/fd/{fd}')
    def open_(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        log['opened'].append(where(fd))
        return fd
    class Listing:
        def __init__(self, target='.'):
            self.inner = real_scandir(target)
            self.record = [where(target) if isinstance(target, int) else os.path.realpath(target), 0]
            log['listed'].append(self.record)
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            self.inner.close()
        def __iter__(self):
            for entry in self.inner:
                self.record[1] += 1
                yield entry
        def close(self):
            self.inner.close()
    def read(fd, size):
        log['read'].append(where(fd))
        return real_read(fd, size)
    os.open, os.scandir, readiness.read_fd = open_, Listing, read
    try:
        yield log
    finally:
        os.open, os.scandir, readiness.read_fd = real_open, real_scandir, real_read

def touched(log, prefix):
    prefix = os.path.realpath(prefix)
    return sorted({p for p in log['opened'] + log['read'] + [r[0] for r in log['listed']] if p == prefix or p.startswith(prefix + '/')})

class Fixture:
    """A representative retained tree: the pinned fixture files at their tree paths, the adevtool vendor skeletons as the
    generated vendor trees, existing nested vendor and output bytes, factory archives, unsafe tree scripts and a lock
    file, all under one temporary directory."""
    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.source = self.tmp/'grapheneos'
        self.home = self.tmp/'home'
        self.home.mkdir()
        self.sentinel = self.tmp/'scripts-ran'
        rows = json.loads((FIXTURES/'PROVENANCE.json').read_text())['files']
        for row in rows:
            fixture = FIXTURES/row['fixture']
            prefix = 'vendor/adevtool' if row['repository'].endswith('/adevtool') else 'build/make'
            if row['kind'] == 'full':
                copy(fixture, self.source/prefix/row['path'])
        # Reached through aosp_product.mk; not among the pinned fixture files, so a stand-in.
        (self.source/'build/make/target/product/handheld_product.mk').write_text('# handheld_product stand-in\n')
        for device in readiness.READINESS_DEVICES:
            skel = FIXTURES/'adevtool/vendor-skels/google_devices'/device
            generated = self.source/'vendor/google_devices'/device
            for name in ('AndroidProducts.mk', 'cmds-for-envsetup.sh', 'firmware/android-info.txt'):
                copy(skel/name, generated/name)
            (generated/f'{device}.mk').write_text(f'# generated {device}\n')
            (self.source/CONTRACT['devices'][device]['kernelDir']).mkdir(parents=True, exist_ok=True)
        (self.source/'vendor/state').mkdir(parents=True)
        (self.source/'vendor/state/frankel.json').write_text('{}\n')
        (self.source/'.repo').mkdir()
        shutil.copyfile(ROOT/'os/pinned-manifest.xml', self.source/'.repo/manifest.xml')
        dl = self.source/'vendor/adevtool/dl'
        dl.mkdir(parents=True)
        self.factory_bytes = b'factory archive stand-in\n'
        self.factory_sha = hashlib.sha256(self.factory_bytes).hexdigest()
        (dl/CONTRACT['devices']['frankel']['vendorInputs']['factoryImage']['file']).write_bytes(self.factory_bytes)
        (dl/CONTRACT['devices']['blazer']['vendorInputs']['factoryImage']['file']).write_bytes(b'other bytes\n')
        # Existing nested bytes the unsafe scripts target: a generated vendor blob, a retained output image, an unpack.
        nested = {'vendor/google_devices/frankel/proprietary/vendor/lib64/libexisting.so': b'\x7fELF existing vendor blob\n',
            'out-rose-first-update-2026102701/target/product/frankel/vendor.img': b'retained vendor image\n' * 64,
            'vendor/adevtool/dl/unpacked/frankel-CP3A.261005.005/vendor/build.prop': b'ro.product.vendor.device=frankel\n'}
        for rel, data in nested.items():
            (self.source/rel).parent.mkdir(parents=True, exist_ok=True)
            (self.source/rel).write_bytes(data)
        self.nested = nested
        for rel in UNSAFE:
            (self.source/rel).parent.mkdir(parents=True, exist_ok=True)
            (self.source/rel).write_text(UNSAFE_SCRIPT.format(name=rel, source=self.source, sentinel=self.sentinel))
            os.chmod(self.source/rel, 0o755)
        os.symlink('make/envsetup.sh', self.source/'build/envsetup.sh')
        self.chain = {rel: readiness.sha256_file(self.source/rel) for rel in readiness.LUNCH_CHAIN}
        self.lock = self.tmp/'frankel-build.lock'
        self.recipe = 'e' * 40
        self.heads = dict(readiness.EXPECTED_PINS)

    def host(self, **kw):
        host = readiness.Host(self.source, self.lock, 'out-rose-first-update-2026102701', 'immortal-os-builder', self.home)
        host.hostname = lambda: 'immortal-os-builder'
        host.foreign = lambda: []
        host.head = lambda: self.recipe
        heads = self.heads
        def runner(argv, **options):
            project = str(Path(argv[argv.index('-C') + 1]).relative_to(self.source))
            return subprocess.CompletedProcess(argv, 0, heads[project] + '\n', '')
        host.runner = runner
        host.system_root = self.tmp/'system'  # never the real /usr/bin of the test machine
        host.system_root.mkdir(exist_ok=True)
        host.step_summary = None
        for key, value in kw.items():
            setattr(host, key, value)
        return host

    def run(self, host=None, attempt=1, evidence=None):
        evidence = evidence or self.tmp/f'evidence-{attempt}-{time.monotonic_ns()}'
        receipt = readiness.run(host or self.host(), '4081', str(attempt), self.recipe, evidence)
        return receipt, evidence

    def receipt(self, evidence):
        return json.loads((evidence/'device-readiness.json').read_text())

@contextlib.contextmanager
def fixture(chain_matches=False, **limits):
    """chain_matches makes the unsafe stand-in chain carry the inspected digests, to show matching bytes run nothing."""
    saved_chain, saved_limits = dict(readiness.LUNCH_CHAIN), dict(readiness.LIMITS)
    with tempfile.TemporaryDirectory() as tmp:
        f = Fixture(tmp)
        if chain_matches:
            readiness.LUNCH_CHAIN.clear()
            readiness.LUNCH_CHAIN.update(f.chain)
        readiness.LIMITS.update(limits)
        try:
            yield f
        finally:
            readiness.LUNCH_CHAIN.clear()
            readiness.LUNCH_CHAIN.update(saved_chain)
            readiness.LIMITS.clear()
            readiness.LIMITS.update(saved_limits)

class ProbeTest(unittest.TestCase):
    def test_a_representative_tree_is_measured_and_cleaned_up(self):
        with fixture() as f:
            before = snapshot(f.source)
            with no_processes() as started:
                receipt, evidence = f.run()
            self.assertEqual(started, [])
            self.assertEqual(snapshot(f.source), before)
            self.assertFalse(f.sentinel.exists())
            self.assertEqual((receipt['status'], receipt['partialReasons']), ('partial', [readiness.CONFINEMENT_UNAVAILABLE]))
            on_disk = f.receipt(evidence)
            self.assertEqual(on_disk, json.loads(json.dumps(receipt)))
            self.assertEqual((evidence/'SHA256SUMS').read_text(),
                f'{readiness.sha256_file(evidence/"device-readiness.json")}  device-readiness.json\n'
                f'{readiness.sha256_file(evidence/"signing-tools.json")}  signing-tools.json\n')
            signing = json.loads((evidence/'signing-tools.json').read_text())
            self.assertEqual(signing['status'], 'observed')
            self.assertEqual({t: r['status'] for t, r in signing['tools'].items()},
                {'make_key': 'missing', 'decrypt-keys': 'missing', 'keytool': 'missing', 'openssl': 'missing',
                 'apksigner.jar': 'missing', 'sdk-aapt': 'missing'})
            self.assertEqual((receipt['recipeSha'], receipt['attempt'], receipt['lock']['acquired'], receipt['executedMake']),
                (f.recipe, '4081-1', True, False))
            self.assertEqual(receipt['contract']['sha256'], readiness.sha256_file(ROOT/'os/devices/pixel10.json'))
            fs = receipt['filesystem']
            st = os.statvfs(f.source)
            self.assertEqual(fs['totalBytes'], st.f_blocks * st.f_frsize)
            self.assertTrue(0 < fs['availableBytes'] <= fs['totalBytes'] and fs['type'])
            self.assertEqual({p: (r['observed'], r['matches']) for p, r in receipt['pins'].items()},
                {p: (rev, True) for p, rev in readiness.EXPECTED_PINS.items()})
            self.assertEqual((receipt['manifest']['comparison'], receipt['manifest']['pinnedSha256']),
                ('match', readiness.sha256_file(ROOT/'os/pinned-manifest.xml')))
            # The pinned public fixture files are recognised by git blob; presence of generated files is not completeness.
            frankel = {r['path']: r for r in receipt['products']['frankel']['files']}
            self.assertTrue(frankel['vendor/adevtool/config/device/frankel.yml']['matchesPinnedFixture'])
            self.assertTrue(frankel['build/make/target/product/generic_system.mk']['matchesPinnedFixture'])
            self.assertTrue(all(r['status'] == 'present' for r in frankel.values()))
            self.assertEqual(receipt['products']['rango']['kernelDir'], 'present')
            generated = receipt['generatedVendor']['frankel']
            self.assertEqual(generated['status'], 'present-unverified')
            self.assertEqual((receipt['vendorState']['status'], receipt['vendorState']['devices']['frankel']),
                ('complete', {'names': ['frankel.json'], 'truncated': False}))
            self.assertTrue({r['path']: r for r in generated['files']}['vendor/google_devices/frankel/AndroidProducts.mk']['matchesPinnedFixture'])
            # Factory presence and checksum are separate facts.
            factory = receipt['factory']
            self.assertEqual((factory['frankel']['status'], factory['frankel']['checksum'], factory['frankel']['unpacked']),
                ('present', 'mismatch', 'present'))
            self.assertEqual((factory['blazer']['status'], factory['blazer']['checksum']), ('present', 'mismatch'))
            self.assertEqual((factory['rango']['status'], factory['rango']['checksum'], factory['rango']['sizeBytes']), ('missing', 'notValidated', None))
            # Lunch never ran: results are unknown, not passed; the stand-in chain is not the inspected revision.
            lunch = receipt['lunch']
            self.assertEqual((lunch['executed'], lunch['status'], lunch['reason']), (False, 'NOT_EXECUTED', readiness.CONFINEMENT_UNAVAILABLE))
            for device in readiness.READINESS_DEVICES:
                self.assertEqual(lunch['devices'][device], {'lunch': 'unknown', 'target': 'unknown', 'combo': f'{device}-cur-user',
                    'missingInputs': []})
            self.assertEqual({r['matchesInspectedRevision'] for r in lunch['chain']['files'].values()}, {False})
            self.assertTrue(lunch['chain']['envsetupEntryResolvesToBuildMake'])
            # The unsafe rango cmds-for-envsetup.sh is reported as differing from the pinned skeleton, not as fine.
            rango = {r['path']: r for r in receipt['generatedVendor']['rango']['files']}
            self.assertFalse(rango['vendor/google_devices/rango/cmds-for-envsetup.sh']['matchesPinnedFixture'])
            self.assertTrue({r['path']: r for r in receipt['generatedVendor']['frankel']['files']}[
                'vendor/google_devices/frankel/cmds-for-envsetup.sh']['matchesPinnedFixture'])
            text = json.dumps(receipt)
            for claim in ('resolved', 'sourceTopLevelUnchanged', 'scratch'):
                self.assertNotIn(claim, text, claim)
            self.assertNotIn('complete', [receipt['status'], lunch['status']])
            usage = receipt['usage']
            self.assertEqual(usage['generatedVendor']['status'], 'complete')
            self.assertGreater(usage['generatedVendor']['entries'], 12)
            self.assertEqual((usage['retainedOutput']['status'], usage['defaultOutput']['status']), ('complete', 'absent'))
            self.assertGreater(usage['retainedOutput']['allocatedBytes'], 0)
            result = readiness.cleanup(f.host(), '4081', '1')
            self.assertEqual(result['removed'], True)
            self.assertFalse((f.home/'rose-actions/device-readiness-4081-1').exists())
            self.assertTrue((f.home/'rose-actions').is_dir())

    def test_a_validated_factory_checksum_needs_the_contract_digest(self):
        with fixture() as f:
            contract = json.loads(readiness.CONTRACT.read_text())
            contract['devices']['frankel']['vendorInputs']['factoryImage']['sha256'] = f.factory_sha
            targets = readiness.contract_targets(contract)
            original = readiness.contract_targets
            readiness.contract_targets = lambda _: targets
            try:
                receipt, _ = f.run()
            finally:
                readiness.contract_targets = original
            self.assertEqual(receipt['factory']['frankel']['checksum'], 'validated')

    def test_a_held_lock_refuses_before_any_task_root_or_inspection(self):
        with fixture() as f, open(f.lock, 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            evidence = f.tmp/'evidence'
            with self.assertRaisesRegex(readiness.CheckFailed, 'lock is held'):
                f.run(evidence=evidence)
            receipt = f.receipt(evidence)
            self.assertEqual((receipt['status'], receipt['lock']['acquired']), ('refused', False))
            self.assertNotIn('filesystem', receipt)
            self.assertFalse((f.home/'rose-actions').exists())
            self.assertEqual(readiness.cleanup(f.host(), '4081', '1')['reason'], 'absent')

    def test_wrong_recipe_instance_foreign_work_or_absent_tree_refuse(self):
        with fixture() as f:
            cases = [({'head': lambda: 'f' * 40}, readiness.CheckFailed, 'recipe'),
                ({'hostname': lambda: 'another-builder'}, readiness.CheckFailed, 'not instance'),
                ({'foreign': lambda: ['launch_cvd', 'ninja']}, readiness.CheckFailed, 'foreign build or guest work'),
                ({'source_root': f.tmp/'absent'}, readiness.SetupMissing, 'not present')]
            for index, (change, error, message) in enumerate(cases):
                evidence = f.tmp/f'evidence-{index}'
                with self.assertRaisesRegex(error, message):
                    f.run(f.host(**change), evidence=evidence)
                self.assertEqual(f.receipt(evidence)['status'], 'refused')
                self.assertFalse((f.home/'rose-actions/device-readiness-4081-1').exists(), message)

    def test_invalid_inputs_are_refused_before_anything_is_touched(self):
        with fixture() as f:
            host = f.host()
            for run_id, attempt, sha in (('x', '1', f.recipe), ('4081', '0', f.recipe), ('4081;id', '1', f.recipe),
                                         ('4081', '1', 'abc'), ('4081', '1', '')):
                with self.assertRaises(readiness.InvalidInput):
                    readiness.run(host, run_id, attempt, sha, f.tmp/'never')
            self.assertFalse((f.tmp/'never').exists())
            with self.assertRaises(readiness.InvalidInput):
                readiness.cleanup(host, '../4081', '1')

    def test_the_contract_and_pins_are_checked(self):
        self.assertEqual(sorted(readiness.contract_targets(CONTRACT)), ['blazer', 'frankel', 'rango'])
        def changed(edit):
            contract = json.loads(json.dumps(CONTRACT))
            edit(contract)
            return contract
        bad = {
            'adevtool pin': lambda c: c['source']['projects'].update({'vendor/adevtool': '0' * 40}),
            'state pin': lambda c: c['source']['projects'].pop('vendor/state'),
            'extra device': lambda c: c['devices'].update({'mustang': c['devices']['frankel']}),
            'lunch combo': lambda c: c['devices']['blazer']['lunch'].update({'expected': 'blazer-cur-userdebug'}),
            'path escape': lambda c: c['shared'].update({'include': '../../../../etc/shadow'}),
            'unsafe config': lambda c: c['devices']['rango'].update({'adevtoolConfig': 'config/device/$(id).yml'}),
            'factory digest': lambda c: c['devices']['frankel']['vendorInputs']['factoryImage'].update({'sha256': 'unknown'}),
        }
        for name, edit in bad.items():
            with self.assertRaises(readiness.CheckFailed, msg=name):
                readiness.contract_targets(changed(edit))

    def test_unsafe_tree_scripts_never_run_even_when_the_chain_bytes_match(self):
        with fixture(chain_matches=True) as f:
            before = snapshot(f.source)
            with no_processes() as started:
                receipt, _ = f.run()
            self.assertEqual(started, [], 'no envsetup, lunch, vendorsetup or cmds-for-envsetup process')
            self.assertFalse(f.sentinel.exists())
            self.assertEqual(snapshot(f.source), before)
            for rel, data in f.nested.items():
                self.assertEqual((f.source/rel).read_bytes(), data, rel)
            # Matching bytes are recorded as metadata and still authorize nothing.
            self.assertEqual({r['matchesInspectedRevision'] for r in receipt['lunch']['chain']['files'].values()}, {True})
            self.assertEqual((receipt['status'], receipt['lunch']['status']), ('partial', 'NOT_EXECUTED'))
            self.assertEqual({d['lunch'] for d in receipt['lunch']['devices'].values()}, {'unknown'})
            self.assertEqual(readiness.cleanup(f.host(), '4081', '1')['removed'], True)
            self.assertEqual(snapshot(f.source), before)

    def test_what_the_scripts_would_have_done_is_invisible_to_root_names(self):
        """The FIX001 finding: running one unsafe script changes nested bytes while every top-level name stays, so names
        can never stand for integrity - and the probe makes no such claim."""
        with fixture() as f:
            names, before = sorted(os.listdir(f.source)), snapshot(f.source)
            subprocess.run(['bash', '--noprofile', '--norc', str(f.source/'device/google/laguna/vendorsetup.sh')], check=True)
            self.assertEqual(sorted(os.listdir(f.source)), names)
            after = snapshot(f.source)
            changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
            self.assertIn('vendor/google_devices/frankel/proprietary/vendor/lib64/libexisting.so', changed)
            self.assertIn('out-rose-first-update-2026102701/target/product/frankel/vendor.img', changed)
            self.assertIn('vendor/adevtool/dl/unpacked/frankel-CP3A.261005.005/vendor/build.prop', changed)
            self.assertFalse(hasattr(readiness, 'top_level') or hasattr(readiness, 'run_lunch'))
            self.assertNotIn('sourceTopLevelUnchanged', (SCRIPTS/'device-readiness.py').read_text())

    def test_pins_not_matching_are_reported_and_nothing_runs(self):
        with fixture() as f:
            f.heads['vendor/state'] = '0' * 40
            with no_processes() as started:
                receipt, _ = f.run()
            self.assertEqual(started, [])
            self.assertEqual((receipt['pins']['vendor/state']['matches'], receipt['pins']['vendor/adevtool']['matches']), (False, True))
            self.assertEqual((receipt['status'], receipt['lunch']['status']), ('partial', 'NOT_EXECUTED'))

    def test_real_git_heads_are_read_and_compared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = git_init(root/'vendor/adevtool')
            record = readiness.git_head(root, 'vendor/adevtool', subprocess.run, 30)
            self.assertEqual(record, {'status': 'read', 'observed': head})
            self.assertNotEqual(head, readiness.EXPECTED_PINS['vendor/adevtool'])
            self.assertEqual(readiness.git_head(root, 'vendor/state', subprocess.run, 30), {'status': 'missing'})
            (root/'vendor/state').mkdir()
            self.assertEqual(readiness.git_head(root, 'vendor/state', subprocess.run, 30)['status'], 'unreadable')

    def test_links_out_of_the_tree_are_never_followed_or_hashed(self):
        with fixture() as f:
            outside = f.tmp/'outside'
            outside.mkdir()
            (outside/'secret.pem').write_text('-----BEGIN PRIVATE KEY-----\n')
            (outside/'big').write_bytes(b'x' * 100000)
            shutil.rmtree(f.source/'vendor/google_devices/blazer')
            os.symlink(outside, f.source/'vendor/google_devices/blazer')
            target = f.source/'vendor/adevtool/config/device/rango.yml'
            target.unlink()
            os.symlink(outside/'secret.pem', target)
            os.symlink(outside, f.source/'vendor/google_devices/frankel/escape')
            receipt, evidence = f.run()
            self.assertEqual(receipt['generatedVendor']['blazer']['status'], 'symlink-refused')
            self.assertIn('vendor/google_devices/blazer symlink-refused', receipt['lunch']['devices']['blazer']['missingInputs'])
            rango = {r['path']: r for r in receipt['products']['rango']['files']}['vendor/adevtool/config/device/rango.yml']
            self.assertEqual(rango, {'path': 'vendor/adevtool/config/device/rango.yml', 'status': 'symlink-refused'})
            self.assertIn('vendor/adevtool/config/device/rango.yml symlink-refused', receipt['lunch']['devices']['rango']['missingInputs'])
            self.assertEqual(receipt['lunch']['devices']['frankel']['missingInputs'], [])
            self.assertLess(receipt['usage']['generatedVendor']['allocatedBytes'], 100000)
            self.assertNotIn('PRIVATE KEY', (evidence/'device-readiness.json').read_text())

    def test_missing_generated_vendor_inputs_are_reported_for_that_device_only(self):
        with fixture() as f:
            shutil.rmtree(f.source/'vendor/google_devices/rango')
            (f.source/'vendor/google_devices/blazer/firmware/android-info.txt').unlink()
            receipt, _ = f.run()
            self.assertEqual(receipt['generatedVendor']['rango']['status'], 'missing')
            self.assertEqual(receipt['generatedVendor']['blazer']['status'], 'incomplete')
            self.assertEqual(receipt['lunch']['devices']['rango']['missingInputs'], ['vendor/google_devices/rango missing'])
            self.assertEqual(receipt['lunch']['devices']['blazer']['missingInputs'], ['vendor/google_devices/blazer incomplete'])
            self.assertEqual(receipt['lunch']['devices']['frankel']['missingInputs'], [])
            self.assertEqual({d['lunch'] for d in receipt['lunch']['devices'].values()}, {'unknown'})

    def test_the_deadline_yields_skipped_and_unavailable_never_zero(self):
        with fixture() as f:
            ticks = iter(range(0, 10 ** 6, 300))
            receipt, _ = f.run(f.host(clock=lambda: next(ticks)))
            self.assertEqual(receipt['status'], 'partial')
            self.assertIn('inspection deadline reached', receipt['partialReasons'])
            for record in receipt['usage'].values():
                self.assertIn(record['status'], ('incomplete', 'absent'))
                self.assertNotIn('allocatedBytes', record)
            self.assertEqual(receipt['factory']['frankel']['checksum'], 'unavailable')

    def test_no_key_environment_or_secret_reaches_the_receipt(self):
        with fixture() as f:
            secret = 'sk-live-very-secret-value'
            saved = dict(os.environ)
            os.environ.update(PIXEL_OTA_TOKEN=secret, GCP_SIGNER_SERVICE_ACCOUNT=secret, ACTIONS_ID_TOKEN_REQUEST_TOKEN=secret)
            (f.source/'vendor/state/frankel-token=abc.json').write_text('{}\n')
            try:
                receipt, evidence = f.run()
            finally:
                os.environ.clear()
                os.environ.update(saved)
            text = (evidence/'device-readiness.json').read_text()
            self.assertNotIn(secret, text)
            self.assertNotIn('token=abc', text)
            self.assertIn('(withheld)', receipt['vendorState']['devices']['frankel']['names'])
            refused = f.tmp/'evidence-refused'
            with self.assertRaises(readiness.CheckFailed):
                f.run(f.host(hostname=lambda: 'token-' + secret), evidence=refused)
            self.assertNotIn(secret, (refused/'device-readiness.json').read_text())
            self.assertEqual(readiness.sanitize('$(id)'), '(withheld)')
            self.assertEqual(readiness.sanitize('api_key=abc'), '(withheld)')

    def test_secret_named_directories_are_neither_entered_nor_read(self):
        with fixture() as f:
            baseline = readiness.usage(f.source, 'vendor/google_devices', readiness.Deadline(60))
            keys = f.source/'vendor/google_devices/frankel/keys'
            keys.mkdir()
            (keys/'releasekey.pk8').write_bytes(b'k' * 50000)
            receipt, _ = f.run()
            self.assertEqual(receipt['usage']['generatedVendor']['skippedDirectories'], 1)
            measured = receipt['usage']['generatedVendor']
            self.assertEqual(measured['entries'], baseline['entries'] + 1, 'only the directory entry itself')
            self.assertLessEqual(measured['allocatedBytes'], baseline['allocatedBytes'] + 8192)
            self.assertEqual(readiness.file_record(f.source, 'vendor/google_devices/frankel/keys/releasekey.pk8')['status'],
                'secret-path-refused')

    def test_an_existing_task_root_is_refused_and_preserved_and_cleanup_spares_foreign_roots(self):
        with fixture() as f:
            root = f.home/'rose-actions/device-readiness-4081-1'
            root.mkdir(parents=True)
            (root/'someone-elses-file').write_text('keep me\n')
            evidence = f.tmp/'evidence'
            with self.assertRaisesRegex(readiness.CheckFailed, 'already exists'):
                f.run(evidence=evidence)
            self.assertEqual(f.receipt(evidence)['status'], 'refused')
            for marker in (None, '4081-2\n'):
                if marker:
                    (root/readiness.MARKER).write_text(marker)
                with self.assertRaisesRegex(readiness.CheckFailed, 'not owned'):
                    readiness.cleanup(f.host(), '4081', '1')
                self.assertEqual((root/'someone-elses-file').read_text(), 'keep me\n')
            # A new attempt of the same run has its own root; the earlier one stays.
            receipt, _ = f.run(attempt=2)
            self.assertEqual(receipt['status'], 'partial')
            readiness.cleanup(f.host(), '4081', '2')
            self.assertTrue((root/'someone-elses-file').is_file())
            link = f.home/'rose-actions/device-readiness-4081-3'
            os.symlink(root, link)
            with self.assertRaisesRegex(readiness.CheckFailed, 'not owned'):
                readiness.cleanup(f.host(), '4081', '3')
            self.assertTrue(root.is_dir())

    def test_a_late_failure_still_writes_its_receipt_and_cleanup_removes_only_its_scratch(self):
        with fixture() as f:
            original = readiness.usage
            def broken(*args, **kw):
                raise readiness.CheckFailed('disk went away')
            readiness.usage = broken
            evidence = f.tmp/'evidence'
            try:
                with self.assertRaisesRegex(readiness.CheckFailed, 'disk went away'):
                    f.run(evidence=evidence)
            finally:
                readiness.usage = original
            receipt = f.receipt(evidence)
            self.assertEqual((receipt['status'], receipt['refusal'], receipt['lock']['acquired']),
                ('refused', 'CheckFailed: disk went away', True))
            self.assertEqual(receipt['lunch']['devices']['frankel']['lunch'], 'unknown')
            before = sorted(str(p.relative_to(f.source)) for p in f.source.rglob('*'))
            self.assertTrue(readiness.cleanup(f.host(), '4081', '1')['removed'])
            self.assertEqual(sorted(str(p.relative_to(f.source)) for p in f.source.rglob('*')), before, 'the tree is untouched')
            # The lock was released: a following attempt can take it.
            with open(f.lock, 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

class BoundaryTest(unittest.TestCase):
    """FIX002: symlinks anywhere on a read or listed path (final or ancestor, into the secret subtree, elsewhere in the
    root or outside it) are refused before anything behind them is opened; directory work is bounded per entry. Only
    synthetic, nonsecret sentinels stand in for secret targets."""
    SENTINEL = b'SYNTHETIC-SENTINEL-NOT-A-KEY\n'

    def secret_subtree(self, f):
        keys = f.source/'keys'
        keys.mkdir()
        for name in ('releasekey.pk8', 'AndroidProducts.mk', 'frankel.json'):
            (keys/name).write_bytes(self.SENTINEL)
        return keys

    def test_direct_and_ancestor_links_into_the_secret_subtree_are_refused_before_any_access(self):
        with fixture() as f:
            keys = self.secret_subtree(f)
            rango = f.source/'vendor/adevtool/config/device/rango.yml'
            rango.unlink()
            os.symlink('../../../../keys/releasekey.pk8', rango)  # direct, in root
            shutil.rmtree(f.source/'vendor/google_devices/blazer')
            os.symlink('../../keys', f.source/'vendor/google_devices/blazer')  # ancestor of every blazer generated file
            with watched() as log:
                receipt, evidence = f.run()
                direct = readiness.file_record(f.source, 'keys/releasekey.pk8')
            self.assertEqual(touched(log, keys), [], 'nothing under keys/ was opened, listed or read')
            self.assertEqual(direct, {'path': 'keys/releasekey.pk8', 'status': 'secret-path-refused'})
            rango_record = {r['path']: r for r in receipt['products']['rango']['files']}['vendor/adevtool/config/device/rango.yml']
            self.assertEqual(rango_record, {'path': 'vendor/adevtool/config/device/rango.yml', 'status': 'symlink-refused'})
            blazer = receipt['generatedVendor']['blazer']
            self.assertEqual(blazer['status'], 'symlink-refused')
            self.assertEqual({r['status'] for r in blazer['files']}, {'symlink-refused'})
            self.assertNotIn(self.SENTINEL.decode().strip(), (evidence/'device-readiness.json').read_text())
            self.assertNotIn(hashlib.sha256(self.SENTINEL).hexdigest(), (evidence/'device-readiness.json').read_text())

    def test_ordinary_and_outside_root_links_are_refused_and_regular_files_are_read(self):
        with fixture() as f:
            outside = f.tmp/'outside'
            (outside/'common').mkdir(parents=True)
            (outside/'common/gen10pixel.yml').write_bytes(self.SENTINEL)
            frankel = f.source/'vendor/adevtool/config/device/frankel.yml'
            frankel.unlink()
            os.symlink('../../../state/frankel.json', frankel)  # an ordinary in-root file
            common = f.source/'vendor/adevtool/config/device/common'
            shutil.rmtree(common)
            os.symlink(outside/'common', common)  # an ancestor leaving the root
            with watched() as log:
                receipt, _ = f.run()
            self.assertEqual(touched(log, outside), [])
            self.assertNotIn(os.path.realpath(f.source/'vendor/state/frankel.json'), log['read'])
            records = {r['path']: r for r in receipt['products']['frankel']['files']}
            self.assertEqual(records['vendor/adevtool/config/device/frankel.yml']['status'], 'symlink-refused')
            self.assertEqual(records['vendor/adevtool/config/device/common/gen10pixel.yml']['status'], 'symlink-refused')
            allowed = records['vendor/adevtool/config/device/pixel-gen10.yml']
            data = (FIXTURES/'adevtool/config/device/pixel-gen10.yml').read_bytes()
            self.assertEqual((allowed['status'], allowed['sha256'], allowed['matchesPinnedFixture']),
                ('present', hashlib.sha256(data).hexdigest(), True))
            self.assertIn(os.path.realpath(f.source/'vendor/adevtool/config/device/pixel-gen10.yml'), log['read'])

    def test_intermediate_directory_links_are_neither_listed_nor_entered(self):
        with fixture() as f:
            keys = self.secret_subtree(f)
            outside = f.tmp/'outside-adevtool'
            shutil.move(str(f.source/'vendor/adevtool'), outside)
            os.symlink(outside, f.source/'vendor/adevtool')  # vendor/adevtool escapes the root
            shutil.rmtree(f.source/'vendor/state')
            os.symlink('../keys', f.source/'vendor/state')  # vendor/state enters the excluded subtree
            os.symlink('../../../../keys', f.source/'vendor/google_devices/frankel/proprietary/keys-link')  # nested entry
            with watched() as log:
                receipt, _ = f.run()
            self.assertEqual(touched(log, keys), [])
            self.assertEqual(touched(log, outside), [], 'nothing behind the vendor/adevtool link was opened or listed')
            self.assertEqual(receipt['usage']['adevtoolDownloads'], {'path': 'vendor/adevtool/dl', 'status': 'refused',
                'reason': 'symlink-refused'})
            self.assertEqual(receipt['vendorState'], {'status': 'refused', 'reason': 'symlink-refused'})
            self.assertEqual(receipt['pins']['vendor/adevtool']['status'], 'symlink-refused')
            self.assertEqual(receipt['factory']['frankel']['status'], 'symlink-refused')
            self.assertEqual(receipt['usage']['generatedVendor']['status'], 'complete', 'the nested link is counted, not followed')

    def wide(self, f, count):
        wide = f.source/'out'
        wide.mkdir()
        for index in range(count):
            (wide/f'part-{index:04d}.o').write_bytes(b'x')
        return wide

    def test_a_wide_directory_stops_at_the_entry_cap_inside_one_listing(self):
        with fixture(usageEntries=250) as f:
            wide = self.wide(f, 600)
            with watched() as log:
                result = readiness.usage(f.source, 'out', readiness.Deadline(600))
            self.assertEqual(result, {'path': 'out', 'status': 'incomplete', 'reason': 'entry cap (250)', 'entriesExamined': 250})
            produced = [n for p, n in log['listed'] if p == os.path.realpath(wide)]
            self.assertEqual(len(produced), 1)
            self.assertLessEqual(produced[0], 251, 'the listing stopped at the cap, not after the whole directory')
            readiness.LIMITS['usageEntries'] = 600
            self.assertEqual(readiness.usage(f.source, 'out', readiness.Deadline(600))['status'], 'complete', 'exactly at the cap')
            readiness.LIMITS['usageEntries'] = 599
            self.assertEqual(readiness.usage(f.source, 'out', readiness.Deadline(600))['reason'], 'entry cap (599)')

    def test_a_wide_directory_stops_at_the_time_cap_inside_one_listing(self):
        with fixture(usageSeconds=100) as f:
            wide = self.wide(f, 600)
            ticks = iter(range(10 ** 6))
            clock = lambda: next(ticks)  # one tick per check: deterministic elapsed time within one directory
            with watched() as log:
                result = readiness.usage(f.source, 'out', readiness.Deadline(10 ** 6, clock), clock)
            self.assertEqual((result['status'], result['reason']), ('incomplete', 'time cap'))
            self.assertNotIn('allocatedBytes', result)
            self.assertLess(result['entriesExamined'], 110)
            produced = [n for p, n in log['listed'] if p == os.path.realpath(wide)][0]
            self.assertLess(produced, 112)
            within = readiness.usage(f.source, 'out', readiness.Deadline(10 ** 6))
            self.assertEqual((within['status'], within['entries']), ('complete', 600))

    def test_an_oversized_vendor_state_listing_is_bounded_and_says_so(self):
        with fixture(listingEntries=300) as f:
            state = f.source/'vendor/state'
            for index in range(400):
                (state/f'frankel-{index:04d}.json').write_text('{}\n')
                (state/f'other-{index:04d}.json').write_text('{}\n')
            with watched() as log:
                result = readiness.state_entries(f.source, ['frankel', 'blazer'], readiness.Deadline(600))
            self.assertEqual((result['status'], result['reason'], result['scanned']), ('incomplete', 'entry cap (300)', 300))
            self.assertLessEqual([n for p, n in log['listed'] if p == os.path.realpath(state)][0], 301)
            self.assertLessEqual(len(result['devices']['frankel']['names']), 50)
            self.assertIn('not its first names', result['note'])
            readiness.LIMITS['listingEntries'] = 10_000
            full = readiness.state_entries(f.source, ['frankel', 'blazer'], readiness.Deadline(600))
            self.assertEqual((full['status'], full['scanned']), ('complete', 801))
            self.assertEqual((len(full['devices']['frankel']['names']), full['devices']['frankel']['truncated']), (50, True))
            self.assertEqual(full['devices']['frankel']['names'], sorted(full['devices']['frankel']['names']))
            self.assertEqual(full['devices']['blazer'], {'names': [], 'truncated': False})
            self.assertIn('not its first names', full['note'])
            ticks = iter(range(10 ** 6))
            clock = lambda: next(ticks)
            readiness.LIMITS['listingSeconds'] = 40
            timed = readiness.state_entries(f.source, ['frankel'], readiness.Deadline(10 ** 6, clock), clock)
            self.assertEqual((timed['status'], timed['reason']), ('incomplete', 'time cap'))
            self.assertLess(timed['scanned'], 45)

class DeadlineTest(unittest.TestCase):
    """FIX003: the time budget is checked before each queued directory, after each listing ends (an empty one included)
    and before reporting complete. A synthetic clock advances only when a listing is exhausted, by a per-directory cost,
    so the elapsed time is deterministic; no real sleep. These are cooperative checks, not syscall timeouts."""
    @contextlib.contextmanager
    def costed(self, costs, close_costs=None):
        """os.scandir whose listings charge costs[path] seconds when exhausted, and os.close charging close_costs[path]
        when that directory's fd is closed (time passing after a listing's own checks); records each listing and every
        directory open_under opens."""
        state = {'now': 0, 'listed': [], 'opened': []}
        real_scandir, real_open_under, real_close = os.scandir, readiness.open_under, os.close
        def close(fd):
            if close_costs:
                try:
                    state['now'] += close_costs.get(os.readlink(f'/proc/self/fd/{fd}'), 0)
                except OSError:
                    pass
            real_close(fd)
        class Listing:
            def __init__(self, target):
                self.inner = real_scandir(target)
                self.path = os.readlink(f'/proc/self/fd/{target}')
                state['listed'].append(self.path)
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                self.inner.close()
            def __iter__(self):
                yield from self.inner
                state['now'] += costs.get(self.path, 0)
        def open_under(root, rel, directory=False):
            if directory:
                state['opened'].append(rel)
            return real_open_under(root, rel, directory)
        os.scandir, readiness.open_under, os.close = Listing, open_under, close
        try:
            yield state, (lambda: state['now'])
        finally:
            os.scandir, readiness.open_under, os.close = real_scandir, real_open_under, real_close

    def tree(self, f, names, files=()):
        (f.source/'out').mkdir(exist_ok=True)
        for name in names:
            (f.source/'out'/name).mkdir(parents=True)
        for name in files:
            (f.source/'out'/name).write_bytes(b'x')
        return {str(f.source/'out'/n): n for n in names}

    def test_three_empty_directories_at_20_seconds_each_exceed_the_45_second_budget(self):
        with fixture(usageSeconds=45) as f:
            paths = self.tree(f, ['a', 'b', 'c'])
            with self.costed({p: 20 for p in paths}) as (state, clock):
                result = readiness.usage(f.source, 'out', readiness.Deadline(10 ** 6, clock), clock)
            self.assertEqual(result, {'path': 'out', 'status': 'incomplete', 'reason': 'time cap', 'entriesExamined': 3})
            self.assertEqual(state['now'], 60, 'the over-budget observation is reported, not labelled complete')

    def test_no_queued_directory_is_opened_or_listed_after_expiry(self):
        with fixture(usageSeconds=45) as f:
            paths = self.tree(f, ['a', 'b', 'c', 'd', 'e'])
            with self.costed({p: 20 for p in paths}) as (state, clock):
                result = readiness.usage(f.source, 'out', readiness.Deadline(10 ** 6, clock), clock)
            self.assertEqual((result['status'], result['reason']), ('incomplete', 'time cap'))
            subdirs = [p for p in state['opened'] if p != 'out']
            self.assertEqual(len(subdirs), 3, 'two directories within budget, the third crossing it, then nothing')
            self.assertEqual(len([p for p in state['listed'] if p in paths]), 3)

    def test_an_empty_listing_that_ends_after_the_deadline_is_a_time_cap(self):
        with fixture(usageSeconds=45, listingSeconds=10) as f:
            (f.source/'out').mkdir()
            with self.costed({str(f.source/'out'): 50}) as (state, clock):
                result = readiness.usage(f.source, 'out', readiness.Deadline(10 ** 6, clock), clock)
            self.assertEqual(result, {'path': 'out', 'status': 'incomplete', 'reason': 'time cap', 'entriesExamined': 0})
            for name in os.listdir(f.source/'vendor/state'):
                (f.source/'vendor/state'/name).unlink()
            with self.costed({str(f.source/'vendor/state'): 50}) as (state, clock):
                listing = readiness.state_entries(f.source, ['frankel'], readiness.Deadline(10 ** 6, clock), clock)
            self.assertEqual((listing['status'], listing['reason'], listing['scanned']), ('incomplete', 'time cap', 0))

    def test_a_listing_whose_exhaustion_passes_the_deadline_is_a_time_cap(self):
        with fixture(usageSeconds=45) as f:
            self.tree(f, [], files=['one.o', 'two.o'])
            with self.costed({str(f.source/'out'): 46}) as (state, clock):
                result = readiness.usage(f.source, 'out', readiness.Deadline(10 ** 6, clock), clock)
            self.assertEqual(result, {'path': 'out', 'status': 'incomplete', 'reason': 'time cap', 'entriesExamined': 2})

    def test_time_passing_between_directories_stops_before_the_next_is_opened(self):
        with fixture(usageSeconds=45) as f:
            paths = self.tree(f, ['a', 'b', 'c', 'd'])
            # Whichever directory the stack pops first (scandir order is the filesystem's) passes the budget on close.
            with self.costed({}, close_costs={p: 50 for p in paths}) as (state, clock):
                result = readiness.usage(f.source, 'out', readiness.Deadline(10 ** 6, clock), clock)
            self.assertEqual((result['status'], result['reason']), ('incomplete', 'time cap'))
            opened = [p for p in state['opened'] if p != 'out']
            self.assertEqual(len(opened), 1, 'nothing opened after expiry')
            self.assertIn(opened[0], {f'out/{n}' for n in paths.values()})

    def test_time_passing_after_the_last_listing_is_not_reported_complete(self):
        with fixture(usageSeconds=45) as f:
            (f.source/'out').mkdir()
            with self.costed({}, close_costs={str(f.source/'out'): 50}) as (state, clock):
                result = readiness.usage(f.source, 'out', readiness.Deadline(10 ** 6, clock), clock)
            self.assertEqual(result, {'path': 'out', 'status': 'incomplete', 'reason': 'time cap', 'entriesExamined': 0})

    def test_within_budget_and_at_the_boundary_usage_completes(self):
        with fixture(usageSeconds=45) as f:
            paths = self.tree(f, ['a', 'b', 'c'])
            for cost in (10, 15):  # 30 s, then exactly 45 s: not over the budget
                with self.costed({p: cost for p in paths}) as (state, clock):
                    result = readiness.usage(f.source, 'out', readiness.Deadline(10 ** 6, clock), clock)
                self.assertEqual((result['status'], result['entries'], state['now']), ('complete', 3, 3 * cost))

class CapacityMetadataTest(unittest.TestCase):
    """FAM-CAPACITY-METADATA-S003: fixed bounded metadata only - manifest three-state comparison, qualified physical
    allocation of the Frankel unpack and generated vendor, strict fs_type markers, the adevtool dependency revision and
    fixed tool entries, prerequisite directories and fixed Blazer paths. Every source-tree executable in these fixtures
    is hostile; none may run, and no tool's bytes are read."""
    HOSTILE = '#!/bin/sh\necho ran >> "{sentinel}"\n'

    def deps(self, f, revision=None, tools=readiness.HOST_TOOLS):
        bin_dir = f.source/readiness.ADEVTOOL_DEPS/'host/linux-x86/bin'
        bin_dir.mkdir(parents=True)
        if revision is not None:
            (f.source/readiness.ADEVTOOL_DEPS/'adevtool_revision').write_bytes(revision)
        for tool in tools:
            (bin_dir/tool).write_text(self.HOSTILE.format(sentinel=f.sentinel))
            os.chmod(bin_dir/tool, 0o755)
        return bin_dir

    def test_manifest_comparison_separates_unavailable_from_mismatch(self):
        with fixture() as f:
            pinned = readiness.sha256_file(ROOT/'os/pinned-manifest.xml')
            check = lambda: readiness.manifest_comparison(readiness.file_record(f.source, '.repo/manifest.xml'), pinned)
            self.assertEqual(check()['comparison'], 'match')
            manifest = f.source/'.repo/manifest.xml'
            manifest.write_text('<manifest><include name="default.xml" /></manifest>\n')
            record = check()
            self.assertEqual((record['comparison'], record['status'], record['pinnedSha256']), ('mismatch', 'present', pinned))
            self.assertEqual(record['sha256'], hashlib.sha256(manifest.read_bytes()).hexdigest())
            manifest.unlink()
            self.assertEqual((check()['comparison'], check()['status']), ('unavailable', 'missing'))
            (f.source/'.repo/manifests').mkdir()
            shutil.copyfile(ROOT/'os/pinned-manifest.xml', f.source/'.repo/manifests/default.xml')
            os.symlink('manifests/default.xml', manifest)  # a repo-style link to the pinned bytes: still never followed
            record = check()
            self.assertEqual((record['comparison'], record['status']), ('unavailable', 'symlink-refused'))
            self.assertNotIn('sha256', record)

    def unpack(self, f):
        root = f.source/readiness.FRANKEL_UNPACK
        (root/'vendor').mkdir(parents=True, exist_ok=True)
        with open(root/'sparse.img', 'wb') as sparse:
            sparse.truncate(8 << 20)
            sparse.seek(4 << 20)
            sparse.write(b'x')
        (root/'blob.bin').write_bytes(b'b' * 65536)
        os.link(root/'blob.bin', root/'vendor/blob-link.bin')
        return root

    def test_physical_allocation_is_qualified_for_sparse_files_and_hard_links(self):
        with fixture() as f:
            root = self.unpack(f)
            device = os.stat(f.source).st_dev
            result = readiness.usage(f.source, readiness.FRANKEL_UNPACK, readiness.Deadline(60), physical=True, root_device=device)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual((result['hardLinkedEntries'], result['uniqueHardLinkedInodes']), (2, 1))
            self.assertIn('counted once', result['hardLinks'])
            blob = os.stat(root/'blob.bin').st_blocks * 512
            sparse = os.stat(root/'sparse.img').st_blocks * 512
            self.assertLess(sparse, 8 << 20)
            self.assertGreaterEqual(result['sparseFiles'], 1)
            self.assertGreater(result['apparentBytes'], result['allocatedBytes'], 'logical size is not physical allocation')
            self.assertLess(result['allocatedBytes'], blob * 2 + sparse + 16 * 4096, 'the linked inode is counted once')
            prop = len(f.nested['vendor/adevtool/dl/unpacked/frankel-CP3A.261005.005/vendor/build.prop'])
            self.assertEqual(result['apparentBytes'], (8 << 20) + 65536 + prop, 'each regular inode once')
            self.assertEqual(result['mount'], {'device': f'{os.major(device)}:{os.minor(device)}', 'sameDeviceAsSourceRoot': True})
            other = readiness.usage(f.source, readiness.FRANKEL_UNPACK, readiness.Deadline(60), physical=True, root_device=-1)
            self.assertFalse(other['mount']['sameDeviceAsSourceRoot'])

    def test_targeted_allocation_caps_skips_and_links_stay_unknown(self):
        with fixture(usageEntries=3) as f:
            self.unpack(f)
            capped = readiness.usage(f.source, readiness.FRANKEL_UNPACK, readiness.Deadline(60), physical=True, root_device=0)
            self.assertEqual((capped['status'], capped['reason']), ('incomplete', 'entry cap (3)'))
            for total in ('allocatedBytes', 'apparentBytes', 'mount'):
                self.assertNotIn(total, capped, 'an incomplete walk is unknown, not a bound')
            readiness.LIMITS['usageEntries'] = 2_000_000
            keys = f.source/readiness.FRANKEL_UNPACK/'keys'
            keys.mkdir()
            (keys/'releasekey.pk8').write_bytes(b'SYNTHETIC-SENTINEL-NOT-A-KEY\n' * 4096)
            with watched() as log:
                skipped = readiness.usage(f.source, readiness.FRANKEL_UNPACK, readiness.Deadline(60), physical=True, root_device=0)
            self.assertEqual(skipped['skippedDirectories'], 1)
            self.assertEqual(touched(log, keys), [])
            shutil.rmtree(f.source/'vendor/google_devices/frankel')
            os.symlink(f.source/readiness.FRANKEL_UNPACK, f.source/'vendor/google_devices/frankel')
            self.assertEqual(readiness.usage(f.source, 'vendor/google_devices/frankel', readiness.Deadline(60), physical=True),
                {'path': 'vendor/google_devices/frankel', 'status': 'refused', 'reason': 'symlink-refused'})

    def test_fs_type_markers_are_bounded_strict_and_never_followed(self):
        with fixture() as f:
            root = self.unpack(f)
            (root/'system.img.fs_type').write_bytes(b'erofs')
            (root/'vendor.img.fs_type').write_bytes(b'ext4')
            (root/'product.img.fs_type').write_bytes(b'ext4\n')
            (root/'odm.img.fs_type').write_bytes(b'xfs')
            (root/'system_ext.img.fs_type').write_bytes(b'e' * 129)
            outside = f.tmp/'outside-marker'
            outside.write_bytes(b'ext4')
            os.symlink(outside, root/'odm_dlkm.img.fs_type')
            with watched() as log:
                markers = readiness.fs_type_markers(f.source)
            self.assertEqual(markers['system'], {'status': 'present', 'fsType': 'erofs', 'sizeBytes': 5})
            self.assertEqual(markers['vendor'], {'status': 'present', 'fsType': 'ext4', 'sizeBytes': 4})
            self.assertEqual(markers['product']['fsType'], 'unknown', 'a newline is not the strict token')
            self.assertEqual(markers['odm']['fsType'], 'unknown')
            self.assertEqual(markers['system_ext'], {'status': 'too-large'})
            self.assertEqual(markers['odm_dlkm'], {'status': 'symlink-refused'})
            self.assertEqual(markers['system_dlkm'], {'status': 'absent'})
            self.assertEqual(sorted(markers), sorted(readiness.FS_TYPE_PARTITIONS))
            self.assertNotIn(os.path.realpath(root/'system_ext.img.fs_type'), log['read'], 'an oversized marker is not read')
            self.assertEqual(touched(log, outside), [])

    def test_adevtool_revision_compares_exactly_and_tools_are_only_lstat(self):
        pin = readiness.EXPECTED_PINS['vendor/adevtool']
        cases = [((pin + '\n').encode(), 'match', None), (pin.encode(), 'mismatch', 'different line ending'),
                 (('0' * 40 + '\n').encode(), 'mismatch', None), ((pin + '\n\n').encode(), 'mismatch', 'different line ending')]
        for content, comparison, reason in cases:
            with fixture() as f:
                bin_dir = self.deps(f, content)
                with no_processes() as started, watched() as log:
                    deps = readiness.adevtool_deps(f.source)
                self.assertEqual(started, [])
                self.assertEqual(deps['revision']['comparison'], comparison, content)
                self.assertEqual(reason is None, 'reason' not in deps['revision'])
                self.assertEqual(touched(log, bin_dir), [os.path.realpath(bin_dir)], 'only the directory, never a tool')
                self.assertFalse(f.sentinel.exists())
        with fixture() as f:
            bin_dir = self.deps(f, None, tools=[t for t in readiness.HOST_TOOLS if t not in ('lpunpack', 'toybox', 'lz4')])
            os.chmod(bin_dir/'aapt2', 0o644)
            os.symlink('/bin/true', bin_dir/'toybox')
            (bin_dir/'lz4').mkdir()
            deps = readiness.adevtool_deps(f.source)
            self.assertEqual(deps['revision'], {'status': 'absent', 'comparison': 'unavailable'})
            tools = deps['tools']
            self.assertEqual(sorted(tools), sorted(readiness.HOST_TOOLS))
            self.assertEqual(tools['simg2img']['status'], 'present')
            self.assertTrue(tools['simg2img']['executable'])
            self.assertFalse(tools['aapt2']['executable'])
            self.assertEqual((tools['lpunpack'], tools['toybox'], tools['lz4']),
                ({'status': 'absent'}, {'status': 'symlink-not-followed'}, {'status': 'not-regular'}))
            self.assertIn('usability is not shown', deps['note'])
        with fixture() as f:
            self.assertEqual(readiness.adevtool_deps(f.source)['tools']['aapt2']['status'], 'absent')

    def test_prerequisites_and_blazer_paths_are_fixed_presence_only(self):
        with fixture() as f:
            node = f.source/'vendor/adevtool/node_modules/.bin'
            node.mkdir(parents=True)
            (node/'node').write_text(self.HOSTILE.format(sentinel=f.sentinel))
            os.chmod(node/'node', 0o755)
            (f.source/'prebuilts/jdk/jdk21/linux-x86/bin').mkdir(parents=True)
            os.symlink(f.tmp, f.source/'prebuilts/build-tools')
            archive = f.source/'vendor/adevtool/dl'/(CONTRACT['devices']['blazer']['vendorInputs']['factoryImage']['file'] + '.tmp')
            archive.write_bytes(b'partial')
            with no_processes() as started:
                prereq = readiness.prerequisites(f.source)
                paths = readiness.blazer_paths(f.source, readiness.contract_targets(CONTRACT)['blazer'])
            self.assertEqual(started, [])
            self.assertFalse(f.sentinel.exists())
            self.assertEqual(prereq['directories'], {'vendor/adevtool/node_modules': 'present',
                'prebuilts/build-tools/path/linux-x86': 'symlink-refused', 'prebuilts/jdk/jdk21/linux-x86/bin': 'present'})
            self.assertEqual((prereq['node']['location'], prereq['node']['version']), ('unknown', 'unknown'))
            self.assertIn('does not show Node >= 24', prereq['node']['reason'])
            self.assertEqual({k: v['status'] for k, v in paths.items()}, {'vendorDestination': 'present', 'archive': 'present',
                'archiveTmp': 'present', 'unpack': 'absent', 'unpackFinalTemp': 'unknown'})
            shutil.rmtree(f.source/'vendor/google_devices/blazer')
            os.symlink('../google_devices/frankel', f.source/'vendor/google_devices/blazer')
            paths = readiness.blazer_paths(f.source, readiness.contract_targets(CONTRACT)['blazer'])
            self.assertEqual(paths['vendorDestination']['status'], 'symlink-refused')
            self.assertIsNone(paths['unpackFinalTemp']['path'])

    def test_a_full_run_records_the_capacity_metadata_and_runs_nothing(self):
        with fixture() as f:
            self.unpack(f)
            (f.source/readiness.FRANKEL_UNPACK/'vendor.img.fs_type').write_bytes(b'erofs')
            self.deps(f, (readiness.EXPECTED_PINS['vendor/adevtool'] + '\n').encode())
            before = snapshot(f.source)
            with no_processes() as started:
                receipt, _ = f.run()
            self.assertEqual((started, f.sentinel.exists(), snapshot(f.source) == before), ([], False, True))
            self.assertEqual(receipt['status'], 'partial')
            capacity = receipt['capacity']
            self.assertEqual(sorted(capacity['allocation']), ['frankelGeneratedVendor', 'frankelUnpack'])
            self.assertEqual({v['status'] for v in capacity['allocation'].values()}, {'complete'})
            self.assertEqual(capacity['fsTypeMarkers']['vendor']['fsType'], 'erofs')
            self.assertEqual(capacity['reserveFloorBytes'], 322122547200)
            self.assertIn('not a Blazer bound', capacity['note'])
            self.assertEqual(receipt['adevtoolDeps']['revision']['comparison'], 'match')
            self.assertEqual(receipt['manifest']['comparison'], 'match')
            self.assertEqual(receipt['blazerPaths']['unpackFinalTemp']['status'], 'unknown')

class ProcTest(unittest.TestCase):
    def test_foreign_processes_are_recognised_from_proc(self):
        with tempfile.TemporaryDirectory() as tmp:
            for pid, comm in (('1', 'systemd'), ('20', 'ninja'), ('30', 'run_cvd'), ('self', 'ninja'), ('40', 'python3')):
                (Path(tmp)/pid).mkdir()
                (Path(tmp)/pid/'comm').write_text(comm + '\n')
            (Path(tmp)/'50').mkdir()  # a process that exited between listing and reading
            self.assertEqual(readiness.foreign_work(tmp), ['ninja', 'run_cvd'])

    def test_the_cli_plans_and_checks_without_running_anything(self):
        result = subprocess.run([sys.executable, str(SCRIPTS/'device-readiness.py'), 'plan'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual((plan['executed'], plan['devices']), (False, ['frankel', 'blazer', 'rango']))
        self.assertEqual((plan['commands'], plan['lunch']['executed']), ({}, False))
        bad = subprocess.run([sys.executable, str(SCRIPTS/'device-readiness.py'), 'run', '--run-id', 'x', '--run-attempt', '1',
            '--recipe-sha', 'a' * 40, '--evidence-dir', '/nonexistent/never'], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)

if __name__ == '__main__':
    unittest.main()
