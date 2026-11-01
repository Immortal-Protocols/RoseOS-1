"""Images through VllmBrain (CLOUD-VISION-001): request construction, stale and rewound images, cancellation,
malformed and oversized inputs, and nonblocking waits.

FIXTURES only: synthetic PIL images, the stand-in tokenizer and the stand-in vLLM server from tests/fakes.py, which
reports whatever prompt_token_ids a test hands it. Nothing here runs a vision model or shows what one would see.
"""

from __future__ import annotations

import base64
import io
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
from rose_cloud import vllm_brain as V  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

PR288 = os.environ.get('ROSE_PR288_DIR')
GEN_FAST = '<|im_start|>assistant\n<think>\n\n</think>\n\n'


@dataclass
class Sampling:
    temperature: float = 0.0
    top_p: float = 0.8
    top_k: int = 20
    presence_penalty: float = 0.0
    max_tokens: int = 16


def screenshot(w=1248, h=1972, color=(30, 120, 200)):
    """A synthetic Fold-cover-sized screenshot: a coloured rectangle on white."""
    img = Image.new('RGB', (w, h), 'white')
    img.paste(Image.new('RGB', (w // 2, h // 3), color), (w // 4, h // 3))
    return img


class Huge:
    """Claims a huge size; must be refused before any pixel work."""
    size = (20000, 20000)

    def convert(self, mode):  # pragma: no cover - must never be reached
        raise AssertionError('converted a refused image')


@unittest.skipIf(Image is None, 'pillow missing')
class Vision(unittest.TestCase):
    def setUp(self):
        self.tok = CharTokenizer()

    def brain(self, *script, prompt='echo', **kw):
        brain_kw = {k: kw.pop(k) for k in ('strict_prompt', 'session_id', 'runtime', 'request_template') if k in kw}
        echo = (lambda body: self.last_seq_tokens()) if prompt == 'echo' else prompt
        fake = FakeVllm(script, prompt_ids=echo, **kw)
        self.addCleanup(fake.close)
        return fake, V.VllmBrain(self.tok, fake.url, 'rose-brain', timeout_s=10, **brain_kw)

    def last_seq_tokens(self):
        return self.expected

    def looked(self, brain, image=None, name='talker'):
        """The conductor's look: user text, the screenshot, more text, the generation prompt."""
        seq = brain.sequence(name)
        list(seq.append_text('<|im_start|>user\nWhat is on my screen?\n'))
        units = seq.append_image(image or screenshot())
        items = []
        try:
            while True:
                items.append(next(units))
        except StopIteration as done:
            n = done.value
        list(seq.append_text('<|im_end|>\n' + GEN_FAST))
        self.expected = list(seq.tokens)
        return seq, n, items

    def test_pr288_sizes_and_token_counts(self):
        _, brain = self.brain()
        # PR288 rose/brain.py's measured table for a 1248x1972 Fold cover screenshot (long side -> tokens).
        for side, tokens in ((640, 260), (768, 360), (1024, 640)):
            self.assertEqual(brain.image_tokens(1248, 1972, side), tokens)
        self.assertEqual(brain.image_size(1248, 1972), (480, 768))
        self.assertEqual(brain.image_size(100, 50), (96, 64))  # never scaled up, kept on the 32 px grid
        self.assertEqual(brain.image_size(10, 10), (32, 32))

    def test_image_request_on_the_chat_route(self):
        fake, brain = self.brain(self.tok.encode('A blue box.<|im_end|>'), session_id='rose-session-1')
        seq, n, items = self.looked(brain)
        self.assertEqual((n, items), (360, [None]))
        vs, pad, ve = brain.vision_start, brain.image_pad, brain.vision_end
        start = seq.images[0][0]
        self.assertEqual(seq.tokens[start:start + n + 2], [vs] + [pad] * n + [ve])
        self.assertEqual({k: seq.last_image[k] for k in ('tokens', 'size', 'encode_ms', 'prefill_ms')},
                         {'tokens': 360, 'size': (480, 768), 'encode_ms': 0.0, 'prefill_ms': 0.0})
        out = list(seq.generate(Sampling()))
        self.assertEqual(out, self.tok.encode('A blue box.<|im_end|>'))
        self.assertEqual(fake.paths, ['/v1/chat/completions'])
        body = fake.requests[0]
        self.assertEqual((body['add_generation_prompt'], body['continue_final_message'], body['add_special_tokens'],
                          body['skip_special_tokens'], body['return_token_ids'], body['stop_token_ids'],
                          body['session_id'], body['stream']),
                         (False, False, False, False, True, sorted(brain.eos), 'rose-session-1', True))
        self.assertNotIn('chat_template', body)  # the server's --chat-template is RAW_TEMPLATE (write_raw_template)
        self.assertNotIn('prompt', body)
        (msg,) = body['messages']
        kinds = [p['type'] for p in msg['content']]
        self.assertEqual(kinds, ['text', 'image_url', 'text'])
        self.assertEqual(msg['content'][0]['text'], '<|im_start|>user\nWhat is on my screen?\n')
        self.assertEqual(msg['content'][2]['text'], '<|im_end|>\n' + GEN_FAST)
        url = msg['content'][1]['image_url']['url']
        self.assertTrue(url.startswith('data:image/png;base64,'))
        png = Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1])))
        self.assertEqual((png.format, png.size, png.mode), ('PNG', (480, 768), 'RGB'))
        self.assertTrue(seq.last_prompt_exact)  # the server's prompt tokens equal the client's
        self.assertTrue(seq.worker_stopped)

    def test_template_per_request_only_when_asked(self):
        fake, brain = self.brain(self.tok.encode('ok<|im_end|>'), request_template=True)
        seq, _, _ = self.looked(brain)
        list(seq.generate(Sampling()))
        self.assertEqual(fake.requests[0]['chat_template'], V.RAW_TEMPLATE)
        import tempfile
        with tempfile.NamedTemporaryFile('r', suffix='.jinja') as f:
            V.write_raw_template(f.name)
            self.assertEqual(Path(f.name).read_text(), V.RAW_TEMPLATE)

    def test_template_renders_back_the_client_text(self):
        """RAW_TEMPLATE with jinja2 (what vLLM renders with), if installed: text verbatim, one placeholder per image."""
        try:
            import jinja2
        except ImportError:
            self.skipTest('jinja2 not installed')
        _, brain = self.brain()
        seq, n, _ = self.looked(brain)
        rendered = jinja2.Environment().from_string(V.RAW_TEMPLATE).render(messages=seq.chat_messages())
        self.assertEqual(rendered, '<|im_start|>user\nWhat is on my screen?\n<|vision_start|><|image_pad|>'
                                   '<|vision_end|><|im_end|>\n' + GEN_FAST)
        # vLLM's processor expands the one pad to n pads: then it is exactly the client's sequence.
        self.assertEqual(self.tok.encode(rendered.replace('<|image_pad|>', '<|image_pad|>' * n)), seq.tokens)

    def test_prompt_mismatch_is_recorded_or_refused(self):
        fake, brain = self.brain(self.tok.encode('ok<|im_end|>'), prompt=lambda body: [1, 2, 3])
        seq, _, _ = self.looked(brain)
        list(seq.generate(Sampling()))
        self.assertIs(seq.last_prompt_exact, False)
        fake, brain = self.brain(self.tok.encode('ok<|im_end|>'), prompt=lambda body: [1, 2, 3], strict_prompt=True)
        seq, _, _ = self.looked(brain)
        before = list(seq.tokens)
        with self.assertRaises(V.Unsupported):
            list(seq.generate(Sampling()))
        self.assertEqual(seq.tokens, before)  # nothing appended from a request on another prompt
        fake, brain = self.brain(self.tok.encode('ok<|im_end|>'), prompt=None)  # an older vLLM: no prompt ids
        seq, _, _ = self.looked(brain)
        list(seq.generate(Sampling()))
        self.assertIsNone(seq.last_prompt_exact)

    def test_rewound_image_is_stale_and_never_sent(self):
        fake, brain = self.brain(self.tok.encode('no screen<|im_end|>'), self.tok.encode('x<|im_end|>'))
        seq = brain.sequence('talker')
        list(seq.append_text('<|im_start|>user\nhi\n'))
        mark = seq.mark()
        list(seq.append_image(screenshot()))
        seq.rewind(mark)  # the user spoke first: PR288's conductor rewinds the look
        list(seq.append_text('(No screenshot: they spoke first.)<|im_end|>\n' + GEN_FAST))
        self.assertEqual(seq.images, [])
        list(seq.generate(Sampling()))
        self.assertEqual(fake.paths, ['/v1/completions'])
        self.assertNotIn(brain.image_pad, fake.requests[0]['prompt'])
        task = brain.sequence('agent')
        list(task.append_text('<|im_start|>user\nstep\n'))
        list(task.append_image(screenshot()))
        fork = task.fork('task 7')
        fork.rewind(V.Mark(5, [], None, 5))
        self.assertEqual((len(task.images), len(fork.images)), (1, 0))  # each sequence keeps its own images

    def test_closing_the_look_midway_adds_nothing(self):
        _, brain = self.brain()
        seq = brain.sequence()
        list(seq.append_text('<|im_start|>user\nlook\n'))
        before = list(seq.tokens)
        units = seq.append_image(screenshot())
        next(units)  # the resize and encode unit ran ...
        units.close()  # ... then the conductor saw the barge-in and closed it
        self.assertEqual((seq.tokens, seq.images, seq.image_tokens), (before, [], 0))

    def test_cancel_during_a_delayed_vision_request(self):
        fake, brain = self.brain(self.tok.encode('late<|im_end|>'), header_delay_s=2.0)
        seq, _, _ = self.looked(brain)
        before = list(seq.tokens)
        flag = threading.Event()
        threading.Timer(0.2, flag.set).start()
        t = time.monotonic()
        self.assertEqual(list(seq.generate(Sampling(), cancel=flag.is_set)), [])
        self.assertLess(time.monotonic() - t, 1.5)
        self.assertEqual(seq.tokens, before)
        self.assertTrue(seq.worker_stopped)

    def test_malformed_oversized_and_too_many(self):
        _, brain = self.brain()
        seq = brain.sequence()
        for bad, why in ((object(), 'not an image'), (Huge(), 'image too large'),
                         (type('Z', (), {'size': (0, 10), 'convert': None})(), 'empty image'),
                         (type('F', (), {'size': (1.5, 2), 'convert': None})(), 'not an image')):
            with self.subTest(why):
                with self.assertRaises(ValueError) as e:
                    list(seq.append_image(bad))
                self.assertEqual(str(e.exception), why)
        self.assertEqual((seq.tokens, seq.images), ([], []))
        tiny = Image.new('RGB', (40, 40), 'red')
        for _ in range(V.MAX_IMAGES):
            list(seq.append_image(tiny))
        with self.assertRaises(V.Unsupported):
            list(seq.append_image(tiny))
        self.assertEqual(len(seq.images), V.MAX_IMAGES)
        big = screenshot(4000, 3000)
        png, size, n = brain.prepare_image(big)
        self.assertEqual((size, n), ((768, 576), 24 * 18))
        self.assertLess(len(png), V.MAX_PNG_BYTES)
        rgba = Image.new('RGBA', (64, 64), (255, 0, 0, 128))
        self.assertEqual(brain.prepare_image(rgba)[1], (64, 64))  # converted to RGB, as PR288 does

    def test_vision_needs_the_image_tokens(self):
        fake = FakeVllm(())
        self.addCleanup(fake.close)
        with self.assertRaises(V.Unsupported):
            V.VllmBrain(CharTokenizer(specials=['<|im_start|>', '<|im_end|>', '<|endoftext|>', '<tool_call>']),
                        fake.url, 'm')
        V.VllmBrain(CharTokenizer(specials=['<|im_start|>', '<|im_end|>', '<|endoftext|>', '<tool_call>']),
                    fake.url, 'm', vision=False)  # a text-only tokenizer works with vision off


