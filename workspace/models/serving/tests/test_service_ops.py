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
            if mode == 'malformed':
                data = b'<html>SECRET-REPLY-TEXT not json'
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            tokens = 0 if mode == 'no_tokens' else 3
            model = 'other' if mode == 'wrong_model' else 'muse-glimmer'
            self.send(200, {'model': model, 'choices': [{'message': {'content': 'SECRET-REPLY-TEXT ready'}}],
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
                    'http_500': (False, 'http_500'), 'malformed': (False, 'malformed_reply')}
        for mode, result in expected.items():
            with self.subTest(mode):
                behavior['mode'] = mode
                self.assertEqual(H.probe(base, 'muse-glimmer'), result)
        self.assertEqual(behavior['completions'], 5)  # a real completion every time the model is listed
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
                                  receipt=tmp / 'receipt.json', record=['record-cmd'], verify_start=tmp / 'vs.json',
                                  verify_full=tmp / 'vf.json')
        for i in range(3):
            H.run_once(a, now=10_000 + i, prober=lambda b, m, facts: (False, 'unreachable'), since=lambda: 0,
                       run=lambda cmd, timeout: calls.append(cmd), publisher=lambda k, v: None, boot=BOOT)
        self.assertEqual(calls, [['systemctl', 'restart', 'muse-glimmer.service']])
        H.run_once(a, now=10_100, prober=lambda b, m, facts: (True, 'ready'), since=lambda: 10_050,
                   run=lambda cmd, timeout: calls.append(cmd), publisher=lambda k, v: None, boot=BOOT)
        self.assertEqual(calls[-1], ['record-cmd'])
        status = json.loads(a.status.read_text())
        self.assertEqual((status['status'], status['restartsLastHour'], status['modelReceipt']), ('ready', 1, False))
        self.assertNotIn('Readiness', a.status.read_text())


BOOT, OLD_BOOT = '2f1c3a9e-0d4b-4c62-9a51-7e8f00c1d2e3', '11111111-2222-3333-4444-555555555555'
RECEIPT_KEYS = {'schema', 'bootId', 'checkedAt', 'ready', 'status', 'category', 'completion', 'lastOkThisBoot',
                'consecutiveFailures', 'restartsLastHour', 'restartLimitPerHour', 'action', 'modelReceipt'}


