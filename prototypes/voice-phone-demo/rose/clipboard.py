"""Copy the owner's requested text to the paired phone's clipboard (F07): the producer side of Rose Agent's
``clipboard_write`` phone command (workspace/rose-agent/app/src/dev/pixel/voice/ClipboardWrite.java).

The shared Rose voice backend is the same on the Mac and in the cloud, so both producers use this one contract:
``command(text)`` builds the command's arguments (the exact text and its UTF-8 SHA-256, so the phone refuses a text
changed in transit) and ``outcome(result)`` reads the phone's reply. Success is reported only for the phone's own
``copied`` after its write; anything else, a lost or late reply included, is not success. Write-only: the clipboard is
never read for Rose, and no outcome carries the text or its digest.
"""
from __future__ import annotations

import hashlib

ACTION = 'clipboard_write'
MAX_BYTES = 65536  # UTF-8 bytes, ClipboardWrite.MAX_BYTES

# Why nothing was copied (the phone wrote nothing), what Rose says, and whether the owner can act on it.
REFUSED = {
    'empty_text': 'There was nothing to copy.',
    'text_too_large': 'That is too long to copy to the phone (64 KB at most).',
    'malformed_text': 'That text has characters the phone clipboard cannot take exactly; nothing was copied.',
    'text_mismatch': 'The text changed on its way to the phone, so nothing was copied.',
    'clipboard_wrong_device': 'Rose Agent could not reach the phone\'s own clipboard; nothing was copied.',
    'clipboard_write_denied': 'Android is blocking Rose Agent from writing the clipboard; nothing was copied.',
    'clipboard_unavailable': 'The phone clipboard is unavailable right now; nothing was copied.',
    'clipboard_unsupported': 'This Rose Agent version cannot copy to the clipboard yet; update Rose Agent on the phone.',
    'offline': 'The phone is not connected, so nothing was copied.',
    'routine_busy': 'The phone is busy finishing another action; nothing was copied. Ask again in a moment.',
    'expired': 'The phone got the request too late, so nothing was copied.',
}
# After the phone's write call: the clipboard may hold something, but not confirmed as the owner's text.
UNCONFIRMED = {
    'clipboard_replaced': 'Something else replaced it on the phone clipboard right away; copy it again if needed.',
    'clipboard_write_failed': 'The phone could not confirm the copy; it may not be on the clipboard.',
    'clipboard_write_unconfirmed': ('The phone wrote it but could not confirm it reached the clipboard while Rose Agent is '
                                    'open on another display; check before pasting.'),
    'timeout': 'The phone did not confirm the copy in time; it may or may not be on the clipboard.',
    'running': 'The phone did not confirm the copy in time; it may or may not be on the clipboard.',
}


class ClipboardRefused(ValueError):
    """Refused before anything was sent to the phone."""

    def __init__(self, code: str):
        super().__init__(REFUSED[code])
        self.code = code


def command(text) -> dict:
    """The command's arguments for exactly ``text``: never trimmed, normalized or re-encoded."""
    if not isinstance(text, str) or not text:
        raise ClipboardRefused('empty_text')
    try:
        data = text.encode('utf-8')  # strict: a lone surrogate cannot be the same text on both ends
    except UnicodeEncodeError:
        raise ClipboardRefused('malformed_text') from None
    if len(data) > MAX_BYTES:
        raise ClipboardRefused('text_too_large')
    return {'text': text, 'sha256': hashlib.sha256(data).hexdigest()}


def outcome(result) -> dict:
    """The conversation's tool result for the phone's reply: ``ok`` only for the phone's confirmed write."""
    r = result if isinstance(result, dict) else {}
    status = str(r.get('status') or 'error')
    if status == 'copied' and r.get('written') is True and r.get('action') == ACTION:
        verified = r.get('verified') if r.get('verified') in ('readback', 'write_accepted') else 'write_accepted'
        out = {'ok': True, 'status': 'copied', 'verified': verified, 'message': 'Copied to the phone clipboard.'}
        if isinstance(r.get('characters'), int):
            out['characters'] = r['characters']
        return out
    if status == 'error' and 'Unsupported phone action' in str(r.get('message', '')):
        status = 'clipboard_unsupported'
    if status in REFUSED:
        return {'ok': False, 'status': status, 'copied': False, 'message': REFUSED[status]}
    if status in UNCONFIRMED:
        return {'ok': False, 'status': status, 'copied': 'unconfirmed', 'message': UNCONFIRMED[status]}
    # Any other reply (an error, an old or unexpected shape): not a confirmed write.
    return {'ok': False, 'status': 'clipboard_write_failed', 'copied': 'unconfirmed',
            'message': UNCONFIRMED['clipboard_write_failed']}


def copy(send, text) -> dict:
    """Validate, send through ``send(action, **args)`` (the phone link), and read the outcome."""
    try:
        args = command(text)
    except ClipboardRefused as exc:
        return {'ok': False, 'status': exc.code, 'copied': False, 'message': str(exc)}
    try:
        reply = send(ACTION, **args)
    except Exception:
        reply = {'status': 'clipboard_write_failed'}
    return outcome(reply)
