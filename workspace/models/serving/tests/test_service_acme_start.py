"""ACME-START-FIX-001: the production ACME-only start path (bootstrap.Host, prepare_acme_http, stop_acme_http) with a
real self-daemonizing nginx, the rendered acme-only.conf, a disposable loopback port and run directory, and shortened
deadlines. Nothing here reaches ACME, DNS, GCP or a live endpoint.

The earlier real-nginx test used `daemon off;` and other stdio, and the fake host simulated the start, so neither saw
a daemon keep the captured stderr pipe open."""
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'service'))
sys.path.insert(0, str(HERE.parent / 'https' / 'tests'))
import bootstrap as B  # noqa: E402
import test_edge  # noqa: E402

render_proxy, NGINX = test_edge.render_proxy, test_edge.NGINX


def alive(pid):
    try:
        state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
    except OSError:
        return False
    return state != 'Z'  # an exited daemon is a zombie until its new parent reaps it


class HostProcessTest(unittest.TestCase):
    """Host.run and Host.start_daemon return bounded categories, never raw stderr."""

    def test_run_bounds_time_and_bytes_while_reading(self):
        host = B.Host()
        t = time.monotonic()
        self.assertEqual(host.run(['sleep', '5'], 1), (124, ''))
        self.assertLess(time.monotonic() - t, 3)
        code, out = host.run([sys.executable, '-c', 'import sys; sys.stdout.write("x" * (3 << 20)); sys.exit(3)'], 30)
        self.assertEqual((code, len(out)), (3, B.CAPTURE_BYTES))  # drained, kept only up to the cap
        self.assertEqual(host.run([sys.executable, '-c', 'import sys; sys.stderr.write("secret"); print("ok")'], 30),
                         (0, 'ok\n'))
        self.assertEqual(host.run(['/nonexistent/binary'], 5), (127, ''))

    def test_run_returns_when_the_command_exits_even_if_a_child_holds_stdout(self):
        t = time.monotonic()
        code, _ = B.Host().run(['sh', '-c', 'sleep 30 & echo started'], 20)
        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - t, 5)
        subprocess.run(['pkill', '-f', '^sleep 30$'], check=False)

    def test_start_daemon_reasons(self):
        host = B.Host()
        self.assertIsNone(host.start_daemon(['true'], 5))
        self.assertEqual(host.start_daemon(['sh', '-c', 'exit 3'], 5), 'exit')
        self.assertEqual(host.start_daemon(['sh', '-c', 'kill -9 $$'], 5), 'signal')
        self.assertEqual(host.start_daemon(['sleep', '5'], 1), 'deadline')
        self.assertEqual(host.start_daemon(['/nonexistent/binary'], 5), 'not_started')


@unittest.skipUnless(NGINX, 'needs a real nginx (ROSE_NGINX)')
class AcmeStartTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='rose-acme-start-'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        os.chmod(self.dir, 0o755)
        self.run_dir, self.webroot, self.conf = self.dir / 'run', self.dir / 'acme', self.dir / 'acme-only.conf'
        self.port = test_edge.free_port()
        self.conf.write_text(render_proxy.acme_only_conf(str(self.run_dir), '127.0.0.1', self.port, str(self.webroot)))
        patch = {'ACME_RUN': str(self.run_dir), 'ACME_WEBROOT': str(self.webroot), 'ACME_PORT': self.port,
                 'ACME_START_S': 5, 'ACME_NGINX': [NGINX, '-p', str(self.run_dir), '-c', str(self.conf)]}
        for name, value in patch.items():
            self.addCleanup(setattr, B, name, getattr(B, name))
            setattr(B, name, value)
        self.host = B.Host()
        self.addCleanup(self.kill_leftovers)

    def kill_leftovers(self):
        pid = B.pid_of(self.host, str(self.run_dir))
        if pid is not None and B.owned(self.host, pid)['exe']:
            os.kill(pid, signal.SIGKILL)

    def pid(self):
        return int((self.run_dir / 'nginx.pid').read_text())

    def prepare(self):
        try:
            return B.prepare_acme_http(self.host), None
        except B.Failed as e:
            return None, e

    def test_original_failure_capture_output_on_the_daemon_hangs_and_leaks_it(self):
        self.webroot.mkdir()
        for sub in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
            (self.run_dir / sub).mkdir(parents=True)
        t = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):  # the 275d6e2 Host.run, deadline shortened from 60 s
            subprocess.run(B.ACME_NGINX, capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL)
        self.assertGreaterEqual(time.monotonic() - t, 3)
        leaked = self.pid()
        self.assertTrue(alive(leaked))  # serving, but the old start path had already reported a failure
        B.stop_acme_http(self.host)
        self.assertFalse(alive(leaked))

    def test_start_proves_pid_config_listener_and_routes_then_cleanup_leaves_nothing(self):
        t = time.monotonic()
        ready, err = self.prepare()
        self.assertIsNone(err, err and (str(err), err.observed))
        self.assertLess(time.monotonic() - t, 5)
        self.assertEqual(ready['pid'], {'exe': True, 'config': True, 'listener': True})
        self.assertEqual(ready['acmeHttp'], {'challenge': 200, **{p: 404 for p in B.ACME_404}})
        self.assertEqual(list((self.webroot / '.well-known/acme-challenge').iterdir()), [])  # probe removed
        pid = self.pid()
        B.stop_acme_http(self.host)
        self.assertFalse(alive(pid))
        self.assertFalse((self.run_dir / 'nginx.pid').exists())
        self.assertEqual(B.listener_inodes(self.host, self.port), set())

    def test_a_leftover_task_nginx_is_replaced_so_the_loaded_config_is_current(self):
        self.assertIsNone(self.prepare()[1])
        first = self.pid()
        self.assertIsNone(self.prepare()[1])
        self.assertFalse(alive(first))
        self.assertNotEqual(self.pid(), first)
        B.stop_acme_http(self.host)

    def test_occupied_port_is_refused_and_nothing_starts(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', self.port))
            s.listen()
            _, err = self.prepare()
        self.assertEqual((str(err), err.observed), ('acme_http_port_occupied', [{'port': self.port}]))
        self.assertFalse((self.run_dir / 'nginx.pid').exists())

    def test_invalid_config_is_refused_before_start(self):
        self.conf.write_text(self.conf.read_text().replace('events {', 'events { bogus_directive on;', 1))
        _, err = self.prepare()
        self.assertEqual(str(err), 'acme_http_config_invalid')
        self.assertFalse((self.run_dir / 'nginx.pid').exists())

    def test_stale_pid_file_is_replaced(self):
        gone = subprocess.Popen(['true'])
        gone.wait()
        self.run_dir.mkdir()
        (self.run_dir / 'nginx.pid').write_text(f'{gone.pid}\n')
        ready, err = self.prepare()
        self.assertIsNone(err, err and (str(err), err.observed))
        self.assertNotEqual(self.pid(), gone.pid)
        B.stop_acme_http(self.host)

    def test_foreign_live_pid_is_refused_and_never_signalled(self):
        foreign = subprocess.Popen(['sleep', '30'])
        self.addCleanup(foreign.wait)
        self.addCleanup(foreign.kill)
        self.run_dir.mkdir()
        (self.run_dir / 'nginx.pid').write_text(f'{foreign.pid}\n')
        _, err = self.prepare()
        self.assertEqual(str(err), 'acme_http_pid_foreign')
        B.stop_acme_http(self.host)  # the finally path of the TLS stage
        self.assertIsNone(foreign.poll())
        self.assertEqual(self.pid(), foreign.pid)


if __name__ == '__main__':
    unittest.main()
