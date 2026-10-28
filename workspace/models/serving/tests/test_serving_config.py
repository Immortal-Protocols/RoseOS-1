"""Muse Glimmer serving draft: pins, runtime flags, Agent Platform container shape and derived capacity stay consistent."""
import importlib.util
import json
from pathlib import Path
import re
import unittest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capacity', HERE/'capacity.py')
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)
CONFIG = capacity.load()
SHA = re.compile(r'^[0-9a-f]{40}$')


def arg(args, flag):
    return args[args.index(flag)+1]


class ServingConfigTest(unittest.TestCase):
    def test_model_pin_is_exact_and_used_by_vllm(self):
        model = CONFIG['model']
        self.assertEqual(model['repo'], 'meta-models/Muse-Glimmer-30B')
        self.assertRegex(model['revision'], SHA)
        args = CONFIG['options'][0]['container_spec']['args']
        self.assertEqual(args[0], model['repo'])
        self.assertEqual(arg(args, '--revision'), model['revision'])
        self.assertEqual(arg(args, '--tokenizer-revision'), model['revision'])

    def test_quantized_alternative_is_the_official_pinned_gguf(self):
        weights = CONFIG['options'][1]['weights']
        self.assertTrue(weights['repo'].startswith('meta-models/'))
        self.assertRegex(weights['revision'], SHA)
        self.assertIn(weights['file'], arg(CONFIG['options'][1]['container_spec']['args'], '--model'))

    def test_vllm_flags_follow_meta_guide(self):
        args = CONFIG['options'][0]['container_spec']['args']
        self.assertEqual(arg(args, '--tool-call-parser'), 'muse_glimmer')
        self.assertEqual(arg(args, '--reasoning-parser'), 'muse_glimmer')
        self.assertEqual(arg(args, '--generation-config'), 'auto')
        self.assertNotIn('--chat-template', args)
        self.assertEqual(arg(args, '--served-model-name'), CONFIG['wire_contract']['model_field'])

    def test_container_matches_machine_and_platform_rules(self):
        for option in CONFIG['options']:
            spec, args = option['container_spec'], option['container_spec']['args']
            port = spec['ports'][0]['containerPort']
            self.assertEqual(arg(args, '--port'), str(port))
            self.assertEqual(spec['startupProbe']['httpGet']['port'], port)
            self.assertNotIn('invokeRoutePrefix', spec)  # exclusive with predictRoute
            self.assertEqual(spec['healthRoute'], '/health')
            self.assertEqual(spec['predictRoute'], '/v1/chat/completions')
            self.assertLessEqual(int(spec['deploymentTimeout'].rstrip('s')), 7200)
            ctx = arg(args, '--max-model-len') if '--max-model-len' in args else arg(args, '--ctx-size')
            self.assertEqual(int(ctx), CONFIG['demo_assumptions']['max_model_len'])
        a100 = CONFIG['options'][0]
        self.assertEqual(int(arg(a100['container_spec']['args'], '--tensor-parallel-size')),
            a100['machine_spec']['acceleratorCount'])

    def test_image_pinned_by_digest_but_not_mirrored(self):
        for option in CONFIG['options']:
            self.assertIsNone(option['container_spec']['imageUri'])  # no Artifact Registry mirror exists
        runtime = CONFIG['options'][0]['runtime']
        digest = re.compile(r'^sha256:[0-9a-f]{64}$')
        self.assertRegex(runtime['image_digest'], digest)
        self.assertRegex(runtime['image_index_digest'], digest)
        self.assertNotEqual(runtime['image_digest'], runtime['image_index_digest'])
        self.assertEqual(runtime['image_platform'], 'linux/amd64')
        self.assertIsNone(CONFIG['options'][1]['runtime']['image_digest'])
        self.assertIn('nothing deployed', CONFIG['status'])

    def test_prompt_envelope_fits_context(self):
        env = CONFIG['prompt_envelope']
        self.assertEqual(env['max_model_len'], CONFIG['demo_assumptions']['max_model_len'])
        self.assertEqual(env['acceptance_prompt_tokens_max']+env['acceptance_max_tokens']+env['slack_tokens'], env['max_model_len'])
        self.assertTrue(any('12,288' in item and '2,048' in item for item in CONFIG['load_test']['accept_if']))

    def test_single_attempt_and_verified_cleanup(self):
        envelope = CONFIG['test_envelope']
        self.assertEqual(envelope['attempts'], 1)
        self.assertIn('any restart, retry or second deploy', envelope['alternatives_requiring_separate_scope'])
        self.assertIn('aiplatform.endpoints.undeploy', CONFIG['cleanup']['operations'])
        self.assertTrue(CONFIG['cleanup']['verify_with'])

    def test_no_credentials_in_config(self):
        text = json.dumps(CONFIG).lower()
        for marker in ('hf_token', 'api_key', 'apikey', 'secret', 'password', 'private_key'):
            self.assertNotIn(marker, text)

    def test_derived_arithmetic(self):
        text = CONFIG['model']['text_config']
        self.assertEqual(text['global_attention_layers']+text['sliding_attention_layers'], text['num_hidden_layers'])
        self.assertAlmostEqual(CONFIG['model']['bf16_parameters']*2/1e9, 59.553, places=2)
        self.assertEqual(capacity.kv_bytes_per_sequence(text, 131072, 1, False), 52*131072*1024)
        self.assertEqual(capacity.kv_bytes_per_sequence(text, 131072, 1, True), (13*131072+39*2048)*1024)
        self.assertEqual(capacity.kv_bytes_per_sequence(text, 1024, 1, True), 52*1024*1024)
        self.assertEqual(capacity.kv_bytes_per_sequence(text, 1024, 4, False), 52*1024*512)

    def test_each_option_has_positive_derived_headroom(self):
        for option in CONFIG['options']:
            self.assertTrue(capacity.fit(CONFIG, option)['fits'], option['id'])

    def test_one_sequence_matches_one_conversation(self):
        a100 = CONFIG['options'][0]
        self.assertEqual(int(arg(a100['container_spec']['args'], '--max-num-seqs')), a100['max_num_seqs'])
        self.assertEqual(a100['max_num_seqs'], CONFIG['demo_assumptions']['concurrent_conversations'])

    def test_conservative_reserve_explains_single_sequence(self):
        a100 = CONFIG['options'][0]
        self.assertTrue(capacity.fit(CONFIG, a100, 16384, 1, 5*2**30)['fits'])
        self.assertFalse(capacity.fit(CONFIG, a100, 16384, 4, 5*2**30)['fits'])

    def test_served_alias_names_the_pin(self):
        model = CONFIG['model']
        self.assertEqual(model['served_alias'], {CONFIG['wire_contract']['model_field']: f"{model['repo']}@{model['revision']}"})
        self.assertEqual(sum(model['weights_fetch']['files'].values()), 49950112952+9603322320+28129897)

    def test_deployment_fits_test_window_and_probe(self):
        hours = CONFIG['test_envelope']['test_hours']
        for option in CONFIG['options']:
            spec = option['container_spec']
            timeout = int(spec['deploymentTimeout'].rstrip('s'))
            self.assertLessEqual(timeout, hours*3600/4)
            probe = spec['startupProbe']
            self.assertLessEqual(probe['periodSeconds']*probe['failureThreshold'], timeout)
        self.assertIn('no separate management fee', CONFIG['test_envelope']['rate_note'])
        self.assertTrue(CONFIG['load_test']['abort_if'] and CONFIG['load_test']['accept_if'])

    def test_bf16_does_not_fit_one_l4(self):
        l4 = dict(CONFIG['options'][0], gpu_memory_bytes=CONFIG['options'][1]['gpu_memory_bytes'],
            machine_spec=dict(CONFIG['options'][0]['machine_spec'], acceleratorCount=1))
        self.assertFalse(capacity.fit(CONFIG, l4)['fits'])


if __name__ == '__main__':
    unittest.main()
