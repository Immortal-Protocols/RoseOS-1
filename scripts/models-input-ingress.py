#!/usr/bin/env python3
"""Original Models signing-input ingress for os-release models-sign (OCT30-INFRA-S013 selected ingress).

Consumes only the original unsigned Models input the PR #149 profile pins (workflow models-android.yml, push to main,
its source, run and attempt) through the guard's existing same-repository authenticated Actions read (the job's
ephemeral github.token with actions: read). No other run, artifact, URL, credential or permission is accepted.

  fetch --out D       hosted guard, before the controller starts any host or anyone gains signer access:
                      run metadata (repository, workflow, event, branch, source, attempt, success), artifact metadata
                      (name, ownership, not expired, size, digest), the archive bytes hashed independently, a strict
                      six-name flat allowlist (no traversal, absolute path, link, duplicate, extra entry, encryption or
                      oversize), the profile's five external hashes plus SHA256SUMS, then the adapter's own data-only
                      verify_input (receipt source/run bindings, native library inside the APK). Writes D/input (the six
                      files) and D/models-input-receipt.json (immutable original bindings and content hashes).
  recheck --input-dir I --receipt R
                      signing job, after the same-run handoff and before any secret access: the fresh directory holds
                      exactly the six files, every hash equals both the profile and the guard receipt, and the receipt
                      names this run and the original bindings.

Prints no token, URL or response body. Exit status: 0 done, 1 refused (a fixed category on stderr), 2 invalid input.
"""
import argparse
import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT/'os/models-signing/models-sign.json'
RECEIPT_NAME = 'models-input-receipt.json'
SCHEMA = 'roseos.models-input-ingress.v1'
INPUT_FILES = {'SHA256SUMS': None, 'apk-metadata.txt': 'apkMetadataSha256', 'artifact-receipt.txt': 'artifactReceiptSha256',
    'build-receipt.txt': 'buildReceiptSha256', 'librose_chat_android.so': 'nativeSha256', 'rose-models-unsigned.apk': 'apkSha256'}
MANIFEST_NAMES = sorted(name for name, key in INPUT_FILES.items() if key)


class Refused(Exception):
    pass


def config():
    return json.loads(CONFIG_PATH.read_text())


def adapter_profile(cfg=None):
    return json.loads((ROOT/(cfg or config())['adapterProfile']).read_text())


def load_adapter():
    spec = importlib.util.spec_from_file_location('models_signing_adapter', ROOT/'workspace/models/signing/models_signing.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def original(cfg=None, profile=None):
    """The one admitted original: identity from the PR #149 profile, archive facts from the ingress config."""
    cfg, profile = cfg or config(), profile or adapter_profile(cfg)
    exp, ingress = profile['expectedInput'], (cfg or config())['input']
    return {'workflowPath': ingress['workflowPath'], 'event': exp['githubEvent'], 'ref': exp['githubRef'],
        'sourceSha': exp['sourceSha'], 'runId': exp['runId'], 'runAttempt': exp['runAttempt'],
        'artifactName': ingress['artifactName'].format(source12=exp['sourceSha'][:12], run=exp['runId'], attempt=exp['runAttempt']),
        'archiveBytes': ingress['archiveBytes'], 'archiveSha256': ingress['archiveSha256']}


# ---------------------------------------------------------------- authenticated Actions read

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # the signed archive location is fetched separately, without the job token


def _opener():
    return urllib.request.build_opener(_NoRedirect)


def github_api(path, token, api_url, accept='application/vnd.github+json', opener=None):
    """GET one repository API path. Returns (status, headers, body bytes); never raises with a URL or body."""
    request = urllib.request.Request(api_url.rstrip('/') + path, headers={'Authorization': f'Bearer {token}',
        'Accept': accept, 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'roseos-models-input-ingress'})
    try:
        with (opener or _opener()).open(request, timeout=60) as response:
            return response.status, dict(response.headers), response.read(64 << 20)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), b''
    except (urllib.error.URLError, OSError) as error:
        raise Refused(f'ingress_unreachable: {type(error).__name__}') from None


