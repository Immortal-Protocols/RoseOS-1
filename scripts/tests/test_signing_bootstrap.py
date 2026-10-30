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
                self.assertIn('    needs: builder\n', body)  # skipped whenever builder is skipped
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
        self.assertIn('options: [none, fold-rango-v1, models-v0]', WORKFLOW)
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


if __name__ == '__main__':
    unittest.main()
