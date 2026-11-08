#!/usr/bin/env python3
"""1F hosted Agent route: scripts/agent-hosted.py and os-release.yml modes agent-hosted and agent-hosted-probe.

Synthetic APKs, a synthetic RoseOS-1 history (test_release_allocate's fixtures), an in-memory ledger with the real
record format (scripts/app_reservation_store.py) and fake gcloud runs only: no network, token, secret, signer,
builder or publication. A passing test is not a hosted build, a signer admission or a release.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import shutil
import unittest
import zipfile
import importlib.util

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
import app_reservation_store as store_module  # noqa: E402
import os_release_shape as shape  # noqa: E402
import test_release_allocate as trl  # noqa: E402  (fixtures only; its own tests are not re-run here)

ROOT = TESTS.parents[1]
spec = importlib.util.spec_from_file_location('agent_hosted', ROOT/'scripts/agent-hosted.py')
hosted = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hosted)
COMMITTED = json.loads((ROOT/'os/release-pipeline/agent/hosted.json').read_text())
WORKFLOW = shape.text()
STORE = {'kind': 'github-annotated-tags', 'repository': 'Example/ledger',
         'reservationPrefix': 'refs/tags/app-reservations/dev.pixel.voice/', 'floorPrefix': 'refs/tags/app-floors/dev.pixel.voice/'}
REF = 'Immortal-Protocols/RoseOS-1/.github/workflows/os-release.yml@refs/heads/main'
ENDPOINT = 'https://100.64.0.9:9444'
COMPILED = b'\x03\x00\x08\x00compiled network security config'


def connection(endpoint=ENDPOINT):
    return (json.dumps({'endpoint': endpoint, 'pin': 'ab' * 32}, indent=2) + '\n').encode()


def apk_bytes(entries=None, extra=()):
    entries = entries if entries is not None else {
        'assets/connection.json': connection(), 'assets/mac-screen-connection.json': connection(),
        'assets/clipboard-connection.json': (json.dumps({'endpoint': ENDPOINT}, indent=2) + '\n').encode(),
        'res/raw/mac_screen_cert.pem': b'-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n',
        'res/xml/mac_screen_network.xml': COMPILED, 'classes.dex': b'dex'}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        for name, data in extra:
            archive.writestr(name, data)
    return buffer.getvalue()


def pin_for(data, compiled=COMPILED, endpoint=ENDPOINT):
    """The publicInputs pin a hosted.json would carry for this synthetic source APK."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = {rel: archive.read(entry) for rel, entry in hosted.apk_entries().items()}
    files[hosted.NETWORK] = hosted.network_config(endpoint)
    return {'sourceApk': {'url': 'https://github.com/Immortal-Protocols/pixel-ota/releases/download/rose-agent-1/x.apk',
                          'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
            'digests': {rel: hashlib.sha256(d).hexdigest() for rel, d in files.items()},
            'compiledNetworkSha256': hashlib.sha256(compiled).hexdigest(), 'basis': 'synthetic'}


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rose-agent-hosted-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_apk(self, data, name='source.apk'):
        path = self.tmp/name
        path.write_bytes(data)
        return path


class PublicInputs(Temp):
    def test_the_four_stored_inputs_are_copied_and_the_config_is_regenerated(self):
        data = apk_bytes()
        cfg = {'publicInputs': pin_for(data)}
        result = hosted.public_inputs(self.write_apk(data), self.tmp/'out', cfg)
        out = self.tmp/'out'
        found = sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file())
        self.assertEqual(found, sorted([*hosted.apk_entries(), hosted.RECEIPT]))
        self.assertEqual((out/'workspace/rose-agent/app/assets/connection.json').read_bytes(), connection())
        self.assertEqual((out/hosted.NETWORK).read_text(), hosted.NETWORK_HEAD + '100.64.0.9' + hosted.NETWORK_TAIL)
        receipt = json.loads((out/hosted.RECEIPT).read_text())
        self.assertEqual(receipt, {'receiptType': 'public-connection-assets', 'files': cfg['publicInputs']['digests']})
        self.assertEqual(result['regenerated'], [hosted.NETWORK])

    def test_any_other_source_or_input_refuses_and_writes_nothing(self):
        data = apk_bytes()
        cfg = {'publicInputs': pin_for(data)}
        cases = {
            'another apk': (apk_bytes(extra=[('x', b'1')]), cfg),
            'a missing entry': (apk_bytes({'assets/connection.json': connection()}), None),
            'a changed compiled config': (apk_bytes(dict(zip(
                ['assets/connection.json', 'assets/mac-screen-connection.json', 'assets/clipboard-connection.json',
                 'res/raw/mac_screen_cert.pem', 'res/xml/mac_screen_network.xml'],
                [connection(), connection(), b'{}', b'pem', b'other compiled']))), None),
            'a changed input': (apk_bytes(), {'publicInputs': dict(pin_for(data), digests=dict(
                pin_for(data)['digests'], **{'workspace/rose-agent/app/res/raw/mac_screen_cert.pem': '0' * 64}))}),
        }
        for label, (source, config) in cases.items():
            if config is None:  # pinned to this APK, so only the content check can refuse it
                config = {'publicInputs': dict(pin_for(source), compiledNetworkSha256=hashlib.sha256(COMPILED).hexdigest(),
                                               digests=pin_for(data)['digests'])} if 'compiled' in label else \
                    {'publicInputs': dict(pin_for(data), sourceApk=dict(pin_for(data)['sourceApk'],
                     size=len(source), sha256=hashlib.sha256(source).hexdigest()))}
            with self.subTest(label), self.assertRaises(hosted.Refused):
                hosted.public_inputs(self.write_apk(source, label + '.apk'), self.tmp/label, config)
            self.assertFalse((self.tmp/label).exists())
        (self.tmp/'taken').mkdir()
        with self.assertRaisesRegex(hosted.Refused, 'already exists'):
            hosted.public_inputs(self.write_apk(data), self.tmp/'taken', cfg)

    def test_an_unpinned_config_is_setup_missing(self):
        with self.assertRaises(hosted.SetupMissing):
            hosted.pinned_inputs({})
        with self.assertRaises(hosted.SetupMissing):
            hosted.pinned_inputs({'publicInputs': dict(pin_for(apk_bytes()), digests={})})

    def test_the_template_is_apps_own(self):
        # scripts/configure-local.py writes exactly this config around escape(u.hostname).
        source = (ROOT/'scripts/configure-local.py').read_text()
        self.assertIn("network.write_text('" + hosted.NETWORK_HEAD + "'+escape(u.hostname)+'"
                      + hosted.NETWORK_TAIL.replace('\n', '\\n') + "')", source)
        self.assertEqual(set(hosted.apk_entries()), set(hosted.load('host-app-build').PUBLIC_ASSETS))
        self.assertLessEqual(set(hosted.apk_entries()), set(hosted.KNOWN_APK_ENTRIES))

    def test_the_committed_pin_is_one_public_release_asset(self):
        pin = hosted.pinned_inputs(COMMITTED)
        self.assertRegex(pin['sourceApk']['url'], r'^https://github\.com/Immortal-Protocols/pixel-ota/releases/download/'
                                                  r'rose-agent-(\d+)/dev\.pixel\.voice-\1\.apk$')
        # Exactly the current inputs: a retired one (host-app-build.RETIRED_ASSETS) is never pinned or checked.
        build = hosted.load('host-app-build')
        self.assertEqual(set(pin['digests']), set(build.PUBLIC_ASSETS))
        self.assertFalse(set(pin['digests']) & set(build.RETIRED_ASSETS))
        self.assertNotIn('/home/pixelbuild', json.dumps(COMMITTED))
        # The five inputs stay git-ignored deployment files: none is committed anywhere in the tree.
        tracked = subprocess.run(['git', '-C', str(ROOT), 'ls-files'], capture_output=True, text=True, check=True).stdout
        for name in ('clipboard-connection.json', 'mac_screen_cert.pem', 'mac_screen_network.xml'):
            self.assertNotIn('/' + name + '\n', '\n' + tracked.replace('\n', '\n/'))


