"""Rose's live customization controls (Nov 7 plan 1O; the theme is plan 1E) as conversation tools: the producer side of Rose Agent's
``customization_*`` app commands (workspace/rose-agent/app/src/dev/pixel/voice/CustomizationConfig.java).

One catalog, ``workspace/rose-agent/app/assets/customization-agent-tools.json``, defines the controls for every
caller: Rose Agent serves it as its app-tool capabilities, this module turns it into conversation tools, and the CLI
(``workspace/rose-agent/customization/rose-customize.mjs``) reads the same file. Each call is the same command:
``app_command`` to ``dev.pixel.voice`` with the tool's operation and the validated arguments, over the existing phone
link. Nothing here adds an endpoint or a phone action.

The controls are the Home column count (0 = automatic), a curated Oxblood wallpaper variant, a bundled font, the
glasses-on hook, and the Rose theme (Oxblood or Glacier: colours and wallpaper across Home, Rose and the system
surfaces; columns and font are kept). The hook's signal is any Bluetooth audio device connecting (glasses on) or the last one
disconnecting (removal), the owner's choice; ``customization_state`` names it in ``glassesWear.basis``.

Success is the phone's own ``verified``/``ok`` after it stored the value. Anything else, a lost or late reply
included, is not success, and nothing is retried here.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

clock, sleep = time.monotonic, time.sleep  # replaced in tests

PACKAGE = 'dev.pixel.voice'
CATALOG = Path(__file__).resolve().parents[3] / 'workspace/rose-agent/app/assets/customization-agent-tools.json'
TIMEOUT_S = 12.0  # SettingsAgentProvider allows 8 s per operation; the link adds its own round trip.

POLICY_LINE = ('Use the customization tools only when the owner asks to change or check Rose Home columns, the Oxblood '
               'wallpaper variant, the font, the glasses-on hook or the theme (for example "switch to Glacier" or "switch '
               'back to Oxblood" is customization_theme); read customization_state for relative requests and '
               'report the returned state: a saved preference is not a visible change, so give Home\'s effectiveColumns, say so '
               'when home.applied is false. The glasses-on hook runs when a Bluetooth audio device connects. Report each '
               'result by its outcome: applied (done and visible), saved_not_shown (saved, not yet shown), '
               'shown_on_home (a theme Home and Rose show but the system surfaces have not confirmed), saved (hook '
               'setting stored), rejected, unavailable or uncertain, using its message; never say a change is done '
               'unless the outcome is applied.')


class CustomizationRefused(ValueError):
    """Refused before anything was sent to the phone."""


def load_catalog(path: Path = CATALOG) -> list[dict]:
    tools = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(tools, list) or not tools:
        raise ValueError('customization catalog must be a non-empty list')
    for spec in tools:
        if spec.get('name') != spec.get('operation') or not str(spec.get('name', '')).startswith('customization_'):
            raise ValueError('customization tools are named after their operation')
        if spec.get('inputSchema', {}).get('type') != 'object':
            raise ValueError('customization tool schema must be an object')
    return tools


_CATALOG = load_catalog()
SPECS = {spec['name']: spec for spec in _CATALOG}
NAMES = frozenset(SPECS)
# The conductor's tool format (codex_conductor.tool): name, description and the catalog's own schema, unchanged.
TOOLS = [{'type': 'function', 'name': s['name'], 'description': s['description'], 'inputSchema': s['inputSchema']}
         for s in _CATALOG]


def validate(name: str, args) -> dict:
    """The arguments exactly as the phone will check them (the same rules as the server's app-tool validator)."""
    spec = SPECS.get(name)
    if spec is None:
        raise CustomizationRefused('Unknown customization tool')
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise CustomizationRefused('Arguments must be an object')
    schema = spec['inputSchema']
    props = schema.get('properties', {})
    for key in schema.get('required', []):
        if key not in args:
            raise CustomizationRefused('Missing argument: ' + key)
    for key, value in args.items():
        field = props.get(key)
        if field is None:
            raise CustomizationRefused('Unknown argument: ' + key)
        kind = field.get('type')
        if kind == 'string' and not isinstance(value, str):
            raise CustomizationRefused('Invalid argument: ' + key)
        if kind == 'integer' and (isinstance(value, bool) or not isinstance(value, int)):
            raise CustomizationRefused('Invalid argument: ' + key)
        if kind == 'boolean' and not isinstance(value, bool):
            raise CustomizationRefused('Invalid argument: ' + key)
        if 'enum' in field and value not in field['enum']:
            raise CustomizationRefused('Unsupported argument: ' + key)
        if 'minimum' in field and value < field['minimum'] or 'maximum' in field and value > field['maximum']:
            raise CustomizationRefused('Argument outside supported range: ' + key)
    if name == 'customization_glasses_hook' and not args:
        raise CustomizationRefused('Supply enabled, action or packageName')
    return dict(args)


def command(name: str, args) -> dict:
    """The phone command for one control: identical to what Rose Agent's registered app tool would send."""
    return {'action': 'app_command', 'packageName': PACKAGE, 'operation': name, 'params': validate(name, args)}


HOME_TOOLS = frozenset({'customization_home_columns', 'customization_wallpaper', 'customization_font', 'customization_theme'})
# A theme is done only when Home drew it and SystemUI acknowledged drawing it (tray, chin, notifications, keyboard).
SYSTEM_TOOLS = frozenset({'customization_theme'})
UNAVAILABLE = frozenset({'offline', 'disconnected'})
UNCERTAIN = frozenset({'timeout', 'uncertain', 'running', 'expired'})
APPLY_WAIT_S, APPLY_POLL_S = 3.0, 0.5  # Home redraws within a frame or two of the saved change while Rose Agent runs

MESSAGES = {
    'applied': 'Done: Home now shows it.',
    'saved_not_shown': ('Saved on the phone, but Home has not shown it yet. It appears when Rose Home next draws; '
                        'nothing else is needed.'),
    'saved': 'Saved on the phone.',
    'shown_on_home': ('Home and Rose show it now, but the tray, notifications and keyboard have not confirmed it yet; '
                      'they follow when the screen is on and the system bar is connected.'),
    'unavailable': 'The phone is not connected, so nothing was changed.',
    'uncertain': 'The phone did not confirm the change; it may or may not have happened. Read customization_state to check.',
}


def outcome(name: str, result) -> dict:
    """The conversation's view of the phone's reply, as one honest ``outcome``:

    - ``applied``: the phone stored it and Home drew that exact revision (a Home control), or the state was read;
    - ``saved_not_shown``: stored, but Home has not drawn it yet; not a success to report as visible;
    - ``saved``: the glasses-on hook's setting was stored (it acts on the next Bluetooth audio connection);
    - ``rejected``: the phone or the argument check refused it, with the reason; nothing changed;
    - ``unavailable``: the phone is not connected; nothing changed;
    - ``uncertain``: no definite answer; it may or may not have happened.

    ``ok`` is true only for applied and saved (and for a read), never for a mere submission."""
    if not isinstance(result, dict):
        result = {'status': 'uncertain'}
    status = str(result.get('status', ''))
    out = {k: result[k] for k in ('status', 'changed', 'customization', 'home', 'system', 'glassesWear', 'reason')
           if k in result}
    out['tool'] = name
    home = result.get('home') if isinstance(result.get('home'), dict) else {}
    if status == 'ok' and name == 'customization_state':
        kind = 'applied'
    elif status == 'verified' and name in SYSTEM_TOOLS:
        system = result.get('system') if isinstance(result.get('system'), dict) else {}
        if home.get('applied') is not True:
            kind = 'saved_not_shown'
        else:
            kind = 'applied' if system.get('applied') is True else 'shown_on_home'
    elif status == 'verified' and name in HOME_TOOLS:
        kind = 'applied' if home.get('applied') is True else 'saved_not_shown'
    elif status == 'verified':
        kind = 'saved'
    elif status in UNAVAILABLE:
        kind = 'unavailable'
    elif status in UNCERTAIN or not status:
        kind = 'uncertain'
    else:
        kind = 'rejected'
    out['outcome'] = kind
    out['ok'] = kind in ('applied', 'saved')
    if kind == 'rejected':
        out['message'] = 'The phone refused it: ' + str(result.get('reason') or result.get('message') or status) + '. Nothing changed.'
    elif name == 'customization_state':
        pass
    elif kind == 'applied' and name == 'customization_home_columns':
        cols = home.get('effectiveColumns')
        asked = (result.get('customization') or {}).get('homeColumns') if isinstance(result.get('customization'), dict) else None
        out['message'] = ('Done: Home now shows ' + str(cols) + ' columns.' +
                          (' That is as many as fit this screen.' if asked not in (None, 0, cols) else ''))
    elif kind == 'applied' and name == 'customization_theme':
        custom = result.get('customization') if isinstance(result.get('customization'), dict) else {}
        out['message'] = 'Done: Rose is now ' + str(custom.get('themeLabel') or custom.get('theme')) + ' everywhere.'
    else:
        out['message'] = MESSAGES[kind]
    return out


def _drawn(state, revision) -> bool:
    home = state.get('home') if isinstance(state, dict) and isinstance(state.get('home'), dict) else {}
    return home.get('applied') is True and home.get('appliedRevision') == revision


def _system_shows(state, theme) -> bool:
    system = state.get('system') if isinstance(state, dict) and isinstance(state.get('system'), dict) else {}
    return theme is not None and system.get('applied') is True and system.get('shown') == theme


def _settled(name, result) -> bool:
    if not (result.get('home') or {}).get('applied'):
        return False
    return name not in SYSTEM_TOOLS or (result.get('system') or {}).get('applied') is True


def call(phone, name: str, args) -> dict:
    """Run one control on the paired phone (blocking: call it off the server's loop)."""
    try:
        cmd = command(name, args)
    except CustomizationRefused as refused:
        return {'ok': False, 'status': 'refused', 'outcome': 'rejected', 'tool': name,
                'message': str(refused) + '. Nothing was sent to the phone.'}
    link = getattr(phone, 'link', None)
    send = getattr(link, 'call', None)
    if send is None:
        return {'ok': False, 'status': 'customization_unsupported', 'outcome': 'unavailable', 'tool': name,
                'message': 'This phone connection cannot change Rose customization.'}
    try:
        result = send(cmd['action'], timeout=TIMEOUT_S, packageName=cmd['packageName'],
                      operation=cmd['operation'], params=cmd['params'])
    except Exception as error:  # a lost link is not success, and the change may or may not have happened
        return {'ok': False, 'status': 'uncertain', 'outcome': 'uncertain', 'tool': name,
                'message': 'The phone did not confirm the change (' + type(error).__name__ + '); read customization_state before retrying.'}
    # A saved Home change is reported as applied only once Home has drawn that exact revision, and a theme only once
    # SystemUI acknowledged drawing it too: re-read state briefly.
    if (name in HOME_TOOLS and isinstance(result, dict) and result.get('status') == 'verified'
            and not _settled(name, result)):
        revision = (result.get('customization') or {}).get('revision')
        theme = (result.get('customization') or {}).get('theme')
        deadline = clock() + APPLY_WAIT_S
        while revision is not None and clock() < deadline:
            sleep(APPLY_POLL_S)
            try:
                state = send(cmd['action'], timeout=TIMEOUT_S, packageName=cmd['packageName'],
                             operation='customization_state', params={})
            except Exception:
                break
            if not isinstance(state, dict) or state.get('status') != 'ok':
                break
            if _drawn(state, revision):
                result = dict(result, home=state['home'])
                if name not in SYSTEM_TOOLS:
                    break
                if _system_shows(state, theme):
                    result = dict(result, system=state['system'])
                    break
    return outcome(name, result)
