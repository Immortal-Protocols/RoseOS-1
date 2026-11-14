"""Project 2 conversational route (docs/APP-BUILDER.md §4, OS Product's workspace_prepare carrier): only explicit
requests reach the phone, and only a committed layout revision counts as done."""
import unittest
from types import SimpleNamespace

from rose import composer


class Link:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.sent = reply, fail, []

    def call(self, action, timeout, **params):
        self.sent.append((action, params))
        if self.fail:
            raise ConnectionError('lost')
        return self.reply


def phone(link):
    return SimpleNamespace(link=link)


class Params(unittest.TestCase):
    def test_kinds_and_text(self):
        self.assertEqual(composer.params('r1', {'kind': 'text', 'text': '  set up for cooking '}),
                         {'requestId': 'r1', 'request': {'kind': 'text', 'text': 'set up for cooking'}})
        self.assertEqual(composer.params('r', {'kind': 'arrange'}), {'requestId': 'r', 'request': {'kind': 'arrange'}})

    def test_refusals(self):
        for args in (None, [], {}, {'kind': 'regenerate'}, {'kind': 'undo'}, {'kind': 'text'},
                     {'kind': 'text', 'text': '  '}, {'kind': 'text', 'text': 'x' * 501}, {'kind': 'arrange', 'text': 'x'},
                     {'kind': 'arrange', 'grid': 3}, {'kind': 'text', 'text': 7}):
            with self.assertRaises(composer.PrepareRefused, msg=args):
                composer.params('r', args)
        for rid in ('', 'x' * 65, None):
            with self.assertRaises(composer.PrepareRefused):
                composer.params(rid, {'kind': 'arrange'})

    def test_tool_schema_matches_the_contract(self):
        schema = composer.TOOL['inputSchema']
        self.assertEqual(schema['properties']['kind']['enum'], list(composer.KINDS))
        self.assertFalse(schema['additionalProperties'])


class Outcome(unittest.TestCase):
    def test_only_a_committed_revision_is_done(self):
        r = composer.outcome('r', {'status': 'committed', 'layout_revision': 4})
        self.assertEqual((r['ok'], r['outcome'], r['revision']), (True, 'prepared', 4))
        r = composer.outcome('r', {'status': 'committed', 'layout_revision': 4, 'shown': True})
        self.assertEqual((r['ok'], r['outcome']), (True, 'shown'))
        self.assertIn('Show workspace', composer.outcome('r', {'status': 'committed', 'layout_revision': 1})['message'])
        for bad in (None, '4', -1, True, 1.5):
            r = composer.outcome('r', {'status': 'committed', 'layout_revision': bad})
            self.assertEqual((r['ok'], r['outcome']), (False, 'uncertain'), bad)

    def test_not_done(self):
        r = composer.outcome('r', {'status': 'rejected', 'reason': 'nothing_available'})
        self.assertEqual((r['ok'], r['outcome'], r['reason']), (False, 'rejected', 'nothing_available'))
        for reason in ('unavailable', 'model_unavailable'):
            self.assertEqual(composer.outcome('r', {'status': 'rejected', 'reason': reason})['outcome'], 'unavailable')
        r = composer.outcome('r', {'status': 'busy'})
        self.assertEqual((r['ok'], r['outcome']), (False, 'busy'))
        r = composer.outcome('r', {'status': 'error', 'message': 'Unsupported phone action: workspace_prepare'})
        self.assertEqual(r['outcome'], 'unavailable')  # an older Rose Agent never ran it

    def test_anything_else_is_uncertain(self):
        for result in (None, 'committed', {}, {'status': 'done', 'layout_revision': 1}, {'status': 'running'},
                       {'status': 'timeout'}, {'status': 'disconnected'}, {'status': 'error', 'message': 'boom'}):
            r = composer.outcome('r', result)
            self.assertEqual((r['ok'], r['outcome']), (False, 'uncertain'), result)


class Call(unittest.TestCase):
    def test_prepared_through_workspace_prepare(self):
        link = Link(reply={'status': 'committed', 'layout_revision': 2})
        r = composer.call(phone(link), 'r1', {'kind': 'arrange'})
        self.assertEqual((r['ok'], r['outcome'], r['revision']), (True, 'prepared', 2))
        self.assertEqual(link.sent, [('workspace_prepare', {'requestId': 'r1', 'request': {'kind': 'arrange'}})])

    def test_refused_arguments_send_nothing(self):
        link = Link(reply={'status': 'committed', 'layout_revision': 2})
        r = composer.call(phone(link), 'r1', {'kind': 'text'})
        self.assertEqual(r['outcome'], 'rejected')
        self.assertIn('Nothing was sent', r['message'])
        self.assertEqual(link.sent, [])

    def test_no_link_or_a_link_that_never_sent_is_offline(self):
        r = composer.call(SimpleNamespace(), 'r', {'kind': 'arrange'})
        self.assertEqual((r['outcome'], r['message']), ('unavailable', composer.MESSAGES['offline']))
        r = composer.call(phone(Link(reply={'status': 'offline', 'message': 'Rose Agent is not connected.'})), 'r',
                          {'kind': 'arrange'})
        self.assertEqual((r['outcome'], r['message']), ('unavailable', composer.MESSAGES['offline']))

    def test_a_lost_link_after_sending_is_uncertain_and_not_resent(self):
        link = Link(fail=True)
        r = composer.call(phone(link), 'r', {'kind': 'text', 'text': 'unwind'})
        self.assertEqual((r['ok'], r['outcome']), (False, 'uncertain'))
        self.assertEqual(len(link.sent), 1)
        r = composer.call(phone(Link(reply={'status': 'disconnected'})), 'r', {'kind': 'arrange'})
        self.assertEqual(r['outcome'], 'uncertain')

    def test_outlasts_rose_agents_wait(self):
        self.assertGreater(composer.TIMEOUT_S, 20.0)

    def test_not_offered_to_the_conversation_yet(self):
        from rose import codex_conductor
        names = {t['name'] for t in codex_conductor.CONVERSATION_TOOLS}
        self.assertNotIn('prepare_workspace', names)
        self.assertNotIn('compose_workspace', names)


if __name__ == '__main__':
    unittest.main()