class CheckApk(Temp):
    def test_a_build_carrying_the_pinned_inputs_passes_and_any_difference_refuses(self):
        data = apk_bytes()
        cfg = {'publicInputs': pin_for(data)}
        self.assertEqual(hosted.check_apk(self.write_apk(data, 'built.apk'), cfg)['compiledNetworkSha256'],
                         hashlib.sha256(COMPILED).hexdigest())
        changed = apk_bytes(extra=[])
        with zipfile.ZipFile(io.BytesIO(changed)) as archive:
            entries = {n: archive.read(n) for n in archive.namelist()}
        for entry, value in (('res/xml/mac_screen_network.xml', b'recompiled differently'),
                             ('assets/clipboard-connection.json', b'{}')):
            with self.subTest(entry=entry), self.assertRaises(hosted.Refused):
                hosted.check_apk(self.write_apk(apk_bytes(dict(entries, **{entry: value})), entry.replace('/', '_')), cfg)
        missing = {n: v for n, v in entries.items() if n != 'res/raw/mac_screen_cert.pem'}
        with self.assertRaisesRegex(hosted.Refused, 'exactly once'):
            hosted.check_apk(self.write_apk(apk_bytes(missing), 'missing.apk'), cfg)


class ProbeSigner(unittest.TestCase):
    PAYLOAD = 'synthetic-payload-never-reported'

    def run_probe(self, outcomes):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            name = command[command.index('--secret') + 1]
            target = Path(command[command.index('--out-file') + 1])
            code, stderr = outcomes.get(name, (0, ''))
            if code == 0:
                target.write_text(self.PAYLOAD)
            return subprocess.CompletedProcess(command, code, '', stderr)
        return hosted.probe_signer(COMMITTED, run=run), calls

    def test_every_pinned_secret_is_read_and_destroyed_without_being_reported(self):
        receipt, calls = self.run_probe({})
        names = list(COMMITTED['host']['integrate']['appSecrets'].values())
        self.assertEqual(receipt['verdict'], 'admitted')
        self.assertEqual(receipt['secrets'], {n: 'read' for n in names})
        self.assertNotIn(self.PAYLOAD, json.dumps(receipt))
        for command in calls:
            self.assertEqual(command[:4], ['gcloud', 'secrets', 'versions', 'access'])
            self.assertEqual(command[4], COMMITTED['host']['integrate']['secretVersion'])
            self.assertEqual(command[command.index('--project') + 1], COMMITTED['host']['integrate']['project'])
            self.assertFalse(Path(command[command.index('--out-file') + 1]).exists(), 'removed at once')
        self.assertEqual(sorted(c[c.index('--secret') + 1] for c in calls), sorted(names))

    def test_failures_are_named_by_category_only(self):
        names = list(COMMITTED['host']['integrate']['appSecrets'].values())
        stderr = {names[0]: (1, 'ERROR: (gcloud.secrets.versions.access) ... The given credential is rejected by the '
                                'attribute condition. secret-text'),
                  names[1]: (1, 'ERROR: PERMISSION_DENIED: Permission denied on resource'),
                  names[2]: (1, 'ERROR: NOT_FOUND: Secret not found')}
        receipt, _ = self.run_probe(stderr)
        self.assertEqual(receipt['verdict'], 'refused')
        self.assertEqual([receipt['secrets'][n] for n in names],
                         ['token-exchange-refused', 'permission-denied', 'not-found', 'read'])
        self.assertNotIn('secret-text', json.dumps(receipt))


