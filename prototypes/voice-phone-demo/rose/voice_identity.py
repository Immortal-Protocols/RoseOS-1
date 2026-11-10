"""The assistant's name and voice, chosen by the owner in conversation (Nov 9 plan 1D).

The owner can rename the assistant ("I'd like to call you Nova") and describe the voice they want ("a warmer, lower
voice"). A description is matched against measurements of the voices themselves, never their names:
rose/voice_traits.json holds, for each Phonon voice in rose/speaker.py, the provider's metadata (locale and gender)
and the pitch, pitch range, spectral tilt and pace measured from the provider's own samples
(scripts/describe_voices.py). The model turns the owner's words into requested directions relative to the current
voice; this module picks the closest voice the running backend can actually load, applies it, and only then saves it.

Both choices persist in the producer's config directory (rose.remote.config_dir, 0600 identity.json), which the Mac
and the cloud producers share as code, so they survive restarts. Renaming changes only what the assistant calls
itself in conversation: the phone app, pairing, About and any wake word are untouched. Nothing here generates or
clones a voice.
"""
from __future__ import annotations

import json
import math
import threading
import unicodedata
from pathlib import Path

DEFAULT_NAME = 'Rose'
DEFAULT_VOICE = 'zoey'
TRAITS_FILE = Path(__file__).with_name('voice_traits.json')
FILE_NAME = 'identity.json'
MAX_NAME = 24

# One step of a requested change ("lower") and a large one ("much lower"), the scale a miss is measured in, and the
# least difference that counts as a change in the asked direction. Pitch and liveliness in semitones, warmth in dB of
# spectral tilt (warmer is lower), pace in percent of speech time (faster is shorter).
DIMENSIONS = {
    'pitch': {'step': 2.5, 'large': 6.0, 'scale': 2.0, 'least': 0.8, 'more': 'higher', 'less': 'lower'},
    'warmth': {'step': 1.5, 'large': 4.0, 'scale': 1.5, 'least': 0.7, 'more': 'warmer', 'less': 'brighter'},
    'liveliness': {'step': 1.0, 'large': 2.5, 'scale': 1.0, 'least': 0.5, 'more': 'livelier', 'less': 'calmer'},
    'pace': {'step': 4.0, 'large': 10.0, 'scale': 4.0, 'least': 2.0, 'more': 'faster', 'less': 'slower'},
}
# same keeps the current voice's gender (the default: the owner did not ask to change it); either allows both.
GENDERS = ('same', 'female', 'male', 'either')
UNREQUESTED_WEIGHT = 0.25  # keep what the owner did not ask to change close to the current voice
US_ACCENTS = {'', 'any', 'american', 'us', 'u.s.', 'usa', 'en-us', 'general american', 'standard american'}


def load_traits(path: Path = TRAITS_FILE) -> dict:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    voices = data.get('voices')
    if not isinstance(voices, dict) or not voices:
        raise ValueError('voice traits must list voices')
    return data


TRAITS = load_traits()


def deltas(current: dict, candidate: dict) -> dict:
    """How candidate differs from current, in each dimension's own unit; positive is DIMENSIONS[d]['more']."""
    return {
        'pitch': 12 * math.log2(candidate['pitch_hz'] / current['pitch_hz']),
        'warmth': current['brightness_db'] - candidate['brightness_db'],
        'liveliness': candidate['pitch_range_st'] - current['pitch_range_st'],
        'pace': 100 * (current['speech_s'] - candidate['speech_s']) / current['speech_s'],
    }


def _level(value) -> int | None:
    """A requested direction as -2..2 (None when the owner did not ask to change it)."""
    if value in (None, '', 'same', 'any'):
        return None
    for dim in DIMENSIONS.values():
        for sign, word in ((1, dim['more']), (-1, dim['less'])):
            if value == word:
                return sign
            if value == 'much_' + word:
                return 2 * sign
    raise ValueError(f'unknown direction {value!r}')


