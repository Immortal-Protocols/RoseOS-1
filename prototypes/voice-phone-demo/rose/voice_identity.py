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
import re
import time
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
UNREQUESTED_WEIGHT = 0.5
UNAVAILABLE_RETRY_S = 300.0  # a voice that failed to load (a download, a network blip) is tried again after this  # keep what the owner did not ask to change close to the current voice
US = frozenset({'american', 'america', 'us', 'usa', 'united', 'states'})
# What follows one of these in its alternative is ruled out ("not a British accent", "American rather than British",
# "any accent except British").
NEGATIONS = frozenset({'not', 'no', 'non', 'without', 'never', 'nor', 'neither', 'doesn', 'don', 'isn', 'aren',
                       'wasn', 'nothing', 'than', 'instead', 'except', 'besides'})
# "doesn't matter", "don't mind": the negation ends there, and what follows is accepted, not refused.
INDIFFERENT = frozenset({'matter', 'matters', 'mind', 'care'})
FILLER = frozenset({
    'accent', 'accents', 'a', 'an', 'the', 'standard', 'general', 'plain', 'neutral', 'north', 'any', 'anything',
    'same', 'none', 'unchanged', 'current', 'usual', 'default', 'regular', 'typical', 'normal', 'particular',
    'preference', 'whatever', 'either', 'change', 'keep', 'it', 'as', 'now', 'is', 'are', 'be', 'one', 'please',
    'like', 'sounding', 'sound', 'sounds', 'voice', 'my', 'your', 'this', 'that', 'fine', 'good', 'ok', 'okay', 'just',
    'still', 'more', 'bit', 'little', 'slight', 'slightly', 'very', 'too', 'quite', 'strong', 'stronger',
    'light', 'lighter', 'mild', 'heavy', 'thick', 'have', 'has', 'had', 'you', 'i', 'me', 'we', 'of', 'with', 'in',
    'to', 'for', 'if', 'would', 'could', 'should', 'want', 'prefer', 'rather', 'kind', 'sort', 'something', 'sure',
    's', 't', 'd', 'm', 'll', 've', 're', 'really', 'much', 'all', 'at', 'else', 'works', 'stay', 'remain', 'other'})


def _alternative(tokens: list[str]):
    """One alternative of an accent clause: True when every voice has it, False when none does, None when it asks for
    no accent at all."""
    asked, refused, negating = set(), set(), False
    for t in tokens:
        if t in NEGATIONS:
            negating = True
        elif t in INDIFFERENT:
            negating = False
        else:
            (refused if negating else asked).add(t)
    if refused & US:  # "not American": an accent no voice has
        return False
    asked -= FILLER
    if asked & US:  # "American English", "US English": English names the language here
        if 'less' in asked:  # "less American": an accent no voice has
            return False
        asked -= US | {'english'}
        return not asked
    asked.discard('less')  # "less strong": no accent named
    return None if not asked else False  # a bare "English" accent usually means England's, which no voice has


ACCENT_NOTE = 'Every available voice has a general American (en-US) accent; none has a regional or other accent.'


