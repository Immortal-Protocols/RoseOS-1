"""Health/recovery decisions, loaded-model probe and verified model storage (MODELS-ALWAYSON-SERVICE-001).
Synthetic: a fake loopback vLLM and a fake Hugging Face cache. No GPU, model or network."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
import health as H  # noqa: E402
import verify_model as V  # noqa: E402


def fake_vllm(behavior):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            ids = [] if behavior['mode'] == 'not_listed' else ['muse-glimmer']
            self.send(200, {'data': [{'id': i} for i in ids]})

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            behavior['completions'] += 1
            mode = behavior['mode']
            if mode == 'http_500':
                return self.send(500, {'error': 'x'})
            tokens = 0 if mode == 'no_tokens' else 3
            model = 'other' if mode == 'wrong_model' else 'muse-glimmer'
            self.send(200, {'model': model, 'choices': [{'message': {'content': 'ready'}}],
                            'usage': {'completion_tokens': tokens}})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Probe(unittest.TestCase):
    def test_ready_only_when_the_loaded_model_generates(self):
        behavior = {'mode': 'ok', 'completions': 0}
        server = fake_vllm(behavior)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_address[1]}'
        expected = {'ok': (True, 'ready'), 'not_listed': (False, 'model_not_listed'),
                    'wrong_model': (False, 'completion_invalid'), 'no_tokens': (False, 'no_tokens_generated'),
                    'http_500': (False, 'http_500')}
        for mode, result in expected.items():
            with self.subTest(mode):
                behavior['mode'] = mode
                self.assertEqual(H.probe(base, 'muse-glimmer'), result)
        self.assertEqual(behavior['completions'], 4)  # a real completion every time the model is listed
        self.assertEqual(H.probe('http://127.0.0.1:9', 'muse-glimmer'), (False, 'unreachable'))


class Decide(unittest.TestCase):
    def run_seq(self, results, start=0.0, active=-10_000.0, state=None):
        state, actions = state or {}, []
        for i, ok in enumerate(results):
            state, action = H.decide(state, ok, 'ready' if ok else 'unreachable', start + 60 * i, active)
            actions.append((state['status'], action))
        return state, actions

    def test_startup_grace_then_bounded_restarts_then_operator(self):
        state, actions = self.run_seq([False] * 3, start=0, active=-30)  # unit just became active, never ready
        self.assertEqual([a for a in actions], [('starting', None)] * 3)
        state, actions = self.run_seq([True, False, False, False], state={})
        self.assertEqual(actions[-1], ('recovering', 'restart'))
        state, actions = self.run_seq([False] * 9, start=1000, state=state)
        self.assertEqual([a[1] for a in actions].count('restart'), 2)  # 3 per hour in total
        self.assertEqual(state['status'], 'failed_needs_operator')
        state, actions = self.run_seq([False] * 5, start=1600, state=state)
        self.assertEqual({a for a in actions}, {('failed_needs_operator', None)})  # stops acting
        state, actions = self.run_seq([True], start=1900, state=state)
        self.assertEqual(state['status'], 'ready')  # recovers on its own if the model comes back

    def test_restart_budget_refills_after_an_hour_and_inactive_unit_counts_as_down(self):
        state = {'restarts': [0, 10, 20], 'failures': 2, 'status': 'degraded', 'lastOk': -100}
        state, action = H.decide(state, False, 'unreachable', 3700, -10_000)
        self.assertEqual((state['status'], action, len(state['restarts'])), ('recovering', 'restart', 1))
        state, action = H.decide({}, False, 'unreachable', 0, None)
        self.assertEqual((state['status'], action, state['failures']), ('down', None, 1))

    def test_run_once_restarts_records_receipt_and_writes_status(self):
        tmp = Path(tempfile.mkdtemp(prefix='health-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        calls = []
        a = types.SimpleNamespace(base='x', model='muse-glimmer', state=tmp / 's.json', status=tmp / 'run/h.json',
                                  receipt=tmp / 'receipt.json', record=['record-cmd'])
        for i in range(3):
            H.run_once(a, now=10_000 + i, prober=lambda b, m: (False, 'unreachable'), since=lambda: 0,
                       run=lambda cmd, timeout: calls.append(cmd))
        self.assertEqual(calls, [['systemctl', 'restart', 'muse-glimmer.service']])
        H.run_once(a, now=10_100, prober=lambda b, m: (True, 'ready'), since=lambda: 10_050,
                   run=lambda cmd, timeout: calls.append(cmd))
        self.assertEqual(calls[-1], ['record-cmd'])
        status = json.loads(a.status.read_text())
        self.assertEqual((status['status'], status['restartsLastHour'], status['modelReceipt']), ('ready', 1, False))
        self.assertNotIn('Readiness', a.status.read_text())


def fake_cache(root, model, extra=None, corrupt=None):
    snap = V.snapshot(root, model['repo'], model['revision'])
    blobs = snap.parents[1] / 'blobs'
    snap.mkdir(parents=True)
    blobs.mkdir(parents=True, exist_ok=True)
    contents = {'config.json': b'{"x": 1}', 'tokenizer.json': b't' * 7, 'model-00001-of-00002.safetensors': b'w' * 11,
                **(extra or {})}
    for rel, data in contents.items():
        lfs = rel.endswith('.safetensors') or rel == 'tokenizer.json'
        blob = hashlib.sha256(data).hexdigest() if lfs else hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()
        (blobs / blob).write_bytes(corrupt(rel, data) if corrupt else data)
        (snap / rel).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(os.path.relpath(blobs / blob, (snap / rel).parent), snap / rel)
    return snap


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='verify-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.model = {'repo': 'meta-models/Muse-Glimmer-30B', 'revision': 'c' * 40,
                      'weights_fetch': {'files': {'model-00001-of-00002.safetensors': 11, 'tokenizer.json': 7}}}
        self.receipt = self.tmp / 'receipt.json'

    def main(self, *args):
        cfg = self.tmp / 'cfg.json'
        cfg.write_text(json.dumps({'model': self.model}))
        return V.main([*args, '--config', str(cfg), '--cache', str(self.tmp / 'hf'), '--receipt', str(self.receipt)])

    def test_first_start_record_then_quick_and_full_checks(self):
        self.assertEqual(self.main('check'), V.EXIT_NO_RECEIPT)  # first start may download
        fake_cache(self.tmp / 'hf', self.model)
        self.assertEqual(self.main('record'), 0)
        self.assertEqual(self.main('check'), 0)
        self.assertEqual(self.main('check', '--full'), 0)

    def test_corruption_and_wrong_pins_refuse(self):
        snap = fake_cache(self.tmp / 'hf', self.model, corrupt=lambda rel, d: d[:-1] + b'X' if rel == 'tokenizer.json' else d)
        self.assertEqual(self.main('record'), V.EXIT_MISMATCH)  # content differs from the hub id
        shutil.rmtree(self.tmp / 'hf')
        fake_cache(self.tmp / 'hf', self.model)
        self.model['weights_fetch']['files']['model-00002-of-00002.safetensors'] = 5
        self.assertEqual(self.main('record'), V.EXIT_MISMATCH)  # a pinned file is missing
        del self.model['weights_fetch']['files']['model-00002-of-00002.safetensors']
        self.assertEqual(self.main('record'), 0)
        snap = V.snapshot(self.tmp / 'hf', self.model['repo'], self.model['revision'])
        blob = (snap / 'model-00001-of-00002.safetensors').resolve()
        blob.write_bytes(b'v' * 11)  # same size, different content
        self.assertEqual(self.main('check'), 0)  # quick check: names and sizes only
        self.assertEqual(self.main('check', '--full'), V.EXIT_MISMATCH)
        (snap / 'extra.bin').write_bytes(b'x')
        self.assertEqual(self.main('check'), V.EXIT_MISMATCH)  # not a cache blob / file set changed
        (snap / 'extra.bin').unlink()
        self.model['revision'] = 'd' * 40
        self.assertEqual(self.main('check'), V.EXIT_MISMATCH)  # receipt is for another revision


if __name__ == '__main__':
    unittest.main()
