"""Project 2 composer intent/result v1 (docs/APP-BUILDER.md §4): only explicit requests reach the phone, and only the
phone's drawn or saved revision counts as done."""
import unittest
from types import SimpleNamespace

from rose import composer

CAPS = {'status': 'ok', 'schemaVersion': 1, 'kinds': list(composer.KINDS)}


class Link:
    def __init__(self, caps=CAPS, reply=None, fail=None):
        self.caps, self.reply, self.fail, self.sent = caps, reply, fail, []

    def call(self, action, timeout, packageName, operation, params):
        self.sent.append((action, packageName, operation, params))
        if self.fail == operation:
            raise ConnectionError('lost')
        if operation == 'capabilities':
            return self.caps
        return self.reply(params) if callable(self.reply) else self.reply


def phone(link):
    return SimpleNamespace(link=link)


def reply(status, **extra):
    return lambda p: {'schemaVersion': 1, 'requestId': p['requestId'], 'status': status, **extra}


class Params(unittest.TestCase):
    def test_kinds_and_text(self):
        self.assertEqual(composer.params('r1', {'kind': 'prompt', 'text': '  set up for cooking '}),
                         {'schemaVersion': 1, 'requestId': 'r1', 'kind': 'prompt', 'text': 'set up for cooking'})
        for kind in ('arrange', 'regenerate', 'undo'):
            self.assertEqual(composer.params('r', {'kind': kind}), {'schemaVersion': 1, 'requestId': 'r', 'kind': kind})

    def test_refusals(self):
        for args in (None, [], {}, {'kind': 'layout'}, {'kind': 'prompt'}, {'kind': 'prompt', 'text': '  '},
                     {'kind': 'prompt', 'text': 'x' * 501}, {'kind': 'undo', 'text': 'x'},
                     {'kind': 'arrange', 'grid': 3}, {'kind': 'prompt', 'text': 7}):
            with self.assertRaises(composer.ComposeRefused, msg=args):
                composer.params('r', args)
        for rid in ('', 'x' * 65, None):
            with self.assertRaises(composer.ComposeRefused):
                composer.params(rid, {'kind': 'arrange'})

    def test_tool_schema_matches_the_contract(self):
        schema = composer.TOOL['inputSchema']
        self.assertEqual(schema['properties']['kind']['enum'], list(composer.KINDS))
        self.assertFalse(schema['additionalProperties'])


class Outcome(unittest.TestCase):
    def test_only_a_named_revision_is_done(self):
        for status in ('shown', 'prepared'):
            r = composer.outcome('r', {'schemaVersion': 1, 'requestId': 'r', 'status': status, 'revision': 4})
            self.assertEqual((r['ok'], r['outcome'], r['revision']), (True, status, 4))
            for bad in (None, '4', -1, True, 1.5):
                r = composer.outcome('r', {'schemaVersion': 1, 'requestId': 'r', 'status': status, 'revision': bad})
                self.assertEqual((r['ok'], r['outcome']), (False, 'uncertain'), bad)

    def test_not_done_statuses(self):
        for status in ('rejected', 'unavailable', 'uncertain'):
            r = composer.outcome('r', {'schemaVersion': 1, 'requestId': 'r', 'status': status, 'revision': 3})
            self.assertEqual((r['ok'], r['outcome']), (False, status))
            self.assertNotIn('revision', r)
        r = composer.outcome('r', {'schemaVersion': 1, 'requestId': 'r', 'status': 'rejected', 'reason': 'no apps'})
        self.assertEqual(r['reason'], 'no apps')

    def test_malformed_other_request_or_version_is_uncertain(self):
        for result in (None, 'shown', {}, {'schemaVersion': 2, 'requestId': 'r', 'status': 'shown', 'revision': 1},
                       {'schemaVersion': 1, 'requestId': 'other', 'status': 'shown', 'revision': 1},
                       {'schemaVersion': 1, 'requestId': 'r', 'status': 'done', 'revision': 1}):
            r = composer.outcome('r', result)
            self.assertEqual((r['ok'], r['outcome']), (False, 'uncertain'), result)