def american(accent: str) -> bool:
    """Whether an accent request names the accent every available voice has (US English), or none.

    Clauses (split at punctuation and "but") must all hold; within one, any of its alternatives ("or") may. A negation
    covers the rest of its alternative ("not a British accent"), so a negated accent is not asked for."""
    text = accent.lower().replace('u.s.', 'us').replace('en_us', 'us').replace('en-us', 'us')
    # "anything but British" excludes, unlike "British but warmer", which joins two clauses.
    text = re.sub(r'\b(anything|any\s+accent|any\s+one|anyone|any)\s+but\b', r'\1 except', text)
    for clause in re.split(r'[,;.!?/]|\bbut\b|\bthough\b', text):
        tokens = re.findall(r'[a-z]+', clause)
        alternatives, current = [], []
        for t in tokens:
            if t == 'or':
                alternatives.append(current)
                current = []
            else:
                current.append(t)
        alternatives.append(current)
        results = [_alternative(a) for a in alternatives if a]
        if results and not any(r is not False for r in results):
            return False
    return True


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
    """The tool arguments as a matcher request; raises ValueError for an unusable one. keep lists the aspects the
    owner asked to stay the same."""
    wanted, keep = {}, []
    for name, dim in DIMENSIONS.items():
        value = args.get(name)
        if value is not None and value not in ('same', dim['more'], dim['less'], 'much_' + dim['more'],
                                               'much_' + dim['less']):
            raise ValueError(f'{name} must be one of same, {dim["less"]}, {dim["more"]}, much_{dim["less"]}, '
                             f'much_{dim["more"]}')
        level = _level(value)
        if level:
            wanted[name] = level
        elif value == 'same':
            keep.append(name)
    gender = args.get('gender') or 'same'
    if gender not in GENDERS:
        raise ValueError('gender must be same, female, male or either')
    accent = str(args.get('accent') or '').strip()
    accent_name = str(args.get('accent_name') or '').strip()
    accent_note = False
    if accent.lower() == 'american':
        # The model judged it American. A name with it is said back as what every voice has (a general American
        # accent, no regional one), which is true whether it was "natural American" or "Southern".
        accent, accent_american, accent_note = 'American', True, bool(accent_name)
    elif accent.lower() == 'other':
        accent, accent_american = accent_name or 'another accent', False
    else:  # not one of the schema's values: read the owner's words
        accent = accent or accent_name
        accent_american = american(accent)
    raw = args.get('other') or []
    other = [str(q).strip() for q in ([raw] if isinstance(raw, str) else raw) if str(q).strip()]
    if not wanted and gender in ('same', 'either') and not accent and not other:
        raise ValueError('Describe how the voice should change.')
    return {'wanted': wanted, 'keep': keep, 'gender': gender, 'accent': accent, 'accent_american': accent_american,
            'accent_note': accent_note,
            'other': other}


def match(current: str, request: dict, available, traits: dict = TRAITS) -> list[dict]:
    """Every available voice other than current that can be measured and has the requested gender (same: the
    current voice's), best first, with how well it fits.

    Each entry: voice, score (lower is closer), met and unmet requested aspects, against (requested aspects it moves
    the opposite way, or a kept aspect it changes by more than a step; also unmet), toward (requested aspects it moves
    the asked way at all, even short of a much_ step), and the measured changes. A much_ request is met only by at
    least one full step. When the owner asks for another gender, a requested pitch is judged against that gender's
    voices (a deeper woman's voice), not the current voice of another gender."""
    voices = traits['voices']
    cur = voices[current]
    gender = cur['gender'] if request['gender'] == 'same' else request['gender']
    keep = request.get('keep', [])
    out = []
    for name in available:
        if name == current or name not in voices:
            continue
        cand = voices[name]
        if gender != 'either' and cand['gender'] != gender:
            continue
        real = deltas(cur, cand)  # what the owner will hear change: always the reported measurements
        switched = cand['gender'] != cur['gender']
        # Asking for another gender changes register: every qualifier ("a deeper woman's voice", "a warmer man's")
        # is judged among that gender's voices, from their typical voice, not from the current voice.
        asked_switch = switched and request['gender'] in ('female', 'male')  # the owner asked for this gender
        d = deltas(typical(voices, cand['gender']), cand) if asked_switch else real
        among = d is not real
        score, met, unmet, against, toward = 0.0, [], [], [], []
        for dim, spec in DIMENSIONS.items():
            level = request['wanted'].get(dim)
            if level is None:
                if dim in keep:  # "keep the same" is always about the current voice, whatever the gender
                    if dim == 'pitch' and asked_switch:
                        unmet.append('same_pitch')  # another gender asked for: its pitch cannot be kept; say so
                    else:
                        if abs(real[dim]) >= spec['least']:  # as much as Rose would report as a change
                            unmet.append('same_' + dim)
                            against.append('same_' + dim)
                        score += (real[dim] / spec['scale']) ** 2
                elif not (dim == 'pitch' and asked_switch):  # only an asked gender change excuses its pitch change
                    score += UNREQUESTED_WEIGHT * (real[dim] / spec['scale']) ** 2  # stay near the current voice
                continue
            target = math.copysign(spec['large'] if abs(level) == 2 else spec['step'], level)
            miss = d[dim] - target
            # Going further the asked way is a smaller miss than falling short of it.
            score += (miss / spec['scale'] / (2 if miss * level > 0 else 1)) ** 2
            word = (('much_' if abs(level) == 2 else '') + (spec['more'] if level > 0 else spec['less']))
            enough = spec['step'] if abs(level) == 2 else spec['least']
            if d[dim] * level > 0 and abs(d[dim]) >= spec['least']:
                toward.append(word)
            if d[dim] * level > 0 and abs(d[dim]) >= enough:
                met.append(word)
            else:
                unmet.append(word)
                if d[dim] * level < 0 and abs(d[dim]) >= spec['least']:
                    against.append(word)
        progress = sum(d[dim] * math.copysign(1, level) / DIMENSIONS[dim]['scale']
                       for dim, level in request['wanted'].items())  # how far it goes the asked ways, in steps
        entry = {'voice': name, 'score': round(score, 3), 'met': met, 'unmet': unmet, 'against': against,
                 'toward': toward, 'progress': round(progress, 3), 'changes': describe_changes(real)}
        if among and request['wanted']:
            entry['judged_among'] = cand['gender']  # met/unmet compare with a typical voice of that gender
        out.append(entry)
    out.sort(key=lambda e: (len(e['against']), len(e['unmet']), e['score']))
    return out


