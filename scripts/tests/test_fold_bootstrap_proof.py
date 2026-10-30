"""OCT30-INFRA-S016: the Fold bootstrap's same-run local tool proof (scripts/signing-bootstrap.py prove/run).

The Models Product helper is replaced here by a MOCK module with the agreed interface (validate_spec, verify_local,
recheck, Refused), admitted by its own git blob. Nothing here proves real tools, reads a host, a secret or the network,
and the excluded PR #190 helper is never loaded."""
import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS/'tests'))
import test_signing_bootstrap as base  # noqa: E402  (shared synthetic fixtures: executor, workspace, archive)

sb = base.sb
SHA, NOW = base.SHA, base.NOW
RUN = ('41100000000', '1')
PROFILES = base.PROFILES

MOCK_HELPER = '''"""MOCK fold_tool_proof for OCT30-INFRA-S016 tests: the agreed interface only, no verification."""
import fcntl, hashlib, json, os
CONTROL = {control!r}
LOCK = {lock!r}

class Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

def _control():
    with open(CONTROL) as f:
        return json.load(f)

def _log(event):
    with open(CONTROL + '.log', 'a') as f:
        f.write(event + '\\n')

def validate_spec(spec):
    return list(_control().get('specProblems', []))

def verify_local(spec, root='/'):
    c = _control()
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _log('verify-without-lock')
    except BlockingIOError:
        _log('verify-under-lock')
    finally:
        os.close(fd)
    if c.get('verifyRefuses'):
        raise Refused(c['verifyRefuses'])
    tools = {{n: {{'path': '/opt/proven/' + n, 'sha256': hashlib.sha256(n.encode()).hexdigest(), 'trustBasis': 'mock'}}
        for n in ('generator', 'decrypt-keys', 'avbtool', 'openssl')}}
    proof = {{'schema': 'mock.fold-tool-proof', 'profile': 'fold-rango-v1', 'tools': tools,
        'environment': {{'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}}, 'host': {{'ID': 'debian', 'VERSION_ID': '12'}}}}
    proof.update(c.get('proofOverride', {{}}))
    return proof

def recheck(proof, root='/'):
    _log('recheck')
    if _control().get('recheckRefuses'):
        raise Refused(_control()['recheckRefuses'])
'''


def blob(path):
    data = Path(path).read_bytes()
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


class Harness:
    def __init__(self, test, **control):
        self.tmp = Path(tempfile.mkdtemp(prefix='fold-proof-'))
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.fixture = base.Fixture(test)
        self.control_path = self.tmp/'control.json'
        self.control(**control)
        self.helper = self.tmp/'fold_tool_proof_mock.py'
        self.helper.write_text(MOCK_HELPER.format(control=str(self.control_path), lock=str(self.fixture.lock)))
        self.profiles = copy.deepcopy(base.READY)
        fold = self.profiles['profiles']['fold-rango-v1']
        local = copy.deepcopy(PROFILES['profiles']['fold-rango-v1']['localProof'])
        local.update(helper=str(self.helper), admittedRevision='c' * 40, admittedBlob=blob(self.helper),
            spec={'schema': 'mock.fold-tool-proof-spec', 'profile': 'fold-rango-v1'})
        fold['localProof'] = local
        for tool in fold['tools'].values():  # the proof mode never needs a checked-in installed identity
            tool['installed'] = {'path': None, 'sha256': None}
        self.proof_dir = self.tmp/'proof'
        self.env = {'GITHUB_SHA': SHA, 'GCP_SIGNER_SERVICE_ACCOUNT': fold['signer'], 'GITHUB_RUN_ID': RUN[0],
            'GITHUB_RUN_ATTEMPT': RUN[1]}

    def control(self, **values):
        self.control_path.write_text(json.dumps(values))

    def log(self):
        path = Path(str(self.control_path) + '.log')
        return path.read_text().split() if path.exists() else []

    def prove(self, env=None):
        return sb.prove('fold-rango-v1', self.profiles, env or self.env, SHA, self.proof_dir, self.fixture.lock, now=NOW)

    def binding(self):
        return json.loads((self.proof_dir/sb.PROOF_FILE).read_text())

    def run(self, executor, binding='same', run_identity=RUN):
        binding = self.binding() if binding == 'same' else binding
        return sb.run('fold-rango-v1', self.profiles, executor, self.fixture.state, self.fixture.lock, SHA, NOW,
            workspace=lambda: self.fixture.workspace(), frankel=base.FRANKEL, proof=binding, run_identity=run_identity)


