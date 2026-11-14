"""Request screening before a build job exists (docs/APP-BUILDER.md §3.6).

``screen(text)`` runs on a create description or a revise request, before the ledger records a job. It is offline,
deterministic and model-free, so a refused request starts no writer, creates no project and costs nothing.

It refuses (HTTP 400) only what it can name precisely:

- ``harm``: covert surveillance of another person, stealing credentials or imitating a real sign-in, malware;
- ``platform``: a request whose subject is an app for another platform (an iPhone, a website, a desktop, a watch or
  a TV) and that does not mention Android.

Everything else is accepted. A request that mentions a power generated apps never get (reading SMS, an accessibility
service, a VPN, a keyboard, drawing over other apps, ...), a library, or a server gets ``notes`` saying what will
happen instead. Those are notes, not refusals, because the words are ambiguous (a piano keyboard, a reminder to send
texts) and the manifest policy refuses the powers themselves whatever the request said
(``template.check_capabilities``, checked again on the built APK). This screen reads English phrases; it is a
courtesy, not the boundary.
"""
from __future__ import annotations

import re
import unicodedata

PEOPLE = (r'(my |his |her |their |a |the |our )?(wife|husband|partner|spouse|girlfriend|boyfriend|ex|kids?|children'
          r'|son|daughter|teen(ager)?s?|employees?|staff|someone|somebody|people|him|her|them|neighbou?rs?|family'
          r'|friends?|roommates?|coworkers?|colleagues?)')
COVERT = r'without (him|her|them|their|my \w+|the user|the owner|anyone|people|it being) (knowing|noticing|consent|permission|seeing)'
OTHER_PLATFORM = (r'(iphone|ios|ipad|apple watch|watch ?os|mac|macos|windows|desktop|pc|linux|smart ?watch|wear ?os'
                  r'|android tv|google tv|tv|web|browser)')
REQUEST = r'^(please |pls |hey |hi )?(can you |could you |i want you to |i want to |i need |i\'d like )?(to )?'

REFUSALS = (
    ('harm', 'This builder won\'t make apps that watch, track or record someone without their knowledge.',
     rf'\b(spy|spying) on {PEOPLE}\b|\b(stalkerware|spyware|keylogger|key logger)\b'
     rf'|\b(track|monitor|record|read|watch|follow|photograph|listen)\w*\b[^.;!?]{{0,60}}\b{COVERT}\b'
     r'|\b(secretly|covertly|discreetly) (track|record|monitor|read|watch|follow|photograph|listen|film|spy)\w*\b'
     r'|\bhidden (spy )?(camera|recorder|tracker|microphone|mic) app\b'),
    ('harm', 'This builder won\'t make apps that collect other people\'s passwords or imitate a real sign-in.',
     r'\b(steal|harvest|phish)\w*\b[^.;!?]{0,30}\b(passwords?|credentials|log ?ins?|card numbers?|bank details)\b'
     r'|\bphishing\b'
     r'|\b(fake|clone of|copy of|imitation of|imitat\w+)\b[^.;!?]{0,25}\b(bank|paypal|venmo|google|gmail|facebook'
     r'|instagram|apple|microsoft|amazon|netflix|whatsapp)\b[^.;!?]{0,15}\b(login|log-in|log in|sign-?in) '
     r'(page|screen|form)\b'),
    ('harm', 'This builder won\'t make malware.',
     r'\b(malware|ransomware|trojan horse|botnet|rootkit|crypto ?miner|cryptojack\w*)\b'
     r'|\b(make|write|create|build|code|develop) (me )?(a |an )?(computer |phone |android )?virus\b'
     r'(?! (simulation|simulator|game|quiz|model|tracker|facts))'),
    ('platform', 'This builder makes Android phone apps only, not apps for other platforms or websites.',
     rf'{REQUEST}(make|build|create|write|code|design|develop|program) (me |us )?(a |an |my |our )?(new |simple )?'
     rf'{OTHER_PLATFORM} (app|application|program|version|extension)\b'
     rf'|{REQUEST}(make|build|create|write|code|design|develop) (me |us )?(a |an |my |our )?(new |simple )?'
     r'(website|web ?site|webpage|landing page|web app|chrome extension|browser extension)\b'
     rf'|^(a |an )?(new |simple )?{OTHER_PLATFORM} (app|application|program|version|extension)\b(?!\?| (list|ideas?'
     r'|wishlist|vs|comparison|notes?|tracker|checklist))'
     r'|^(a |an )?(new |simple )?(wear ?os |smart ?watch |apple watch )?watch face\b(?!\?)'),
)

