"""BLAZER-CANDIDATE-BUNDLE: scripts/blazer-candidate-bundle.py assembles exactly the bundle Flavors' admit consumes,
from actual signed bytes only. Synthetic text "APKs", fake aapt/apksigner reporting what the bytes say and a fake git
object view (the blazer_integration test fixtures); no key, network, host or signing."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'scripts/tests'))
import blazer_integration as bi  # noqa: E402
import test_blazer_integration as fixture  # noqa: E402

spec = importlib.util.spec_from_file_location('blazer_candidate_bundle', ROOT/'scripts/blazer-candidate-bundle.py')
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)


class Assemble(fixture.Fixture):
    def setUp(self):
        super().setUp()
        self.apps = self.tmp/'signed'
        self.apps.mkdir()
        for name in os.listdir(self.bundle/'signed-apps'):
            shutil.copyfile(self.bundle/'signed-apps'/name, self.apps/name)
        self.policy = self.tmp/'policy.json'
        shutil.copyfile(self.bundle/'background-policy.json', self.policy)
        self.out = self.tmp/'assembled'

    def assemble(self, **change):
        args = dict(apps_dir=self.apps, policy_path=self.policy, build=fixture.BUILD, product=fixture.PRODUCT,
                    tools=self.tools, out=self.out, git_read=self.git)
        args.update(change)
        return producer.assemble(**args)

    def test_the_assembled_bundle_is_admitted_and_its_values_come_from_the_bytes(self):
        candidate = self.assemble()
        self.assertEqual(candidate, json.loads((self.out/'candidate.json').read_text()))
        self.assertEqual(candidate['recipe'], fixture.HEAD)
        self.assertEqual(dict(map(tuple, candidate['apps'])), fixture.VERSIONS)
        self.bundle = self.out
        record, inputs = self.admit(out=self.tmp/'inputs')
        self.assertEqual(record['candidate']['build'], fixture.BUILD)
        self.assertEqual(sorted(p.name for p in self.out.rglob('*')), sorted(
            ['background-policy.json', 'candidate.json', 'signed-apps'] +
            [f'{p}{s}' for p in fixture.VERSIONS for s in ('.apk', '.receipt.json')]))

    def test_the_version_is_read_from_the_apk_never_supplied(self):
        package = 'dev.pixel.voice'
        (self.apps/f'{package}.apk').write_bytes(self.apk(package, version=313))
        with self.assertRaisesRegex(producer.Refused, 'does not match'):
            self.assemble()  # the receipt no longer matches the bytes
        self.assertFalse(self.out.exists())

    def test_refusals_leave_nothing_behind(self):
        cases = {
            'receipt signer': lambda: self.edit_receipt('dev.pixel.voice', certificateSha256=['0' * 64]),
            'unverified': lambda: self.edit_receipt('dev.pixel.voice', signatureVerified=False),
            'extra file': lambda: (self.apps/'notes.txt').write_text('x'),
            'missing receipt': lambda: (self.apps/'dev.pixel.voice.receipt.json').unlink(),
            'policy version': lambda: self.edit_policy('voice', versionCode=999),
            'policy bytes': lambda: self.edit_policy('targets', apkSha256='0' * 64),
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                self.setUp()
                change()
                with self.assertRaises(producer.Refused):
                    self.assemble()
                self.assertFalse(self.out.exists())

    def test_wrong_signer_inside_the_apk_refuses(self):
        package = 'dev.pixel.calculator.agent'
        data = self.apk(package, signer='1' * 64)
        (self.apps/f'{package}.apk').write_bytes(data)
        self.edit_receipt(package, sha256=fixture.sha(data))
        with self.assertRaisesRegex(producer.Refused, 'existing app signer'):
            self.assemble()

    def test_inputs_identity_and_output_refusals(self):
        for change, word in (({'build': '123'}, 'ten-digit'), ({'product': 'abc'}, 'full commit'),
                             ({'product': 'd' * 40}, 'pinned Android manifest'),
                             ({'git_read': lambda *a: None}, 'recipe'),
                             ({'tools': {'aapt': 'relative', 'apksigner': self.tools['apksigner']}}, 'absolute regular file')):
            with self.subTest(change=list(change)):
                with self.assertRaisesRegex(producer.Refused, word):
                    self.assemble(**change)
        self.out.mkdir()
        with self.assertRaisesRegex(producer.Refused, 'exists'):
            self.assemble()
        self.out.rmdir()
        link = self.tmp/'apps-link'
        link.symlink_to(self.apps)
        with self.assertRaisesRegex(producer.Refused, 'not a link'):
            self.assemble(apps_dir=link)
        (self.apps/'dev.pixel.voice.apk').unlink()
        (self.apps/'dev.pixel.voice.apk').symlink_to(self.bundle/'signed-apps/dev.pixel.voice.apk')
        with self.assertRaisesRegex(producer.Refused, 'not a link'):
            self.assemble()

    def test_cli_refuses_with_exit_1_and_never_signs(self):
        text = (ROOT/'scripts/blazer-candidate-bundle.py').read_text()
        for absent in ('sign-apk', 'gcloud', 'secret', 'urlopen', 'requests'):
            self.assertNotIn(absent, text.split('"""', 2)[2])
        argv = ['assemble', '--apps-dir', str(self.apps), '--policy', str(self.policy), '--build', '1',
                '--product', fixture.PRODUCT, '--aapt', self.tools['aapt'], '--apksigner', self.tools['apksigner'],
                '--out', str(self.out)]
        self.assertEqual(producer.main(argv), 1)

    def edit_receipt(self, package, **change):
        path = self.apps/f'{package}.receipt.json'
        path.write_text(json.dumps({**json.loads(path.read_text()), **change}))

    def edit_policy(self, role, **change):
        policy = json.loads(self.policy.read_text())
        if role == 'targets':
            policy['targets'][0].update(change)
        else:
            policy[role].update(change)
        self.policy.write_text(json.dumps(policy))


if __name__ == '__main__':
    unittest.main()
