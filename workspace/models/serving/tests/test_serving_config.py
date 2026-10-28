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

    def test_unresolved_image_blocks_deployment(self):
        for option in CONFIG['options']:
            self.assertIsNone(option['container_spec']['imageUri'])
            self.assertIsNone(option['runtime']['image_digest'])
        self.assertIn('nothing deployed', CONFIG['status'])

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

    def test_bf16_does_not_fit_one_l4(self):
        l4 = dict(CONFIG['options'][0], gpu_memory_bytes=CONFIG['options'][1]['gpu_memory_bytes'],
            machine_spec=dict(CONFIG['options'][0]['machine_spec'], acceleratorCount=1))
        self.assertFalse(capacity.fit(CONFIG, l4)['fits'])


if __name__ == '__main__':
    unittest.main()