NOTES = (
    ('capability', 'Generated apps can\'t read or send SMS, read the call log or record calls, so the app will '
     'keep its own list instead and say what it could not do.',
     r'\b(sms (app|inbox)|my sms|read (my |incoming )?sms|(auto-?reply|forward) (to |my )?(sms|text messages)'
     r'|call log|call history|call record(er|ing)|record (my |phone |incoming |outgoing )+calls)\b'),
    ('capability', 'Generated apps can\'t be accessibility services, device administrators, VPNs, keyboards or '
     'notification readers, so the app will do what it can without that and say what it could not do.',
     r'\b(accessibility service|device (admin|administrator|owner)|vpn (app|service)|a vpn\b(?! (cost|comparison'
     r'|list|notes?))|input method|replace (my|the) keyboard|custom (typing )?keyboard|notification listener'
     r'|(read|forward|log) (all|every|other apps\'?) notifications)\b'),
    ('capability', 'Generated apps can\'t draw over, control or inspect other apps, or have root or system access, '
     'so the app will do what it can inside itself and say what it could not do.',
     r'\b((over|on top of) (other|all|any) apps|(block|lock|hide|uninstall|freeze|kill) (other|any|all|certain) apps'
     r'|app (locker|blocker)|usage stats|root access|rooted|superuser|system permissions?|privileged app'
     r'|platform (key|signature)|change system settings|secure settings)\b'),
    ('library', 'Apps here use built-in Android features only, so {match} won\'t be used; the app will do the same '
     'with the framework, or say what it could not do.',
     r'\b(firebase|admob|google maps (sdk|api)|maps sdk|play services|jetpack( compose)?|compose ui|androidx'
     r'|kotlin|flutter|react native|unity|unreal|xamarin|cordova|retrofit|okhttp|glide|room database|realm)\b'),
    ('online', 'Apps here run only on this phone: nothing is hosted online yet, so {match} will stay on the device.',
     r'\b(multiplayer|backend|back-end|cloud sync|sync (across|between) (devices|phones)|user accounts'
     r'|(sign ?up|log ?in|login) (with|for) (google|facebook|email|accounts?)|leaderboard|online'
     r'|share (it )?with (friends|others)|push notifications?)\b'),
)

_REFUSALS = tuple((category, message, re.compile(p)) for category, message, p in REFUSALS)
_NOTES = tuple((code, message, re.compile(p)) for code, message, p in NOTES)


def normalize(text: str) -> str:
    """Lower case and compatibility forms (fullwidth, ligatures); invisible format characters removed, so they cannot
    split a word; plain quotes; other controls and separators as single spaces."""
    text = unicodedata.normalize('NFKC', text).casefold()
    text = text.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
    text = ''.join('' if unicodedata.category(c) == 'Cf' else ' ' if unicodedata.category(c)[0] in 'CZ' else c
                   for c in text)
    return re.sub(r'\s+', ' ', text).strip()


def screen(text: str) -> dict:
    """``{'ok': True, 'notes': [...]}``, or ``{'ok': False, 'category', 'message', 'notes': []}`` for the first
    refusal. Messages are owner-facing and never quote the request back beyond a matched product name."""
    clean = normalize(text)
    android = re.search(r'\bandroid\b', clean) is not None
    for category, message, pattern in _REFUSALS:
        if category == 'platform' and android:
            continue  # "an Android and iPhone app", "iOS vs Android": the Android app is what gets built
        if pattern.search(clean):
            return {'ok': False, 'category': category, 'message': message + ' Nothing was started.', 'notes': []}
    notes = []
    for _code, message, pattern in _NOTES:
        found = pattern.search(clean)
        if found:
            notes.append(message.format(match=found.group(0)))
    return {'ok': True, 'notes': notes}
