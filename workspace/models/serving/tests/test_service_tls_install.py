"""The rendered certbot deploy hook, run exactly as rendered (bin/tls-deploy through bash) against real nginx and a
synthetic retained lineage. Only /run/rose-edge, /usr/sbin/nginx and (unless root) the listen ports are pointed at
disposable equivalents. `nginx -t` binds the listeners and tolerates only EADDRINUSE, so the ACME port is held while the
hook runs, as the ACME-only Nginx holds :80 during issuance.

The 275d6e2 hook passed `--nginx /usr/sbin/nginx -p ... -c ...`, which argparse rejected (exit 2) before any
certificate access; certbot still exited 0, so the stage reported certificate_not_installed."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'service'))
sys.path.insert(0, str(HERE.parent / 'https' / 'tests'))
import render_service  # noqa: E402
import test_edge  # noqa: E402
import test_tls  # noqa: E402
import tls_deploy  # noqa: E402

NGINX = test_tls.NGINX


def setUpModule():
    test_tls.setUpModule()


def tearDownModule():
    test_tls.tearDownModule()


class CliTest(unittest.TestCase):
    def test_the_old_rendered_form_is_what_argparse_rejected(self):
        old = ['--tls-dir', '/x/tls', '--nginx', '/usr/sbin/nginx', '-p', '/run/rose-edge', '-c', '/x/nginx.conf']
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
            tls_deploy.main(old)
        self.assertEqual(e.exception.code, 2)

    def test_the_rendered_hook_parses_into_the_edge_nginx_command(self):
        line = render_service.files(render_service.capacity.load(render_service.capacity.CONFIG))['bin/tls-deploy']
        argv = line.split('\n', 1)[1].replace('\\\n', ' ').split()
        self.assertEqual(argv[:2], ['exec', '/usr/bin/python3'])
        seen = {}
        original = tls_deploy.deploy
        tls_deploy.deploy = lambda lineage, tls_dir, hostname, nginx, edge_running: seen.update(
            nginx=nginx, tls_dir=str(tls_dir), hostname=hostname) or {'result': 'deployed'}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tls_deploy.main(argv[3:] + ['--lineage', '/etc/letsencrypt/live/models.a-list.com']), 0)
        finally:
            tls_deploy.deploy = original
        self.assertEqual(seen, {'nginx': ['/usr/sbin/nginx', '-p', '/run/rose-edge', '-c', '/etc/rose-models/edge/nginx.conf'],
                                'tls_dir': '/etc/rose-models/tls', 'hostname': 'models.a-list.com'})


@unittest.skipUnless(NGINX and test_tls.OPENSSL, 'needs a real nginx (ROSE_NGINX) and openssl')
class RenderedHookTest(unittest.TestCase):
    PRODUCTION_PORTS = False

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-tls-install-'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.root, self.run_edge = self.dir / 'etc-rose-models', self.dir / 'run-edge'
        render_service.render(self.root, root=str(self.root))
        conf = [(render_service.RUN_EDGE, self.run_edge)]
        self.acme = ('0.0.0.0', 80)
        if not self.PRODUCTION_PORTS:
            self.acme = ('127.0.0.1', test_edge.free_port())
            conf += [('0.0.0.0:443', f'127.0.0.1:{test_edge.free_port()}'), ('127.0.0.1:8080', f'127.0.0.1:{test_edge.free_port()}'),
                     ('0.0.0.0:80;', '%s:%d;' % self.acme)]
        for rel, swaps in (('bin/tls-deploy', ((render_service.RUN_EDGE, self.run_edge), (render_service.NGINX, NGINX))),
                           ('edge/nginx.conf', conf)):
            path = self.root / rel
            text = path.read_text()
            for old, new in swaps:
                self.assertIn(old, text)
                text = text.replace(old, str(new))
            path.write_text(text)
        self.run_edge.mkdir()  # what the TLS stage creates; the edge (and its temp dirs) is not running yet
        self.lineage = self.dir / 'letsencrypt/live/models.a-list.com'  # the retained lineage: exact files, no CA
        self.lineage.mkdir(parents=True)
        shutil.copy(test_tls.PKI / 'leaf-fullchain.pem', self.lineage / 'fullchain.pem')
        shutil.copy(test_tls.PKI / 'leaf.key', self.lineage / 'privkey.pem')
        os.chmod(self.lineage / 'privkey.pem', 0o600)

    def hook(self):
        with socket.socket() as held:  # the ACME-only Nginx's :80 listener during issuance
            held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            held.bind(self.acme)
            held.listen()
            return self._hook()

    def _hook(self):
        return subprocess.run(['bash', str(self.root / 'bin/tls-deploy')], capture_output=True, text=True, timeout=120,
                              env={**os.environ, 'RENEWED_LINEAGE': str(self.lineage)})

    def test_rendered_hook_installs_the_retained_lineage_unchanged(self):
        r = self.hook()
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((out['result'], out['reloaded'], out['previous']), ('deployed', False, None))
        current = self.root / 'tls/current'
        self.assertTrue(current.is_symlink())
        for name in ('fullchain.pem', 'privkey.pem'):
            self.assertEqual((current / name).read_bytes(), (self.lineage / name).read_bytes())  # not reissued
        self.assertEqual(oct((current / 'privkey.pem').stat().st_mode & 0o777), '0o600')
        self.assertNotIn('PRIVATE KEY', r.stdout + r.stderr)

    def test_rendered_hook_refuses_a_mismatched_key_and_installs_nothing(self):
        shutil.copy(test_tls.PKI / 'wrong.key', self.lineage / 'privkey.pem')
        os.chmod(self.lineage / 'privkey.pem', 0o600)
        r = self.hook()
        self.assertEqual(r.returncode, 1, r.stderr[-2000:])
        self.assertEqual(json.loads(r.stdout)['result'], 'refused')
        self.assertFalse((self.root / 'tls/current').exists())


@unittest.skipUnless(NGINX and test_tls.OPENSSL and os.geteuid() == 0, 'needs root for :80/:443, as on the VM')
class RenderedHookProductionPortsTest(RenderedHookTest):
    PRODUCTION_PORTS = True


if __name__ == '__main__':
    unittest.main()
