"""FOLD-FACTORY-PACKAGE: the manual rango-factory-package mode of .github/workflows/os-release.yml and
scripts/rango-factory-package-wiring.py.

The workflow's own shell and the wiring run with stand-ins only (no host, lock, generation, hashing of real inputs,
token or network): the guard, the hosted pin check, the exact helper and inspection invocations, the bounded steps,
the cleanup report and the upload allowlist, and the owned-start controller/cleanup route. The helpers
(scripts/host-factory-package.py, scripts/factory-package.py) are Signing-owned and never run here."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT/'.github/workflows/os-release.yml').read_text()
MODE = 'rango-factory-package'
WIRING = ROOT/'scripts/rango-factory-package-wiring.py'
PRODUCTION = '/home/pixelbuild/grapheneos-2026100600'
LOCK = '/home/pixelbuild/frankel-build.lock'
TF = f'{PRODUCTION}/releases/2026103001/release-rango-2026103001/rango-target_files.zip'
OTA = f'{PRODUCTION}/releases/2026103001/rango-otatools.zip'

spec = importlib.util.spec_from_file_location('rango_factory_package_wiring', WIRING)
wiring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wiring)


def jobs():
    body = WORKFLOW.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)', body))


def step(job, name):
    match = re.search(r'(?ms)^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |\Z)', job)
    assert match, name
    return match[1]


def script(step_text):
    block = re.search(r'(?ms)^        run: \|\n(.*)', step_text)
    if block:
        lines = []
        for line in block[1].splitlines():
            if line.strip() and not line.startswith(' ' * 10):
                break
            lines.append(line[10:])
        return '\n'.join(lines) + '\n'
    return re.search(r'(?m)^        run: (.+)$', step_text)[1] + '\n'


def code(text):
    return '\n'.join(line for line in text.splitlines() if not line.strip().startswith('#'))


def wire(*argv):
    return subprocess.run([sys.executable, str(WIRING), *argv], capture_output=True, text=True, cwd=ROOT)


class Pins(unittest.TestCase):
    def test_literal_pins_are_the_dependency_answers_and_agree_with_committed_sources(self):
        pins = wiring.PINS
        self.assertEqual(pins['targetFiles'], {'path': TF, 'bytes': 4927914252,
            'sha256': '73db76e500bcb71757da6dbc43337067af23395a38eaac940b9ae7d5aca0baf0'})
        self.assertEqual(pins['otatools'], {'path': OTA,
            'sha256': '22d62464ff3fa74fe3fa2b4c118bba116bc3a3aa504feddcdcf38fc3f3cbb664'})  # size not established
        self.assertEqual(pins['releaseScript'], {'path': f'{PRODUCTION}/script/generate-release.sh',
            'sha256': '01f7847ecdc6600bd2e3ffebe45cf79cee4fead1d6b61686c4d3c70e9c548311'})
        self.assertEqual(pins['avbPkmd'], {'path': f'{PRODUCTION}/releases/2026103001/public/avb_pkmd.bin',
            'sha256': 'aee947942b80c74dab9956209b7f7836a1824d9aeb1e9dd782dc157c63fd8f27'})
        self.assertEqual(pins['fastbootSha256'], 'f18fdc8bf467c4bb361ffc62db5f50ca981aa5f37ffb6cb9fd4ac9ff3c4e47a9')
        result = wire('check')
        self.assertEqual((result.returncode, result.stdout),
            (0, 'package_seconds=5400\nobserve_seconds=900\nclassify_seconds=7200\nstage_seconds=900\njob_minutes=297\n'))

    def test_drift_from_any_committed_source_refuses(self):
        for name, change in (('otatools', {'sha256': 'a' * 64}), ('releaseScript', {'sha256': 'b' * 64}),
                             ('avbPkmd', {'sha256': 'c' * 64}), ('targetFiles', {'sha256': 'XYZ'}),
                             ('targetFiles', {'path': '/home/pixelbuild/elsewhere/tf.zip'}),
                             ('otatools', {'path': f'{PRODUCTION}/releases/../keys/x.zip'}), ('targetFiles', {'bytes': 0})):
            pins = json.loads(json.dumps(wiring.PINS))
            pins[name].update(change)
            with self.subTest(name=name, change=change), self.assertRaises(wiring.Invalid):
                wiring.check(pins=pins)
        with self.assertRaises(wiring.Invalid):
            wiring.check(pins=dict(wiring.PINS, fastbootSha256='0' * 63))

    def test_bounds_cover_every_step_and_are_not_a_measurement(self):
        bounds, minutes = wiring.check()
        self.assertEqual(bounds, {'packageSeconds': 5400, 'observeSeconds': 900, 'classifySeconds': 7200, 'stageSeconds': 900})
        self.assertEqual(wiring.INSTALLER, {'capBytes': 8 << 30, 'retentionDays': 3, 'uploadMinutes': 45})
        self.assertEqual(minutes, 297)  # the approved total including the private export (900 s + 30 s staging, 45-min upload)
        self.assertLessEqual(bounds['stageSeconds'] + 30 + wiring.INSTALLER['uploadMinutes'] * 60, 3630)
        self.assertGreater(bounds['packageSeconds'], 3600 + 10)  # beyond the helper's 60-min generation and kill grace
        self.assertGreaterEqual(bounds['classifySeconds'], 2 * 3600)  # two tool calls, each 3600 s in the helper
        self.assertGreaterEqual(minutes * 60, sum(bounds.values()) + 4 * 30 + wiring.INSTALLER['uploadMinutes'] * 60)
        self.assertIn('not measurements', wiring.__doc__)
        self.assertIn('not a usage cap', wiring.__doc__)


class Invocations(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        self.root = wiring.task_root('41500000000', '1', parent=self.parent)

    def test_task_root_is_per_run_under_the_proposed_parent(self):
        self.assertEqual(wiring.task_root('41500000000', '2'),
                         Path('/home/pixelbuild/rose-actions/rango-factory-package-41500000000-2'))
        for run_id, attempt in (('', '1'), ('0', '1'), ('1', '01'), ('1x', '1'), ('-1', '1')):
            with self.assertRaises(wiring.Invalid):
                wiring.task_root(run_id, attempt)

    def test_exact_helper_call_with_lock_and_no_signing_or_publication(self):
        argv = wiring.package_argv(self.root)
        self.assertEqual(argv[1:], [str(ROOT/'scripts/host-factory-package.py'), '--device', 'rango', '--build', '2026103001',
            '--target-files', TF, '--target-files-sha256', '73db76e500bcb71757da6dbc43337067af23395a38eaac940b9ae7d5aca0baf0',
            '--otatools', OTA, '--otatools-sha256', '22d62464ff3fa74fe3fa2b4c118bba116bc3a3aa504feddcdcf38fc3f3cbb664',
            '--release-script', f'{PRODUCTION}/script/generate-release.sh',
            '--release-script-sha256', '01f7847ecdc6600bd2e3ffebe45cf79cee4fead1d6b61686c4d3c70e9c548311',
            '--avb-pkmd', f'{PRODUCTION}/releases/2026103001/public/avb_pkmd.bin',
            '--fastboot-sha256', 'f18fdc8bf467c4bb361ffc62db5f50ca981aa5f37ffb6cb9fd4ac9ff3c4e47a9',
            '--lock', LOCK, '--out', str(self.root/'package')])

    def test_exact_inspection_calls(self):
        self.assertEqual(wiring.observe_argv(self.root, '41500000000', '1')[1:], [str(ROOT/'scripts/factory-package.py'),
            'observe', TF, '--flavor', 'rango', '--build', '2026103001',
            '--source', 'os-release rango-factory-package run 41500000000 attempt 1',
            '--out', str(self.root/'inspection/target-files-observation.json')])
        self.assertEqual(wiring.classify_argv(self.root)[1:], [str(ROOT/'scripts/factory-package.py'), 'classify',
            str(self.root/'package/rango-install-2026103001.zip'), '--flavor', 'rango', '--target-files', TF,
            '--target-files-observation', str(self.root/'inspection/target-files-observation.json'),
            '--otatools', OTA, '--otatools-sha256', '22d62464ff3fa74fe3fa2b4c118bba116bc3a3aa504feddcdcf38fc3f3cbb664',
            '--work-dir', str(self.root/'classify-work'), '--build', '2026103001'])

    def packaged_tree(self, status='packaged', archive=True):
        (self.root/'package/work').mkdir(parents=True)
        (self.root/'package/work').rmdir()  # the helper removed its own work
        (self.root/'package/factory-package-receipt.json').write_text(json.dumps({'status': status}))
        (self.root/'package/factory-package.log').write_text('raw local log')
        if archive:
            (self.root/'package/rango-install-2026103001.zip').write_bytes(b'PK')
        (self.root/'inspection').mkdir()

    def test_classify_reports_ready_and_incomplete_and_fails_otherwise(self):
        seen = []

        def runner(code):
            def run(argv, stdout):
                seen.append(argv)
                stdout.write(json.dumps({'factoryReady': code == 0}))
                return subprocess.CompletedProcess(argv, code)
            return run
        self.packaged_tree()
        self.assertEqual(wiring.classify(self.root, runner(3)), 0)  # truthful incomplete is a reported result
        self.assertEqual(json.loads((self.root/'inspection/classify.json').read_text()), {'factoryReady': False})
        self.assertEqual(seen, [wiring.classify_argv(self.root)])
        with self.assertRaises(FileExistsError):  # write-once, never reused
            wiring.classify(self.root, runner(0))
        for code, expected in ((0, 0), (2, 2), (1, 1)):
            os.rmdir(self.root/'classify-work')
            os.unlink(self.root/'inspection/classify.json')
            self.assertEqual(wiring.classify(self.root, runner(code)), expected, code)

    def test_nothing_is_inspected_without_a_packaged_archive(self):
        for status, archive in (('failed', True), ('packaged', False)):
            root = wiring.task_root(str(len(status) + archive + 1), '1', parent=self.parent)
            self.root = root
            self.packaged_tree(status, archive)
            with self.subTest(status=status, archive=archive), self.assertRaises(wiring.Invalid):
                wiring.classify(root, lambda *a, **k: self.fail('classify ran'))
            self.assertFalse((root/'classify-work').exists())

    def test_report_uploads_only_the_allowlist_and_states_actual_cleanup(self):
        self.packaged_tree()
        (self.root/'inspection/target-files-observation.json').write_text('{}')
        (self.root/'inspection/classify.json').write_text('{}')
        (self.root/'classify-work').mkdir()
        upload = self.parent/'upload'
        state = wiring.report(self.root, upload)
        self.assertEqual(sorted(p.name for p in upload.iterdir()),
            ['classify.json', 'cleanup.json', 'factory-package-receipt.json', 'target-files-observation.json'])
        self.assertEqual((state['packageWorkRemoved'], state['classifyWorkRemoved'], state['classifyWorkLeft']), (True, True, []))
        self.assertEqual(state['retained'], {'rango-install-2026103001.zip': True, 'factory-package-receipt.json': True,
                                             'factory-package.log': True})
        self.assertTrue((self.root/'package/rango-install-2026103001.zip').exists())  # retained on the host

    def test_report_is_truthful_about_leftovers_and_a_missing_task_root(self):
        self.packaged_tree()
        (self.root/'package/work').mkdir()
        (self.root/'classify-work/factory-package-x').mkdir(parents=True)
        state = wiring.report(self.root, self.parent/'upload')
        self.assertEqual((state['packageWorkRemoved'], state['classifyWorkRemoved'], state['classifyWorkLeft']),
                         (False, False, ['factory-package-x']))
        absent = wiring.task_root('7', '1', parent=self.parent)
        state = wiring.report(absent, self.parent/'upload-absent')
        self.assertFalse(state['taskRootExists'])
        self.assertEqual([p.name for p in (self.parent/'upload-absent').iterdir()], ['cleanup.json'])

    def test_cli_refuses_misplaced_options_and_a_missing_parent(self):
        for argv in (('check', '--run-id', '1'), ('package',), ('package', '--run-id', '1'),
                     ('report', '--run-id', '1', '--attempt', '1'), ('classify', '--run-id', '1', '--attempt', '1',
                     '--upload-dir', '/tmp/x')):
            self.assertEqual(wire(*argv).returncode, 2, argv)
        with self.assertRaises(wiring.Invalid):
            wiring.new_dir(self.parent/'absent-parent'/'root')



class Guard(unittest.TestCase):
    BASE = {'MODE': MODE, 'CANDIDATE_SHA': '', 'BUILD_NUMBER': '', 'BUILD_TIMESTAMP': '', 'APPS': '', 'PROMOTE': 'false',
            'CHANNEL': 'beta', 'RESUME_STAGE': 'none', 'RESUME_SOURCE_RUN': '', 'RESUME_UNSIGNED_SHA256': '',
            'RESUME_SIGNED_SHA256': '', 'CAMPAIGN': '', 'APP_PUBLISH': 'false', 'ROSE_RANGO_PUBLISH': 'false',
            'ROSE_FIRST_USE_ENROLL': 'false', 'ROSE_RECOVER_RUN': '', 'BOOTSTRAP_PROFILE': 'none', 'MODELS_BOOTSTRAP_RUN': '',
            'REUSE_BINDING': '', 'ALPHA_RECOVER': 'false', 'ROSE_DEVICE': 'frankel', 'BLAZER_PREP_STAGE': 'none',
            'GITHUB_SHA': 'f' * 40}

    def run_guard(self, **change):
        guard = 'set -euo pipefail\n' + script(step(jobs()['guard'], 'Candidate is an exact commit on main'))
        env = {'PATH': os.environ['PATH'], **self.BASE, **change}
        return subprocess.run(['bash', '-c', guard], env={k: v for k, v in env.items() if v is not None},
            capture_output=True, cwd=ROOT).returncode

    def test_whole_guard_admits_exactly_the_bare_mode(self):
        self.assertEqual(self.run_guard(), 0)
        for change in ({'CANDIDATE_SHA': 'a' * 40}, {'BUILD_NUMBER': '2026103001'}, {'BUILD_TIMESTAMP': '1793500000'},
                       {'APPS': '[["dev.pixel.voice",1]]'}, {'PROMOTE': 'true'}, {'RESUME_STAGE': 'sign'},
                       {'RESUME_SOURCE_RUN': '1'}, {'RESUME_UNSIGNED_SHA256': 'a' * 64}, {'RESUME_SIGNED_SHA256': 'a' * 64},
                       {'CAMPAIGN': 'start'}, {'APP_PUBLISH': 'true'}, {'ROSE_RANGO_PUBLISH': 'true'},
                       {'ROSE_FIRST_USE_ENROLL': 'true'}, {'ROSE_RECOVER_RUN': '41000000000'},
                       {'BOOTSTRAP_PROFILE': 'fold-rango-factory-v1'}, {'MODELS_BOOTSTRAP_RUN': '1'}, {'REUSE_BINDING': '{}'},
                       {'ALPHA_RECOVER': 'true'}, {'ROSE_DEVICE': 'rango'}, {'BLAZER_PREP_STAGE': 'source'}):
            with self.subTest(change=change):
                self.assertNotEqual(self.run_guard(**change), 0)

    def test_mode_is_manual_selectable_and_checked_before_the_controller(self):
        self.assertIn('signing-bootstrap, rango-factory-package, models-sign,', WORKFLOW)
        self.assertEqual(re.findall(r'(?m)^  (\w+):\n', WORKFLOW.split('\npermissions:', 1)[0].split('\non:\n', 1)[1]),
                         ['workflow_dispatch'])
        guard = jobs()['guard']
        self.assertIn("      - name: Rango factory package check\n        id: factory-package\n        if: inputs.mode == "
                      "'rango-factory-package'\n        run: python3 scripts/rango-factory-package-wiring.py check >> "
                      "\"$GITHUB_OUTPUT\"\n", guard)
        for key in ('package_seconds', 'observe_seconds', 'classify_seconds', 'job_minutes'):
            self.assertIn(f'      factory_{key}: ${{{{ steps.factory-package.outputs.{key} }}}}\n', guard)
        self.assertEqual(re.search(r'(?m)^    needs: (.+)$', jobs()['controller'])[1], 'guard')


class Job(unittest.TestCase):
    def setUp(self):
        self.job = jobs()[MODE]
        self.tmp = Path(tempfile.mkdtemp())

    def test_read_only_builder_job_with_bounded_steps(self):
        head = code(self.job.split('    steps:\n', 1)[0])
        self.assertEqual(head, "    needs: [guard, controller]\n    if: inputs.mode == 'rango-factory-package'\n"
            '    runs-on: [self-hosted, roseos-frankel-builder]\n'
            "    timeout-minutes: ${{ fromJSON(needs.guard.outputs.factory_job_minutes || '1') }}\n"
            '    permissions:\n      contents: read\n    env:\n'
            '      PACKAGE_SECONDS: ${{ needs.guard.outputs.factory_package_seconds }}\n'
            '      OBSERVE_SECONDS: ${{ needs.guard.outputs.factory_observe_seconds }}\n'
            '      CLASSIFY_SECONDS: ${{ needs.guard.outputs.factory_classify_seconds }}\n'
            '      STAGE_SECONDS: ${{ needs.guard.outputs.factory_stage_seconds }}')
        body = code(self.job.replace(step(self.job, 'Private installer artifact (exactly SHA256SUMS and the ZIP)'), ''))
        for absent in ('id-token', 'secrets.', 'actions-gcp-login', 'GCP_', 'PIXEL_', 'publish', 'host-sign', 'signing-bootstrap',
                       'ssh-keygen', 'download-artifact', 'release-pipeline.py', 'factory-package.log', '.zip', 'target_files',
                       'rm -rf'):
            self.assertNotIn(absent, body)
        self.assertEqual(sorted(set(re.findall(r'python3 (\S+)', body))), ['scripts/rango-factory-package-wiring.py'])
        self.assertIn('          persist-credentials: false\n', self.job)
        for name, var, command in (('Package the install archive (bounded, under the helper\'s shared lock)', 'PACKAGE', 'package'),
                                   ('Observe the signed target-files (bounded)', 'OBSERVE', 'observe'),
                                   ('Classify the install archive (bounded)', 'CLASSIFY', 'classify')):
            self.assertEqual(script(step(self.job, name)), f'timeout --kill-after=30s "${{{var}_SECONDS}}s" python3 '
                f'scripts/rango-factory-package-wiring.py {command} --run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT"\n')
            self.assertNotIn('if:', step(self.job, name))  # each runs only after the previous step succeeded
        self.assertEqual([n for n, b in jobs().items() if 'rango-factory-package-wiring.py package' in b], [MODE])
        self.assertNotIn('rango-factory-package', jobs()['rango-factory-observe'])

    def test_recheck_on_the_builder_must_match_the_guard_bounds(self):
        bin_dir = self.tmp/'bin'
        bin_dir.mkdir()
        (bin_dir/'python3').write_text('#!/bin/sh\nprintf "%s\\n" "$CHECK_OUTPUT"\n')
        (bin_dir/'python3').chmod(0o755)
        text = script(step(self.job, 'Recheck the pins and bounds on the builder'))
        good = 'package_seconds=5400\nobserve_seconds=900\nclassify_seconds=7200\nstage_seconds=900\njob_minutes=297'
        def run(**env):
            base = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'RUNNER_TEMP': str(self.tmp), 'CHECK_OUTPUT': good,
                    'PACKAGE_SECONDS': '5400', 'OBSERVE_SECONDS': '900', 'CLASSIFY_SECONDS': '7200', 'STAGE_SECONDS': '900'}
            return subprocess.run(['bash', '-c', text], env={**base, **env}, capture_output=True).returncode
        self.assertEqual(run(), 0)
        for change in ({'PACKAGE_SECONDS': '3600'}, {'CLASSIFY_SECONDS': ''}, {'STAGE_SECONDS': '60'}, {'OBSERVE_SECONDS': '900s'},
                       {'CHECK_OUTPUT': good.replace('7200', '3600')}):
            self.assertNotEqual(run(**change), 0, change)

    def test_report_and_upload_always_run_with_only_the_allowlist_directory(self):
        report = step(self.job, 'Cleanup report and allowlisted receipts')
        self.assertIn('        if: always()\n', report)
        self.assertEqual(script(report), 'python3 scripts/rango-factory-package-wiring.py report --run-id "$GITHUB_RUN_ID" '
            '--attempt "$GITHUB_RUN_ATTEMPT" --upload-dir "$RUNNER_TEMP/rango-factory-package-upload-$GITHUB_RUN_ID-'
            '$GITHUB_RUN_ATTEMPT" --installer-upload "$INSTALLER_UPLOAD"\n')
        self.assertIn("          INSTALLER_UPLOAD: ${{ steps.installer-artifact.outcome || 'skipped' }}\n", report)
        artifact = step(self.job, 'Receipts artifact')
        self.assertIn('        if: always()\n', artifact)
        self.assertIn('          path: ${{ runner.temp }}/rango-factory-package-upload-${{ github.run_id }}-'
                      '${{ github.run_attempt }}\n          retention-days: 7\n', artifact)


class Ownership(unittest.TestCase):
    def test_owned_start_cleanup_covers_this_mode_and_joins_cleanup(self):
        controller, cleanup = jobs()['controller'], jobs()['cleanup']
        own = "inputs.mode == 'blazer-prep' || inputs.mode == 'rango-factory-package'"
        self.assertIn(f'      - if: {own}\n        run: python3 scripts/release-pipeline.py start-builder --ownership-record', controller)
        self.assertIn(f'        if: always() && ({own})\n        run: python3 scripts/release-pipeline.py builder-ownership', controller)
        self.assertIn(f'      - if: {own}\n        env:\n          STARTED: ${{{{ needs.controller.outputs.started }}}}\n'
                      '        run: python3 scripts/release-pipeline.py stop-builder --only-if-started "$STARTED"\n', cleanup)
        self.assertIn('blazer-prep, rango-factory-package, app-builder', re.search(r'(?m)^    needs: (.+)$', cleanup)[1])



class InstallerExport(unittest.TestCase):
    """The private installer artifact: exactly SHA256SUMS and the ZIP, only after inspection and verification."""
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        self.root = wiring.task_root('41500000000', '1', parent=self.parent)
        self.job = jobs()[MODE]

    def tree(self, data=b'PK-install', recorded=None, report=True):
        (self.root/'package').mkdir(parents=True)
        (self.root/'inspection').mkdir()
        archive = self.root/'package/rango-install-2026103001.zip'
        archive.write_bytes(data)
        output = {'file': 'rango-install-2026103001.zip', 'size': len(data), 'sha256': wiring.sha256_file(archive)}
        output.update(recorded or {})
        (self.root/'package/factory-package-receipt.json').write_text(json.dumps({'status': 'packaged', 'output': output}))
        (self.root/'package/factory-package.log').write_text('raw local log')
        if report:
            (self.root/'inspection/target-files-observation.json').write_text('{}')
            (self.root/'inspection/classify.json').write_text(json.dumps({'factoryReady': False}))
        return output

    def test_verified_archive_is_staged_with_one_hash_line(self):
        output = self.tree()
        record = wiring.stage_installer(self.root)
        self.assertEqual(record, {'file': 'package/rango-install-2026103001.zip', 'bytes': output['size'],
                                  'sha256': output['sha256'], 'capBytes': 8 << 30, 'retentionDays': 3})
        self.assertEqual((self.root/'SHA256SUMS').read_text(), f'{output["sha256"]}  package/rango-install-2026103001.zip\n')
        with self.assertRaises(FileExistsError):
            wiring.stage_installer(self.root)  # write-once

    def test_cap_hash_size_and_missing_inspection_refuse_and_stage_nothing(self):
        cases = (({}, {'cap': 4}, 'above the proposed 4-byte cap'), ({'sha256': 'a' * 64}, {}, 'sha256 differs'),
                 ({'size': 3}, {}, 'not the recorded 3'), ({'file': 'other.zip'}, {}, 'does not record'),
                 ({'sha256': 'short'}, {}, 'does not record'))
        for i, (recorded, kwargs, message) in enumerate(cases):
            self.root = wiring.task_root(str(i + 1), '1', parent=self.parent)
            self.tree(recorded=recorded)
            with self.subTest(recorded=recorded), self.assertRaisesRegex(wiring.Invalid, message):
                wiring.stage_installer(self.root, **kwargs)
            self.assertFalse((self.root/'SHA256SUMS').exists())
        self.root = wiring.task_root('9', '1', parent=self.parent)
        self.tree(report=False)
        with self.assertRaisesRegex(wiring.Invalid, 'not exported uninspected'):
            wiring.stage_installer(self.root)
        self.assertFalse((self.root/'SHA256SUMS').exists())

    def test_artifact_step_exports_exactly_two_files_privately_for_three_days(self):
        stage = step(self.job, 'Stage the installer for the private artifact (bounded)')
        self.assertEqual(script(stage), 'timeout --kill-after=30s "${STAGE_SECONDS}s" python3 scripts/rango-factory-package-'
            'wiring.py stage-installer --run-id "$GITHUB_RUN_ID" --attempt "$GITHUB_RUN_ATTEMPT" >> "$GITHUB_OUTPUT"\n')
        self.assertNotIn('if:', stage)  # only after inspection succeeded (classify 0 or 3)
        artifact = step(self.job, 'Private installer artifact (exactly SHA256SUMS and the ZIP)')
        self.assertEqual(artifact, '        id: installer-artifact\n        timeout-minutes: 45\n'
            '        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2\n        with:\n'
            '          name: rango-install-2026103001-${{ github.run_id }}-${{ github.run_attempt }}\n'
            '          path: |\n            ${{ steps.installer.outputs.root }}/SHA256SUMS\n'
            '            ${{ steps.installer.outputs.root }}/package/rango-install-2026103001.zip\n'
            '          retention-days: 3\n          if-no-files-found: error\n          compression-level: 0\n')
        self.assertEqual(wiring.INSTALLER['uploadMinutes'], 45)
        self.assertEqual(self.job.count('actions/upload-artifact@'), 2)  # the installer and the unchanged reports

    def test_stage_prints_only_the_root_for_the_artifact_paths(self):
        self.tree()
        bound = self.root
        original = wiring.task_root
        wiring.task_root = lambda run_id, attempt: bound
        try:
            import contextlib
            import io
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(wiring.main(['stage-installer', '--run-id', '41500000000', '--attempt', '1']), 0)
        finally:
            wiring.task_root = original
        self.assertEqual(out.getvalue(), f'root={bound}\n')

    def test_reports_stay_intact_and_record_the_installer_outcome_truthfully(self):
        self.tree()
        wiring.stage_installer(self.root)
        for outcome in ('failure', 'success'):
            root = self.root
            (root/'cleanup.json').unlink(missing_ok=True)
            upload = self.parent/f'upload-{outcome}'
            state = wiring.report(root, upload, outcome)
            self.assertEqual(sorted(p.name for p in upload.iterdir()),
                ['classify.json', 'cleanup.json', 'factory-package-receipt.json', 'target-files-observation.json'])
            self.assertEqual(state['installer']['uploadOutcome'], outcome)
            self.assertTrue(state['installer']['staged'])
            self.assertEqual((state['installer']['retentionDays'], state['installer']['capBytes']), (3, 8 << 30))
        unstaged = wiring.task_root('8', '1', parent=self.parent)
        self.root = unstaged
        self.tree(report=False)
        state = wiring.report(unstaged, self.parent/'upload-unstaged')
        self.assertEqual((state['installer']['staged'], state['installer']['uploadOutcome']), (False, 'not-run'))
        self.assertEqual(wire('stage-installer', '--run-id', '1', '--attempt', '1', '--installer-upload', 'success').returncode, 2)


if __name__ == '__main__':
    unittest.main()
