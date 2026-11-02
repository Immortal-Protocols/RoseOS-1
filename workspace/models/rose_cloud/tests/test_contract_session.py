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
        self.assertEqual(r['models'], {'status': 200, 'ids': 1, 'idsExact': True, 'maxModelLen': 8192,
                                       'timedOut': False})
        self.assertEqual(r['notRun'], {})
        self.assertFalse(any(r[k]['timedOut'] for k in C.REPLIES))
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
        self.assertEqual((r['tool']['verdict'], r['notRun']), ('failed:malformed_action', {'toolAfter': 'no_proposal'}))
        self.assertNotIn('toolAfter', r)
        self.assertEqual(len(fake.bodies), 1)  # no follow-up body without the open_app it continues
        self.assertFalse(r['allExpected'])

    def test_the_client_limits_are_the_generated_rust_defaults(self):
        # rose-chat-core tests/qwen_contract.rs writes Limits::default() into the fixture and fails on drift
        self.assertEqual(C.CLIENT, {'inference_timeout_ms': 120000, 'max_tokens': 512, 'max_reply_chars': 8000})
        self.assertEqual((C.REQUEST_TIMEOUT_S, C.MAX_REPLY_CHARS), (120, 8000))
        for steps in C.REQUESTS['cases'].values():
            for step in steps:
                self.assertEqual(json.loads(step['body'])['max_tokens'], C.CLIENT['max_tokens'])
        self.assertLess(C.CASE_BUDGET_S, 300)  # bootstrap's QWEN_WARM_SESSION_S, asserted there too
        self.assertGreaterEqual(C.CASE_BUDGET_S, C.MODELS_TIMEOUT_S + 2 * C.REQUEST_TIMEOUT_S)  # chat and tool fit

    def test_unreachable_server_raises_and_keeps_what_was_measured(self):
        box = {}
        with self.assertRaises(OSError):
            C.run('http://127.0.0.1:9', 'chat', into=box)
        self.assertEqual((box['case'], box['step']), ('chat', 'models'))