class HealthReceipt(unittest.TestCase):
    """MODEL-STATUS-RENEWAL-001: the allowlisted receipt published to guest attribute rose-models/health."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='health-receipt-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.behavior = {'mode': 'ok', 'completions': 0}
        server = fake_vllm(self.behavior)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.a = types.SimpleNamespace(
            base=f'http://127.0.0.1:{server.server_address[1]}', model='muse-glimmer', state=self.tmp / 's.json',
            status=self.tmp / 'run/h.json', receipt=self.tmp / 'receipt.json', record=None,
            verify_start=self.tmp / 'run/verify-start.json', verify_full=self.tmp / 'verify-full.json')
        self.published, self.calls = [], []

    def once(self, now, since=0, publisher=None, boot=BOOT, prober=H.probe):
        state, action = H.run_once(self.a, now=now, prober=prober, since=lambda: since, boot=boot,
                                   run=lambda cmd, timeout: self.calls.append(cmd),
                                   publisher=publisher or (lambda k, v: self.published.append((k, v))))
        return state, action, json.loads(self.a.status.read_text())

    def last(self):
        key, value = self.published[-1]
        self.assertEqual(key, 'health')
        self.assertLessEqual(len(value), H.RECEIPT_MAX)
        return json.loads(value)

    def test_ready_receipt_has_only_allowlisted_nonsecret_facts(self):
        self.a.receipt.write_text(json.dumps({'recordedAt': '2026-10-30T22:10:00Z', 'files': {'x': {'sha256': 'f' * 64}}}))
        self.a.verify_start.parent.mkdir(parents=True)
        self.a.verify_start.write_text(json.dumps({'result': 'verified', 'full': False, 'at': 9_000,
                                                   'reason': 'blob_changed: x.safetensors'}))
        self.a.verify_full.write_text(json.dumps({'result': 'verified', 'full': True, 'at': 5_000}))
        _, _, status = self.once(10_000)
        r = self.last()
        self.assertEqual(set(r), RECEIPT_KEYS)
        self.assertEqual((r['bootId'], r['checkedAt'], r['ready'], r['status'], r['category']),
                         (BOOT, 10_000, True, 'ready', 'ready'))
        self.assertEqual({k: v for k, v in r['completion'].items() if k != 'latencyMs'},
                         {'listed': True, 'http': 200, 'tokens': 3})
        self.assertIsInstance(r['completion']['latencyMs'], int)
        self.assertEqual((r['lastOkThisBoot'], r['restartsLastHour'], r['restartLimitPerHour']), (10_000, 0, 3))
        self.assertEqual(r['modelReceipt'], {'present': True, 'recordedAt': '2026-10-30T22:10:00Z',
                                             'startCheck': {'result': 'verified', 'full': False, 'at': 9_000},
                                             'fullCheck': {'result': 'verified', 'full': True, 'at': 5_000}})
        raw = self.published[-1][1]
        for secret in ('SECRET-REPLY-TEXT', 'Readiness', 'content', 'messages', 'safetensors', 'Bearer', 'f' * 64):
            self.assertNotIn(secret, raw)
        self.assertTrue(status['published'])

    def test_previous_boot_success_is_never_reported_as_this_boot(self):
        self.a.state.write_text(json.dumps({'status': 'ready', 'lastOk': 9_000, 'lastOkBoot': OLD_BOOT,
                                            'failures': 0, 'restarts': []}))
        self.behavior['mode'] = 'not_listed'  # just booted, model still loading
        state, action, _ = self.once(10_000, since=9_990)
        r = self.last()
        self.assertEqual((r['bootId'], r['ready'], r['status'], r['lastOkThisBoot'], action),
                         (BOOT, False, 'starting', None, None))
        self.behavior['mode'] = 'ok'
        self.once(10_060, since=9_990)
        self.assertEqual((self.last()['lastOkThisBoot'], self.last()['ready']), (10_060, True))

    def test_unavailable_model_and_malformed_reply_are_categories_without_content(self):
        for mode, category, http in (('http_500', 'http_500', 500), ('malformed', 'malformed_reply', None),
                                     ('no_tokens', 'no_tokens_generated', 200), ('not_listed', 'model_not_listed', None)):
            with self.subTest(mode):
                self.behavior['mode'] = mode
                self.once(20_000, since=0)
                r = self.last()
                self.assertEqual((r['ready'], r['category'], r['completion']['http']), (False, category, http))
                self.assertNotIn('SECRET-REPLY-TEXT', self.published[-1][1])
        self.once(20_000, since=0, prober=lambda b, m, facts: H.probe('http://127.0.0.1:9', m, facts=facts))
        self.assertEqual((self.last()['category'], self.last()['completion']['listed']), ('unreachable', False))
        self.assertEqual(self.last()['modelReceipt'], {'present': False, 'recordedAt': None, 'startCheck': None,
                                                       'fullCheck': None})

    def test_publication_failure_never_changes_the_decision_or_restarts(self):
        def broken(key, value):
            raise OSError('metadata server unreachable')
        self.behavior['mode'] = 'http_500'
        decisions = {}
        for label, publisher in (('published', None), ('broken', broken)):
            self.a.state.unlink(missing_ok=True)
            self.calls.clear()
            seq = [self.once(30_000 + 60 * i, since=0, publisher=publisher)[:2] for i in range(4)]
            decisions[label] = ([(s['status'], a) for s, a in seq], list(self.calls))
        self.assertEqual(decisions['published'], decisions['broken'])
        self.assertEqual(decisions['broken'][1], [['systemctl', 'restart', 'muse-glimmer.service']])  # only the limit
        status = json.loads(self.a.status.read_text())
        self.assertEqual((status['published'], status['publishError']), (False, 'OSError'))
        self.behavior['mode'] = 'ok'
        self.calls.clear()
        _, action, status = self.once(40_000, since=0, publisher=broken)
        self.assertEqual((action, self.calls, status['status']), (None, [], 'ready'))  # telemetry never restarts

    def test_stale_or_unreadable_verify_results_are_qualified_not_trusted(self):
        self.a.verify_full.write_text('not json')
        self.a.verify_start.parent.mkdir(parents=True)
        self.a.verify_start.write_text(json.dumps({'result': 'surprise', 'full': 'yes', 'at': True}))
        self.once(50_000)
        self.a.receipt.write_text(json.dumps({'recordedAt': 'yesterday <script>'}))
        self.once(50_060)
        self.assertEqual(self.last()['modelReceipt'], {'present': True, 'recordedAt': None,
                                                       'fullCheck': {'result': 'unreadable'},
                                                       'startCheck': {'result': 'unknown', 'full': False, 'at': None}})

    def test_unknown_boot_id_and_oversize_receipts(self):
        self.assertEqual(H.boot_id(self.tmp / 'missing'), 'unknown')
        (self.tmp / 'bid').write_text('../../etc/passwd\n')
        self.assertEqual(H.boot_id(self.tmp / 'bid'), 'unknown')
        state = {'status': 'ready', 'failures': 0, 'restarts': list(range(500)), 'lastOk': None}
        with self.assertRaises(ValueError):
            H.receipt(BOOT, 1, True, 'ready', state, None, {}, True, None,
                      {'result': 'verified', 'full': True, 'at': 1, 'pad': 'x' * 3000})


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