class Call(unittest.TestCase):
    def test_shown_after_capabilities(self):
        link = Link(reply=reply('shown', revision=2))
        r = composer.call(phone(link), 'dev.example.composer', 'r1', {'kind': 'arrange'})
        self.assertEqual((r['ok'], r['outcome'], r['revision']), (True, 'shown', 2))
        self.assertEqual([s[2] for s in link.sent], ['capabilities', 'workspace_compose'])
        self.assertEqual(link.sent[1][3], {'schemaVersion': 1, 'requestId': 'r1', 'kind': 'arrange'})

    def test_refused_arguments_send_nothing(self):
        link = Link(reply=reply('shown', revision=2))
        r = composer.call(phone(link), 'dev.example.composer', 'r1', {'kind': 'prompt'})
        self.assertEqual(r['outcome'], 'rejected')
        self.assertIn('Nothing was sent', r['message'])
        self.assertEqual(link.sent, [])

    def test_no_provider_or_contract_is_unavailable_and_composes_nothing(self):
        self.assertEqual(composer.call(phone(Link()), None, 'r', {'kind': 'arrange'})['outcome'], 'unavailable')
        self.assertEqual(composer.call(SimpleNamespace(), 'p', 'r', {'kind': 'arrange'})['outcome'], 'unavailable')
        for caps in (None, {'status': 'ok', 'schemaVersion': 2, 'kinds': ['arrange']},
                     {'status': 'ok', 'schemaVersion': 1, 'kinds': ['prompt']}, {'status': 'error'}):
            link = Link(caps=caps, reply=reply('shown', revision=1))
            r = composer.call(phone(link), 'p', 'r', {'kind': 'arrange'})
            self.assertEqual(r['outcome'], 'unavailable', caps)
            self.assertEqual([s[2] for s in link.sent], ['capabilities'])
        link = Link(fail='capabilities')
        r = composer.call(phone(link), 'p', 'r', {'kind': 'arrange'})
        self.assertEqual((r['outcome'], r['message']), ('unavailable', composer.MESSAGES['offline']))
        r = composer.call(phone(Link(caps={'status': 'timeout'})), 'p', 'r', {'kind': 'arrange'})
        self.assertEqual(r['message'], composer.MESSAGES['offline'])
        self.assertGreater(composer.TIMEOUT_S, 20.0)  # outlasts Rose Agent's app_command wait
        r = composer.call(phone(Link(caps={'status': 'ok', 'schemaVersion': 1, 'kinds': ['prompt']})), 'p', 'r',
                          {'kind': 'arrange'})
        self.assertEqual(r['message'], composer.MESSAGES['unsupported'])
        r = composer.call(phone(Link(reply={'status': 'offline'})), 'p', 'r', {'kind': 'arrange'})
        self.assertEqual((r['outcome'], r['message']), ('unavailable', composer.MESSAGES['offline']))

    def test_a_lost_link_during_compose_is_uncertain_and_not_resent(self):
        link = Link(fail='workspace_compose')
        r = composer.call(phone(link), 'p', 'r', {'kind': 'regenerate'})
        self.assertEqual((r['ok'], r['outcome']), (False, 'uncertain'))
        self.assertEqual([s[2] for s in link.sent], ['capabilities', 'workspace_compose'])

    def test_prepared_without_interrupting(self):
        link = Link(reply=reply('prepared', revision=9))
        r = composer.call(phone(link), 'p', 'r', {'kind': 'prompt', 'text': 'unwind'})
        self.assertEqual((r['ok'], r['outcome'], r['revision']), (True, 'prepared', 9))
        self.assertIn('swipes', r['message'])

    def test_not_offered_to_the_conversation_yet(self):
        from rose import codex_conductor
        self.assertNotIn('compose_workspace', {t['name'] for t in codex_conductor.CONVERSATION_TOOLS})


if __name__ == '__main__':
    unittest.main()
