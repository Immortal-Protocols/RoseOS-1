"""A voice request for an app reaches the app builder (docs/APP-BUILDER.md §6): one conversation tool,
``open_app_builder``, that opens Rose Agent's builder on the phone with the owner's words filled in. The owner reviews
the request and taps Build there; nothing is built, queued or sent to the builder by voice.

It is the existing phone command and nothing new on the phone: ``app_command`` to ``dev.pixel.voice`` with operation
``open_builder`` and params ``{description}``, which Rose Agent's own agent provider (SettingsAgentProvider, #881)
answers with ``{status: "launch_requested", built: false}``, or ``{status: "locked"}`` while the keyguard is up.
"""
from __future__ import annotations

PACKAGE = 'dev.pixel.voice'
OPERATION = 'open_builder'
NAME = 'open_app_builder'
NAMES = frozenset({NAME})
MAX_DESCRIPTION = 4000
TIMEOUT_S = 12.0  # the provider answers at once; the link adds its own round trip
# Replies after which the builder may already be open: the provider's own wait ran out, the phone's receipt is still
# running, the link's wait ran out, or the link dropped mid-call (as rose.customization and rose.composer read them).
UNCERTAIN = frozenset({'uncertain', 'running', 'timeout', 'disconnected'})

TOOLS = [{'type': 'function', 'name': NAME,
          'description': ('Open the app builder on the owner\'s phone with their description of an app filled in, for '
                          'them to review and tap Build. It never builds anything itself.'),
          'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['description'],
                          'properties': {'description': {'type': 'string', 'maxLength': MAX_DESCRIPTION,
                                                         'description': 'The app the owner asked for, in their words.'}}}}]

POLICY_LINE = ('When the owner asks you to make, build or change an app of their own, use open_app_builder with their '
               'description: it opens the app builder on the phone with it filled in, and they tap Build there. Say '
               'the builder is open for them to review; never say the app is built, building or ready. If it reports '
               'the phone is locked, ask them to unlock it and ask again.')


def call(phone, name: str, args) -> dict:
    """Open the builder on the paired phone (blocking: call it off the server's loop). Never retried: a lost reply
    may still have opened it."""
    description = args.get('description') if isinstance(args, dict) else None
    if name != NAME or not isinstance(description, str) or not description.strip() \
            or len(description) > MAX_DESCRIPTION or set(args) - {'description'}:
        return {'ok': False, 'status': 'refused', 'tool': NAME,
                'message': 'open_app_builder takes the app\'s description as text. Nothing was sent to the phone.'}
    send = getattr(getattr(phone, 'link', None), 'call', None)
    if send is None:
        return {'ok': False, 'status': 'unavailable', 'tool': NAME,
                'message': 'This phone connection cannot open the app builder.'}
    try:
        result = send('app_command', timeout=TIMEOUT_S, packageName=PACKAGE, operation=OPERATION,
                      params={'description': description.strip()})
    except Exception as error:
        return {'ok': False, 'status': 'uncertain', 'tool': NAME,
                'message': f'The phone did not confirm it ({type(error).__name__}); the builder may or may not be open.'}
    status = result.get('status') if isinstance(result, dict) else None
    if status == 'launch_requested':
        return {'ok': True, 'status': status, 'built': False, 'tool': NAME,
                'message': 'The app builder is open on the phone with the request filled in; the owner taps Build there.'}
    if status in UNCERTAIN:  # sent, and not answered: it may have opened, so it is never reported as not opened
        return {'ok': False, 'status': 'uncertain', 'tool': NAME,
                'message': ('The phone did not confirm it; the builder may or may not be open. Ask the owner to check '
                            'the phone before asking again.')}
    if status == 'locked':
        return {'ok': False, 'status': status, 'tool': NAME,
                'message': 'The phone is locked, so the builder was not opened; ask the owner to unlock it and ask again.'}
    return {'ok': False, 'status': status or 'uncertain', 'tool': NAME,
            'message': 'The phone did not open the app builder' + (f' ({status}).' if status else '.')}