class Deadlines(unittest.TestCase):
    """MODELS-QWEN-CONTRACT-002: the original Mac's PR353 runs hit the old 55 s driver bound (the initial full run in
    long, the repeated full run in the tool follow-up) and the TimeoutError ended the case with allExpected null. The
    driver now waits the client's own timeout, records a timeout as the client's verdict and accounts for what did not
    run. Scaled: the client timeout 120 s -> 1 s, the case budget 285 s -> 3.5 s."""

    def setUp(self):
        for name, value in (('REQUEST_TIMEOUT_S', 1.0), ('CASE_BUDGET_S', 3.5)):
            old = getattr(C, name)
            setattr(C, name, value)
            self.addCleanup(setattr, C, name, old)

    def serve(self, **mode):
        fake = FakeOpenAI(mode=mode)
        self.addCleanup(fake.close)
        return fake

    def test_a_timeout_is_the_clients_verdict_not_an_escaping_error(self):
        # the repeated full run: chat and the proposal answered, the reply after the tool result did not in time
        fake = self.serve(delay_s={'after': 1.5})
        r = C.run(fake.url, 'tool')  # the old driver raised TimeoutError here and left allExpected unset
        self.assertEqual((r['step'], r['allExpected'], r['notRun']), ('done', False, {}))
        self.assertEqual((r['tool']['verdict'], r['tool']['timedOut']), ('open_app:calculator', False))
        a = r['toolAfter']
        self.assertEqual((a['verdict'], a['expectMet'], a['timedOut'], a['status'], a['identityExact']),
                         ('failed:timeout_response', False, True, None, False))
        self.assertGreaterEqual(a['seconds'], 1.0)
        self.assertLess(a['seconds'], 1.4)  # it gave up at the client's timeout, not when the server answered
        self.assertEqual(len(fake.bodies), 2)

    def test_partial_results_stay_and_later_requests_still_run(self):
        # the initial full run: chat and the tool loop answered, long did not in time; every answered reply is kept
        fake = self.serve(delay_s={'long': 1.5})
        r = C.run(fake.url, 'contract')
        self.assertEqual([r[k]['verdict'] for k in C.REPLIES],
                         ['final', 'open_app:calculator', 'final', 'failed:timeout_response'])
        self.assertEqual((r['long']['recalled'], r['long']['beyondProven'], r['long']['timedOut']), (False, None, True))
        self.assertEqual((r['allExpected'], r['reasoningSeparated'], r['notRun']), (False, True, {}))
        # a slow first reply: the tool loop's follow-up is never sent without its proposal, and that is listed
        fake = self.serve(delay_s={'tool': 1.5})
        r = C.run(fake.url, 'contract')
        self.assertEqual((r['tool']['verdict'], r['notRun']), ('failed:timeout_response', {'toolAfter': 'no_proposal'}))
        self.assertEqual((r['chat']['verdict'], r['long']['verdict'], r['allExpected']), ('final', 'final', False))

    def test_requests_without_a_full_client_wait_left_are_listed_not_run(self):
        C.CASE_BUDGET_S = 2.4  # restored by setUp's cleanup
        fake = self.serve(delay_s={'chat': 1.2, 'tool': 1.2})  # each times out at 1 s: 2 s of the 2.4 s budget used
        r = C.run(fake.url, 'contract')
        self.assertEqual([r[k]['verdict'] for k in ('chat', 'tool')], ['failed:timeout_response'] * 2)
        self.assertEqual(r['notRun'], {'toolAfter': 'no_proposal', 'long': 'budget'})
        self.assertNotIn('long', r)
        self.assertEqual((r['allExpected'], len(fake.bodies)), (False, 2))  # long was never sent
        fake = self.serve(delay_s={'chat': 0.5, 'tool': 0.5, 'after': 0.5, 'long': 0.5})  # slow but in time
        r = C.run(fake.url, 'contract')
        self.assertEqual((r['notRun'], r['allExpected']), ({'long': 'budget'}, False))  # 1.5 s used: 0.9 s left
        self.assertTrue(all(r[k]['expectMet'] for k in ('chat', 'tool', 'toolAfter')))
        C.CASE_BUDGET_S = 0.9  # less than one client wait: nothing is sent, all listed
        r = C.run(fake.url, 'tool')
        self.assertEqual((r['notRun'], r['allExpected']), ({'tool': 'budget', 'toolAfter': 'budget'}, False))
        C.CASE_BUDGET_S = 2.4
        r = C.run(fake.url, 'long')  # the operator's follow-up case: alone it fits and passes
        self.assertEqual((r['notRun'], r['allExpected'], r['long']['recalled']), ({}, True, True))

    def test_a_slow_answer_inside_the_client_timeout_passes_with_its_time(self):
        fake = self.serve(delay_s={'chat': 0.6})
        r = C.run(fake.url, 'chat')
        self.assertEqual((r['allExpected'], r['chat']['timedOut'], r['chat']['verdict']), (True, False, 'final'))
        self.assertGreaterEqual(r['chat']['seconds'], 0.6)

    def test_timeout_receipts_stay_private_and_exact(self):
        fake = self.serve(delay_s={'after': 1.5, 'long': 1.5})
        r = C.run(fake.url, 'contract')
        line = json.dumps(r)
        for leak in LEAKS:
            self.assertNotIn(leak, line)
        self.assertEqual(set(r['toolAfter']), set(r['chat']))  # a timeout keeps the reply's full, fixed shape
        self.assertTrue({'recalled', 'beyondProven'} <= set(r['long']))

    def test_models_check_timeout_is_recorded(self):
        old = C.MODELS_TIMEOUT_S
        C.MODELS_TIMEOUT_S = 0.3
        self.addCleanup(setattr, C, 'MODELS_TIMEOUT_S', old)

        class SlowModels(FakeOpenAI):
            pass
        fake = self.serve()
        real = fake.server.RequestHandlerClass.do_GET

        def slow_get(handler):
            if handler.path == '/v1/models':
                import time
                time.sleep(0.6)
            return real(handler)
        fake.server.RequestHandlerClass.do_GET = slow_get
        r = C.run(fake.url, 'chat')
        self.assertEqual((r['models']['timedOut'], r['models']['idsExact'], r['allExpected']), (True, False, False))
        self.assertEqual(r['chat']['verdict'], 'final')  # the request itself still ran and is reported

    def test_stdlib_only(self):
        import ast
        tree = ast.parse((HERE.parent / 'rose_cloud' / 'contract_session.py').read_text())
        mods = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
            n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(mods, {'__future__', 'json', 'socket', 'time', 'urllib', 'pathlib'})