def request_from(args: dict) -> dict:
    """The tool arguments as a matcher request; raises ValueError for an unusable one."""
    wanted = {}
    for name, dim in DIMENSIONS.items():
        value = args.get(name)
        if value is not None and value not in ('same', dim['more'], dim['less'], 'much_' + dim['more'],
                                               'much_' + dim['less']):
            raise ValueError(f'{name} must be one of same, {dim["less"]}, {dim["more"]}, much_{dim["less"]}, '
                             f'much_{dim["more"]}')
        level = _level(value)
        if level:
            wanted[name] = level
    gender = args.get('gender') or 'same'
    if gender not in GENDERS:
        raise ValueError('gender must be same, female, male or either')
    accent = str(args.get('accent') or '').strip()
    other = [str(q).strip() for q in (args.get('other') or []) if str(q).strip()]
    if not wanted and gender in ('same', 'either') and not accent and not other:
        raise ValueError('Describe how the voice should change.')
    return {'wanted': wanted, 'gender': gender, 'accent': accent, 'other': other}


def match(current: str, request: dict, available, traits: dict = TRAITS) -> list[dict]:
    """Every available voice other than current that can be measured and has the requested gender (same: the
    current voice's), best first, with how well it fits.

    Each entry: voice, score (lower is closer), met and unmet requested aspects, and the measured changes."""
    voices = traits['voices']
    cur = voices[current]
    gender = cur['gender'] if request['gender'] == 'same' else request['gender']
    out = []
    for name in available:
        if name == current or name not in voices:
            continue
        cand = voices[name]
        if gender != 'either' and cand['gender'] != gender:
            continue
        d = deltas(cur, cand)
        switched = cand['gender'] != cur['gender']
        score, met, unmet = 0.0, [], []
        for dim, spec in DIMENSIONS.items():
            level = request['wanted'].get(dim)
            if level is None:
                if not (dim == 'pitch' and switched):  # another gender's pitch is not "a change" to keep small
                    score += UNREQUESTED_WEIGHT * (d[dim] / spec['scale']) ** 2
                continue
            target = math.copysign(spec['large'] if abs(level) == 2 else spec['step'], level)
            score += ((d[dim] - target) / spec['scale']) ** 2
            word = (('much_' if abs(level) == 2 else '') + (spec['more'] if level > 0 else spec['less']))
            (met if d[dim] * level > 0 and abs(d[dim]) >= spec['least'] else unmet).append(word)
        out.append({'voice': name, 'score': round(score, 3), 'met': met, 'unmet': unmet,
                    'changes': describe_changes(d)})
    out.sort(key=lambda e: (len(e['unmet']), e['score']))
    return out


def describe_changes(d: dict) -> list[str]:
    """Measured differences worth saying, as plain words ("2.4 semitones lower")."""
    words = []
    for dim, spec in DIMENSIONS.items():
        value = d[dim]
        if abs(value) < spec['least']:
            continue
        way = spec['more'] if value > 0 else spec['less']
        amount = {'pitch': f'{abs(value):.1f} semitones', 'warmth': f'{abs(value):.1f} dB',
                  'liveliness': f'{abs(value):.1f} semitones of range', 'pace': f'{abs(value):.0f}%'}[dim]
        words.append(f'{amount} {way}' if dim != 'warmth' else f'{way} by {amount} of spectral tilt')
    return words


def valid_name(raw) -> str:
    """The name to use, or ValueError: a short name of letters, with spaces, hyphens, apostrophes or periods."""
    if not isinstance(raw, str):
        raise ValueError('The name must be text.')
    name = ' '.join(unicodedata.normalize('NFC', raw).split())
    if not name or len(name) > MAX_NAME or len(name.split()) > 3:
        raise ValueError(f'Use a short name of at most three words and {MAX_NAME} characters.')
    if not any(c.isalpha() for c in name) or not all(c.isalpha() or c in " -'’." for c in name):
        raise ValueError('A name can use letters, spaces, hyphens, apostrophes and periods.')
    return name


