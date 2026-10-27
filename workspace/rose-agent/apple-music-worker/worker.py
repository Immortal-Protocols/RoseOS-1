#!/usr/bin/env python3
"""An isolated, manually invoked Apple Music procedure. No LLM or voice routing."""
import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import sys
import time
import uuid

MAP = json.loads((Path(__file__).with_name('map.json')).read_text())
PACKAGE = MAP['package']
S = MAP['selectors']

def norm(text):
    return ' '.join(str(text or '').split()).casefold()

class NeedsAssistance(Exception):
    pass

def checked_screen(screen):
    if screen.get('status') != 'ok' or screen.get('packageName') != PACKAGE:
        raise NeedsAssistance('Expected an unlocked Apple Music screen.')
    if screen.get('truncated') or not screen.get('snapshotId'):
        raise NeedsAssistance('Screen is incomplete; refusing to act.')
    if any(n.get('password') or str(n.get('class', '')).endswith('Dialog') for n in screen.get('nodes', [])):
        raise NeedsAssistance('Unexpected protected field or dialog; needs review.')
    return screen

def nodes(screen):
    return [n for n in screen.get('nodes', []) if n.get('visible', True) and n.get('enabled', True) and not n.get('password')]

def labels(screen, label):
    return [n for n in nodes(screen) if label in (n.get('text'), n.get('description'))]

def editable(screen):
    found = [n for n in nodes(screen) if n.get('editable') and n.get('class') == S['searchFieldClass']]
    if len(found) > 1:
        raise NeedsAssistance('Multiple search fields; screen needs review.')
    return found[0] if found else None

def player(screen):
    return bool(labels(screen, S['playerMarker'])) or any(n.get('viewId') in S.get('fullPlayerContentViews', []) for n in nodes(screen))

def title_key(text):
    return norm(re.sub(r'\s*\((?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?\)\s*$', '', str(text), flags=re.I))

def song_matches(screen, title, artist):
    # The query handles relevance. Accept its first song, including live versions.
    rows = {}
    for n in nodes(screen):
        if n.get('clickTarget') is not None:
            rows.setdefault(n['clickTarget'], []).append(n)
    for target, group in rows.items():
        credit = next((n.get('text', '') for n in group if n.get('text', '').startswith('Song • ')), None)
        label = next((n.get('text', '') for n in group if n.get('viewId') == 'com.apple.android.music:id/title'), None)
        if label is None and not any('viewId' in n for n in group):
            label = next((n.get('text', '') for n in group if n.get('text', '') and not n.get('text', '').startswith('Song • ')), None)
        if credit and label:
            return [{'target':target, 'label':label, 'artist':credit[len('Song • '):]}]
    return []

def verified_compact_player(screen, title, playing=True):
    return (not player(screen) and len(labels(screen, S['pause'] if playing else S['play'])) == 1
            and any(norm(n.get('description')) == norm('playing '+title) for n in nodes(screen)))

def verified_player(screen, title, artist, playing=True):
    return (player(screen)
            and any(norm(n.get('text')) == norm(title) for n in nodes(screen))
            and any(norm(n.get('text')) == norm(artist) for n in nodes(screen))
            and bool(labels(screen, S['pause'] if playing else S['play'])))