def fetch_location(location, limit, opener=None):
    """The archive bytes from the API's signed redirect: https only, an Azure blob host, no token, bounded."""
    parsed = urllib.parse.urlsplit(location or '')
    if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.blob.core.windows.net'):
        raise Refused('artifact_location_unexpected: the archive redirect is not an https Actions blob location')
    request = urllib.request.Request(location, headers={'User-Agent': 'roseos-models-input-ingress'})
    try:
        with (opener or _opener()).open(request, timeout=120) as response:
            data = response.read(limit + 1)
    except (urllib.error.URLError, OSError) as error:
        raise Refused(f'artifact_download_failed: {type(error).__name__}') from None
    if len(data) > limit:
        raise Refused('artifact_oversize: the archive exceeds its recorded size')
    return data


def _json(status, body, what):
    if status != 200:
        raise Refused(f'{what}_unavailable: HTTP {status}')
    try:
        return json.loads(body)
    except ValueError:
        raise Refused(f'{what}_unavailable: unreadable response') from None


def check_run(run, want, repository):
    problems = []
    checks = {'id': int(want['runId']), 'run_attempt': int(want['runAttempt']), 'head_sha': want['sourceSha'],
        'event': want['event'], 'path': want['workflowPath'], 'status': 'completed', 'conclusion': 'success'}
    for key, value in checks.items():
        if run.get(key) != value:
            problems.append(f'{key} is {run.get(key)!r}')
    if want['ref'] != f"refs/heads/{run.get('head_branch')}":
        problems.append(f"head_branch is {run.get('head_branch')!r}")
    repo, head = run.get('repository') or {}, run.get('head_repository') or {}
    if repo.get('full_name') != repository or head.get('full_name') != repository or not repo.get('id') or head.get('id') != repo.get('id'):
        problems.append('not a same-repository run')
    if problems:
        raise Refused('original_run_mismatch: ' + '; '.join(problems))
    return repo['id']


def check_artifact(listing, want, repository_id, now):
    matches = [a for a in listing.get('artifacts') or [] if a.get('name') == want['artifactName']]
    if len(matches) != 1:
        raise Refused(f'original_artifact_missing: {len(matches)} artifacts carry the original name')
    artifact = matches[0]
    origin = artifact.get('workflow_run') or {}
    problems = []
    if artifact.get('expired') is not False:
        problems.append('expired')
    try:
        expires = datetime.datetime.fromisoformat(str(artifact.get('expires_at')).replace('Z', '+00:00'))
        if expires <= now:
            problems.append('past its expiry')
    except ValueError:
        problems.append('no expiry')
    if artifact.get('size_in_bytes') != want['archiveBytes']:
        problems.append(f"size is {artifact.get('size_in_bytes')!r}")
    if artifact.get('digest') != 'sha256:' + want['archiveSha256']:
        problems.append('digest differs')
    if (origin.get('id') != int(want['runId']) or origin.get('head_sha') != want['sourceSha']
            or origin.get('repository_id') != repository_id or origin.get('head_repository_id') != repository_id):
        problems.append('not owned by the original run')
    if not isinstance(artifact.get('id'), int):
        problems.append('no id')
    if problems:
        raise Refused('original_artifact_mismatch: ' + '; '.join(problems))
    return artifact


