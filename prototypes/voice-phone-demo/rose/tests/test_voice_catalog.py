"""Catalog integrity and synthetic accent-selection tests. No model, samples or network."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rose.tests import test_codex as fixtures  # Linux stand-ins, not real audio
from rose.tests.test_voice_identity import Speaker
from rose import speaker, voice_catalog as catalog, voice_identity as vi

ROOT = Path(__file__).resolve().parents[2]
OLD = ['zoey', 'harper', 'everly', 'juniper', 'hadley', 'delia', 'easton', 'micah', 'miles']
NEW = {'trevor', 'russell', 'preston', 'maeve', 'freya', 'pippa', 'elodie_rose', 'imogen', 'lottie', 'bryony',
       'alfie', 'hugh', 'edwin', 'spencer', 'toby', 'teddy'}


def synthetic_traits():
    # Deliberately synthetic measurements for control-flow checks, never a production measurement artifact.
    base = {'voice_id': 'test', 'gender': 'female', 'locale': 'en-us', 'category': 'ordinary',
            'pitch_hz': 200.0, 'brightness_db': -10.0, 'pitch_range_st': 5.0, 'speech_s': 15.0}
    return {'voices': {
        'zoey': dict(base),
        'us_low': {**base, 'pitch_hz': 170.0},
        'british': {**base, 'locale': 'en-gb', 'pitch_hz': 198.0},
        'british_low': {**base, 'locale': 'en-gb', 'pitch_hz': 175.0},
        'man': {**base, 'gender': 'male', 'pitch_hz': 125.0},
        'british_man': {**base, 'locale': 'en-gb', 'gender': 'male', 'pitch_hz': 128.0},
        'character': {**base, 'locale': 'en-gb', 'category': 'character'},
    }}


class MemoryStore:
    def __init__(self):
        self.data = {}

    def read(self):
        return dict(self.data)

    def write(self, data):
        self.data = dict(data)


class Catalog(unittest.TestCase):
    def test_catalog_preserves_ids_and_adds_only_sixteen_ordinary_candidates(self):
        voices = catalog.CATALOG['voices']
        self.assertEqual(set(voices), set(OLD) | NEW)
        self.assertEqual(list(voices)[:9], OLD)
        self.assertEqual(len({v['sha256'] for v in voices.values()}), 25)
        for name in OLD:
            self.assertEqual(voices[name]['voice_id'], vi.TRAITS['voices'][name]['voice_id'])
            self.assertEqual(voices[name]['quality'], 'existing')
        for name in NEW:
            self.assertEqual(voices[name]['category'], 'ordinary')
            self.assertEqual(voices[name]['quality'], 'pending_listening')
        self.assertEqual(sum(voices[n]['locale'] == 'en-gb' for n in NEW), 13)
        self.assertEqual(vi.DEFAULT_VOICE, 'zoey')

    def test_rejects_noncommercial_model_or_duplicate_asset(self):
        for mutate in (lambda d: d['source'].update(model='gradium/phonon-40m-en'),
                       lambda d: d['voices']['harper'].update(sha256=d['voices']['zoey']['sha256'])):
            data = copy.deepcopy(catalog.CATALOG)
            mutate(data)
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / 'catalog.json'
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    catalog.load_catalog(path)

    def test_asset_hash_is_verified_without_model_loading(self):
        payload = b'synthetic asset test, not a real voice'
        data = {'voices': {'test': {'voice_id': 'synthetic', 'sha256': hashlib.sha256(payload).hexdigest()}}}
        with tempfile.TemporaryDirectory() as td, patch.object(catalog, 'CATALOG', data):
            path = Path(td) / 'voice'
            path.write_bytes(payload)
            self.assertEqual(catalog.verify_voice_file('synthetic', path), str(path))
            path.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                catalog.verify_voice_file('synthetic', path)
            with self.assertRaisesRegex(ValueError, 'not in'):
                catalog.verify_voice_file('unknown', path)

    def test_cached_and_downloaded_lookup_are_both_pinned_and_verified(self):
        for effects, count in ((['/fake/cached'], 1), ([FileNotFoundError(), '/fake/downloaded'], 2)):
            download = Mock(side_effect=effects)
            verify = Mock(return_value='/verified')
            with patch.dict(sys.modules, {'huggingface_hub': SimpleNamespace(hf_hub_download=download)}), \
                    patch.object(speaker, 'verify_voice_file', verify):
                self.assertEqual(speaker._voice_file(catalog.VOICES['zoey']), '/verified')
            self.assertEqual(download.call_count, count)
            for call in download.call_args_list:
                self.assertEqual(call.kwargs['revision'], catalog.VOICE_REVISION)
            self.assertTrue(download.call_args_list[0].kwargs['local_files_only'])
            verify.assert_called_once()

    def test_bad_cached_bytes_never_fall_back_to_unpinned_download(self):
        download = Mock(return_value='/fake')
        with patch.dict(sys.modules, {'huggingface_hub': SimpleNamespace(hf_hub_download=download)}), \
                patch.object(speaker, 'verify_voice_file', side_effect=ValueError('bad hash')):
            with self.assertRaises(ValueError):
                speaker._voice_file(catalog.VOICES['zoey'])
        download.assert_called_once()

    def test_cloud_mirror_is_current_without_activating_cloud(self):
        subprocess.run([sys.executable, str(ROOT / 'scripts/sync_voice_catalog.py'), '--check'], check=True)

    def test_incomplete_character_corpus_is_explicit_not_a_shorter_pace(self):
        spec = importlib.util.spec_from_file_location('describe_voices', ROOT / 'scripts/describe_voices.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        entry = {'sample_dir': 'samples/31m/en/characters/story_voice', 'gender': None}
        paths = [f'{entry["sample_dir"]}/{p}.ogg' for p in module.PROMPTS]
        self.assertEqual(module.sample_paths(entry, paths), paths)
        with self.assertRaisesRegex(ValueError, 'missing common-corpus'):
            module.sample_paths(entry, paths[:-1])


class ExpandedMeasuredCatalog(unittest.TestCase):
    def test_existing_measurements_are_unchanged_and_new_corpus_matches(self):
        data = vi.TRAITS
        self.assertEqual(set(data['voices']), set(catalog.VOICES))
        self.assertEqual(data['source']['revision'], catalog.VOICE_REVISION)
        self.assertEqual(data['source']['additional_traits_import']['sha256'],
                         '6e92712f8272dcca75d86278fe54e66470e9f45bf7008c31b91a422d6e8d7982')
        for name, value in data['voices'].items():
            self.assertEqual(list(value['samples']), data['source']['prompts'])
            entry = catalog.CATALOG['voices'][name]
            self.assertEqual((value['voice_id'], value['locale'], value['gender']),
                             (entry['voice_id'], entry['locale'], entry['gender']))

    def test_all_twenty_five_voices_preserve_unasked_gender_accent_and_directions(self):
        for current in catalog.VOICES:
            for dimension, spec in vi.DIMENSIONS.items():
                for word in (spec['less'], spec['more'], 'much_' + spec['less'], 'much_' + spec['more']):
                    identity, sp = vi.VoiceIdentity(), Speaker(current, voices=catalog.VOICES)
                    result = identity.change_voice(sp, {dimension: word})
                    if not result['ok']:
                        self.assertEqual(result['reason'], 'no_close_match', (current, dimension, word, result))
                        continue
                    before, after = vi.TRAITS['voices'][current], vi.TRAITS['voices'][sp.voice]
                    self.assertEqual((before['gender'], before['locale']), (after['gender'], after['locale']))
                    delta = vi.deltas(before, after)[dimension]
                    direction = 1 if word.replace('much_', '') == spec['more'] else -1
                    self.assertGreater(delta * direction, 0)
                    threshold = spec['step'] if word.startswith('much_') else spec['least']
                    self.assertGreaterEqual(abs(delta), threshold)

    def test_natural_language_same_accent_is_preserved_during_refinement(self):
        for current in catalog.VOICES:
            for accent in ('keep the same accent', 'same accent', 'keep my current accent', 'unchanged accent'):
                identity, sp = vi.VoiceIdentity(), Speaker(current, voices=catalog.VOICES)
                result = identity.change_voice(sp, {'accent': accent, 'pitch': 'lower'})
                if result['ok']:
                    self.assertEqual(vi.TRAITS['voices'][sp.voice]['locale'],
                                     vi.TRAITS['voices'][current]['locale'], (current, accent, result))
        for accent in ('not the same accent', "I don't want the same accent", 'I don’t want the same accent',
                       'I don‘t want the same accent'):
            requested = vi.request_from({'accent': accent})
            self.assertEqual(vi.request_locales(requested, {'locale': 'en-gb'}), {'en-us'})
        requested = vi.request_from({'accent': 'not Australian, keep the current accent'})
        self.assertEqual(vi.request_locales(requested, {'locale': 'en-gb'}), {'en-gb'})

    def test_contradictory_keep_constraints_never_change_the_current_voice(self):
        for current, data in vi.TRAITS['voices'].items():
            accent = 'American' if data['locale'] == 'en-us' else 'British'
            for description in (f'not {accent}, keep the current accent',
                                f'keep the same accent, not {accent}'):
                for qualifiers in ({}, {'pitch': 'lower'}):
                    identity, sp = vi.VoiceIdentity(), Speaker(current, voices=catalog.VOICES)
                    result = identity.change_voice(sp, {'accent': description, **qualifiers})
                    self.assertFalse(result['ok'], (current, description, result))
                    self.assertEqual((result['voice'], result['changed'], sp.voice, sp.calls),
                                     (current, False, current, []))
                    self.assertNotIn('alternative', result)
            identity, sp = vi.VoiceIdentity(), Speaker(current, voices=catalog.VOICES)
            result = identity.change_voice(sp, {'accent': 'I don’t want the same accent'})
            self.assertTrue(result['ok'], (current, result))
            self.assertNotEqual(result['locale'], data['locale'])

    def test_each_new_voice_survives_saved_restart_and_revert(self):
        for name in NEW:
            store = MemoryStore()
            store.data = {'name': 'Nova', 'voice': name, 'previous_voice': 'zoey'}
            identity = vi.VoiceIdentity(store)
            self.assertEqual((identity.name, identity.startup_voice(None)), ('Nova', name))
            sp = Speaker(name, voices=catalog.VOICES)
            self.assertTrue(identity.revert(sp)['ok'])
            self.assertEqual((sp.voice, store.data['voice']), ('zoey', 'zoey'))

    def test_full_catalog_switches_both_directions_and_excludes_characters(self):
        identity, sp = vi.VoiceIdentity(), Speaker(voices=catalog.VOICES)
        self.assertTrue(identity.change_voice(sp, {'accent': 'british'})['ok'])
        self.assertEqual(vi.TRAITS['voices'][sp.voice]['locale'], 'en-gb')
        self.assertTrue(identity.change_voice(sp, {'accent': 'american'})['ok'])
        self.assertEqual(vi.TRAITS['voices'][sp.voice]['locale'], 'en-us')


class AccentSelection(unittest.TestCase):
    def make(self, current='zoey', available=None, missing=(), store=None):
        traits = synthetic_traits()
        return vi.VoiceIdentity(store=store, traits=traits), Speaker(current, voices=available or traits['voices'],
                                                                   missing=missing)

    def test_accent_only_switch_then_persist_refine_revert(self):
        store = MemoryStore()
        identity, sp = self.make(store=store)
        result = identity.change_voice(sp, {'accent': 'british'})
        self.assertTrue(result['ok'])
        self.assertEqual((sp.voice, result['locale'], store.data['voice']), ('british', 'en-gb', 'british'))
        self.assertIn('Reply now', result['note'])
        restarted = vi.VoiceIdentity(store, traits=synthetic_traits())
        self.assertEqual(restarted.startup_voice(None), 'british')
        refined = restarted.change_voice(sp, {'pitch': 'lower', 'accent': 'same'})
        self.assertTrue(refined['ok'])
        self.assertEqual(sp.voice, 'british_low')
        self.assertTrue(restarted.revert(sp)['ok'])
        self.assertEqual(sp.voice, 'british')
        self.assertTrue(restarted.revert(sp, 'original')['ok'])
        self.assertEqual(sp.voice, 'zoey')

    def test_unspecified_accent_stays_current(self):
        identity, sp = self.make('british')
        result = identity.change_voice(sp, {'pitch': 'lower'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['locale'], 'en-gb')

    def test_exclusions_and_alternatives(self):
        for request, expected in (
                ('not American', {'en-gb'}), ('not British', {'en-us'}),
                ('American or British', {'en-us', 'en-gb'}), ('British rather than American', {'en-gb'}),
                ('not American or British', set()), ('neither British nor American', set()),
                ('please not American or British', set()), ('anything except American or British', set()),
                ('not American English', {'en-gb'}), ('not British English', {'en-us'}),
                ('American or British except American', {'en-gb'}),
                ('British or American except British', {'en-us'}),
                ('neither American nor English', set()),
                ("I don't want American or British", set()), ("I do not want American or British", set()),
                ("I don't mind British or American", {'en-us', 'en-gb'}),
                ('American English', {'en-us'}), ('English', {'en-gb'}),
                ('British English', {'en-gb'}), ('Scottish', set()), ('Southern American', set())):
            self.assertEqual(vi.accent_locales(request), expected, request)
        identity, sp = self.make()
        result = identity.change_voice(sp, {'accent': 'other', 'accent_name': 'not American'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['locale'], 'en-gb')

    def test_unconfigured_unsupported_and_excluded_accents_do_not_change_voice(self):
        for request in ('british', 'Australian', 'not American or British', 'please not American or British',
                        'anything except American or British', 'I do not want American or British'):
            identity, sp = self.make(available=['zoey', 'us_low', 'man'])
            result = identity.change_voice(sp, {'accent': request, 'pitch': 'lower'})
            self.assertFalse(result['ok'])
            self.assertEqual((sp.voice, sp.calls), ('zoey', []))
            self.assertTrue(result['limits'])
            self.assertNotIn('alternative', result)

    def test_exclusions_apply_across_alternatives_in_live_selection(self):
        for current, request, locale in (
                ('zoey', 'American or British except American', 'en-gb'),
                ('british', 'British or American except British', 'en-us')):
            for directions in ({}, {'pitch': 'lower'}):
                identity, sp = self.make(current)
                result = identity.change_voice(sp, {'accent': request, **directions})
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['locale'], locale)
        identity, sp = self.make()
        result = identity.change_voice(sp, {'accent': 'neither American nor English'})
        self.assertFalse(result['ok'])
        self.assertEqual(sp.calls, [])

    def test_failed_british_load_never_falls_back_to_american(self):
        identity, sp = self.make(missing=['british', 'british_low'])
        result = identity.change_voice(sp, {'accent': 'british'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'unavailable')
        self.assertEqual(sp.voice, 'zoey')
        self.assertEqual(set(sp.calls), {'british', 'british_low'})
        self.assertNotIn('alternative', result)
        repeat = identity.change_voice(sp, {'accent': 'british'})
        self.assertFalse(repeat['ok'])
        self.assertEqual(set(sp.calls), {'british', 'british_low'})

    def test_character_is_never_admitted_by_generic_accent_request(self):
        identity, sp = self.make(available=['zoey', 'character'])
        result = identity.change_voice(sp, {'accent': 'british'})
        self.assertFalse(result['ok'])
        self.assertEqual(sp.calls, [])

    def test_gender_and_accent_constraints_both_apply(self):
        identity, sp = self.make()
        result = identity.change_voice(sp, {'gender': 'male', 'accent': 'british'})
        self.assertTrue(result['ok'])
        self.assertEqual(sp.voice, 'british_man')

    def test_save_and_rollback_failure_are_reported_truthfully(self):
        store = MemoryStore()
        identity, sp = self.make(store=store)
        store.write = Mock(side_effect=OSError('synthetic storage failure'))
        sp.missing.add('zoey')  # restoration will fail after the British voice loads
        result = identity.change_voice(sp, {'accent': 'british'})
        self.assertFalse(result['ok'])
        self.assertTrue(result['rollback_failed'])
        self.assertTrue(result['changed'])
        self.assertFalse(result['saved'])
        self.assertEqual((sp.voice, result['voice'], store.data), ('british', 'british', {}))


if __name__ == '__main__':
    unittest.main()