class Admission(unittest.TestCase):
    def test_checked_in_profile_refuses_the_excluded_pr190_helper(self):
        local = PROFILES['profiles']['fold-rango-v1']['localProof']
        self.assertEqual((local['helper'], local['admittedRevision'], local['admittedBlob'], local['spec']),
            ('scripts/fold_tool_proof.py', None, None, None))
        self.assertEqual(local['excluded'][0]['revision'], '97b23c30b0898aacb563ed7567671adf340917c0')
        reasons = sb.readiness('fold-rango-v1', PROFILES, NOW)
        if blob(ROOT/'scripts/fold_tool_proof.py') == local['excluded'][0]['gitBlob']:
            self.assertEqual([c for c, _ in reasons], ['proof-helper-excluded'])
        else:  # once the corrected helper replaces it on main, it is still unadmitted until pinned here
            self.assertEqual([c for c, _ in reasons], ['proof-helper-unadmitted'])
        h = Harness(self)
        self.assertEqual(sb.prove('fold-rango-v1', PROFILES, h.env, SHA, h.proof_dir, h.fixture.lock, now=NOW)[0], 'blocked')
        self.assertFalse(h.proof_dir.exists())

    def test_excluded_blob_refuses_even_if_admitted(self):
        h = Harness(self)
        local = h.profiles['profiles']['fold-rango-v1']['localProof']
        local['excluded'] = [{'gitBlob': local['admittedBlob']}]
        self.assertEqual([c for c, _ in sb.readiness('fold-rango-v1', h.profiles, NOW)], ['proof-helper-excluded'])

    def test_unadmitted_mismatched_or_missing_helper_and_spec_problems_refuse(self):
        for change, code in ((lambda l: l.update(admittedBlob=None), 'proof-helper-unadmitted'),
                             (lambda l: l.update(admittedRevision='main'), 'proof-helper-unadmitted'),
                             (lambda l: l.update(admittedBlob='d' * 40), 'proof-helper-mismatch'),
                             (lambda l: l.update(helper='scripts/no-such-helper.py'), 'proof-helper-missing'),
                             (lambda l: l.update(spec=None), 'proof-spec-missing')):
            h = Harness(self)
            change(h.profiles['profiles']['fold-rango-v1']['localProof'])
            self.assertEqual([c for c, _ in sb.readiness('fold-rango-v1', h.profiles, NOW)], [code])
        h = Harness(self, specProblems=['source-unpinned', 'bad code with spaces'])
        self.assertEqual(sb.readiness('fold-rango-v1', h.profiles, NOW),
            [('proof-spec-invalid', 'source-unpinned'), ('proof-spec-invalid', 'unrecognised-code')])

    def test_models_profile_needs_no_proof(self):
        self.assertNotIn('localProof', PROFILES['profiles']['models-v0'])
        h = Harness(self)
        self.assertEqual(sb.prove('models-v0', PROFILES, h.env, SHA, h.proof_dir, h.fixture.lock), ('not-required', []))


