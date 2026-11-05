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
    def test_checked_in_profile_admits_only_the_corrected_helper(self):
        local = PROFILES['profiles']['fold-rango-v1']['localProof']
        self.assertEqual((local['helper'], local['admittedRevision'], local['admittedBlob']),
            ('scripts/fold_tool_proof.py', 'b4e317ad6af4a719ffa41f9deffe70f3e1d298af', '66f52b19408e50aa8520fc9b6ae1b9f72c7bfb46'))
        self.assertEqual(local['excluded'][0]['revision'], '97b23c30b0898aacb563ed7567671adf340917c0')
        self.assertNotEqual(local['admittedBlob'], local['excluded'][0]['gitBlob'])
        self.assertEqual(blob(ROOT/'scripts/fold_tool_proof.py'), local['admittedBlob'])  # main carries the admitted bytes
        self.assertEqual(sb.readiness('fold-rango-v1', PROFILES, NOW), [])
        helper, _ = sb.load_proof_helper(PROFILES['profiles']['fold-rango-v1'])
        self.assertEqual(helper.validate_spec(local['spec']), [])
        # On this runner the builder tree is absent: the real helper refuses with a fixed code and nothing is bound.
        h = Harness(self)
        status, codes = sb.prove('fold-rango-v1', PROFILES, h.env, SHA, h.proof_dir, h.fixture.lock, now=NOW)
        self.assertEqual(status, 'refused')
        self.assertTrue(codes and all(re.fullmatch(r'[a-z][a-z0-9_-]*', c) for c in codes), codes)
        self.assertFalse(h.proof_dir.exists())

    def test_the_excluded_pr190_helper_bytes_refuse(self):
        # History-independent (CI checks out one commit): any helper file whose git blob is the recorded PR #190 blob
        # refuses, even when a profile names that same blob as admitted.
        excluded = PROFILES['profiles']['fold-rango-v1']['localProof']['excluded'][0]
        self.assertEqual((excluded['pr'], excluded['gitBlob']), (190, 'a42ec8a45d3e9758822dfd726536d42b0ff26fb4'))
        h = Harness(self)
        h.profiles['profiles']['fold-rango-v1']['localProof']['admittedBlob'] = excluded['gitBlob']
        saved = sb.git_blob_id
        sb.git_blob_id = lambda path, end, clock: excluded['gitBlob']
        self.addCleanup(setattr, sb, 'git_blob_id', saved)
        self.assertEqual([c for c, _ in sb.readiness('fold-rango-v1', h.profiles, NOW)], ['proof-helper-excluded'])
        self.assertEqual(h.prove(), ('blocked', ['proof-helper-excluded']))
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


