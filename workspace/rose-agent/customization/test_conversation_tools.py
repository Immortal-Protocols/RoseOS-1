"""rose.customization (prototypes/voice-phone-demo): the conversation side of Rose's four customization controls,
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
    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = [], result, error

    def call(self, action, timeout=None, **args):
        self.calls.append({'action': action, **args})
        if self.error:
            raise self.error
        return self.result


class FakePhone:
    def __init__(self, link):
        self.link = link


class CatalogTest(unittest.TestCase):
    def test_tools_are_the_catalog_unchanged(self):
        raw = json.loads(c.CATALOG.read_text(encoding='utf-8'))
        self.assertEqual([t['name'] for t in c.TOOLS], [t['name'] for t in raw])
        self.assertEqual(c.NAMES, {'customization_state', 'customization_home_columns', 'customization_wallpaper',
                                   'customization_font', 'customization_glasses_hook'})
        for tool, spec in zip(c.TOOLS, raw):
            self.assertEqual(tool['inputSchema'], spec['inputSchema'])
            self.assertEqual(tool['description'], spec['description'])
            self.assertEqual(spec['operation'], spec['name'])
            self.assertIs(spec['inputSchema']['additionalProperties'], False)

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
                ('customization_theme', {}, 'Unknown customization tool'),
                ('customization_state', [], 'Arguments must be an object')]:
            with self.assertRaises(c.CustomizationRefused, msg=name) as caught:
                c.validate(name, args)
            self.assertEqual(str(caught.exception), message)


class CallTest(unittest.TestCase):
    def test_sends_the_registered_app_command(self):
        link = FakeLink({'status': 'verified', 'changed': True, 'customization': {'homeColumns': 5}})
        out = c.call(FakePhone(link), 'customization_home_columns', {'columns': 5})
        self.assertEqual(link.calls, [{'action': 'app_command', 'packageName': 'dev.pixel.voice',
                                       'operation': 'customization_home_columns', 'params': {'columns': 5}}])
        self.assertTrue(out['ok'])
        self.assertEqual(out['customization'], {'homeColumns': 5})
        self.assertEqual(c.command('customization_home_columns', {'columns': 5}),
                         {'action': 'app_command', 'packageName': 'dev.pixel.voice',
                          'operation': 'customization_home_columns', 'params': {'columns': 5}})

    def test_refusal_sends_nothing(self):
        link = FakeLink({'status': 'verified'})
        out = c.call(FakePhone(link), 'customization_wallpaper', {'variant': 'neon'})
        self.assertEqual(link.calls, [])
        self.assertEqual((out['ok'], out['status']), (False, 'refused'))

    def test_only_the_phones_own_confirmation_is_success(self):
        for result in [{'status': 'uncertain'}, {'status': 'error', 'reason': 'Unsupported font'}, None, 'odd',
                       {'status': 'not_executed'}]:
            out = c.call(FakePhone(FakeLink(result)), 'customization_font', {'font': 'mono'})
            self.assertFalse(out['ok'], result)
            self.assertTrue(out['message'])
        self.assertEqual(c.call(FakePhone(FakeLink({'status': 'error', 'reason': 'Unsupported font'})),
                                'customization_font', {'font': 'mono'})['message'], 'Unsupported font')
        self.assertTrue(c.call(FakePhone(FakeLink({'status': 'ok'})), 'customization_state', {})['ok'])

    def test_lost_link_is_uncertain_and_not_retried(self):
        link = FakeLink(error=TimeoutError('late'))
        out = c.call(FakePhone(link), 'customization_glasses_hook', {'enabled': True})
        self.assertEqual((out['ok'], out['status'], len(link.calls)), (False, 'uncertain', 1))
        self.assertIn('customization_state', out['message'])

    def test_no_link(self):
        out = c.call(object(), 'customization_state', {})
        self.assertEqual((out['ok'], out['status']), (False, 'customization_unsupported'))

    def test_glasses_state_passes_through(self):
        wear = {'signalAvailable': False, 'source': 'none'}
        out = c.call(FakePhone(FakeLink({'status': 'ok', 'glassesWear': wear})), 'customization_state', {})
        self.assertEqual(out['glassesWear'], wear)


if __name__ == '__main__':
    unittest.main()
