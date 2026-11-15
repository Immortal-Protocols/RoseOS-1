"""Creator invites on a native host (docs/APP-BUILDER.md §7.7): Chariot's result fixture, copied byte for byte, held
to what the merged command produces: OS Core's refusal words (§7.4) and the invite token's shape. Synthetic values
only; nothing here reaches a host or creates an invite. The generated command itself is Home's to test (§7.5)."""
import hashlib
import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures' / 'chariot' / 'native-invites-results.json'
REPO = HERE.parents[3]
ADAPTER = REPO / 'workspace' / 'rose-app-runtime-signin' / 'src' / 'invite.rs'
BASE32 = REPO / 'workspace' / 'rose-signin' / 'src' / 'base32.rs'
SESSION = REPO / 'workspace' / 'rose-signin' / 'src' / 'session.rs'
# Chariot's source: Immortal-Protocols/ProtocolsBackend #2564 at 814fd66c, chariot/test/fixtures/native_invites/.
SOURCE_BLOB = '925b85f881b5cc823d709eb0692ebbbe369d1c91'
TOKEN = re.compile(r'[A-Z2-7]{52}')


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def adapter_words() -> set[str]:
    """The words OS Core's InviteError prints (InviteError::word), read from the adapter's source."""
    text = ADAPTER.read_text(encoding='utf-8')
    body = text[text.index('pub fn word(self)'):]
    body = body[:body.index('\n    }\n')]
    return set(re.findall(r'=> "([a-z_]+)"', body))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_it_is_chariots_bytes(self):
        self.assertEqual(git_blob(FIXTURE.read_bytes()), SOURCE_BLOB)

    def test_the_request_names_the_native_host_and_the_deployment(self):
        request = self.doc['request']
        self.assertEqual((request['method'], request['path']), ('POST', '/v1/app-hosts/{recorded_native_host_id}/invites'))
        self.assertEqual(set(request['body']), {'account', 'revision_sha256', 'deployment_sha256'})
        self.assertIn('Idempotency-Key', request['header'])

    def test_the_limits_fit_what_the_command_prints(self):
        limits = self.doc['limits']
        self.assertEqual(limits, {'timeout_seconds': 30, 'stdout_bytes': 53, 'stderr_bytes': 256})
        self.assertEqual(limits['stdout_bytes'], 52 + 1)  # one token line
        # The token is rose-signin's session::mint(): random bytes in unpadded RFC 4648 base32, so its length is
        # read from that source, not assumed.
        base32 = BASE32.read_text(encoding='utf-8')
        self.assertIn('b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"', base32)
        encode = base32[base32.index('pub fn encode('):]
        encode = encode[:encode.index('\n}\n')]
        self.assertNotIn("'='", encode)  # the encoder never pushes a padding character
        session = SESSION.read_text(encoding='utf-8')
        mint = session[session.index('pub fn mint()'):]
        mint = mint[:mint.index('\n}\n')]
        found = re.findall(r'Secret::random\((\d+)\)', mint)
        self.assertEqual(len(found), 1, 'mint() draws one random value')
        self.assertEqual(-(-int(found[0]) * 8 // 5) + 1, limits['stdout_bytes'])  # its characters plus a newline
        longest = max(len(f'rose server: no invite: {w}\n') for w in adapter_words())
        self.assertLess(longest, limits['stderr_bytes'])

    def test_results(self):
        results = {k: v for k, v in self.doc.items() if isinstance(v, dict) and 'json' in v}
        for name, case in results.items():
            with self.subTest(name):
                body = case['json']
                self.assertEqual(set(body), {'outcome', 'reason', 'exit_code', 'replayed', 'token'})
                self.assertIn(body['outcome'], ('issued', 'refused', 'outcome_unknown'))
                if body['outcome'] == 'issued':
                    self.assertEqual((case['status'], body['reason'], body['exit_code']), (200, None, 0))
                    if body['replayed']:
                        self.assertIsNone(body['token'])  # a replay never carries the token
                    else:
                        self.assertRegex(body['token'], TOKEN)
                        self.assertEqual(len(body['token']) + 1, self.doc['limits']['stdout_bytes'])
                else:
                    self.assertIsNone(body['token'])
                if body['outcome'] == 'refused' and body['exit_code'] == 1:
                    self.assertIn(body['reason'], adapter_words())  # the adapter's own word, carried as is
                if body['outcome'] == 'outcome_unknown':
                    self.assertIsNone(body['exit_code'])
        self.assertEqual({r['json']['outcome'] for r in results.values()}, {'issued', 'refused', 'outcome_unknown'})

    def test_the_adapter_words_are_the_agreed_five(self):
        self.assertEqual(adapter_words(),
                         {'invalid_name', 'already_enrolled', 'not_found', 'restore_pending', 'store_unavailable'})


if __name__ == '__main__':
    unittest.main()
