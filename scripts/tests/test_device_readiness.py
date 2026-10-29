"""FAM-HOST-READINESS-S002: the os-release device-readiness mode and scripts/device-readiness.py.

The workflow mode reuses the guard, controller, shared builder runner, global concurrency and cleanup; the inspection job
holds contents: read only and no build, sign, publish or alpha job can run in it; cleanup always waits for it and always
stops the builder. The probe runs here against a representative fixture tree (the pinned adevtool and platform_build
fixture files, the adevtool vendor skeletons and a stand-in lunch chain), never against a fabricated host result: lock
conflict, invalid input, contract and pin refusal, symlink escape, missing generated vendor inputs, tool timeout, secret
non-disclosure, scratch collision and foreign-file preservation, and cleanup after early and late failure. Offline; no
builder, network or cloud access."""
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
    if '    needs: builder\n' not in jobs_.get('publisher', '') or not re.search(r"inputs\.mode == 'app-only'", jobs_.get('app-publisher', '')):
        failures.append('a publisher is not bound to a build mode')
    if not re.search(r'\[\[ "\$MODE" != device-readiness \|\| "\$CANDIDATE_SHA" == "\$GITHUB_SHA" \]\]', text):
        failures.append('the guard does not bind the candidate to the workflow recipe')
    return failures

class WorkflowTest(unittest.TestCase):
    def test_the_mode_reuses_the_existing_trust_and_runs_nothing_else(self):
        self.assertEqual(wiring_failures(WORKFLOW), [])
        self.assertIn('        options: [os-release, device-readiness, app-only, emulator-alpha,', WORKFLOW)
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
            'builder in readiness mode': WORKFLOW.replace("    if: inputs.mode == 'os-release'\n", "    if: inputs.mode == 'os-release' || inputs.mode == 'device-readiness'\n"),
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

FAKE_ENVSETUP = r'''lunch() {
  IFS=- read -r p r v <<< "$1"
  export TARGET_PRODUCT=$p TARGET_RELEASE=$r TARGET_BUILD_VARIANT=$v
  mkdir -p "$OUT_DIR" && echo "$p $r $v" > "$OUT_DIR/.leftovers" && env > "$OUT_DIR/lunch-env.txt"
  [[ -z "${READINESS_SLEEP:-}" && ! -e "$PWD/slow-$p" ]] || { sleep 60 & sleep 60; }
  [[ ! -e "$PWD/write-outside-$p" ]] || : > "$PWD/stray-$p"
  echo "PIXEL_OTA_TOKEN=leaked-token-value private key material" >&2
  echo "plain diagnostic line" >&2
}
get_build_var() {
  case $1 in
    PRODUCT_DEVICE) echo "$TARGET_PRODUCT" ;;
    TARGET_CPU_ABI) echo arm64-v8a ;;
    TARGET_BOARD_PLATFORM) echo laguna ;;
    PLATFORM_SDK_VERSION) echo 37 ;;
    PRODUCT_OUT) echo "$OUT_DIR/target/product/$TARGET_PRODUCT" ;;
    *) eval "echo \${$1:-}" ;;
  esac
}
'''

def copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)

def git_init(path, message='pin'):
    path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
    for args in (['init', '-q'], ['commit', '-q', '--allow-empty', '-m', message]):
        subprocess.run(['git', '-C', str(path), *args], check=True, env=env, capture_output=True)
    return subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()

