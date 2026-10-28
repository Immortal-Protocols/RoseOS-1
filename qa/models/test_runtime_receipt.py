#!/usr/bin/env python3
"""Self-tests for runtime_receipt.py check logic on synthetic receipt directories.

No binary, server or network: each test writes FIXTURE receipt files and judges them.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime_receipt as rr  # noqa: E402

P = rr.pins()


def write(d, name, value):
    with open(os.path.join(d, name), 'w') as f:
        f.write(value if isinstance(value, str) else json.dumps(value))


def good_dir(tier='runtime'):
    d = tempfile.mkdtemp(prefix='models-qa-receipt-')
    write(d, 'meta.json', {'tier': tier})
    write(d, 'instance.json', {'instance_id': rr.EXPECTED_INSTANCE, 'gpu': 'FIXTURE nvidia-h100-80gb'})
    write(d, 'server.json', {'image_digest': P['image_digest'], 'startup_log_excerpt': 'FIXTURE',
                             'args': [P['repo'], '--revision', P['revision'], '--served-model-name', P['alias']]})
    write(d, 'health.json', {'status': 200})
    write(d, 'models.json', {'status': 200, 'body': json.dumps({'data': [{'id': P['alias'], 'root': 'x'}]})})
    write(d, 'client.json', {'bin_sha256': 'ab' * 32, 'source_rev': 'f' * 40, 'exit': 0})
    turns = [{'type': 'turn', 'index': 1, 'outcome': 'ok', 'served_model': P['alias'], 'prompt_tokens': 20},
             {'type': 'turn', 'index': 2, 'outcome': 'ok', 'served_model': P['alias'], 'prompt_tokens': 40}]
    write(d, 'transcript.jsonl', '\n'.join(json.dumps(r) for r in [{'type': 'run'}, *turns, {'type': 'summary'}]) + '\n')
    write(d, 'stdout.txt', f'assistant> OK\nassistant> {rr.CODE_WORD}\n')
    return d


def judge(d):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = rr.check_dir(d)
    with open(os.path.join(d, 'verdict.json')) as f:
        return code, json.load(f), out.getvalue()


class Check(unittest.TestCase):
    def setUp(self):
        self.d = good_dir()

    def tearDown(self):
        shutil.rmtree(self.d)

    def state(self, verdict, prefix):
        return next(r['state'] for r in verdict['results'] if r['id'].startswith(prefix))

    def test_complete_runtime_receipt_is_actual_model_pass(self):
        code, v, _ = judge(self.d)
        self.assertEqual(code, 0)
        self.assertEqual(v['verdict'], 'ACTUAL-MODEL TWO-TURN CHAT: PASS')
        self.assertTrue(v['observed']['reply_2_recalls_code_word'])

    def test_fixture_tier_never_claims_actual_model(self):
        write(self.d, 'meta.json', {'tier': 'fixture'})
        _, v, _ = judge(self.d)
        self.assertNotIn('ACTUAL-MODEL', v['verdict'])

    def test_missing_receipts_are_unavailable_not_pass(self):
        os.remove(os.path.join(self.d, 'instance.json'))
        os.remove(os.path.join(self.d, 'server.json'))
        code, v, _ = judge(self.d)
        self.assertEqual((self.state(v, 'R1'), self.state(v, 'R2'), code), ('UNAVAILABLE', 'UNAVAILABLE', 1))

    def test_wrong_instance_or_revision_fails(self):
        write(self.d, 'instance.json', {'instance_id': '1', 'gpu': 'H100'})
        write(self.d, 'server.json', {'image_digest': P['image_digest'], 'startup_log_excerpt': 'x',
                                      'args': [P['repo'], '--revision', '0' * 40, '--served-model-name', P['alias']]})
        _, v, _ = judge(self.d)
        self.assertEqual((self.state(v, 'R1'), self.state(v, 'R2'), v['failure_class']), ('FAIL', 'FAIL', 'model identity'))

    def test_history_delta_below_threshold_fails(self):
        lines = open(os.path.join(self.d, 'transcript.jsonl')).read().replace('"prompt_tokens": 40', '"prompt_tokens": 22')
        write(self.d, 'transcript.jsonl', lines)
        _, v, _ = judge(self.d)
        self.assertEqual(self.state(v, 'R6'), 'FAIL')

    def test_secret_like_text_fails_r7(self):
        write(self.d, 'stderr.txt', 'Authorization: Bearer abcdefghijklmnop')
        _, v, _ = judge(self.d)
        self.assertEqual(self.state(v, 'R7'), 'FAIL')

    def test_recall_is_observed_not_gated(self):
        write(self.d, 'stdout.txt', 'assistant> OK\nassistant> I do not remember\n')
        code, v, _ = judge(self.d)
        self.assertEqual(code, 0)
        self.assertFalse(v['observed']['reply_2_recalls_code_word'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
