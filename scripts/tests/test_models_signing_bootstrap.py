"""OCT30-INFRA-S012: the models-v0 PKCS12 bootstrap in scripts/signing-bootstrap.py.

Mocks and synthetic, non-secret fixtures only: the "certificate" is hand-built DER with a random fake 4096-bit
modulus and a fake signature, the "keystore" is placeholder bytes, and keytool is a mock runner. No key is generated,
no secret is read or written, nothing touches a host, IAM or the network."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('signing_bootstrap_models', SCRIPTS/'signing-bootstrap.py')
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)
PROFILES = sb.load_profiles()
NOW = datetime(2026, 10, 30, 2, 0, tzinfo=timezone.utc)
SHA = 'a' * 40
FRANKEL = {'otaCertificateSha256': 'f' * 64, 'avbPublicKeySha256': 'e' * 64, 'apps': [{'signerSha256': 'd' * 64}]}


# ---------------------------------------------------------------- synthetic DER

def tlv(tag, content):
    n = len(content)
    if n < 0x80:
        length = bytes([n])
    else:
        raw = n.to_bytes((n.bit_length() + 7) // 8, 'big')
        length = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + length + content


def oid(dotted):
    parts = [int(p) for p in dotted.split('.')]
    out = bytes([parts[0] * 40 + parts[1]])
    for value in parts[2:]:
        chunk = [value & 0x7f]
        value >>= 7
        while value:
            chunk.append(0x80 | (value & 0x7f))
            value >>= 7
        out += bytes(reversed(chunk))
    return tlv(0x06, out)


def name(cn):
    return tlv(0x30, tlv(0x31, tlv(0x30, oid('2.5.4.3') + tlv(0x0c, cn.encode()))))


def synthetic_cert(cn='Rose Models', issuer=None, bits=4096, days=10000, sig='1.2.840.113549.1.1.11'):
    """A structurally valid X.509 certificate with a FAKE random modulus and FAKE signature. Not a key."""
    modulus = bytearray(os.urandom(bits // 8))
    modulus[0] |= 0x80
    spki = tlv(0x30, tlv(0x30, oid('1.2.840.113549.1.1.1') + tlv(0x05, b'')) +
        tlv(0x03, b'\0' + tlv(0x30, tlv(0x02, b'\0' + bytes(modulus)) + tlv(0x02, b'\x01\x00\x01'))))
    start = datetime(2026, 10, 30, 2, 0, 0)
    validity = tlv(0x30, tlv(0x17, start.strftime('%y%m%d%H%M%SZ').encode()) +
        tlv(0x18, (start + timedelta(days=days)).strftime('%Y%m%d%H%M%SZ').encode()))
    alg = tlv(0x30, oid(sig) + tlv(0x05, b''))
    tbs = tlv(0x30, tlv(0xa0, tlv(0x02, b'\x02')) + tlv(0x02, b'\x01') + alg + name(issuer or cn) + validity + name(cn) + spki)
    der = tlv(0x30, tbs + alg + tlv(0x03, b'\0' + b'\x55' * 512))
    pem = b'-----BEGIN CERTIFICATE-----\n' + base64.encodebytes(der) + b'-----END CERTIFICATE-----\n'
    return der, pem


def listing(der, aliases=('rose-models',), entry='PrivateKeyEntry', store='PKCS12', fingerprint=None):
    fp = fingerprint or hashlib.sha256(der).hexdigest()
    colon = ':'.join(fp[i:i + 2].upper() for i in range(0, 64, 2))
    lines = [f'Keystore type: {store}', 'Keystore provider: SUN', '', f'Your keystore contains {len(aliases)} entr{"y" if len(aliases) == 1 else "ies"}', '']
    for alias in aliases:
        lines += [f'Alias name: {alias}', 'Creation date: Oct 30, 2026', f'Entry type: {entry}', 'Certificate chain length: 1',
            'Certificate[1]:', 'Owner: CN=Rose Models', 'Issuer: CN=Rose Models', 'Certificate fingerprints:', f'\t SHA256: {colon}', '']
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- mocks

def ready_profiles():
    profiles = copy.deepcopy(PROFILES)
    models = profiles['profiles']['models-v0']
    models['access'].update(addVersionExpires='2027-01-01T00:00:00Z', accessorExpires='2027-01-01T00:00:00Z')
    return profiles


READY = ready_profiles()
MODELS = READY['profiles']['models-v0']
TRUST = MODELS['tools']['keytool']['trust']
KEYTOOL = f'{TRUST["root"]}/linux-x86/bin/keytool'


class KeytoolRunner:
    """Mock keytool: writes a placeholder store, the synthetic certificate and its listing; records argv."""

    def __init__(self, cert=None, list_text=None, fail_step=None):
        self.der, self.pem = cert or synthetic_cert()
        self.list_text = list_text if list_text is not None else listing(self.der)
        self.fail_step, self.calls = fail_step, []

    def __call__(self, tool, argv, cwd, stdin_path, stdout_path=None):
        index = len(self.calls)
        self.calls.append((tool, list(argv), stdin_path, stdout_path))
        if index == self.fail_step:
            return 1
        out = Path(cwd)
        if argv[0] == '-genkeypair':
            (out/'keystore.p12').write_bytes(b'\x30\x82\x10\x00' + b'SYNTHETIC-PKCS12-PLACEHOLDER')
        elif argv[0] == '-exportcert':
            Path(argv[argv.index('-file') + 1]).write_bytes(self.pem)
        elif argv[0] == '-list':
            Path(stdout_path).write_text(self.list_text)
        return 0


class Executor:
    def __init__(self, runner=None, states=None, adds=None, recheck=None, trees=None):
        self.runner, self.calls = runner or KeytoolRunner(), []
        self.states, self.adds, self.recheck = states or {}, list(adds or ['1', '1']), recheck
        self.trees = list(trees or [])

    def tool_tree(self, trust):
        self.calls.append(('tree',))
        if self.trees:
            return self.trees.pop(0)
        return {'treeId': trust['treeId'], 'toolBlob': trust['toolBlob']}

    def tool_sha256(self, path):
        self.calls.append(('tool', path))
        return hashlib.sha256(b'keytool').hexdigest()

    def secret_state(self, project, secret):
        self.calls.append(('metadata', secret))
        if self.recheck and 'generate' in [c[0] for c in self.calls]:
            return self.recheck.get(secret, {'exists': True, 'versions': []})
        return self.states.get(secret, {'exists': True, 'versions': []})

    def generate(self, workspace, profile):
        self.calls.append(('generate',))
        return sb.generate_keys(workspace, profile, self.runner, token='synthetic-models-token')

    def add_version(self, project, secret, file):
        self.calls.append(('add', secret, Path(file).name))
        outcome = self.adds.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kinds(self):
        return [c[0] for c in self.calls]


class Fixture:
    def __init__(self, test):
        self.tmp = Path(tempfile.mkdtemp(prefix='models-bootstrap-'))
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp/'state'
        self.state.mkdir()
        self.created = []

    def workspace(self):
        import contextlib

        @contextlib.contextmanager
        def ws():
            path = Path(tempfile.mkdtemp(prefix='ws-', dir=self.tmp))
            os.chmod(path, 0o700)
            self.created.append(path)
            try:
                yield path
            finally:
                shutil.rmtree(path)
        return ws()

    def run(self, executor, profiles=READY, frankel=FRANKEL):
        return sb.run('models-v0', profiles, executor, self.state, self.tmp/'lock', SHA, NOW, workspace=self.workspace,
            frankel=frankel)

    def journal(self):
        path = self.state/'models-v0.json'
        return json.loads(path.read_text()) if path.exists() else None


def codes(receipt):
    return [r['code'] for r in receipt['reasons']]


# ---------------------------------------------------------------- tests

class Contract(unittest.TestCase):
    def test_fixed_alias_algorithm_subject_and_destinations(self):
        recipe = MODELS['generation']['recipe']
        gen = recipe[0]['args']
        for flag, value in (('-alias', 'rose-models'), ('-keyalg', 'RSA'), ('-keysize', '4096'), ('-sigalg', 'SHA256withRSA'),
                            ('-validity', '10000'), ('-storetype', 'PKCS12'), ('-dname', '{dname}')):
            self.assertEqual(gen[gen.index(flag) + 1], value, flag)
        self.assertEqual(MODELS['generation']['dname'], 'CN=Rose Models')
        self.assertEqual(MODELS['generation']['identity'], {'alias': 'rose-models', 'keyAlgorithm': 'RSA', 'keySize': 4096,
            'signatureAlgorithm': 'SHA256withRSA', 'subject': 'CN=Rose Models', 'validityDays': 10000})
        # Passwords only through the passphrase file; never a literal or the environment.
        for step in recipe:
            args = step['args']
            for i, arg in enumerate(args):
                if arg.startswith('-storepass') or arg.startswith('-keypass'):
                    self.assertTrue(arg.endswith(':file'), arg)
                    self.assertEqual(args[i + 1], '{passphrase_file}')
        self.assertEqual(sb.recipe_problems(MODELS), [])
        self.assertEqual(MODELS['secrets'], {'keys': 'PIXEL_MODELS_SIGNING_KEYSTORE_V1', 'passphrase': 'PIXEL_MODELS_SIGNING_PASSPHRASE_V1'})
        self.assertTrue(MODELS['recheckEmptyBeforeImport'])
        self.assertNotIn('recheckEmptyBeforeImport', PROFILES['profiles']['fold-rango-v1'])  # Fold behavior unchanged

    def test_vocabulary_guards(self):
        def problems(change):
            profile = copy.deepcopy(MODELS)
            change(profile['generation'])
            return sb.recipe_problems(profile)
        for label, change in {
            'stdout into the store': lambda g: g['recipe'][2].update(stdout='keystore.p12'),
            'stdout not produced': lambda g: g['recipe'][2].update(produces=['rose-models.x509.pem']),
            'unsafe dname': lambda g: g.update(dname='CN=x;rm'),
            'literal password': lambda g: g['recipe'][0]['args'].__setitem__(g['recipe'][0]['args'].index('{passphrase_file}'), '/tmp/pw'),
            'public output unproduced': lambda g: g.update(publicOutputs=['rose-models.x509.pem', 'keytool-list.txt', 'extra.txt']),
        }.items():
            with self.subTest(label):
                self.assertTrue(problems(change), label)


class Generation(unittest.TestCase):
    def ws(self):
        path = Path(tempfile.mkdtemp(prefix='gen-'))
        self.addCleanup(shutil.rmtree, path, True)
        os.chmod(path, 0o700)
        return path

    def test_store_is_verified_and_projected(self):
        ws, runner = self.ws(), KeytoolRunner()
        identity = sb.generate_keys(ws, MODELS, runner, token='synthetic-models-token')
        self.assertEqual(sorted(os.listdir(ws)), ['keystore.p12', 'passphrase'])
        self.assertEqual(identity['certificateSha256'], hashlib.sha256(runner.der).hexdigest())
        self.assertEqual((identity['alias'], identity['keySize'], identity['validityDays'], identity['subject']),
            ('rose-models', 4096, 10000, 'CN=Rose Models'))
        self.assertTrue(all('synthetic-models-token' not in ' '.join(c[1]) for c in runner.calls))
        self.assertEqual([c[3] is not None for c in runner.calls], [False, False, True])  # only the listing is captured
        self.assertEqual({c[0] for c in runner.calls}, {KEYTOOL})

    def test_identity_rejections(self):
        der, pem = synthetic_cert()
        for label, cert, text in (
            ('two aliases', (der, pem), listing(der, aliases=('rose-models', 'other'))),
            ('secret entry', (der, pem), listing(der, entry='SecretKeyEntry')),
            ('jks store', (der, pem), listing(der, store='JKS')),
            ('fingerprint mismatch', (der, pem), listing(der, fingerprint='0' * 64)),
            ('2048-bit key', synthetic_cert(bits=2048), None),
            ('sha1 signature', synthetic_cert(sig='1.2.840.113549.1.1.5'), None),
            ('wrong subject', synthetic_cert(cn='Rose Agent'), None),
            ('short validity', synthetic_cert(days=3650), None),
            ('not self-issued', synthetic_cert(issuer='Someone Else'), None),
        ):
            with self.subTest(label):
                runner = KeytoolRunner(cert=cert, list_text=text)
                with self.assertRaises(sb.Blocked) as blocked:
                    sb.generate_keys(self.ws(), MODELS, runner, token='t')
                self.assertEqual(blocked.exception.code, 'pkcs12-identity', label)
        runner = KeytoolRunner(cert=(der, b'not a pem'))
        runner.list_text = listing(der)
        with self.assertRaises(sb.Blocked) as blocked:
            sb.generate_keys(self.ws(), MODELS, runner, token='t')
        self.assertEqual(blocked.exception.code, 'pkcs12-invalid')

    def test_keytool_failure_and_unmeasured_tool(self):
        with self.assertRaises(sb.Blocked) as blocked:
            sb.generate_keys(self.ws(), MODELS, KeytoolRunner(fail_step=1), token='t')
        self.assertEqual(blocked.exception.code, 'generator-failed')
        broken = copy.deepcopy(MODELS)
        broken['tools']['keytool']['trust']['treeId'] = 'not-a-tree'
        runner = KeytoolRunner()
        with self.assertRaises(sb.Blocked) as blocked:
            sb.generate_keys(self.ws(), broken, runner)
        self.assertEqual((blocked.exception.code, runner.calls), ('tool-unapproved', []))


class Bootstrap(unittest.TestCase):
    def test_altered_local_jdk_refuses_before_metadata_or_keytool(self):
        for observed in ({'treeId': '0' * 40, 'toolBlob': TRUST['toolBlob']}, {'treeId': TRUST['treeId'], 'toolBlob': '1' * 40}, None):
            with self.subTest(observed=observed):
                f, ex = Fixture(self), Executor(trees=[observed])
                receipt, _ = f.run(ex)
                self.assertEqual((receipt['status'], codes(receipt)), ('blocked', ['tool-identity']))
                self.assertEqual(ex.kinds(), ['tree'])
                self.assertEqual(ex.runner.calls, [])
                self.assertIsNone(f.journal())

    def test_jdk_changed_between_checks_refuses_before_generation(self):
        f, ex = Fixture(self), Executor(trees=[None, {'treeId': '2' * 40, 'toolBlob': TRUST['toolBlob']}])
        ex.trees[0] = {'treeId': TRUST['treeId'], 'toolBlob': TRUST['toolBlob']}
        receipt, _ = f.run(ex)
        self.assertEqual((receipt['status'], codes(receipt)), ('blocked', ['tool-identity']))
        self.assertNotIn('generate', ex.kinds())
        self.assertEqual(ex.runner.calls, [])
        self.assertIsNone(f.journal())

    def test_checked_in_profile_binds_the_pinned_tree(self):
        tool = PROFILES['profiles']['models-v0']['tools']['keytool']
        self.assertEqual(tool['kind'], 'pinned-tree')
        self.assertEqual((tool['trust']['revision'], tool['trust']['treeId'], tool['trust']['toolBlob']),
            ('ef5bcc92586b839ae3dbacc154127092fa4002ec', '860b81e5dd00ffe239268d5a043ac97de4cb4b44',
             'fe420a3f54ebd6dfaeef6d10a4bea7e2f72c28de'))
        self.assertEqual(sb.manifest_revision('prebuilts/jdk/jdk21'), tool['trust']['revision'])
        self.assertEqual(tool['installed'], {'path': None, 'sha256': None})  # no host hash is exported or trusted
        self.assertEqual(set(PROFILES['profiles']['models-v0']['tools']), {'keytool'})  # no APK-signing tool gates the key
        for change, word in ((('revision', 'f' * 40), 'pinned-manifest revision'), (('treeId', 'x'), 'treeId'),
                             (('tool', '../bin/keytool'), 'trust.tool'), (('root', 'relative/jdk'), 'trust.root'),
                             (('basis', 'installed-hash'), 'pinned-git-tree')):
            trust = dict(tool['trust'], **{change[0]: change[1]})
            self.assertTrue(any(word in p for p in sb.pinned_tree_problems(trust)), change)

    def test_complete_import_receipt_is_public_only(self):
        f, ex = Fixture(self), Executor()
        receipt, private = f.run(ex)
        self.assertEqual(receipt['status'], 'complete', receipt['reasons'])
        self.assertEqual(receipt['tools']['keytool']['trustBasis'], 'pinned-git-tree')
        self.assertEqual(ex.kinds().count('tree'), 2)  # before metadata and again immediately before generation
        self.assertEqual([c for c in ex.calls if c[0] == 'add'], [('add', 'PIXEL_MODELS_SIGNING_KEYSTORE_V1', 'keystore.p12'),
            ('add', 'PIXEL_MODELS_SIGNING_PASSPHRASE_V1', 'passphrase')])
        self.assertEqual(ex.kinds().count('metadata'), 4)  # preflight plus the pre-import empty recheck
        out = sb.public_receipt(receipt, private)
        text = json.dumps(out)
        for forbidden in ('synthetic-models-token', 'SYNTHETIC-PKCS12-PLACEHOLDER', 'BEGIN CERTIFICATE'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(set(out['publicIdentity']), {'certificateSha256', 'alias', 'keyAlgorithm', 'keySize',
            'signatureAlgorithm', 'subject', 'validityDays', 'notBefore'})
        self.assertEqual(f.journal()['status'], 'complete')
        self.assertFalse(any(p.exists() for p in f.created))

    def test_occupied_or_unavailable_before_generation(self):
        for states, code in (({'PIXEL_MODELS_SIGNING_PASSPHRASE_V1': {'exists': True, 'versions': ['1']}}, 'secret-occupied'),
                             ({'PIXEL_MODELS_SIGNING_KEYSTORE_V1': sb.Unavailable('denied')}, 'metadata-unavailable')):
            f, ex = Fixture(self), Executor(states={k: v for k, v in states.items() if not isinstance(v, Exception)})
            if isinstance(list(states.values())[0], Exception):
                ex.secret_state = lambda project, secret: (_ for _ in ()).throw(sb.Unavailable('denied'))
            receipt, _ = f.run(ex)
            self.assertIn(code, codes(receipt))
            self.assertNotIn('generate', ex.kinds())

    def test_empty_secret_race_recheck_refuses_import(self):
        f = Fixture(self)
        ex = Executor(recheck={'PIXEL_MODELS_SIGNING_KEYSTORE_V1': {'exists': True, 'versions': ['1']}})
        receipt, _ = f.run(ex)
        self.assertEqual((receipt['status'], codes(receipt)), ('failed', ['secret-occupied']))
        self.assertNotIn('add', ex.kinds())
        self.assertEqual(f.journal()['status'], 'failed-before-commit')
        again = Executor()
        self.assertEqual((codes(f.run(again)[0]), again.calls), (['previous-attempt'], []))  # never regenerated

    def test_first_and_second_import_outcomes(self):
        for adds, status, journal in (([sb.AddFailed('denied')], 'failed', 'failed-before-commit'),
                                      ([sb.Uncertain('timeout')], 'recovery-needed', 'recovery-needed'),
                                      (['1', sb.AddFailed('denied')], 'recovery-needed', 'recovery-needed'),
                                      (['1', sb.Uncertain('lost')], 'recovery-needed', 'recovery-needed')):
            with self.subTest(adds=adds):
                f, ex = Fixture(self), Executor(adds=adds)
                receipt, _ = f.run(ex)
                self.assertEqual(receipt['status'], status)
                self.assertEqual(f.journal()['status'], journal)
                self.assertEqual(ex.kinds().count('add'), len(adds))  # no blind retry
                if adds[0] == '1':
                    self.assertEqual(receipt['secrets']['keys']['committedVersion'], '1')

    def test_cancellation_records_recovery_and_cleans_up(self):
        f, ex = Fixture(self), Executor(adds=['1', KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            f.run(ex)
        self.assertEqual(f.journal()['status'], 'recovery-needed')
        self.assertFalse(any(p.exists() for p in f.created))

    def test_existing_identity_collision(self):
        runner = KeytoolRunner()
        f, ex = Fixture(self), Executor(runner=runner)
        frankel = dict(FRANKEL, apps=[{'signerSha256': hashlib.sha256(runner.der).hexdigest()}])
        receipt, _ = f.run(ex, frankel=frankel)
        self.assertEqual((receipt['status'], codes(receipt)), ('failed', ['identity-collision']))
        self.assertNotIn('add', ex.kinds())

    def test_unexpected_generator_output(self):
        class Extra(Executor):
            def generate(self, workspace, profile):
                identity = super().generate(workspace, profile)
                (Path(workspace)/'debug.log').write_text('x')
                return identity
        f = Fixture(self)
        receipt, _ = f.run(Extra())
        self.assertEqual((receipt['status'], codes(receipt)), ('failed', ['unexpected-output']))



class GitTree(unittest.TestCase):
    """git_tree_id against the git CLI on synthetic directories (no JDK bytes)."""

    def tree(self):
        root = Path(tempfile.mkdtemp(prefix='tree-'))
        self.addCleanup(shutil.rmtree, root, True)
        base = root/'linux-x86'
        (base/'bin').mkdir(parents=True)
        (base/'lib/server').mkdir(parents=True)
        (base/'legal/java.base').mkdir(parents=True)
        (base/'legal/java.compiler').mkdir(parents=True)
        (base/'bin/keytool').write_bytes(b'#!synthetic keytool launcher\n')
        os.chmod(base/'bin/keytool', 0o755)
        (base/'lib/libjli.so').write_bytes(b'synthetic libjli')
        (base/'lib/server/libjvm.so').write_bytes(b'synthetic libjvm')
        (base/'legal/java.base/LICENSE').write_text('synthetic license')
        os.symlink('../java.base/LICENSE', base/'legal/java.compiler/LICENSE')
        return root, base

    def git_cli(self, root):
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        subprocess.run(['git', '-C', str(root), 'add', '-A', 'linux-x86'], check=True)
        tree = subprocess.run(['git', '-C', str(root), 'write-tree'], check=True, capture_output=True, text=True).stdout.strip()
        return subprocess.run(['git', '-C', str(root), 'rev-parse', f'{tree}:linux-x86'], check=True, capture_output=True,
            text=True).stdout.strip()

    def test_matches_git_and_detects_changes(self):
        root, base = self.tree()
        expected = self.git_cli(root)
        self.assertEqual(sb.git_tree_id(base), expected)
        (base/'lib/libjli.so').write_bytes(b'altered libjli')
        self.assertNotEqual(sb.git_tree_id(base), expected)
        (base/'lib/libjli.so').write_bytes(b'synthetic libjli')
        self.assertEqual(sb.git_tree_id(base), expected)
        (base/'bin/java').write_bytes(b'unexpected override')
        self.assertNotEqual(sb.git_tree_id(base), expected)
        (base/'bin/java').unlink()
        os.chmod(base/'bin/keytool', 0o644)
        self.assertNotEqual(sb.git_tree_id(base), expected)
        os.chmod(base/'bin/keytool', 0o755)
        (base/'lib/server/libjvm.so').unlink()
        self.assertNotEqual(sb.git_tree_id(base), expected)

    def test_forged_index_is_never_consulted(self):
        root, base = self.tree()
        expected = self.git_cli(root)
        (base/'bin/keytool').write_bytes(b'tampered keytool')  # the git index still records the original
        self.assertNotEqual(sb.git_tree_id(base), expected)
        self.assertTrue((root/'.git/index').exists())

    def test_escapes_and_special_files_refuse(self):
        for label, change in {
            'absolute symlink': lambda b: os.symlink('/etc/passwd', b/'lib/evil'),
            'escaping symlink': lambda b: os.symlink('../../outside', b/'lib/evil'),
            'fifo': lambda b: os.mkfifo(b/'lib/fifo'),
            'odd name': lambda b: (b/'lib/bad name').write_text('x'),
        }.items():
            with self.subTest(label):
                _, base = self.tree()
                change(base)
                with self.assertRaises(sb.Blocked):
                    sb.git_tree_id(base)
        root, base = self.tree()
        os.symlink(base, root/'alias')
        with self.assertRaises(sb.Blocked):
            sb.git_tree_id(root/'alias')  # a symlinked ancestor

    def test_production_executor_uses_the_pinned_paths(self):
        root, base = self.tree()
        trust = dict(TRUST, root=str(root), treePath='linux-x86', tool='bin/keytool')
        observed = sb.GcloudExecutor().tool_tree(trust)
        self.assertEqual(observed['treeId'], self.git_cli(root))
        self.assertEqual(observed['toolBlob'], hashlib.sha1(b'blob %d\0' % len(b'#!synthetic keytool launcher\n')
            + b'#!synthetic keytool launcher\n').hexdigest())


class Environment(unittest.TestCase):
    def test_keytool_gets_a_fresh_minimal_environment(self):
        captured = {}

        def fake_run(argv, **kw):
            captured.update(kw)
            return types.SimpleNamespace(returncode=0)
        saved = sb.subprocess.run
        sb.subprocess.run = fake_run
        os.environ['JAVA_TOOL_OPTIONS'] = '-javaagent:/tmp/evil.jar'
        try:
            tmp = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, tmp, True)
            sb.subprocess_runner(KEYTOOL, ['-list'], str(tmp), None, stdout_path=str(tmp/'out.txt'))
        finally:
            sb.subprocess.run = saved
            os.environ.pop('JAVA_TOOL_OPTIONS', None)
        self.assertEqual(set(captured['env']), {'PATH', 'LC_ALL', 'HOME'})
        for name in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'CLASSPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH'):
            self.assertNotIn(name, captured['env'])


class Wiring(unittest.TestCase):
    def test_actual_ci_runs_this_suite(self):
        root = SCRIPTS.parent
        ci = (root/'.github/workflows/release-verification.yml').read_text()
        self.assertIn('          python3 scripts/tests/test_models_signing_bootstrap.py\n', ci)
        self.assertIn('python3 scripts/tests/test_models_signing_bootstrap.py\n', (root/'scripts/test-local.sh').read_text())
        self.assertEqual(ci.count("      - 'scripts/tests/test_models_signing_bootstrap.py'\n"), 2)

if __name__ == '__main__':
    unittest.main()