class IdentityStore:
    """identity.json in the producer's config directory: name, voice and the voice before it."""

    def __init__(self, directory: Path | None = None):
        if directory is None:
            from .remote import config_dir
            directory = config_dir()
        self.path = Path(directory) / FILE_NAME

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {'unreadable': True}
        return data if isinstance(data, dict) else {'unreadable': True}

    def write(self, data: dict) -> None:
        from .remote import atomic_write
        atomic_write(self.path, (json.dumps(data, indent=1, sort_keys=True) + '\n').encode('utf-8'))


class VoiceIdentity:
    """The assistant's current name and voice, the tools that change them, and their persistence.

    store None keeps everything in memory (tests, and conductors built without a producer)."""

    def __init__(self, store: IdentityStore | None = None, traits: dict = TRAITS):
        self.store, self.traits = store, traits
        self.lock = threading.Lock()
        saved = store.read() if store else {}
        self.unreadable = bool(saved.get('unreadable'))
        name = saved.get('name')
        try:
            self.name = valid_name(name) if name else DEFAULT_NAME
        except ValueError:
            self.name = DEFAULT_NAME
        voice = saved.get('voice')
        self.voice = voice if voice in traits['voices'] else None  # None: the producer's own choice
        previous = saved.get('previous_voice')
        self.previous = previous if previous in traits['voices'] else None
        self.unavailable: set[str] = set()

    # -------------------------------------------------------------- persistence
    def _save(self, name, voice, previous) -> bool:
        if self.store is None:
            return True
        data = {'name': name, 'voice': voice, 'previous_voice': previous}
        try:
            self.store.write({k: v for k, v in data.items() if v is not None})
        except OSError:
            return False
        return True

    def startup_voice(self, explicit: str | None) -> str:
        """The voice to load at start: an explicit --voice, else the saved choice, else the default."""
        return explicit or self.voice or DEFAULT_VOICE

    def loaded(self, voice: str) -> None:
        """Record the voice the speaker actually loaded at start (a saved one may have failed)."""
        if self.voice is None or voice != self.voice:
            self.voice = voice if voice in self.traits['voices'] else self.voice

    # --------------------------------------------------------------------- tools
    def rename(self, raw) -> dict:
        try:
            name = valid_name(raw)
        except ValueError as exc:
            return {'ok': False, 'reason': 'invalid_name', 'message': str(exc)}
        with self.lock:
            if not self._save(name, self.voice, self.previous):
                return {'ok': False, 'reason': 'not_saved', 'message': 'The name could not be saved; it is unchanged.'}
            old, self.name = self.name, name
        return {'ok': True, 'name': name, 'previous_name': old, 'saved': True,
                'note': (f'Call yourself {name} from now on. Rose\'s phone app, pairing, About and any wake word '
                         'are unchanged.')}

    def change_voice(self, speaker, args: dict) -> dict:
        if speaker is None:
            return {'ok': False, 'reason': 'no_voice', 'message': 'This backend has no voice to change.'}
        current = getattr(speaker, 'voice', None) or self.voice or DEFAULT_VOICE
        if current not in self.traits['voices']:
            return {'ok': False, 'reason': 'unmeasured_voice',
                    'message': 'The current voice has no measurements, so a description cannot be matched to it.'}
        named = args.get('voice')
        if named:
            return self._apply(speaker, current, [{'voice': str(named).lower(), 'met': [], 'unmet': [],
                                                   'changes': None}], {'wanted': {}, 'gender': 'any',
                                                                       'accent': '', 'other': []})
        try:
            request = request_from(args)
        except ValueError as exc:
            return {'ok': False, 'reason': 'invalid_request', 'message': str(exc)}
        available = [v for v in getattr(speaker, 'voices', self.traits['voices']) if v not in self.unavailable]
        ranked = match(current, request, available, self.traits)
        limits = self._limits(request)
        if not request['wanted'] and request['gender'] in ('same', 'either', self._gender(current)):
            # Only an accent or unmeasured qualities: nothing measured to move toward, so keep the voice.
            return {'ok': False, 'reason': 'no_close_match', 'changed': False, 'voice': current, 'limits': limits,
                    'message': 'Nothing in that description can be matched to the available voices; the current '
                               'voice was kept.'}
        if request['gender'] in ('female', 'male') and request['gender'] != self._gender(current):
            if not ranked:
                return {'ok': False, 'reason': 'no_match', 'changed': False, 'voice': current, 'limits': limits,
                        'message': f'No {request["gender"]} voice is available on this backend.'}
            return self._apply(speaker, current, ranked, request, limits)
        if not ranked or not ranked[0]['met']:
            # Nothing of this gender moves in any asked direction: keep the voice and offer the nearest alternative,
            # another gender's only if the owner would accept one.
            wider = match(current, {**request, 'gender': 'either'}, available, self.traits)
            best = next((e for e in wider if e['met']), ranked[0] if ranked else None)
            result = {'ok': False, 'reason': 'no_close_match', 'changed': False, 'voice': current, 'limits': limits,
                      'message': ('No available ' + ('' if request['gender'] == 'either' else
                                                     f'{self._gender(current)} ') + 'voice is '
                                  + ' and '.join(self._words(request['wanted'])) + ' than the current one.')}
            if best is not None:
                result['alternative'] = {'voice': best['voice'], 'gender': self._gender(best['voice']),
                                         'met': best['met'], 'unmet': best['unmet'], 'changes': best['changes']}
                result['message'] += (' The nearest alternative is offered; apply it only if the owner agrees '
                                       '(change_voice with its voice).')
            return result
        return self._apply(speaker, current, ranked, request, limits)

    def revert(self, speaker, to: str = 'previous') -> dict:
        if speaker is None:
            return {'ok': False, 'reason': 'no_voice', 'message': 'This backend has no voice to change.'}
        current = getattr(speaker, 'voice', None) or self.voice or DEFAULT_VOICE
        target = DEFAULT_VOICE if to == 'original' else self.previous
        if target is None or target == current:
            return {'ok': False, 'reason': 'nothing_to_revert', 'changed': False, 'voice': current,
                    'message': 'This is already the ' + ('original' if to == 'original' else 'only chosen') + ' voice.'}
        return self._apply(speaker, current, [{'voice': target, 'met': [], 'unmet': [], 'changes': None}],
                           {'wanted': {}, 'gender': 'any', 'accent': '', 'other': []}, reverted=True)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _words(wanted: dict) -> list[str]:
        return [('much_' if abs(level) == 2 else '') + (DIMENSIONS[dim]['more'] if level > 0 else DIMENSIONS[dim]['less'])
                for dim, level in wanted.items()]

    def _gender(self, voice):
        return self.traits['voices'].get(voice, {}).get('gender')

    def _limits(self, request) -> list[str]:
        """What a description asked for that these voices cannot be matched on."""
        limits = []
        accent = request['accent'].lower()
        if accent not in US_ACCENTS:
            limits.append(f'Every available voice has an American (en-US) accent; none is {request["accent"]}.')
        if request['other']:
            limits.append('Only pitch, warmth, liveliness and pace were measured from the voices\' samples; '
                          + ', '.join(request['other']) + ' could not be matched.')
        return limits

    def _apply(self, speaker, current, ranked, request, limits=None, reverted=False) -> dict:
        skipped = []
        for entry in ranked:
            voice = entry['voice']
            if voice not in self.traits['voices'] and voice not in getattr(speaker, 'voices', ()):
                skipped.append(voice)
                continue
            try:
                speaker.set_voice(voice)
            except Exception:
                self.unavailable.add(voice)
                skipped.append(voice)
                continue
            with self.lock:
                if not self._save(self.name, voice, current):
                    try:
                        speaker.set_voice(current)
                    except Exception:
                        pass
                    return {'ok': False, 'reason': 'not_saved', 'changed': False, 'voice': current,
                            'message': 'The new voice could not be saved, so the current voice was kept.'}
                self.voice, self.previous = voice, current
            changes = entry['changes'] if entry['changes'] is not None else describe_changes(
                deltas(self.traits['voices'][current], self.traits['voices'][voice])) \
                if voice in self.traits['voices'] else []
            result = {'ok': True, 'changed': True, 'voice': voice, 'previous_voice': current, 'saved': True,
                      'match': 'reverted' if reverted else ('close' if not entry['unmet'] else 'partial'),
                      'met': entry['met'], 'unmet': entry['unmet'], 'measured_changes': changes,
                      'note': 'Reply now with one short sentence in this voice so the owner hears it, and ask '
                              'whether it suits them.'}
            if limits:
                result['limits'] = limits
            if skipped:
                result['closer_unavailable'] = skipped
                result['note'] += (' A closer voice could not be loaded on this backend; say this is the closest '
                                   'available one.')
            return result
        return {'ok': False, 'reason': 'unavailable', 'changed': False, 'voice': current,
                'message': 'No matching voice could be loaded on this backend; the current voice was kept.'}


