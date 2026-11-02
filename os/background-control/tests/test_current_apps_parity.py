"""#156/#225 parity against the common fixture qa/parity/current-apps-parity.json (App Core owns it; Home and Launcher3
read it). Every case's controllerReply and platform view must reach the case's one expected category under the agreed
render rule, and every case with controllerInputs is run through the production controller CurrentApps (compiled, via
CurrentAppsParityProbe), whose reply must match controllerReply. Launcher3's side is checked here with the reference
rule over the platform view; OS Product's own tests check their consumer. No device, framework or UI is involved."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = json.loads((ROOT/'qa/parity/current-apps-parity.json').read_text())
SRC = ROOT/'os/background-control/agent-pip/src/dev/pixel/backgroundcontrol'
CONTROLLER = 'dev.pixel.backgroundcontrol'
LIVE_SESSION = {'ready', 'blocked', 'launching'}


def render_reply(r):
    """The agreed rule over a current_apps reply (what Home renders): cards only for a complete, well-formed live list."""
    if r is None or not isinstance(r, dict): return {'category': 'cannot_confirm', 'apps': []}
    if r.get('complete') is not True: return {'category': 'cannot_confirm', 'apps': []}
    if r.get('status') == 'none': return {'category': 'genuine_empty', 'apps': []}
    if r.get('status') != 'live' or r.get('sessionStatus') not in LIVE_SESSION: return {'category': 'cannot_confirm', 'apps': []}
    apps = sorted({a['packageName'] for a in r.get('apps', [])})
    return {'category': 'cards', 'apps': apps} if apps else {'category': 'genuine_empty', 'apps': []}


def render_platform(p):
    """The reference rule over the per-device platform view (what Launcher3 renders)."""
    if p.get('unavailable') or p.get('locked') or p.get('malformed'): return {'category': 'cannot_confirm', 'apps': []}
    mine = [d for d in p['devices'] if d['owner'] == 'controller']
    if not mine: return {'category': 'genuine_empty', 'apps': []}
    if len(mine) > 1 or not mine[0]['complete']: return {'category': 'cannot_confirm', 'apps': []}
    apps = sorted(set(mine[0]['apps']) - {CONTROLLER})
    return {'category': 'cards', 'apps': apps} if apps else {'category': 'genuine_empty', 'apps': []}


def line(c):
    def typed(v):
        t, x = v
        if t == 'S': return 'S:' + ','.join(x)
        if t == 'B': return 'B:' + ('true' if x else 'false')
        return f'{t}:{x}'
    platform = '-' if c['platform'] is None else ';'.join(typed(c['platform'][k]) for k in ('generation', 'packageNames', 'userId', 'complete', 'closed'))
    front = '-' if c['front'] is None else ','.join(c['front'])
    return '\t'.join([str(c['locked']).lower(), str(c['session']).lower(), c['status'], str(c['blocked']).lower(), c['target'] or '-', front, platform])


class ParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp()
        subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '8', '-target', '8', '-Xlint:-options',
                        '-d', cls.out, str(SRC/'CurrentApps.java'), str(Path(__file__).parent/'CurrentAppsParityProbe.java')],
                       check=True, capture_output=True)
        cls.cases = {c['name']: c for c in FIXTURE['cases']}

    def run_controller(self, inputs):
        r = subprocess.run(['java', '-cp', self.out, 'dev.pixel.backgroundcontrol.CurrentAppsParityProbe', CONTROLLER],
                           input='\n'.join(line(c) for c in inputs) + '\n', capture_output=True, text=True, check=True)
        out = []
        for l in r.stdout.rstrip('\n').split('\n'):
            status, complete, reason, source, session_status, apps = l.split('\t')
            out.append({'status': status, 'complete': complete == 'true', 'incompleteReason': reason, 'source': source,
                        'sessionStatus': session_status or None,
                        'apps': [{'packageName': a.split(':')[0], 'front': a.split(':')[1] == 'true', 'target': a.split(':')[2] == 'true'}
                                 for a in apps.split(',') if a]})
        return out

    def test_production_controller_produces_each_fixture_reply(self):
        produced = [c for c in FIXTURE['cases'] if 'controllerInputs' in c]
        self.assertGreaterEqual(len(produced), 13)
        for c, got in zip(produced, self.run_controller([c['controllerInputs'] for c in produced])):
            with self.subTest(c['name']):
                want = c['controllerReply']
                for k in ('status', 'complete', 'incompleteReason', 'source', 'sessionStatus', 'apps'):
                    self.assertEqual(got[k], want[k], f"{c['name']}: {k}")

    def test_both_surfaces_reach_the_one_expected_category(self):
        for c in FIXTURE['cases']:
            with self.subTest(c['name']):
                if c['controllerReply'] is not None: self.assertEqual(render_reply(c['controllerReply']), c['expected'], 'reply')
                if c['platform'] is not None: self.assertEqual(render_platform(c['platform']), c['expected'], 'platform')

    def test_sequences_replace_the_whole_set(self):
        for seq in FIXTURE['sequences']:
            for step in seq['steps']:
                with self.subTest(seq['name'] + ':' + str(step.get('case', 'reconnect'))):
                    if step.get('reconnect'):
                        self.assertEqual(step['expected']['category'], 'cannot_confirm', 'a reconnect shows no old cards before the next answer')
                    else:
                        self.assertEqual(step['expected'], self.cases[step['case']]['expected'])

    def test_session_unreleased_is_covered_and_never_empty(self):
        c = self.cases['session_unreleased_cleanup_failed']
        self.assertEqual(c['expected']['category'], 'cannot_confirm')
        self.assertEqual(c['controllerReply']['incompleteReason'], 'session_unreleased')
        self.assertTrue(any('session_unreleased' in r for r in FIXTURE['residualDifferences']))
        self.assertEqual(set(FIXTURE['categories']), {'cards', 'genuine_empty', 'cannot_confirm'})


if __name__ == '__main__':
    unittest.main()
