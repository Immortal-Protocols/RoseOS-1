"""Producer-binding regressions only; no APK signing, host access or runtime-verification claim."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('mustang_input_binding_test', ROOT/'scripts/mustang-pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class InputBinding(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT/'os/build-only/mustang-host.json').read_text())
        self.allocation = pipeline.app_input_allocation(self.cfg)

    def test_committed_binding_names_the_actual_frankel_allocation(self):
        self.assertEqual(self.allocation['run'], {'attempt':'1', 'id':'41873084560'})
        self.assertEqual(self.allocation['build'], '2026110403')
        self.assertEqual(dict(self.allocation['apps']), {
            'dev.pixel.voice':364, 'dev.pixel.backgroundcontrol':38, 'dev.pixel.calculator.agent':5})

    def test_mixed_producer_paths_refuse_without_reading_host_files(self):
        for field in ('appsDir', 'policy'):
            cfg = copy.deepcopy(self.cfg)
            cfg[field] = cfg[field].replace('2026110403', '2026110301')
            with self.assertRaisesRegex(ValueError, 'same recorded Frankel producer'):
                pipeline.app_input_allocation(cfg)

    def test_wrong_or_missing_admitted_app_refuses_before_any_output_read(self):
        apps = copy.deepcopy(self.allocation['apps'])
        apps[0][1] -= 1
        for found in (apps, self.allocation['apps'][:-1]):
            with self.assertRaisesRegex(ValueError, 'differ from the bound Frankel allocation'):
                pipeline.app_input_admission(self.cfg, {'apps':found}, Path('/missing-synthetic-bundle'))

    def test_runtime_receipt_is_bound_to_the_producer_and_exact_admitted_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory/'signed-apps').mkdir()
            for package, _ in self.allocation['apps']:
                (directory/'signed-apps'/f'{package}.apk').write_bytes(b'synthetic already-admitted fixture')
                (directory/'signed-apps'/f'{package}.receipt.json').write_text('{}')
            (directory/'background-policy.json').write_text('{}')
            result = pipeline.app_input_admission(self.cfg, {'apps':self.allocation['apps']}, directory)
            self.assertEqual(result['producerSource'], self.allocation['sourceSha'])
            self.assertEqual(result['producerCandidate'], self.allocation['candidateSha'])
            self.assertEqual(dict((a['package'], a['versionCode']) for a in result['apps']), dict(self.allocation['apps']))
            self.assertEqual(result['compiledAppAndPolicyVerification'],
                'omitted per original sprint; this records input admission only')


if __name__ == '__main__':
    unittest.main()