class ReusedSubject(unittest.TestCase):
    """pro-blazer-v1 reuses fold-rango-v1's admitted spec and tools. The admitted helper pins its own subject (its
    validate_spec accepts only spec.profile 'fold-rango-v1' and every proof names it), so the consumer compares the proof
    with the subject its spec pins, and only while its localProof and whole tool set are exactly the subject's."""

    def harness(self, **control):
        h = Harness(self, **control)
        fold = h.profiles['profiles']['fold-rango-v1']
        blazer = copy.deepcopy(fold)
        blazer.update(device='blazer', purpose='synthetic blazer copy of the ready fold profile',
                      secrets={'keys': 'PIXEL_OS_SIGNING_KEYS_BLAZER_V1', 'passphrase': 'PIXEL_OS_SIGNING_PASSPHRASE_BLAZER_V1'})
        h.profiles['profiles']['pro-blazer-v1'] = blazer
        return h, blazer

    def prove(self, h):
        return sb.prove('pro-blazer-v1', h.profiles, h.env, SHA, h.proof_dir, h.fixture.lock, now=NOW)

    def run_blazer(self, h, executor, binding='same'):
        binding = h.binding() if binding == 'same' else binding
        return sb.run('pro-blazer-v1', h.profiles, executor, h.fixture.state, h.fixture.lock, SHA, NOW,
            workspace=lambda: h.fixture.workspace(), frankel=base.FRANKEL, proof=binding, run_identity=RUN)

    def test_checked_in_blazer_profile_reuses_exactly_the_pinned_subject(self):
        profiles = PROFILES['profiles']
        blazer, fold = profiles['pro-blazer-v1'], profiles['fold-rango-v1']
        self.assertEqual(blazer['localProof']['spec']['profile'], 'fold-rango-v1')
        self.assertEqual(sb.proof_subject('pro-blazer-v1', blazer, PROFILES), ('fold-rango-v1', None))
        proof = {'schema': 'roseos.fold-tool-proof.v2', 'profile': 'fold-rango-v1', 'environment': dict(sb.RUNNER_ENVIRONMENT),
                 'tools': {t: {'path': f'/usr/bin/{t}', 'sha256': 'a' * 64} for t in sb.PROOF_TOOLS}}
        self.assertEqual(sb.proof_problems('pro-blazer-v1', blazer, proof, PROFILES), [])
        self.assertEqual(sb.proof_problems('fold-rango-v1', fold, proof, PROFILES), [])
        # Without the reviewed set the reuse cannot be shown, and a proof naming the consumer is foreign.
        self.assertEqual(sb.proof_problems('pro-blazer-v1', blazer, proof), ['proof-subject'])
        self.assertEqual(sb.proof_problems('pro-blazer-v1', blazer, dict(proof, profile='pro-blazer-v1'), PROFILES),
                         ['proof-foreign'])
        self.assertEqual(sb.proof_problems('fold-rango-v1', fold, proof), [])  # the subject itself needs no reviewed set

    def test_identical_copy_proves_binds_and_imports(self):
        h, blazer = self.harness()
        self.assertEqual(sb.readiness('pro-blazer-v1', h.profiles, NOW), [])
        self.assertEqual(self.prove(h), ('proven', []))
        binding = h.binding()
        self.assertEqual((binding['profile'], binding['proof']['profile']), ('pro-blazer-v1', 'fold-rango-v1'))
        executor = base.MockExecutor(archive=base.archive_bytes(prefix='blazer/'))
        receipt, _ = self.run_blazer(h, executor)
        self.assertEqual(receipt['status'], 'complete', receipt['reasons'])
        self.assertEqual(h.log(), ['verify-under-lock', 'recheck'])
        self.assertEqual({c[1] for c in executor.calls if c[0] == 'tool'}, {f'/opt/proven/{t}' for t in sb.PROOF_TOOLS})
        self.assertEqual(blazer['tools']['openssl']['installed'], {'path': None, 'sha256': None})  # never rewritten

    def test_reuse_refuses_unless_spec_and_tools_are_exactly_the_subjects(self):
        def other_tool(profiles):
            tool = profiles['profiles']['pro-blazer-v1']['tools']['avbtool']
            tool['sourceSha256'] = '0' * 64

        def other_local(profiles):
            profiles['profiles']['pro-blazer-v1']['localProof']['admissionBasis'] = 'a different admission'

        def no_subject(profiles):
            profiles['profiles']['pro-blazer-v1']['localProof']['spec'] = dict(
                profiles['profiles']['pro-blazer-v1']['localProof']['spec'], profile='not-a-reviewed-profile')

        for name, change in (('tools differ', other_tool), ('localProof differs', other_local), ('no reviewed subject', no_subject)):
            h, _ = self.harness()
            change(h.profiles)
            self.assertEqual(self.prove(h), ('refused', ['proof-subject', 'proof-foreign'] if name == 'no reviewed subject'
                                             else ['proof-subject']), name)
            self.assertFalse(h.proof_dir.exists(), name)
            executor = base.MockExecutor(archive=base.archive_bytes(prefix='blazer/'))
            receipt, _ = self.run_blazer(h, executor, binding=None)
            self.assertEqual((receipt['status'], base.codes(receipt)), ('blocked', ['proof-missing']), name)
            self.assertEqual(executor.calls, [], name)

    def test_a_proof_bound_before_the_tools_diverged_is_refused_before_metadata(self):
        h, _ = self.harness()
        self.assertEqual(self.prove(h), ('proven', []))
        h.profiles['profiles']['pro-blazer-v1']['tools']['generator']['sourceSha256'] = '1' * 64
        executor = base.MockExecutor(archive=base.archive_bytes(prefix='blazer/'))
        receipt, _ = self.run_blazer(h, executor)
        self.assertEqual((receipt['status'], base.codes(receipt)), ('blocked', ['proof-subject']))
        self.assertEqual(executor.calls, [])
        self.assertEqual(h.log(), ['verify-under-lock'])  # refused before the helper's recheck

    def test_foreign_subject_and_cross_profile_binding_still_refuse(self):
        h, _ = self.harness(proofOverride={'profile': 'pro-blazer-v1'})
        self.assertEqual(self.prove(h), ('refused', ['proof-foreign']))
        h, _ = self.harness()
        self.assertEqual(h.prove(), ('proven', []))  # a fold-rango-v1 binding ...
        executor = base.MockExecutor(archive=base.archive_bytes(prefix='blazer/'))
        receipt, _ = self.run_blazer(h, executor)  # ... is never accepted for pro-blazer-v1
        self.assertEqual((receipt['status'], base.codes(receipt)), ('blocked', ['proof-binding']))
        self.assertEqual(executor.calls, [])


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


