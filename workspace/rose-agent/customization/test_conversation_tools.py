"""rose.customization (prototypes/voice-phone-demo): the conversation side of Rose's customization controls,
offline with a fake phone link. Kept beside the CLI so the offline suite runs it under the existing workspace/** CI
trigger (scripts/tests/test_app_tests_paths.py); stdlib only."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'prototypes/voice-phone-demo'))
from rose import customization as c  # noqa: E402


class FakeLink:
    """Answers the control with result; customization_state reads with each of states in turn (then the last)."""
    def __init__(self, result=None, error=None, states=None):
        self.calls, self.result, self.error, self.states = [], result, error, list(states or [])

    def call(self, action, timeout=None, **args):
        self.calls.append({'action': action, **args})
        if self.error:
            raise self.error
        if args.get('operation') == 'customization_state' and self.states:
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return self.result


class FakePhone:
    def __init__(self, link):
        self.link = link


class CatalogTest(unittest.TestCase):
    def test_tools_are_the_catalog_unchanged(self):
        raw = json.loads(c.CATALOG.read_text(encoding='utf-8'))
        self.assertEqual([t['name'] for t in c.TOOLS], [t['name'] for t in raw])
        self.assertEqual(c.NAMES, {'customization_state', 'customization_home_columns', 'customization_wallpaper',
                                   'customization_font', 'customization_glasses_hook', 'customization_theme'})
        for tool, spec in zip(c.TOOLS, raw):
            self.assertEqual(tool['inputSchema'], spec['inputSchema'])
            self.assertEqual(tool['description'], spec['description'])
            self.assertEqual(spec['operation'], spec['name'])
            self.assertIs(spec['inputSchema']['additionalProperties'], False)

    def test_themes_are_the_shared_definition(self):
        defined = json.loads((Path(__file__).resolve().parents[3] / 'themes/rose-themes.json').read_text())
        themes = c.SPECS['customization_theme']['inputSchema']['properties']['theme']['enum']
        self.assertEqual(themes, [t['id'] for t in defined['themes']])
        self.assertIn('customization_theme', c.POLICY_LINE)

    def test_wallpaper_is_oxblood_only(self):
        variants = c.SPECS['customization_wallpaper']['inputSchema']['properties']['variant']['enum']
        self.assertEqual(variants, ['classic', 'garnet', 'ember', 'velvet', 'dusk'])
        with self.assertRaises(c.CustomizationRefused):
            c.validate('customization_wallpaper', {'variant': 'teal'})


class ValidateTest(unittest.TestCase):
    def test_accepted(self):
        for name, args in [('customization_state', {}), ('customization_home_columns', {'columns': 0}),
                           ('customization_home_columns', {'columns': 8}), ('customization_font', {'font': 'serif'}),
                           ('customization_glasses_hook', {'enabled': False}),
                           ('customization_glasses_hook', {'action': 'app', 'packageName': 'com.example.notes'})]:
            self.assertEqual(c.validate(name, args), args, name)

    def test_refused(self):
        for name, args, message in [
                ('customization_home_columns', {}, 'Missing argument: columns'),
                ('customization_home_columns', {'columns': 9}, 'Unsupported argument: columns'),
                ('customization_home_columns', {'columns': 2}, 'Unsupported argument: columns'),
                ('customization_home_columns', {'columns': True}, 'Invalid argument: columns'),
                ('customization_home_columns', {'columns': 5.0}, 'Invalid argument: columns'),
                ('customization_home_columns', {'columns': 4, 'rows': 2}, 'Unknown argument: rows'),
                ('customization_font', {'font': 'comic'}, 'Unsupported argument: font'),
                ('customization_glasses_hook', {}, 'Supply enabled, action or packageName'),
                ('customization_glasses_hook', {'enabled': 'yes'}, 'Invalid argument: enabled'),
                ('customization_theme', {}, 'Missing argument: theme'),
                ('customization_theme', {'theme': 'ocean'}, 'Unsupported argument: theme'),
                ('customization_aurora', {}, 'Unknown customization tool'),
                ('customization_state', [], 'Arguments must be an object')]:
            with self.assertRaises(c.CustomizationRefused, msg=name) as caught:
                c.validate(name, args)
            self.assertEqual(str(caught.exception), message)


class CallTest(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self._clock, self._sleep = c.clock, c.sleep
        c.clock = lambda: self.now[0]
        c.sleep = lambda s: self.now.__setitem__(0, self.now[0] + s)

    def tearDown(self):
        c.clock, c.sleep = self._clock, self._sleep

    SAVED = {'status': 'verified', 'changed': True, 'customization': {'homeColumns': 5, 'revision': 7},
             'home': {'appliedRevision': 6, 'applied': False, 'effectiveColumns': None}}

    def test_sends_the_registered_app_command(self):
        link = FakeLink(self.SAVED, states=[{'status': 'ok', 'home': {'appliedRevision': 7, 'applied': True, 'effectiveColumns': 5}}])
        out = c.call(FakePhone(link), 'customization_home_columns', {'columns': 5})
        self.assertEqual(link.calls[0], {'action': 'app_command', 'packageName': 'dev.pixel.voice',
                                         'operation': 'customization_home_columns', 'params': {'columns': 5}})
        self.assertEqual(c.command('customization_home_columns', {'columns': 5}),
                         {'action': 'app_command', 'packageName': 'dev.pixel.voice',
                          'operation': 'customization_home_columns', 'params': {'columns': 5}})
        self.assertEqual((out['ok'], out['outcome']), (True, 'applied'))
        self.assertEqual(out['message'], 'Done: Home now shows 5 columns.')

    def test_applied_reports_what_fits_not_the_request(self):
        saved = dict(self.SAVED, customization={'homeColumns': 8, 'revision': 7})
        link = FakeLink(saved, states=[{'status': 'ok', 'home': {'appliedRevision': 7, 'applied': True, 'effectiveColumns': 5}}])
        out = c.call(FakePhone(link), 'customization_home_columns', {'columns': 8})
        self.assertEqual(out['message'], 'Done: Home now shows 5 columns. That is as many as fit this screen.')

    def test_saved_but_not_drawn_is_not_success(self):
        never = {'status': 'ok', 'home': {'appliedRevision': 6, 'applied': False, 'effectiveColumns': None}}
        link = FakeLink(dict(self.SAVED, customization={'wallpaper': 'ember', 'revision': 7}), states=[never])
        out = c.call(FakePhone(link), 'customization_wallpaper', {'variant': 'ember'})
        self.assertEqual((out['ok'], out['outcome']), (False, 'saved_not_shown'))
        self.assertIn('not shown it yet', out['message'])
        polls = [x for x in link.calls if x.get('operation') == 'customization_state']
        self.assertTrue(1 <= len(polls) <= int(c.APPLY_WAIT_S / c.APPLY_POLL_S) + 1, len(polls))

    def test_an_older_drawn_revision_is_not_this_change(self):
        older = {'status': 'ok', 'home': {'appliedRevision': 6, 'applied': True, 'effectiveColumns': 4}}
        out = c.call(FakePhone(FakeLink(self.SAVED, states=[older])), 'customization_home_columns', {'columns': 5})
        self.assertEqual(out['outcome'], 'saved_not_shown')

    def test_already_drawn_in_the_reply_needs_no_poll(self):
        drawn = dict(self.SAVED, home={'appliedRevision': 7, 'applied': True, 'effectiveColumns': 5})
        link = FakeLink(drawn)
        out = c.call(FakePhone(link), 'customization_font', {'font': 'serif'})
        self.assertEqual((out['ok'], out['outcome'], len(link.calls)), (True, 'applied', 1))

    THEMED = {'status': 'verified', 'changed': True,
              'customization': {'theme': 'glacier', 'themeLabel': 'Glacier', 'revision': 9},
              'home': {'appliedRevision': 8, 'applied': False}, 'system': {'shown': 'oxblood', 'applied': False}}

    def test_a_theme_is_done_only_when_home_and_the_system_show_it(self):
        home_only = {'status': 'ok', 'home': {'appliedRevision': 9, 'applied': True}, 'system': {'shown': 'oxblood', 'applied': False}}
        both = {'status': 'ok', 'home': {'appliedRevision': 9, 'applied': True}, 'system': {'shown': 'glacier', 'applied': True}}
        link = FakeLink(self.THEMED, states=[home_only, both])
        out = c.call(FakePhone(link), 'customization_theme', {'theme': 'glacier'})
        self.assertEqual(link.calls[0]['params'], {'theme': 'glacier'})
        self.assertEqual((out['ok'], out['outcome']), (True, 'applied'))
        self.assertEqual(out['message'], 'Done: Rose is now Glacier everywhere.')
        self.assertEqual(out['system'], both['system'])

    def test_a_theme_home_shows_but_the_system_never_confirms_is_not_done(self):
        home_only = {'status': 'ok', 'home': {'appliedRevision': 9, 'applied': True}, 'system': {'shown': 'oxblood', 'applied': False}}
        out = c.call(FakePhone(FakeLink(self.THEMED, states=[home_only])), 'customization_theme', {'theme': 'glacier'})
        self.assertEqual((out['ok'], out['outcome']), (False, 'shown_on_home'))
        self.assertIn('have not confirmed', out['message'])

    def test_a_theme_the_system_acknowledges_for_another_value_is_not_done(self):
        wrong = {'status': 'ok', 'home': {'appliedRevision': 9, 'applied': True}, 'system': {'shown': 'oxblood', 'applied': True}}
        out = c.call(FakePhone(FakeLink(self.THEMED, states=[wrong])), 'customization_theme', {'theme': 'glacier'})
        self.assertEqual(out['outcome'], 'shown_on_home')

    def test_a_theme_not_drawn_on_home_is_saved_not_shown(self):
        never = {'status': 'ok', 'home': {'appliedRevision': 8, 'applied': False}, 'system': {'shown': 'glacier', 'applied': True}}
        out = c.call(FakePhone(FakeLink(self.THEMED, states=[never])), 'customization_theme', {'theme': 'glacier'})
        self.assertEqual((out['ok'], out['outcome']), (False, 'saved_not_shown'))

    def test_an_oxblood_wallpaper_refused_in_glacier_is_rejected(self):
        refused = {'status': 'error', 'reason': 'That wallpaper belongs to Oxblood; Glacier draws its own. Switch to Oxblood first'}
        out = c.call(FakePhone(FakeLink(refused)), 'customization_wallpaper', {'variant': 'velvet'})
        self.assertEqual((out['ok'], out['outcome']), (False, 'rejected'))
        self.assertIn('Switch to Oxblood first', out['message'])

    def test_hook_setting_is_saved_not_applied(self):
        link = FakeLink({'status': 'verified', 'customization': {'revision': 3}, 'glassesWear': {'source': 'bluetooth_audio_connection'}})
        out = c.call(FakePhone(link), 'customization_glasses_hook', {'enabled': True})
        self.assertEqual((out['ok'], out['outcome'], len(link.calls)), (True, 'saved', 1))
        self.assertEqual(out['glassesWear'], {'source': 'bluetooth_audio_connection'})

    def test_refusal_sends_nothing(self):
        link = FakeLink({'status': 'verified'})
        out = c.call(FakePhone(link), 'customization_wallpaper', {'variant': 'neon'})
        self.assertEqual(link.calls, [])
        self.assertEqual((out['ok'], out['status'], out['outcome']), (False, 'refused', 'rejected'))
        self.assertIn('Nothing was sent', out['message'])

    def test_failure_kinds_are_told_apart(self):
        for result, kind in [({'status': 'offline', 'message': 'Rose Agent is not connected.'}, 'unavailable'),
                             ({'status': 'disconnected'}, 'unavailable'),
                             ({'status': 'timeout'}, 'uncertain'), ({'status': 'uncertain'}, 'uncertain'),
                             (None, 'uncertain'), ('odd', 'uncertain'), ({}, 'uncertain'),
                             ({'status': 'error', 'reason': 'Unsupported font'}, 'rejected'),
                             ({'status': 'not_executed'}, 'rejected'), ({'status': 'routine_busy'}, 'rejected')]:
            out = c.call(FakePhone(FakeLink(result)), 'customization_font', {'font': 'mono'})
            self.assertEqual((out['ok'], out['outcome']), (False, kind), result)
            self.assertTrue(out['message'], result)
        self.assertEqual(c.call(FakePhone(FakeLink({'status': 'error', 'reason': 'Unsupported font'})),
                                'customization_font', {'font': 'mono'})['message'],
                         'The phone refused it: Unsupported font. Nothing changed.')
        self.assertIn('not connected', c.call(FakePhone(FakeLink({'status': 'offline'})), 'customization_font', {'font': 'mono'})['message'])

    def test_state_read_is_ok(self):
        out = c.call(FakePhone(FakeLink({'status': 'ok'})), 'customization_state', {})
        self.assertEqual((out['ok'], out['outcome']), (True, 'applied'))

    def test_lost_link_is_uncertain_and_not_retried(self):
        link = FakeLink(error=TimeoutError('late'))
        out = c.call(FakePhone(link), 'customization_glasses_hook', {'enabled': True})
        self.assertEqual((out['ok'], out['status'], out['outcome'], len(link.calls)), (False, 'uncertain', 'uncertain', 1))
        self.assertIn('customization_state', out['message'])

    def test_no_link(self):
        out = c.call(object(), 'customization_state', {})
        self.assertEqual((out['ok'], out['status'], out['outcome']), (False, 'customization_unsupported', 'unavailable'))

    def test_glasses_state_passes_through(self):
        wear = {'signalAvailable': True, 'source': 'bluetooth_audio_connection'}
        out = c.call(FakePhone(FakeLink({'status': 'ok', 'glassesWear': wear})), 'customization_state', {})
        self.assertEqual(out['glassesWear'], wear)


if __name__ == '__main__':
    unittest.main()