class ConnectedRoute(unittest.TestCase):
    def test_valid_complete_proof_reaches_generation_and_import(self):
        h = Harness(self)
        self.assertEqual(sb.readiness('fold-rango-v1', h.profiles, NOW), [])
        self.assertEqual(h.prove(), ('proven', []))
        self.assertEqual(h.log(), ['verify-under-lock'])
        self.assertFalse(h.fixture.lock.exists() and _held(h.fixture.lock))  # released before login
        binding = h.binding()
        self.assertEqual((binding['profile'], binding['recipeSha'], binding['runId'], binding['runAttempt']),
            ('fold-rango-v1', SHA, RUN[0], RUN[1]))
        self.assertEqual(oct((h.proof_dir/sb.PROOF_FILE).stat().st_mode & 0o777), '0o600')
        executor = base.MockExecutor()
        receipt, _ = h.run(executor)
        self.assertEqual(receipt['status'], 'complete', receipt['reasons'])
        # The bound proof is rechecked under the lock before any metadata, then both containers are checked empty.
        self.assertEqual(h.log(), ['verify-under-lock', 'recheck'])
        kinds = executor.kinds()
        self.assertEqual(kinds[:4], ['tool'] * 4)
        self.assertEqual(kinds[4:], ['metadata', 'metadata', 'generate', 'add', 'add'])
        self.assertEqual({c[1] for c in executor.calls if c[0] == 'tool'}, {f'/opt/proven/{t}' for t in sb.PROOF_TOOLS})
        self.assertEqual(receipt['tools']['openssl'], {'trustBasis': 'local-proof', 'path': '/opt/proven/openssl',
            'sha256': hashlib.sha256(b'openssl').hexdigest()})
        self.assertEqual(receipt['localProof']['proofSha256'], binding['proofSha256'])
        public = sb.public_receipt(receipt)
        self.assertIn('localProof', public)
        # The checked-in profile is never rewritten from runtime observations.
        self.assertEqual(h.profiles['profiles']['fold-rango-v1']['tools']['openssl']['installed'], {'path': None, 'sha256': None})

    def test_proof_refusals_happen_before_any_key_or_metadata(self):
        cases = [(dict(verifyRefuses='distro_unsupported'), ('refused', ['distro_unsupported'])),
                 (dict(verifyRefuses='official_verifier_failed'), ('refused', ['official_verifier_failed'])),
                 (dict(verifyRefuses='not a fixed code!'), ('refused', ['proof-refused'])),
                 (dict(proofOverride={'profile': 'models-v0'}), ('refused', ['proof-foreign'])),
                 (dict(proofOverride={'environment': {'PATH': '/tmp:/usr/bin', 'LC_ALL': 'C'}}), ('refused', ['proof-environment'])),
                 (dict(proofOverride={'tools': {'generator': {'path': 'relative', 'sha256': 'x'}}}), ('refused', ['proof-tools'])),
                 (dict(proofOverride={'schema': None}), ('refused', ['proof-malformed']))]
        for control, expected in cases:
            h = Harness(self, **control)
            self.assertEqual(h.prove(), expected, control)
            self.assertFalse(h.proof_dir.exists(), control)
            executor = base.MockExecutor()
            receipt, _ = h.run(executor, binding=None)
            self.assertEqual((receipt['status'], base.codes(receipt)), ('blocked', ['proof-missing']), control)
            self.assertEqual(executor.calls, [], control)
            self.assertEqual(h.fixture.created, [], control)

    def test_wrong_run_changed_or_drifted_proof_is_rejected_before_metadata(self):
        h = Harness(self)
        h.prove()
        good = h.binding()
        variants = {
            'other run': (good, ('41100000001', '1'), 'proof-binding'),
            'other attempt': (good, (RUN[0], '2'), 'proof-binding'),
            'edited proof': (dict(good, proof=dict(good['proof'], host={'ID': 'ubuntu'})), RUN, 'proof-binding'),
            'other recipe': (dict(good, recipeSha='b' * 40), RUN, 'proof-binding'),
            'other helper': (dict(good, helperBlob='e' * 40), RUN, 'proof-binding'),
            'not a binding': ('junk', RUN, 'proof-missing'),
        }
        for name, (binding, identity, code) in variants.items():
            executor = base.MockExecutor()
            receipt, _ = h.run(executor, binding=binding, run_identity=identity)
            self.assertEqual((receipt['status'], base.codes(receipt)), ('blocked', [code]), name)
            self.assertEqual(executor.calls, [], name)
        h.control(recheckRefuses='changed_after_proof')
        executor = base.MockExecutor()
        receipt, _ = h.run(executor)
        self.assertEqual((receipt['status'], base.codes(receipt), receipt['reasons'][0]['detail']),
            ('blocked', ['proof-drift'], 'changed_after_proof'))
        self.assertEqual(executor.calls, [])

    def test_installed_bytes_must_still_match_the_proof(self):
        h = Harness(self)
        h.prove()
        executor = base.MockExecutor(tools={'avbtool': 'f' * 64})
        receipt, _ = h.run(executor)
        self.assertEqual(base.codes(receipt), ['tool-identity'])
        self.assertNotIn('metadata', executor.kinds())

    def test_nonempty_partial_journal_and_metadata_denial_still_refuse_before_keys(self):
        h = Harness(self)
        h.prove()
        for states, code in (({'PIXEL_OS_SIGNING_KEYS_RANGO_V1': {'exists': True, 'versions': ['1']}}, 'secret-occupied'),
                             ({'PIXEL_OS_SIGNING_PASSPHRASE_RANGO_V1': sb.Unavailable('denied')}, 'metadata-unavailable')):
            executor = base.MockExecutor(states=states)
            receipt, _ = h.run(executor)
            self.assertEqual(base.codes(receipt), [code])
            self.assertNotIn('generate', executor.kinds())
        (h.fixture.state/'fold-rango-v1.json').write_text(json.dumps({'status': 'importing'}))
        executor = base.MockExecutor()
        receipt, _ = h.run(executor)
        self.assertEqual((base.codes(receipt), receipt['recoveryNeeded'], executor.calls), (['previous-attempt'], True, []))

    def test_held_lock_refuses_the_proof(self):
        h = Harness(self)
        fd = os.open(h.fixture.lock, os.O_RDWR | os.O_CREAT)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.assertEqual(h.prove(), ('blocked', ['lock-held']))
        self.assertEqual(h.log(), [])

    def test_cli_prints_only_a_fixed_summary(self):
        h = Harness(self, verifyRefuses='package_unavailable')
        saved = sb.load_profiles
        sb.load_profiles = lambda root=None: h.profiles
        self.addCleanup(setattr, sb, 'load_profiles', saved)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sb.main(['prove', '--profile', 'fold-rango-v1', '--recipe-sha', SHA, '--proof-dir', str(h.proof_dir)],
                env=dict(h.env))
        # The CLI uses the pipeline lock path; on this runner it may be absent, which is a fixed refusal too.
        self.assertEqual(code, 4)
        lines = out.getvalue().splitlines()
        self.assertTrue(all(re.fullmatch(r'::error::signing-bootstrap prove: [a-z][a-z0-9_-]*|\{"status": "\w+", "codes": \[.*\]\}', l)
            for l in lines), lines)