class Fixture:
    """A representative retained tree: the pinned fixture files at their tree paths, the adevtool vendor skeletons as the
    generated vendor trees, factory archives, a stand-in lunch chain and a lock file, all under one temporary directory."""
    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.source = self.tmp/'grapheneos'
        self.home = self.tmp/'home'
        self.home.mkdir()
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
        (dl/'unpacked/frankel-CP3A.261005.005').mkdir(parents=True)
        (self.source/'build/make/envsetup.sh').write_text(FAKE_ENVSETUP)
        (self.source/'build/make/shell_utils.sh').write_text('# stand-in\n')
        (self.source/'build/soong/scripts').mkdir(parents=True)
        (self.source/'build/soong/soong_ui.bash').write_text('# stand-in\n')
        (self.source/'build/soong/scripts/microfactory.bash').write_text('# stand-in\n')
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
def fixture(**limits):
    saved_chain, saved_limits = dict(readiness.LUNCH_CHAIN), dict(readiness.LIMITS)
    with tempfile.TemporaryDirectory() as tmp:
        f = Fixture(tmp)
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
            receipt, evidence = f.run()
            self.assertEqual(receipt['status'], 'complete')
            on_disk = f.receipt(evidence)
            self.assertEqual(on_disk, json.loads(json.dumps(receipt)))
            self.assertEqual((evidence/'SHA256SUMS').read_text(),
                f'{readiness.sha256_file(evidence/"device-readiness.json")}  device-readiness.json\n')
            self.assertEqual((receipt['recipeSha'], receipt['attempt'], receipt['lock']['acquired'], receipt['executedMake']),
                (f.recipe, '4081-1', True, False))
            self.assertEqual(receipt['contract']['sha256'], readiness.sha256_file(ROOT/'os/devices/pixel10.json'))
            fs = receipt['filesystem']
            st = os.statvfs(f.source)
            self.assertEqual(fs['totalBytes'], st.f_blocks * st.f_frsize)
            self.assertTrue(0 < fs['availableBytes'] <= fs['totalBytes'] and fs['type'])
            self.assertEqual({p: (r['observed'], r['matches']) for p, r in receipt['pins'].items()},
                {p: (rev, True) for p, rev in readiness.EXPECTED_PINS.items()})
            self.assertTrue(receipt['manifest']['equalsPinnedManifest'])
            # The pinned public fixture files are recognised by git blob; presence of generated files is not completeness.
            frankel = {r['path']: r for r in receipt['products']['frankel']['files']}
            self.assertTrue(frankel['vendor/adevtool/config/device/frankel.yml']['matchesPinnedFixture'])
            self.assertTrue(frankel['build/make/target/product/generic_system.mk']['matchesPinnedFixture'])
            self.assertTrue(all(r['status'] == 'present' for r in frankel.values()))
            self.assertEqual(receipt['products']['rango']['kernelDir'], 'present')
            generated = receipt['generatedVendor']['frankel']
            self.assertEqual(generated['status'], 'present-unverified')
            self.assertEqual(generated['vendorStateEntries'], ['frankel.json'])
            self.assertTrue({r['path']: r for r in generated['files']}['vendor/google_devices/frankel/AndroidProducts.mk']['matchesPinnedFixture'])
            # Factory presence and checksum are separate facts.
            factory = receipt['factory']
            self.assertEqual((factory['frankel']['status'], factory['frankel']['checksum'], factory['frankel']['unpacked']),
                ('present', 'mismatch', 'present'))
            self.assertEqual((factory['blazer']['status'], factory['blazer']['checksum']), ('present', 'mismatch'))
            self.assertEqual((factory['rango']['status'], factory['rango']['checksum'], factory['rango']['sizeBytes']), ('missing', 'notValidated', None))
            for device in readiness.READINESS_DEVICES:
                lunch = receipt['lunch']['devices'][device]
                self.assertEqual(lunch['status'], 'resolved', lunch)
                self.assertEqual(lunch['values'], {'TARGET_PRODUCT': device, 'TARGET_RELEASE': 'cur', 'TARGET_BUILD_VARIANT': 'user',
                    'PRODUCT_DEVICE': device, 'TARGET_BOARD_PLATFORM': 'laguna', 'TARGET_CPU_ABI': 'arm64-v8a',
                    'PLATFORM_SDK_VERSION': '37', 'PRODUCT_OUT': f'$OUT_DIR/target/product/{device}'})
                self.assertEqual(lunch['stderrTail'], ['plain diagnostic line'])
                # Every lunch write landed in this attempt's scratch.
                self.assertTrue((f.home/'rose-actions/device-readiness-4081-1/scratch'/device/'out/.leftovers').is_file())
            self.assertTrue(receipt['lunch']['sourceTopLevelUnchanged'])
            usage = receipt['usage']
            self.assertEqual(usage['generatedVendor']['status'], 'complete')
            self.assertGreater(usage['generatedVendor']['entries'], 12)
            self.assertEqual((usage['retainedOutput']['status'], usage['defaultOutput']['status']), ('absent', 'absent'))
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
            with self.assertRaises(readiness.InvalidInput):
                readiness.lunch_script('mustang')

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

    def test_pins_or_lunch_chain_not_matching_skip_lunch_as_setup_missing(self):
        with fixture() as f:
            f.heads['vendor/state'] = '0' * 40
            receipt, _ = f.run()
            self.assertEqual(receipt['pins']['vendor/state']['matches'], False)
            for device in readiness.READINESS_DEVICES:
                self.assertEqual(receipt['lunch']['devices'][device]['status'], 'SETUP_MISSING')
                self.assertIn('vendor/adevtool or vendor/state is not at the contract pin', receipt['lunch']['devices'][device]['missing'])
            self.assertFalse((f.home/'rose-actions/device-readiness-4081-1/scratch').exists(), 'nothing was run')
        with fixture() as f:
            (f.source/'build/soong/soong_ui.bash').write_text('# a different, uninspected chain\n')
            receipt, _ = f.run()
            self.assertEqual(receipt['lunch']['chainProblems'], ['build/soong/soong_ui.bash is not the inspected pinned bytes'])
            self.assertEqual({d['status'] for d in receipt['lunch']['devices'].values()}, {'SETUP_MISSING'})

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
            self.assertEqual(receipt['generatedVendor']['blazer']['status'], 'link-not-followed')
            self.assertEqual(receipt['lunch']['devices']['blazer']['status'], 'SETUP_MISSING')
            rango = {r['path']: r for r in receipt['products']['rango']['files']}['vendor/adevtool/config/device/rango.yml']
            self.assertEqual(rango, {'path': 'vendor/adevtool/config/device/rango.yml', 'status': 'escapes-root'})
            self.assertEqual(receipt['lunch']['devices']['rango']['status'], 'SETUP_MISSING')
            self.assertEqual(receipt['lunch']['devices']['frankel']['status'], 'resolved')
            self.assertLess(receipt['usage']['generatedVendor']['allocatedBytes'], 100000)
            self.assertNotIn('PRIVATE KEY', (evidence/'device-readiness.json').read_text())

    def test_missing_generated_vendor_inputs_are_setup_missing_for_that_device_only(self):
        with fixture() as f:
            shutil.rmtree(f.source/'vendor/google_devices/rango')
            (f.source/'vendor/google_devices/blazer/firmware/android-info.txt').unlink()
            receipt, _ = f.run()
            self.assertEqual(receipt['generatedVendor']['rango']['status'], 'missing')
            self.assertEqual(receipt['generatedVendor']['blazer']['status'], 'incomplete')
            self.assertEqual(receipt['lunch']['devices']['rango'],
                {'status': 'SETUP_MISSING', 'missing': ['vendor/google_devices/rango missing']})
            self.assertEqual(receipt['lunch']['devices']['blazer']['status'], 'SETUP_MISSING')
            self.assertEqual(receipt['lunch']['devices']['frankel']['status'], 'resolved')

    def test_a_tool_timeout_is_recorded_and_its_process_group_killed(self):
        with fixture(lunchSeconds=2) as f:
            (f.source/'slow-blazer').write_text('')
            start = time.monotonic()
            receipt, _ = f.run()
            self.assertLess(time.monotonic() - start, 30)
            self.assertEqual(receipt['lunch']['devices']['blazer'], {'status': 'timeout', 'seconds': 2})
            self.assertEqual(receipt['lunch']['devices']['rango']['status'], 'resolved')
            self.assertEqual(subprocess.run(['pgrep', '-f', 'sleep 60'], capture_output=True).stdout, b'')

    def test_a_write_outside_the_scratch_is_reported_and_the_run_is_not_complete(self):
        with fixture() as f:
            (f.source/'write-outside-rango').write_text('')
            receipt, _ = f.run()
            self.assertEqual((receipt['status'], receipt['lunch']['sourceTopLevelUnchanged']), ('partial', False))
            self.assertTrue((f.source/'stray-rango').exists(), 'reported, never deleted by the probe')

    def test_the_deadline_yields_skipped_and_unavailable_never_zero(self):
        with fixture() as f:
            ticks = iter(range(0, 10 ** 6, 300))
            receipt, _ = f.run(f.host(clock=lambda: next(ticks)))
            self.assertEqual(receipt['status'], 'partial')
            self.assertIn('skipped', {d['status'] for d in receipt['lunch']['devices'].values()})
            for record in receipt['usage'].values():
                self.assertIn(record['status'], ('unavailable', 'absent'))
                self.assertNotIn('allocatedBytes', record)
            self.assertEqual(receipt['factory']['frankel']['checksum'], 'unavailable')

    def test_no_key_environment_or_secret_reaches_lunch_or_the_receipt(self):
        with fixture() as f:
            secret = 'sk-live-very-secret-value'
            saved = dict(os.environ)
            os.environ.update(PIXEL_OTA_TOKEN=secret, GCP_SIGNER_SERVICE_ACCOUNT=secret, ACTIONS_ID_TOKEN_REQUEST_TOKEN=secret)
            try:
                receipt, evidence = f.run()
                lunch_env = (f.home/'rose-actions/device-readiness-4081-1/scratch/frankel/out/lunch-env.txt').read_text()
            finally:
                os.environ.clear()
                os.environ.update(saved)
            self.assertNotIn(secret, lunch_env)
            self.assertEqual(sorted(line.split('=', 1)[0] for line in lunch_env.splitlines() if line.split('=', 1)[0] in
                ('HOME', 'TMPDIR', 'OUT_DIR')), ['HOME', 'OUT_DIR', 'TMPDIR'])
            self.assertIn(f'OUT_DIR={f.home}/rose-actions/device-readiness-4081-1/scratch/frankel/out\n', lunch_env)
            text = (evidence/'device-readiness.json').read_text()
            for marker in (secret, 'leaked-token-value', 'private key material'):
                self.assertNotIn(marker, text)
            self.assertEqual(readiness.sanitize('$(id)'), '(withheld)')
            self.assertEqual(readiness.sanitize('api_key=abc'), '(withheld)')
            self.assertEqual(readiness.tail('ok\nAuthorization: Bearer x\n\x1b[31mred\n'), ['ok', '?[31mred'])

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
            self.assertEqual(receipt['status'], 'complete')
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
            self.assertEqual(receipt['lunch']['devices']['frankel']['status'], 'resolved')
            before = sorted(str(p.relative_to(f.source)) for p in f.source.rglob('*'))
            self.assertTrue(readiness.cleanup(f.host(), '4081', '1')['removed'])
            self.assertEqual(sorted(str(p.relative_to(f.source)) for p in f.source.rglob('*')), before, 'the tree is untouched')
            # The lock was released: a following attempt can take it.
            with open(f.lock, 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

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
        self.assertNotIn(' m ', ' '.join(plan['commands']['frankel']))
        self.assertNotRegex(json.dumps(plan['commands']), r'\b(make|installclean|m|repo sync|adevtool)\b ')
        bad = subprocess.run([sys.executable, str(SCRIPTS/'device-readiness.py'), 'run', '--run-id', 'x', '--run-attempt', '1',
            '--recipe-sha', 'a' * 40, '--evidence-dir', '/nonexistent/never'], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)

if __name__ == '__main__':
    unittest.main()