DIRECTIONS = {name: {'type': 'string', 'enum': ['same', d['less'], d['more'], 'much_' + d['less'], 'much_' + d['more']],
                     'description': f'Relative to the current voice; same or omitted keeps it.'}
              for name, d in DIMENSIONS.items()}
TOOLS = [
    {'type': 'function', 'name': 'rename_assistant',
     'description': 'Change the name you call yourself, when the owner gives you one. Saved across restarts. Rose\'s '
                    'app, pairing, About and wake word stay unchanged. Say the name changed only when ok is true.',
     'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name'],
                     'additionalProperties': False}},
    {'type': 'function', 'name': 'change_voice',
     'description': 'Change your voice to the closest available one when the owner describes how it should sound. '
                    'Give only what they asked for, relative to your current voice: pitch lower/higher (deeper is '
                    'lower), warmth warmer/brighter, liveliness calmer/livelier, pace slower/faster, gender, an accent, '
                    'and in other any quality none of these cover (for example raspy). Voices were measured from their '
                    'own samples. voice applies an alternative a previous result offered, once the owner agrees. '
                    'Saved across restarts.',
     'inputSchema': {'type': 'object', 'properties': {
         **DIRECTIONS,
         'gender': {'type': 'string', 'enum': list(GENDERS),
                    'description': 'same (default) keeps the current gender; either only if the owner does not mind.'},
         'accent': {'type': 'string'},
         'other': {'type': 'array', 'items': {'type': 'string'}},
         'voice': {'type': 'string', 'description': 'Only a voice a previous change_voice result offered.'}},
         'additionalProperties': False}},
    {'type': 'function', 'name': 'revert_voice',
     'description': 'Go back to the voice before the last change (previous) or to the original voice (original).',
     'inputSchema': {'type': 'object', 'properties': {'to': {'type': 'string', 'enum': ['previous', 'original']}},
                     'additionalProperties': False}},
]
NAMES = frozenset(t['name'] for t in TOOLS)
POLICY_LINE = ('When the owner names you, use rename_assistant; when they describe how your voice should sound, use '
               'change_voice, and revert_voice to go back. After a successful voice change, reply with one short '
               'sentence so they hear it and ask if it suits them; offer to adjust. Say a name or voice changed only '
               'when the tool returns ok. When it returns unmet, limits or no_close_match, say plainly what could not '
               'be matched and offer the alternative it gives. Do not list voice names unless asked.')


def conversation_policy(base: str, name: str) -> str:
    """The conversation instructions with the assistant's current name."""
    return base.replace('You are Rose,', f'You are {name},', 1) if name != DEFAULT_NAME else base