def safe_members(data, limits):
    """The archive's six flat members by name, refusing anything else. Nothing is written."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError):
        raise Refused('archive_corrupt: not a readable zip') from None
    names = [info.filename for info in infos]
    for name in names:
        if name.startswith('/') or '\\' in name or '/' in name or name in ('', '.', '..'):
            raise Refused(f'archive_unsafe: path {name[:60]!r}')
    if len(names) != len(set(names)):
        raise Refused('archive_unsafe: duplicate names')
    if sorted(names) != sorted(INPUT_FILES):
        raise Refused('archive_inventory: entries are not exactly the six original files')
    files, total = {}, 0
    for info in infos:
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG) or info.is_dir():  # zip writers may omit the type bits
            raise Refused(f'archive_unsafe: {info.filename} is not a regular file')
        if info.flag_bits & 0x1:
            raise Refused(f'archive_unsafe: {info.filename} is encrypted')
        total += info.file_size
        if info.file_size > limits['memberBytes'] or total > limits['totalBytes']:
            raise Refused(f'archive_oversize: {info.filename}')
        try:
            body = archive.read(info)
        except (zipfile.BadZipFile, OSError, EOFError):
            raise Refused(f'archive_corrupt: {info.filename}') from None
        if len(body) != info.file_size:
            raise Refused(f'archive_corrupt: {info.filename} size')
        files[info.filename] = body
    return files


def check_contents(files, profile):
    """Profile hashes for the five pinned files; SHA256SUMS lists exactly those five with the same bytes."""
    exp = profile['expectedInput']
    hashes = {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}
    for name, key in INPUT_FILES.items():
        if key and hashes[name] != exp[key]:
            raise Refused(f'input_changed: {name} differs from the pinned input')
    try:
        lines = [line.split() for line in files['SHA256SUMS'].decode('ascii').splitlines()]
    except UnicodeDecodeError:
        raise Refused('input_changed: SHA256SUMS is not text') from None
    listed = {}
    for parts in lines:
        if len(parts) != 2 or parts[1] in listed:
            raise Refused('input_changed: SHA256SUMS is malformed')
        listed[parts[1]] = parts[0]
    if sorted(listed) != MANIFEST_NAMES or any(listed[n] != hashes[n] for n in MANIFEST_NAMES):
        raise Refused('input_changed: SHA256SUMS does not list exactly the five pinned files')
    return hashes


def write_input(out, files):
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise Refused('ingress_destination_exists: the consumer directory must be fresh')
    out.mkdir(mode=0o700)
    directory = out/'input'
    directory.mkdir(mode=0o700)
    for name, body in files.items():
        fd = os.open(directory/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(body)
    return directory


def fetch(out, *, token, repository, api_url, consumer, now=None, http=github_api, download=fetch_location,
          run_tool=None, cfg=None, profile=None):
    cfg = cfg or config()
    profile = profile or adapter_profile(cfg)
    want = original(cfg, profile)
    limits = cfg['input']['limits']
    now = now or datetime.datetime.now(datetime.timezone.utc)
    base = f'/repos/{repository}/actions'
    run = _json(*http(f"{base}/runs/{want['runId']}", token, api_url)[::2], 'original_run')
    repository_id = check_run(run, want, repository)
    query = urllib.parse.urlencode({'name': want['artifactName'], 'per_page': 100})
    listing = _json(*http(f"{base}/runs/{want['runId']}/artifacts?{query}", token, api_url)[::2], 'original_artifact')
    artifact = check_artifact(listing, want, repository_id, now)
    status, headers, _ = http(f"{base}/artifacts/{artifact['id']}/zip", token, api_url)
    if status != 302:
        raise Refused(f'artifact_download_failed: HTTP {status}')
    data = download({k.lower(): v for k, v in headers.items()}.get('location'), want['archiveBytes'])
    if len(data) != want['archiveBytes'] or hashlib.sha256(data).hexdigest() != want['archiveSha256']:
        raise Refused('archive_mismatch: the archive bytes are not the original archive')
    files = safe_members(data, limits)
    hashes = check_contents(files, profile)
    directory = write_input(out, files)
    adapter = load_adapter()
    try:
        adapter.verify_input(profile, directory, **({'run_tool': run_tool} if run_tool else {}))
    except adapter.Refused as error:
        raise Refused(f'input_binding: {error}') from None
    receipt = {'schema': SCHEMA, 'repository': repository, 'original': dict(want, artifactId=artifact['id']),
        'files': hashes, 'consumer': consumer, 'checkedAt': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'checks': ['run-metadata', 'artifact-metadata', 'archive-sha256', 'six-name-allowlist', 'profile-hashes',
                   'sha256sums', 'adapter-verify-input-data-only'],
        'deferred': {'aaptBadging': 'builder pinned aapt before any secret access'},
        'transport': 'the same-run handoff is a new package; its digest is not the original archive digest'}
    fd = os.open(Path(out)/RECEIPT_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(receipt, f, indent=2, sort_keys=True)
    return receipt


# ---------------------------------------------------------------- signing-job recheck

def _sha256(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise Refused(f'input_unavailable: {Path(path).name} is not a regular file')
        return hashlib.sha256(f.read()).hexdigest()


def recheck(input_dir, receipt_path, consumer_run, cfg=None, profile=None):
    """After the handoff, before any secret: the fresh directory, the profile and the guard receipt must agree."""
    cfg = cfg or config()
    profile = profile or adapter_profile(cfg)
    if not input_dir:
        raise Refused('input_unavailable: no verified input directory was handed to this job')
    base = Path(input_dir)
    if not base.is_absolute() or os.path.realpath(base) != str(base) or not base.is_dir():
        raise Refused('input_unavailable: the handed-off directory is missing or not a plain directory')
    if sorted(os.listdir(base)) != sorted(INPUT_FILES):
        raise Refused('input_unavailable: the handed-off directory does not hold exactly the six original files')
    try:
        fd = os.open(receipt_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as f:
            receipt = json.load(f)
    except (OSError, ValueError, TypeError):
        raise Refused('ingress_receipt_missing: no readable guard verification receipt') from None
    want = original(cfg, profile)
    recorded = dict(receipt.get('original') or {})
    recorded.pop('artifactId', None)
    if receipt.get('schema') != SCHEMA or recorded != want:
        raise Refused('ingress_receipt_mismatch: the receipt does not bind the original input')
    if str((receipt.get('consumer') or {}).get('runId')) != str(consumer_run):
        raise Refused('ingress_receipt_mismatch: the receipt was not produced by this run')
    exp = profile['expectedInput']
    observed = {name: _sha256(base/name) for name in INPUT_FILES}
    for name, key in INPUT_FILES.items():
        if key and observed[name] != exp[key]:
            raise Refused(f'input_changed: {name} differs from the pinned input after the handoff')
    if observed != receipt.get('files'):
        raise Refused('input_changed: the handed-off files differ from the guard receipt')
    return {'directory': str(base), 'files': observed, 'receiptSha256': hashlib.sha256(Path(receipt_path).read_bytes()).hexdigest(),
        'original': receipt['original']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = parser.add_subparsers(dest='command', required=True)
    f = sub.add_parser('fetch')
    f.add_argument('--out', required=True)
    r = sub.add_parser('recheck')
    r.add_argument('--input-dir', required=True)
    r.add_argument('--receipt', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'fetch':
            token = os.environ.get('GH_TOKEN', '')
            repository = os.environ.get('GITHUB_REPOSITORY', '')
            if not token or not repository:
                print('invalid: GH_TOKEN and GITHUB_REPOSITORY are required', file=sys.stderr)
                return 2
            consumer = {'runId': os.environ.get('GITHUB_RUN_ID'), 'runAttempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
                'job': os.environ.get('GITHUB_JOB')}
            result = fetch(args.out, token=token, repository=repository,
                api_url=os.environ.get('GITHUB_API_URL', 'https://api.github.com'), consumer=consumer)
            result = {'files': result['files'], 'consumer': result['consumer']}
        else:
            result = recheck(args.input_dir, args.receipt, os.environ.get('GITHUB_RUN_ID'))
            result = {'files': result['files']}
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
