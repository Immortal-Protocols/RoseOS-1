"""Waiting for vLLM inside PR288's own cooperative Runtime: higher priorities keep running, cancellation is prompt.

Needs ROSE_PR288_DIR (PR288 prototypes/voice-phone-demo) for rose/runtime.py; skipped, with the reason, without it.
The server is the stand-in from tests/fakes.py with a delayed response header or a mid-stream stall. What this
shows is scheduling and cleanup on the real PR288 scheduler, not vLLM's own behaviour.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from fakes import CharTokenizer, FakeVllm  # noqa: E402
from rose_cloud import coop, nomlx  # noqa: E402
from rose_cloud.vllm_brain import VllmBrain  # noqa: E402

PR288 = os.environ.get('ROSE_PR288_DIR')


@dataclass
class Sampling:
    temperature: float = 0.0
    top_p: float = 0.8
    top_k: int = 20
    presence_penalty: float = 0.0
    max_tokens: int = 64


def threads(prefix):
    return [t for t in threading.enumerate() if t.name.startswith(prefix)]


@unittest.skipUnless(PR288, 'ROSE_PR288_DIR (PR288 prototypes/voice-phone-demo checkout) not set')
class Scheduling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nomlx.install()
        sys.path.insert(0, PR288)
        from rose import runtime
        cls.rt = runtime

    def setUp(self):
        self.tok = CharTokenizer()
        self.runtime = self.rt.Runtime()
        self.addCleanup(self.runtime.stop)

    def brain(self, *script, **kw):
        fake = FakeVllm(script, **kw)
        self.addCleanup(fake.close)
        return fake, VllmBrain(self.tok, fake.url, 'rose-brain', timeout_s=30, runtime=self.runtime)

    def talk_job(self, brain, seen, cancel=None):
        def job():
            seq = brain.sequence('talker')
            yield from seq.append_text('<|im_start|>user\nhi<|im_end|>\n')
            seen['start'] = time.monotonic()
            for tok in seq.generate(Sampling(), cancel=cancel):
                seen.setdefault('tokens', []).append(tok)
                yield None
            seen['end'] = time.monotonic()
            seen['seq'] = seq
        return self.runtime.submit(job, priority=self.rt.TALK, name='talk')

    def probe(self, priority, seen, key):
        submitted = time.monotonic()

        def job():
            seen[key] = time.monotonic() - submitted
            return None
            yield  # pragma: no cover
        return self.runtime.submit(job, priority=priority, name=key)

    def wait_for(self, cond, timeout=10):
        deadline = time.monotonic() + timeout
        while not cond() and time.monotonic() < deadline:
            time.sleep(0.005)
        return cond()

    def test_delayed_header_lets_listening_run_and_keeps_tokens_tokens(self):
        fake, brain = self.brain(self.tok.encode('Hello.<|im_end|>'), header_delay_s=1.0)
        seen = {}
        talk = self.talk_job(brain, seen)
        self.assertTrue(self.wait_for(lambda: 'start' in seen))
        time.sleep(0.1)  # the talker is now waiting for the response header
        self.probe(self.rt.LISTEN, seen, 'listen')
        self.probe(self.rt.SPEAK, seen, 'speak')
        self.probe(self.rt.BACKGROUND, seen, 'background')
        self.assertTrue(self.wait_for(lambda: 'listen' in seen and 'speak' in seen, 2))
        self.assertLess(seen['listen'], 0.1)  # ran during the 1 s wait
        self.assertLess(seen['speak'], 0.1)
        self.assertNotIn('background', seen)  # lower priority waits behind the talker, as behind GPU work
        talk.result(10)
        self.assertEqual(seen['tokens'], self.tok.encode('Hello.<|im_end|>'))
        self.assertTrue(all(isinstance(t, int) for t in seen['tokens']))  # never a Sleep or other marker
        self.assertTrue(self.wait_for(lambda: 'background' in seen))
        self.assertGreater(brain.waiter.nested_units, 0)
        self.assertTrue(seen['seq'].worker_stopped)

    def test_mid_stream_stall_lets_listening_run(self):
        fake, brain = self.brain(self.tok.encode('abcdefgh<|im_end|>'), stall_at=3, stall_s=1.0)
        seen = {}
        talk = self.talk_job(brain, seen)
        self.assertTrue(self.wait_for(lambda: len(seen.get('tokens', [])) >= 3))
        time.sleep(0.1)
        self.probe(self.rt.LISTEN, seen, 'listen')
        self.assertTrue(self.wait_for(lambda: 'listen' in seen, 2))
        self.assertLess(seen['listen'], 0.1)
        talk.result(10)
        self.assertEqual(seen['tokens'], self.tok.encode('abcdefgh<|im_end|>'))

    def test_cancelling_the_job_during_a_stall_stops_the_worker_and_adds_nothing(self):
        fake, brain = self.brain(self.tok.encode('abcdefgh<|im_end|>'), stall_at=2, stall_s=3.0)
        seen = {}
        talk = self.talk_job(brain, seen)
        self.assertTrue(self.wait_for(lambda: len(seen.get('tokens', [])) >= 2))
        time.sleep(0.1)
        t = time.monotonic()
        talk.cancel()
        try:
            talk.result(5)  # generate saw the cancellation inside its wait and returned; the job then ended
        except self.rt.Cancelled:
            pass  # or the runtime noticed it first, at a unit boundary
        self.assertLess(time.monotonic() - t, 1.0)  # not the 3 s stall
        self.assertTrue(self.wait_for(lambda: not threads('vllm-stream'), 3))  # worker joined
        self.assertEqual(seen['tokens'], self.tok.encode('ab'))
        self.assertTrue(self.wait_for(lambda: fake.aborted == 1, 5))  # the server saw the disconnect

    def test_cancel_callable_during_delayed_header(self):
        fake, brain = self.brain(self.tok.encode('abc<|im_end|>'), header_delay_s=2.0)
        flag = threading.Event()
        seen = {}
        talk = self.talk_job(brain, seen, cancel=flag.is_set)
        self.assertTrue(self.wait_for(lambda: 'start' in seen))
        time.sleep(0.1)
        flag.set()
        talk.result(2)  # generate returned at once; the job finished normally
        self.assertNotIn('tokens', seen)
        self.assertTrue(seen['seq'].worker_stopped)
        self.assertEqual(seen['seq'].tokens, self.tok.encode('<|im_start|>user\nhi<|im_end|>\n'))

    def test_waiter_refuses_a_foreign_runtime(self):
        with self.assertRaises(TypeError):
            coop.Waiter(object())


if __name__ == '__main__':
    unittest.main()