class NoThinking(unittest.TestCase):
    """MODELS-QWEN-CONTRACT-NOTHINK-001: the no-thinking profile's rule. Every check stays; reasoning in its own field
    is replaced by no reasoning at all, and a misrouted answer is the client's empty_completion, never a pass."""

    def serve(self, **mode):
        fake = FakeOpenAI(mode=mode)
        self.addCleanup(fake.close)
        return fake

    def test_a_server_with_thinking_off_meets_every_case(self):
        fake = self.serve(nothink=True)
        r = C.run(fake.url, 'contract', thinking=False)
        self.assertEqual((r['allExpected'], r['thinking'], r['noThinking'], r['reasoningSeparated'], r['notRun']),
                         (True, False, True, False, {}))
        self.assertEqual([r[k]['verdict'] for k in C.REPLIES], ['final', 'open_app:calculator', 'final', 'final'])
        self.assertTrue(all(r[k]['reasoningChars'] == 0 and r[k]['reasoningField'] is None
                            and r[k]['identityExact'] for k in C.REPLIES))
        self.assertEqual((r['long']['recalled'], r['long']['beyondProven']), (True, True))
        steps = [s for n in ('chat', 'tool', 'long') for s in C.REQUESTS['cases'][n]]
        self.assertEqual(fake.bodies, [s['body'].encode() for s in steps])  # the client's bodies, unchanged
        line = json.dumps(r)
        for leak in LEAKS:
            self.assertNotIn(leak, line)

    def test_each_rule_holds_only_for_its_own_profile(self):
        r = C.run(self.serve().url, 'chat', thinking=False)  # thinking still on: the setting did not take
        self.assertEqual((r['chat']['verdict'], r['noThinking'], r['allExpected']), ('final', False, False))
        r = C.run(self.serve(nothink=True).url, 'chat')  # the thinking profile's rule still needs reasoning
        self.assertEqual((r['thinking'], r['reasoningSeparated'], r['allExpected']), (True, False, False))
        r = C.run(self.serve().url, 'contract')  # and still passes a thinking server, unchanged
        self.assertEqual((r['allExpected'], r['reasoningSeparated'], r['noThinking']), (True, True, False))

    def test_misrouted_answers_and_inline_thinking_fail(self):
        r = C.run(self.serve(misroute=True).url, 'contract', thinking=False)
        self.assertEqual([r[k]['verdict'] for k in ('chat', 'tool', 'long')], ['failed:empty_completion'] * 3)
        self.assertEqual((r['notRun'], r['noThinking'], r['allExpected']),
                         ({'toolAfter': 'no_proposal'}, False, False))
        r = C.run(self.serve(nothink=True, inline_think=True).url, 'chat', thinking=False)
        self.assertEqual((r['chat']['verdict'], r['chat']['thinkInContent'], r['noThinking'], r['allExpected']),
                         ('failed:malformed_action', True, False, False))
        for mode in ('wrong_id', 'prose', 'forget', 'truncate', 'extra_model'):
            with self.subTest(mode):
                r = C.run(self.serve(nothink=True, **{mode: True}).url, 'contract', thinking=False)
                self.assertFalse(r['allExpected'])

    def test_timeouts_and_budget_unchanged(self):
        for name, value in (('REQUEST_TIMEOUT_S', 1.0), ('CASE_BUDGET_S', 3.5)):
            old = getattr(C, name)
            setattr(C, name, value)
            self.addCleanup(setattr, C, name, old)
        r = C.run(self.serve(nothink=True, delay_s={'after': 1.5}).url, 'tool', thinking=False)
        self.assertEqual((r['toolAfter']['verdict'], r['toolAfter']['timedOut'], r['allExpected']),
                         ('failed:timeout_response', True, False))

if __name__ == '__main__':
    unittest.main()
