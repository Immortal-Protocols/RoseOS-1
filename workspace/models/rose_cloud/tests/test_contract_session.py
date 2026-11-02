"""rose_cloud/contract_session.py (MODELS-QWEN-CONTRACT-001) against tests/fakes.FakeOpenAI, a scripted stand-in for
`vllm serve` with the proposed profile on 127.0.0.1. It tests the cases, the verdicts and the receipt's redaction, not
a model: whether the real Qwen server meets the contract is what the diagnostic is for.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from fakes import FakeOpenAI  # noqa: E402
from rose_cloud import contract_session as C  # noqa: E402

VERDICTS = json.loads((HERE / 'fixtures' / 'qwen_contract_verdicts.json').read_text())
LEAKS = (FakeOpenAI.THINKING, 'SECRET', 'AMBERFALCON', 'amberfalcon', 'Hello, I am Rose', 'Journal', 'river',
         'calculator, please', 'Who are you')


class Verdicts(unittest.TestCase):
    def test_the_shared_fixture_matches_the_rust_agent(self):
        # rose-chat-core tests/qwen_contract.rs asserts the same expectations against rose_chat_core::agent::Agent
        self.assertGreaterEqual(len(VERDICTS['cases']), 30)
        for case in VERDICTS['cases']:
            with self.subTest(case['name']):
                self.assertEqual(C.verdict(200, case['body'].encode()), case['expect'])
        self.assertTrue({c['expect'] for c in VERDICTS['cases']} <= set(C.VERDICTS))

    def test_http_errors_and_identity(self):
        ok = next(c['body'] for c in VERDICTS['cases'] if c['name'] == 'final_with_reasoning').encode()
        self.assertEqual(C.verdict(400, ok), 'failed:http_status')
        self.assertEqual(C.verdict(200, ok, model='muse-glimmer'), 'failed:model_identity_mismatch')
        self.assertEqual(C.observe(200, ok, 'final')['identityExact'], True)
        unreported = next(c['body'] for c in VERDICTS['cases'] if c['name'] == 'final_unreported_model').encode()
        o = C.observe(200, unreported, 'final')
        self.assertEqual((o['verdict'], o['expectMet'], o['identityExact']), ('final', True, False))  # the agent
        # accepts a reply without a model field; the contract requires the exact identity anyway

    def test_requests_are_the_pinned_client_bodies(self):
        r = C.REQUESTS
        self.assertEqual((r['schema'], r['model'], r['allowlist']), ('rose-qwen-contract-requests/1', 'rose-qwen',
                                                                     ['calculator']))
        self.assertEqual(sorted(r['cases']), ['chat', 'long', 'tool'])
        for name, steps in r['cases'].items():
            for step in steps:
                body = json.loads(step['body'])
                self.assertEqual((body['model'], body['max_tokens'], body['stream']), ('rose-qwen', 512, False))
                self.assertNotIn('temperature', body)  # the client sends none
                self.assertIn(step['expect'], C.VERDICTS)
        long = json.loads(r['cases']['long'][0]['body'])
        self.assertLessEqual(sum(len(m['content']) for m in long['messages']), 20000)  # the client's own budget
        self.assertIn('AMBERFALCON', long['messages'][1]['content'])  # given at the start, asked at the end
        self.assertEqual(r['cases']['long'][0]['recall'], 'amberfalcon')


class Run(unittest.TestCase):
    def serve(self, **mode):
        fake = FakeOpenAI(mode=mode)
        self.addCleanup(fake.close)
        return fake

    def test_a_conforming_server_meets_every_case_on_one_server(self):
        fake = self.serve()
        r = C.run(fake.url, 'contract')
        self.assertEqual((r['step'], r['allExpected'], r['reasoningSeparated']), ('done', True, True))
        self.assertEqual(r['models'], {'status': 200, 'ids': 1, 'idsExact': True, 'maxModelLen': 8192})
        self.assertEqual([r[k]['verdict'] for k in ('chat', 'tool', 'toolAfter', 'long')],
                         ['final', 'open_app:calculator', 'final', 'final'])
        self.assertTrue(all(r[k]['identityExact'] and r[k]['reasoningField'] == 'reasoning_content'
                            and r[k]['thinkInContent'] is False for k in ('chat', 'tool', 'toolAfter', 'long')))
        self.assertEqual((r['long']['recalled'], r['long']['beyondProven']), (True, True))
        self.assertGreater(r['long']['promptTokens'], 2048)
        # exactly the pinned client bodies, in order, byte for byte
        steps = [s for n in ('chat', 'tool', 'long') for s in C.REQUESTS['cases'][n]]
        self.assertEqual(fake.bodies, [s['body'].encode() for s in steps])
        self.assertEqual(fake.paths, ['/v1/models'] + ['/v1/chat/completions'] * 4)
        line = json.dumps(r)
        for leak in LEAKS:
            self.assertNotIn(leak, line)

    def test_single_cases(self):
        fake = self.serve()
        self.assertEqual([C.run(fake.url, c)['allExpected'] for c in ('chat', 'tool', 'long')], [True] * 3)
        r = C.run(fake.url, 'rm -rf /')
        self.assertEqual((r['case'], r['step']), (None, 'bad_case'))
        self.assertNotIn('allExpected', r)
        self.assertEqual(sorted(C.CASES), ['chat', 'contract', 'long', 'tool'])

    def test_each_contract_break_is_reported_not_accepted(self):
        checks = {
            'inline_think': lambda r: r['chat']['verdict'] == 'failed:malformed_action' and r['chat']['thinkInContent']
            and not r['reasoningSeparated'],
            'wrong_id': lambda r: r['chat']['verdict'] == 'failed:model_identity_mismatch'
            and r['chat']['identityExact'] is False,
            'no_reasoning': lambda r: r['chat']['verdict'] == 'final' and not r['reasoningSeparated'],
            'prose': lambda r: r['chat']['verdict'] == 'failed:malformed_action',
            'forget': lambda r: r['long']['verdict'] == 'final' and r['long']['recalled'] is False,
            'short_prompt': lambda r: r['long']['beyondProven'] is False,
            'truncate': lambda r: r['chat']['verdict'] == 'failed:truncated_reply' and r['chat']['finish'] == 'length',
            'extra_model': lambda r: r['models']['idsExact'] is False and r['models']['ids'] == 2,
            'max_model_len': lambda r: r['models']['maxModelLen'] == 2048,
        }
        for mode, check in checks.items():
            with self.subTest(mode):
                fake = self.serve(**{mode: 2048 if mode == 'max_model_len' else True})
                r = C.run(fake.url, 'contract')
                self.assertEqual(r['step'], 'done')
                self.assertFalse(r['allExpected'])
                self.assertTrue(check(r), r)
                line = json.dumps(r)
                for leak in LEAKS:
                    self.assertNotIn(leak, line)

    def test_the_follow_up_needs_the_expected_proposal(self):
        fake = self.serve(prose=True)
        r = C.run(fake.url, 'tool')
        self.assertEqual((r['tool']['verdict'], r['toolAfter']), ('failed:malformed_action', None))
        self.assertEqual(len(fake.bodies), 1)  # no follow-up body without the open_app it continues
        self.assertFalse(r['allExpected'])

    def test_unreachable_server_raises_and_keeps_what_was_measured(self):
        box = {}
        with self.assertRaises(OSError):
            C.run('http://127.0.0.1:9', 'chat', into=box)
        self.assertEqual((box['case'], box['step']), ('chat', 'models'))

    def test_stdlib_only(self):
        import ast
        tree = ast.parse((HERE.parent / 'rose_cloud' / 'contract_session.py').read_text())
        mods = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
            n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(mods, {'__future__', 'json', 'time', 'urllib', 'pathlib'})


if __name__ == '__main__':
    unittest.main()
