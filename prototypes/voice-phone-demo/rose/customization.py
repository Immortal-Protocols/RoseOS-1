"""Rose's four live customization controls (Nov 7 plan 1O) as conversation tools: the producer side of Rose Agent's
``customization_*`` app commands (workspace/rose-agent/app/src/dev/pixel/voice/CustomizationConfig.java).

One catalog, ``workspace/rose-agent/app/assets/customization-agent-tools.json``, defines the controls for every
caller: Rose Agent serves it as its app-tool capabilities, this module turns it into conversation tools, and the CLI
(``workspace/rose-agent/customization/rose-customize.mjs``) reads the same file. Each call is the same command:
``app_command`` to ``dev.pixel.voice`` with the tool's operation and the validated arguments, over the existing phone
link. Nothing here adds an endpoint or a phone action.

The controls are the Home column count (0 = automatic), a curated Oxblood wallpaper variant, a bundled font, and the
glasses-on hook. The hook runs only on a real glasses-worn signal; ``customization_state`` reports whether this phone
has one (at this revision it does not, and connection is never substituted).

Success is the phone's own ``verified``/``ok`` after it stored the value. Anything else, a lost or late reply
included, is not success, and nothing is retried here.
"""
from __future__ import annotations

import json
from pathlib import Path

PACKAGE = 'dev.pixel.voice'
CATALOG = Path(__file__).resolve().parents[3] / 'workspace/rose-agent/app/assets/customization-agent-tools.json'
TIMEOUT_S = 12.0  # SettingsAgentProvider allows 8 s per operation; the link adds its own round trip.

POLICY_LINE = ('Use the customization tools only when the owner asks to change or check Rose Home columns, the Oxblood '
               'wallpaper variant, the font or the glasses-on hook; read customization_state for relative requests and '
               'report the returned state, including when the glasses-worn signal is unavailable.')


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


def outcome(name: str, result) -> dict:
    """The conversation's view of the phone's reply: ok only for the phone's own verified/ok."""
    if not isinstance(result, dict):
        return {'ok': False, 'status': 'uncertain', 'tool': name,
                'message': 'The phone did not confirm the change; read customization_state before retrying.'}
    status = str(result.get('status', ''))
    out = {k: result[k] for k in ('status', 'changed', 'customization', 'home', 'glassesWear', 'reason')
           if k in result}
    out['ok'] = status in ('verified', 'ok')
    out['tool'] = name
    if not out['ok']:
        out.setdefault('message', str(result.get('reason') or result.get('message')
                                      or 'The phone did not confirm the change; read customization_state before retrying.'))
    return out


def call(phone, name: str, args) -> dict:
    """Run one control on the paired phone (blocking: call it off the server's loop)."""
    try:
        cmd = command(name, args)
    except CustomizationRefused as refused:
        return {'ok': False, 'status': 'refused', 'tool': name, 'message': str(refused)}
    link = getattr(phone, 'link', None)
    send = getattr(link, 'call', None)
    if send is None:
        return {'ok': False, 'status': 'customization_unsupported', 'tool': name,
                'message': 'This phone connection cannot change Rose customization.'}
    try:
        result = send(cmd['action'], timeout=TIMEOUT_S, packageName=cmd['packageName'],
                      operation=cmd['operation'], params=cmd['params'])
    except Exception as error:  # a lost link is not success, and the change may or may not have happened
        return {'ok': False, 'status': 'uncertain', 'tool': name,
                'message': 'The phone did not confirm the change (' + type(error).__name__ + '); read customization_state before retrying.'}
    return outcome(name, result)