@unittest.skipUnless(PR288, 'ROSE_PR288_DIR (PR288 prototypes/voice-phone-demo checkout) not set')
@unittest.skipIf(Image is None, 'pillow missing')
class VisionOnTheRuntime(unittest.TestCase):
    def test_listening_runs_while_a_vision_request_waits(self):
        from rose_cloud import nomlx
        nomlx.install()
        sys.path.insert(0, PR288)
        from rose import runtime as R
        rt = R.Runtime()
        self.addCleanup(rt.stop)
        tok = CharTokenizer()
        fake = FakeVllm([tok.encode('ok<|im_end|>')], header_delay_s=1.5, prompt_ids=lambda body: [])
        self.addCleanup(fake.close)
        brain = V.VllmBrain(tok, fake.url, 'rose-brain', runtime=rt)
        seen = {}

        def look():
            seq = brain.sequence('talker')
            yield from seq.append_text('<|im_start|>user\nlook\n')
            n = yield from seq.append_image(screenshot())
            yield from seq.append_text('<|im_end|>\n' + GEN_FAST)
            seen['n'], seen['start'] = n, time.monotonic()
            for _ in seq.generate(Sampling()):
                yield None
            seen['done'] = time.monotonic()

        rt.submit(look, priority=R.TALK, name='talker-look')
        deadline = time.monotonic() + 5
        while 'start' not in seen and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
        submitted = time.monotonic()

        def listen():
            seen['listen'] = time.monotonic() - submitted
            return None
            yield  # pragma: no cover
        rt.submit(listen, priority=R.LISTEN, name='listen-probe')
        deadline = time.monotonic() + 3
        while 'listen' not in seen and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertLess(seen['listen'], 0.1)  # ran inside the talker's wait for the vision response
        self.assertNotIn('done', seen)
        self.assertEqual(seen['n'], 360)


if __name__ == '__main__':
    unittest.main()