class MemoryLedger:
    """The real record format and parse(), held in memory: a ref is created only when absent."""

    def __init__(self, store=STORE):
        self.store, self.tags = store, {}

    def head(self):
        return 'f' * 40

    def read(self, package):
        return store_module.parse(self.store, self.tags, package)

    def create(self, ref, record, head):
        if ref in self.tags:
            return False
        self.tags[ref] = ('tag', store_module.message(record))
        return True

    def ledger(self):
        return store_module.Ledger(self.store, self, self, sleep=lambda s: None)


class Reservation(unittest.TestCase):
    def setUp(self):
        self.repo, self.home = trl.app_repo(self)
        self.cfg = json.loads(json.dumps(COMMITTED))
        self.cfg['reservation']['store'] = STORE
        self.memory = MemoryLedger()
        self.out = self.home/'reservation'
        self.env = {'GITHUB_SHA': self.repo.source, 'GITHUB_WORKFLOW_REF': REF, 'GITHUB_RUN_ID': '4300',
                    'GITHUB_RUN_ATTEMPT': '1'}

    def add_floor(self, through, run='4100'):
        self.memory.ledger().record_floor(store_module.floor('dev.pixel.voice', through, 'e' * 64,
                                                             {'workflowRef': REF, 'runId': run, 'runAttempt': '1'}, STORE))

    def reserve(self, *releases, env=None):
        listing = trl.app_listing(*releases)
        return hosted.reserve(self.out, env or self.env, self.cfg, self.memory.ledger(), listing,
                              clock=lambda: trl.NOW, repo=self.repo.path)

    def test_no_builder_floor_means_no_reservation(self):
        with self.assertRaises(hosted.SetupMissing) as caught:
            self.reserve()
        self.assertIn('decision D2', caught.exception.missing[0])
        self.assertEqual(self.memory.tags, {})
        self.assertFalse(self.out.exists())

    def test_a_fresh_version_clears_the_floor_and_is_held_for_this_run(self):
        self.add_floor(368)
        result = self.reserve()
        self.assertEqual((result['app_skip'], result['version_code']), ('false', '369'))
        record = json.loads((self.out/'reservation.json').read_text())
        allocation = json.loads((self.out/'allocation.json').read_text())
        self.assertEqual(self.memory.read('dev.pixel.voice')['reservations'], {369: record})
        self.assertEqual(record['owner'], {'workflowRef': REF, 'runId': '4300', 'runAttempt': '1'})
        self.assertEqual((record['sourceSha'], record['candidateSha'], record['allocationSha256']),
                         (self.repo.source, allocation['candidateSha'], allocation['allocationSha256']))
        self.assertEqual(allocation['versionName'], '369-hosted-beta')
        self.assertEqual(hosted.ALLOC.derive_app(allocation, self.repo.path), record['candidateSha'])
        self.assertEqual(json.loads(result['apps']), [['dev.pixel.voice', 369]])

    def test_every_other_consumed_version_is_cleared_too(self):
        self.add_floor(300)
        # A published app tag, a draft, another run's ledger reservation and a committed Frankel allocation record.
        other = store_module.reservation('dev.pixel.voice', 330, 'a' * 40, 'b' * 40, 'c' * 64,
                                         {'workflowRef': REF, 'runId': '4200', 'runAttempt': '1'}, STORE)
        self.memory.ledger().reserve(other)
        frankel = trl.allocate(self.repo)
        self.repo.write(f'os/latest-beta/frankel-{frankel["build"]}-allocation.json', json.dumps(frankel))
        self.repo.source = self.repo.commit('Record a Frankel allocation')
        voice = dict(frankel['apps'])['dev.pixel.voice']
        self.env['GITHUB_SHA'] = self.repo.source
        result = self.reserve({'tag_name': 'rose-agent-325', 'draft': False, 'prerelease': True},
                              {'tag_name': 'rose-agent-326', 'draft': True, 'prerelease': True})
        self.assertEqual(int(result['version_code']), max(330, 326, voice) + 1)

    def test_sources_already_published_through_the_ledger_skip(self):
        self.add_floor(368)
        first = self.reserve()
        # rose-agent-369 is now published from these same sources: a later run has nothing to build.
        self.out = self.home/'second'
        result = self.reserve({'tag_name': 'rose-agent-369', 'draft': False, 'prerelease': True},
                              env=dict(self.env, GITHUB_RUN_ID='4301'))
        self.assertEqual(result['app_skip'], 'true')
        self.assertEqual(sorted(self.memory.read('dev.pixel.voice')['reservations']), [int(first['version_code'])])
        self.assertFalse((self.out/'reservation.json').exists())

    def test_a_version_another_run_holds_first_is_never_shared(self):
        self.add_floor(368)

        class Racing(MemoryLedger):
            def create(inner, ref, record, head):  # another run creates the same ref just before this one
                inner.tags[ref] = ('tag', store_module.message(dict(store_module.reservation(
                    'dev.pixel.voice', record['versionCode'], 'a' * 40, 'b' * 40, 'c' * 64,
                    {'workflowRef': REF, 'runId': '4999', 'runAttempt': '1'}, STORE))))
                return False
        racing = Racing()
        racing.tags = dict(self.memory.tags)
        with self.assertRaisesRegex(store_module.Refused, 'already held by another record'):
            hosted.reserve(self.out, self.env, self.cfg, racing.ledger(), trl.app_listing(), clock=lambda: trl.NOW,
                           repo=self.repo.path)
        self.assertFalse((self.out/'reservation.json').exists())

    def test_bind_and_confirm_admit_only_this_runs_held_reservation(self):
        self.add_floor(368)
        self.reserve()
        exported = hosted.bind(self.out, self.env, self.cfg, self.memory.ledger(), repo=self.repo.path)
        record = json.loads((self.out/'reservation.json').read_text())
        self.assertEqual(exported, {'ROSE_CANDIDATE_SHA': record['candidateSha'], 'APPS': '[["dev.pixel.voice",369]]',
                                    'ROSE_APP_RESERVATION': str((self.out/'reservation.json').resolve())})
        self.assertEqual(hosted.confirm(self.out/'reservation.json', self.env, self.cfg, self.memory.ledger()), record)
        for label, env in (('a re-run attempt', dict(self.env, GITHUB_RUN_ATTEMPT='2')),
                           ('another run', dict(self.env, GITHUB_RUN_ID='4301')),
                           ('another dispatch commit', dict(self.env, GITHUB_SHA='0' * 40))):
            with self.subTest(label), self.assertRaises(hosted.Refused):
                hosted.bind(self.out, env, self.cfg, self.memory.ledger(), repo=self.repo.path)
        empty = MemoryLedger()
        with self.assertRaises(store_module.Refused):
            hosted.bind(self.out, self.env, self.cfg, empty.ledger(), repo=self.repo.path)
        with self.assertRaises(store_module.Refused):
            hosted.confirm(self.out/'reservation.json', self.env, self.cfg, empty.ledger())
        allocation = json.loads((self.out/'allocation.json').read_text())
        (self.out/'allocation.json').write_text(json.dumps(dict(allocation, versionCode=370)))
        with self.assertRaises(Exception):
            hosted.bind(self.out, self.env, self.cfg, self.memory.ledger(), repo=self.repo.path)

    def test_the_cli_refuses_before_any_write_without_a_token(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/agent-hosted.py'), 'reserve', '--out',
                                 str(self.home/'cli')], capture_output=True, text=True,
                                env=dict(os.environ, PIXEL_OTA_TOKEN=''))
        self.assertEqual((result.returncode, json.loads(result.stderr)['verdict']), (4, 'SETUP_MISSING'))
        self.assertFalse((self.home/'cli').exists())


