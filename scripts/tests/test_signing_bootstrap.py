"""OCT29-INFRA-S005: scripts/signing-bootstrap.py and its guarded os-release.yml mode.

Only mock executors run here: no key is generated, no secret is read or written, no IAM call is made and nothing is
dispatched. The checked-in fold-rango-v1 profile is resource-bound but not ready (incomplete inventory, unapproved
tools), and models-v0 (S012) has an unmeasured keytool, so both stop before any side effect. A test-only READY copy of the fold profile (fake
tool paths and digests, complete inventory, future access) exercises the state machine with a fake archive whose
certificate and AVB bytes are random. Fixture limits: the fake generator writes files the test chose; real
generation, real gcloud responses and real permission outcomes are not exercised."""
import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
MODULE = SCRIPTS/'signing-bootstrap.py'
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()


def load(source=None):
    spec = importlib.util.spec_from_file_location('signing_bootstrap', MODULE)
    module = importlib.util.module_from_spec(spec)
    if source is None:
        spec.loader.exec_module(module)
    else:
        exec(compile(source, str(MODULE), 'exec'), module.__dict__)
    return module


sb = load()
PROFILES = sb.load_profiles()
NOW = datetime(2026, 10, 29, 21, 30, tzinfo=timezone.utc)
SHA = 'a' * 40
PASSPHRASE = b'correct horse battery staple synthetic\n'
FRANKEL = {'otaCertificateSha256': 'f' * 64, 'avbPublicKeySha256': 'e' * 64}


# The checked-in, source-bound recipe (S010: make_key per signing key, then the AVB steps). Tests only ever run it
# through MockRunner, which writes synthetic files; no generator, openssl or avbtool is executed.
SYNTHETIC_RECIPE = PROFILES['profiles']['fold-rango-v1']['generation']['recipe']
CERT_STEPS = sum(1 for s in SYNTHETIC_RECIPE if s['tool'] == 'generator')
ENCRYPTED_DER = bytes([0x30, 0x10, 0x30, 0x0e]) + b'synthetic-encrypted'
ENCRYPTED_PEM = b'-----BEGIN ENCRYPTED PRIVATE KEY-----\nc3ludGhldGlj\n-----END ENCRYPTED PRIVATE KEY-----\n'


def key_member(name, seed=b''):
    if name.endswith('.pk8'):
        return ENCRYPTED_DER + name.encode() + seed
    if name == 'avb.pem':
        return ENCRYPTED_PEM
    if name.endswith('.x509.pem'):
        return cert_pem(name.encode() + seed)
    return b'synthetic ' + name.encode() + seed


def ready_profiles():
    profiles = copy.deepcopy(PROFILES)
    fold = profiles['profiles']['fold-rango-v1']
    fold['keyInventory']['complete'] = True
    # These suites exercise the measured-installed tool mode; the same-run local-proof mode (OCT30-INFRA-S016) that the
    # checked-in Fold profile uses is covered by scripts/tests/test_fold_bootstrap_proof.py.
    fold.pop('localProof')
    for name, tool in fold['tools'].items():
        tool['installed'] = {'path': f'/opt/synthetic/{name}', 'sha256': hashlib.sha256(name.encode()).hexdigest()}
    fold['access'].update(addVersionExpires='2027-01-01T00:00:00Z', accessorExpires='2027-01-01T00:00:00Z')
    return profiles


READY = ready_profiles()
FOLD = READY['profiles']['fold-rango-v1']


def cert_pem(seed):
    body = base64.encodebytes(hashlib.sha512(seed).digest() * 4).decode()
    return f'-----BEGIN CERTIFICATE-----\n{body}-----END CERTIFICATE-----\n'.encode()


