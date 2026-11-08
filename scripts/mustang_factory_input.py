#!/usr/bin/env python3
"""Download only the pinned public factory input on Mustang's dedicated disk.

Plan by default. No extraction, vendor generation, Make, credentials or STOP.
The existing controller/JIT/cleanup owns the VM lifecycle. Partial bytes and
failed receipts are retained; no resume, overwrite or implicit adoption.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

import mustang_controller as controller
import mustang_ephemeral as ephemeral
import mustang_hostdeps as deps
import mustang_lane
import mustang_ota

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 3600
RESERVE = 300 << 30
DIRECTORY = 'mustang-factory-inputs'
RECEIPT = 'factory-input-receipt.json'
FLAGS = {'factory_only', 'lock_admin_only', 'hostdeps_only', 'source_only', 'dry_run', 'publish', 'vendor_only'}


def guard(env, event):
    lane = mustang_lane.validate(mustang_lane.configuration())
    run = controller.invocation(lane, env)
    inputs = event.get('inputs') if isinstance(event, dict) else None
    disabled = lambda value: value is None or value is False or (type(value) is str and value == 'false')
    if env.get('GITHUB_REPOSITORY_ID') != '1428996648' or not isinstance(inputs, dict) \
            or set(inputs)-FLAGS or not (inputs.get('factory_only') is True or inputs.get('factory_only') == 'true') \
            or any(not disabled(inputs.get(k)) for k in FLAGS-{'factory_only'}):
        raise ValueError('factory input requires its exclusive fresh manual scope')
    ephemeral.require_group5(json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text()))
    pin = deps.readers.recipe(ROOT)['factory']
    if pin['url'] != 'https://dl.google.com/dl/android/aosp/'+pin['file'] \
            or Path(pin['file']).name != pin['file'] or not pin['sizeBytes'] > 0 \
            or len(bytes.fromhex(pin['sha256'])) != 32:
        raise ValueError('factory recipe differs from its pinned official input')
    return lane, run, pin


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname != 'dl.google.com' or parsed.username \
                or parsed.password or parsed.port not in (None, 443):
            raise ValueError('factory redirect leaves the official HTTPS host')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def space(path, remaining):
    info = os.statvfs(path)
    available = info.f_bavail*info.f_frsize
    if available < RESERVE+remaining or info.f_favail < 1024:
        raise ValueError('factory input reserve or inode bound exceeded')
    return available


def download(directory, pin, *, opener=None, clock=time.monotonic, observation=None):
    """The caller holds the canonical lock and owns this fresh directory."""
    deps.plain_dir(directory)
    final = directory/pin['file']
    part = directory/(pin['file']+'.part')
    if os.path.lexists(final) or os.path.lexists(part):
        raise ValueError('factory input output exists; preserve and reconcile it')
    start = clock(); deadline = start+TIMEOUT
    result = observation if observation is not None else {}
    result.update({'url': pin['url'], 'file': pin['file'], 'expectedBytes': pin['sizeBytes'],
              'expectedSha256': pin['sha256'], 'verified': False, 'partialPreservedOnFailure': True,
              'path': str(final), 'partialPath': str(part), 'downloadedBytes': 0,
              'timeoutSeconds': TIMEOUT, 'reserveBytes': RESERVE})
    digest = hashlib.sha256()
    result['availableBytesBefore'] = space(directory, pin['sizeBytes'])
    connect = opener or urllib.request.build_opener(OfficialRedirect()).open
    try:
        request = urllib.request.Request(pin['url'], headers={'Accept-Encoding': 'identity'})
        with connect(request, timeout=60) as response:
            length = response.headers.get('Content-Length', '')
            if response.status != 200 or response.geturl() != pin['url'] \
                    or response.headers.get('Content-Encoding', 'identity') != 'identity' \
                    or not length.isascii() or not length.isdigit() or int(length) != pin['sizeBytes']:
                raise ValueError('factory HTTP identity, status or length differs')
            fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, 'wb') as output:
                os.fchmod(output.fileno(), 0o644)
                while True:
                    if clock() >= deadline:
                        raise ValueError('factory input total timeout')
                    block = response.read(min(1 << 20, pin['sizeBytes']-result['downloadedBytes']+1))
                    if not block:
                        break
                    result['downloadedBytes'] += len(block)
                    if result['downloadedBytes'] > pin['sizeBytes']:
                        raise ValueError('factory input byte bound exceeded')
                    output.write(block);digest.update(block)
                    space(directory, pin['sizeBytes']-result['downloadedBytes'])
                output.flush();os.fsync(output.fileno())
            result['sha256'] = digest.hexdigest()
            if result['downloadedBytes'] != pin['sizeBytes'] or result['sha256'] != pin['sha256']:
                raise ValueError('factory input size or hash differs')
            # A hard link publishes without replacing any existing final name.
            os.link(part, final, follow_symlinks=False)
            part.unlink()
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
            result['verified'] = True
            result['availableBytesAfter'] = space(directory, 0)
    finally:
        result['elapsedSeconds'] = round(clock()-start, 3)
    return result


def guest(env):
    lane, run, pin = guard(env, deps.read_event(env))
    if env.get('GITHUB_JOB') != 'factory' or pwd.getpwuid(os.geteuid()).pw_name != 'mustang-runner':
        raise ValueError('factory input requires its dedicated unprivileged guest job')
    cfg = json.loads((ROOT/'os/build-only/mustang-runtime.json').read_text())
    dedicated = mustang_ota.module('mustang_factory_identity', 'mustang-dedicated.py')
    paths = dedicated.host_identity(lane, cfg['dataFilesystemUuid'], mount=cfg['dataMount'])
    if deps.plain_dir(paths['storageRoot']).st_dev != deps.plain_dir(paths['home']).st_dev:
        raise ValueError('factory input storage leaves the dedicated data filesystem')
    clean = deps.child_env(paths['home']/'runner', paths['storageRoot'])
    git = ['/usr/bin/git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(ROOT)]
    deadline = time.monotonic()+60
    if deps.checked(git+['rev-parse', 'HEAD'], ROOT, clean, deadline) != run['source'] \
            or deps.checked(git+['status', '--porcelain'], ROOT, clean, deadline):
        raise ValueError('factory checkout differs from captured clean source')
    return lane, dict(run=run, builder=lane['vm'], diskUuid=cfg['dataFilesystemUuid']), paths, pin


def execute(env):
    lane, identity, paths, pin = guest(env)
    with deps.lock(Path(lane['lockPath'])):
        attempt = paths['storageRoot']/('mustang-factory-'+identity['run']['runId']+'-'+identity['run']['runAttempt'])
        attempt.mkdir(mode=0o750);deps.plain_dir(attempt)
        result = {'schema': 'roseos.mustang-factory-input.v1', 'identity': identity,
                  'scope': 'pinned-public-factory-download-only', 'success': False,
                  'authenticatedStop': False, 'extracted': False, 'vendorGenerated': False,
                  'buildStarted': False, 'signingStarted': False}
        try:
            directory = paths['storageRoot']/DIRECTORY
            directory.mkdir(mode=0o750);deps.plain_dir(directory)
            result['download'] = {}
            download(directory, pin, observation=result['download'])
            measured = deps.readers.measure_factory(str(paths['storageRoot']), str(directory/pin['file']))
            if measured['matchesPinnedFactory'] is not True:
                raise ValueError('factory input readback did not match pinned archive')
            result['readback'] = measured;result['success'] = True
        except BaseException as error:
            result['failureType'] = type(error).__name__
            # Never serialize arbitrary network error text, which may contain URLs.
            result['failureCode'] = str(error) if type(error) is ValueError else 'factory-input-operation-failed'
            if isinstance(error, urllib.error.HTTPError):
                result['failureCode'] = 'official-download-http-refused'
                result['httpStatus'] = error.code
            raise
        finally:
            deps.receipt(attempt/RECEIPT, result)
        return result


def report(env, destination):
    _lane, identity, paths, _pin = guest(env)
    attempt = paths['storageRoot']/('mustang-factory-'+identity['run']['runId']+'-'+identity['run']['runAttempt'])
    data = json.loads(deps.readers.read_regular(str(paths['storageRoot']), str(attempt/RECEIPT), 65536))
    if data.get('schema') != 'roseos.mustang-factory-input.v1' or data.get('identity') != identity:
        raise ValueError('factory receipt belongs to another run or builder')
    deps.receipt(destination, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'run', 'report'), nargs='?', default='plan')
    parser.add_argument('--execute', action='store_true');parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.action == 'plan' or (args.action == 'run' and not args.execute):
        print(json.dumps({'execute': False, 'factory': deps.readers.recipe()['factory'],
                          'timeoutSeconds': TIMEOUT, 'scope': 'pinned-public-factory-download-only'}));return 4
    if args.action == 'report':
        if args.out is None: raise ValueError('receipt output is required')
        report(os.environ, args.out)
    else: execute(os.environ)
    return 0


if __name__ == '__main__':
    def cancelled(_signum, _frame):
        raise ValueError('factory input cancelled')
    signal.signal(signal.SIGTERM, cancelled)
    try: code = main()
    except Exception as error:
        print(json.dumps({'success': False, 'failureType': type(error).__name__}));code = 4
    sys.exit(code)
