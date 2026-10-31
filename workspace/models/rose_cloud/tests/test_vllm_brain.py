"""VllmBrain/VllmSequence against a stand-in vLLM server (tests/fakes.py): protocol handling, not a model.

The flows mirror how PR288's conductor and task manager drive the brain at its pinned head: the warm-up
(system prefix, mark, greeting, generate, rewind), a reply that ends with <|im_end|> fed into the cache, a barge-in
that stops a reply mid-stream, a phone-agent fork, and thinking with an extra stop token.
"""

from __future__ import annotations

import socket
import sys
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from fakes import CharTokenizer, FakeVllm  # noqa: E402
from rose_cloud.vllm_brain import ServerError, Unsupported, VllmBrain  # noqa: E402


@dataclass
class Sampling:  # the fields of PR288 rose/brain.py Sampling, which imports mlx and cannot load here
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    presence_penalty: float = 0.0
    max_tokens: int = 400


GREEDY = Sampling(temperature=0.0)
GEN_FAST = '<|im_start|>assistant\n<think>\n\n</think>\n\n'


class Base(unittest.TestCase):
    def setUp(self):
        self.tok = CharTokenizer()

    def server(self, *script, **kw):
        fake = FakeVllm(script, **kw)
        self.addCleanup(fake.close)
        return fake, VllmBrain(self.tok, fake.url, 'rose-brain', timeout_s=10)

    def ids(self, text):
        return self.tok.encode(text)


class Flows(Base):
    def test_warm_up_marks_and_rewinds_exactly(self):
        fake, brain = self.server(self.ids('Hi there'))
        seq = brain.sequence('talker')
        prefix = brain.system_prefix('You are Rose.', [{'name': 'look'}])
        for _ in seq.append_text(prefix):
            pass
        mark = seq.mark()
        for _ in seq.append_text('<|im_start|>user\nHello!<|im_end|>\n' + GEN_FAST):
            pass
        sent = list(seq.tokens)
        out = list(seq.generate(Sampling(temperature=0.0, max_tokens=6)))
        self.assertEqual(out, self.ids('Hi the'))
        body = fake.requests[0]
        self.assertEqual(body['prompt'], sent)  # exactly the client's tokens, specials included
        self.assertEqual((body['max_tokens'], body['temperature'], body['top_k'], body['stop_token_ids'],
                          body['skip_special_tokens'], body['stream'], body['return_token_ids'], body['model']),
                         (6, 0.0, 20, sorted(brain.eos), False, True, True, 'rose-brain'))
        seq.rewind(mark)
        self.assertEqual(seq.tokens, self.ids(prefix))
        self.assertEqual((seq.prefill_tokens, seq.decode_tokens, seq.requests), (len(sent), 6, 1))

    def test_stop_token_is_yielded_and_fed(self):
        fake, brain = self.server(self.ids('OK.') + [brain_im_end()] + self.ids('junk'), self.ids('x'))
        seq = brain.sequence()
        list(seq.append_text('<|im_start|>user\nhi<|im_end|>\n' + GEN_FAST))
        out = list(seq.generate(GREEDY))
        self.assertEqual(out[-1], brain.im_end)
        self.assertEqual(seq.tokens[-1], brain.im_end)
        list(seq.append_text('<|im_start|>user\nnext<|im_end|>\n'))
        list(seq.generate(Sampling(max_tokens=1)))
        self.assertEqual(fake.requests[1]['prompt'][: len(fake.requests[0]['prompt']) + len(out)],
                         fake.requests[0]['prompt'] + out)  # the cache holds the reply and its <|im_end|>

    def test_barge_in_keeps_only_yielded_tokens_and_aborts_the_request(self):
        fake, brain = self.server(self.ids('a long reply that goes on and on'), delay_s=0.02)
        seq = brain.sequence()
        list(seq.append_text('q'))
        start = len(seq.tokens)
        gen = seq.generate(Sampling(max_tokens=100))
        heard = [next(gen), next(gen)]
        gen.close()
        self.assertEqual(seq.tokens[start:], heard)
        deadline = time.monotonic() + 5
        while not fake.aborted and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(fake.aborted, 1)  # the server saw the disconnect and stopped writing
        self.assertLess(len(fake.sent), len('a long reply that goes on and on'))

    def test_cancel_callable_stops_before_appending(self):
        fake, brain = self.server(self.ids('abcdef'))
        seq = brain.sequence()
        list(seq.append_text('q'))
        n = []
        out = list(seq.generate(Sampling(max_tokens=10), cancel=lambda: len(n) >= 3 or n.append(1)))
        self.assertEqual(len(out), 3)
        self.assertEqual(seq.tokens[-3:], out)

    def test_fork_is_independent_and_rewind_then_append_is_exact(self):
        fake, brain = self.server(self.ids('tap'), self.ids('yes'))
        agent = brain.sequence('agent')
        list(agent.append_text('<|im_start|>system\nphone agent<|im_end|>\n'))
        base = list(agent.tokens)
        task = agent.fork('task 1')
        list(task.append_text('<|im_start|>user\nopen Maps<|im_end|>\n' + GEN_FAST))
        list(task.generate(Sampling(max_tokens=3)))
        self.assertEqual(agent.tokens, base)
        mark = task.mark()
        list(task.append_text('<|im_start|>user\nscreen A<|im_end|>\n'))
        task.rewind(mark)
        list(task.append_text('<|im_start|>user\nscreen B<|im_end|>\n'))
        list(task.generate(Sampling(max_tokens=3)))
        self.assertEqual(fake.requests[1]['prompt'],
                         base + self.ids('<|im_start|>user\nopen Maps<|im_end|>\n' + GEN_FAST) + self.ids('tap')
                         + self.ids('<|im_start|>user\nscreen B<|im_end|>\n'))
        with self.assertRaises(ValueError):
            agent.rewind(mark)  # a mark from a longer sequence

    def test_thinking_stop_set_and_sampling(self):
        think_end = self.tok.encode('</think>')[0]
        fake, brain = self.server(self.ids('reasoning') + [think_end])
        seq = brain.sequence()
        list(seq.append_text('<|im_start|>user\nwhy?<|im_end|>\n<|im_start|>assistant\n<think>\n'))
        out = list(seq.generate(Sampling(temperature=0.6, top_p=0.95, top_k=0, presence_penalty=1.5,
                                         max_tokens=1200), stop=brain.eos | {think_end}))
        self.assertEqual(out[-1], think_end)
        body = fake.requests[0]
        self.assertEqual((body['temperature'], body['top_p'], body['top_k'], body['presence_penalty'],
                          body['stop_token_ids']), (0.6, 0.95, -1, 1.5, sorted(brain.eos | {think_end})))


