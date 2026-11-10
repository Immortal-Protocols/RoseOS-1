"""Nov 9 plan 1D: the owner names the assistant and describes its voice, entirely in conversation."""
import ast
import json
import os
import re
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rose.tests import test_codex as fixtures  # installs the Linux stand-ins when mlx is absent
from rose import voice_identity as vi

ROOT = Path(__file__).resolve().parents[2]
ALL = ['zoey', 'harper', 'everly', 'juniper', 'hadley', 'delia', 'easton', 'micah', 'miles']


def speaker_voices() -> dict:
    tree = ast.parse((ROOT / 'rose/speaker.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'VOICES')
    return ast.literal_eval(node.value)


class Speaker:
    """A speaker with Speaker's voice surface; set_voice fails for voices this backend cannot load."""

    def __init__(self, voice='zoey', voices=ALL, missing=()):
        self.voice, self.voices, self.missing, self.calls = voice, {v: v for v in voices}, set(missing), []
        self.utterances = []

    def set_voice(self, voice):
        self.calls.append(voice)
        if voice in self.missing or voice not in self.voices:
            raise RuntimeError(f'voice {voice} is not cached and could not be downloaded')
        self.voice = voice

    audible = False

    def utterance(self, rid, started):
        speaker = self

        class Utterance(fixtures.FakeUtterance):
            def add(self, text):
                self.spoken.append((speaker.voice, text))  # the voice each piece is synthesized in
                super().add(text)
        u = Utterance(rid)
        u.spoken = []
        self.utterances.append(u)
        return u

    def stop(self):
        for u in self.utterances:
            u.cancel()


class Grounding(unittest.TestCase):
    """The matcher's data is measured from the provider's samples at a pinned revision, not inferred from names."""

    def test_traits_cover_exactly_the_speaker_catalog_from_the_pinned_provider_samples(self):
        traits = json.loads((ROOT / 'rose/voice_traits.json').read_text())
        src = traits['source']
        self.assertEqual(src['repo'], 'gradium/phonon-voices')
        phonon = (ROOT.parents[1] / 'workspace/models/rose_cloud/rose_cloud/phonon.py').read_text()
        self.assertIn(f"VOICE_REPO = ('gradium/phonon-voices', '{src['revision']}')", phonon)  # the cloud's pin
        self.assertEqual(src['model'], '31m')  # rose/speaker.py speaks Phonon S (31M)
        self.assertIn('phonon-31m-en', (ROOT / 'rose/speaker.py').read_text())
        catalog = speaker_voices()
        self.assertEqual(set(traits['voices']), set(catalog))
        for name, v in traits['voices'].items():
            self.assertEqual(v['voice_id'], catalog[name], name)
            self.assertEqual((v['language'], v['locale']), ('en', 'en-us'), name)
            self.assertIn(v['gender'], ('female', 'male'), name)
            self.assertEqual(list(v['samples']), src['prompts'])
            self.assertTrue(all(re.fullmatch(r'[0-9a-f]{64}', h) for h in v['samples'].values()), name)
            for key in ('pitch_hz', 'pitch_range_st', 'brightness_db', 'speech_s'):
                self.assertIsInstance(v[key], (int, float), (name, key))
            self.assertGreater(v['voiced_frames'], 500, name)
        self.assertEqual({n for n, v in traits['voices'].items() if v['gender'] == 'male'}, {'easton', 'micah', 'miles'})

    def test_octave_errors_are_folded_not_counted_as_range(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('describe_voices', ROOT / 'scripts/describe_voices.py')
        dv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dv)
        fixed = dv.octave_fixed([140, 141, 139, 280, 70, 142, 600])
        self.assertEqual(len(fixed), 6)  # 600 Hz cannot be folded near 140 and is dropped
        self.assertTrue(all(abs(12 * __import__('math').log2(f / 140)) <= 7 for f in fixed))

    def test_the_matcher_reads_measurements_only(self):
        source = (ROOT / 'rose/voice_identity.py').read_text()
        for name in ALL:  # no voice is special-cased by name, except the default
            self.assertNotIn(f"'{name}'", source.replace("DEFAULT_VOICE = 'zoey'", ''), name)
        renamed = {'voices': {f'v{i}': v for i, v in enumerate(vi.TRAITS['voices'].values())}}
        a = vi.match('v0', vi.request_from({'pitch': 'lower', 'warmth': 'warmer'}), list(renamed['voices']), renamed)
        b = vi.match('zoey', vi.request_from({'pitch': 'lower', 'warmth': 'warmer'}), ALL)
        self.assertEqual([e['score'] for e in a], [e['score'] for e in b])  # names change nothing


class Matching(unittest.TestCase):
    def change(self, current, **args):
        identity, speaker = vi.VoiceIdentity(), Speaker(current)
        return identity.change_voice(speaker, args), speaker

    def test_warmer_lower_picks_a_voice_measured_warmer_and_lower(self):
        r, speaker = self.change('zoey', warmth='warmer', pitch='lower')
        self.assertTrue(r['ok'])
        self.assertEqual(r['match'], 'close')
        cur, new = vi.TRAITS['voices']['zoey'], vi.TRAITS['voices'][r['voice']]
        self.assertLess(new['pitch_hz'], cur['pitch_hz'])
        self.assertLess(new['brightness_db'], cur['brightness_db'])
        self.assertEqual(new['gender'], cur['gender'])  # not asked to change
        self.assertEqual(speaker.voice, r['voice'])
        self.assertEqual(set(r['met']), {'lower', 'warmer'})
        self.assertTrue(any('lower' in c for c in r['measured_changes']))

    def test_every_satisfied_direction_moves_the_measurement_that_way(self):
        for current in ALL:
            for dim, spec in vi.DIMENSIONS.items():
                for word in (spec['less'], spec['more']):
                    with self.subTest(current=current, ask=word):
                        r, _ = self.change(current, **{dim: word})
                        if not r['ok']:
                            self.assertEqual(r['reason'], 'no_close_match')
                            self.assertEqual(r['voice'], current)
                            continue
                        d = vi.deltas(vi.TRAITS['voices'][current], vi.TRAITS['voices'][r['voice']])[dim]
                        self.assertGreater(d * (1 if word == spec['more'] else -1), 0, r)
                        self.assertEqual(vi.TRAITS['voices'][r['voice']]['gender'],
                                         vi.TRAITS['voices'][current]['gender'])

    def test_gender_changes_only_when_asked(self):
        r, _ = self.change('zoey', gender='male')
        self.assertTrue(r['ok'])
        self.assertEqual(vi.TRAITS['voices'][r['voice']]['gender'], 'male')
        r, speaker = self.change('easton', warmth='warmer')  # no warmer male voice exists
        self.assertFalse(r['ok'])
        self.assertEqual((r['reason'], speaker.voice, speaker.calls), ('no_close_match', 'easton', []))
        self.assertEqual(r['alternative']['gender'], 'female')
        self.assertIn('apply it only if the owner agrees', r['message'])

    def test_no_close_match_keeps_the_voice_and_offers_the_nearest(self):
        r, speaker = self.change('zoey', pitch='higher')  # the highest female voice
        self.assertFalse(r['ok'])
        self.assertEqual((r['reason'], r['voice'], speaker.calls), ('no_close_match', 'zoey', []))
        self.assertIn('higher', r['message'])
        self.assertNotIn('alternative', r)  # no voice of either gender is higher: nothing to offer
        r, speaker = self.change('easton', warmth='warmer')  # no warmer male voice, but a warmer female one
        self.assertEqual((r['reason'], speaker.calls), ('no_close_match', []))
        self.assertIn('warmer', r['alternative']['met'])

    def test_only_the_offered_alternative_can_be_applied_by_name(self):
        identity, speaker = vi.VoiceIdentity(), Speaker('easton')
        for name in ('miles', 'juniper', 'easton', 'nobody'):  # nothing offered yet
            r = identity.change_voice(speaker, {'voice': name})
            self.assertEqual((r['reason'], speaker.calls), ('not_offered', []), name)
        offer = identity.change_voice(speaker, {'warmth': 'warmer'})  # no warmer male voice: an offer
        alt = offer['alternative']['voice']
        self.assertEqual(identity.change_voice(speaker, {'voice': 'miles' if alt != 'miles' else 'micah'})['reason'],
                         'not_offered')
        applied = identity.change_voice(speaker, {'voice': alt})
        self.assertTrue(applied['ok'])
        self.assertEqual((applied['match'], speaker.voice, applied['unmet']), ('alternative', alt, offer['alternative']['unmet']))
        self.assertEqual(identity.change_voice(speaker, {'voice': alt})['reason'], 'not_offered')  # used once
        self.assertEqual((identity.previous, identity.revert(speaker)['ok'], speaker.voice), ('easton', True, 'easton'))

    def test_unmatchable_qualities_are_named_not_faked(self):
        r, speaker = self.change('zoey', accent='British')
        self.assertFalse(r['ok'])
        self.assertEqual(speaker.calls, [])
        self.assertIn('American (en-US)', r['limits'][0])
        r, _ = self.change('zoey', warmth='warmer', other=['raspy'])
        self.assertTrue(r['ok'])
        self.assertIn('raspy could not be matched', r['limits'][0])
        r, _ = self.change('zoey', accent='American', warmth='warmer')
        self.assertNotIn('limits', r)

    def test_no_voice_moving_the_wrong_way_is_applied_or_offered(self):
        for current in ALL:
            for dim, spec in vi.DIMENSIONS.items():
                for word in (spec['less'], spec['more'], 'much_' + spec['less'], 'much_' + spec['more']):
                    for extra in ({}, {'warmth': 'warmer'}, {'pitch': 'lower'}):
                        args = {**extra, dim: word}
                        with self.subTest(current=current, args=args):
                            r, _ = self.change(current, **args)
                            voice = r['voice'] if r['ok'] else (r.get('alternative') or {}).get('voice')
                            if voice is None:
                                continue
                            d = vi.deltas(vi.TRAITS['voices'][current], vi.TRAITS['voices'][voice])
                            for k, w in args.items():
                                sign = 1 if w.replace('much_', '') == vi.DIMENSIONS[k]['more'] else -1
                                self.assertFalse(d[k] * sign < 0 and abs(d[k]) >= vi.DIMENSIONS[k]['least'], (r, k))

    def test_a_much_request_needs_a_full_step(self):
        r, speaker = self.change('easton', pitch='much_lower')  # the male voices are within a semitone
        self.assertFalse(r['ok'])
        self.assertEqual(speaker.calls, [])
        r, _ = self.change('zoey', pitch='much_lower')
        if r['ok']:
            d = vi.deltas(vi.TRAITS['voices']['zoey'], vi.TRAITS['voices'][r['voice']])['pitch']
            self.assertLessEqual(d, -vi.DIMENSIONS['pitch']['step'])

    def test_warmer_lower_from_easton_is_not_a_much_brighter_voice(self):
        r, speaker = self.change('easton', warmth='warmer', pitch='lower')
        self.assertNotEqual(speaker.voice, 'micah')  # 5.5 dB brighter: the opposite of warmer
        self.assertFalse(r['ok'])

    def test_asking_for_the_current_gender_says_so(self):
        r, speaker = self.change('zoey', gender='female')
        self.assertEqual((r['reason'], speaker.calls), ('already', []))
        self.assertIn('already female', r['message'])

    def test_same_keeps_that_aspect(self):
        r, _ = self.change('zoey', warmth='warmer', pitch='same')
        self.assertTrue(r['ok'])
        d = vi.deltas(vi.TRAITS['voices']['zoey'], vi.TRAITS['voices'][r['voice']])
        self.assertLessEqual(abs(d['pitch']), vi.DIMENSIONS['pitch']['step'])
        self.assertGreater(d['warmth'], 0)

    def test_a_much_request_offers_a_same_gender_voice_that_moves_part_way(self):
        r, speaker = self.change('delia', pitch='much_higher')
        self.assertEqual((r['reason'], speaker.calls), ('no_close_match', []))
        alt = r['alternative']
        self.assertEqual(alt['gender'], 'female')
        self.assertGreater(vi.deltas(vi.TRAITS['voices']['delia'], vi.TRAITS['voices'][alt['voice']])['pitch'], 0)
        self.assertNotIn('much_', r['message'])

    def test_another_gender_with_a_pitch_qualifier_is_judged_among_that_gender(self):
        females = sorted(v['pitch_hz'] for v in vi.TRAITS['voices'].values() if v['gender'] == 'female')
        r, _ = self.change('easton', gender='female', pitch='lower')  # "a woman with a deeper voice"
        self.assertTrue(r['ok'])
        self.assertLess(vi.TRAITS['voices'][r['voice']]['pitch_hz'], females[len(females) // 2])
        # The reported change is the real one from the current voice: a woman's voice is higher than Easton's.
        self.assertTrue(any('higher' in c and 'semitones' in c for c in r['measured_changes']), r)
        r, _ = self.change('easton', gender='female', pitch='higher')  # "a woman with a higher voice"
        self.assertGreater(vi.TRAITS['voices'][r['voice']]['pitch_hz'], females[len(females) // 2])
        r, _ = self.change('zoey', gender='male', warmth='warmer')  # "a warmer man's voice": the warmest man
        males = {n: v for n, v in vi.TRAITS['voices'].items() if v['gender'] == 'male'}
        self.assertEqual(r['voice'], min(males, key=lambda n: males[n]['brightness_db']))

    def test_a_kept_aspect_is_kept_from_the_current_voice_across_a_gender_change(self):
        for current, args in (('micah', {'gender': 'female', 'warmth': 'same'}), ('zoey', {'gender': 'male', 'pace': 'same'}),
                              ('zoey', {'gender': 'male', 'warmth': 'same'}), ('easton', {'gender': 'female', 'pace': 'same'})):
            with self.subTest(current=current, args=args):
                r, _ = self.change(current, **args)
                if not r['ok']:
                    self.assertEqual(r['reason'], 'no_close_match')
                    self.assertIn('keeping the same', r['message'])
                    continue
                kept = next(k for k, v in args.items() if v == 'same')
                d = vi.deltas(vi.TRAITS['voices'][current], vi.TRAITS['voices'][r['voice']])[kept]
                self.assertLessEqual(abs(d), vi.DIMENSIONS[kept]['step'], r)

    def test_keep_pitch_holds_for_either_gender_and_the_fallback(self):
        for current, args in (('harper', {'gender': 'either', 'pitch': 'same', 'warmth': 'brighter', 'pace': 'faster'}),
                              ('zoey', {'gender': 'either', 'pitch': 'same', 'warmth': 'brighter'}),
                              ('zoey', {'pitch': 'same', 'pace': 'faster'}), ('juniper', {'pitch': 'same', 'pace': 'faster'})):
            with self.subTest(current=current, args=args):
                r, _ = self.change(current, **args)
                voice = r['voice'] if r['ok'] else (r.get('alternative') or {}).get('voice')
                if voice is None:
                    continue
                d = vi.deltas(vi.TRAITS['voices'][current], vi.TRAITS['voices'][voice])['pitch']
                if r['ok'] or 'same_pitch' not in r['alternative']['unmet']:  # applied, or offered as keeping it
                    self.assertLessEqual(abs(d), vi.DIMENSIONS['pitch']['step'], r)
                elif args.get('gender') != 'either':  # an offered voice that breaks the keep says so, and keeps
                    # the gender unless the owner does not mind it
                    self.assertEqual(vi.TRAITS['voices'][voice]['gender'], vi.TRAITS['voices'][current]['gender'])

    def test_a_kept_aspect_never_moves_more_than_rose_would_report(self):
        for current in ALL:
            for keep in vi.DIMENSIONS:
                for ask in vi.DIMENSIONS:
                    if ask == keep:
                        continue
                    for word in (vi.DIMENSIONS[ask]['less'], vi.DIMENSIONS[ask]['more']):
                        r, _ = self.change(current, **{keep: 'same', ask: word})
                        if r['ok']:
                            d = vi.deltas(vi.TRAITS['voices'][current], vi.TRAITS['voices'][r['voice']])[keep]
                            self.assertLess(abs(d), vi.DIMENSIONS[keep]['least'], (current, keep, ask, word, r))

    def test_an_asked_gender_change_stays_near_the_current_voice_otherwise(self):
        women = [n for n, v in vi.TRAITS['voices'].items() if v['gender'] == 'female']
        for man in ('easton', 'micah', 'miles'):
            def distance(w):  # the unrequested aspects, from the current voice (pitch excused by the asked change)
                d = vi.deltas(vi.TRAITS['voices'][man], vi.TRAITS['voices'][w])
                return sum((d[k] / vi.DIMENSIONS[k]['scale']) ** 2 for k in ('warmth', 'liveliness', 'pace'))
            pick = vi.VoiceIdentity().change_voice(Speaker(man), {'gender': 'female'})['voice']
            self.assertEqual(pick, min(women, key=distance), man)

    def test_a_revert_from_an_unmeasured_voice_reports_success(self):
        identity = vi.VoiceIdentity()
        identity.previous = 'miles'
        speaker = Speaker('NbpkqMVS3CJeq2j8', voices=ALL + ['NbpkqMVS3CJeq2j8'])
        r = identity.revert(speaker)
        self.assertTrue(r['ok'])
        self.assertEqual((speaker.voice, r['measured_changes']), ('miles', []))

    def test_keep_blocked_message_names_only_what_blocks(self):
        for current in ALL:
            r, _ = self.change(current, pitch='lower', liveliness='same', pace='same')
            if r['ok'] or 'you asked to keep' not in r['message']:
                continue
            fits = [e for e in vi.match(current, vi.request_from({'pitch': 'lower', 'liveliness': 'same',
                                                                  'pace': 'same'}), ALL) if e['met']]
            for kept in ('liveliness', 'pace'):
                if kept in r['message']:  # every fitting voice really changes it
                    self.assertTrue(all('same_' + kept in e['against'] for e in fits), (current, kept, r))

    def test_a_keep_that_blocks_everything_still_offers_the_nearest(self):
        r, speaker = self.change('zoey', gender='male', pace='same')
        self.assertEqual((r['reason'], speaker.calls), ('no_close_match', []))
        self.assertIn('same_pace', r['alternative']['unmet'])  # disclosed, the owner decides
        males = {n: v for n, v in vi.TRAITS['voices'].items() if v['gender'] == 'male'}
        zoey = vi.TRAITS['voices']['zoey']['speech_s']
        self.assertEqual(r['alternative']['voice'], min(males, key=lambda n: abs(males[n]['speech_s'] - zoey)))

    def test_other_given_as_a_string_is_one_quality(self):
        r, _ = self.change('zoey', warmth='warmer', other='raspy')
        self.assertIn('raspy could not be matched', r['limits'][0])
        self.assertNotIn('r, a', r['limits'][0])

    def test_indifferent_or_negated_accents(self):
        for accent in ('not British', 'American, not British', "doesn't matter", 'no preference', 'normal',
                       'no particular accent'):
            r, _ = self.change('zoey', pitch='lower', accent=accent)
            self.assertNotIn('limits', r, accent)
        r, _ = self.change('zoey', pitch='lower', accent='not American')
        self.assertIn('limits', r)

    def test_accent_alternatives_negation_and_indifference(self):
        for accent in ('American or British', 'British or American', 'more American', 'a slightly more American accent',
                       'not Scottish or Irish', 'the one you have', 'not too strong', 'not a British accent',
                       'without a Southern accent', 'not an English accent', 'neither British nor Irish',
                       "I don't mind", 'American or British, either is fine'):
            r, speaker = self.change('zoey', accent=accent)
            self.assertEqual((r['reason'], speaker.calls), ('already', []), accent)
            r, _ = self.change('zoey', pitch='lower', accent=accent)
            self.assertNotIn('limits', r, accent)
        # Indifference never hides an accent that was named; a negation never hides one named after it.
        for accent in ('British or Australian, either is fine', 'not American, either British or Irish',
                       'a normal British accent', "British, if you don't mind", 'Irish in particular',
                       "I don't mind British", 'Southern American', 'not American or British', 'no, British'):
            r, _ = self.change('zoey', pitch='lower', accent=accent)
            self.assertIn('none is ' + accent, r['limits'][0], accent)
            r, _ = self.change('zoey', accent=accent)
            self.assertNotEqual(r['reason'], 'already', accent)

    def test_either_gender_keep_blocked_offer_may_be_another_gender(self):
        r = vi.VoiceIdentity().change_voice(Speaker('miles'), {'liveliness': 'calmer', 'pitch': 'same',
                                                               'gender': 'either'})
        self.assertFalse(r['ok'])
        self.assertIn('alternative', r)  # the voice the message says fits is offered
        self.assertIn('same_pitch', r['alternative']['unmet'])

    def test_a_gender_change_cannot_keep_the_pitch_and_says_so(self):
        r, _ = self.change('zoey', gender='male', pitch='same')
        self.assertTrue(r['ok'])
        self.assertEqual(r['match'], 'partial')
        self.assertIn('same_pitch', r['unmet'])

    def test_a_result_judged_among_another_gender_says_so(self):
        r, _ = self.change('easton', gender='female', pitch='lower')
        self.assertEqual(r['judged_among'], 'female')
        self.assertIn('among female voices', r['note'])
        r, _ = self.change('zoey', warmth='warmer')
        self.assertNotIn('judged_among', r)

    def test_an_asked_gender_alternative_is_never_another_gender(self):
        r, _ = self.change('zoey', gender='male', pitch='higher')  # no man's voice is higher than a typical man's
        self.assertFalse(r['ok'])
        males = {n: v for n, v in vi.TRAITS['voices'].items() if v['gender'] == 'male'}
        self.assertEqual(r['alternative']['voice'], max(males, key=lambda n: males[n]['pitch_hz']))  # the highest man
        r, _ = self.change('easton', gender='male', pitch='higher')  # named, the current gender: still only men
        self.assertTrue('alternative' not in r or r['alternative']['gender'] == 'male', r)

    def test_american_accents_are_recognised(self):
        for accent in ('American', 'American English', 'US English', 'a General American accent', 'en-US', 'U.S.',
                       'keep it American', 'same as now', 'America', 'American-sounding', 'an American one', 'en_US'):
            r, speaker = self.change('zoey', accent=accent)
            self.assertEqual((r['reason'], speaker.calls), ('already', []), accent)
            r, _ = self.change('zoey', accent=accent, pitch='lower')
            self.assertTrue(r['ok'], accent)
            self.assertNotIn('limits', r, accent)
        r, _ = self.change('zoey', accent='British English', pitch='lower')
        self.assertIn('none is British English', r['limits'][0])
        for accent in ('English', 'an English accent', 'Standard English'):  # usually England's, which no voice has
            r, _ = self.change('zoey', accent=accent, pitch='lower')
            self.assertIn('none is ' + accent, r['limits'][0], accent)
            r, _ = self.change('zoey', accent=accent)
            self.assertNotEqual(r['reason'], 'already', accent)

    def test_an_offer_answers_only_the_next_call_and_alone(self):
        identity, speaker = vi.VoiceIdentity(), Speaker('easton')
        alt = identity.change_voice(speaker, {'warmth': 'warmer'})['alternative']['voice']
        mixed = identity.change_voice(speaker, {'voice': alt, 'pace': 'slower'})
        self.assertEqual((mixed['reason'], speaker.calls), ('invalid_request', []))
        self.assertTrue(identity.change_voice(speaker, {'voice': alt})['ok'])  # the offer still stood
        identity, speaker = vi.VoiceIdentity(), Speaker('easton')
        alt = identity.change_voice(speaker, {'warmth': 'warmer'})['alternative']['voice']
        identity.change_voice(speaker, {'accent': 'British'})  # another result in between
        self.assertEqual(identity.change_voice(speaker, {'voice': alt})['reason'], 'not_offered')

    def test_a_voice_that_failed_to_load_is_retried_later_or_once_it_loads(self):
        best = vi.match('zoey', vi.request_from({'warmth': 'warmer'}), ALL)[0]['voice']
        identity, speaker = vi.VoiceIdentity(), Speaker('zoey', missing={best})
        self.assertNotEqual(identity.change_voice(speaker, {'warmth': 'warmer'})['voice'], best)
        speaker.missing.clear()
        speaker.voice = 'zoey'
        self.assertNotEqual(identity.change_voice(speaker, {'warmth': 'warmer'})['voice'], best)  # still skipped
        with patch('rose.voice_identity.time.monotonic', return_value=time.monotonic() + vi.UNAVAILABLE_RETRY_S + 1):
            speaker.voice = 'zoey'
            self.assertEqual(identity.change_voice(speaker, {'warmth': 'warmer'})['voice'], best)
        identity.unavailable[best] = time.monotonic()
        self.assertTrue(identity.adopt('zoey', best))
        self.assertNotIn(best, identity.unavailable)

    def test_invalid_or_empty_requests_change_nothing(self):
        for args in ({}, {'pitch': 'sideways'}, {'gender': 'robot'}, {'pitch': 'same'}):
            r, speaker = self.change('zoey', **args)
            self.assertFalse(r['ok'], args)
            self.assertEqual(speaker.calls, [], args)

    def test_only_voices_the_running_backend_has_are_used(self):
        identity = vi.VoiceIdentity()
        speaker = Speaker('zoey', voices=['zoey', 'harper', 'everly'])  # a cloud config with three files
        r = identity.change_voice(speaker, {'warmth': 'warmer', 'pitch': 'lower'})
        self.assertIn(r['voice'], ('harper', 'everly'))
        self.assertTrue(set(speaker.calls) <= {'harper', 'everly'})

    def test_a_closer_voice_that_will_not_load_is_skipped_and_said(self):
        best = vi.match('zoey', vi.request_from({'warmth': 'warmer', 'pitch': 'lower'}), ALL)[0]['voice']
        identity, speaker = vi.VoiceIdentity(), Speaker('zoey', missing={best})
        r = identity.change_voice(speaker, {'warmth': 'warmer', 'pitch': 'lower'})
        self.assertTrue(r['ok'])
        self.assertNotEqual(r['voice'], best)
        self.assertEqual(r['closer_unavailable'], [best])
        self.assertIn('closest available', r['note'])
        self.assertIn(best, identity.unavailable)
        speaker = Speaker('zoey', missing=set(ALL) - {'zoey'})
        r = vi.VoiceIdentity().change_voice(speaker, {'warmth': 'warmer'})
        self.assertFalse(r['ok'])
        self.assertEqual((r['reason'], speaker.voice), ('unavailable', 'zoey'))

    def test_when_every_fitting_voice_fails_nothing_unfitting_is_applied(self):
        fitting = [e['voice'] for e in vi.match('zoey', vi.request_from({'warmth': 'warmer'}), ALL)
                   if e['met'] and not e['against']]
        speaker = Speaker('zoey', missing=set(fitting))
        r = vi.VoiceIdentity().change_voice(speaker, {'warmth': 'warmer'})
        self.assertFalse(r['ok'])
        self.assertEqual((r['reason'], speaker.voice), ('unavailable', 'zoey'))
        self.assertEqual(set(speaker.calls), set(fitting))


class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def identity(self):
        return vi.VoiceIdentity(vi.IdentityStore(self.dir))

    def test_name_and_voice_survive_a_restart_and_revert_round_trips(self):
        one, speaker = self.identity(), Speaker('zoey')
        self.assertTrue(one.rename("  Nova  ")['ok'])
        changed = one.change_voice(speaker, {'warmth': 'warmer', 'pitch': 'lower'})
        self.assertTrue(changed['ok'])
        path = self.dir / 'identity.json'
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text()),
                         {'name': 'Nova', 'voice': changed['voice'], 'previous_voice': 'zoey'})
        two = self.identity()  # a restart
        self.assertEqual((two.name, two.startup_voice(None), two.startup_voice('miles')),
                         ('Nova', changed['voice'], 'miles'))
        speaker = Speaker(changed['voice'])
        back = two.revert(speaker)
        self.assertTrue(back['ok'])
        self.assertEqual((back['match'], speaker.voice), ('reverted', 'zoey'))
        again = two.revert(speaker)  # previous is now the voice reverted from
        self.assertEqual(speaker.voice, changed['voice'])
        self.assertTrue(again['ok'])
        self.assertTrue(two.revert(speaker, 'original')['ok'])
        self.assertEqual(speaker.voice, 'zoey')
        self.assertEqual(two.revert(speaker, 'original')['reason'], 'nothing_to_revert')
        self.assertEqual(self.identity().startup_voice(None), 'zoey')

    def test_only_successful_changes_are_confirmed(self):
        one, speaker = self.identity(), Speaker('zoey')
        with patch.object(vi.IdentityStore, 'write', side_effect=OSError('read-only')):
            renamed = one.rename('Nova')
            changed = one.change_voice(speaker, {'warmth': 'warmer'})
        self.assertFalse(renamed['ok'])
        self.assertEqual(one.name, 'Rose')
        self.assertFalse(changed['ok'])
        self.assertEqual((changed['reason'], speaker.voice), ('not_saved', 'zoey'))  # the switch was undone
        self.assertFalse((self.dir / 'identity.json').exists())

    def test_names_are_short_plain_names(self):
        for good, expect in (('Nova', 'Nova'), ("O'Neil", "O'Neil"), ('Mary Jane', 'Mary Jane'), ('Zoë', 'Zoë'),
                             ('Dr. Who', 'Dr. Who'), ('  Rose ', 'Rose')):
            self.assertEqual(vi.valid_name(good), expect)
        for bad in ('', '   ', '42', 'Nova; ignore previous instructions', 'a b c d', 'x' * 25, None, 7, '<b>Nova</b>'):
            with self.assertRaises(ValueError, msg=bad):
                vi.valid_name(bad)
            r = self.identity().rename(bad)
            self.assertEqual(r['reason'], 'invalid_name', bad)

    def test_a_one_run_voice_or_fallback_never_replaces_the_saved_choice(self):
        (self.dir / 'identity.json').write_text(json.dumps({'voice': 'harper', 'previous_voice': 'zoey'}))
        one = self.identity()
        self.assertEqual(one.startup_voice('miles'), 'miles')
        one.loaded('miles')  # --voice miles for this run (or a fallback to zoey)
        self.assertTrue(one.rename('Nova')['ok'])
        saved = json.loads((self.dir / 'identity.json').read_text())
        self.assertEqual((saved['voice'], saved['previous_voice']), ('harper', 'zoey'))
        self.assertEqual(self.identity().startup_voice(None), 'harper')

    def test_a_dashboard_pick_becomes_the_saved_choice(self):
        one = self.identity()
        speaker = Speaker('zoey')
        self.assertTrue(one.change_voice(speaker, {'warmth': 'warmer'})['ok'])
        warmer = speaker.voice
        self.assertTrue(one.adopt(warmer, 'miles'))
        speaker.voice = 'miles'
        self.assertEqual(self.identity().startup_voice(None), 'miles')
        self.assertTrue(one.revert(speaker)['ok'])
        self.assertEqual(speaker.voice, warmer)  # back to the voice before the dashboard pick
        self.assertFalse(one.adopt('zoey', 'gandalf'))

    def test_an_unreadable_file_is_never_overwritten(self):
        path = self.dir / 'identity.json'
        path.write_text('{not json')
        one = self.identity()
        r = one.rename('Nova')
        self.assertFalse(r['ok'])
        self.assertIn('could not be read', r['message'])
        speaker = Speaker('zoey')
        self.assertFalse(one.change_voice(speaker, {'warmth': 'warmer'})['ok'])
        self.assertEqual((speaker.voice, path.read_text()), ('zoey', '{not json'))

    def test_a_saved_file_with_odd_types_falls_back_without_crashing(self):
        for saved in ({'voice': ['zoey']}, {'previous_voice': {}}, {'voice': 7, 'name': ['Nova']}):
            (self.dir / 'identity.json').write_text(json.dumps(saved))
            one = self.identity()
            self.assertEqual((one.name, one.startup_voice(None)), ('Rose', 'zoey'), saved)

    def test_reverting_to_the_voice_this_run_already_speaks_saves_it(self):
        (self.dir / 'identity.json').write_text(json.dumps({'voice': 'harper', 'previous_voice': 'zoey'}))
        one = self.identity()
        speaker = Speaker('zoey')  # a startup fallback or a one-run --voice zoey
        r = one.revert(speaker, 'original')
        self.assertTrue(r['ok'])
        self.assertEqual((r['changed'], speaker.calls), (False, []))
        self.assertEqual(self.identity().startup_voice(None), 'zoey')

    def test_american_with_a_kept_aspect_is_already(self):
        r = vi.VoiceIdentity().change_voice(Speaker('zoey'), {'accent': 'American', 'pitch': 'same'})
        self.assertEqual(r['reason'], 'already')

    def test_previous_is_never_the_voice_being_applied(self):
        (self.dir / 'identity.json').write_text(json.dumps({'voice': 'harper', 'previous_voice': 'miles'}))
        one = self.identity()
        speaker = Speaker('NbpkqMVS3CJeq2j8', voices=ALL + ['NbpkqMVS3CJeq2j8'])  # a raw --voice id this run
        self.assertTrue(one.revert(speaker)['ok'])
        saved = json.loads((self.dir / 'identity.json').read_text())
        self.assertEqual((saved['voice'], saved['previous_voice']), ('miles', 'harper'))
        self.assertTrue(one.revert(speaker)['ok'])  # still a way back
        self.assertEqual(speaker.voice, 'harper')
        two = self.identity()
        self.assertTrue(two.adopt('miles', 'miles'))
        saved = json.loads((self.dir / 'identity.json').read_text())
        self.assertNotEqual(saved.get('previous_voice'), saved['voice'])

    def test_accent_same_is_no_accent_request(self):
        identity, speaker = vi.VoiceIdentity(), Speaker('zoey')
        r = identity.change_voice(speaker, {'warmth': 'warmer', 'accent': 'same'})
        self.assertTrue(r['ok'])
        self.assertNotIn('limits', r)

    def test_a_damaged_or_foreign_file_falls_back_to_defaults(self):
        (self.dir / 'identity.json').write_text('{not json')
        one = self.identity()
        self.assertEqual((one.name, one.startup_voice(None)), ('Rose', 'zoey'))
        (self.dir / 'identity.json').write_text(json.dumps({'name': 'x' * 99, 'voice': 'gandalf'}))
        two = self.identity()
        self.assertEqual((two.name, two.startup_voice(None)), ('Rose', 'zoey'))


class Conversation(unittest.TestCase):
    """Through CodexConductor's conversation tools, as Codex calls them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = fixtures.Transcripts(self.tmp.name + '/t')
        self.identity = vi.VoiceIdentity(vi.IdentityStore(Path(self.tmp.name)))
        self.speaker = Speaker('zoey')
        self.engine = fixtures.CodexConductor(fixtures.RealPhone(), fixtures.Bus(), self.speaker, self.store,
                                              client_factory=fixtures.FakeCodex, identity=self.identity)
        self.engine.start()

    def tearDown(self):
        self.engine.stop()
        self.store.close()
        self.tmp.cleanup()

    def tool(self, tool_name, **args):
        s = self.engine.sessions['conversation']
        e = {"callId": "ipc-" + tool_name, "params": {"tool": tool_name, "arguments": args, "turnId": s.turn,
                                                 "callId": "tool-" + tool_name}}
        self.engine._tool(s, e)
        return json.loads(self.engine.client.results[-1]['result']['contentItems'][0]['text'])

    def opened_instructions(self):
        return [p['instructions'] for op, _, p in self.engine.client.calls if op == 'open']

    def test_tools_and_policy_are_offered(self):
        opened = next(p for op, _, p in self.engine.client.calls if op == 'open')
        names = {t['name'] for t in opened['tools']}
        self.assertTrue({'rename_assistant', 'change_voice', 'revert_voice'} <= names)
        self.assertIn('change_voice', opened['instructions'])
        self.assertIn('Do not list voice names', opened['instructions'])
        self.assertTrue(opened['instructions'].startswith('You are Rose,'))

    def test_rename_is_confirmed_saved_and_used_by_the_next_thread(self):
        r = self.tool('rename_assistant', name='Nova')
        self.assertTrue(r['ok'])
        self.assertIn('Call yourself Nova', r['note'])
        self.assertEqual(json.loads((Path(self.tmp.name) / 'identity.json').read_text())['name'], 'Nova')
        self.engine.reset()  # a new thread (as after a restart)
        self.assertTrue(self.opened_instructions()[-1].startswith('You are Nova,'))
        self.assertIn('Rose', self.opened_instructions()[-1])  # the rest of the policy is unchanged

    def test_the_reply_after_a_voice_change_is_spoken_in_the_new_voice(self):
        s = self.engine.sessions['conversation']
        self.engine._user_turn('Could you use a warmer, lower voice?')
        self.assertTrue(fixtures.wait(lambda: s.turn is not None))
        self.engine._event({'event': 'delta', 'session': s.name, 'turnId': s.turn, 'delta': 'Sure. '})
        r = self.tool('change_voice', warmth='warmer', pitch='lower')
        self.assertTrue(r['ok'])
        self.engine._event({'event': 'delta', 'session': s.name, 'turnId': s.turn,
                            'delta': 'How does this sound?'})
        spoken = self.speaker.utterances[-1].spoken
        self.assertEqual(spoken, [('zoey', 'Sure. '), (r['voice'], 'How does this sound?')])
        self.assertIn('one short sentence', r['note'])
        back = self.tool('revert_voice')
        self.assertTrue(back['ok'])
        self.assertEqual(self.speaker.voice, 'zoey')

    def test_a_new_conversation_has_no_standing_offer(self):
        self.speaker.voice = 'easton'
        self.assertIn('alternative', self.tool('change_voice', warmth='warmer'))
        self.engine.reset()
        self.assertIsNone(self.identity.offered)

    def test_a_change_that_finishes_after_the_bridge_cancelled_it_becomes_a_note(self):
        s = self.engine.sessions['conversation']
        self.engine._event({'event': 'tool_cancelled', 'callId': 'ipc-change_voice'})  # barge-in during the load
        r = self.tool('change_voice', warmth='warmer')
        self.assertTrue(r['ok'])
        notes = [n for c, n in self.engine._late_notes if c == s.conversation]
        self.assertEqual(len(notes), 1)
        self.assertIn('change_voice call that was answered as cancelled finished: {', notes[0])
        self.assertIn('"changed": true', notes[0])
        self.assertIn('ask whether it suits them', notes[0])
        # Not cancelled: no note (the model saw the result).
        self.engine._late_notes = []
        self.tool('rename_assistant', name='Nova')
        self.assertEqual(self.engine._late_notes, [])

    def test_a_cancelled_change_note_keeps_limits_and_the_offered_alternative(self):
        s = self.engine.sessions['conversation']
        self.engine._event({'event': 'tool_cancelled', 'callId': 'ipc-change_voice'})
        self.tool('change_voice', pitch='lower', other=['raspy'])
        note = [n for c, n in self.engine._late_notes if c == s.conversation][-1]
        self.assertIn('raspy could not be matched', note)
        self.engine._late_notes = []
        self.speaker.voice = 'easton'
        self.engine._event({'event': 'tool_cancelled', 'callId': 'ipc-change_voice'})
        self.tool('change_voice', warmth='warmer')
        note = [n for c, n in self.engine._late_notes if c == s.conversation][-1]
        self.assertIn('"alternative"', note)
        self.assertIn(self.identity.offered['voice'], note)

    def test_unavailable_closest_voice_is_reported_not_claimed(self):
        r = self.tool('change_voice', accent='Scottish')
        self.assertFalse(r['ok'])
        self.assertIn('American (en-US)', r['limits'][0])
        self.assertEqual(self.speaker.voice, 'zoey')


class Startup(unittest.TestCase):
    """rose.app: the saved voice is loaded at start; an explicit --voice wins; a voice that will not load falls back."""

    def app(self, argv, saved=None):
        from rose import app as appmod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        if saved:
            (Path(tmp.name) / 'identity.json').write_text(json.dumps(saved))
        from rose import remote
        with patch.dict(os.environ, {remote.CONFIG_ENV: tmp.name}), \
                patch('rose.transcripts.Transcripts', lambda *a, **k: SimpleNamespace()):
            return appmod.Server(appmod.parse_args(argv))

    def test_saved_voice_and_explicit_override(self):
        a = self.app(['--brain', 'codex'], {'name': 'Nova', 'voice': 'everly'})
        self.assertEqual((a.voice, a.identity.name), ('everly', 'Nova'))
        b = self.app(['--brain', 'codex', '--voice', 'miles'], {'voice': 'everly'})
        self.assertEqual(b.voice, 'miles')
        c = self.app([], {'voice': 'everly'})  # the local brain keeps its own default
        self.assertEqual((c.voice, c.identity), ('zoey', None))

    def test_a_saved_voice_that_will_not_load_falls_back_to_the_default(self):
        a = self.app(['--brain', 'codex'], {'voice': 'everly'})
        loads = []

        def load(voice):
            loads.append(voice)
            if voice == 'everly':
                raise RuntimeError('voice everly is not cached and could not be downloaded')
        a.speaker = SimpleNamespace(load=load)
        a._load_voice()
        self.assertEqual((loads, a.voice), (['everly', 'zoey'], 'zoey'))
        b = self.app(['--brain', 'codex', '--voice', 'everly'])
        b.speaker = SimpleNamespace(load=load)
        with self.assertRaises(RuntimeError):  # an explicit choice is not silently replaced
            b._load_voice()


if __name__ == '__main__':
    unittest.main()
