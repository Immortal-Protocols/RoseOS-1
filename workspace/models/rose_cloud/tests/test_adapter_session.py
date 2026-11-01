"""rose_cloud/adapter_session.py (CLOUD-QWEN-ADAPTER-001): the real adapter (vllm_brain.VllmBrain over HTTP) driven
through one bounded session against tests/fakes.FakeVllm, a scripted stand-in server on 127.0.0.1. The fake replays
token ids, rebuilds the chat route's prompt ids from the request body as vLLM's processor would (text tokens; each
image one vision_start + (w/32)*(h/32) pads + vision_end), and can serve /metrics, run one sequence at a time and keep
generating after a disconnect. It tests the adapter's behaviour and the session's evidence logic, not a model.
"""

from __future__ import annotations

import base64
import json
import struct
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from fakes import CharTokenizer, FakeVllm  # noqa: E402
from rose_cloud import adapter_session as A  # noqa: E402
from rose_cloud.vllm_brain import VllmBrain  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover - PR288's environment and CI have Pillow
    Image = None

TEMPLATE = None  # CharTokenizer renders its own chat format


def server_prompt_ids(tok):
    """What vLLM's processor would report as prompt_token_ids for a chat-route body rendered by RAW_TEMPLATE."""
    vs, pad, ve = (tok.encode(t)[0] for t in ('<|vision_start|>', '<|image_pad|>', '<|vision_end|>'))

    def ids(body):
        out = []
        for part in body['messages'][0]['content']:
            if part['type'] == 'text':
                out += tok.encode(part['text'])
            else:
                png = base64.b64decode(part['image_url']['url'].split(',', 1)[1])
                w, h = struct.unpack('>II', png[16:24])
                out += [vs] + [pad] * ((w // 32) * (h // 32)) + [ve]
        return out
    return ids


@unittest.skipIf(Image is None, 'Pillow is needed for the synthetic image')
class Session(unittest.TestCase):
    def setUp(self):
        self.tok = CharTokenizer()
        self.image = Image.new('RGB', (256, 256), (255, 0, 0))
        for name, value in (('CANCEL_MAX_TOKENS', 60), ('IDLE_WAIT_S', 1.0), ('PROBE_SLOW_S', 1.0)):
            old = getattr(A, name)
            setattr(A, name, value)
            self.addCleanup(setattr, A, name, old)

    def ids(self, text):
        return self.tok.encode(text)

    def script(self, cancel_tokens=None, late=('ab',)):
        im_end = self.tok.encode('<|im_end|>')
        return [self.ids('Hi there friend') + im_end, self.ids('Red') + im_end, self.ids('Square') + im_end,
                self.ids('Sunny') + im_end, self.ids('Fine') + im_end,
                cancel_tokens if cancel_tokens is not None else self.ids('1, 2, 3, ' * 40),
                self.ids('y'), self.ids(late[0])]

    def run_session(self, **fake_kw):
        script = fake_kw.pop('script', None) or self.script()
        fake = FakeVllm(script, prompt_ids=server_prompt_ids(self.tok), **fake_kw)
        self.addCleanup(fake.close)
        brain = VllmBrain(self.tok, fake.url, 'rose-brain', TEMPLATE, timeout_s=10)
        return fake, A.run_session(brain, self.image, A.Server(fake.url, timeout_s=2))

    def test_multi_turn_image_history_fork_rewind_and_inflight_abort(self):
        fake, r = self.run_session(serial=True, metrics=True, delay_s=0.02)
        self.assertEqual(r['step'], 'done')
        self.assertEqual(r['imageTokens'], 64)
        self.assertEqual([r[t]['tokens'] for t in ('turn1', 'turn2', 'turn3')], [16, 4, 7])
        self.assertTrue(all(r[t]['endedWithImEnd'] for t in ('turn2', 'turn3')))
        self.assertEqual((r['turn1']['promptExact'], r['turn2']['promptExact'], r['turn3']['promptExact']),
                         (None, True, True))  # the text route has no server prompt to compare; the chat route does
        self.assertEqual(fake.paths[:6], ['/v1/completions', '/v1/chat/completions', '/v1/chat/completions',
                                          '/v1/chat/completions', '/v1/completions', '/v1/completions'])
        self.assertEqual(r['history'], {'rewindExact': True, 'forkUntouched': True, 'staleImageDropped': True,
                                        'staleRequestSent': True, 'forkImages': 1})
        self.assertNotIn('messages', fake.requests[4])  # after the stale image was rewound away: the text route
        self.assertEqual(r['imagesInHistory'], 1)
        c = r['cancel']
        self.assertEqual((c['fired'], c['tokensAtCancel'], c['appended'], c['noLateAppend'], c['workerJoined'],
                          c['runningAfter'], c['probeTokens'], c['semantics']),
                         (True, A.CANCEL_AFTER, A.CANCEL_AFTER, True, True, 0.0, 1, 'inflight_abort'))
        self.assertLess(c['clientStopS'], 1.0)
        self.assertEqual(fake.aborted, 1)  # the server saw the disconnect
        self.assertLess(len(fake.requests[5]['prompt']) + A.CANCEL_MAX_TOKENS, 2048)  # inside the context
        self.assertEqual((r['lateCancel']['tokens'], r['lateCancel']['lateCancelNoop']), (2, True))
        self.assertEqual(len(fake.requests), 8)  # exactly the scripted requests: no retry, nothing extra
        line = json.dumps(r)
        for text in ('Hi there', 'Red', 'Square', 'Sunny', 'colour', 'Rose', 'weather', 'image_url', 'base64'):
            self.assertNotIn(text, line)  # counts, booleans, timings and hashes only

    def test_server_that_keeps_generating_is_reported_not_claimed(self):
        _, r = self.run_session(serial=True, metrics=True, delay_s=0.05, ignore_disconnect=True)
        c = r['cancel']
        self.assertTrue(c['noLateAppend'])  # the client still suppressed every late token
        self.assertEqual(c['semantics'], 'client_suppressed_only')
        self.assertGreater(c['runningAfter'], 0)  # the gauge showed the request still running
        self.assertGreaterEqual(c['probeS'], A.PROBE_SLOW_S)  # and the probe waited behind it

    def test_without_metrics_the_probe_alone_is_labelled(self):
        _, r = self.run_session(serial=True, metrics=False, delay_s=0.02)
        self.assertEqual((r['cancel']['runningAfter'], r['cancel']['semantics']), (None, 'inflight_abort_probe_only'))
        _, r = self.run_session(serial=True, metrics=False, delay_s=0.05, ignore_disconnect=True)
        self.assertEqual(r['cancel']['semantics'], 'client_suppressed_only')

    def test_cancel_after_completion_is_not_an_abort(self):
        script = self.script(cancel_tokens=self.ids('1') + self.tok.encode('<|im_end|>'))
        del script[6]  # no probe follows a request that finished before the cancel
        fake, r = self.run_session(serial=True, metrics=True, script=script)
        self.assertEqual(len(fake.requests), 7)
        c = r['cancel']
        self.assertEqual((c['fired'], c['semantics'], c['runningAfter'], c['probeS']),
                         (False, 'completed_before_cancel', None, None))

    def test_prompt_mismatch_is_reported(self):
        fake = FakeVllm(self.script(), prompt_ids=lambda body: [7, 7, 7], serial=True, metrics=True)
        self.addCleanup(fake.close)
        r = A.run_session(VllmBrain(self.tok, fake.url, 'rose-brain', None, timeout_s=10), self.image,
                          A.Server(fake.url))
        self.assertEqual((r['turn2']['promptExact'], r['turn3']['promptExact']), (False, False))

    def test_server_error_stops_at_the_step(self):
        fake = FakeVllm(self.script(), status=503)
        self.addCleanup(fake.close)
        brain = VllmBrain(self.tok, fake.url, 'rose-brain', None, timeout_s=5)
        from rose_cloud.vllm_brain import ServerError
        with self.assertRaises(ServerError):
            A.run_session(brain, self.image, A.Server(fake.url))
        self.assertEqual(len(fake.requests), 1)  # nothing retried

    def test_metrics_parser(self):
        fake = FakeVllm(metrics=True)
        self.addCleanup(fake.close)
        self.assertEqual(A.Server(fake.url).running(), 0.0)
        self.assertIsNone(A.Server('http://127.0.0.1:9').running())  # unreachable: unknown, never 0
        self.assertEqual(A.RUNNING.findall(b'vllm:num_requests_running{model_name="m",engine="0"} 2.0\n'
                                           b'vllm:num_requests_running{model_name="m",engine="1"} 1.0\n'), [b'2.0',
                                                                                                           b'1.0'])


if __name__ == '__main__':
    unittest.main()
