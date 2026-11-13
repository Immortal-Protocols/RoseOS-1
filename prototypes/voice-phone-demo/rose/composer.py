"""Project 2: route the owner's explicit requests for the workspace page to the phone-local composer.

The page left of Home is composed on the phone (OS Product's composer and model). Rose's conversation only forwards
an explicit request and reports, honestly, what the phone did. The server never generates a layout. The contract is
docs/APP-BUILDER.md §4 (composer intent/result v1):

- request ``{schemaVersion: 1, requestId, kind, text?}`` with ``kind`` prompt | arrange | regenerate | undo, sent as
  ``app_command`` operation ``workspace_compose`` to the composer provider over the existing phone link;
- result ``{schemaVersion: 1, requestId, status, revision?, reason?}`` with ``status`` shown | prepared | rejected |
  unavailable | uncertain.

``ok`` is true only when the phone says it drew (shown) or composed and saved (prepared) that exact revision, never
for a mere submission. A lost link is uncertain and is never re-sent. Until a provider reports this contract through
its ``capabilities`` operation, nothing is sent and the outcome is unavailable, so Rose never claims a page she
cannot change. Direct actions run on the phone; agent tasks chosen on the page use the existing task route.
"""
from __future__ import annotations

SCHEMA_VERSION = 1
OPERATION = 'workspace_compose'
KINDS = ('prompt', 'arrange', 'regenerate', 'undo')
STATUSES = ('shown', 'prepared', 'rejected', 'unavailable', 'uncertain')
MAX_TEXT = 500
MAX_REQUEST_ID = 64
TIMEOUT_S = 25.0  # longer than Rose Agent's 20 s app_command wait, so a slow composition's answer is not dropped

TOOL = {
    'type': 'function', 'name': 'compose_workspace',
    'description': ('Ask the phone to compose the workspace page left of Home, only when the owner explicitly asks for '
                    'it: prompt (their words, for example "set up for cooking"), arrange (a best guess), regenerate '
                    'or undo. The phone composes it locally; say it changed only when the result is shown or '
                    'prepared.'),
    'inputSchema': {'type': 'object', 'properties': {
        'kind': {'type': 'string', 'enum': list(KINDS)},
        'text': {'type': 'string', 'description': 'With prompt: the owner\'s request in their words.'}},
        'required': ['kind'], 'additionalProperties': False},
}
POLICY_LINE = ('Use compose_workspace only when the owner explicitly asks to set up, rearrange, regenerate or undo the '
               'workspace page left of Home. It is composed on the phone: say it changed only when the result is '
               'shown or prepared, and never describe a layout yourself.')

MESSAGES = {
    'shown': 'Done: the workspace page shows it now.',
    'prepared': 'Ready: the workspace page is prepared and shows it when the owner swipes to it.',
    'rejected': 'The phone refused it, so the workspace page did not change.',
    'unavailable': 'The workspace page is not available on this phone, so nothing changed.',
    'offline': 'The phone is not connected or did not answer, so nothing was changed.',
    'unsupported': 'The workspace page on this phone does not support that yet, so nothing changed.',
    'busy': 'The phone is busy and did not take it, so nothing changed. Ask again in a moment.',
    'uncertain': 'The phone did not confirm it; the workspace page may or may not have changed.',
}


class ComposeRefused(ValueError):
    """An argument this contract does not allow; nothing is sent."""


def params(request_id: str, args) -> dict:
    """The v1 request for one compose call, or ComposeRefused."""
    if not isinstance(args, dict):
        raise ComposeRefused('Arguments must be an object')
    extra = set(args) - {'kind', 'text'}
    if extra:
        raise ComposeRefused('Unknown argument ' + sorted(extra)[0])
    kind = args.get('kind')
    if kind not in KINDS:
        raise ComposeRefused('kind must be one of ' + ', '.join(KINDS))
    if not isinstance(request_id, str) or not 0 < len(request_id) <= MAX_REQUEST_ID:
        raise ComposeRefused('requestId must be 1 to 64 characters')
    out = {'schemaVersion': SCHEMA_VERSION, 'requestId': request_id, 'kind': kind}
    text = args.get('text')
    if kind == 'prompt':
        if not isinstance(text, str) or not text.strip():
            raise ComposeRefused('prompt needs the owner\'s request as text')
        text = text.strip()
        if len(text) > MAX_TEXT:
            raise ComposeRefused(f'text is longer than {MAX_TEXT} characters')
        out['text'] = text
    elif text is not None:
        raise ComposeRefused(f'{kind} takes no text')
    return out


