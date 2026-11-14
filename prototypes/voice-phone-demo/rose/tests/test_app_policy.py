"""The fail-closed screening policy (docs/APP-BUILDER.md §3.7): exhaustive chain properties, the default policy that
accepts nothing, and recorded offline fixtures. No gate here calls a model or the network."""
import itertools
import tempfile
import time
import unittest
from pathlib import Path

from rose.app_builder import policy, toolchain
from rose.app_builder.service import Builder, Refused
from rose.tests.policy_fixtures import RecordedGate, approving_policy, load
from rose.tests.test_app_builder import SDK

R = policy.ScreenRequest('create', 'a stopwatch')
# What a gate does; the last five are not well-formed answers and must count as unavailable.
BEHAVIOURS = ('allow', 'review_required', 'deny', 'raise', 'not_a_decision', 'wrong_gate', 'wrong_version',
              'unknown_verdict', 'no_reason')


class Scripted:
    def __init__(self, behaviour, can_allow, index):
        self.behaviour, self.can_allow = behaviour, can_allow
        self.name, self.version = f'g{index}', '1'

    def assess(self, request):
        b = self.behaviour
        if b == 'raise':
            raise RuntimeError('gate failed')
        if b == 'not_a_decision':
            return {'verdict': 'allow'}
        reason = '' if b in ('allow', 'no_reason') else 'Because. Nothing was started.'
        verdict = {'unknown_verdict': 'approve', 'no_reason': 'deny', 'wrong_gate': 'allow',
                   'wrong_version': 'allow'}.get(b, b)
        name = 'other' if b == 'wrong_gate' else self.name
        version = '2' if b == 'wrong_version' else self.version
        return policy.Decision(verdict, 'c', reason, name, version)


def oracle(spec):
    """The chain's verdict, from the rules alone (independent of policy.Chain)."""
    if not spec:
        return policy.DENY
    seen = []
    for behaviour, can_allow in spec:
        verdict = behaviour if behaviour in policy.VERDICTS else policy.REVIEW  # malformed: unavailable
        seen.append((verdict, can_allow))
        if verdict == policy.DENY:
            return policy.DENY
    if any(v == policy.REVIEW for v, _ in seen):
        return policy.REVIEW
    return policy.ALLOW if any(c for _, c in seen) else policy.REVIEW


class ChainProperties(unittest.TestCase):
    def test_every_chain_of_up_to_three_gates(self):
        cases = 0
        for length in range(4):
            for spec in itertools.product(itertools.product(BEHAVIOURS, (False, True)), repeat=length):
                chain = policy.Chain([Scripted(b, c, i) for i, (b, c) in enumerate(spec)])
                decision, decisions = chain.assess(R)
                cases += 1
                self.assertEqual(decision.verdict, oracle(spec), spec)
                if decision.verdict == policy.ALLOW:  # the fail-closed invariants, stated directly
                    self.assertTrue(all(b == 'allow' for b, _ in spec), spec)
                    self.assertTrue(any(c for _, c in spec), spec)
                else:
                    self.assertTrue(decision.reason.strip(), spec)
                if any(b not in policy.VERDICTS for b, _ in spec[:len(decisions)]):
                    self.assertNotEqual(decision.verdict, policy.ALLOW, spec)
        self.assertEqual(cases, 1 + 18 + 18 ** 2 + 18 ** 3)

    def test_a_gate_that_hangs_is_unavailable_in_time(self):
        class Slow:
            name, version, can_allow = 'slow', '1', True

            def assess(self, request):
                time.sleep(1.0)
                return policy.Decision(policy.ALLOW, 'none', '', 'slow', '1')
        start = time.monotonic()
        decision, _ = policy.Chain([Slow()], timeout_s=0.1).assess(R)
        self.assertLess(time.monotonic() - start, 0.9)
        self.assertEqual((decision.verdict, decision.category), (policy.REVIEW, policy.UNAVAILABLE))

    def test_deny_stops_the_chain_and_no_objection_never_allows(self):
        later = RecordedGate()
        decision, decisions = policy.Chain([policy.PhraseGate(), later]).assess(
            policy.ScreenRequest('create', 'spy on my wife'))
        self.assertEqual((decision.verdict, decision.gate, len(decisions), later.seen), (policy.DENY, 'phrase', 1, []))
        decision, _ = policy.Chain([policy.PhraseGate(), RecordedGate(can_allow=False)]).assess(R)
        self.assertEqual((decision.verdict, decision.category), (policy.REVIEW, policy.UNAVAILABLE))