def typical(voices: dict, gender: str) -> dict:
    """The median voice of a gender, measure by measure (a reference, not a voice)."""
    group = [v for v in voices.values() if v['gender'] == gender]
    mid = lambda key: sorted(v[key] for v in group)[len(group) // 2]
    return {key: mid(key) for key in ('pitch_hz', 'brightness_db', 'pitch_range_st', 'speech_s')}


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

    An identity.json that exists but cannot be read is never overwritten: changes are then refused, not saved over it.

    store None keeps everything in memory (tests, and conductors built without a producer)."""

    UNREADABLE = (' The saved name and voice file could not be read, so it was left as it is; the producer\'s operator '
                  'can check its config directory.')

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
        voice = saved.get('voice') if isinstance(saved.get('voice'), str) else None
        # The owner's chosen voice as saved (None: never chosen). Only a change the owner made replaces it: a one-run
        # --voice or a startup fallback changes what is spoken, never the choice.
        self.voice = voice if voice in traits['voices'] else None
        previous = saved.get('previous_voice') if isinstance(saved.get('previous_voice'), str) else None
        self.previous = previous if previous in traits['voices'] else None
        self.unavailable: dict[str, float] = {}  # voice -> when it last failed to load (retried after a while)
        self.offered: dict | None = None  # the alternative the last no_close_match offered

    # -------------------------------------------------------------- persistence
    def _save(self, name, voice, previous) -> bool:
        if self.store is None:
            return True
        if self.unreadable:  # never overwrite a saved choice that could not be read
            return False
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
        """The speaker loaded voice at start. The saved choice is kept as is: matching starts from what is spoken."""

    def adopt(self, before: str | None, voice: str) -> bool:
        """A voice chosen outside conversation (the loopback dashboard's /api/voice) becomes the saved choice, so a
        revert and the next start follow what the owner last picked. False when it could not be saved."""
        if voice not in self.traits['voices']:
            return False
        with self.lock:
            previous = before if before in self.traits['voices'] and before != voice else self._fallback_previous(voice)
            if not self._save(self.name, voice, previous):
                return False
            self.voice, self.previous, self.offered = voice, previous, None
            self.unavailable.pop(voice, None)  # it just loaded
        return True

    # --------------------------------------------------------------------- tools
    def rename(self, raw) -> dict:
        try:
            name = valid_name(raw)
        except ValueError as exc:
            return {'ok': False, 'reason': 'invalid_name', 'message': str(exc)}
        with self.lock:
            if not self._save(name, self.voice, self.previous):
                return {'ok': False, 'reason': 'not_saved', 'message': 'The name could not be saved; it is unchanged.'
                        + (self.UNREADABLE if self.unreadable else '')}
            old, self.name = self.name, name
        return {'ok': True, 'name': name, 'previous_name': old, 'saved': True,
                'note': (f'Call yourself {name} from now on. This is the saved conversational and visible agent identity. '
                         'RoseOS, the app package, pairing, About and any wake word are unchanged.')}

    def change_voice(self, speaker, args: dict, _retried: bool = False) -> dict:
        if speaker is None:
            return {'ok': False, 'reason': 'no_voice', 'message': 'This backend has no voice to change.'}
        current = getattr(speaker, 'voice', None) or self.voice or DEFAULT_VOICE
        if current not in self.traits['voices']:
            return {'ok': False, 'reason': 'unmeasured_voice',
                    'message': 'The current voice has no measurements, so a description cannot be matched to it.'}
        named = args.get('voice')
        offered, self.offered = self.offered, None  # an offer answers only the very next call
        if named:
            # Only the alternative the last result offered, on its own, never a voice chosen by its name.
            if any(args.get(k) and args.get(k) != 'same' for k in (*DIMENSIONS, 'accent', 'accent_name', 'other')) \
                    or args.get('gender') not in (None, 'same'):
                self.offered = offered  # the offer still stands
                return {'ok': False, 'reason': 'invalid_request', 'changed': False, 'voice': current,
                        'message': 'Apply the offered voice on its own, or describe the whole change again.'}
            if not offered or str(named).lower() != offered['voice'] or offered['voice'] == current:
                self.offered = offered if offered and offered['voice'] != current else None
                return {'ok': False, 'reason': 'not_offered', 'changed': False, 'voice': current,
                        'message': 'Only the alternative a previous change_voice result offered can be applied; '
                                   'otherwise describe the voice.'}
            return self._apply(speaker, current, [offered], label='alternative')
        try:
            request = request_from(args)
        except ValueError as exc:
            return {'ok': False, 'reason': 'invalid_request', 'message': str(exc)}
        now = time.monotonic()
        available = [v for v in getattr(speaker, 'voices', self.traits['voices'])
                     if now - self.unavailable.get(v, -UNAVAILABLE_RETRY_S) >= UNAVAILABLE_RETRY_S]
        limits = self._limits(request)
        gender = self._gender(current)
        if not request['wanted'] and request['gender'] in ('same', 'either', gender):
            if not request['other'] and request['accent'] and request['accent_american']:
                return {'ok': False, 'reason': 'already', 'changed': False, 'voice': current,
                        **({'limits': limits} if limits else {}),
                        'message': 'The current voice already has an American (en-US) accent, like every available '
                                   'voice.'}
            if request['gender'] == gender:
                return {'ok': False, 'reason': 'already', 'changed': False, 'voice': current, 'limits': limits,
                        'message': f'The current voice is already {gender}; ask how it should sound different.'}
            # Only an accent or unmeasured qualities: nothing measured to move toward, so keep the voice.
            return {'ok': False, 'reason': 'no_close_match', 'changed': False, 'voice': current, 'limits': limits,
                    'message': 'Nothing in that description can be matched to the available voices; the current '
                               'voice was kept.'}
        ranked = match(current, request, available, self.traits)
        # A voice qualifies when it moves in no asked direction the wrong way and, for a description, meets at least
        # one of them; a change of gender asked for alone qualifies by gender.
        changing_gender = request['gender'] in ('female', 'male') and request['gender'] != gender
        fits = [e for e in ranked if not e['against'] and (e['met'] or (changing_gender and not request['wanted']))]
        if fits:
            result = self._apply(speaker, current, fits, limits=limits)
            if result.get('reason') != 'unavailable':
                return result
            if limits:
                result['limits'] = limits
            if _retried:
                return result
            # None of the fitting voices loaded, and each is now marked unavailable: answer from the voices that are
            # left, as repeating the request would. Another fit is applied; otherwise the nearest alternative is
            # offered alongside the limits.
            again = self.change_voice(speaker, args, _retried=True)
            if again.get('ok'):
                again['closer_unavailable'] = result['unavailable'] + again.get('closer_unavailable', [])
                return again
            if again.get('alternative'):
                result['alternative'] = again['alternative']
                result['message'] += (' The nearest alternative is offered; apply it only if the owner agrees '
                                      '(change_voice with its voice).')
            else:
                self.offered = None
            return result
        if changing_gender and not ranked:
            return {'ok': False, 'reason': 'no_match', 'changed': False, 'voice': current, 'limits': limits,
                    'message': f'No {request["gender"]} voice is available on this backend.'}
        # Nothing qualifies: keep the voice and offer the nearest voice that moves the asked way, even short of a
        # much_ step: of the asked (or current) gender first, another gender's only when none does.
        wider = match(current, {**request, 'gender': 'either'}, available, self.traits)
        same = [e for e in ranked if e['toward'] and not e['against']]
        best = same[0] if same else None
        if best is None and changing_gender:
            # The owner asked for this gender: of its voices that move nothing the wrong way, the one that goes
            # furthest the asked way.
            best = max((e for e in ranked if not e['against']), key=lambda e: e['progress'], default=None)
        if best is None and request['gender'] in ('same', 'either'):  # another gender only when none was named
            best = next((e for e in wider if e['toward'] and not e['against']), None)
        if best is None and request['keep']:
            # Only a kept aspect stands in the way: offer the voice that moves the asked way and, of those, meets the
            # most and breaks the keep least, the broken keep named in its unmet so the owner decides.
            near = [e for e in ranked if all(a.startswith('same_') for a in e['against'])
                    and (e['toward'] or changing_gender)
                    and (changing_gender or request['gender'] == 'either'
                         or self._gender(e['voice']) == gender)]  # no unasked gender change

            def breach(e):
                real = deltas(self.traits['voices'][current], self.traits['voices'][e['voice']])
                return sum(abs(real[a[len('same_'):]]) / DIMENSIONS[a[len('same_'):]]['scale'] for a in e['against'])
            best = min(near, key=lambda e: (len(e['against']), sum(not u.startswith('same_') for u in e['unmet']),
                                            breach(e), e['score']), default=None)
        scope = '' if request['gender'] in ('either',) else f'{request["gender"] if changing_gender else gender} '
        kept = ' and '.join(request['keep'])
        blocked = [e for e in ranked if e['met'] and e['against'] and all(a.startswith('same_') for a in e['against'])]
        if request['keep'] and blocked:
            common = set.intersection(*(set(e['against']) for e in blocked)) or set.union(*(set(e['against'])
                                                                                       for e in blocked))
            names = ' and '.join(a[len('same_'):] for a in sorted(common))
            message = f'Every available {scope}voice that fits also changes the {names} you asked to keep.'
        elif request['wanted']:
            # No voice is stopped by a keep alone (that is the message above), so none is named here; "the other
            # way" only when a voice that goes far enough also moves another asked aspect the wrong way.
            contrary = any(e['met'] and any(not a.startswith('same_') for a in e['against']) for e in ranked)
            message = (f'No available {scope}voice is ' + ' and '.join(w.replace('_', ' ')
                                                                    for w in self._words(request['wanted']))
                       + (f' than a typical {request["gender"]} voice' if changing_gender else ' than the current one')
                       + (' without moving the other way.' if contrary and not changing_gender else '.'))
        else:
            message = f'No available {scope}voice fits that description' + (f' while keeping the same {kept}.'
                                                                            if kept else '.')
        result = {'ok': False, 'reason': 'no_close_match', 'changed': False, 'voice': current, 'limits': limits,
                  'message': message}
        if best is not None:
            self.offered = best
            result['alternative'] = {'voice': best['voice'], 'gender': self._gender(best['voice']),
                                     'met': best['met'], 'unmet': best['unmet'], 'changes': best['changes']}
            if best.get('judged_among'):
                result['alternative']['judged_among'] = best['judged_among']
            result['message'] += (' The nearest alternative is offered; apply it only if the owner agrees '
                                   '(change_voice with its voice).')
        return result

    def revert(self, speaker, to: str = 'previous') -> dict:
        self.offered = None
        if speaker is None:
            return {'ok': False, 'reason': 'no_voice', 'message': 'This backend has no voice to change.'}
        current = getattr(speaker, 'voice', None) or self.voice or DEFAULT_VOICE
        target = DEFAULT_VOICE if to == 'original' else self.previous
        if target is not None and target == current and self.voice not in (None, target):
            # This run already speaks it (a one-run --voice or a startup fallback): make it the saved choice too.
            with self.lock:
                if not self._save(self.name, target, self.voice):
                    return {'ok': False, 'reason': 'not_saved', 'changed': False, 'voice': current,
                            'message': 'The choice could not be saved.' + (self.UNREADABLE if self.unreadable else '')}
                self.voice, self.previous = target, self.voice
            return {'ok': True, 'changed': False, 'voice': target, 'saved': True, 'match': 'reverted',
                    'note': 'This voice is already speaking; it is now also the saved choice for the next start.'}
        if target is None or target == current:
            return {'ok': False, 'reason': 'nothing_to_revert', 'changed': False, 'voice': current,
                    'message': 'This is already the ' + ('original' if to == 'original' else 'only chosen') + ' voice.'}
        return self._apply(speaker, current, [{'voice': target, 'met': [], 'unmet': [], 'against': [],
                                               'changes': None}], label='reverted')

    # ------------------------------------------------------------------ helpers
    def _fallback_previous(self, voice):
        """The voice to go back to when the voice being left is not a measured one: the saved choice, else the one
        before it, never the voice being applied."""
        for candidate in (self.voice, self.previous):
            if candidate in self.traits['voices'] and candidate != voice:
                return candidate
        return None

    @staticmethod
    def _words(wanted: dict) -> list[str]:
        return [('much_' if abs(level) == 2 else '') + (DIMENSIONS[dim]['more'] if level > 0 else DIMENSIONS[dim]['less'])
                for dim, level in wanted.items()]

    def _gender(self, voice):
        return self.traits['voices'].get(voice, {}).get('gender')

    def _limits(self, request) -> list[str]:
        """What a description asked for that these voices cannot be matched on."""
        limits = []
        if not request['accent_american']:
            limits.append(f'Every available voice has an American (en-US) accent; none is {request["accent"]}.')
        elif request.get('accent_note'):
            limits.append(ACCENT_NOTE)
        if request['other']:
            limits.append('Only pitch, warmth, liveliness and pace were measured from the voices\' samples; '
                          + ', '.join(request['other']) + ' could not be matched.')
        return limits

    def _apply(self, speaker, current, candidates, limits=None, label=None) -> dict:
        """Switch to the first candidate this backend can load, then save it; only qualifying candidates are given,
        so a failure to load never falls through to a voice that does not fit."""
        skipped = []
        for entry in candidates:
            voice = entry['voice']
            if voice not in self.traits['voices']:
                skipped.append(voice)
                continue
            try:
                speaker.set_voice(voice)
            except Exception:
                self.unavailable[voice] = time.monotonic()
                skipped.append(voice)
                continue
            with self.lock:
                measured = current in self.traits['voices']
                previous = current if measured else self._fallback_previous(voice)
                if not self._save(self.name, voice, previous):
                    try:
                        speaker.set_voice(current)
                    except Exception:
                        pass
                    return {'ok': False, 'reason': 'not_saved', 'changed': False, 'voice': current,
                            'message': 'The new voice could not be saved, so the current voice was kept.'
                            + (self.UNREADABLE if self.unreadable else '')}
                self.voice, self.previous, self.offered = voice, previous, None
                self.unavailable.pop(voice, None)
            changes = entry['changes'] if entry['changes'] is not None else describe_changes(
                deltas(self.traits['voices'][current], self.traits['voices'][voice])) if measured else []
            result = {'ok': True, 'changed': True, 'voice': voice, 'previous_voice': current, 'saved': True,
                      'match': label or ('close' if not entry['unmet'] else 'partial'),
                      'met': entry['met'], 'unmet': entry['unmet'], 'measured_changes': changes,
                      **({'judged_among': entry['judged_among']} if entry.get('judged_among') else {}),
                      'note': 'Reply now with one short sentence in this voice so the owner hears it, and ask '
                              'whether it suits them.'}
            if entry.get('judged_among'):
                result['note'] += (f' met and unmet describe it among {entry["judged_among"]} voices (for example a '
                                   f'deeper {entry["judged_among"]} voice); measured_changes are the change from the '
                                   'previous voice.')
            if limits:
                result['limits'] = limits
            if skipped:
                result['closer_unavailable'] = skipped
                result['note'] += (' A closer voice could not be loaded on this backend; say this is the closest '
                                   'available one.')
            return result
        return {'ok': False, 'reason': 'unavailable', 'changed': False, 'voice': current, 'unavailable': skipped,
                'message': 'No voice that fits could be loaded on this backend; the current voice was kept.'}


DIRECTIONS = {name: {'type': 'string', 'enum': ['same', d['less'], d['more'], 'much_' + d['less'], 'much_' + d['more']],
                     'description': f'Relative to the current voice; same or omitted keeps it.'}
              for name, d in DIMENSIONS.items()}
TOOLS = [
    {'type': 'function', 'name': 'rename_assistant',
     'description': 'Change your conversational and visible agent name when the owner gives you one. Saved across '
                    'restarts. RoseOS, the app package, pairing, About and wake word stay unchanged. '
                    'Say the name changed only when ok is true.',
     'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name'],
                     'additionalProperties': False}},
    {'type': 'function', 'name': 'change_voice',
     'description': 'Change your voice to the closest available one when the owner describes how it should sound. '
                    'Give only what they asked for, relative to your current voice: pitch lower/higher (deeper is '
                    'lower), warmth warmer/brighter, liveliness calmer/livelier, pace slower/faster, gender, an accent, '
                    'and with a gender change only qualifiers the owner gave about that gender\'s voice ("a deeper '
                    'man\'s voice"), never one implied by the change itself, '
                    'and in other any quality none of these cover (for example raspy). Voices were measured from their '
                    'own samples. voice applies an alternative a previous result offered, once the owner agrees. '
                    'Saved across restarts.',
     'inputSchema': {'type': 'object', 'properties': {
         **DIRECTIONS,
         'gender': {'type': 'string', 'enum': list(GENDERS),
                    'description': 'same (default) keeps the current gender; either only if the owner does not mind.'},
         'accent': {'type': 'string', 'enum': ['american', 'other'],
                    'description': 'american when the owner asks for a general American (US) accent or to keep the '
                                   'current one; other when they want any other accent, a regional one (Southern, New '
                                   'York) or a less American one, named in accent_name. Omit it when they do not mind '
                                   'or only rule out a non-American one.'},
         'accent_name': {'type': 'string', 'description': 'With accent other: that accent in the owner\'s words, for '
                                                            'example British or Southern.'},
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