def supports(capabilities, kind: str) -> bool:
    """Whether a provider's capabilities reply offers this contract version and kind."""
    return (isinstance(capabilities, dict) and capabilities.get('status') == 'ok'
            and capabilities.get('schemaVersion') == SCHEMA_VERSION
            and isinstance(capabilities.get('kinds'), list) and kind in capabilities['kinds'])


def outcome(request_id: str, result) -> dict:
    """The conversation's honest view of the phone's reply. Anything malformed, for another request or of another
    version is uncertain: the page may or may not have changed."""
    status = 'uncertain'
    revision = reason = None
    if (isinstance(result, dict) and result.get('schemaVersion') == SCHEMA_VERSION
            and result.get('requestId') == request_id and result.get('status') in STATUSES):
        status = result['status']
        revision = result.get('revision')
        if status in ('shown', 'prepared') and (not isinstance(revision, int) or isinstance(revision, bool)
                                                or revision < 0):
            status, revision = 'uncertain', None  # success without the revision it names is not a confirmation
        if status == 'rejected':
            reason = result.get('reason') if isinstance(result.get('reason'), str) else None
    out = {'ok': status in ('shown', 'prepared'), 'outcome': status, 'requestId': request_id,
           'message': MESSAGES[status]}
    if out['ok']:
        out['revision'] = revision
    if reason:
        out['reason'] = reason[:200]
    return out


def call(phone, provider: str | None, request_id: str, args) -> dict:
    """Forward one explicit request to the phone's composer (blocking: call it off the server's loop)."""
    try:
        request = params(request_id, args)
    except ComposeRefused as refused:
        return {'ok': False, 'outcome': 'rejected', 'requestId': request_id,
                'message': str(refused) + '. Nothing was sent to the phone.'}
    send = getattr(getattr(phone, 'link', None), 'call', None)
    if not provider:
        return {'ok': False, 'outcome': 'unavailable', 'requestId': request_id, 'message': MESSAGES['unavailable']}
    offline = {'ok': False, 'outcome': 'unavailable', 'requestId': request_id, 'message': MESSAGES['offline']}
    if send is None:
        return offline
    try:
        capabilities = send('app_command', timeout=TIMEOUT_S, packageName=provider, operation='capabilities',
                            params={})
    except Exception:
        return offline
    if isinstance(capabilities, dict) and capabilities.get('status') in ('timeout', 'offline', 'disconnected'):
        return offline
    if isinstance(capabilities, dict) and capabilities.get('status') in ('routine_busy', 'running', 'expired',
                                                                         'error', 'uncertain'):
        return {'ok': False, 'outcome': 'unavailable', 'requestId': request_id, 'message': MESSAGES['busy']}
    if not supports(capabilities, request['kind']):
        offers_contract = (isinstance(capabilities, dict) and capabilities.get('status') == 'ok'
                           and capabilities.get('schemaVersion') == SCHEMA_VERSION)
        return {'ok': False, 'outcome': 'unavailable', 'requestId': request_id,
                'message': MESSAGES['unsupported' if offers_contract else 'unavailable']}
    try:
        result = send('app_command', timeout=TIMEOUT_S, packageName=provider, operation=OPERATION, params=request)
    except Exception:  # a lost link is not success, and it may or may not have composed: never re-send
        result = None
    if isinstance(result, dict) and result.get('status') == 'offline':
        return offline  # the link says the command was never sent
    # 'disconnected' means the link dropped after sending: the phone may have composed it, so that is uncertain.
    return outcome(request_id, result)