class Fixtures(unittest.TestCase):
    SETS = ('benign.jsonl', 'capability_notes.jsonl', 'refused.jsonl')

    def requests(self):
        for name in self.SETS:
            for row in load(name):
                yield name, row, policy.ScreenRequest(row['kind'], row['text'], tuple(row['priorText']))

    def test_fixture_files_are_well_formed(self):
        ids = set()
        for name, row, _ in self.requests():
            self.assertEqual(set(row) - {'notes'}, {'id', 'kind', 'text', 'priorText', 'expected', 'category', 'source'})
            self.assertIn(row['expected'], policy.VERDICTS)
            self.assertNotIn(row['id'], ids)
            ids.add(row['id'])
        self.assertGreater(len(ids), 150)

    def test_the_default_policy_accepts_none_of_them(self):
        chain = policy.default_policy()
        for name, row, request in self.requests():
            decision, _ = chain.assess(request)
            self.assertNotEqual(decision.verdict, policy.ALLOW, row['id'])
            if row['expected'] == policy.DENY:
                self.assertEqual(decision.verdict, policy.DENY, row['id'])
            else:
                self.assertEqual((decision.verdict, decision.category), (policy.REVIEW, policy.UNAVAILABLE), row['id'])

    def test_recorded_replay_matches_every_expectation(self):
        recorded = RecordedGate(recorded={row['text']: row['expected'] for _, row, _ in self.requests()},
                                default=None)
        chain = policy.Chain([policy.PhraseGate(), recorded])
        for name, row, request in self.requests():
            decision, _ = chain.assess(request)
            self.assertEqual(decision.verdict, row['expected'], row['id'])
            if row.get('notes'):
                self.assertTrue(decision.notes, row['id'])
        decision, _ = chain.assess(policy.ScreenRequest('create', 'not recorded anywhere'))
        self.assertEqual((decision.verdict, decision.category), (policy.REVIEW, policy.UNAVAILABLE))


class BuilderPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def builder(self, chain=None):
        b = Builder(Path(self.tmp.name) / 'b', toolchain.Tools(SDK), generate=lambda *a: None, policy=chain)
        self.addCleanup(b.ledger.close)
        return b

    def test_the_default_builder_accepts_nothing(self):
        b = self.builder()
        with self.assertRaises(Refused) as cm:
            b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'a stopwatch'})
        self.assertEqual(cm.exception.details, {'code': 'review_required', 'category': 'unavailable'})
        self.assertTrue(str(cm.exception).endswith('Nothing was started.'))
        self.assertEqual(b.projects(), [])
        self.assertFalse(b.run_next())

    def test_decisions_are_recorded_and_revise_is_judged_with_its_context(self):
        gate = RecordedGate()
        b = self.builder(policy.Chain([policy.PhraseGate(), gate]))
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r1', 'description': 'chess with Firebase'})
        recorded = b.ledger.screening(out['jobId'])
        self.assertEqual([d['gate'] for d in recorded['decisions']], ['phrase', 'recorded'])
        self.assertEqual([d['verdict'] for d in recorded['decisions']], ['allow', 'allow'])
        self.assertEqual(recorded['notes'], out['notes'])
        b.ledger.finish_failed(out['jobId'], 'internal', 'x', True)
        b.revise('d', out['projectId'], {'schemaVersion': 1, 'clientRequestId': 'r2', 'request': 'add a clock'})
        self.assertEqual(gate.seen[-1].kind, 'revise')
        self.assertEqual(gate.seen[-1].prior_text, ('chess with Firebase',))

    def test_review_required_is_never_overridden_and_a_replay_keeps_its_decision(self):
        gate = RecordedGate(recorded={'a timer': policy.REVIEW})
        b = self.builder(policy.Chain([policy.PhraseGate(), gate]))
        for _ in range(2):  # asking again changes nothing
            with self.assertRaises(Refused) as cm:
                b.create('d', {'schemaVersion': 1, 'clientRequestId': 't', 'description': 'a timer'})
            self.assertEqual(cm.exception.details['code'], 'review_required')
        self.assertEqual(b.projects(), [])
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'chess with Firebase'})
        b.policy = policy.default_policy()  # the rules change: a replay keeps its ids and notes
        again = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'chess with Firebase'})
        self.assertEqual(again, out)
        with self.assertRaises(Refused):  # anything new is screened by the current (accept-nothing) policy
            b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r3', 'description': 'chess with Firebase'})


if __name__ == '__main__':
    unittest.main()
