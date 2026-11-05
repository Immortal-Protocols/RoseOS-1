"""APP-INPUTS-EXPORT: scripts/host-app-inputs-export.py and its os-release.yml mode.

Synthetic attempt directories and records in temporary directories only: no builder, lock, tool, network or host."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'scripts/tests'))
import os_release_shape as shape  # noqa: E402
spec = importlib.util.spec_from_file_location('host_app_inputs_export', ROOT/'scripts/host-app-inputs-export.py')
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)
CONFIG = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
PIN = json.loads((ROOT/'os/build-only/mustang-reconcile.json').read_text())['candidateInputs']
SIGNER = PIN['appSignerSha256']
BUILD, CANDIDATE, SOURCE = '2026110499', 'c' * 40, 'd' * 40
VERSIONS = {'dev.pixel.voice': 364, 'dev.pixel.calculator.agent': 5, 'dev.pixel.backgroundcontrol': 38}
WORKFLOW = shape.text()
MODE = 'app-inputs-export'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='app-inputs-export-'))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp/'repo'
        self.base = self.tmp/'rose-actions'/f'frankel-{BUILD}'
        self.apps = self.base/'release'/'signed-apps'
        self.apps.mkdir(parents=True)
        self.record = {'schema': 'roseos.latest-allocation.v1', 'device': 'frankel', 'build': BUILD,
                       'tag': f'frankel-{BUILD}', 'candidateSha': CANDIDATE, 'sourceSha': SOURCE,
                       'apps': [[p, v] for p, v in VERSIONS.items()], 'run': {'id': '41873084560', 'attempt': '1'}}
        self.write_record('os/ota/mustang-frankel-%s-allocation.json' % BUILD, self.record)
        for package in VERSIONS:
            apk = f'APK {package}'.encode()
            (self.apps/f'{package}.apk').write_bytes(apk)
            (self.apps/f'{package}.apk.idsig').write_bytes(b'v4 ' + apk)
            (self.apps/f'{package}.receipt.json').write_text(json.dumps(
                {'schemaVersion': 1, 'apk': f'{package}.apk', 'sha256': sha(apk), 'certificateSha256': [SIGNER],
                 'signatureVerified': True}))
        self.policy = {'schemaVersion': 1, 'sdk': PIN['sdk'], 'prototypeControl': PIN['prototypeControl'],
                       'agent': self.row('dev.pixel.backgroundcontrol'), 'voice': self.row('dev.pixel.voice'),
                       'targets': [self.row('dev.pixel.calculator.agent')]}
        (self.base/'release'/'background-policy.json').write_text(json.dumps(self.policy))
        self.chained = []

    def row(self, package):
        return {'packageName': package, 'versionCode': VERSIONS[package],
                'apkSha256': sha(f'APK {package}'.encode()), 'certificates': [SIGNER.upper()]}

    def write_record(self, relative, record):
        path = self.repo/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record))

    @staticmethod
    def identity(apk, aapt, apksigner, sdk):
        package = Path(apk).name[:-len('.apk')]
        return {'packageName': package, 'versionCode': VERSIONS[package], 'certificates': [SIGNER.upper()]}

    def chain(self, base, candidate, build, device):
        self.chained.append((base, candidate, build, device))

    def export(self, artifact=None, identity=None, config=CONFIG):
        artifact = artifact or self.tmp/'artifact'
        return export.export(config, BUILD, self.base, artifact, {'aapt': 'aapt', 'apksigner': 'apksigner'},
                             root=self.repo, identity=identity or self.identity, chain=self.chain), artifact

    def refused(self, message, **kwargs):
        with self.assertRaisesRegex(export.AdapterFailed, message):
            self.export(**kwargs)


class Export(Fixture):
    def test_the_exact_set_leaves_unchanged_with_a_receipt_and_sums(self):
        receipt, artifact = self.export()
        self.assertEqual(self.chained, [(self.base, CANDIDATE, BUILD, 'frankel')])
        names = sorted(str(p.relative_to(artifact)) for p in artifact.rglob('*') if p.is_file())
        self.assertEqual(names, sorted(['SHA256SUMS', 'app-inputs-export.json', 'background-policy.json'] +
                                       [f'signed-apps/{p}{s}' for p in VERSIONS for s in ('.apk', '.receipt.json', '.apk.idsig')]))
        for name in names:
            if name.startswith('signed-apps/'):
                self.assertEqual((artifact/name).read_bytes(), (self.base/'release'/name).read_bytes())
        self.assertEqual(receipt['producer'], {'device': 'frankel', 'build': BUILD, 'tag': f'frankel-{BUILD}',
                                               'candidateSha': CANDIDATE, 'sourceSha': SOURCE, 'run': self.record['run'],
                                               'allocation': {'path': f'os/ota/mustang-frankel-{BUILD}-allocation.json',
                                                              'sha256': sha((self.repo/f'os/ota/mustang-frankel-{BUILD}-allocation.json').read_bytes())}})
        self.assertEqual({a['package']: a['versionCode'] for a in receipt['apps']}, VERSIONS)
        self.assertEqual(receipt['signerSha256'], SIGNER)
        self.assertEqual(json.loads((artifact/'app-inputs-export.json').read_text()), receipt)
        for line in (artifact/'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ', 1)
            self.assertEqual(sha((artifact/name).read_bytes()), digest, name)
        self.assertEqual(len((artifact/'SHA256SUMS').read_text().splitlines()), len(names) - 1)

    def test_one_committed_record_names_the_producer(self):
        self.write_record('os/latest-beta/allocation.json', self.record)  # an identical copy counts once
        self.export()
        other = dict(self.record, candidateSha='e' * 40)
        self.write_record('os/latest-beta/allocation.json', other)
        self.refused('differ', artifact=self.tmp/'a2')
        (self.repo/'os/latest-beta/allocation.json').unlink()
        (self.repo/f'os/ota/mustang-frankel-{BUILD}-allocation.json').unlink()
        self.refused('no committed', artifact=self.tmp/'a3')

    def test_an_incomplete_record_refuses(self):
        for change in ({'device': 'mustang'}, {'tag': 'frankel-1'}, {'candidateSha': 'x'}, {'sourceSha': None},
                       {'apps': None}, {'run': None}):
            with self.subTest(change=change):
                self.write_record(f'os/ota/mustang-frankel-{BUILD}-allocation.json', dict(self.record, **change))
                self.refused('not a complete Frankel allocation', artifact=self.tmp/f'a-{len(str(change))}')

    def test_the_attempt_chain_must_hold(self):
        def broken(*args):
            raise export.AdapterFailed('the attempt receipt chain is not valid: synthetic')
        with self.assertRaisesRegex(export.AdapterFailed, 'receipt chain'):
            export.export(CONFIG, BUILD, self.base, self.tmp/'artifact', {}, root=self.repo, identity=self.identity,
                          chain=broken)
        self.assertFalse((self.tmp/'artifact').exists())

    def test_the_inventory_is_exactly_the_nine_signed_files(self):
        (self.apps/'extra.apk').write_bytes(b'x')
        self.refused('inventory')
        (self.apps/'extra.apk').unlink()
        (self.apps/'dev.pixel.voice.apk.idsig').unlink()
        self.refused('inventory')
        os.symlink(self.apps/'dev.pixel.voice.apk', self.apps/'dev.pixel.voice.apk.idsig')
        self.refused('link')

    def test_each_receipt_must_match_its_bytes_signer_and_verification(self):
        path = self.apps/'dev.pixel.voice.receipt.json'
        good = json.loads(path.read_text())
        for change in ({'sha256': '0' * 64}, {'certificateSha256': ['0' * 64]}, {'signatureVerified': False},
                       {'schemaVersion': 2}, {'apk': 'other.apk'}):
            with self.subTest(change=change):
                path.write_text(json.dumps(dict(good, **change)))
                self.refused('does not match')
        path.write_text('not json')
        self.refused('not JSON')

    def test_the_tools_must_read_the_allocated_package_version_and_signer(self):
        for wrong in ({'versionCode': 363}, {'certificates': ['0' * 64]}, {'packageName': 'dev.pixel.other'}):
            with self.subTest(wrong=wrong):
                self.refused('signed by the app signer', identity=lambda apk, *a, w=wrong: dict(self.identity(apk, *a), **w))

        def failing(*args):
            raise ValueError('No verified signer')
        self.refused('identity not verified', identity=failing)

    def test_the_policy_must_pin_exactly_these_bytes(self):
        policy = self.base/'release'/'background-policy.json'
        for change in (lambda p: p['voice'].update(apkSha256='0' * 64), lambda p: p['targets'][0].update(versionCode=4),
                       lambda p: p.update(sdk=36), lambda p: p['targets'].append(dict(p['targets'][0], packageName='a.b')),
                       lambda p: p['agent'].update(apkSha256='0' * 64)):
            data = json.loads(json.dumps(self.policy))
            change(data)
            policy.write_text(json.dumps(data))
            self.refused('policy')
        policy.unlink()
        (self.tmp/'elsewhere.json').write_text(json.dumps(self.policy))  # a valid policy, reached only through a link
        os.symlink(self.tmp/'elsewhere.json', policy)
        self.refused('regular file')

    def test_the_allocation_and_contract_must_name_the_same_three_apps(self):
        for apps in ([[p, v] for p, v in VERSIONS.items() if p != 'dev.pixel.calculator.agent'],
                     [[p, v] for p, v in VERSIONS.items()] + [['dev.pixel.extra', 1]]):
            self.write_record(f'os/ota/mustang-frankel-{BUILD}-allocation.json', dict(self.record, apps=apps))
            self.refused('the allocation pins apps', artifact=self.tmp/f'a{len(apps)}')

    def test_a_file_that_changes_while_copied_refuses(self):
        from unittest import mock
        real = shutil.copyfile

        def corrupting(source, target):
            real(source, target)
            if str(target).endswith('dev.pixel.voice.apk'):
                Path(target).write_bytes(b'changed')
        with mock.patch.object(export.shutil, 'copyfile', corrupting):
            self.refused('changed while being copied')

    def test_the_contract_signer_must_be_the_release_signer(self):
        config = json.loads(json.dumps(CONFIG))
        config['identities']['appSignerSha256'] = '0' * 64
        self.refused('signer differs', config=config)


class Main(Fixture):
    def setUp(self):
        super().setUp()
        self.config = json.loads(json.dumps(CONFIG))
        self.config['host'].update(lockPath=str(self.tmp/'lock'), sourceRoot=str(self.tmp/'src'))
        self.config['host']['tools'].update(aapt='/bin/true', apksigner='/bin/true')
        self.config_path = self.tmp/'frankel.json'
        self.config_path.write_text(json.dumps(self.config))
        self.env = {'BUILD_NUMBER': BUILD, 'ROSE_ATTEMPTS': str(self.tmp/'rose-actions')}

    def test_refusals_before_any_read_or_write(self):
        for env, message in (({'BUILD_NUMBER': '123'}, 'ten-digit'), ({}, 'ten-digit')):
            with self.assertRaisesRegex(export.AdapterFailed, message):
                export.main(self.tmp/'out', self.config_path, dict(self.env, **env) if env else {'ROSE_ATTEMPTS': self.env['ROSE_ATTEMPTS']})
        (self.tmp/'exists').mkdir()
        with self.assertRaisesRegex(export.AdapterFailed, 'already exists'):
            export.main(self.tmp/'exists', self.config_path, self.env)
        with self.assertRaisesRegex(export.AdapterFailed, 'outside the original attempt'):
            export.main(self.base/'out', self.config_path, self.env)

    def test_a_busy_lock_refuses_and_writes_nothing(self):
        import fcntl
        with open(self.tmp/'lock', 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(export.AdapterFailed, 'lock is held'):
                export.main(self.tmp/'out', self.config_path, self.env)
        self.assertFalse((self.tmp/'out').exists())

    def test_a_refusal_after_the_lock_leaves_no_partial_artifact(self):
        # The real chain check refuses this synthetic attempt (it has no stage receipts): nothing is left to upload.
        with self.assertRaises(export.AdapterFailed):
            export.main(self.tmp/'out', self.config_path, self.env)
        self.assertFalse((self.tmp/'out').exists())

    def test_a_refusal_mid_copy_removes_the_partial_artifact(self):
        from unittest import mock

        def half(config, build, base, artifact, tools):
            (artifact/'signed-apps').mkdir(parents=True)
            (artifact/'signed-apps'/'dev.pixel.voice.apk').write_bytes(b'partial')
            raise export.AdapterFailed('dev.pixel.voice.apk changed while being copied')
        with mock.patch.object(export, 'export', half):
            with self.assertRaisesRegex(export.AdapterFailed, 'changed'):
                export.main(self.tmp/'out', self.config_path, self.env)
        self.assertFalse((self.tmp/'out').exists())

    def test_missing_configuration_names_the_keys(self):
        self.config['host']['tools']['aapt'] = ''
        self.config['host']['lockPath'] = ''
        self.config_path.write_text(json.dumps(self.config))
        with self.assertRaises(export.AdapterSetupMissing) as caught:
            export.main(self.tmp/'out', self.config_path, self.env)
        self.assertEqual(sorted(caught.exception.missing), ['host.lockPath', 'host.tools.aapt'])


class Workflow(unittest.TestCase):
    def setUp(self):
        self.jobs = shape.jobs(WORKFLOW)

    def test_the_mode_is_manual_owned_bare_and_waited_for(self):
        self.assertIn(MODE, shape.modes())
        self.assertIn(MODE, shape.owned_modes())          # it stops the builder only if its own controller started it
        self.assertIn(MODE, shape.main_check_exempt_modes())  # no candidate: the committed record names the producer
        self.assertIn(MODE, shape.cleanup_needs())

    def test_the_guard_admits_only_a_build_number(self):
        guard = self.jobs['guard']
        branch = guard.split(f'elif [[ "$MODE" == {MODE} ]]; then\n', 1)[1].split('\n          elif ', 1)[0]
        self.assertIn('[[ -z "$CANDIDATE_SHA" && "$BUILD_NUMBER" =~ ^[1-9][0-9]{9}$ && -z "$BUILD_TIMESTAMP" && -z "$APPS" ]]', branch)
        self.assertIn('"$APP_PUBLISH" == false', branch)
        self.assertIn('"$ROSE_DEVICE" == frankel', branch)
        self.assertIn('"$PROMOTE" == false && "$RESUME_STAGE" == none', branch)

    def test_the_job_reads_and_uploads_only_privately(self):
        job = self.jobs[MODE].split('\n\n  #', 1)[0]  # this job only, not the next job's leading comment
        self.assertIn(f"    needs: controller\n    if: inputs.mode == '{MODE}'\n    runs-on: [self-hosted, roseos-frankel-builder]\n", job)
        self.assertIn('    permissions:\n      contents: read\n    steps:', job)
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'create-github-app-token', 'PIXEL_OTA', 'publish'):
            self.assertNotIn(absent, job)
        runs = re.findall(r'^      (?:- |  )run: (.+)$', job, re.M)
        self.assertEqual(runs, ['python3 scripts/host-app-inputs-export.py --check', '|',
                                'python3 scripts/host-app-inputs-export.py --artifact-dir "$ROSE_BASE/artifact"',
                                'test -z "${ROSE_BASE:-}" || rm -rf "$ROSE_BASE"'])
        self.assertIn('base="$HOME/rose-actions/app-inputs-export-$GITHUB_RUN_ID"', job)
        self.assertIn('name: app-inputs-frankel-${{ inputs.build_number }}-${{ github.run_id }}-${{ github.run_attempt }}', job)
        self.assertIn('retention-days: 14', job)

    def test_reclaim_knows_its_scratch(self):
        policy = json.loads((ROOT/'os/release-pipeline/storage/builder-reclaim.json').read_text())
        scratch = next(c for c in policy['classes'] if c['name'] == 'run-scratch')
        self.assertTrue(re.fullmatch(scratch['entry'], 'app-inputs-export-41900000000'))


if __name__ == '__main__':
    unittest.main()
