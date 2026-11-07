#!/usr/bin/env python3
"""Controller for one opt-in possess install.

The journal is revoke-required before the guest can write a line. This module
does not call GCP, start an instance, or SSH. ``guest-install-execute`` refuses.
"""
import hashlib
import json
import os
from pathlib import Path

import yogi_possess_guest as guest
import yogi_possession

JOURNAL = 'possess-journal.json'
SKEW_SECONDS = 30
MIN_REMAINING_SECONDS = 90
SCHEMA = 'roseos.yogi-possess-journal.v1'


class Refused(guest.GuestRefused):
    pass


def _atomic_json(directory, name, payload):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    text = (json.dumps(payload, sort_keys=True) + '\n').encode()
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    tmp = f'.{name}.{os.getpid()}.tmp'
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dirfd)
        try:
            os.write(fd, text)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(f'/proc/self/fd/{dirfd}/{tmp}', f'/proc/self/fd/{dirfd}/{name}')
        os.fsync(dirfd)
    finally:
        os.close(dirfd)
    written = (directory / name).read_bytes()
    if written != text:
        raise Refused('journal-write')


def _load(out):
    path = Path(out) / JOURNAL
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        raise Refused('journal-unreadable') from None
    if not isinstance(record, dict) or record.get('schema') != SCHEMA:
        raise Refused('journal-schema')
    return record


def journal_status(out):
    path = Path(out) / JOURNAL
    if not path.is_file():
        return 'absent'
    try:
        return _load(out).get('state') or 'unreadable'
    except Refused:
        return 'unreadable'


def begin(out, operation_id, blob):
    """Durable revoke-required intent. Call this before any guest install."""
    comment = guest.comment_for(operation_id)
    if not guest.BLOB_RE.fullmatch(blob or ''):
        raise Refused('blob')
    path = Path(out) / JOURNAL
    if path.exists():
        current = _load(out)
        if current.get('state') != 'revoked':
            raise Refused('unrevoked-journal')
        archived = path.with_name(f'possess-journal.{current.get("operationId")}.revoked.json')
        os.replace(path, archived)
    record = {
        'schema': SCHEMA,
        'state': 'revoke-required',
        'operationId': operation_id,
        'comment': comment,
        'blob': blob,
        'exactLine': None,
        'probeSha256': hashlib.sha256(yogi_possession.PROBE.encode()).hexdigest(),
        'unit': yogi_possession.POSSESSION_UNIT,
        'backwardClock': guest.BACKWARD_CLOCK,
        'qualifiedParser': guest.QUALIFIED_PARSER,
        'guestAuthenticated': False,
        'clean': False,
        'startupScriptInstalled': False,
    }
    _atomic_json(out, JOURNAL, record)
    stored = _load(out)
    if stored.get('state') != 'revoke-required' or stored.get('exactLine') is not None:
        raise Refused('journal-write')
    return stored


def _clock_precheck(evidence, controller_unix):
    if not isinstance(evidence, dict) or evidence.get('synchronized') is not True:
        raise Refused('clock-unsynchronized')
    if not guest.version_allowed(evidence.get('sshdVersion')):
        raise Refused('sshd-version')
    guest_unix = evidence.get('guestUnix')
    if not isinstance(guest_unix, int) or not isinstance(controller_unix, int):
        raise Refused('clock')
    if abs(guest_unix - controller_unix) > SKEW_SECONDS:
        raise Refused('clock-skew')


def apply_install(out, root, evidence, controller_unix):
    record = _load(out)
    if record.get('state') != 'revoke-required':
        raise Refused('journal-state')
    if record.get('exactLine'):
        raise Refused('install-already-recorded')
    _clock_precheck(evidence, controller_unix)
    if hashlib.sha256(evidence.get('probe', '').encode()).hexdigest() != record['probeSha256']:
        raise Refused('probe')
    if evidence.get('unit') != record['unit']:
        raise Refused('unit')
    try:
        receipt = guest.install(
            root, blob=record['blob'], operation_id=record['operationId'], probe=evidence['probe'],
            unit=evidence['unit'], now_unix=evidence['guestUnix'], synchronized=True,
            sshd_version=evidence['sshdVersion'], sshd_config=evidence['sshdConfig'],
            passwd_text=evidence['passwd'])
    except guest.GuestRefused as error:
        raise Refused(error.code) from None
    remaining = receipt['expiryUnix'] - controller_unix
    if receipt['expiryUnix'] != evidence['guestUnix'] + guest.MAX_EXPIRY_SECONDS:
        raise Refused('expiry-window')
    if remaining < MIN_REMAINING_SECONDS or remaining > guest.MAX_EXPIRY_SECONDS + SKEW_SECONDS:
        raise Refused('expiry-window')
    record['exactLine'] = receipt['line']
    record['expiry'] = receipt['expiry']
    record['guestUnix'] = receipt['guestUnix']
    record['guestAuthenticated'] = False
    record['clean'] = False
    _atomic_json(out, JOURNAL, record)
    receipt['journal'] = 'revoke-required'
    receipt['guestAuthenticated'] = False
    receipt['clean'] = False
    return receipt