YOGI_ROOTS = {'generator': '/srv/rose-yogi/src/development', 'decrypt-keys': '/srv/rose-yogi/src/script',
              'avbtool': '/srv/rose-yogi/src/external/avb'}


def relocated(profile, roots):
    """A copy of `profile` whose spec is the reviewed fold-rango-v1 spec with the given script roots."""
    copied = copy.deepcopy(profile)
    spec = copy.deepcopy(PROFILES['profiles']['fold-rango-v1']['localProof']['spec'])
    for script, root in roots.items():
        spec['scripts'][script] = {'root': root} if isinstance(root, str) else root
    copied['localProof']['spec'] = spec
    copied['localProof']['specBasis'] = 'the dedicated builder tree (synthetic test roots, not measured)'
    return copied


class RelocatedSubjectTree(unittest.TestCase):
    """A dedicated builder keeps its own source tree: a consumer may move the subject's tree (one common prefix, each
    script at the subject's project path) while everything else in localProof and every tool stays the subject's. The
    helper, its admission, pinned blobs and host checks are unchanged, so the bytes at the new roots are still verified."""

    PROOF = {'schema': 'roseos.fold-tool-proof.v2', 'profile': 'fold-rango-v1', 'environment': dict(sb.RUNNER_ENVIRONMENT),
             'tools': {t: {'path': f'/usr/bin/{t}', 'sha256': 'a' * 64} for t in sb.PROOF_TOOLS}}

    def yogi(self, roots=YOGI_ROOTS):
        return relocated(PROFILES['profiles']['yogi-v1'], roots)

    def test_yogi_tree_reuses_the_subject_and_the_frankel_tree_still_does(self):
        yogi = self.yogi()
        self.assertEqual(sb.proof_subject('yogi-v1', yogi, PROFILES), ('fold-rango-v1', None))
        self.assertEqual(sb.proof_problems('yogi-v1', yogi, self.PROOF, PROFILES), [])
        self.assertEqual(sb.proof_problems('yogi-v1', yogi, dict(self.PROOF, profile='yogi-v1'), PROFILES), ['proof-foreign'])
        self.assertEqual(sb.proof_problems('yogi-v1', yogi, self.PROOF), ['proof-subject'])  # no reviewed set: refused
        self.assertEqual(sb.proof_subject('pro-mustang-v1', PROFILES['profiles']['pro-mustang-v1'], PROFILES),
                         ('fold-rango-v1', None))  # unchanged: identical localProof
        ftp = importlib.util.spec_from_file_location('ftp_relocated', ROOT/'scripts/fold_tool_proof.py')
        module = importlib.util.module_from_spec(ftp)
        ftp.loader.exec_module(module)
        self.assertEqual(module.validate_spec(yogi['localProof']['spec']), [])  # the admitted helper accepts the shape
        with tempfile.TemporaryDirectory() as tmp:  # roots are verified, never trusted: wrong bytes there refuse
            spec = copy.deepcopy(yogi['localProof']['spec'])
            for script, project in (('generator', 'development/tools/make_key'), ('decrypt-keys', 'script/decrypt-keys'),
                                    ('avbtool', 'external/avb/avbtool.py')):
                spec['scripts'][script]['root'] = tmp + '/src/' + project.split('/')[0] + (
                    '/avb' if script == 'avbtool' else '')
                target = Path(tmp)/'src'/project
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('#!/bin/bash\necho not the pinned script\n')
            self.assertEqual(module.validate_spec(spec), [])
            with self.assertRaises(module.Refused) as caught:
                module.verify_local(spec)
            self.assertEqual(str(caught.exception), 'source_mismatch')

    def test_relocated_profile_is_ready_only_when_otherwise_bound(self):
        profiles = copy.deepcopy(PROFILES)
        yogi = self.yogi()
        profiles['profiles']['yogi-v1'] = yogi
        self.assertIn('profile-unbound', [code for code, _ in sb.readiness('yogi-v1', profiles, NOW)])  # status unchanged
        yogi.update(status='resource-bound', missing=[])
        self.assertEqual(sb.readiness('yogi-v1', profiles, NOW), [])

    def test_anything_but_a_whole_tree_move_is_refused(self):
        cases = {
            'project path differs': dict(YOGI_ROOTS, avbtool='/srv/rose-yogi/src/avb'),
            'same depth, other project': dict(YOGI_ROOTS, avbtool='/srv/rose-yogi/src/external/vbmeta'),
            'scripts swapped': dict(YOGI_ROOTS, generator=YOGI_ROOTS['decrypt-keys'], **{'decrypt-keys': YOGI_ROOTS['generator']}),
            'two trees': dict(YOGI_ROOTS, generator='/srv/other/src/development'),
            'root is the project at /': {s: '/' + r.split('/src/', 1)[1] for s, r in YOGI_ROOTS.items()},
            'relative root': dict(YOGI_ROOTS, generator='srv/rose-yogi/src/development'),
            'dot-dot root': dict(YOGI_ROOTS, generator='/srv/rose-yogi/src/../src/development'),
            'extra root field': dict(YOGI_ROOTS, generator={'root': YOGI_ROOTS['generator'], 'sha256': '0' * 64}),
            'non-string root': dict(YOGI_ROOTS, generator={'root': None}),
        }
        for name, roots in cases.items():
            with self.subTest(name):
                self.assertEqual(sb.proof_subject('yogi-v1', self.yogi(roots), PROFILES), ('fold-rango-v1', 'proof-subject'))

        def without_script(p):
            del p['localProof']['spec']['scripts']['avbtool']

        def extra_script(p):
            p['localProof']['spec']['scripts']['openssl'] = {'root': '/srv/rose-yogi/src/openssl'}

        def other_schema(p):
            p['localProof']['spec']['schema'] = 'roseos.fold-tool-proof-spec.v3'

        def extra_spec_field(p):
            p['localProof']['spec']['roots'] = '/srv/rose-yogi/src'

        def other_blob(p):
            p['localProof']['admittedBlob'] = '0' * 40

        def other_image(p):
            p['localProof']['hostImage'] = dict(p['localProof']['hostImage'], image='debian-cloud/other')

        def extra_local_field(p):
            p['localProof']['note'] = 'x'

        def other_tool(p):
            p['tools']['avbtool'] = dict(p['tools']['avbtool'], sourceSha256='0' * 64)

        def no_spec(p):
            p['localProof']['spec'] = None

        for change in (without_script, extra_script, other_schema, extra_spec_field, other_blob, other_image,
                       extra_local_field, other_tool):
            with self.subTest(change.__name__):
                yogi = self.yogi()
                change(yogi)
                self.assertEqual(sb.proof_subject('yogi-v1', yogi, PROFILES), ('fold-rango-v1', 'proof-subject'))
        yogi = self.yogi()
        no_spec(yogi)
        self.assertEqual(sb.proof_subject('yogi-v1', yogi, PROFILES), (None, 'proof-subject'))
        yogi = self.yogi()
        yogi['localProof']['spec']['profile'] = 'pro-mustang-v1'  # only the helper's own subject is reviewed by it
        self.assertEqual(sb.proof_subject('yogi-v1', yogi, PROFILES)[1], 'proof-subject')
        self.assertEqual(sb.relocated_spec(None, PROFILES['profiles']['fold-rango-v1']['localProof']['spec']), False)

    def test_relocated_copy_proves_binds_and_imports(self):
        h = Harness(self)
        fold = h.profiles['profiles']['fold-rango-v1']
        fold['localProof']['spec']['scripts'] = {s: {'root': '/home/pixelbuild/tree/' + r.split('/src/', 1)[1]}
                                                 for s, r in YOGI_ROOTS.items()}
        moved = copy.deepcopy(fold)
        moved['localProof']['spec']['scripts'] = {s: {'root': r} for s, r in YOGI_ROOTS.items()}
        moved['localProof']['specBasis'] = 'synthetic dedicated tree'
        moved.update(device='yogi', secrets={'keys': 'PIXEL_OS_SIGNING_KEYS_YOGI_V1',
                                             'passphrase': 'PIXEL_OS_SIGNING_PASSPHRASE_YOGI_V1'})
        h.profiles['profiles']['yogi-v1'] = moved
        self.assertEqual(sb.readiness('yogi-v1', h.profiles, NOW), [])
        self.assertEqual(sb.prove('yogi-v1', h.profiles, h.env, SHA, h.proof_dir, h.fixture.lock, now=NOW), ('proven', []))
        executor = base.MockExecutor(archive=base.archive_bytes(prefix='yogi/'))
        receipt, _ = sb.run('yogi-v1', h.profiles, executor, h.fixture.state, h.fixture.lock, SHA, NOW,
                            workspace=lambda: h.fixture.workspace(), frankel=base.FRANKEL, proof=h.binding(), run_identity=RUN)
        self.assertEqual(receipt['status'], 'complete', receipt['reasons'])
        self.assertEqual(h.log(), ['verify-under-lock', 'recheck'])
        moved['localProof']['spec']['scripts']['avbtool'] = {'root': '/srv/rose-yogi/src/avb'}  # not a tree move
        shutil.rmtree(h.proof_dir)
        self.assertEqual(sb.prove('yogi-v1', h.profiles, h.env, SHA, h.proof_dir, h.fixture.lock, now=NOW),
                         ('refused', ['proof-subject']))
        self.assertFalse(h.proof_dir.exists())


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