class Phone:
    def __init__(self):
        base = Path.home()/'.local/share/rose-agent/phone'
        if not (base/'control.py').exists():
            base = Path(__file__).resolve().parents[1]/'phone'
        sys.path.insert(0, str(base))
        spec = importlib.util.spec_from_file_location('pixel_phone_control', base/'control.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
    def __call__(self, request):
        command = self.module.call('/phone/commands', request)
        deadline = time.monotonic() + 23
        while command['status'] == 'pending' and time.monotonic() < deadline:
            time.sleep(.15)
            command = self.module.call('/phone/commands/' + command['id'])
        if command['status'] != 'completed':
            raise NeedsAssistance('Phone command did not complete: ' + command['status'])
        return command['result']

class Worker:
    def __init__(self, phone, now=time.monotonic, sleep=time.sleep):
        self.phone, self.now, self.sleep = phone, now, sleep
        self.steps = []
    def call(self, action, **args):
        start = self.now()
        try:
            result = self.phone({'action': action, **args})
        except (OSError, ValueError) as error:
            self.steps.append({'action': action, 'elapsedMs': round((self.now()-start)*1000), 'status': 'transport_error'})
            raise NeedsAssistance('Phone transport failed (' + type(error).__name__ + '); action outcome is unknown.') from error
        self.steps.append({'action': action, 'elapsedMs': round((self.now()-start)*1000), 'status': result.get('status')})
        return result
    def inspect(self):
        return checked_screen(self.call('inspect_ui'))
    def tap(self, screen, label, target=None):
        args = {'snapshotId': screen['snapshotId'], 'label': label}
        if target is not None:
            args['nodeId'] = target
        result = self.call('tap_label', **args)
        if result.get('status') != 'action_accepted':
            # No automatic physical-touch retry: the first tap might have worked.
            raise NeedsAssistance('Tap needs review: ' + str(result.get('status')))
        return checked_screen(result['observation']) if result.get('observation') else self.inspect()
    def wait(self, screen, predicate):
        deadline = self.now() + MAP['limits']['readWaitSeconds']
        while not predicate(screen):
            if self.now() >= deadline:
                raise NeedsAssistance('Expected screen did not appear; no further taps sent.')
            self.sleep(.25)
            screen = self.inspect()
        return screen
    def prepare_search(self, screen):
        for _ in range(MAP['limits']['navigationSteps']):
            if screen.get('status') != 'ok' or screen.get('packageName') != PACKAGE:
                raise NeedsAssistance('Expected Apple Music.')
            # Full player can expose underlying search nodes: close it FIRST.
            if player(screen):
                result = self.call('back')
                if result.get('status') != 'action_accepted':
                    raise NeedsAssistance('Could not dismiss player.')
                screen = self.inspect()
                continue
            checked_screen(screen)
            if editable(screen):
                return screen
            tabs = [n for n in labels(screen, S['searchTab']) if n.get('clickTarget') is not None]
            targets = {n['clickTarget'] for n in tabs}
            if len(targets) != 1:
                raise NeedsAssistance('No unambiguous Search tab. Needs a new mapped state.')
            screen = self.tap(screen, S['searchTab'], targets.pop())
        raise NeedsAssistance('Search route exceeded its navigation limit.')
    def run(self, title, artist, pause_after=False):
        started = self.now()
        verified_ms = None
        try:
            result = self.call('open_app', packageName=PACKAGE)
            if result.get('status') != 'opened' or result.get('packageName') != PACKAGE:
                raise NeedsAssistance('Apple Music did not open.')
            screen = self.prepare_search(self.call('inspect_ui'))
            field = editable(screen)
            if not field.get('focused'):
                focus = {'snapshotId':screen['snapshotId'], 'nodeId':field['id']}
                if field.get('text') or field.get('description'):
                    self.call('tap_label', label=field.get('text') or field['description'], **focus)
                else:
                    self.call('tap', **focus)
                screen = self.inspect()
                field = editable(screen)
                if not field or not field.get('focused'):
                    raise NeedsAssistance('Search field did not receive focus.')
            query = title + ' ' + artist
            result = self.call('set_text', snapshotId=screen['snapshotId'], nodeId=field['id'], text=query)
            if result.get('status') == 'stale_screen':
                # The device explicitly guarantees the rejected write did not run.
                # Re-read and re-identify the field once; never reuse its old ID.
                screen = self.inspect()
                field = editable(screen) if not player(screen) else None
                if not field:
                    raise NeedsAssistance('Search screen changed during entry.')
                result = self.call('set_text', snapshotId=screen['snapshotId'], nodeId=field['id'], text=query)
            if result.get('status') != 'action_accepted':
                raise NeedsAssistance('Search text was not accepted.')
            screen = self.inspect()
            field = editable(screen)
            if not field or field.get('text') != query:
                raise NeedsAssistance('Search field does not match this request.')
            if field.get('imeEnter') and field.get('focused'):
                submitted = self.call('ime_enter', snapshotId=screen['snapshotId'], nodeId=field['id'])
                if submitted.get('status') != 'action_accepted':
                    raise NeedsAssistance('Search submission was not accepted.')
                screen = self.inspect()
            screen = self.wait(screen, lambda s: bool(song_matches(s, title, artist)))
            field = editable(screen)
            if not field or field.get('text') not in (query, query + ' in Apple Music'):
                raise NeedsAssistance('Search field does not match this request.')
            matches = song_matches(screen, title, artist)
            if len(matches) != 1:
                raise NeedsAssistance('Multiple matching song versions. Needs a user choice.')
            match = matches[0]
            title = match['label']
            artist = match['artist']
            screen = self.tap(screen, match['label'], match['target'])
            # A song tap is NEVER repeated if playback is delayed.
            screen = self.wait(screen, lambda s: player(s) or (bool(labels(s, S['pause'])) and any(norm(n.get('description')) == norm('playing '+title) for n in nodes(s))))
            screen = self.wait(screen, lambda s: verified_player(s, title, artist) or verified_compact_player(s, title))
            elapsed = round((self.now()-started)*1000)
            verified_ms = elapsed
            if pause_after:
                # Full-player elapsed time changes invalidate whole-tree snapshots.
                # The compact player's explicit Pause control has no ticking timer.
                if player(screen):
                    self.call('back')
                screen = self.wait(self.inspect(), lambda s: not player(s))
                if not any(norm(n.get('description')) == norm('playing '+title) for n in nodes(screen)):
                    raise NeedsAssistance('Song changed before test cleanup.')
                screen = self.tap(screen, S['pause'])
                self.wait(screen, lambda s: bool(labels(s, S['play'])) and not labels(s, S['pause']) and any(norm(n.get('description')) == norm('playing '+title) for n in nodes(s)))
            return {'status': 'verified_playing_then_paused' if pause_after else 'verified_playing', 'title': title, 'artist': artist,
                    'elapsedToVerifiedPlayingMs': elapsed, 'modelCalls': 0, 'steps': self.steps,
                    'verification': 'Exact title/artist result selected once, then matching player title and Pause state; not acoustic verification.'}
        except NeedsAssistance as error:
            return {'status': 'verified_playing_cleanup_needs_assistance' if verified_ms is not None else 'needs_assistance', 'elapsedToVerifiedPlayingMs': verified_ms, 'reason': str(error), 'elapsedMs': round((self.now()-started)*1000), 'modelCalls': 0, 'steps': self.steps}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('title'); parser.add_argument('artist')
    parser.add_argument('--pause-after', action='store_true', help='Pause after verifying playback, for tests.')
    args = parser.parse_args()
    if not args.title.strip() or not args.artist.strip() or len(args.title)+len(args.artist)>300:
        parser.error('Provide a specific title and artist, at most 300 characters combined.')
    result = Worker(Phone()).run(args.title.strip(), args.artist.strip(), args.pause_after)
    directory = Path.home()/'.config/pixel-voice/apple-music-worker'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory/(str(uuid.uuid4())+'.json')
    fd = os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd,'w') as out: json.dump(result,out,indent=2)
    print(json.dumps(result)); return 0 if result['status'] in ('verified_playing', 'verified_playing_then_paused') else 2

if __name__ == '__main__':
    sys.exit(main())
