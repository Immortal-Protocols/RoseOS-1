"""Project 2: route the owner's explicit requests for the workspace page to the phone-local composer.

The page left of Home is composed on the phone (OS Product's page and Models' composer). Rose's conversation only
forwards an explicit request and reports, honestly, what the phone did. The server never generates a layout. The
carrier is OS Product's (workspace/rose-agent/docs/workspace-contract.md, #787); App Core's side is
docs/APP-BUILDER.md §4:

- request: the phone action ``workspace_prepare`` over the existing /ws/phone link, with
  ``{requestId, request: {kind: arrange | text, text?}}``. The phone gates a repeated requestId exactly once.
- reply: ``{status: committed | rejected | busy, layout_revision, shown?, reason?}``.

``ok`` is true only for ``committed`` with an integer ``layout_revision``: ``shown`` when the workspace itself is on
screen and drew it, otherwise ``prepared`` (the "Workspace ready" offer; the app in front stays in front). A lost link
or a missing answer is uncertain and is never re-sent. Direct actions run on the phone; Rose tasks chosen on the
page are started through ``/workspace/v1/tasks`` after the owner confirms.
"""
from __future__ import annotations

ACTION = 'workspace_prepare'
KINDS = ('arrange', 'text')
MAX_TEXT = 500
MAX_REQUEST_ID = 64
TIMEOUT_S = 25.0  # longer than Rose Agent's 20 s command wait, so its own reply arrives

TOOL = {
    'type': 'function', 'name': 'prepare_workspace',
    'description': ('Ask the phone to prepare the workspace page left of Home, only when the owner explicitly asks '
                    'for it: text (their words, for example "set up for cooking") or arrange (a best guess). The '
                    'phone composes it locally and the app they are in stays in front; say it is ready only when '
                    'the result says so.'),
    'inputSchema': {'type': 'object', 'properties': {
        'kind': {'type': 'string', 'enum': list(KINDS)},
        'text': {'type': 'string', 'description': 'With text: the owner\'s request in their words.'}},
        'required': ['kind'], 'additionalProperties': False},
}
POLICY_LINE = ('Use prepare_workspace only when the owner explicitly asks to set up or arrange the workspace page '
               'left of Home. It is composed on the phone: say it is ready or showing only when the result says '
               'so, and never describe a layout yourself.')

MESSAGES = {
    'shown': 'Done: the workspace page shows it now.',
    'prepared': 'Ready: the workspace is prepared. Tap Show workspace, or swipe right from Home.',
    'rejected': 'The phone could not prepare it, so the workspace did not change.',
    'unavailable': 'The workspace page is not available on this phone, so nothing changed.',
    'busy': 'The phone was busy and did not take it, so nothing changed. Ask again in a moment.',
    'offline': 'The phone is not connected or did not answer, so nothing was changed.',
    'uncertain': 'The phone did not confirm it; the workspace may or may not have changed.',
}
UNAVAILABLE_REASONS = ('unavailable', 'model_unavailable')


class PrepareRefused(ValueError):
    """An argument this contract does not allow; nothing is sent."""


def params(request_id: str, args) -> dict:
    """The request for one prepare call, or PrepareRefused."""
    if not isinstance(args, dict):
        raise PrepareRefused('Arguments must be an object')
    extra = set(args) - {'kind', 'text'}
    if extra:
        raise PrepareRefused('Unknown argument ' + sorted(extra)[0])
    kind = args.get('kind')
    if kind not in KINDS:
        raise PrepareRefused('kind must be one of ' + ', '.join(KINDS))
    if not isinstance(request_id, str) or not 0 < len(request_id) <= MAX_REQUEST_ID:
        raise PrepareRefused('requestId must be 1 to 64 characters')
    request = {'kind': kind}
    text = args.get('text')
    if kind == 'text':
        if not isinstance(text, str) or not text.strip():
            raise PrepareRefused('text needs the owner\'s request')
        text = text.strip()
        if len(text) > MAX_TEXT:
            raise PrepareRefused(f'text is longer than {MAX_TEXT} characters')
        request['text'] = text
    elif text is not None:
        raise PrepareRefused('arrange takes no text')
    return {'requestId': request_id, 'request': request}


def _reply(outcome: str, request_id: str, **extra) -> dict:
    return {'ok': outcome in ('shown', 'prepared'), 'outcome': outcome, 'requestId': request_id,
            'message': MESSAGES['offline' if extra.pop('offline', False) else outcome], **extra}


def outcome(request_id: str, result) -> dict:
    """The conversation's honest view of the phone's reply."""
    if not isinstance(result, dict):
        return _reply('uncertain', request_id)
    status = result.get('status')
    if status == 'committed':
        revision = result.get('layout_revision')
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            return _reply('uncertain', request_id)  # success without the revision it names is not a confirmation
        return _reply('shown' if result.get('shown') is True else 'prepared', request_id, revision=revision)
    if status == 'rejected':
        reason = result.get('reason') if isinstance(result.get('reason'), str) else None
        if reason in UNAVAILABLE_REASONS:
            return _reply('unavailable', request_id)
        return _reply('rejected', request_id, **({'reason': reason[:200]} if reason else {}))
    if status == 'busy':
        return _reply('busy', request_id)
    if status == 'offline' and 'layout_revision' not in result:
        return _reply('unavailable', request_id, offline=True)  # the link's own refusal: it was never sent
    message = result.get('message') if isinstance(result.get('message'), str) else ''
    if status == 'error' and message.startswith('Unsupported phone action'):
        return _reply('unavailable', request_id)  # an older Rose Agent refused the action before running it
    return _reply('uncertain', request_id)


def call(phone, request_id: str, args) -> dict:
    """Forward one explicit request to the phone (blocking: call it off the server's loop)."""
    try:
        body = params(request_id, args)
    except PrepareRefused as refused:
        return {'ok': False, 'outcome': 'rejected', 'requestId': request_id,
                'message': str(refused) + '. Nothing was sent to the phone.'}
    send = getattr(getattr(phone, 'link', None), 'call', None)
    if send is None:
        return _reply('unavailable', request_id, offline=True)
    try:
        result = send(ACTION, timeout=TIMEOUT_S, **body)
    except Exception:  # a lost link is not success, and it may or may not have composed: never re-send
        result = None
    return outcome(request_id, result)
