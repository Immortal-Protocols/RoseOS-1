"""PR288's own scheduler and task manager, unchanged, driving VllmBrain (stand-in server, stand-in tokenizer).

Runs only with ROSE_PR288_DIR (PR288 prototypes/voice-phone-demo at the pinned head) and PR288's dependencies
(numpy, pillow, fastapi). It shows that rose/runtime.py, rose/tasks.py, rose/bus.py and rose/phone.py run as
written on a host without MLX, through rose_cloud.nomlx, and that a deep-think task completes through the adapter:
prompt segments, the thinking stop token, the spoken answer and the task result. It does not show a real model's
answer, the phone agent's tool-call loop or any audio.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from fakes import CharTokenizer, FakeVllm  # noqa: E402
from rose_cloud import nomlx  # noqa: E402
from rose_cloud.vllm_brain import VllmBrain  # noqa: E402

PR288 = os.environ.get('ROSE_PR288_DIR')


@unittest.skipUnless(PR288, 'ROSE_PR288_DIR (PR288 prototypes/voice-phone-demo checkout) not set')
class Pr288Tasks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy  # noqa: F401
            import PIL  # noqa: F401
        except ImportError as e:
            raise unittest.SkipTest(f'PR288 dependency missing: {e.name}')
        nomlx.install()
        sys.path.insert(0, PR288)
        from rose import runtime, tasks
        from rose.bus import Bus
        from rose.phone import Phone
        cls.runtime_mod, cls.tasks, cls.Bus, cls.Phone = runtime, tasks, Bus, Phone

    def test_think_task_completes_through_the_adapter(self):
        tok = CharTokenizer()
        fake = FakeVllm([tok.encode('Two plus two is four.</think>'), tok.encode('It is four.<|im_end|>')])
        self.addCleanup(fake.close)
        runtime = self.runtime_mod.Runtime()
        self.addCleanup(runtime.stop)
        brain = VllmBrain(tok, fake.url, 'rose-brain', timeout_s=10, runtime=runtime)  # waits via coop.Waiter
        done = threading.Event()
        finished = []
        manager = self.tasks.TaskManager(runtime, brain, self.Phone(vision=False), self.Bus(),
                                         lambda task: (finished.append(task), done.set()))
        manager.prepare()
        task = manager.start_think('What is two plus two?')
        self.assertTrue(done.wait(30))
        self.assertEqual((task.status, task.result), ('done', 'It is four.'))
        self.assertEqual([s['tokens'] for s in task.steps], [len('Two plus two is four.'), len('It is four.')])
        think, answer = fake.requests
        self.assertEqual(think['stop_token_ids'], sorted(brain.eos | {tok.encode('</think>')[0]}))
        self.assertTrue(tok.decode(think['prompt']).endswith('<|im_start|>assistant\n<think>\n'))
        text = tok.decode(answer['prompt'])
        self.assertIn('Two plus two is four.</think>', text)  # the thought stays in the cache before the answer
        self.assertTrue(text.endswith('<|im_start|>assistant\n<think>\n\n</think>\n\n'))
        self.assertEqual(manager._thinker.tokens, tok.encode(brain.system_prefix(self.tasks.THINK_SYSTEM)))  # forked

    def test_model_code_still_refuses_without_mlx(self):
        from rose_cloud.nomlx import MlxUnavailable
        import mlx.core as mx
        for op in ('argmax', 'softmax', 'random', 'zeros', 'new_stream_cpu'):
            with self.assertRaises(MlxUnavailable):
                getattr(mx, op)  # model arithmetic (PR288's Brain sampling, mlx-audio) has no stand-in
        import numpy as np
        self.assertIsInstance(mx.array([1.0]), np.ndarray)  # only host audio wrapping does (PR288 listener)


if __name__ == '__main__':
    unittest.main()
