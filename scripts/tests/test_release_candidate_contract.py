"""Checked-in candidate contract for build 2026102803 (Rose Agent 301, controller 28, Calculator 4), the immutable
2026102802 draft it supersedes, and the controller's async builder start/stop, using the retained nonsecret evidence in
this repository and a fake gcloud."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('release_pipeline', SCRIPTS/'release-pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)
from background_apks import installed_precedence  # noqa: E402

CONFIG = json.loads((ROOT/'os/release-pipeline/frankel.json').read_text())
APPS = [['dev.pixel.voice', 301], ['dev.pixel.calculator.agent', 4], ['dev.pixel.backgroundcontrol', 28]]
DRAFT = {'build': '2026102803', 'timestamp': '1793176860'}
# The verified 2026102802 draft's notes are immutable evidence (pixel-ota draft frankel-2026102802).
NOTES_2802_SHA256 = 'a84fac7a77cd521d53226bcf1d8b89528f6f23b0f8a0eb42b73570055a4f865f'

def manifest_version(path):
    text = (ROOT/path).read_text()
    return (re.search(r'package="([^"]+)"', text).group(1), int(re.search(r'android:versionCode="(\d+)"', text).group(1)),
        re.search(r'android:versionName="([^"]+)"', text).group(1))

class CheckedInCandidateTest(unittest.TestCase):
    def test_host_paths_are_the_retained_p1_paths(self):
        host = CONFIG['host']
        self.assertEqual(host['sign']['otatools'],
            'host/linux-x86/obj/ETC/otatools-packagelinux_glibc_x86_64_intermediates/otatools-packagelinux_glibc_x86_64')
        self.assertEqual(host['tools']['debugfs'], '/usr/sbin/debugfs')
        self.assertEqual(host['tools']['simg2img'],
            '/home/pixelbuild/grapheneos-2026100600/releases/2026102801/release-frankel-2026102801/bin/simg2img')
        self.assertEqual(pipeline.preflight('guard', CONFIG, {'ROSEOS_BUILDER_RUNNER_READY': 'true',
            'GCP_WORKLOAD_IDENTITY_PROVIDER': 'x', 'GCP_CONTROLLER_SERVICE_ACCOUNT': 'x', 'GCP_SIGNER_SERVICE_ACCOUNT': 'x',
            'PIXEL_OTA_PUBLISHER': 'github-app', 'PIXEL_OTA_APP_ID': '1', 'PIXEL_OTA_APP_KEY_PRESENT': 'true'}, 'production'), [])

    def test_source_manifests_match_the_allocated_versions(self):
        self.assertEqual(manifest_version('workspace/rose-agent/app/AndroidManifest.xml'), ('dev.pixel.voice', 301, '301-wave-beta'))
        self.assertEqual(manifest_version('workspace/pixel-calculator-agent/AndroidManifest.xml')[:2], ('dev.pixel.calculator.agent', 4))
        self.assertEqual(manifest_version('os/background-control/agent-pip/AndroidManifest.xml'),
            ('dev.pixel.backgroundcontrol', 28, '2.1.5-pip'))

    def test_watch_extras_match_between_producer_and_consumer(self):
        producer = (ROOT/'workspace/rose-agent/app/src/dev/pixel/voice/TaskControl.java').read_text()
        consumer = (ROOT/'os/background-control/agent-pip/src/dev/pixel/backgroundcontrol/WatchBinding.java').read_text()
        for extra in ('dev.pixel.backgroundcontrol.extra.WATCH_TASK_ID', 'dev.pixel.backgroundcontrol.extra.WATCH_SESSION_ID'):
            self.assertIn(f'"{extra}"', producer)
            self.assertIn(f'"{extra}"', consumer)

    def test_release_notes_bind_to_the_candidate_and_describe_the_draft(self):
        self.assertEqual(hashlib.sha256((ROOT/'os/release-notes/frankel-2026102802.md').read_bytes()).hexdigest(), NOTES_2802_SHA256)
        path = CONFIG['host']['sign']['updateNotes'].format(device='frankel', build='2026102803')
        notes = (ROOT/path).read_text()
        flat = ' '.join(notes.split())
        for expected in ('2026102803', 'frankel', 'user build', 'versionCode 301 (301-wave-beta)', '28 (2.1.5-pip)', '4 (unchanged)',
                'not promoted', 'Beta channel (pixel-ota Latest) is unchanged', 'not been accepted on a phone',
                'locked sessions off', 'prototype agent list empty', 'not yet verified at runtime'):
            self.assertIn(expected, flat)
        for claim in ('downgrade', 'available on Alpha', 'available on Prod'):
            self.assertNotIn(claim, flat)
        # host-sign reads the notes from the candidate commit with git show; HEAD holds the same bytes as the tree.
        head = subprocess.run(['git', '-C', str(ROOT), 'show', f'HEAD:{path}'], capture_output=True)
        if head.returncode == 0:
            self.assertEqual(head.stdout.decode(), notes)

    def test_reconcile_passes_the_delivered_identities_baseline(self):
        reconcile = CONFIG['host']['integrate']['reconcile']
        args = reconcile['applyArgs']
        self.assertEqual(args[args.index('--installed-identities') + 1], '{installed_identities}')
        self.assertEqual(reconcile['installedIdentities'], CONFIG['versionBaselines']['installedEvidence']['identities'])
        installed = json.loads((ROOT/reconcile['installedIdentities']).read_text())
        signer = CONFIG['identities']['appSignerSha256'].upper()
        rows = [{'packageName': p, 'versionCode': v, 'certificates': [signer]} for p, v in APPS]
        issues, notes = installed_precedence(rows, installed)
        self.assertEqual(issues, [])
        self.assertEqual(len(notes), 1)  # unchanged calculator 4 equals the delivered copy; voice and controller are newer

    def test_version_baselines_admit_the_draft_and_refuse_older_candidates(self):
        pipeline.check_version_baselines(CONFIG, DRAFT, APPS)
        for apps, cand, message in (
                ([['dev.pixel.voice', 300], *APPS[1:]], DRAFT, 'dev.pixel.voice versionCode 300 must exceed retained 2026102802 versionCode 300'),
                ([*APPS[:2], ['dev.pixel.backgroundcontrol', 27]], DRAFT,
                    'dev.pixel.backgroundcontrol versionCode 27 must exceed retained 2026102802 versionCode 27'),
                ([['dev.pixel.voice', 299], *APPS[1:]], DRAFT, 'must exceed retained 2026102801 versionCode 299'),
                ([['dev.pixel.voice', 297], *APPS[1:]], DRAFT, 'below delivered 2026102701 versionCode 298'),
                (APPS[1:], DRAFT, 'dev.pixel.voice versionCode None must exceed'),
                (APPS, {'build': '2026102803', 'timestamp': '1793163120'}, 'must exceed retained 2026102802 \\(1793163120\\)'),
                (APPS, {'build': '2026102802', 'timestamp': '1793176860'}, 'must exceed retained 2026102802')):
            with self.assertRaisesRegex(pipeline.CheckFailed, message):
                pipeline.check_version_baselines(CONFIG, cand, apps)
        without = {k: v for k, v in CONFIG.items() if k != 'versionBaselines'}
        with self.assertRaises(pipeline.SetupMissing):
            pipeline.check_version_baselines(without, DRAFT, APPS)

class BuilderControlTest(unittest.TestCase):
    """Fake gcloud: describe pops the next status from a queue (repeating the last); start/stop exit as configured."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.gcloud = self.root/'gcloud'
        self.gcloud.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(f'''
            import json, sys
            root = {str(self.root)!r}
            open(root + '/argv.log', 'a').write(json.dumps(sys.argv[1:]) + '\\n')
            if sys.argv[3] == 'describe':
                states = json.load(open(root + '/states.json'))
                print(states.pop(0) if len(states) > 1 else states[0])
                json.dump(states, open(root + '/states.json', 'w'))
            else:
                sys.exit(int(open(root + '/action-exit').read()))'''))
        self.gcloud.chmod(0o755)
        self.now = [0]

    def control(self, action, states, action_exit=0, timeout=50):
        (self.root/'states.json').write_text(json.dumps(states))
        (self.root/'action-exit').write_text(str(action_exit))
        sleep = lambda seconds: self.now.__setitem__(0, self.now[0] + seconds)
        return pipeline.Production().control_builder(action, gcloud=str(self.gcloud), timeout=timeout, interval=10,
            sleep=sleep, clock=lambda: self.now[0])

    def calls(self):
        return [json.loads(line) for line in (self.root/'argv.log').read_text().splitlines()]

    def test_start_submits_async_once_and_polls_describe_only(self):
        self.assertEqual(self.control('start', ['TERMINATED', 'TERMINATED', 'STAGING', 'RUNNING']), 'RUNNING')
        calls = self.calls()
        requests = [c for c in calls if c[2] != 'describe']
        self.assertEqual(requests, [['compute', 'instances', 'start', 'immortal-os-builder', '--project=protocols-465723',
            '--zone=us-west2-a', '--async', '--quiet']])
        self.assertTrue(all(c[:3] in (['compute', 'instances', 'describe'], ['compute', 'instances', 'start']) for c in calls))
        self.assertTrue(all('--format=value(status)' in c for c in calls if c[2] == 'describe'))
        self.assertFalse(any('operations' in ' '.join(c) for c in calls))

    def test_stop_and_already_in_target_or_transition_send_no_duplicate_request(self):
        self.assertEqual(self.control('stop', ['RUNNING', 'STOPPING', 'TERMINATED']), 'TERMINATED')
        (self.root/'argv.log').unlink()
        self.assertEqual(self.control('start', ['RUNNING']), 'RUNNING')
        self.assertEqual(self.control('start', ['STAGING', 'RUNNING']), 'RUNNING')
        self.assertEqual([c for c in self.calls() if c[2] != 'describe'], [])

    def test_denied_request_fails_at_once_and_uncertain_submission_is_reconciled(self):
        with self.assertRaisesRegex(pipeline.CheckFailed, 'start request failed .*observed TERMINATED'):
            self.control('start', ['TERMINATED'], action_exit=1)
        self.assertEqual(len([c for c in self.calls() if c[2] != 'describe']), 1)
        (self.root/'argv.log').unlink()
        self.assertEqual(self.control('start', ['TERMINATED', 'PROVISIONING', 'RUNNING'], action_exit=1), 'RUNNING')
        self.assertEqual(len([c for c in self.calls() if c[2] != 'describe']), 1)

    def test_timeout_and_wrong_state_report_the_last_observed_state(self):
        with self.assertRaisesRegex(pipeline.CheckFailed, 'did not reach RUNNING within 50s; last observed STAGING'):
            self.control('start', ['TERMINATED', 'STAGING'])
        with self.assertRaisesRegex(pipeline.CheckFailed, 'moved to STOPPING while waiting for RUNNING'):
            self.control('start', ['TERMINATED', 'STAGING', 'STOPPING'])
        (self.root/'argv.log').unlink()
        with self.assertRaisesRegex(pipeline.CheckFailed, 'builder is SUSPENDED; refusing to start'):
            self.control('start', ['SUSPENDED'])
        self.assertEqual([c for c in self.calls() if c[2] != 'describe'], [])

if __name__ == '__main__':
    unittest.main()