class Protocol(Base):
    def test_older_logprobs_token_id_form(self):
        fake, brain = self.server(self.ids('hello') + [brain_im_end()], mode='logprobs', chunk=2)
        seq = brain.sequence()
        list(seq.append_text('q'))
        self.assertEqual(list(seq.generate(GREEDY)), self.ids('hello') + [brain.im_end])

    def test_stop_token_reported_only_as_stop_reason(self):
        fake, brain = self.server(self.ids('ok') + [brain_im_end()], send_stop=False)
        seq = brain.sequence()
        list(seq.append_text('q'))
        self.assertEqual(list(seq.generate(GREEDY)), self.ids('ok') + [brain.im_end])
        self.assertEqual(seq.tokens[-1], brain.im_end)

    def test_max_tokens_ends_without_stop(self):
        fake, brain = self.server(self.ids('abcdefgh'), chunk=3)
        seq = brain.sequence()
        list(seq.append_text('q'))
        self.assertEqual(list(seq.generate(Sampling(max_tokens=5))), self.ids('abcde'))

    def test_server_stopping_early_is_refused_not_approximated(self):
        fake, brain = self.server(self.ids('ab'))  # the script ends: an EOS-like stop the caller did not request
        seq = brain.sequence()
        list(seq.append_text('q'))
        with self.assertRaises(Unsupported):
            list(seq.generate(Sampling(max_tokens=10)))
        fake, brain = self.server(self.ids('ab') + [brain_im_end()], send_stop=False)
        seq = brain.sequence()
        list(seq.append_text('q'))
        stop_only = {self.tok.encode('</think>')[0]}  # the server's default stop (<|im_end|>) still applies
        with self.assertRaises(Unsupported):
            list(seq.generate(Sampling(max_tokens=10), stop=stop_only))

    def test_errors_carry_no_server_text(self):
        for kw in (dict(status=500), dict(raw=b'data: {not json SECRET\n\n'), dict(raw=b'data: ' + b'x' * (2 << 20)),
                   dict(raw=b'data: {"error": "SECRET"}\n\n'), dict(raw=b''),
                   dict(raw=b'data: {"choices": [{"token_ids": ["SECRET"]}]}\n\n')):
            with self.subTest(kw=list(kw)):
                fake, brain = self.server(**kw)
                seq = brain.sequence()
                list(seq.append_text('q'))
                with self.assertRaises(ServerError) as e:
                    list(seq.generate(GREEDY))
                self.assertNotIn('SECRET', str(e.exception))
                self.assertNotIn('/root', str(e.exception))

    def test_unreachable_server(self):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        brain = VllmBrain(self.tok, f'http://127.0.0.1:{port}', 'm', timeout_s=2)
        seq = brain.sequence()
        list(seq.append_text('q'))
        with self.assertRaises(ServerError):
            list(seq.generate(GREEDY))


class Unsupported_(Base):
    def test_vision_is_refused(self):
        fake, brain = self.server()
        seq = brain.sequence()
        with self.assertRaises(Unsupported):
            list(seq.append_image(object()))
        with self.assertRaises(Unsupported):
            brain.image_size(1248, 1972)
        self.assertEqual(fake.requests, [])

    def test_other_tokenizer_is_refused(self):
        with self.assertRaises(Unsupported):
            VllmBrain(CharTokenizer(specials=['<|im_start|>', '<|im_end|>']), 'http://127.0.0.1:9', 'm')

    def test_generate_needs_a_prompt_and_zero_budget_does_nothing(self):
        fake, brain = self.server()
        seq = brain.sequence()
        with self.assertRaises(ValueError):
            list(seq.generate(GREEDY))
        list(seq.append_text('q'))
        self.assertEqual(list(seq.generate(Sampling(max_tokens=0))), [])
        self.assertEqual(fake.requests, [])


def brain_im_end():
    return CharTokenizer().encode('<|im_end|>')[0]


if __name__ == '__main__':
    unittest.main()