def resume_revoke(out, root, passwd_text):
    """Idempotent exact revoke. Does not restore a backup and does not mark clean."""
    record = _load(out)
    if record.get('state') != 'revoke-required':
        raise Refused('journal-state')
    try:
        result = guest.revoke(root, blob=record['blob'], comment=record['comment'], passwd_text=passwd_text,
                              exact_line=record.get('exactLine'))
    except guest.GuestRefused as error:
        raise Refused(error.code) from None
    record['revoke'] = {'removed': bool(result.get('removed')), 'absent': bool(result.get('absent')),
                        'rewritten': bool(result.get('rewritten'))}
    record['guestAuthenticated'] = False
    record['clean'] = False
    _atomic_json(out, JOURNAL, record)
    result['journal'] = 'revoke-required'
    result['guestAuthenticated'] = False
    result['clean'] = False
    return result


def accept_inspect(out, inspect_receipt):
    """Clean only after a separate inspect reads both paths and finds no marker or blob."""
    record = _load(out)
    if record.get('state') != 'revoke-required':
        raise Refused('journal-state')
    revoke = record.get('revoke')
    if not isinstance(revoke, dict) or not (revoke.get('removed') or revoke.get('absent')):
        raise Refused('revoke-not-run')
    if not isinstance(inspect_receipt, dict):
        raise Refused('inspect')
    if inspect_receipt.get('authPathsRead') != 2 or inspect_receipt.get('possessMarkerLines') != 0:
        raise Refused('inspect-not-clean')
    if inspect_receipt.get('blobPresent') is not False:
        raise Refused('inspect-not-clean')
    record['state'] = 'revoked'
    record['guestAuthenticated'] = False
    record['clean'] = True
    record['inspect'] = {
        'authPathsRead': 2, 'possessMarkerLines': 0, 'blobPresent': False,
    }
    _atomic_json(out, JOURNAL, record)
    return record


def render_bootstrap(binding):
    """Python startup text. It is not installed by this function."""
    if not isinstance(binding, dict) or binding.get('action') not in {'install', 'revoke'}:
        raise Refused('binding')
    source = Path(__file__).resolve().parent.joinpath('yogi_possess_guest.py').read_text()
    if 'BINDING_JSON = None' not in source:
        raise Refused('bootstrap-marker')
    encoded = json.dumps(binding, sort_keys=True)
    if '@@' in encoded:
        raise Refused('binding')
    rendered = source.replace('BINDING_JSON = None', 'BINDING_JSON = ' + json.dumps(encoded), 1)
    if 'google-guest-agent' in rendered or 'systemctl enable' in rendered or 'set-metadata' in rendered:
        raise Refused('bootstrap-scope')
    return rendered


def write_plan(out):
    plan = {
        'schema': 'roseos.yogi-possess-guest-plan.v1',
        'liveExecute': False,
        'mutation': False,
        'handshakeUnchanged': True,
        'journal': 'written only by begin(), before install',
        'guestAuthenticated': False,
        'clean': False,
        'qualifiedParser': guest.QUALIFIED_PARSER,
        'backwardClock': guest.BACKWARD_CLOCK,
        'maxExpirySeconds': guest.MAX_EXPIRY_SECONDS,
        'deadlineSeconds': guest.MAX_DEADLINE_SECONDS,
        'skewSeconds': SKEW_SECONDS,
        'source': source_hashes(),
    }
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / 'guest-install-plan.json').write_text(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    return plan


def source_hashes():
    root = Path(__file__).resolve().parent
    names = ('yogi_possess_guest.py', 'yogi_possess_install.py')
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def live_execute_refused():
    raise Refused('live guest install is not authorized')
