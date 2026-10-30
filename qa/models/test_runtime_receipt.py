#!/usr/bin/env python3
"""SYNTHETIC EVALUATOR TESTS for runtime_receipt.py check logic.

These write fresh synthetic receipt files (tiny fake weights, a synthetic pin and
synthetic captures) and judge them. They are tests of the evaluator, not runtime
receipts: no binary, server, model or network is involved.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime_receipt as rr  # noqa: E402

SYNTH_FILES = {'part-1.bin': b'synthetic weights one', 'part-2.bin': b'synthetic weights two!', 'tok.json': b'{}'}
PIN = {'repo': 'qa-synthetic/model', 'revision': 'a' * 40, 'alias': 'qa-alias', 'image_digest': 'sha256:' + 'b' * 64,
       'files': {k: len(v) for k, v in SYNTH_FILES.items()}}
REPLY1 = 'OK'


def write(d, name, value):
    with open(os.path.join(d, name), 'w') as f:
        f.write(value if isinstance(value, str) else json.dumps(value))


def chat_body(messages):
    return {'model': PIN['alias'], 'messages': messages, 'max_tokens': rr.MAX_TOKENS, 'stream': False}


def capture_lines(req2_messages=None, reply1=REPLY1, count=2, max_tokens=rr.MAX_TOKENS):
    sys_msg = {'role': 'system', 'content': rr.SYSTEM_PROMPT}
    m1 = [sys_msg, {'role': 'user', 'content': rr.TURNS[0]}]
    m2 = req2_messages if req2_messages is not None else m1 + [
        {'role': 'assistant', 'content': reply1}, {'role': 'user', 'content': rr.TURNS[1]}]
    bodies = [chat_body(m1), chat_body(m2)]
    for b in bodies:
        b['max_tokens'] = max_tokens
    resp = [{'choices': [{'message': {'role': 'assistant', 'content': reply1}}]},
            {'choices': [{'message': {'role': 'assistant', 'content': rr.CODE_WORD}}]}]
    lines = [{'schema': rr.CAPTURE_SCHEMA, 'seq': i + 1, 'path': '/v1/chat/completions', 'request': bodies[i],
              'response_status': 200, 'response': resp[i]} for i in range(2)][:count]
    return ''.join(json.dumps(x) + '\n' for x in lines)


def good_dir(tier='runtime'):
    d = tempfile.mkdtemp(prefix='models-qa-synthetic-receipt-')
    write(d, 'meta.json', {'tier': tier})
    write(d, 'instance.json', {'instance_id': rr.EXPECTED_INSTANCE, 'gpu': 'SYNTHETIC nvidia-h100-80gb'})
    write(d, 'server.json', {'image_digest': PIN['image_digest'], 'startup_log_excerpt': 'SYNTHETIC',
                             'args': [PIN['repo'], '--revision', PIN['revision'], '--served-model-name', PIN['alias']]})
    write(d, 'health.json', {'status': 200})
    write(d, 'models.json', {'status': 200, 'body': json.dumps({'data': [{'id': PIN['alias'], 'root': 'x'}]})})
    write(d, 'client.json', {'bin_sha256': 'c' * 64, 'source_rev': 'f' * 40, 'exit': 0})
    turns = [{'type': 'turn', 'index': 1, 'outcome': 'ok', 'served_model': PIN['alias'], 'prompt_tokens': 30},
             {'type': 'turn', 'index': 2, 'outcome': 'ok', 'served_model': PIN['alias'], 'prompt_tokens': 60}]
    write(d, 'transcript.jsonl', ''.join(json.dumps(r) + '\n' for r in [{'type': 'run'}, *turns, {'type': 'summary'}]))
    write(d, 'stdout.txt', f'assistant> {REPLY1}\nassistant> {rr.CODE_WORD}\n')
    write(d, 'capture.jsonl', capture_lines())
    wdir = os.path.join(d, 'weights-src')
    os.mkdir(wdir)
    for name, data in SYNTH_FILES.items():
        with open(os.path.join(wdir, name), 'wb') as f:
            f.write(data)
    observed = rr.observe_weights(wdir)
    shutil.rmtree(wdir)
    write(d, 'weights_observed.json', observed)
    write(d, 'expected_manifest.json', {
        'schema': rr.MANIFEST_SCHEMA, 'repo': PIN['repo'], 'revision': PIN['revision'],
        'provenance': {'source': 'SYNTHETIC independent manifest for evaluator tests', 'retrieved_utc': 'synthetic'},
        'files': {k: {'bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()} for k, v in SYNTH_FILES.items()}})
    return d


def judge(d):
    with contextlib.redirect_stdout(io.StringIO()):
        code = rr.check_dir(d, pin=PIN)
    with open(os.path.join(d, 'verdict.json')) as f:
        return code, json.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = good_dir()

    def tearDown(self):
        shutil.rmtree(self.d)

    def state(self, prefix):
        _, v = judge(self.d)
        return next((r['state'], r['observed']) for r in v['results'] if r['id'].startswith(prefix))

    def edit_json(self, name, fn):
        with open(os.path.join(self.d, name)) as f:
            value = json.load(f)
        fn(value)
        write(self.d, name, value)


class Verdicts(Base):
    def test_complete_synthetic_runtime_receipt_passes(self):
        code, v = judge(self.d)
        self.assertEqual(code, 0, v['results'])
        self.assertTrue(v['verdict'].startswith('ACTUAL-MODEL TWO-TURN CHAT: PASS'))

    def test_fixture_tier_never_claims_actual_model(self):
        write(self.d, 'meta.json', {'tier': 'fixture'})
        code, v = judge(self.d)
        self.assertEqual(code, 0)
        self.assertNotIn('ACTUAL-MODEL', v['verdict'])

    def test_missing_instance_and_server_are_unavailable(self):
        os.remove(os.path.join(self.d, 'instance.json'))
        os.remove(os.path.join(self.d, 'server.json'))
        self.assertEqual(self.state('R1')[0], 'UNAVAILABLE')
        self.assertEqual(self.state('R2 ')[0], 'UNAVAILABLE')

    def test_secret_like_text_fails_r7(self):
        write(self.d, 'stderr.txt', 'Authorization: Bearer abcdefghijklmnop')
        self.assertEqual(self.state('R7')[0], 'FAIL')

    def test_recall_is_observed_not_gated(self):
        write(self.d, 'stdout.txt', f'assistant> {REPLY1}\nassistant> I do not remember\n')
        code, v = judge(self.d)
        self.assertEqual(code, 0)
        self.assertFalse(v['observed']['reply_2_recalls_code_word'])


class History(Base):
    def test_missing_capture_is_inconclusive_despite_large_token_growth(self):
        os.remove(os.path.join(self.d, 'capture.jsonl'))
        self.edit_json_lines_tokens(30, 900)
        state, _ = self.state('R6')
        self.assertEqual(state, 'INCONCLUSIVE')
        code, v = judge(self.d)
        self.assertEqual(v['observed']['prompt_token_delta'], 870)
        self.assertNotIn('ACTUAL-MODEL', v['verdict'])

    def edit_json_lines_tokens(self, a, b):
        path = os.path.join(self.d, 'transcript.jsonl')
        text = open(path).read().replace('"prompt_tokens": 30', f'"prompt_tokens": {a}').replace(
            '"prompt_tokens": 60', f'"prompt_tokens": {b}')
        write(self.d, 'transcript.jsonl', text)

    def test_dropped_prior_assistant_fails(self):
        sys_msg = {'role': 'system', 'content': rr.SYSTEM_PROMPT}
        write(self.d, 'capture.jsonl', capture_lines([sys_msg, {'role': 'user', 'content': rr.TURNS[0]},
                                                      {'role': 'user', 'content': rr.TURNS[1]}]))
        self.edit_json_lines_tokens(30, 900)
        self.assertEqual(self.state('R6')[0], 'FAIL')

    def test_reordered_history_fails(self):
        sys_msg = {'role': 'system', 'content': rr.SYSTEM_PROMPT}
        write(self.d, 'capture.jsonl', capture_lines([sys_msg, {'role': 'assistant', 'content': REPLY1},
                                                      {'role': 'user', 'content': rr.TURNS[0]},
                                                      {'role': 'user', 'content': rr.TURNS[1]}]))
        self.assertEqual(self.state('R6')[0], 'FAIL')

    def test_mutated_prior_assistant_content_fails(self):
        sys_msg = {'role': 'system', 'content': rr.SYSTEM_PROMPT}
        write(self.d, 'capture.jsonl', capture_lines([sys_msg, {'role': 'user', 'content': rr.TURNS[0]},
                                                      {'role': 'assistant', 'content': 'OK!'},
                                                      {'role': 'user', 'content': rr.TURNS[1]}]))
        self.edit_json_lines_tokens(30, 900)
        self.assertEqual(self.state('R6')[0], 'FAIL')

    def test_missing_system_control_or_wrong_max_tokens_fails(self):
        write(self.d, 'capture.jsonl', capture_lines(max_tokens=1024))
        self.assertEqual(self.state('R6')[0], 'FAIL')

    def test_extra_hidden_request_or_single_request_fails(self):
        extra = capture_lines() + capture_lines(count=1)
        write(self.d, 'capture.jsonl', extra)
        self.assertEqual(self.state('R6')[0], 'FAIL')
        write(self.d, 'capture.jsonl', capture_lines(count=1))
        self.assertEqual(self.state('R6')[0], 'FAIL')

    def test_malformed_capture_fails(self):
        write(self.d, 'capture.jsonl', '{"schema": "other"}\n')
        self.assertEqual(self.state('R6')[0], 'FAIL')
        write(self.d, 'capture.jsonl', 'not json\n')
        self.assertEqual(self.state('R6')[0], 'FAIL')

    def test_capture_contradicting_client_output_fails(self):
        write(self.d, 'stdout.txt', f'assistant> something else\nassistant> {rr.CODE_WORD}\n')
        self.assertEqual(self.state('R6')[0], 'FAIL')

    def test_missing_usage_is_not_a_failure_when_capture_proves_history(self):
        path = os.path.join(self.d, 'transcript.jsonl')
        write(self.d, 'transcript.jsonl', open(path).read().replace(', "prompt_tokens": 30', '').replace(
            ', "prompt_tokens": 60', ''))
        self.assertEqual(self.state('R6')[0], 'PASS')
        _, v = judge(self.d)
        self.assertIsNone(v['observed']['prompt_token_delta'])


class Weights(Base):
    def test_matching_hashes_against_independent_manifest_pass(self):
        self.assertEqual(self.state('R2v')[0], 'PASS')

    def test_same_size_changed_content_fails(self):
        self.edit_json('weights_observed.json', lambda v: v['files']['part-1.bin'].update(
            sha256=hashlib.sha256(b'synthetic weights ONE').hexdigest()))
        self.assertEqual(self.state('R2v')[0], 'FAIL')

    def test_wrong_manifest_revision_fails(self):
        self.edit_json('expected_manifest.json', lambda v: v.update(revision='0' * 40))
        self.assertEqual(self.state('R2v')[0], 'FAIL')

    def test_size_only_evidence_is_inconclusive(self):
        self.edit_json('weights_observed.json', lambda v: [f.pop('sha256') for f in v['files'].values()])
        self.assertEqual(self.state('R2v')[0], 'INCONCLUSIVE')

    def test_missing_manifest_or_provenance_is_inconclusive(self):
        self.edit_json('expected_manifest.json', lambda v: v.pop('provenance'))
        self.assertEqual(self.state('R2v')[0], 'INCONCLUSIVE')
        os.remove(os.path.join(self.d, 'expected_manifest.json'))
        self.assertEqual(self.state('R2v')[0], 'INCONCLUSIVE')

    def test_manifest_copied_from_observed_or_self_verified_flag_is_inconclusive(self):
        self.edit_json('expected_manifest.json', lambda v: v['provenance'].update(copied_from_observed=True))
        self.assertEqual(self.state('R2v')[0], 'INCONCLUSIVE')
        self.edit_json('expected_manifest.json', lambda v: v['provenance'].update(
            copied_from_observed=False, source='weights_observed.json'))
        self.assertEqual(self.state('R2v')[0], 'INCONCLUSIVE')

    def test_bare_verified_flag_does_not_establish_identity(self):
        os.remove(os.path.join(self.d, 'expected_manifest.json'))
        self.edit_json('weights_observed.json', lambda v: v.update(verified=True))
        self.edit_json('server.json', lambda v: v.update(weights_verified=True))
        self.assertEqual(self.state('R2v')[0], 'INCONCLUSIVE')

    def test_missing_or_extra_files_fail(self):
        self.edit_json('weights_observed.json', lambda v: v['files'].pop('tok.json'))
        self.assertEqual(self.state('R2v')[0], 'FAIL')
        shutil.rmtree(self.d)
        self.d = good_dir()
        self.edit_json('weights_observed.json', lambda v: v['files'].update(
            {'extra.bin': {'bytes': 1, 'sha256': 'd' * 64}}))
        self.assertEqual(self.state('R2v')[0], 'FAIL')

    def test_manifest_omitting_pinned_file_fails(self):
        def drop(v):
            v['files'].pop('part-2.bin')
        self.edit_json('expected_manifest.json', drop)
        self.edit_json('weights_observed.json', drop)
        self.assertEqual(self.state('R2v')[0], 'FAIL')

    def test_declared_weights_alone_do_not_pass_r2v(self):
        os.remove(os.path.join(self.d, 'weights_observed.json'))
        self.edit_json('server.json', lambda v: v.update(weights={'repo': PIN['repo'], 'revision': PIN['revision']}))
        self.assertEqual(self.state('R2 ')[0], 'PASS')
        self.assertEqual(self.state('R2v')[0], 'INCONCLUSIVE')
        code, v = judge(self.d)
        self.assertEqual(code, 1)
        self.assertNotIn('ACTUAL-MODEL', v['verdict'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
