#!/usr/bin/env python3
"""Offline tests of the QA setup plan and tool manifest for EXP006-ISOLATED-TRIAL-001. Synthetic only: no VM, network,
token or key. They also check the plan against OS Product's merged trial_preflight.py without changing it."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tp = load('trial_plan', HERE/'trial_plan.py')
preflight = load('trial_preflight_for_qa', ROOT/'os/tests/persistent-app-experiment/trial_preflight.py')
MANIFEST = json.loads((HERE/'tool-manifest.json').read_text())
T0 = 1793451600  # a fixed synthetic T0


class PlanInvariants(unittest.TestCase):
    def setUp(self):
        self.plan = tp.plan(T0)

    def test_the_generated_plan_validates(self):
        self.assertEqual(tp.validate(self.plan), [])

    def test_timeline_matches_the_approved_trial(self):
        minutes = {k: (v - T0) // 60 for k, v in self.plan['timeline'].items()}
        self.assertEqual(minutes, {'sshKeyRemoved': 55, 'sshKeyExpires': 60, 'lastJobStart': 75, 'stopAccepting': 110,
                                   'gceStop': 120})
        self.assertEqual(tp.MIN_REMAINING_MINUTES * 60, preflight.MIN_REMAINING, 'same 35-minute rule as the preflight')

    def test_deadline_file_is_one_unix_time_at_stop_accepting(self):
        self.assertEqual(self.plan['deadlineFile']['content'], f'{T0 + 110 * 60}\n')

    def test_teardown_timer_is_one_shot_at_t_plus_110(self):
        timer = self.plan['teardown']['timer']
        self.assertIn('OnCalendar=' + tp.utc(T0 + 6600) + '\n', timer)
        self.assertIn('Persistent=false', timer)
        systemd = shutil.which('systemd-analyze')
        if systemd:
            out = subprocess.run([systemd, 'calendar', tp.utc(T0 + 6600)], capture_output=True, text=True)
            if out.returncode == 0:
                self.assertIn('Next elapse', out.stdout)

    def test_outbound_ruleset_parses_when_nft_can_check_it(self):
        nft = shutil.which('nft')
        if not nft:
            self.skipTest('nft not installed')
        with tempfile.NamedTemporaryFile('w', suffix='.nft') as f:
            f.write(self.plan['outbound']['nft'])
            f.flush()
            out = subprocess.run([nft, '-c', '-f', f.name], capture_output=True, text=True)
        if out.returncode and 'Operation not permitted' in out.stderr:
            self.skipTest('nft -c needs netlink permission here')
        self.assertEqual(out.returncode, 0, out.stderr)


class PlanRefusals(unittest.TestCase):
    def refused(self, mutate, fragment):
        plan = tp.plan(T0)
        mutate(plan)
        problems = tp.validate(plan)
        self.assertTrue(any(fragment in p for p in problems), problems)

    def test_each_broken_invariant_is_refused(self):
        cases = [
            (lambda p: p['timeline'].update(sshKeyExpires=T0 + 61 * 60), 'outlives T+60m'),
            (lambda p: p['timeline'].update(lastJobStart=T0 + 80 * 60), 'less than 35 min'),
            (lambda p: p['timeline'].update(stopAccepting=T0 + 115 * 60), 'less than 10 min'),
            (lambda p: p['gce'].update(maxRunDuration=10800), 'GCE STOP'),
            (lambda p: p['gce'].update(automaticRestart=True), 'GCE STOP'),
            (lambda p: p['deadlineFile'].update(content=f'{T0 + 125 * 60}\n'), 'equal to stopAccepting'),
            (lambda p: p['deadlineFile'].update(fileMode='0644'), 'root-owned'),
            (lambda p: p['teardown'].update(timer=p['teardown']['timer'].replace('Persistent=false', 'Persistent=true')), 'fire once'),
            (lambda p: p['teardown'].update(service=p['teardown']['service'].replace('User=root', 'User=rose-test')), 'root oneshot'),
            (lambda p: p['teardown'].update(script=p['teardown']['script'] + './config.sh remove --token "$T"\n'), 'tokens'),
            (lambda p: p['teardown'].update(script=p['teardown']['script'].replace('.credentials_rsaparams', '')), 'lacks'),
            (lambda p: p['outbound'].update(nft=p['outbound']['nft'].replace('policy drop;', 'policy accept;')), 'default-drop'),
            (lambda p: p['outbound'].update(nft=p['outbound']['nft'].replace('    meta nfproto ipv6 drop\n', '')), 'IPv6-drop'),
            (lambda p: p['outbound'].update(nft=p['outbound']['nft'].replace('tcp dport 443 accept', 'tcp dport 443 accept\n    tcp dport 80 accept')), 'accepts more'),
            (lambda p: p['outbound'].update(budgetBytes=16 * 2**30, nft=tp.nft_ruleset(16 * 2**30)), 'USD 1 reserve'),
            (lambda p: p.update(cleanup=[c for c in p['cleanup'] if c['item'] != 'ssh-key']), 'cleanup misses'),
            (lambda p: [c.update(owner='vm-timer') for c in p['cleanup'] if c['item'] == 'runner-registration'], 'browser operator'),
        ]
        for mutate, fragment in cases:
            with self.subTest(fragment):
                self.refused(mutate, fragment)

    def test_quota_after_accept_is_refused(self):
        plan = tp.plan(T0)
        nft = plan['outbound']['nft']
        swapped = nft.replace('    tcp dport 443 quota name "egress_budget" drop\n    tcp dport 443 accept\n',
                              '    tcp dport 443 accept\n    tcp dport 443 quota name "egress_budget" drop\n')
        plan['outbound']['nft'] = swapped
        self.assertTrue(any('evaluated after' in p for p in tp.validate(plan)))


class DeadlineAgainstMergedPreflight(unittest.TestCase):
    """The plan's deadline file, written as setup would, judged by trial_preflight.deadline_facts unchanged."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='qa-trial-deadline-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.dir = self.root/'roseos-systemui-test'
        self.dir.mkdir(mode=0o755)
        self.file = self.dir/'deadline'
        self.file.write_text(tp.plan(T0)['deadlineFile']['content'])
        os.chmod(self.file, 0o444)
        os.chmod(self.dir, 0o755)
        if os.geteuid() != 0:   # files here belong to the test user; judge modes only, as root ownership cannot be made
            patcher = mock.patch.object(preflight, '_root_only', lambda info: not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
            patcher.start()
            self.addCleanup(patcher.stop)

    def facts(self, now):
        return preflight.deadline_facts(now, self.file)

    def test_jobs_may_start_from_setup_until_the_last_start(self):
        for minute in (10, 30, 60, 75):
            with self.subTest(minute):
                problems, facts = self.facts(T0 + minute * 60)
                self.assertEqual(problems, [])
                self.assertEqual(facts['remainingSeconds'], (110 - minute) * 60)

    def test_a_job_after_the_last_start_is_refused(self):
        problems, _ = self.facts(T0 + 75 * 60 + 1)
        self.assertTrue(problems and 'at least 2100' in problems[0])

    def test_a_deadline_written_beyond_the_trial_is_refused(self):
        os.chmod(self.file, 0o644)
        self.file.write_text(f'{T0 + 130 * 60}\n')
        os.chmod(self.file, 0o444)
        problems, _ = self.facts(T0)
        self.assertTrue(problems and 'beyond the approved two-hour trial' in problems[0])

    def test_a_writable_deadline_is_refused(self):
        os.chmod(self.file, 0o666)
        problems, _ = self.facts(T0 + 600)
        self.assertTrue(problems and 'writable only by root' in problems[0])


class TeardownScriptOffline(unittest.TestCase):
    """The generated teardown run against a fake root with pkill/pgrep/systemctl stubbed: what it removes and reports."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='qa-trial-teardown-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        runner = self.root/'home/rose-test/actions-runner'
        (runner/'_work/RoseOS-1').mkdir(parents=True)
        for name in ('.runner', '.credentials', '.credentials_rsaparams', 'config.sh'):
            (runner/name).write_text('synthetic\n')
        (self.root/'home/rose-test/.android/avd').mkdir(parents=True)
        (self.root/'tmp').mkdir()
        (self.root/'var/tmp').mkdir(parents=True)
        (self.root/'var/log').mkdir(parents=True)
        self.bin = self.root/'stubbin'
        self.bin.mkdir()
        self.log = self.root/'calls.log'
        for tool, code in (('pkill', 0), ('pgrep', 1), ('systemctl', 0)):
            path = self.bin/tool
            path.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{self.log}"\nexit {code}\n')
            path.chmod(0o755)
        self.script = self.root/'teardown.sh'
        self.script.write_text(tp.plan(T0)['teardown']['script'])
        self.script.chmod(0o755)

    def run_teardown(self):
        env = dict(os.environ, ROSE_TRIAL_ROOT=str(self.root), ROSE_TRIAL_GRACE='0', PATH=f'{self.bin}:{os.environ["PATH"]}')
        return subprocess.run(['bash', str(self.script)], env=env, capture_output=True, text=True)

    def test_bash_syntax(self):
        self.assertEqual(subprocess.run(['bash', '-n', str(self.script)]).returncode, 0)

    def test_removes_runner_credentials_work_and_android_state_and_reports_without_claiming_unregistration(self):
        result = self.run_teardown()
        self.assertEqual(result.returncode, 0, result.stderr)
        runner = self.root/'home/rose-test/actions-runner'
        for name in ('.runner', '.credentials', '.credentials_rsaparams', '_work'):
            self.assertFalse((runner/name).exists(), name)
        self.assertTrue((runner/'config.sh').exists(), 'the runner install stays for the operator to unregister')
        self.assertFalse((self.root/'home/rose-test/.android').exists())
        receipt = json.loads((self.root/'var/log/roseos-trial-teardown.json').read_text())
        self.assertEqual((receipt['teardown'], receipt['runnerUnregistered']), ('removed', 'not-by-timer'))
        calls = self.log.read_text()
        self.assertIn('pkill -TERM -u rose-test', calls)
        self.assertIn('pkill -KILL -u rose-test', calls)
        self.assertIn('systemctl disable --now roseos-trial-teardown.timer', calls)

    def test_surviving_processes_make_the_teardown_incomplete(self):
        (self.bin/'pgrep').write_text(f'#!/bin/sh\necho "pgrep $*" >> "{self.log}"\nexit 0\n')
        result = self.run_teardown()
        self.assertNotEqual(result.returncode, 0)
        receipt = json.loads((self.root/'var/log/roseos-trial-teardown.json').read_text())
        self.assertEqual(receipt['teardown'], 'incomplete')


class LeakScan(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='qa-trial-scan-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root/'_diag').mkdir()
        (self.root/'_diag/Runner_20261031.log').write_text('Enter runner registration token: ****\n')
        (self.root/'.bash_history').write_text('./config.sh --url https://github.com/Immortal-Protocols/RoseOS-1\n')
        self.sentinel = 'HARMLESS-NOT-A-TOKEN-7f3a9c'

    def scan(self):
        out = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO(self.sentinel + '\n')), mock.patch('sys.stdout', out):
            code = tp.main(['scan-leak', str(self.root)])
        return code, json.loads(out.getvalue())

    def test_absent_sentinel_passes_with_fixed_words(self):
        code, result = self.scan()
        self.assertEqual((code, result), (0, {'result': 'absent', 'filesScanned': 2, 'filesWithSentinel': 0}))

    def test_a_logged_sentinel_is_found_and_never_echoed(self):
        (self.root/'_diag/Worker.log').write_text(f'token={self.sentinel}\n')
        code, result = self.scan()
        self.assertEqual((code, result['result'], result['filesWithSentinel']), (1, 'present', 1))
        self.assertNotIn(self.sentinel, json.dumps(result))

    def test_short_sentinel_is_refused(self):
        self.sentinel = 'short'
        with mock.patch('sys.stdin', io.StringIO('short\n')), mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(tp.main(['scan-leak', str(self.root)]), 2)


class ToolManifest(unittest.TestCase):
    def test_every_required_tool_is_pinned_or_explicitly_missing(self):
        paths = {p['path'] for p in MANIFEST['sdk']['packages']}
        self.assertEqual(paths, {'cmdline-tools;22.0', 'platform-tools', 'emulator', 'system-images;android-35;default;x86_64',
                                 'platforms;android-35', 'build-tools;35.0.0'})
        for p in MANIFEST['sdk']['packages']:
            with self.subTest(p['path']):
                self.assertRegex(p['sha1'], r'^[0-9a-f]{40}$')
                self.assertRegex(p['sha256'], r'^[0-9a-f]{64}$')
                self.assertEqual((p['channel'], p['license']), ('stable', 'android-sdk-license'))
        self.assertEqual(MANIFEST['runner']['sha256'], 'missing')
        self.assertTrue(MANIFEST['runner']['missingReason'])

    def test_no_new_licence_term_and_no_shim_sdkmanager(self):
        self.assertEqual(MANIFEST['license']['newTerms'], [])
        self.assertEqual({p['license'] for p in MANIFEST['sdk']['packages']}, {MANIFEST['license']['id']})
        cmdline = next(p for p in MANIFEST['sdk']['packages'] if p['path'].startswith('cmdline-tools'))
        self.assertNotEqual(cmdline['revision'], '23.0', '23.0 downloads an unpinned android-cli at run time')

    def test_platform_and_build_tools_match_the_repository_pins(self):
        script = (ROOT/'scripts/setup-android-sdk.sh').read_text()
        for p in MANIFEST['sdk']['packages']:
            if p['path'] in ('platforms;android-35', 'build-tools;35.0.0'):
                self.assertIn(f'fetch {p["archive"]} {p["sha256"]}', script)


if __name__ == '__main__':
    unittest.main()
