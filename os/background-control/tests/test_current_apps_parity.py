"""#156/#225 parity: the switcher and Rose's apps render the same current apps from the same platform state
(os/tests/fixtures/current-apps-parity.json, shared with Launcher3 and Home). Each paired scenario holds one platform
state in both consumer input forms: Launcher3's onAutomatedDeviceAppsChanged arguments and the controller's inputs.
The controller side runs the production CurrentApps (compiled, through CurrentAppsParityProbe); the Launcher3 side runs
the reference rule below, which OS Product's own tests check their consumer against. Both must reach the one expected
render. No device, framework or UI is involved."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = json.loads((ROOT/'os/tests/fixtures/current-apps-parity.json').read_text())
CONTROLLER, UID = FIXTURE['controllerPackage'], FIXTURE['controllerUid']
SRC = ROOT/'os/background-control/agent-pip/src/dev/pixel/backgroundcontrol'


def render_reply(status, complete, apps):
    """The common render rule over the controller's current_apps reply."""
    if not complete: return {'kind': 'cannot_confirm'}
    if apps: return {'kind': 'cards', 'apps': sorted(set(apps))}
    return {'kind': 'empty'}


def render_launcher(s):
    """The common render rule over Launcher3's listener arguments (reference): only devices the controller owns with its
    own uid are in scope; more than one, an incomplete one or malformed arrays cannot be confirmed."""
    n = len(s['deviceIds'])
    if not (len(s['ownerPackages']) == len(s['ownerUids']) == len(s['deviceComplete']) == n) \
            or len(s['appDeviceIds']) != len(s['appPackageNames']):
        return {'kind': 'cannot_confirm'}
    owned = [i for i in range(n) if s['ownerPackages'][i] == CONTROLLER and s['ownerUids'][i] == UID]
    if any(d not in s['deviceIds'] for d in s['appDeviceIds']): return {'kind': 'cannot_confirm'}
    if not owned: return {'kind': 'empty'}
    if len(owned) > 1 or not s['deviceComplete'][owned[0]]: return {'kind': 'cannot_confirm'}
    device = s['deviceIds'][owned[0]]
    apps = [p for d, p in zip(s['appDeviceIds'], s['appPackageNames']) if d == device and p != CONTROLLER]
    return {'kind': 'cards', 'apps': sorted(set(apps))} if apps else {'kind': 'empty'}


def line(c):
    def typed(v):
        t, x = v
        if t == 'S': return 'S:' + ','.join(x)
        if t == 'B': return 'B:' + ('true' if x else 'false')
        return f'{t}:{x}'
    platform = '-' if c['platform'] is None else ';'.join(typed(c['platform'][k]) for k in ('generation', 'packageNames', 'userId', 'complete', 'closed'))
    front = '-' if c['front'] is None else ','.join(c['front'])
    return '\t'.join([str(c['locked']).lower(), str(c['session']).lower(), c['status'], str(c['blocked']).lower(),
                      c['target'] or '-', front, platform])


class ParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp()
        subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '8', '-target', '8', '-Xlint:-options',
                        '-d', cls.out, str(SRC/'CurrentApps.java'), str(Path(__file__).parent/'CurrentAppsParityProbe.java')],
                       check=True, capture_output=True)

    def controller(self, inputs):
        r = subprocess.run(['java', '-cp', self.out, 'dev.pixel.backgroundcontrol.CurrentAppsParityProbe', CONTROLLER],
                           input='\n'.join(line(c) for c in inputs) + '\n', capture_output=True, text=True, check=True)
        replies = []
        for l in r.stdout.rstrip('\n').split('\n'):
            status, complete, reason, apps = l.split('\t')
            replies.append((status, complete == 'true', reason, [a for a in apps.split(',') if a]))
        return replies

    def test_paired_inputs_render_identically_on_both_surfaces(self):
        paired = FIXTURE['paired']
        replies = self.controller([s['controller'] for s in paired])
        for s, (status, complete, reason, apps) in zip(paired, replies):
            with self.subTest(s['name']):
                self.assertEqual(render_reply(status, complete, apps), s['render'], 'controller: ' + s['name'])
                self.assertEqual(render_launcher(s['launcher']), s['render'], 'launcher: ' + s['name'])
                if not complete: self.assertTrue(reason, 'an incomplete reply always says why')

    def test_one_sided_failures_never_render_a_genuine_empty(self):
        sided = [s for s in FIXTURE['one_sided'] if 'controller' in s]
        for s, (status, complete, reason, apps) in zip(sided, self.controller([s['controller'] for s in sided])):
            with self.subTest(s['name']):
                self.assertEqual(render_reply(status, complete, apps), s['render'])
                self.assertNotIn('apps', render_reply(status, complete, apps), 'no app cards are rendered from an incomplete answer')

    def test_transitions_replace_the_whole_set(self):
        by_name = {s['name']: s for s in FIXTURE['paired']}
        for t in FIXTURE['transitions']:
            steps = [by_name[n] for n in t['steps']]
            replies = self.controller([s['controller'] for s in steps])
            shown = None
            for s, (status, complete, reason, apps) in zip(steps, replies):
                rendered = render_reply(status, complete, apps)
                self.assertEqual(rendered, s['render'], t['name'] + ': ' + s['name'])
                self.assertEqual(render_launcher(s['launcher']), rendered, t['name'] + ': ' + s['name'])
                shown = rendered.get('apps', [])  # replaced, never merged with the previous step
            self.assertEqual(shown, steps[-1]['render'].get('apps', []))

    def test_the_fixture_covers_the_agreed_cases(self):
        kinds = {s['render']['kind'] for s in FIXTURE['paired']} | {s['render']['kind'] for s in FIXTURE['one_sided']}
        self.assertEqual(kinds, {'cards', 'empty', 'cannot_confirm', 'loading'})
        names = {s['name'] for s in FIXTURE['paired']}
        for required in ('front_and_hidden_apps', 'handover_open_and_empty', 'another_user_activity_omitted', 'malformed_answer',
                         'closed_session', 'unreleased_session_and_its_device', 'another_owner_device_ignored'):
            self.assertIn(required, names)


if __name__ == '__main__':
    unittest.main()
