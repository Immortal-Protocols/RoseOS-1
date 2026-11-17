"""A voice request for an app opens the app builder on the phone (rose.builder_voice): the exact phone command #881's
SettingsAgentProvider answers, an honest outcome for each reply, and nothing built or retried by voice."""
import unittest
from types import SimpleNamespace

from rose import builder_voice, codex_conductor


class Link:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def call(self, action, timeout=None, **params):
        self.calls.append((action, params))
        if self.error:
            raise self.error
        return self.reply


def phone(link):
    return SimpleNamespace(link=link)


class OpenBuilder(unittest.TestCase):
    def test_the_phone_command_is_exactly_the_providers(self):
        link = Link({'status': 'launch_requested', 'built': False})
        out = builder_voice.call(phone(link), 'open_app_builder', {'description': '  a stopwatch  '})
        self.assertEqual(link.calls, [('app_command', {'packageName': 'dev.pixel.voice', 'operation': 'open_builder',
                                                       'params': {'description': 'a stopwatch'}})])
        self.assertEqual((out['ok'], out['built']), (True, False))
        self.assertIn('taps Build', out['message'])

    def test_each_reply_is_reported_for_what_it_is(self):
        for reply, error, status in (({'status': 'locked'}, None, 'locked'), ({'status': 'unavailable'}, None,
                                                                                'unavailable'),
                                     (None, TimeoutError(), 'uncertain'), ('junk', None, 'uncertain')):
            with self.subTest(status=status):
                out = builder_voice.call(phone(Link(reply, error)), 'open_app_builder', {'description': 'notes'})
                self.assertEqual((out['ok'], out['status']), (False, status))

    def test_nothing_is_sent_for_a_request_it_cannot_take(self):
        for args in ({}, {'description': ''}, {'description': 7}, {'description': 'x' * 4001},
                     {'description': 'notes', 'build': True}, None):
            with self.subTest(args=args):
                link = Link({'status': 'launch_requested'})
                self.assertEqual(builder_voice.call(phone(link), 'open_app_builder', args)['status'], 'refused')
                self.assertEqual(link.calls, [])
        self.assertEqual(builder_voice.call(SimpleNamespace(), 'open_app_builder', {'description': 'x'})['status'],
                         'unavailable')

    def test_the_conversation_has_the_tool_and_its_policy(self):
        self.assertIn('open_app_builder', [t['name'] for t in codex_conductor.CONVERSATION_TOOLS])
        self.assertIn(builder_voice.POLICY_LINE, codex_conductor.CONVERSATION_POLICY)


if __name__ == '__main__':
    unittest.main()
