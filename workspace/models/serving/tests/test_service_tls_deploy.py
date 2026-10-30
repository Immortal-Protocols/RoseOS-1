"""Certificate renewal deploy hook with rollback (MODELS-ALWAYSON-SERVICE-001). Disposable synthetic chains from the
TLS edge tests; Nginx is a recorded stand-in here (the real-nginx TLS behavior is covered in https/tests)."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'service'))
sys.path.insert(0, str(HERE.parent / 'https' / 'tests'))
import tls_deploy as T  # noqa: E402
import test_tls  # noqa: E402


@unittest.skipUnless(test_tls.OPENSSL, 'needs openssl')
class Deploy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pki = Path(tempfile.mkdtemp(prefix='deploy-pki-'))
        test_tls.make_pki(cls.pki)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.pki, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='deploy-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tls = self.tmp / 'tls'
        self.calls, self.fail = [], set()

    def lineage(self, name):
        d = self.tmp / f'live-{name}-{len(list(self.tmp.iterdir()))}'
        d.mkdir()
        shutil.copy(self.pki / f'{name}-fullchain.pem', d / 'fullchain.pem')
        shutil.copy(self.pki / f'{name}.key', d / 'privkey.pem')
        return d

    def nginx(self, argv, capture_output, timeout):
        self.calls.append(argv[1:])
        return types.SimpleNamespace(returncode=1 if tuple(argv[1:]) in self.fail else 0)

    def deploy(self, name, now):
        return T.deploy(self.lineage(name), self.tls, test_tls.HOST, ['nginx'], run=self.nginx, now=now)

    def test_deploy_then_failed_renewals_keep_the_previous_chain(self):
        first = self.deploy('leaf', 1_000)
        self.assertEqual(first['result'], 'deployed')
        self.assertEqual(os.readlink(self.tls / 'current'), first['current'])
        self.assertEqual(oct((self.tls / first['current'] / 'privkey.pem').stat().st_mode & 0o777), '0o600')
        self.assertEqual(self.calls, [['-t'], ['-s', 'reload']])
        for name, stage in (('wrong', 'check'), ('self', 'check')):
            with self.subTest(name):
                out = self.deploy(name, 2_000)
                self.assertEqual((out['result'], out['stage']), ('refused', stage))
                self.assertEqual(os.readlink(self.tls / 'current'), first['current'])
        self.fail = {('-t',)}
        out = self.deploy('leaf', 3_000)
        self.assertEqual((out['result'], out['stage'], out['current']), ('rolled_back', 'nginx_test', first['current']))
        self.assertEqual(os.readlink(self.tls / 'current'), first['current'])
        self.assertEqual(self.calls[-1], ['-s', 'reload'])  # Nginx is reloaded on the previous chain
        self.fail = {('-s', 'reload')}
        out = self.deploy('leaf', 4_000)
        self.assertEqual((out['result'], out['stage']), ('rolled_back', 'reload'))
        self.assertEqual(sorted(p.name for p in self.tls.glob('gen-*')), [first['current']])
        self.fail = set()
        second = self.deploy('leaf', 5_000)
        third = self.deploy('leaf', 6_000)
        self.assertEqual((third['previous'], sorted(p.name for p in self.tls.glob('gen-*'))),
                         (second['current'], sorted([second['current'], third['current']])))  # older ones pruned
        self.assertNotIn('PRIVATE', json.dumps(third))

    def test_first_issuance_before_the_edge_runs_tests_but_does_not_reload(self):
        out = T.deploy(self.lineage('leaf'), self.tls, test_tls.HOST, ['nginx'], run=self.nginx, now=1_000,
                       edge_running=False)
        self.assertEqual((out['result'], out['reloaded'], self.calls), ('deployed', False, [['-t']]))
        self.assertFalse(T.running(self.tmp / 'no.pid'))
        (self.tmp / 'me.pid').write_text(str(os.getpid()))
        self.assertTrue(T.running(self.tmp / 'me.pid'))

    def test_expired_chain_is_refused(self):
        out = self.deploy('leaf', 4_102_444_800)  # year 2100
        self.assertEqual((out['result'], out['stage'], out['reason']), ('refused', 'check', 'leaf has expired'))
        self.assertFalse((self.tls / 'current').exists())


if __name__ == '__main__':
    unittest.main()
