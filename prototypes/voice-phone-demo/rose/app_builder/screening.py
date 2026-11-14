"""Request screening before a build job exists (docs/APP-BUILDER.md §3.6).

``screen(text)`` runs on a create description or a revise request, before the ledger records a job. It is offline,
deterministic and model-free, so a refused request starts no writer, creates no project and costs nothing.

It refuses only requests this builder cannot or will not make:

- ``platform``: not an Android phone app (iPhone, a website, a desktop app, a browser extension, a watch or TV);
- ``capability``: powers generated apps are never given (accessibility services, device admin, SMS and call logs,
  VPN, drawing over or reading other apps, a keyboard, root or system access);
- ``harm``: covert surveillance of another person, capturing credentials, impersonating a bank or sign-in, malware.

Anything else is accepted. A request that names a library or needs a server is accepted with ``notes`` saying what
will happen instead (framework APIs only; nothing is hosted online).

This is a courtesy, not the boundary: it reads English phrases and a reworded request can pass it. What a generated
app may declare is enforced on the source and on the built APK (``template.check_capabilities``), whatever the
request said.
"""
from __future__ import annotations

import re
import unicodedata

REFUSALS = (
    ('platform', 'This builder makes Android phone apps only, so it can\'t make an app for iPhone or iPad.',
     r'\b(ios|iphone|ipad|apple watch)\b'),
    ('platform', 'This builder makes Android phone apps only, not websites or web apps.',
     r'\b(make|build|create|design|code) (me |us )?(a |an |my |our )?(new )?(website|web ?site|web ?app|webpage'
     r'|landing page)\b'),
    ('platform', 'This builder makes Android phone apps only, not browser extensions.',
     r'\b(chrome|firefox|browser|safari) (extension|add-?on|plugin)\b'),
    ('platform', 'This builder makes Android phone apps only, not desktop programs.',
     r'\b(windows|mac ?os|macbook|linux|desktop|pc) (app|application|program|software)\b'),
    ('platform', 'This builder makes Android phone apps only, not watch or TV apps.',
     r'\b(wear ?os|smart ?watch|watch face|android tv|google tv|tv app)\b'),
    ('capability', 'Generated apps can\'t be accessibility services, which can read and control other apps.',
     r'\baccessibility (service|api|permission)\b'),
    ('capability', 'Generated apps can\'t be device administrators.',
     r'\bdevice (admin|administrator|owner)\b'),
    ('capability', 'Generated apps can\'t read or send SMS, read the call log or record calls.',
     r'\b(read|send|forward|back ?up|intercept|auto-?repl\w*|schedul\w*|block|filter)s?\b.{0,20}'
     r'\b(sms|texts|text messages?)\b|\bsms app\b|\b(call log|call history)\b|\brecord(s|ing)? (phone )?calls?\b'
     r'|\bcall recorder\b'),
    ('capability', 'Generated apps can\'t be a VPN.', r'\bvpn\b'),
    ('capability', 'Generated apps can\'t draw over other apps or read their notifications or screens.',
     r'\b(over|on top of) (other|all|any) apps\b|\bfloating (bubble|window|button)\b'
     r'|\boverlay (on|over|above) (other|all|any)\b'
     r'|\b(read|see|capture|forward|log)\b.{0,20}\b(other apps\'?|all( my)?|every) notifications\b'
     r'|\bnotification listener\b|\bscreen ?(recorder|recording|capture of other)\b'),
    ('capability', 'Generated apps can\'t be a keyboard.', r'\b(custom |my own |new )?keyboard app\b|\binput method\b'),
    ('capability', 'Generated apps can\'t control, block or install other apps.',
     r'\b(block|lock|hide|uninstall|install|freeze|kill) (other|any|all|certain) apps\b|\bapp (lock|locker|blocker)\b'
     r'|\b(usage stats|app usage of)\b'),
    ('capability', 'Generated apps can\'t have root or system access.',
     r'\b(root(ed)? (access|my phone|the phone)|superuser|system app|system permissions?|privileged app'
     r'|platform (key|signature)|change system settings|secure settings)\b'),
    ('harm', 'This builder won\'t make apps that watch or track someone without their knowledge.',
     r'\b(spy|spying) on\b|\bstalk\w*|\b(keylogger|key logger|stalkerware|spyware)\b'
     r'|\b(secretly|covertly|in secret)\b.{0,25}\b(track|record|monitor|watch|listen|read|follow|photograph)\w*'
     r'|\bwithout (him|her|them|my \w+|their|the user|the owner|anyone) (knowing|noticing|seeing)\b'
     r'|\bhidden (camera|recorder|tracker|microphone|mic)\b'
     r'|\b(read|see|monitor|check)\b.{0,15}\b(my |his |her |their )?(partner|wife|husband|girlfriend|boyfriend|ex'
     r'|employee|kid|child|son|daughter)\'?s? (messages|texts|chats|whatsapp|location|photos|calls)\b'),
    ('harm', 'This builder won\'t make apps that collect other people\'s passwords or imitate a sign-in.',
     r'\b(steal|harvest|capture|grab|collect|phish)\w*\b.{0,25}\b(passwords?|credentials|logins?|card numbers?'
     r'|pins?|2fa|otp|codes)\b|\bphishing\b'
     r'|\b(fake|copy|clone|imitat\w*|look(s|ing)? (exactly )?like)\b.{0,30}\b(bank|paypal|venmo|login|log-in'
     r'|sign-?in) (page|screen|app)\b'),
    ('harm', 'This builder won\'t make malware.',
     r'\b(malware|ransomware|trojan|botnet|backdoor|rootkit|crypto ?miner|ddos)\b'
     r'|\b(make|write|create|build|code) (me )?(a |an )?(computer |phone )?virus\b'),
)