def archive_bytes(members=None, prefix='rango/'):
    if members is None:
        members = {name: key_member(name) for name in FOLD['keyInventory']['requiredByConsumer']}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name, data in members.items():
            if isinstance(data, tarfile.TarInfo):
                tar.addfile(data)
                continue
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class MockExecutor:
    def __init__(self, archive=None, passphrase=PASSPHRASE, states=None, adds=None, tools=None, extra_output=None,
                 passphrase_mode=0o600):
        self.calls = []
        self.archive = archive_bytes() if archive is None else archive
        self.passphrase, self.passphrase_mode, self.extra_output = passphrase, passphrase_mode, extra_output
        self.states = states or {}
        self.adds = list(adds or ['1', '1'])
        self.tools = tools or {}

    def tool_sha256(self, path):
        self.calls.append(('tool', path))
        name = Path(path).name
        return self.tools.get(name, hashlib.sha256(name.encode()).hexdigest())

    def secret_state(self, project, secret):
        self.calls.append(('metadata', secret))
        state = self.states.get(secret, {'exists': True, 'versions': []})
        if isinstance(state, Exception):
            raise state
        return state

    def generate(self, workspace, profile):
        self.calls.append(('generate',))
        for name, data, mode in (('keys.tar.gz', self.archive, 0o600), ('passphrase', self.passphrase, self.passphrase_mode)):
            fd = os.open(workspace/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            os.chmod(workspace/name, mode)
        if self.extra_output:
            (workspace/self.extra_output).write_bytes(b'x')

    def add_version(self, project, secret, file):
        self.calls.append(('add', secret))
        outcome = self.adds.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kinds(self):
        return [c[0] for c in self.calls]


class Fixture:
    def __init__(self, test):
        self.tmp = Path(tempfile.mkdtemp(prefix='bootstrap-test-'))
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp/'state'
        self.state.mkdir()
        self.lock = self.tmp/'frankel-build.lock'
        self.created = []

    @contextlib.contextmanager
    def workspace(self, remove=True):
        path = Path(tempfile.mkdtemp(prefix='ws-', dir=self.tmp))
        os.chmod(path, 0o700)
        self.created.append(path)
        try:
            yield path
        finally:
            if remove:
                shutil.rmtree(path)

    def run(self, executor, profile='fold-rango-v1', profiles=READY, remove=True, now=NOW):
        return sb.run(profile, profiles, executor, self.state, self.lock, SHA, now,
            workspace=lambda: self.workspace(remove), frankel=FRANKEL)

    def journal(self, profile='fold-rango-v1'):
        path = self.state/f'{profile}.json'
        return json.loads(path.read_text()) if path.exists() else None


def codes(receipt):
    return [r['code'] for r in receipt['reasons']]


class ZeroSideEffects(unittest.TestCase):
    def test_checked_in_profiles_stop_before_anything(self):
        # models-v0 is source-ready (pinned-tree keytool); its host-side refusals are in test_models_signing_bootstrap.py.
        for name, expected in (('fold-rango-v1', {'proof-missing'}),
                               ('rango', {'profile-unknown'}), ('none', {'profile-unknown'})):
            f, ex = Fixture(self), MockExecutor()
            receipt, _ = f.run(ex, name, PROFILES)
            self.assertEqual(receipt['status'], 'blocked', name)
            self.assertTrue(expected <= set(codes(receipt)), (name, codes(receipt)))
            self.assertEqual(ex.calls, [], name)
            self.assertFalse(f.lock.exists(), name)
            self.assertEqual(f.created, [], name)
            self.assertIsNone(f.journal(name), name)

    def test_fold_tools_are_each_unapproved_and_models_names_its_missing_resources(self):
        reasons = sb.readiness('fold-rango-v1', PROFILES, NOW)
        self.assertEqual(reasons, [])  # the four tools are proven per run (scripts/tests/test_fold_bootstrap_proof.py)
        self.assertEqual([c for c, d in sb.readiness('fold-rango-v1', ready_profiles(), NOW)], [])
        self.assertEqual(sb.readiness('models-v0', PROFILES, NOW), [])  # pinned-tree basis complete in source
        self.assertEqual(sb.readiness('fold-rango-v1', READY, NOW), [])

    def test_expiring_or_missing_access_stops_before_anything(self):
        for change in ({'addVersionExpires': '2026-10-29T21:45:00Z'}, {'accessorExpires': '2026-10-29T20:00:00Z'},
                       {'addVersionExpires': 'tomorrow'}):
            profiles = ready_profiles()
            profiles['profiles']['fold-rango-v1']['access'].update(change)
            f, ex = Fixture(self), MockExecutor()
            receipt, _ = f.run(ex, profiles=profiles)
            self.assertTrue({'access-expired', 'access-unknown'} & set(codes(receipt)), change)
            self.assertEqual(ex.calls, [])
        # The real add-only grant expires 2026-10-30T19:45:00Z: within the margin after 19:15.
        late = datetime(2026, 10, 30, 19, 20, tzinfo=timezone.utc)
        profiles = ready_profiles()
        profiles['profiles']['fold-rango-v1']['access']['addVersionExpires'] = PROFILES['profiles']['fold-rango-v1']['access']['addVersionExpires']
        self.assertIn('access-expired', dict(sb.readiness('fold-rango-v1', profiles, late)))

    def test_untrusted_tool_paths_are_not_approved(self):
        for path in ('relative/tool', '/opt/../tmp/tool', None):
            profiles = ready_profiles()
            profiles['profiles']['fold-rango-v1']['tools']['generator']['installed']['path'] = path
            self.assertIn('tool-unapproved', dict(sb.readiness('fold-rango-v1', profiles, NOW)), path)

    def test_tool_identity_mismatch_stops_before_metadata(self):
        f, ex = Fixture(self), MockExecutor(tools={'openssl': '0' * 64})
        receipt, _ = f.run(ex)
        self.assertEqual(codes(receipt), ['tool-identity'])
        self.assertEqual(set(ex.kinds()), {'tool'})
        self.assertEqual(f.created, [])

    def test_lock_held_stops_before_tools(self):
        import fcntl
        f, ex = Fixture(self), MockExecutor()
        fd = os.open(f.lock, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        receipt, _ = f.run(ex)
        self.assertEqual(codes(receipt), ['lock-held'])
        self.assertEqual(ex.calls, [])


class Metadata(unittest.TestCase):
    def check(self, states, code):
        f, ex = Fixture(self), MockExecutor(states=states)
        receipt, _ = f.run(ex)
        self.assertEqual(receipt['status'], 'blocked')
        self.assertIn(code, codes(receipt))
        self.assertNotIn('generate', ex.kinds())
        self.assertNotIn('add', ex.kinds())
        self.assertEqual(f.created, [])
        self.assertIsNone(f.journal())
        return receipt

    def test_occupied_missing_unavailable_and_uncertain(self):
        self.check({'PIXEL_OS_SIGNING_PASSPHRASE_RANGO_V1': {'exists': True, 'versions': ['1']}}, 'secret-occupied')
        self.check({'PIXEL_OS_SIGNING_KEYS_RANGO_V1': {'exists': False, 'versions': []}}, 'secret-missing')
        receipt = self.check({'PIXEL_OS_SIGNING_KEYS_RANGO_V1': sb.Unavailable('gcloud exited 1')}, 'metadata-unavailable')
        self.assertIn('secretmanager.versions.list', receipt['reasons'][0]['detail'])
        self.check({'PIXEL_OS_SIGNING_KEYS_RANGO_V1': {'exists': True}}, 'metadata-uncertain')
        self.check({'PIXEL_OS_SIGNING_KEYS_RANGO_V1': None}, 'metadata-uncertain')


class Generation(unittest.TestCase):
    def failed(self, executor, code):
        f = Fixture(self)
        receipt, _ = f.run(executor)
        self.assertEqual(receipt['status'], 'failed', codes(receipt))
        self.assertIn(code, codes(receipt))
        self.assertNotIn('add', executor.kinds())
        self.assertEqual(receipt['cleanup'], {'workspaceCreated': True, 'workspaceRemoved': True})
        self.assertFalse(any(p.exists() for p in f.created))
        self.assertEqual(f.journal()['status'], 'generating')  # a later attempt is refused until reviewed
        return receipt

    def test_malicious_and_incomplete_archives(self):
        def link(name, kind=tarfile.SYMTYPE):
            info = tarfile.TarInfo(name)
            info.type, info.linkname = kind, '/etc/passwd'
            return info
        base = {n: key_member(n) for n in FOLD['keyInventory']['requiredByConsumer']}
        cases = {
            'absolute': archive_bytes(base, prefix='/abs/'),
            'traversal': archive_bytes(base, prefix='../'),
            'symlink': archive_bytes(dict(base, **{'evil.pk8': link('rango/evil.pk8')})),
            'hardlink': archive_bytes(dict(base, **{'evil': link('rango/evil', tarfile.LNKTYPE)})),
            'missing bluetooth': archive_bytes({k: v for k, v in base.items() if not k.startswith('bluetooth')}),
            'unexpected member': archive_bytes(dict(base, **{'other.pk8': key_member('other.pk8')})),
            'nested too deep': archive_bytes(base, prefix='a/b/'),
            'not gzip': b'plain bytes',
            'bad certificate': archive_bytes(dict(base, **{'releasekey.x509.pem': b'no pem'})),
            'wrong device directory': archive_bytes(base, prefix='frankel/'),
            'plain pk8': archive_bytes(dict(base, **{'platform.pk8': bytes([0x30, 0x10, 0x02, 0x01, 0x00]) + b'plain'})),
            'plain avb.pem': archive_bytes(dict(base, **{'avb.pem': b'-----BEGIN PRIVATE KEY-----\nx\n'})),
        }
        for name, data in cases.items():
            with self.subTest(name):
                code = {'missing bluetooth': 'archive-inventory', 'unexpected member': 'archive-inventory',
                    'plain pk8': 'archive-unencrypted', 'plain avb.pem': 'archive-unencrypted'}.get(name, 'archive-unsafe')
                self.failed(MockExecutor(archive=data), code)

    def test_output_projection_and_permissions(self):
        self.failed(MockExecutor(extra_output='debug.log'), 'unexpected-output')
        self.failed(MockExecutor(passphrase_mode=0o644), 'unexpected-output')
        self.failed(MockExecutor(passphrase=b'\n'), 'passphrase-empty')

    def test_frankel_identity_is_refused(self):
        archive = archive_bytes()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            avb = tar.extractfile('rango/avb_pkmd.bin').read()
        frankel = dict(FRANKEL, avbPublicKeySha256=hashlib.sha256(avb).hexdigest())
        f, ex = Fixture(self), MockExecutor(archive=archive)
        receipt, _ = sb.run('fold-rango-v1', READY, ex, f.state, f.lock, SHA, NOW, workspace=f.workspace, frankel=frankel)
        self.assertEqual((receipt['status'], codes(receipt)), ('failed', ['identity-collision']))
        self.assertNotIn('add', ex.kinds())


class Import(unittest.TestCase):
    def test_complete_receipt_is_public_only(self):
        f, ex = Fixture(self), MockExecutor(adds=['1', '1'])
        receipt, private = f.run(ex)
        self.assertEqual(receipt['status'], 'complete', receipt['reasons'])
        self.assertEqual(ex.kinds()[-3:], ['generate', 'add', 'add'])
        self.assertEqual({r: e['committedVersion'] for r, e in receipt['secrets'].items()}, {'keys': '1', 'passphrase': '1'})
        self.assertEqual(receipt['payloadReadback'], 'not performed')
        self.assertEqual(receipt['cleanup'], {'workspaceCreated': True, 'workspaceRemoved': True})
        out = sb.public_receipt(receipt, private)
        self.assertEqual(set(out) - sb.RECEIPT_KEYS, set())
        text = json.dumps(out)
        self.assertNotIn(PASSPHRASE.strip().decode(), text)
        self.assertNotIn(PASSPHRASE.hex(), text)
        self.assertNotIn('synthetic releasekey.pk8', text)
        self.assertTrue(all('<workspace>/' in op for op in out['operations']))
        self.assertEqual(out['publicIdentity']['members'], sorted(FOLD['keyInventory']['requiredByConsumer']))
        self.assertEqual(f.journal()['status'], 'complete')
        # A second run never regenerates or adds a duplicate version.
        again = MockExecutor()
        receipt2, _ = f.run(again)
        self.assertEqual((codes(receipt2), again.calls, receipt2['recoveryNeeded']), (['previous-attempt'], [], False))

    def test_partial_import_is_recovery_needed_not_complete(self):
        f, ex = Fixture(self), MockExecutor(adds=['1', sb.AddFailed('permission denied')])
        receipt, _ = f.run(ex)
        self.assertEqual(receipt['status'], 'recovery-needed')
        self.assertTrue(receipt['recoveryNeeded'])
        self.assertEqual(receipt['secrets']['keys'], {'name': 'PIXEL_OS_SIGNING_KEYS_RANGO_V1', 'committedVersion': '1', 'state': 'committed'})
        self.assertEqual(receipt['secrets']['passphrase']['state'], 'not-committed')
        journal = f.journal()
        self.assertEqual((journal['status'], journal['secrets']['keys']['committedVersion']), ('recovery-needed', '1'))
        self.assertEqual(ex.kinds().count('add'), 2)  # no retry, rollback or delete call exists
        nxt = MockExecutor()
        receipt2, _ = f.run(nxt)
        self.assertEqual((codes(receipt2), nxt.calls, receipt2['recoveryNeeded']), (['previous-attempt'], [], True))

    def test_uncertain_first_add_stops_without_second(self):
        for outcome in (sb.Uncertain('timeout'), 'projects/x/secrets/y/versions/1', ''):
            with self.subTest(outcome=outcome):
                f, ex = Fixture(self), MockExecutor(adds=[outcome, '1'])
                receipt, _ = f.run(ex)
                self.assertEqual(receipt['status'], 'recovery-needed')
                self.assertEqual(ex.kinds().count('add'), 1)
                self.assertEqual(receipt['secrets']['keys']['state'], 'uncertain')
                self.assertEqual(f.journal()['status'], 'recovery-needed')

    def test_definite_first_failure_commits_nothing(self):
        f, ex = Fixture(self), MockExecutor(adds=[sb.AddFailed('denied')])
        receipt, _ = f.run(ex)
        self.assertEqual((receipt['status'], codes(receipt)), ('failed', ['add-failed']))
        self.assertEqual(f.journal()['status'], 'failed-before-commit')
        self.assertFalse(receipt['recoveryNeeded'])

    def test_cancellation_during_import_records_recovery_and_cleans_up(self):
        f, ex = Fixture(self), MockExecutor(adds=['1', KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            f.run(ex)
        self.assertEqual(f.journal()['status'], 'recovery-needed')
        self.assertEqual(f.journal()['secrets']['keys']['committedVersion'], '1')
        self.assertFalse(any(p.exists() for p in f.created))

    def test_cleanup_failure_is_reported_and_not_complete(self):
        f, ex = Fixture(self), MockExecutor()
        receipt, _ = f.run(ex, remove=False)
        self.assertEqual(receipt['status'], 'cleanup-failed')
        self.assertEqual(receipt['cleanup'], {'workspaceCreated': True, 'workspaceRemoved': False})
        self.assertIn('cleanup-failed', codes(receipt))

    def test_redaction_refuses_private_values(self):
        receipt = {'status': 'complete', 'reasons': [{'code': 'x', 'detail': PASSPHRASE.strip().decode()}]}
        with self.assertRaises(sb.Blocked):
            sb.public_receipt(receipt, [PASSPHRASE.strip()])
        with self.assertRaises(sb.Blocked):
            sb.public_receipt({'reasons': [PASSPHRASE.strip().hex()]}, [PASSPHRASE.strip()])
        self.assertNotIn('environment', sb.public_receipt({'environment': {'A': 'B'}, 'status': 'blocked'}))


class Production(unittest.TestCase):
    def fake_gcloud(self, script):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        path = tmp/'gcloud'
        path.write_text('#!/bin/bash\n' + script)
        path.chmod(0o700)
        return str(path), tmp

    def test_generation_is_not_implemented_and_add_failures_are_uncertain(self):
        with self.assertRaises(sb.Blocked) as blocked:
            sb.GcloudExecutor().generate(Path('/nonexistent'), PROFILES['profiles']['fold-rango-v1'])
        self.assertEqual(blocked.exception.code, 'tool-unapproved')  # unmeasured tools: before any file or process
        gcloud, tmp = self.fake_gcloud('echo "$@" > "$(dirname "$0")/argv"; exit 1\n')
        data = tmp/'passphrase'
        data.write_bytes(PASSPHRASE)
        with self.assertRaises(sb.Uncertain):
            sb.GcloudExecutor(gcloud).add_version('protocols-465723', 'PIXEL_OS_SIGNING_PASSPHRASE_RANGO_V1', data)
        argv = (tmp/'argv').read_text()
        self.assertIn('--data-file', argv)
        self.assertNotIn(PASSPHRASE.strip().decode(), argv)
        gcloud, _ = self.fake_gcloud('echo not-json\n')
        with self.assertRaises(sb.Uncertain):
            sb.GcloudExecutor(gcloud).add_version('p', 's', data)
        gcloud, _ = self.fake_gcloud('echo \'{"name": "projects/1/secrets/s/versions/1"}\'\n')
        self.assertEqual(sb.GcloudExecutor(gcloud).add_version('p', 's', data), '1')

    def test_metadata_denied_is_unavailable(self):
        gcloud, _ = self.fake_gcloud('echo "PERMISSION_DENIED secretmanager.versions.list" >&2; exit 1\n')
        with self.assertRaises(sb.Unavailable) as error:
            sb.GcloudExecutor(gcloud).secret_state('protocols-465723', 'PIXEL_OS_SIGNING_KEYS_RANGO_V1')
        self.assertNotIn('PERMISSION_DENIED', str(error.exception))  # gcloud stderr is not echoed

    def test_tool_identity_refuses_symlinks(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp/'real').write_bytes(b'tool')
        os.symlink(tmp/'real', tmp/'link')
        self.assertEqual(sb.GcloudExecutor().tool_sha256(str(tmp/'real')), hashlib.sha256(b'tool').hexdigest())
        with self.assertRaises(OSError):
            sb.GcloudExecutor().tool_sha256(str(tmp/'link'))


GUARD_ENV = {'MODE': 'signing-bootstrap', 'BOOTSTRAP_PROFILE': 'fold-rango-v1', 'CANDIDATE_SHA': SHA, 'GITHUB_SHA': SHA,
    'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'ROSE_DEVICE': 'frankel', 'PROMOTE': 'false',
    'APP_PUBLISH': 'false', 'ALPHA_RECOVER': 'false', 'RESUME_STAGE': 'none', 'RESUME_SOURCE_RUN': '',
    'RESUME_UNSIGNED_SHA256': '', 'RESUME_SIGNED_SHA256': '', 'REUSE_BINDING': ''}


class Guard(unittest.TestCase):
    def test_guard_admits_only_an_exact_bootstrap_of_a_ready_profile(self):
        self.assertEqual(sb.guard(GUARD_ENV, READY, NOW), [])
        for change in ({'CANDIDATE_SHA': 'b' * 40}, {'GITHUB_REF': 'refs/heads/feature'}, {'GITHUB_EVENT_NAME': 'push'},
                       {'ROSE_DEVICE': 'rango'}, {'PROMOTE': 'true'}, {'APP_PUBLISH': 'true'}, {'ALPHA_RECOVER': 'true'},
                       {'RESUME_STAGE': 'sign'}, {'RESUME_SOURCE_RUN': '1'}, {'REUSE_BINDING': '{}'},
                       {'BOOTSTRAP_PROFILE': 'none'}, {'BOOTSTRAP_PROFILE': '../x'},
                       {'MODE': 'os-release'}):
            self.assertTrue(sb.guard({**GUARD_ENV, **change}, READY, NOW), change)
        # OCT30-INFRA-S016: the checked-in fold profile is source-ready (tools proven per run on the host); the guard admits
        # the dispatch only before its add-only access margin and never after it.
        self.assertEqual(sb.guard(GUARD_ENV, PROFILES, NOW), [])
        self.assertTrue(sb.guard(GUARD_ENV, PROFILES, datetime(2026, 10, 30, 19, 20, tzinfo=timezone.utc)))
        self.assertEqual(sb.guard({**GUARD_ENV, 'BOOTSTRAP_PROFILE': 'models-v0'}, PROFILES, NOW), [])  # the host proves the tree

    def test_cli_guard_and_preflight_fail_closed_with_checked_in_profiles(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sb.main(['guard'], env=dict(GUARD_ENV, CANDIDATE_SHA='b' * 40)), 4)
            self.assertEqual(sb.main(['preflight', '--profile', 'models-v0', '--recipe-sha', SHA],
                env={'GITHUB_SHA': SHA, 'GCP_SIGNER_SERVICE_ACCOUNT': 'other@x.iam.gserviceaccount.com'}), 4)
            self.assertEqual(sb.main(['run', '--profile', 'rango', '--recipe-sha', SHA, '--state-dir', '/nonexistent/s',
                '--receipt-dir', '/nonexistent/r'], env={'GITHUB_SHA': SHA, 'GCP_SIGNER_SERVICE_ACCOUNT': FOLD['signer']}), 4)
        self.assertIn('candidate must be this workflow commit', out.getvalue())
        self.assertIn('profile-unknown', out.getvalue())
        self.assertIn('GCP_SIGNER_SERVICE_ACCOUNT is not the profile signer', out.getvalue())
        self.assertEqual(sb.preflight('fold-rango-v1', READY, {'GITHUB_SHA': SHA, 'GCP_SIGNER_SERVICE_ACCOUNT': FOLD['signer']}, SHA, NOW), [])
        self.assertTrue(sb.preflight('fold-rango-v1', READY, {'GITHUB_SHA': SHA, 'GCP_SIGNER_SERVICE_ACCOUNT': 'other@x.iam.gserviceaccount.com'}, SHA, NOW))
        self.assertTrue(sb.preflight('fold-rango-v1', READY, {'GITHUB_SHA': 'b' * 40, 'GCP_SIGNER_SERVICE_ACCOUNT': FOLD['signer']}, SHA, NOW))

    def test_plan_lists_exact_operations_and_permissions(self):
        p = sb.plan('fold-rango-v1', PROFILES)
        perms = {op['permission'] for op in p['metadata']}
        self.assertEqual(perms, {'secretmanager.secrets.get', 'secretmanager.versions.list'})
        self.assertEqual({op['permission'] for op in p['add']}, {'secretmanager.versions.add'})
        self.assertIn('gcloud secrets versions list PIXEL_OS_SIGNING_KEYS_RANGO_V1 --project protocols-465723', p['metadata'][1]['operation'])
        models = sb.plan('models-v0', PROFILES)
        self.assertEqual({op['permission'] for op in models['metadata']}, {'secretmanager.secrets.get', 'secretmanager.versions.list'})
        self.assertIn('PIXEL_MODELS_SIGNING_KEYSTORE_V1', models['add'][0]['operation'])


def jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return {m[1]: m[2] for m in re.finditer(r'^  ([\w-]+):\n((?:(?:    .*)?\n)*)', body, re.M)}


JOBS = jobs(WORKFLOW)


class Workflow(unittest.TestCase):
    def test_only_the_bootstrap_job_runs_in_its_mode(self):
        job = JOBS['signing-bootstrap']
        self.assertIn("    needs: controller\n    if: inputs.mode == 'signing-bootstrap'\n", job)
        self.assertIn('runs-on: [self-hosted, roseos-frankel-builder]', job)
        for name, body in JOBS.items():
            if name in ('guard', 'controller', 'cleanup', 'signing-bootstrap'):
                continue
            condition = re.search(r'    if: ([^\n]*(?:\n      [^\n]*)*)\n', body)
            if name == 'publisher':
                self.assertIn('    needs: [builder, signed-builder]\n', body)
                self.assertIn("needs.builder.result == 'success'", condition[1])  # skipped whenever builder is skipped
                continue
            self.assertTrue(condition and "inputs.mode == '" in condition[1], name)
            self.assertNotIn('signing-bootstrap', condition[1], name)
        self.assertIn('inputs.app_publish', JOBS['app-publisher'])
        for word in ('secrets.', 'PIXEL_OTA', 'create-github-app-token', 'os-release-stage', 'host-sign', 'host-app',
                     'release-pipeline.py publish', 'emulator-alpha'):
            self.assertNotIn(word, job, word)

    def test_preflight_precedes_login_and_cleanup_waits(self):
        job = JOBS['signing-bootstrap']
        order = [job.index(s) for s in ('signing-bootstrap.py preflight', 'actions-gcp-login.py login', 'signing-bootstrap.py run',
                 'actions-gcp-login.py logout')]
        self.assertEqual(order, sorted(order))
        self.assertIn("      - name: Drop signer access\n        if: always()\n", job)
        self.assertIn('path: ${{ runner.temp }}/signing-bootstrap/signing-bootstrap-receipt.json', job)
        needs = re.search(r'    needs: \[([^\]]*)\]', JOBS['cleanup'])[1].split(', ')
        self.assertIn('signing-bootstrap', needs)
        self.assertIn("    if: always() && needs.controller.result != 'skipped'\n", JOBS['cleanup'])
        self.assertIn('  group: os-release-frankel\n', WORKFLOW)
        self.assertIn("      - name: Signing-bootstrap guard\n        if: inputs.mode == 'signing-bootstrap'\n", JOBS['guard'])
        self.assertIn('options: [none, fold-rango-v1, models-v0, pro-blazer-v1, fold-rango-factory-v1, pro-blazer-factory-v1]', WORKFLOW)
        self.assertIn('        options: [frankel]\n', WORKFLOW)  # no device is admitted

    def guard_script(self):
        script = re.search(r'- name: Candidate is an exact commit on main\n(?:.*\n)*?        run: \|\n((?: {10}.*\n)+)', JOBS['guard'])[1]
        return '\n'.join(line for line in script.splitlines() if 'git merge-base' not in line)

    def test_shell_guard_binds_bootstrap_and_keeps_other_modes(self):
        base = dict(os.environ, **GUARD_ENV, BUILD_NUMBER='2026102901', BUILD_TIMESTAMP='1793300000',
            APPS='[["dev.pixel.voice", 309]]')
        cases = [({}, True), ({'GITHUB_SHA': 'b' * 40}, False), ({'BOOTSTRAP_PROFILE': 'none'}, False),
                 ({'MODE': 'os-release', 'GITHUB_SHA': 'b' * 40}, False), ({'MODE': 'os-release', 'BOOTSTRAP_PROFILE': 'none'}, True),
                 ({'PROMOTE': 'true'}, False), ({'APP_PUBLISH': 'true'}, False), ({'ROSE_DEVICE': 'rango'}, False),
                 ({'MODE': 'app-only', 'BOOTSTRAP_PROFILE': 'none'}, True), ({'MODE': 'app-only'}, False)]
        for change, ok in cases:
            env = {**base, **change}
            if change.get('BOOTSTRAP_PROFILE') == 'none' and change.get('MODE') != 'signing-bootstrap':
                env.pop('BOOTSTRAP_PROFILE')  # older callers that never set it still pass
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', self.guard_script()], cwd=ROOT, env=env,
                capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, ok, (change, result.stderr))

    def test_actual_ci_runs_these_tests(self):
        ci = (ROOT/'.github/workflows/release-verification.yml').read_text()
        self.assertIn('          python3 scripts/tests/test_signing_bootstrap.py\n', ci)
        self.assertIn('python3 scripts/tests/test_signing_bootstrap.py\n', (ROOT/'scripts/test-local.sh').read_text())
        for path in ('scripts/signing-bootstrap.py', 'scripts/tests/test_signing_bootstrap.py', 'os/signing-bootstrap/**'):
            self.assertEqual(ci.count(f"      - '{path}'\n"), 2, path)


# Mutation controls: removing a guard from a copy of the module makes the matching unsafe case proceed.
class MutationControls(unittest.TestCase):
    def mutated(self, needle, replacement):
        source = MODULE.read_text()
        self.assertEqual(source.count(needle), 1, needle)
        return load(source.replace(needle, replacement))

    def run_with(self, module, executor, profiles=READY):
        f = Fixture(self)
        return module.run('fold-rango-v1', profiles, executor, f.state, f.lock, SHA, NOW, workspace=f.workspace, frankel=FRANKEL)[0]

    def test_guards_are_load_bearing(self):
        cases = [
            ("    if inventory.get('complete') is not True:", "    if False:", PROFILES, MockExecutor, 'inventory-incomplete'),
            ("        if state['versions']:", "        if False:", READY,
             lambda: MockExecutor(states={'PIXEL_OS_SIGNING_KEYS_RANGO_V1': {'exists': True, 'versions': ['1']}}), None),
            ("        if observed != installed['sha256']:", "        if False:", READY, lambda: MockExecutor(tools={'openssl': '0' * 64}), None),
            ("        if len(value) >= 4 and", "        if False and", READY, None, None),
        ]
        # inventory: with the check removed the fold profile still stops on tools, so remove both to show the gate.
        incomplete = ready_profiles()
        incomplete['profiles']['fold-rango-v1']['keyInventory']['complete'] = False
        self.assertIn('inventory-incomplete', dict(sb.readiness('fold-rango-v1', incomplete, NOW)))
        m = self.mutated(cases[0][0], cases[0][1])
        self.assertNotIn('inventory-incomplete', dict(m.readiness('fold-rango-v1', incomplete, NOW)))
        for needle, replacement, profiles, factory, _ in cases[1:3]:
            ex = factory()
            self.assertEqual(self.run_with(sb, ex, profiles)['status'], 'blocked')
            ex = factory()
            self.assertEqual(self.run_with(self.mutated(needle, replacement), ex, profiles)['status'], 'complete', needle)
        m = self.mutated(cases[3][0], cases[3][1])
        self.assertEqual(m.public_receipt({'reasons': [PASSPHRASE.strip().decode()]}, [PASSPHRASE.strip()])['reasons'],
            [PASSPHRASE.strip().decode()])

    def test_partial_import_guard_is_load_bearing(self):
        needle = "            return _recovery(receipt, journal, name, stop, f'{secret} add failed after keys were committed')"
        m = self.mutated(needle, "            continue")
        receipt = self.run_with(m, MockExecutor(adds=['1', m.AddFailed('x')]))
        self.assertEqual(receipt['status'], 'complete')  # the unsafe report the real guard prevents
        self.assertEqual(self.run_with(sb, MockExecutor(adds=['1', sb.AddFailed('x')]))['status'], 'recovery-needed')

    def test_identity_collision_guard_is_load_bearing(self):
        archive = archive_bytes()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            avb = hashlib.sha256(tar.extractfile('rango/avb_pkmd.bin').read()).hexdigest()
        m = self.mutated("return stop('identity-collision', 'public identity equals the Frankel identity; keys are never copied', 'failed')", 'pass')
        f = Fixture(self)
        receipt = m.run('fold-rango-v1', READY, MockExecutor(archive=archive), f.state, f.lock, SHA, NOW, workspace=f.workspace,
            frankel=dict(FRANKEL, avbPublicKeySha256=avb))[0]
        self.assertEqual(receipt['status'], 'complete')


    def test_encryption_and_recipe_guards_are_load_bearing(self):
        base = {n: key_member(n) for n in FOLD['keyInventory']['requiredByConsumer']}
        plain = archive_bytes(dict(base, **{'platform.pk8': bytes([0x30, 0x10, 0x02, 0x01, 0x00]) + b'plain'}))
        self.assertEqual(self.run_with(sb, MockExecutor(archive=plain))['status'], 'failed')
        m = self.mutated("                if name.endswith('.pk8') and not encrypted_pkcs8_der(read(name)):", "                if False:")
        self.assertEqual(self.run_with(m, MockExecutor(archive=plain))['status'], 'complete')
        profile = copy.deepcopy(FOLD)
        profile['generation']['recipe'][0]['args'].append('/etc/passwd')
        self.assertTrue(sb.recipe_problems(profile))
        m = self.mutated("or (LITERAL.fullmatch(arg) and '..' not in arg)", "or True")
        self.assertEqual(m.recipe_problems(profile), [])


class MockRunner:
    """Stands in for the process boundary: writes synthetic files for each step's produces list."""

    def __init__(self, fail_step=None, extra=None, skip=None):
        self.calls, self.fail_step, self.extra, self.skip = [], fail_step, extra, skip

    def __call__(self, tool, argv, cwd, stdin_path):
        index = len(self.calls)
        self.calls.append((tool, list(argv), cwd, stdin_path))
        if index == self.fail_step:
            return 3
        step = FOLD['generation']['recipe'][index]
        for name in step['produces']:
            if name != self.skip:
                (Path(cwd)/name).write_bytes(key_member(name, b'gen'))
        if self.extra and index == 0:
            (Path(cwd)/self.extra).write_bytes(b'x')
        return step.get('exitStatus', 0)


class Generator(unittest.TestCase):
    def workspace(self):
        path = Path(tempfile.mkdtemp(prefix='gen-'))
        self.addCleanup(shutil.rmtree, path, True)
        os.chmod(path, 0o700)
        return path

    def test_recipe_packs_exact_inventory_deterministically(self):
        outputs = []
        for _ in range(2):
            ws, runner = self.workspace(), MockRunner()
            sb.generate_keys(ws, FOLD, runner, token='synthetic-token')
            self.assertEqual(sorted(os.listdir(ws)), ['keys.tar.gz', 'passphrase'])
            self.assertEqual(oct(os.stat(ws/'keys.tar.gz').st_mode & 0o777), '0o600')
            identity = sb.inspect_archive(ws/'keys.tar.gz', FOLD)
            self.assertEqual(identity['members'], sorted(FOLD['keyInventory']['requiredByConsumer']))
            outputs.append((ws/'keys.tar.gz').read_bytes())
            # Fixed approved tool paths, the passphrase only as a file path or stdin, never its value in argv.
            self.assertEqual({c[0] for c in runner.calls}, {FOLD['tools'][s['tool']]['installed']['path'] for s in SYNTHETIC_RECIPE})
            self.assertTrue(all('synthetic-token' not in ' '.join(c[1]) for c in runner.calls))
            self.assertEqual([c[3] is not None for c in runner.calls], [True] * CERT_STEPS + [False] * 3)
            self.assertIn('/CN=GrapheneOS/', runner.calls[0][1])
            self.assertIn('file:' + str(ws/'passphrase'), runner.calls[-1][1])  # -passout file:, not the value
        self.assertEqual(outputs[0], outputs[1])

    def test_failed_extra_or_missing_outputs_block(self):
        for runner, code in ((MockRunner(fail_step=2), 'generator-failed'), (MockRunner(extra='debug.log'), 'unexpected-output'),
                             (MockRunner(skip='avb_pkmd.bin'), 'unexpected-output')):
            with self.assertRaises(sb.Blocked) as blocked:
                sb.generate_keys(self.workspace(), FOLD, runner, token='t')
            self.assertEqual(blocked.exception.code, code)

    def test_checked_in_fold_recipe_needs_measured_tools_and_nothing_runs(self):
        runner, ws = MockRunner(), self.workspace()
        with self.assertRaises(sb.Blocked) as blocked:
            sb.generate_keys(ws, PROFILES['profiles']['fold-rango-v1'], runner)
        self.assertEqual((blocked.exception.code, runner.calls, os.listdir(ws)), ('tool-unapproved', [], []))
        # OCT30-INFRA-S016: the checked-in Fold tools are proven per run by the admitted helper, so source readiness is
        # complete, while generation itself still refuses a profile without proof-bound tools.
        self.assertEqual(sb.readiness('fold-rango-v1', PROFILES, NOW), [])

    def test_recipe_rejects_injection_and_incomplete_steps(self):
        def problems(change):
            profile = copy.deepcopy(FOLD)
            change(profile['generation'])
            return sb.recipe_problems(profile)
        self.assertEqual(problems(lambda g: None), [])
        for name, change in {
            'absolute path': lambda g: g['recipe'][0]['args'].append('/etc/passwd'),
            'traversal': lambda g: g['recipe'][0]['args'].append('{out}/../keys'),
            'shell': lambda g: g['recipe'][0]['args'].append('$(id)'),
            'separator': lambda g: g['recipe'][0]['args'].append('a;b'),
            'dotdot literal': lambda g: g['recipe'][0]['args'].append('a..b'),
            'unknown tool': lambda g: g['recipe'][0].update(tool='bash'),
            'stdin': lambda g: g['recipe'][0].update(stdin='/dev/tty'),
            'extra key': lambda g: g['recipe'][0].update(env={'A': 'B'}),
            'non-inventory output': lambda g: g['recipe'][0]['produces'].append('shared.pk8'),
            'duplicate output': lambda g: g['recipe'][1]['produces'].append('releasekey.pk8'),
            'omitted generator step': lambda g: g['recipe'].pop(0),
            'bad subject': lambda g: g.update(subject='CN=x; rm'),
            'empty': lambda g: g.update(recipe=[]),
        }.items():
            with self.subTest(name):
                self.assertTrue(problems(change), name)
        profile = copy.deepcopy(FOLD)
        profile['generation']['recipe'] = None
        self.assertEqual([c for c, _ in sb.recipe_problems(profile)], ['missing'])

    def test_end_to_end_with_adapter_and_mock_provider(self):
        class Provider(MockExecutor):
            def generate(self, workspace, profile):
                self.calls.append(('generate',))
                sb.generate_keys(workspace, profile, MockRunner(), token='synthetic-token')
        f, ex = Fixture(self), Provider()
        receipt, private = f.run(ex)
        self.assertEqual(receipt['status'], 'complete', receipt['reasons'])
        out = sb.public_receipt(receipt, private)
        self.assertNotIn('synthetic-token', json.dumps(out))
        self.assertEqual(set(out['tools']), set(FOLD['tools']))
        self.assertTrue(all(set(v) == {'installedSha256', 'source'} for v in out['tools'].values()))


class Inventory(unittest.TestCase):
    def test_roles_reconcile_with_the_consumer_contract(self):
        inventory = PROFILES['profiles']['fold-rango-v1']['keyInventory']
        required = sorted(f for r in inventory['roles'] if not r.get('optional') for f in r['files'])
        optional = sorted(f for r in inventory['roles'] if r.get('optional') for f in r['files'])
        self.assertEqual(required, sorted(inventory['requiredByConsumer']))
        self.assertEqual(optional, inventory['optionalByConsumer'])
        self.assertTrue(set(PROFILES['profiles']['fold-rango-v1']['archive']['publicIdentityFiles']) <= set(required))
        self.assertTrue(all(r['evidence'] for r in inventory['roles']))
        # S010: the -d default set of sign_target_files_apks.py @ b0f697df plus the generate-release.sh consumer files.
        self.assertEqual({r['role'] for r in inventory['roles'] if not r.get('optional')}, {'releasekey', 'platform', 'shared',
            'media', 'networkstack', 'sdk_sandbox', 'bluetooth', 'gmscompat_lib', 'nfc', 'avb', 'avb-public'})
        self.assertTrue(inventory['complete'])
        self.assertNotIn('missing', inventory)
        for role in inventory['roles']:
            if role['files'][0].endswith('.pk8'):
                self.assertIn('scrypt-encrypted', role['formats'][0])  # make_key -passout env:password -scrypt
                self.assertIn('-passin', role['formats'][0])  # decrypt-keys / host-decrypt-keys consumer

    def test_tool_source_identity_is_not_an_installed_identity(self):
        for name, tool in PROFILES['profiles']['fold-rango-v1']['tools'].items():
            self.assertEqual(tool['installed'], {'path': None, 'sha256': None}, name)
            self.assertIn(tool['kind'], ('source-script', 'host-binary'), name)
            if tool['kind'] == 'source-script':
                self.assertRegex(tool['sourceSha256'], '^[0-9a-f]{64}$', name)
                self.assertTrue(tool['source']['pathVerified'], name)
            else:
                self.assertIsNone(tool['source'], name)  # openssl: a host binary, no source identity claimed
        plan = sb.plan('fold-rango-v1', PROFILES)
        self.assertEqual(sorted(t['tool'] for t in plan['laterHostPreflight']), ['avbtool', 'decrypt-keys', 'generator', 'openssl'])
        self.assertEqual(plan['missingSources'], [])  # S010: inventory and recipe sources are closed

    def test_models_is_its_own_pkcs12_profile(self):
        models, fold = PROFILES['profiles']['models-v0'], PROFILES['profiles']['fold-rango-v1']
        self.assertEqual(models['status'], 'resource-bound')
        self.assertEqual(models['secrets'], {'keys': 'PIXEL_MODELS_SIGNING_KEYSTORE_V1', 'passphrase': 'PIXEL_MODELS_SIGNING_PASSPHRASE_V1'})
        self.assertFalse(set(models['secrets'].values()) & set(fold['secrets'].values()))
        self.assertEqual(models['archive']['format'], 'pkcs12')
        self.assertEqual(sb.profile_outputs(models), ('keystore.p12', 'passphrase'))
        self.assertEqual(sb.profile_outputs(fold), ('keys.tar.gz', 'passphrase'))
        self.assertEqual(models['tools']['keytool']['installed'], {'path': None, 'sha256': None})


FIXTURES = SCRIPTS/'tests/fixtures/signing-bootstrap'


class Sources(unittest.TestCase):
    """S010 source bindings: the four owner-approved pinned reads (two copied as fixtures, two recorded)."""

    def tools(self):
        return PROFILES['profiles']['fold-rango-v1']['tools']

    def test_fixtures_match_the_recorded_source_identities(self):
        for tool, name in (('generator', 'make_key'), ('decrypt-keys', 'decrypt-keys')):
            self.assertEqual(sb.verify_source(self.tools()[tool], (FIXTURES/name).read_bytes()), [], tool)
        stfa = PROFILES['profiles']['fold-rango-v1']['sources']['signTargetFilesApks']
        self.assertEqual((stfa['revision'], stfa['gitBlob']), ('b0f697df8cc9519408ca0a404e918cd51bde0735',
            'b3bb318917f5e8744c4d5a505112373e58e141a5'))
        avb = self.tools()['avbtool']['source']
        self.assertEqual((avb['revision'], avb['gitBlob']), ('ba2dec4b035b0a3b61c5f8f8a74d86bcd450b1ee',
            'c0f3c7c62c4121edca58727ae60e17818d46155c'))

    def test_pinned_source_mismatch_is_detected(self):
        data = bytearray((FIXTURES/'make_key').read_bytes())
        data[-2:-1] = b'X'
        problems = sb.verify_source(self.tools()['generator'], bytes(data))
        self.assertEqual(len(problems), 2)
        profiles = ready_profiles()
        profiles['profiles']['fold-rango-v1']['tools']['avbtool']['source']['gitBlob'] = None
        self.assertIn('source-unpinned', dict(sb.readiness('fold-rango-v1', profiles, NOW)))
        profiles = ready_profiles()
        profiles['profiles']['fold-rango-v1']['tools']['generator']['sourceSha256'] = 'f' * 63
        self.assertIn('source-unpinned', dict(sb.readiness('fold-rango-v1', profiles, NOW)))

    def test_make_key_steps_match_the_source_and_the_consumer(self):
        make_key = (FIXTURES/'make_key').read_text()
        decrypt = (FIXTURES/'decrypt-keys').read_text()
        # make_key: two arguments select RSA-4096; the password is read from stdin; the pk8 is scrypt-encrypted DER;
        # its EXIT trap always exits 1. decrypt-keys consumes exactly that encrypted DER with -passin.
        for fact in ('openssl genrsa -f4 4096', 'read -rp', '-outform DER -out $1.pk8', '-scrypt', "exit 1' EXIT"):
            self.assertIn(fact, make_key)
        self.assertIn('openssl pkcs8 -inform DER -in $key.pk8 -passin env:password', decrypt)
        self.assertIn('openssl pkcs8 -topk8 -in avb.pem -passin env:password', decrypt)
        recipe = PROFILES['profiles']['fold-rango-v1']['generation']['recipe'][:CERT_STEPS]
        cert_roles = [r['role'] for r in PROFILES['profiles']['fold-rango-v1']['keyInventory']['roles'] if r['files'][0].endswith('.pk8')]
        self.assertEqual([s['args'] for s in recipe], [[f'{{out}}/{r}', '{subject}'] for r in cert_roles])
        self.assertTrue(all(s['tool'] == 'generator' and s['stdin'] == 'passphrase' and s['exitStatus'] == 1 for s in recipe))

    def test_only_installed_identities_remain_and_they_refuse_before_anything(self):
        reasons = sb.readiness('fold-rango-v1', PROFILES, NOW)
        # OCT30-INFRA-S016: installed identities come only from the same-run local proof of the admitted helper, so
        # nothing is open in source; every installed identity stays null in the profile.
        self.assertEqual(reasons, [])
        self.assertTrue(all(t['installed']['sha256'] is None for t in PROFILES['profiles']['fold-rango-v1']['tools'].values()))
        self.assertEqual(sb.recipe_problems(PROFILES['profiles']['fold-rango-v1']), [])
        self.assertNotIn('missing', PROFILES['profiles']['fold-rango-v1']['generation'])
        # Even with a valid recipe, an open generation.missing entry refuses generation.
        profiles = ready_profiles()
        profiles['profiles']['fold-rango-v1']['generation']['missing'] = [{'fact': 'synthetic open item'}]
        self.assertIn('recipe-missing', dict(sb.readiness('fold-rango-v1', profiles, NOW)))
        with self.assertRaises(sb.Blocked) as blocked:
            sb.generate_keys(Path('/nonexistent'), profiles['profiles']['fold-rango-v1'], MockRunner())
        self.assertEqual(blocked.exception.code, 'recipe-missing')

    def test_exit_status_vocabulary(self):
        profile = copy.deepcopy(FOLD)
        profile['generation']['recipe'][0]['exitStatus'] = 2
        self.assertTrue(sb.recipe_problems(profile))
        profile['generation']['recipe'][0]['exitStatus'] = True
        self.assertTrue(sb.recipe_problems(profile))

        class ZeroRunner(MockRunner):
            def __call__(self, tool, argv, cwd, stdin_path):
                super().__call__(tool, argv, cwd, stdin_path)
                return 0  # make_key always exits 1; a 0 is not its success
        ws = Path(tempfile.mkdtemp(prefix='gen-'))
        self.addCleanup(shutil.rmtree, ws, True)
        with self.assertRaises(sb.Blocked) as blocked:
            sb.generate_keys(ws, FOLD, ZeroRunner(), token='t')
        self.assertEqual(blocked.exception.code, 'generator-failed')


    def test_helper_fixtures_bind_the_recipe_and_subject(self):
        generation = PROFILES['profiles']['fold-rango-v1']['generation']
        for name, identity in generation['sources'].items():
            self.assertEqual(sb.verify_source(identity, (FIXTURES/name).read_bytes()), [], name)
        common = (FIXTURES/'common.sh').read_text()
        keys = re.search(r'readonly signing_keys=\(([^)]*)\)', common)[1].split()
        self.assertEqual(sorted(keys), sorted(s['args'][0][6:] for s in generation['recipe'] if s['tool'] == 'generator'))
        generate = (FIXTURES/'generate-keys').read_text()
        for fact in ('CN=GrapheneOS', 'make_key $key "/CN=$CN/"', 'openssl genrsa 4096 | openssl pkcs8 -topk8 -scrypt -out avb.pem',
                     'avbtool.py extract_public_key --key avb.pem --output avb_pkmd.bin'):
            self.assertIn(fact, generate)
        self.assertIn('-passout env:new_password -scrypt', (FIXTURES/'encrypt-keys').read_text())
        self.assertEqual(generation['subject'], '/CN=GrapheneOS/')
        self.assertIn('no match with the existing Frankel keys is claimed', generation['subjectBasis'])
        avb = generation['recipe'][CERT_STEPS:]
        self.assertEqual([s['tool'] for s in avb], ['openssl', 'avbtool', 'openssl'])
        self.assertEqual(avb[2]['removes'], ['avb.unencrypted.pem'])
        self.assertEqual(generation['intermediates'], ['avb.unencrypted.pem'])

    def test_intermediate_vocabulary(self):
        def problems(change):
            profile = copy.deepcopy(FOLD)
            change(profile['generation'])
            return sb.recipe_problems(profile)
        self.assertEqual(problems(lambda g: None), [])
        for name, change in {
            'never removed': lambda g: g['recipe'][-1].pop('removes'),
            'removes inventory file': lambda g: g['recipe'][-1].update(removes=['avb.pem']),
            'removes before produced': lambda g: g['recipe'][CERT_STEPS].update(removes=['avb.unencrypted.pem']),
            'undeclared intermediate': lambda g: g.update(intermediates=[]),
            'intermediate is inventory': lambda g: g.update(intermediates=['avb.pem']),
            'unsafe intermediate name': lambda g: g.update(intermediates=['../x.pem']),
        }.items():
            with self.subTest(name):
                self.assertTrue(problems(change), name)

    def test_intermediate_is_removed_from_the_workspace(self):
        ws = Path(tempfile.mkdtemp(prefix='gen-'))
        self.addCleanup(shutil.rmtree, ws, True)
        sb.generate_keys(ws, FOLD, MockRunner(), token='t')
        with tarfile.open(ws/'keys.tar.gz') as tar:
            self.assertNotIn('rango/avb.unencrypted.pem', tar.getnames())
        self.assertEqual(sorted(os.listdir(ws)), ['keys.tar.gz', 'passphrase'])



# ---------------------------------------------------------------- FOLD-FACTORY-IDENTITY (factory_signing, mocks only)
# No ssh-keygen runs and no key is generated: the runner and Popen are mocks, and the "key" files are byte fixtures in
# the openssh-key-v1 layout with random bytes standing in for the public key and the encrypted body (not a key pair).
import struct
import sys
from unittest import mock
sys.path.insert(0, str(SCRIPTS))
import factory_signing as fs  # noqa: E402

TOOL = '/opt/synthetic/ssh-keygen'


def ssh_string(b):
    return struct.pack('>I', len(b)) + b


def fake_key(cipher=b'aes256-ctr', kdf=b'bcrypt', rounds=100, keys=1, key_type=b'ssh-ed25519', raw=None):
    """A synthetic openssh-key-v1 file: header fields as ssh-keygen writes them, random bytes elsewhere."""
    raw = os.urandom(32) if raw is None else raw
    public = ssh_string(key_type) + ssh_string(raw)
    options = ssh_string(os.urandom(16)) + struct.pack('>I', rounds) if kdf == b'bcrypt' else b''
    blob = b'openssh-key-v1\0' + ssh_string(cipher) + ssh_string(kdf) + ssh_string(options) + struct.pack('>I', keys) \
        + ssh_string(public) + ssh_string(os.urandom(144))
    body = base64.b64encode(blob).decode()
    text = '-----BEGIN OPENSSH PRIVATE KEY-----\n' + '\n'.join(body[i:i + 70] for i in range(0, len(body), 70)) \
        + '\n-----END OPENSSH PRIVATE KEY-----\n'
    return text.encode(), 'ssh-ed25519 ' + base64.b64encode(public).decode()


class KeygenMock:
    """Stands in for ssh-keygen: records argv/env/cwd/seconds, checks the pinned helper is in place, writes fixtures."""

    def __init__(self, key=None, pub=None, category='ok', extra=None, derive='same', sign='ok', verify='ok', negative='nonzero_exit'):
        self.calls, self.key, self.pub, self.category, self.extra = [], key, pub, category, extra
        self.derive, self.sign, self.verify, self.negative, self.public = derive, sign, verify, negative, None

    def __call__(self, argv, env, cwd, seconds, stdin_path=None, keep_output=False):
        helper = Path(env['SSH_ASKPASS']) if 'SSH_ASKPASS' in env else None
        self.calls.append({'argv': argv, 'env': dict(env), 'cwd': Path(cwd), 'seconds': seconds, 'stdin': stdin_path,
                           'keepOutput': keep_output, 'helper': helper.read_bytes() if helper else None,
                           'helperMode': oct(helper.stat().st_mode & 0o777) if helper else None})
        done = lambda category, **extra: dict({'exit': 0 if category == 'ok' else 1 if category == 'nonzero_exit' else None,
                                              'category': category, 'outputBytes': 0, 'outputSha256': None}, **extra)
        if argv[1] == '-y':  # self-check decrypt: prints the public key
            public = self.public if self.derive == 'same' else fake_key()[1]
            return done('ok', output=(public + '\n').encode()) if self.derive != 'fail' else done('nonzero_exit')
        if argv[1:3] == ['-Y', 'sign']:
            if self.sign == 'ok':
                Path(argv[-1] + '.sig').write_text('-----BEGIN SSH SIGNATURE-----\nc3lu\n-----END SSH SIGNATURE-----\n')
            return done(self.sign)
        if argv[1:3] == ['-Y', 'verify']:
            return done(self.verify if argv[argv.index('-n') + 1] == 'factory images' else self.negative)
        if self.category == 'ok':
            key, pub = (self.key, self.pub) if self.key else fake_key()
            self.public = pub
            out = Path(argv[argv.index('-f') + 1])
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(key)
            Path(f'{out}.pub').write_text(pub + ' \n')
            if self.extra:
                (Path(cwd)/self.extra).write_bytes(b'x')
        return {'exit': 0 if self.category == 'ok' else None, 'category': self.category, 'outputBytes': 0, 'outputSha256': None}


def bootstrap_options():
    """The os-release.yml bootstrap_profile choice list itself (not the first `options: [none` line in the file)."""
    block = re.search(r'\n      bootstrap_profile:\n(?:        (?!options:)[^\n]*\n)*        options: \[([^\]]*)\]\n', WORKFLOW)
    return block[1].split(', ')


def owner_dir(test):
    path = Path(tempfile.mkdtemp(prefix='fs-'))
    os.chmod(path, 0o700)
    test.addCleanup(shutil.rmtree, path, True)
    return path


class FactoryKeyGeneration(unittest.TestCase):
    def test_passphrase_only_through_the_pinned_helper_never_argv_or_environment(self):
        ws = owner_dir(self)
        runner = KeygenMock()
        with mock.patch.dict(os.environ, {'GITHUB_TOKEN': 'ghs_synthetic', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'x'}):
            identity = fs.generate_key(ws, TOOL, runner, token='SYNTHETIC-passphrase-token')
        call = runner.calls[0]
        self.assertEqual(call['argv'], [TOOL, '-q', '-t', 'ed25519', '-a', '100', '-C', '', '-f', str(ws/'id_ed25519')])
        self.assertFalse({'-N', '-P', '-p'} & set(call['argv']))
        self.assertEqual(call['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'SSH_ASKPASS_REQUIRE': 'force',
                                       'SSH_ASKPASS': str(ws/'askpass')})
        self.assertEqual(call['helper'], f'#!/bin/sh\nexec cat {ws}/passphrase\n'.encode())
        self.assertEqual(call['helperMode'], '0o700')
        self.assertEqual(call['seconds'], 60)
        self.assertNotIn('SYNTHETIC-passphrase-token', json.dumps(call, default=str) + json.dumps(identity))
        self.assertEqual(sorted(os.listdir(ws)), ['id_ed25519', 'passphrase'])  # helper and .pub removed
        self.assertEqual(oct((ws/'passphrase').stat().st_mode & 0o777), '0o600')
        self.assertEqual((ws/'passphrase').read_text(), 'SYNTHETIC-passphrase-token\n')
        self.assertRegex(identity['publicKey'], r'^ssh-ed25519 [A-Za-z0-9+/]{68}$')
        blob = base64.b64decode(identity['publicKey'].split()[1])
        self.assertEqual(identity['fingerprint'], 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('='))
        self.assertEqual(fs.ASKPASS_TEMPLATE_SHA256, hashlib.sha256(b'#!/bin/sh\nexec cat {passfile}\n').hexdigest())

    def test_unencrypted_weak_wrong_or_failed_generation_refuses(self):
        cases = {
            'unencrypted': (fake_key(cipher=b'none', kdf=b'none'), 'key_unencrypted'),
            'weak kdf': (fake_key(rounds=16), 'bcrypt rounds below 100'),
            'two keys': (fake_key(keys=2), 'exactly one key'),
            'rsa': (fake_key(key_type=b'ssh-rsa'), 'not an ssh-ed25519 key'),
            'short public': (fake_key(raw=b'x' * 31), 'not a 32-byte'),
        }
        for name, ((key, pub), pattern) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(fs.Refused, pattern):
                    fs.generate_key(owner_dir(self), TOOL, KeygenMock(key, pub))
        key, _ = fake_key()
        with self.assertRaisesRegex(fs.Refused, 'does not equal the private key header'):
            fs.generate_key(owner_dir(self), TOOL, KeygenMock(key, fake_key()[1]))
        for category in ('timeout', 'nonzero_exit', 'output_overflow', 'spawn_failed'):
            with self.subTest(category):
                ws = owner_dir(self)
                with self.assertRaisesRegex(fs.Refused, f'generator_failed: ssh-keygen {category}'):
                    fs.generate_key(ws, TOOL, KeygenMock(category=category))
                self.assertFalse((ws/'askpass').exists(), 'the helper is removed on failure too')
        with self.assertRaisesRegex(fs.Refused, 'unexpected_output'):
            fs.generate_key(owner_dir(self), TOOL, KeygenMock(extra='stray'))
        with self.assertRaisesRegex(fs.Refused, 'path_refused'):
            fs.generate_key(owner_dir(self), 'ssh-keygen', KeygenMock())  # never a PATH lookup


class FakeProc:
    """A Popen stand-in over a real pipe (no process): writes the given bytes, optionally never exits."""

    def __init__(self, data=b'', exits=True, returncode=0):
        read, write = os.pipe()
        self.stdout = os.fdopen(read, 'rb')
        os.write(write, data[:60000])
        if exits:
            os.close(write)
        else:
            self._write = write
        self.pid, self.returncode, self.exits, self.waits = 424242, None, exits, []
        self._rc = returncode

    def poll(self):
        return self._rc if self.exits else None

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if hasattr(self, '_write'):
            os.close(self._write)
            del self._write
        self.returncode = self._rc if self.exits else -9
        return self.returncode


class BoundedRunner(unittest.TestCase):
    def run_with(self, proc, clock=None):
        kills, spawned = [], []

        def popen(argv, **kw):
            spawned.append((argv, kw))
            return proc
        with mock.patch.object(fs.os, 'killpg', lambda pid, sig: kills.append((pid, sig))):
            result = fs.run_bounded(['/opt/synthetic/tool'], {'PATH': '/usr/bin:/bin'}, '/tmp', 5, popen=popen,
                                    **({'clock': clock} if clock else {}))
        return result, kills, spawned

    def test_spawn_shape_and_whole_group_ended_and_reaped_on_success(self):
        proc = FakeProc(b'Signing file x\n')
        result, kills, spawned = self.run_with(proc)
        argv, kw = spawned[0]
        self.assertEqual((argv, kw['env'], kw['start_new_session'], kw['close_fds']), (['/opt/synthetic/tool'],
                                                                                     {'PATH': '/usr/bin:/bin'}, True, True))
        self.assertIs(kw['preexec_fn'], fs._no_core)
        self.assertEqual(kw['stdin'].name, os.devnull)
        self.assertEqual(kills, [(424242, fs.signal.SIGTERM), (424242, fs.signal.SIGKILL)])
        self.assertEqual(result, {'exit': 0, 'category': 'ok', 'outputBytes': 15,
                                  'outputSha256': hashlib.sha256(b'Signing file x\n').hexdigest()})
        self.assertNotIn('output', result)  # bytes are never returned

    def test_overflow_and_timeout_end_the_group(self):
        result, kills, _ = self.run_with(FakeProc(b'x' * 40000, exits=False))
        self.assertEqual((result['category'], result['exit'], result['outputBytes']), ('output_overflow', None, 16385))
        self.assertEqual([k[1] for k in kills], [fs.signal.SIGTERM, fs.signal.SIGKILL])
        ticks = iter([0, 0] + [100] * 10)
        result, kills, _ = self.run_with(FakeProc(exits=False), clock=lambda: next(ticks))
        self.assertEqual((result['category'], result['exit']), ('timeout', None))
        self.assertEqual([k[1] for k in kills], [fs.signal.SIGTERM, fs.signal.SIGKILL])
        result, _, _ = self.run_with(FakeProc(returncode=3))
        self.assertEqual((result['category'], result['exit']), ('nonzero_exit', 3))

    def test_spawn_failure(self):
        def popen(argv, **kw):
            raise FileNotFoundError(argv[0])
        self.assertEqual(fs.run_bounded(['/nonexistent/tool'], {}, '/tmp', 5, popen=popen)['category'], 'spawn_failed')


class FactoryPin(unittest.TestCase):
    def test_bound_pin_only_when_genuinely_bound(self):
        self.assertIsNone(fs.bound_pin({'namespace': 'factory images', 'principal': None, 'publicKey': None, 'status': 'missing'}))
        for flavor in ('rango', 'frankel'):  # a committed pin is null (signing off) or genuinely bound; a bound pin's
            pin = json.loads((ROOT/f'os/flavors/{flavor}.json').read_text())['factorySigning']  # receipt is checked by
            if pin.get('publicKey') is None and pin.get('principal') is None:                  # factory-archive-sign check
                self.assertIsNone(fs.bound_pin(pin))
            else:
                self.assertEqual(fs.bound_pin(pin)['principal'], pin['principal'])
        good = {'namespace': 'factory images', 'principal': 'roseos-factory-images-rango-v1', 'publicKey': fake_key()[1],
                'status': 'bound'}
        self.assertEqual(fs.bound_pin(good)['principal'], 'roseos-factory-images-rango-v1')
        for name, change in (('half bound', {'publicKey': None}), ('other namespace', {'namespace': 'file'}),
                             ('not bound', {'status': 'missing'}), ('rsa', {'publicKey': 'ssh-rsa ' + 'A' * 68}),
                             ('bad principal', {'principal': 'Root User'})):
            with self.subTest(name), self.assertRaises(fs.Refused):
                fs.bound_pin(dict(good, **change))


def sign_runner(calls, sign='ok', verify='ok', write=True):
    def runner(argv, env, cwd, seconds, stdin_path=None):
        calls.append({'argv': argv, 'env': dict(env), 'stdin': stdin_path,
                      'helper': Path(env['SSH_ASKPASS']).read_bytes() if 'SSH_ASKPASS' in env else None,
                      'signers': (Path(cwd)/'allowed_signers').read_text() if (Path(cwd)/'allowed_signers').exists() else None})
        if argv[1:3] == ['-Y', 'sign']:
            if write and sign == 'ok':
                Path(argv[-1] + '.sig').write_text('-----BEGIN SSH SIGNATURE-----\nc3lu\n-----END SSH SIGNATURE-----\n')
            return {'exit': 0 if sign == 'ok' else 1, 'category': sign, 'outputBytes': 0, 'outputSha256': None}
        return {'exit': 0 if verify == 'ok' else 255, 'category': verify, 'outputBytes': 0, 'outputSha256': None}
    return runner


class FactorySignVerify(unittest.TestCase):
    def setUp(self):
        self.dir = owner_dir(self)
        self.archive = self.dir/'rango-install-2026103001.zip'
        self.archive.write_bytes(b'synthetic archive bytes')
        self.key, self.passfile = self.dir/'id_ed25519', self.dir/'passphrase'
        self.key.write_bytes(fake_key()[0]), self.passfile.write_text('SYNTHETIC\n')
        self.work = owner_dir(self)
        self.pin = {'principal': 'roseos-factory-images-rango-v1', 'publicKey': fake_key()[1]}

    def test_sign_then_verify_against_the_pin_only(self):
        calls = []
        sig = fs.sign_and_verify(self.archive, self.key, self.passfile, self.pin, self.work, TOOL, sign_runner(calls))
        sign, verify = calls
        self.assertEqual(sign['argv'], [TOOL, '-Y', 'sign', '-n', 'factory images', '-f', str(self.key), str(self.archive)])
        self.assertEqual(sign['helper'], f'#!/bin/sh\nexec cat {self.passfile}\n'.encode())
        self.assertEqual(verify['argv'], [TOOL, '-Y', 'verify', '-f', str(self.work/'allowed_signers'), '-I',
                                          'roseos-factory-images-rango-v1', '-n', 'factory images', '-s', f'{self.archive}.sig'])
        self.assertEqual(verify['signers'], f'roseos-factory-images-rango-v1 namespaces="factory images" {self.pin["publicKey"]}\n')
        self.assertEqual((verify['stdin'], 'SSH_ASKPASS' in verify['env']), (str(self.archive), False))
        self.assertEqual(sorted(os.listdir(self.work)), [])  # helper and allowed_signers removed
        self.assertEqual(sig['sha256'], hashlib.sha256(Path(f'{self.archive}.sig').read_bytes()).hexdigest())
        self.assertNotIn('SYNTHETIC', json.dumps(calls[0]['argv']) + json.dumps(sig))

    def test_failed_sign_or_verify_leaves_no_signature(self):
        for kw, pattern in (({'sign': 'timeout'}, 'sign_failed'), ({'write': False}, 'no single small SSH signature'),
                            ({'verify': 'nonzero_exit'}, 'verify_failed')):
            with self.subTest(kw):
                with self.assertRaisesRegex(fs.Refused, pattern):
                    fs.sign_and_verify(self.archive, self.key, self.passfile, self.pin, self.work, TOOL, sign_runner([], **kw))
                self.assertFalse(os.path.lexists(f'{self.archive}.sig'))
                self.assertEqual(sorted(os.listdir(self.work)), [])
        Path(f'{self.archive}.sig').write_text('pre-existing')
        with self.assertRaisesRegex(fs.Refused, 'signature_exists'):
            fs.sign_and_verify(self.archive, self.key, self.passfile, self.pin, self.work, TOOL, sign_runner([]))


def factory_ready_profiles():
    profiles = copy.deepcopy(PROFILES)
    profile = profiles['profiles']['fold-rango-factory-v1']
    profile.update(status='resource-bound')
    profile['tools']['ssh-keygen']['installed'] = {'path': TOOL, 'sha256': hashlib.sha256(b'ssh-keygen').hexdigest()}
    profile['access'].update(addVersionExpires='2026-10-30T00:30:00Z', accessorExpires='2026-11-27T21:30:00Z',
                             basis='synthetic test readback')
    return profiles


OBSERVE_SPEC = importlib.util.spec_from_file_location('ssh_keygen_observe_for_bootstrap', SCRIPTS/'ssh-keygen-observe.py')
OBSERVE = importlib.util.module_from_spec(OBSERVE_SPEC)
OBSERVE_SPEC.loader.exec_module(OBSERVE)


class SshKeygenEvidence(unittest.TestCase):
    """Every committed copy of the builder's ssh-keygen identity agrees with os/signing-bootstrap/ssh-keygen-evidence.json
    (scripts/ssh-keygen-observe.py committed_problems). Synthetic reports only; nothing reads a host."""
    RUN = {'workflow': 'os-release.yml', 'mode': 'blazer-prep', 'stage': 'ssh-keygen-observe', 'runId': '41700000001',
           'attempt': '1', 'workflowSha': 'c' * 40, 'artifact': 'blazer-prep-ssh-keygen-observe-41700000001-1'}

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='sshkg-evidence-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        for rel in (OBSERVE.EVIDENCE, OBSERVE.PROFILES, OBSERVE.BINDING):
            (self.root/rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/rel, self.root/rel)
        self.committed = copy.deepcopy(json.loads((ROOT/OBSERVE.EVIDENCE).read_text()))
        if self.committed.get('status') == 'measured':  # the committed report travels with its evidence
            shutil.copyfile(ROOT/self.committed['observation']['file'], self.root/self.committed['observation']['file'])
        self.unmeasure()  # every case below starts from the unmeasured state, whatever is committed
        self.report = {'schema': OBSERVE.SCHEMA, 'host': OBSERVE.HOST, 'complete': True, 'bindable': True, 'problems': [],
                       'lock': {'held': True, 'path': '/home/pixelbuild/frankel-build.lock'},
                       'files': {'tool': {'path': '/usr/bin/ssh-keygen', 'state': 'present', 'sha256': 'b' * 64}},
                       'package': {'manager': 'dpkg', 'source': 'database', 'name': 'openssh-client', 'version': '1:9.2p1-2+deb12u3',
                                   'architecture': 'amd64', 'status': 'install ok installed', 'listsTool': True,
                                   'md5sumMatches': True, 'diverted': False},
                       'version': {'source': 'package-database', 'upstream': '9.2p1', 'meetsMinimum': True}}

    def unmeasure(self):
        self.edit(OBSERVE.EVIDENCE, lambda e: e.update(status='unmeasured', observation=None, run=None))

        def null_profiles(p):
            for name in ('fold-rango-factory-v1', 'pro-blazer-factory-v1'):
                p['profiles'][name]['tools']['ssh-keygen']['installed'] = dict(OBSERVE.NULL_IDENTITY)
                if OBSERVE.MEASURED_ENTRY not in p['profiles'][name]['missing']:
                    p['profiles'][name]['missing'].append(OBSERVE.MEASURED_ENTRY)
        self.edit(OBSERVE.PROFILES, null_profiles)
        self.edit(OBSERVE.BINDING, lambda b: b.update(sshKeygen=dict(OBSERVE.NULL_IDENTITY)))

    def edit(self, rel, change):
        path = self.root/rel
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value, indent=2))

    def measure(self, report=None, blazer=False, keep_missing=False, run=None, digest=None):
        data = json.dumps(report or self.report, sort_keys=True).encode()
        (self.root/OBSERVE.EVIDENCE_DIR/'ssh-keygen-observation.json').write_bytes(data)
        identity = {'path': '/usr/bin/ssh-keygen', 'sha256': (report or self.report)['files']['tool']['sha256']}
        self.edit(OBSERVE.EVIDENCE, lambda e: e.update(status='measured', run=dict(run or self.RUN), observation={
            'file': f'{OBSERVE.EVIDENCE_DIR}/ssh-keygen-observation.json', 'sha256': digest or hashlib.sha256(data).hexdigest()}))

        def bind_profiles(p):
            for name in ('fold-rango-factory-v1', *(('pro-blazer-factory-v1',) if blazer else ())):
                p['profiles'][name]['tools']['ssh-keygen']['installed'] = dict(identity)
                if not keep_missing:
                    p['profiles'][name]['missing'] = [m for m in p['profiles'][name]['missing'] if m != OBSERVE.MEASURED_ENTRY]
        self.edit(OBSERVE.PROFILES, bind_profiles)
        self.edit(OBSERVE.BINDING, lambda b: b.update(sshKeygen=dict(identity)))
        return identity

    def test_committed_copies_agree_with_the_committed_evidence(self):
        self.assertEqual(OBSERVE.committed_problems(ROOT), [])
        self.assertEqual(OBSERVE.committed_problems(self.root), [])  # the unmeasured state is consistent too

    def test_a_copy_bound_without_measured_evidence_fails(self):
        self.edit(OBSERVE.BINDING, lambda b: b.update(sshKeygen={'path': '/usr/bin/ssh-keygen', 'sha256': 'b' * 64}))
        self.assertEqual(OBSERVE.committed_problems(self.root), [f'{OBSERVE.BINDING}: bound without measured evidence'])

    def test_measured_evidence_binds_rango_and_optionally_blazer(self):
        identity = self.measure()
        self.assertEqual(OBSERVE.committed_problems(self.root), [])
        self.measure(blazer=True)
        self.assertEqual(OBSERVE.committed_problems(self.root), [])
        self.assertEqual(json.loads((self.root/OBSERVE.BINDING).read_text())['sshKeygen'], identity)

    def test_measured_evidence_must_match_every_copy_and_its_run(self):
        cases = {
            'blazer differs': (lambda: self.edit(OBSERVE.PROFILES, lambda p: p['profiles']['pro-blazer-factory-v1']['tools']
                               ['ssh-keygen'].update(installed={'path': '/usr/bin/ssh-keygen', 'sha256': 'd' * 64})),
                               'pro-blazer-factory-v1: neither null nor the measured identity'),
            'binding differs': (lambda: self.edit(OBSERVE.BINDING, lambda b: b.update(sshKeygen={'path': '/usr/bin/ssh-keygen',
                                'sha256': 'd' * 64})), f'{OBSERVE.BINDING}: not the measured identity'),
            'rango left null': (lambda: self.edit(OBSERVE.PROFILES, lambda p: p['profiles']['fold-rango-factory-v1']['tools']
                                ['ssh-keygen'].update(installed={'path': None, 'sha256': None})), 'fold-rango-factory-v1: not the measured identity'),
        }
        for name, (change, expected) in cases.items():
            with self.subTest(name):
                self.setUp()
                self.measure()
                change()
                self.assertIn(expected, OBSERVE.committed_problems(self.root))
        for name, kwargs, expected in (
                ('wrong digest', dict(digest='0' * 64), 'evidence.observation: sha256 is not the committed report bytes'),
                ('incomplete report', dict(report=dict(self.report, complete=False)), 'evidence.observation: bind refuses'),
                ('wrong artifact', dict(run=dict(self.RUN, artifact='blazer-prep-ssh-keygen-observe-1-1')), 'evidence.run:'),
                ('other stage', dict(run=dict(self.RUN, stage='binding-observe')), 'evidence.run:'),
                ('missing kept', dict(keep_missing=True), 'fold-rango-factory-v1: the missing list must name the measurement')):
            with self.subTest(name):
                self.setUp()
                self.measure(**kwargs)
                problems = OBSERVE.committed_problems(self.root)
                self.assertTrue(any(p.startswith(expected) for p in problems), problems)

    def test_observation_file_must_live_beside_the_evidence(self):
        self.measure()
        self.edit(OBSERVE.EVIDENCE, lambda e: e['observation'].update(file='os/elsewhere/ssh-keygen-observation.json'))
        self.assertEqual(OBSERVE.committed_problems(self.root),
                         [f'evidence.observation: not a file under {OBSERVE.EVIDENCE_DIR} with its sha256'])


class FactoryBootstrapProfile(unittest.TestCase):
    def test_factory_profile_is_a_dispatch_choice_that_the_guard_refuses_until_ready(self):
        self.assertIn('fold-rango-factory-v1', bootstrap_options())  # owner-authorized dispatch choice
        env = dict(GUARD_ENV, BOOTSTRAP_PROFILE='fold-rango-factory-v1', ROSE_DEVICE='frankel')
        # The hosted guard refuses exactly while readiness has reasons, whatever is committed.
        self.assertEqual(bool(sb.guard(env, PROFILES, NOW)), bool(sb.readiness('fold-rango-factory-v1', PROFILES, NOW)))
        unbound = copy.deepcopy(PROFILES)
        unbound['profiles']['fold-rango-factory-v1']['status'] = 'unbound'
        self.assertTrue(any(f.startswith('profile-unbound') for f in sb.guard(env, unbound, NOW)))
        self.assertTrue(sb.guard(dict(env, ROSE_DEVICE='rango'), unbound, NOW))  # the dispatch shape is unchanged

    def test_checked_in_profile_is_ready_and_an_unbound_copy_refuses_before_anything(self):
        unbound = copy.deepcopy(PROFILES)
        unbound['profiles']['fold-rango-factory-v1']['status'] = 'unbound'
        self.assertEqual(sb.readiness('fold-rango-factory-v1', unbound, NOW)[0][0], 'profile-unbound')
        profile = PROFILES['profiles']['fold-rango-factory-v1']
        # Committed: resource-bound, nothing missing, access checked live (no recorded expiry, so NOW does not matter).
        self.assertEqual((profile['status'], profile['missing']), ('resource-bound', []))
        self.assertEqual(sb.readiness('fold-rango-factory-v1', PROFILES, NOW), [])
        self.assertEqual((profile['principal'], profile['archive']['format']), ('roseos-factory-images-rango-v1', 'openssh-ed25519'))
        self.assertEqual(profile['secrets'], {'keys': 'PIXEL_OS_FACTORY_SIGNING_KEY_RANGO_V1',
                                              'passphrase': 'PIXEL_OS_FACTORY_SIGNING_PASSPHRASE_RANGO_V1'})
        # Never on the committed profile: once the measured ssh-keygen is bound, generate_openssh would reach the real
        # tool (it uses factory_signing.run_bounded, not this runner). A copy with the tool unbound must refuse, and any
        # process start fails the test.
        unbound = copy.deepcopy(profile)
        unbound['tools']['ssh-keygen']['installed'] = {'path': None, 'sha256': None}
        saved = sb.factory_signing.run_bounded
        sb.factory_signing.run_bounded = lambda *a, **k: self.fail('a process was started')
        self.addCleanup(setattr, sb.factory_signing, 'run_bounded', saved)
        with self.assertRaises(sb.Blocked) as ctx:
            sb.generate_keys(owner_dir(self), unbound, runner=None)
        self.assertEqual(ctx.exception.code, 'tool-unapproved')

    def test_ready_copy_generates_projects_and_imports_only_the_key_and_passphrase(self):
        profiles = factory_ready_profiles()
        self.assertEqual(sb.readiness('fold-rango-factory-v1', profiles, NOW), [])
        runner = KeygenMock()

        class Executor(MockExecutor):
            def tool_sha256(self, path):
                self.calls.append(('tool', path))
                return hashlib.sha256(b'ssh-keygen').hexdigest()

            def generate(self, workspace, profile):
                self.calls.append(('generate',))
                return sb.generate_openssh(workspace, profile, runner=runner, token='SYNTHETIC-token')
        executor, fixture = Executor(), Fixture(self)
        receipt = sb.run('fold-rango-factory-v1', profiles, executor, fixture.state, fixture.lock, SHA, NOW,
                         workspace=lambda: fixture.workspace(), frankel=FRANKEL)[0]
        self.assertEqual(receipt['status'], 'complete', receipt['reasons'])
        self.assertEqual([c for c in executor.calls if c[0] == 'add'],
                         [('add', 'PIXEL_OS_FACTORY_SIGNING_KEY_RANGO_V1'), ('add', 'PIXEL_OS_FACTORY_SIGNING_PASSPHRASE_RANGO_V1')])
        identity = receipt['publicIdentity']
        self.assertEqual((identity['principal'], identity['namespace']), ('roseos-factory-images-rango-v1', 'factory images'))
        self.assertRegex(identity['fingerprint'], r'^SHA256:[A-Za-z0-9+/]{43}$')
        self.assertNotIn('SYNTHETIC-token', json.dumps(receipt))
        self.assertEqual(runner.calls[0]['argv'][0], TOOL)  # the measured path, not a PATH lookup


class FactoryWorkspaceExec(unittest.TestCase):
    """The askpass helper runs from the RAM workspace: a noexec mount refuses before the journal, never after it."""

    def test_workspace_problem_detects_noexec_and_unusable(self):
        flags = lambda value: (lambda path: type('StatVfs', (), {'f_flag': value})())
        self.assertIsNone(fs.workspace_problem('/x', statvfs=flags(0)))
        self.assertTrue(fs.workspace_problem('/x', statvfs=flags(os.ST_NOEXEC | os.ST_NOSUID)).startswith('workspace_noexec'))

        def denied(path):
            raise PermissionError
        self.assertEqual(fs.workspace_problem('/x', statvfs=denied), 'workspace_unusable: PermissionError')

    def test_noexec_workspace_refuses_before_the_journal_and_the_run_can_be_retried(self):
        profiles, runner, fixture = factory_ready_profiles(), KeygenMock(), Fixture(self)

        class Executor(MockExecutor):
            def tool_sha256(self, path):
                self.calls.append(('tool', path))
                return hashlib.sha256(b'ssh-keygen').hexdigest()

            def generate(self, workspace, profile):
                self.calls.append(('generate',))
                return sb.generate_openssh(workspace, profile, runner=runner, token='SYNTHETIC-token')

        def bootstrap(executor):
            return sb.run('fold-rango-factory-v1', profiles, executor, fixture.state, fixture.lock, SHA, NOW,
                          workspace=lambda: fixture.workspace(), frankel=FRANKEL)[0]
        saved = sb.factory_signing.workspace_problem
        sb.factory_signing.workspace_problem = lambda path: 'workspace_noexec: synthetic'
        try:
            executor = Executor()
            receipt = bootstrap(executor)
        finally:
            sb.factory_signing.workspace_problem = saved
        self.assertEqual((receipt['status'], codes(receipt)), ('blocked', ['workspace-noexec']))
        self.assertNotIn('generate', executor.kinds())
        self.assertNotIn('add', executor.kinds())
        self.assertEqual(runner.calls, [])
        self.assertFalse((fixture.state/'fold-rango-factory-v1.json').exists())  # no journal: not used up
        retry = Executor()
        self.assertEqual(bootstrap(retry)['status'], 'complete')
        self.assertEqual([c[0] for c in retry.calls].count('add'), 2)


class FactoryBootstrapStop(unittest.TestCase):
    """A SIGTERM or SIGHUP (outer timeout, cancelled job) still removes the RAM workspace holding the fresh key, and an
    interrupted import is journaled as recovery-needed, never retried. Signals are sent to this test process only."""

    def setUp(self):
        saved = {each: signal.getsignal(each) for each in fs.STOP_SIGNALS}
        self.addCleanup(lambda: [signal.signal(each, handler) for each, handler in saved.items()])
        sb.factory_signing.stop_on_signals(sb.interrupted)
        self.profiles, self.runner, self.fixture = factory_ready_profiles(), KeygenMock(), Fixture(self)

    def executor(self, stop_at):
        runner, test = self.runner, self

        class Executor(MockExecutor):
            def tool_sha256(self, path):
                self.calls.append(('tool', path))
                return hashlib.sha256(b'ssh-keygen').hexdigest()

            def generate(self, workspace, profile):
                self.calls.append(('generate',))
                test.workspace = Path(workspace)
                identity = sb.generate_openssh(workspace, profile, runner=runner, token='SYNTHETIC-token')
                if stop_at == 'generate':
                    os.kill(os.getpid(), signal.SIGTERM)
                    time.sleep(1)
                return identity

            def add_version(self, project, secret, file):
                version = super().add_version(project, secret, file)
                if stop_at == 'between-adds' and secret.endswith('KEY_RANGO_V1'):
                    os.kill(os.getpid(), signal.SIGTERM)
                    time.sleep(1)
                return version
        return Executor()

    def run_bootstrap(self, executor):
        return sb.run('fold-rango-factory-v1', self.profiles, executor, self.fixture.state, self.fixture.lock, SHA, NOW,
                      workspace=lambda: self.fixture.workspace(), frankel=FRANKEL)[0]

    def test_the_cli_installs_the_stop_handlers(self):
        for each in fs.STOP_SIGNALS:
            signal.signal(each, signal.SIG_DFL)
        with contextlib.redirect_stdout(io.StringIO()):
            sb.main(['plan', '--profile', 'fold-rango-factory-v1'], env={})
        self.assertTrue(all(callable(signal.getsignal(each)) for each in fs.STOP_SIGNALS))

    def test_a_stop_during_generation_removes_the_workspace_and_adds_nothing(self):
        executor = self.executor('generate')
        receipt = self.run_bootstrap(executor)
        self.assertEqual(codes(receipt), ['interrupted'])
        self.assertTrue(receipt['cleanup']['workspaceRemoved'])
        self.assertFalse(self.workspace.exists())  # the fresh private key and passphrase are gone
        self.assertNotIn('add', executor.kinds())
        journal = json.loads((self.fixture.state/'fold-rango-factory-v1.json').read_text())
        self.assertEqual(journal['status'], 'generating')  # a later run refuses previous-attempt: no silent regeneration
        retry = self.executor(None)
        self.assertEqual(codes(self.run_bootstrap(retry)), ['previous-attempt'])

    def test_a_stop_inside_an_add_is_uncertain_and_recorded_for_recovery(self):
        executor = self.executor('between-adds')  # lands inside the key add, before its outcome is returned
        receipt = self.run_bootstrap(executor)
        self.assertTrue(receipt['recoveryNeeded'])
        self.assertIn('recovery-needed', codes(receipt))
        self.assertEqual((receipt['secrets']['keys']['state'], receipt['secrets']['passphrase']['state']),
                         ('uncertain', 'untouched'))  # never assumed committed; the passphrase is never added
        self.assertEqual([c[0] for c in executor.calls].count('add'), 1)
        self.assertTrue(receipt['cleanup']['workspaceRemoved'])
        self.assertFalse(self.workspace.exists())
        journal = json.loads((self.fixture.state/'fold-rango-factory-v1.json').read_text())
        self.assertEqual(journal['status'], 'recovery-needed')


class FactorySelfCheck(unittest.TestCase):
    """The pre-import decrypt/sign/verify/negative self-check, through the mocked ssh-keygen only."""

    def generated(self, **kw):
        ws, runner = owner_dir(self), KeygenMock(**kw)
        identity = fs.generate_key(ws, TOOL, runner, token='SYNTHETIC-selfcheck-token')
        return ws, runner, identity

    def test_self_check_decrypts_signs_verifies_and_requires_a_negative(self):
        ws, runner, identity = self.generated()
        result = fs.self_check(ws, TOOL, identity, 'roseos-factory-images-rango-v1', runner)
        self.assertEqual(result, {'decrypt': True, 'signVerify': True, 'negativeRefused': True})
        derive, sign, verify, negative = runner.calls[1:]
        self.assertEqual((derive['argv'][1:3], derive['keepOutput']), (['-y', '-f'], True))
        self.assertEqual(derive['env']['SSH_ASKPASS_REQUIRE'], 'force')  # decrypt only through the helper
        self.assertEqual(sign['argv'][1:5], ['-Y', 'sign', '-n', 'factory images'])
        self.assertEqual(verify['argv'][verify['argv'].index('-n') + 1], 'factory images')
        self.assertEqual(negative['argv'][negative['argv'].index('-n') + 1], fs.SELF_CHECK_NEGATIVE_NAMESPACE)
        self.assertNotIn('SSH_ASKPASS', verify['env'])
        self.assertEqual(sorted(os.listdir(ws)), ['id_ed25519', 'passphrase'])  # self-check material removed
        self.assertNotIn('SYNTHETIC-selfcheck-token', json.dumps(runner.calls, default=str))

    def test_each_failed_step_refuses_and_cleans_up(self):
        for kw, pattern in (({'derive': 'other'}, 'does not decrypt to its public key'),
                            ({'derive': 'fail'}, 'does not decrypt to its public key'),
                            ({'sign': 'timeout'}, 'sign_failed'), ({'verify': 'nonzero_exit'}, 'verify_failed'),
                            ({'negative': 'ok'}, 'another namespace did not fail'),
                            ({'negative': 'timeout'}, 'another namespace did not fail')):
            with self.subTest(kw):
                ws, runner, identity = self.generated(**kw)
                with self.assertRaisesRegex(fs.Refused, 'selfcheck_failed: .*' + pattern):
                    fs.self_check(ws, TOOL, identity, 'roseos-factory-images-rango-v1', runner)
                self.assertEqual(sorted(os.listdir(ws)), ['id_ed25519', 'passphrase'])

    def test_bounded_runner_returns_output_only_when_asked(self):
        def popen(argv, **kw):
            return FakeProc(b'ssh-ed25519 AAAA\n')
        with mock.patch.object(fs.os, 'killpg', lambda pid, sig: None):
            kept = fs.run_bounded(['/opt/synthetic/tool'], {}, '/tmp', 5, popen=popen, keep_output=True)
            hidden = fs.run_bounded(['/opt/synthetic/tool'], {}, '/tmp', 5, popen=popen)
        self.assertEqual(kept['output'], b'ssh-ed25519 AAAA\n')
        self.assertNotIn('output', hidden)


class FactoryProfileValidation(unittest.TestCase):
    def test_access_is_proven_live_so_unrecorded_expiries_are_not_required(self):
        # Owner decision: the run's credentialed metadata calls prove access (any expiry, revocation or wrong grant),
        # so a ready factory profile needs no recorded timestamps; a recorded one is still enforced (next test).
        profiles = factory_ready_profiles()
        profiles['profiles']['fold-rango-factory-v1']['access'].update(addVersionExpires=None, accessorExpires=None, basis=None)
        self.assertEqual(sb.readiness('fold-rango-factory-v1', profiles, NOW), [])
        self.assertEqual(sb.guard(dict(GUARD_ENV, BOOTSTRAP_PROFILE='fold-rango-factory-v1'), profiles, NOW), [])
        # Only profiles that opt in (access.liveCheck) drop the recorded-expiry rule.
        fold = copy.deepcopy(READY)
        fold['profiles']['fold-rango-v1']['access'].update(addVersionExpires=None)
        self.assertIn('access-unknown', [c for c, _ in sb.readiness('fold-rango-v1', fold, NOW)])
        fold['profiles']['fold-rango-v1']['access']['liveCheck'] = True
        self.assertNotIn('access-unknown', [c for c, _ in sb.readiness('fold-rango-v1', fold, NOW)])
        unopted = copy.deepcopy(profiles)
        unopted['profiles']['fold-rango-factory-v1']['access'].pop('liveCheck')
        self.assertIn('access-unknown', [c for c, _ in sb.readiness('fold-rango-factory-v1', unopted, NOW)])
        self.assertEqual({n for n, p in PROFILES['profiles'].items() if (p.get('access') or {}).get('liveCheck') is True},
                         {'fold-rango-factory-v1', 'pro-blazer-factory-v1', 'pro-blazer-v1'})
        checked = PROFILES['profiles']['fold-rango-factory-v1']['access']  # a committed record, if any, is never partial
        self.assertTrue(checked['basis'] or (checked['addVersionExpires'] is None and checked['accessorExpires'] is None))
        # Each opted-in policy text claims the recorded-expiry ceilings only where they are enforced (openssh_problems),
        # and a pre-import recheck only where the run makes one (recheckEmptyBeforeImport, _inside).
        for name, profile in PROFILES['profiles'].items():
            if (profile.get('access') or {}).get('liveCheck') is True:
                openssh = profile['archive']['format'] == 'openssh-ed25519'
                self.assertEqual('held to those ceilings' in profile['access']['policy'], openssh, name)
                self.assertEqual('repeat just before the import' in profile['access']['policy'],
                                 profile.get('recheckEmptyBeforeImport') is True, name)

    def test_a_live_check_profile_must_recheck_and_a_bound_profile_lists_nothing_missing(self):
        profiles = factory_ready_profiles()
        profiles['profiles']['fold-rango-factory-v1']['access'].update(addVersionExpires=None, accessorExpires=None, basis=None)
        self.assertEqual(sb.readiness('fold-rango-factory-v1', profiles, NOW), [])
        # Without recorded expiries there is no margin, so the pre-import recheck is required with liveCheck.
        for name in ('fold-rango-factory-v1', 'fold-rango-v1'):
            source = profiles if name == 'fold-rango-factory-v1' else copy.deepcopy(READY)
            with self.subTest(name):
                bad = copy.deepcopy(source)
                bad['profiles'][name]['access'].update(liveCheck=True, addVersionExpires=None, accessorExpires=None)
                bad['profiles'][name].pop('recheckEmptyBeforeImport', None)
                self.assertIn(('profile-invalid', 'access.liveCheck needs recheckEmptyBeforeImport true'),
                              sb.readiness(name, bad, NOW))
                bad['profiles'][name]['recheckEmptyBeforeImport'] = True
                self.assertNotIn('profile-invalid', [c for c, _ in sb.readiness(name, bad, NOW)])
        self.assertTrue(all(p.get('recheckEmptyBeforeImport') is True for p in PROFILES['profiles'].values()
                            if (p.get('access') or {}).get('liveCheck') is True))
        # A resource-bound profile that still lists a missing prerequisite is refused before anything else.
        pending = copy.deepcopy(profiles)
        pending['profiles']['fold-rango-factory-v1']['missing'] = ['one approved bootstrap run']
        self.assertEqual([c for c, _ in sb.readiness('fold-rango-factory-v1', pending, NOW)], ['profile-unbound'])
        self.assertTrue(sb.guard(dict(GUARD_ENV, BOOTSTRAP_PROFILE='fold-rango-factory-v1'), pending, NOW))
        self.assertFalse([n for n, p in PROFILES['profiles'].items() if p.get('status') == 'resource-bound' and p.get('missing')])
        # While a live-check profile still lists its bootstrap grant, the entry asks for the one binding per secret.
        for name, profile in PROFILES['profiles'].items():
            grants = [m for m in profile.get('missing') or [] if 'versions.add' in str(m)]
            if (profile.get('access') or {}).get('liveCheck') is True and grants:
                with self.subTest(name):
                    self.assertTrue(all('one binding per secret' in m and 'at most 4 h' in m for m in grants), grants)

    def test_after_the_first_live_check_a_denied_add_or_recheck_uses_up_the_run_and_is_never_retried(self):
        profiles = factory_ready_profiles()
        profiles['profiles']['fold-rango-factory-v1']['access'].update(addVersionExpires=None, accessorExpires=None, basis=None)
        runner = KeygenMock()

        class Executor(MockExecutor):
            def tool_sha256(self, path):
                self.calls.append(('tool', path))
                return hashlib.sha256(b'ssh-keygen').hexdigest()

            def generate(self, workspace, profile):
                self.calls.append(('generate',))
                return sb.generate_openssh(workspace, profile, runner=runner, token='SYNTHETIC-token')

        class RecheckDenied(Executor):
            def secret_state(self, project, secret):
                denied = [c for c in self.calls if c[0] == 'generate']
                self.calls.append(('metadata', secret))
                if denied:
                    raise sb.Unavailable('the access lapsed after the first check')
                return {'exists': True, 'versions': []}
        for name, executor, adds, expected in (
                ('add denied', Executor(adds=[sb.Uncertain('versions.add denied')]), 1, 'recovery-needed'),
                ('recheck denied', RecheckDenied(), 0, 'metadata-unavailable')):
            with self.subTest(name):
                fixture = Fixture(self)
                receipt = sb.run('fold-rango-factory-v1', profiles, executor, fixture.state, fixture.lock, SHA, NOW,
                                 workspace=lambda: fixture.workspace(), frankel=FRANKEL)[0]
                self.assertIn(expected, codes(receipt))
                self.assertIn('generate', executor.kinds())  # the key existed: this run is used up
                self.assertEqual(executor.kinds().count('add'), adds)
                self.assertTrue(receipt['cleanup']['workspaceRemoved'])
                journal = json.loads((fixture.state/'fold-rango-factory-v1.json').read_text())
                self.assertIn(journal['status'], ('recovery-needed', 'failed-before-commit'))
                retry = sb.run('fold-rango-factory-v1', profiles, Executor(), fixture.state, fixture.lock, SHA, NOW,
                               workspace=lambda: fixture.workspace(), frankel=FRANKEL)[0]
                self.assertEqual(codes(retry), ['previous-attempt'])  # never retried or regenerated

    def test_plan_tells_the_admin_to_grant_one_binding_per_secret(self):
        plan = sb.plan('fold-rango-factory-v1', PROFILES)
        self.assertIn('one binding per secret', plan['grant'])
        self.assertIn('versions.add', plan['grant'])
        rows = plan['metadata'] + plan['add']  # no row may suggest the split predefined-role grant
        self.assertEqual({r['grantedBy'] for r in rows}, {'the one bootstrap binding on this secret (grant)'})
        self.assertEqual(sorted({r['permission'] for r in rows}),
                         ['secretmanager.secrets.get', 'secretmanager.versions.add', 'secretmanager.versions.list'])
        fold = sb.plan('fold-rango-v1', PROFILES)  # a recorded-expiry profile keeps the predefined roles
        self.assertIsNone(fold['grant'])
        self.assertEqual({r['grantedBy'] for r in fold['metadata'] + fold['add']},
                         {'roles/secretmanager.viewer', 'roles/secretmanager.secretVersionAdder'})

    def test_a_failed_live_access_call_stops_before_any_key_or_journal_and_can_be_retried(self):
        profiles = factory_ready_profiles()
        profiles['profiles']['fold-rango-factory-v1']['access'].update(addVersionExpires=None, accessorExpires=None, basis=None)
        runner, fixture = KeygenMock(), Fixture(self)

        class Executor(MockExecutor):
            def tool_sha256(self, path):
                self.calls.append(('tool', path))
                return hashlib.sha256(b'ssh-keygen').hexdigest()

            def generate(self, workspace, profile):
                self.calls.append(('generate',))
                return sb.generate_openssh(workspace, profile, runner=runner, token='SYNTHETIC-token')

        def bootstrap(executor):
            return sb.run('fold-rango-factory-v1', profiles, executor, fixture.state, fixture.lock, SHA, NOW,
                          workspace=lambda: fixture.workspace(), frankel=FRANKEL)[0]
        for denial in (sb.Unavailable('permission denied: the access expired'), sb.Unavailable('the grant was revoked')):
            with self.subTest(str(denial)):
                executor = Executor(states={'PIXEL_OS_FACTORY_SIGNING_PASSPHRASE_RANGO_V1': denial})
                receipt = bootstrap(executor)
                self.assertEqual((receipt['status'], codes(receipt)), ('blocked', ['metadata-unavailable']))
                self.assertNotIn('generate', executor.kinds())
                self.assertNotIn('add', executor.kinds())
                self.assertFalse((fixture.state/'fold-rango-factory-v1.json').exists())  # nothing used up
        retry = Executor()
        self.assertEqual(bootstrap(retry)['status'], 'complete')

    def test_recorded_expiries_must_fit_the_approved_ceilings_with_a_basis(self):
        for change, code in (({'addVersionExpires': '2026-10-30T02:00:00Z'}, 'access-window'),   # 4 h 30 m
                             ({'accessorExpires': '2026-12-01T00:00:00Z'}, 'access-window'),     # > 30 d
                             ({'addVersionExpires': '2026-11-28T00:00:00Z'}, 'access-window'),   # after accessor
                             ({'basis': None}, 'access-unknown'),
                             ({'addVersionExpires': '2026-10-29T21:45:00Z'}, 'access-expired'),  # inside the margin
                             ({'accessorExpires': None, 'addVersionExpires': '2026-10-30T02:00:00Z'}, 'access-window'),
                             ({'addVersionExpires': None, 'accessorExpires': '2026-12-01T00:00:00Z'}, 'access-window'),
                             ({'addVersionExpires': None, 'basis': None}, 'access-unknown'),
                             ({'addVersionExpires': 'tomorrow'}, 'access-unknown')):
            with self.subTest(change):
                profiles = factory_ready_profiles()
                profiles['profiles']['fold-rango-factory-v1']['access'].update(change)
                self.assertIn(code, [c for c, _ in sb.readiness('fold-rango-factory-v1', profiles, NOW)])

    def test_profile_shape_is_closed(self):
        for name, mutate in (('principal', lambda p: p.update(principal='Root User')),
                             ('inventory', lambda p: p['keyInventory'].update(requiredByConsumer=['id_ed25519', 'x'])),
                             ('extra tool', lambda p: p['tools'].update(openssl={'kind': 'installed', 'installed': {}})),
                             ('recipe', lambda p: p['generation'].update(recipe=[])),
                             ('no recheck', lambda p: p.update(recheckEmptyBeforeImport=False))):
            with self.subTest(name):
                profiles = factory_ready_profiles()
                mutate(profiles['profiles']['fold-rango-factory-v1'])
                self.assertIn('profile-invalid', [c for c, _ in sb.readiness('fold-rango-factory-v1', profiles, NOW)])
        tool = PROFILES['profiles']['fold-rango-factory-v1']['tools']['ssh-keygen']['installed']
        self.assertEqual(set(tool), {'path', 'sha256'})
        self.assertEqual(OBSERVE.committed_problems(), [])  # never invented: null, or exactly the committed measurement


class FactoryImportRecovery(unittest.TestCase):
    """The two-secret import is not atomic: partial or uncertain outcomes stop and are never retried or regenerated."""

    def run_factory(self, fixture, adds, runner=None):
        runner = runner or KeygenMock()

        class Executor(MockExecutor):
            def tool_sha256(self, path):
                self.calls.append(('tool', path))
                return hashlib.sha256(b'ssh-keygen').hexdigest()

            def generate(self, workspace, profile):
                self.calls.append(('generate',))
                return sb.generate_openssh(workspace, profile, runner=runner, token='SYNTHETIC-token')
        executor = Executor(adds=adds)
        receipt = sb.run('fold-rango-factory-v1', factory_ready_profiles(), executor, fixture.state, fixture.lock, SHA, NOW,
                         workspace=lambda: fixture.workspace(), frankel=FRANKEL)[0]
        return receipt, executor, runner

    def test_uncertain_or_partial_import_is_recovery_needed_and_never_repeated(self):
        for adds, committed in (([sb.Uncertain('timeout')], {'keys': None, 'passphrase': None}),
                                (['1', sb.AddFailed('denied')], {'keys': '1', 'passphrase': None}),
                                (['1', sb.Uncertain('lost response')], {'keys': '1', 'passphrase': None})):
            with self.subTest(adds):
                fixture = Fixture(self)
                receipt, executor, _ = self.run_factory(fixture, adds)
                self.assertTrue(receipt['recoveryNeeded'], receipt['reasons'])
                self.assertIn('recovery-needed', codes(receipt))
                self.assertEqual({r: e['committedVersion'] for r, e in receipt['secrets'].items()}, committed)
                self.assertEqual(fixture.journal('fold-rango-factory-v1')['status'], 'recovery-needed')
                again, executor2, runner2 = self.run_factory(fixture, ['1', '1'])
                self.assertEqual(codes(again), ['previous-attempt'])
                self.assertTrue(again['recoveryNeeded'])
                self.assertEqual(executor2.calls, [])  # no lock, metadata, generation or add
                self.assertEqual(runner2.calls, [])    # and no ssh-keygen: never regenerated

    def test_keys_add_refused_before_commit_is_failed_not_retried(self):
        fixture = Fixture(self)
        receipt, executor, _ = self.run_factory(fixture, [sb.AddFailed('denied')])
        self.assertEqual((receipt['status'], receipt['recoveryNeeded']), ('failed', False))
        self.assertEqual([c for c in executor.calls if c[0] == 'add'], [('add', 'PIXEL_OS_FACTORY_SIGNING_KEY_RANGO_V1')])
        self.assertEqual(codes(self.run_factory(fixture, ['1', '1'])[0]), ['previous-attempt'])

    def test_self_check_failure_adds_nothing(self):
        fixture = Fixture(self)
        receipt, executor, _ = self.run_factory(fixture, ['1', '1'], KeygenMock(negative='ok'))
        self.assertIn('selfcheck-failed', codes(receipt))
        self.assertEqual([c for c in executor.calls if c[0] == 'add'], [])
        self.assertEqual(codes(self.run_factory(fixture, ['1', '1'])[0]), ['previous-attempt'])

    def test_an_identity_without_a_complete_self_check_is_never_imported(self):
        for reported in (None, {'decrypt': True, 'signVerify': True, 'negativeRefused': False}):
            with self.subTest(reported):
                fixture, runner = Fixture(self), KeygenMock()

                class Executor(MockExecutor):
                    def tool_sha256(self, path):
                        return hashlib.sha256(b'ssh-keygen').hexdigest()

                    def generate(self, workspace, profile):
                        identity = dict(fs.generate_key(workspace, TOOL, runner), principal=profile['principal'])
                        return identity if reported is None else dict(identity, selfCheck=reported)
                executor = Executor()
                receipt = sb.run('fold-rango-factory-v1', factory_ready_profiles(), executor, fixture.state, fixture.lock,
                                 SHA, NOW, workspace=lambda: fixture.workspace(), frankel=FRANKEL)[0]
                self.assertIn('selfcheck-failed', codes(receipt))
                self.assertEqual([c for c in executor.calls if c[0] == 'add'], [])

    def test_complete_run_records_the_self_check_before_import(self):
        fixture = Fixture(self)
        receipt, executor, runner = self.run_factory(fixture, ['1', '1'])
        self.assertEqual(receipt['status'], 'complete', receipt['reasons'])
        self.assertEqual(receipt['publicIdentity']['selfCheck'], {'decrypt': True, 'signVerify': True, 'negativeRefused': True})
        steps = [c['argv'][1] if c['argv'][1] != '-Y' else c['argv'][2] for c in runner.calls]
        self.assertEqual(steps, ['-q', '-y', 'sign', 'verify', 'verify'])
        self.assertNotIn('SYNTHETIC-token', json.dumps(sb.public_receipt(receipt, [b'SYNTHETIC-token'])))


class BlazerProfiles(unittest.TestCase):
    """BLAZER-SIGN: the two Blazer bootstrap profiles exist but are unbound; nothing can run until approvals land."""

    def test_both_profiles_are_unbound_and_refuse_before_anything(self):
        for name in ('pro-blazer-v1', 'pro-blazer-factory-v1'):
            with self.subTest(name):
                self.assertEqual(sb.readiness(name, PROFILES, NOW)[0][0], 'profile-unbound')
                profile = PROFILES['profiles'][name]
                self.assertEqual((profile['status'], profile['device']), ('unbound', 'blazer'))
                self.assertTrue(profile['missing'])
                # Both are dispatch choices (pro-blazer-v1 by Release's handoff, pro-blazer-factory-v1 by the owner's
                # 2026-11-03 instruction); the guard still refuses each while it is unbound.
                self.assertIn(name, bootstrap_options())

    def test_blazer_secrets_are_new_and_distinct(self):
        profiles = PROFILES['profiles']
        blazer = {v for n in ('pro-blazer-v1', 'pro-blazer-factory-v1') for v in profiles[n]['secrets'].values()}
        others = {v for n, p in profiles.items() if not n.startswith('pro-blazer') for v in p['secrets'].values()}
        self.assertEqual(len(blazer), 4)
        self.assertFalse(blazer & others)
        self.assertTrue(all('BLAZER_V1' in v for v in blazer))

    def test_os_profile_reuses_the_reviewed_fold_recipe_and_tools(self):
        fold, pro = PROFILES['profiles']['fold-rango-v1'], PROFILES['profiles']['pro-blazer-v1']
        for key in ('generation', 'tools', 'keyInventory', 'archive', 'localProof', 'signer', 'project', 'avbAlgorithm'):
            self.assertEqual(pro.get(key), fold.get(key), key)
        self.assertEqual((pro['access']['addVersionExpires'], pro['access']['accessorExpires']), (None, None))

    def test_factory_profile_shape_matches_rango_with_its_own_principal(self):
        rango, pro = PROFILES['profiles']['fold-rango-factory-v1'], PROFILES['profiles']['pro-blazer-factory-v1']
        self.assertEqual(pro['principal'], 'roseos-factory-images-blazer-v1')
        # status, access and resourceBasis are per-profile records; the measured identity is held equal-or-null to the
        # committed evidence by ssh-keygen-observe.py committed_problems.
        def shape(profile):
            value = copy.deepcopy({k: v for k, v in profile.items()
                                   if k not in ('device', 'principal', 'purpose', 'secrets', 'missing', 'status', 'access',
                                                'resourceBasis')})
            value['tools']['ssh-keygen'].pop('installed')
            return value
        self.assertEqual(shape(pro), shape(rango))
        # The committed Blazer profile lacks only its approvals: status alone never admits it while its missing list
        # names them; marked resource-bound with nothing missing it is ready as committed (measured tool, live access,
        # no recorded expiry needed).
        ready = copy.deepcopy(PROFILES)
        ready['profiles']['pro-blazer-factory-v1']['status'] = 'resource-bound'
        self.assertEqual([c for c, _ in sb.readiness('pro-blazer-factory-v1', ready, NOW)], ['profile-unbound'])
        ready['profiles']['pro-blazer-factory-v1']['missing'] = []
        self.assertEqual(sb.readiness('pro-blazer-factory-v1', ready, NOW), [])
        recorded = copy.deepcopy(ready)  # a recorded expiry inside the ceilings, with its basis, is still accepted
        recorded['profiles']['pro-blazer-factory-v1']['access'].update(
            addVersionExpires='2026-10-30T00:30:00Z', accessorExpires='2026-11-27T21:30:00Z', basis='synthetic test readback')
        self.assertEqual(sb.readiness('pro-blazer-factory-v1', recorded, NOW), [])


if __name__ == '__main__':
    unittest.main()
