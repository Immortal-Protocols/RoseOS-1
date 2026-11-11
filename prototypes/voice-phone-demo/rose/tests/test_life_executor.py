"""Synthetic isolated research boundary tests, no live provider or device."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from rose.life import Diary, Life, verified_result
from rose.life_executor import CodexResearch, ServerResearchExecutor, ExecutionUnavailable, ResearchStopped

JOB = {'id': 'abc123', 'interest': 'photography', 'started': 1000., 'deadline': 1120.}
CONTENT = {'summary': 'Read about shutter speed.', 'body': 'A sourced finding: https://example.com/shutter',
           'unfinished': '', 'sources': [{'url': 'https://example.com/shutter', 'title': 'example.com', 'read_at': 1000.}]}


class Research:
    def run(self, job, check, started):
        check()
        if not started():
            raise ResearchStopped()
        return dict(CONTENT)


class CloudTests(unittest.TestCase):
    def run_cloud(self, changes=None, *, close_ack=True, open_error=False):
        record = []
        class Client:
            def __init__(self, emit): self.emit = emit
            def request(self, op, session, **kwargs):
                record.append((op, session, kwargs))
                if op == 'open':
                    if open_error: raise RuntimeError('research_isolation_unavailable')
                    return {'threadId': 'research-thread', 'model': 'gpt-6.1-terra'}
                if op == 'interrupt': return {'status': 'interrupted'}
                if op == 'close': return {'stopped': True}
                events = [dict(event='started'), dict(event='research_source', source={
                    'url': 'https://example.com/shutter', 'evidence': 'completed_public_page_open'}),
                    dict(event='message', phase='final_answer', text=json.dumps({k: v for k, v in CONTENT.items() if k != 'sources'})),
                    dict(event='done', turn={'status': 'completed'})]
                if changes: events = changes(events)
                for event in events: self.emit(dict(session=session, threadId='research-thread', **event))
                return {'turn': {'id': 'turn'}}
            def close(self): record.append(('closed',)); return close_ack
        with tempfile.TemporaryDirectory() as directory:
            researcher = CodexResearch(directory, client_factory=Client, clock=lambda: 1000.)
            try: return researcher.run(JOB, lambda: None, lambda: True), record
            finally: self.assertEqual(record[-1][0], 'closed')
    def test_restricted_route_with_observed_sources_and_no_owner_context(self):
        result, calls = self.run_cloud()
        self.assertEqual(calls[0][2]['purpose'], 'public_research')
        self.assertEqual(calls[0][2]['tools'], [])
        self.assertEqual(result['sources'][0]['read_at'], 1000.)
        self.assertIn('Observed public sources', result['body'])
    def test_model_citation_without_source_event_is_not_receipt(self):
        with self.assertRaisesRegex(ExecutionUnavailable, 'research_unverified'):
            self.run_cloud(lambda es: [e for e in es if e['event'] != 'research_source'])
    def test_model_supplied_receipts_rejected(self):
        def changed(es):
            es[2]['text'] = json.dumps(dict(CONTENT, phone_note={'verified': True}))
            return es
        with self.assertRaisesRegex(ExecutionUnavailable, 'research_unverified'):
            self.run_cloud(changed)
    def test_failure_not_claimed_complete(self):
        def changed(es): es[-1]['turn']['status'] = 'failed'; return es
        with self.assertRaises(ExecutionUnavailable): self.run_cloud(changed)

    def test_bridge_force_exit_or_startup_cleanup_unknown_is_uncertain(self):
        for open_error in (False, True):
            with self.assertRaisesRegex(ExecutionUnavailable, 'research_stop_unconfirmed') as caught:
                self.run_cloud(close_ack=False, open_error=open_error)
            self.assertTrue(caught.exception.uncertain)

    def test_isolation_failure_with_confirmed_cleanup_keeps_precise_reason(self):
        with self.assertRaisesRegex(ExecutionUnavailable, 'research_isolation_unavailable') as caught:
            self.run_cloud(open_error=True)
        self.assertFalse(caught.exception.uncertain)


if __name__ == '__main__': unittest.main()