NOTES = (
    ('library', 'Apps here use built-in Android features only, so {match} won\'t be used; the app will do the same '
     'with the framework, or say what it could not do.',
     r'\b(firebase|admob|google maps (sdk|api)|maps sdk|play services|jetpack( compose)?|compose ui|androidx'
     r'|kotlin|flutter|react native|unity|unreal|xamarin|cordova|retrofit|okhttp|glide|room database|realm)\b'),
    ('online', 'Apps here run only on this phone: nothing is hosted online yet, so {match} will stay on the device.',
     r'\b(multiplayer|backend|back-end|server|cloud sync|sync (across|between) (devices|phones)|user accounts'
     r'|(sign ?up|log ?in|login) (screen|page|with|accounts?)|leaderboard|online|share (it )?with (friends|others)'
     r'|push notifications?)\b'),
)

_REFUSALS = tuple((category, message, re.compile(p)) for category, message, p in REFUSALS)
_NOTES = tuple((code, message, re.compile(p)) for code, message, p in NOTES)


def normalize(text: str) -> str:
    """Lower case, compatibility forms (fullwidth, ligatures), plain quotes and single spaces."""
    text = unicodedata.normalize('NFKC', text).casefold()
    text = text.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
    text = ''.join(' ' if unicodedata.category(c)[0] in 'CZ' else c for c in text)  # controls, separators
    return re.sub(r'\s+', ' ', text).strip()


def screen(text: str) -> dict:
    """``{'ok': True, 'notes': [...]}``, or ``{'ok': False, 'category', 'message', 'notes': []}`` for the first
    refusal. The messages are owner-facing and never quote the request back beyond a matched product name."""
    clean = normalize(text)
    for category, message, pattern in _REFUSALS:
        if pattern.search(clean):
            return {'ok': False, 'category': category, 'message': message + ' Nothing was started.', 'notes': []}
    notes = []
    for _code, message, pattern in _NOTES:
        found = pattern.search(clean)
        if found:
            notes.append(message.format(match=found.group(0)))
    return {'ok': True, 'notes': notes}