def _held(path):
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()


class Workflow(unittest.TestCase):
    def job(self):
        return WORKFLOW.split('\n  signing-bootstrap:\n', 1)[1].split('\n\n  ', 1)[0]

    def test_proof_runs_before_login_and_binds_the_run(self):
        job = self.job()
        order = [job.index(s) for s in ('signing-bootstrap.py preflight', 'signing-bootstrap.py prove', 'actions-gcp-login.py login',
            'signing-bootstrap.py run', 'actions-gcp-login.py logout')]
        self.assertEqual(order, sorted(order))
        self.assertIn('--proof-dir "$RUNNER_TEMP/signing-bootstrap-proof"', job)
        self.assertIn('--proof-file "$RUNNER_TEMP/signing-bootstrap-proof/fold-tool-proof.json"', job)
        self.assertIn("      - name: Drop signer access\n        if: always()\n", job)
        self.assertNotIn('fold_tool_proof.py', WORKFLOW)  # only the admitted blob, through signing-bootstrap.py

    def test_actual_ci_runs_this_suite(self):
        ci = (ROOT/'.github/workflows/release-verification.yml').read_text()
        self.assertIn('          python3 scripts/tests/test_fold_bootstrap_proof.py\n', ci)
        self.assertIn('python3 scripts/tests/test_fold_bootstrap_proof.py\n', (ROOT/'scripts/test-local.sh').read_text())


if __name__ == '__main__':
    unittest.main()