AGENT_JOBS = ('agent-signer', 'agent-reserve', 'agent-build', 'agent-publisher')


def step(job, name):
    found = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert found, name
    return found[0]


class Workflow(unittest.TestCase):
    def setUp(self):
        self.jobs = shape.jobs(WORKFLOW)

    def test_the_two_modes_are_manual_options_of_the_agent_lane(self):
        self.assertEqual(shape.modes()[-2:], ['agent-hosted', 'agent-hosted-probe'])
        lanes = json.loads((ROOT/'os/release-pipeline/lanes/capacity.json').read_text())['lanes']
        self.assertEqual(lanes['agent']['modes'], ['app-only', 'agent-hosted', 'agent-hosted-probe'])
        self.assertLessEqual({'agent-hosted', 'agent-hosted-probe'}, shape.main_check_exempt_modes())

    def test_every_agent_job_is_github_hosted_and_never_touches_the_builder(self):
        for name in AGENT_JOBS:
            job = self.jobs[name]
            self.assertIn('    runs-on: ubuntu-24.04\n', job, name)
            for absent in ('self-hosted', 'start-builder', 'stop-builder', 'rose-actions', 'pixelbuild', 'lockPath',
                           'release-allocate.py latest', 'release-allocate.py app '):
                self.assertNotIn(absent, job, (name, absent))
        controller = self.jobs['controller']
        self.assertIn("    if: needs.guard.outputs.mustang_skip != 'true' && inputs.mode != 'agent-hosted' && "
                      "inputs.mode != 'agent-hosted-probe'\n", controller)
        self.assertNotIn('agent-', ' '.join(shape.cleanup_needs()))  # cleanup follows the controller, which never runs

    def test_the_signer_probe_runs_first_and_reads_only_into_ram(self):
        job = self.jobs['agent-signer']
        self.assertIn("    needs: guard\n    if: inputs.mode == 'agent-hosted' || inputs.mode == 'agent-hosted-probe'\n", job)
        self.assertIn('      id-token: write\n', job)
        names = re.findall(r'(?m)^      - name: (.+)$', job)
        self.assertEqual(names, ['Signer access for the probe',
                                 'The four app signing secrets are readable (read into RAM, destroyed, never printed)',
                                 'Drop signer access', 'Signer probe receipt'])
        self.assertIn('run: python3 scripts/agent-hosted.py probe-signer --out "$RUNNER_TEMP/agent-signer-probe.json"', job)
        self.assertNotIn('create-github-app-token', job)

    def test_only_the_reserve_and_publisher_jobs_mint_the_publisher_token(self):
        for name in AGENT_JOBS:
            minted = 'create-github-app-token' in self.jobs[name]
            self.assertEqual(minted, name in ('agent-reserve', 'agent-publisher'), name)
        reserve = self.jobs['agent-reserve']
        self.assertIn("    needs: agent-signer\n    if: inputs.mode == 'agent-hosted'\n", reserve)
        self.assertNotIn('id-token', reserve)
        self.assertIn('          repositories: pixel-ota\n          permission-contents: write\n', reserve)
        self.assertIn('run: python3 scripts/agent-hosted.py reserve --out "$RUNNER_TEMP/agent-reservation"', reserve)
        build = self.jobs['agent-build']
        self.assertNotIn('PIXEL_OTA', build)
        self.assertNotIn('secrets.', build)

    def test_the_build_binds_the_reservation_and_signs_only_in_agent_hosted(self):
        job = self.jobs['agent-build']
        self.assertIn("      !cancelled() && needs.agent-signer.result == 'success' && (inputs.mode == 'agent-hosted-probe' ||\n"
                      "      (inputs.mode == 'agent-hosted' && needs.agent-reserve.result == 'success' && "
                      "needs.agent-reserve.outputs.app_skip == 'false'))\n", job)
        for name in ('Reservation records', 'Bind the reservation (the same candidate, owned by this run, held by the ledger)',
                     'Signer access for app signing', 'Build, sign and verify Rose Agent (hosted)', 'Private Actions artifact'):
            self.assertIn("        if: inputs.mode == 'agent-hosted'\n", step(job, name), name)
        self.assertIn("        if: inputs.mode == 'agent-hosted-probe'\n",
                      step(job, 'Unsigned Agent-only build of the dispatch commit (probe)'))
        self.assertIn("        if: always() && inputs.mode == 'agent-hosted'\n", step(job, 'Drop signer access'))
        sign = step(job, 'Build, sign and verify Rose Agent (hosted)')
        self.assertIn('scripts/host-app-release.py --hosted --portable-inputs "$RUNNER_TEMP/agent-portable-inputs.json"', sign)
        self.assertIn('scripts/agent-hosted.py check-apk', sign)
        self.assertIn('ANDROID_SDK_ROOT="$sdk" bash scripts/setup-android-sdk.sh',
                      step(job, 'Pinned Android SDK (scripts/setup-android-sdk.sh, sha256-checked archives)'))
        self.assertIn('scripts/agent-hosted.py public-inputs', step(job, 'Pinned public connection inputs (hosted.json publicInputs)'))
        self.assertIn('python3 scripts/host-app-release.py --check --hosted --portable-inputs',
                      step(job, "App's portable toolchain record (portable-build.py prepare and inputs)"))

    def test_the_publisher_confirms_the_ledger_and_publishes_only_with_app_publish(self):
        job = self.jobs['agent-publisher']
        self.assertIn("      !cancelled() && inputs.mode == 'agent-hosted' && inputs.app_publish && "
                      "needs.agent-build.result == 'success'\n", job)
        names = re.findall(r'(?m)^      - name: (.+)$', job)
        self.assertEqual(names[:2], ["The ledger still holds this run's reservation", 'Mint pixel-ota publisher token'])
        self.assertIn('python3 scripts/app_release_publisher.py --hosted --reservation '
                      '"$RUNNER_TEMP/agent-reservation/reservation.json" --artifact-dir "$RUNNER_TEMP/app-artifact"', job)


class Guard(unittest.TestCase):
    def run_guard(self, mode, **change):
        block = re.search(r'(?ms)^        run: \|\n(.*)', step(shape.jobs(WORKFLOW)['guard'], 'Candidate is an exact commit on main'))[1]
        lines = []
        for line in block.splitlines():  # the run block ends at its first line indented less than its own
            if line.strip() and not line.startswith(' ' * 10):
                break
            lines.append(line[10:])
        script = 'set -euo pipefail\n' + '\n'.join(lines) + '\n'
        head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'origin/main'], capture_output=True, text=True)
        sha = head.stdout.strip() if head.returncode == 0 else 'f' * 40
        env = {'PATH': os.environ['PATH'], **shape.dispatch_env(WORKFLOW, MODE=mode, PROMOTE='false', APP_PUBLISH='false',
                                                                 ALPHA_RECOVER='false', GITHUB_SHA=sha, GITHUB_RUN_ATTEMPT='1'),
               **change}
        return subprocess.run(['bash', '-c', script], env=env, capture_output=True, cwd=ROOT).returncode

    def test_bare_dispatches_are_admitted_and_every_pin_refused(self):
        if subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--verify', '-q', 'origin/main'], capture_output=True).returncode:
            self.skipTest('no origin/main in this checkout')
        for mode in ('agent-hosted', 'agent-hosted-probe'):
            self.assertEqual(self.run_guard(mode), 0, mode)
            for change in ({'CANDIDATE_SHA': 'a' * 40}, {'BUILD_NUMBER': '2026110801'}, {'BUILD_TIMESTAMP': '1793000000'},
                           {'APPS': '[["dev.pixel.voice",369]]'}, {'APPS': 'auto'}, {'PROMOTE': 'true'},
                           {'RESUME_STAGE': 'sign'}, {'CAMPAIGN': 'start'}, {'ROSE_RANGO_PUBLISH': 'true'},
                           {'BOOTSTRAP_PROFILE': 'fold-rango-v1'}, {'REUSE_BINDING': '{}'}, {'ALPHA_RECOVER': 'true'},
                           {'BLAZER_PREP_STAGE': 'source'}, {'MUSTANG_PUBLISH_OTA': 'true'}, {'GITHUB_RUN_ATTEMPT': '2'},
                           {'GITHUB_SHA': '0' * 40}):
                with self.subTest(mode=mode, change=change):
                    self.assertNotEqual(self.run_guard(mode, **change), 0)
        self.assertEqual(self.run_guard('agent-hosted', APP_PUBLISH='true'), 0)
        self.assertNotEqual(self.run_guard('agent-hosted-probe', APP_PUBLISH='true'), 0)


if __name__ == '__main__':
    unittest.main()
