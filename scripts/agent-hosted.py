#!/usr/bin/env python3
"""The hosted Rose Agent route of os-release.yml (modes agent-hosted and agent-hosted-probe), on GitHub-hosted runners.

Release's steps around App's portable build (workspace/rose-agent/portable-build.py), the hosted build and signing
(scripts/host-app-release.py --hosted) and the hosted publisher (scripts/app_release_publisher.py --hosted). Every
committed value comes from os/release-pipeline/agent/hosted.json; nothing here reads the Frankel builder.

  public-inputs --apk FILE --out DIR
      The five public connection inputs App's build needs (host-app-build.PUBLIC_ASSETS), taken from the one pinned
      public Rose Agent APK (publicInputs.sourceApk: its exact size and sha256). The four an APK carries byte for byte
      (connection, mac-screen and clipboard JSON, the raw certificate) are copied out unchanged; the network security
      config, which an APK carries only compiled, is regenerated from the connection endpoint's host with App's own
      template (scripts/configure-local.py). Each file must equal its committed digest (publicInputs.digests). DIR
      receives them at their candidate-relative paths with public-assets-receipt.json, exactly what portable-build.py
      prepare reads. Nothing is invented: a source that differs in any byte refuses.
  check-apk --apk FILE
      A built APK (unsigned or signed) carries exactly the pinned source APK's five input entries: the four files
      unchanged and the compiled network security config byte-identical (so the regenerated source compiles to what
      the published Agent carries).
  probe-signer --out FILE
      After the job's own signer login (decision D4): each of the four PIXEL_APP_SIGNING_* versions the committed config
      names is read, with the same helper the signing uses, into an owner-only RAM directory that is overwritten and
      removed at once. The nonsecret receipt names each secret and whether it was read; no payload, size or digest.
  reserve --out DIR
      From the dispatch commit (GITHUB_SHA): either the newest published rose-agent release already has these Rose Agent
      sources (app_skip=true; nothing is reserved), or one fresh versionCode above everything consumed (the shared
      ledger's reservations and its highest builder floor, every pixel-ota release including drafts, the committed
      Frankel config and allocation records), derived as release-allocate.py's version-only candidate and held
      atomically in the shared ledger (scripts/app_reservation_store.py) for this run. It refuses with exit 4 while the
      ledger holds no builder floor (decision D2: the Frankel builder's own consumed history is not yet in the ledger).
  bind --dir DIR
      In the build job: the reserve job's allocation and reservation re-derive the same candidate, are owned by this
      run and are held by the ledger; exports ROSE_CANDIDATE_SHA, APPS and ROSE_APP_RESERVATION.
  confirm --reservation FILE
      In the publisher job: the ledger still holds exactly this run's reservation.
Exit status: 0 done, 1 refused, 2 usage, 4 setup missing. Errors never carry a token, secret or response body.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlparse
from xml.sax.saxutils import escape
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_reservation  # noqa: E402
import app_reservation_store as ledger_store  # noqa: E402
import host_secrets  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HOSTED = ROOT/'os/release-pipeline/agent/hosted.json'
PACKAGE = 'dev.pixel.voice'
SECRETS = ('keystore', 'config', 'storePassword', 'keyPassword')
RECEIPT = 'public-assets-receipt.json'
NETWORK = 'workspace/rose-agent/app/res/xml/mac_screen_network.xml'
# Where a published APK carries each public input it has ever had (workspace/rose-agent/build.sh copies the stored ones
# unchanged); the inputs host-app-build.PUBLIC_ASSETS names now are taken from it (apk_entries()).
KNOWN_APK_ENTRIES = {'workspace/rose-agent/app/assets/connection.json': 'assets/connection.json',
               'workspace/rose-agent/app/assets/mac-screen-connection.json': 'assets/mac-screen-connection.json',
               'workspace/pixel-clipboard/clipboard-connection.json': 'assets/clipboard-connection.json',
               'workspace/rose-agent/app/res/raw/mac_screen_cert.pem': 'res/raw/mac_screen_cert.pem',
               NETWORK: 'res/xml/mac_screen_network.xml'}
# App's network security config template (scripts/configure-local.py), around the escaped endpoint host.
NETWORK_HEAD = ('<network-security-config><base-config cleartextTrafficPermitted="false"><trust-anchors><certificates '
                'src="system"/></trust-anchors></base-config><domain-config cleartextTrafficPermitted="false"><domain '
                'includeSubdomains="false">')
NETWORK_TAIL = ('</domain><trust-anchors><certificates src="system"/><certificates src="@raw/mac_screen_cert"/>'
                '</trust-anchors></domain-config></network-security-config>\n')
APK_MAX_BYTES = 64 * 1024**2
VERSION_SUFFIX = 'hosted-beta'
RELEASES_API = 'https://api.github.com'


class Refused(Exception):
    pass


class SetupMissing(Exception):
    def __init__(self, missing):
        super().__init__(', '.join(missing))
        self.missing = missing


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(data):
    return hashlib.sha256(data).hexdigest()


ALLOC = load('release-allocate')  # one instance, so its Refused is the class main() reports


def config(path=HOSTED):
    return json.loads(Path(path).read_text())


def apk_entries():
    """{candidate-relative input: APK entry} for exactly the inputs host-app-build.PUBLIC_ASSETS names now."""
    assets = load('host-app-build').PUBLIC_ASSETS
    unknown = [a for a in assets if a not in KNOWN_APK_ENTRIES]
    if unknown or NETWORK not in assets:
        raise Refused('the public-input map does not cover host-app-build.PUBLIC_ASSETS: ' + ', '.join(unknown or [NETWORK]))
    return {a: KNOWN_APK_ENTRIES[a] for a in assets}


def pinned_inputs(cfg):
    """The committed public-input pin: the source APK {url, size, sha256}, each input's digest and the compiled config's."""
    pin = cfg.get('publicInputs') or {}
    apk, digests = pin.get('sourceApk') or {}, pin.get('digests')
    assets = list(apk_entries())
    if not isinstance(apk.get('url'), str) or not apk['url'].startswith('https://github.com/') \
            or type(apk.get('size')) is not int or not 0 < apk['size'] <= APK_MAX_BYTES \
            or not re.fullmatch(r'[0-9a-f]{64}', str(apk.get('sha256'))):
        raise SetupMissing(['publicInputs.sourceApk (url, size, sha256)'])
    if not isinstance(digests, dict) or set(digests) != set(assets) \
            or not all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for v in digests.values()) \
            or not re.fullmatch(r'[0-9a-f]{64}', str(pin.get('compiledNetworkSha256'))):
        raise SetupMissing(['publicInputs.digests and publicInputs.compiledNetworkSha256'])
    return pin


def network_config(endpoint):
    """App's template for the endpoint's host, exactly as scripts/configure-local.py writes it."""
    url = urlparse(endpoint)
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise Refused('the pinned connection endpoint is not an https URL without credentials')
    return (NETWORK_HEAD + escape(url.hostname) + NETWORK_TAIL).encode()


def source_entries(apk_path, pin):
    """{relative: bytes} of the five inputs from the pinned APK, after its exact size and sha256 are checked."""
    data = Path(apk_path).read_bytes() if not Path(apk_path).is_symlink() and Path(apk_path).is_file() else None
    if data is None or len(data) != pin['sourceApk']['size'] or sha256(data) != pin['sourceApk']['sha256']:
        raise Refused('the public-input source is not the pinned APK (size and sha256)')
    with zipfile.ZipFile(apk_path) as apk:
        names = apk.namelist()
        entries = apk_entries()
        if any(names.count(entry) != 1 for entry in entries.values()):
            raise Refused('the pinned APK does not carry each public input exactly once')
        found = {relative: apk.read(entry) for relative, entry in entries.items()}
    endpoint = json.loads(found['workspace/rose-agent/app/assets/connection.json'])['endpoint']
    compiled = found[NETWORK]
    found[NETWORK] = network_config(endpoint)
    return found, compiled


def public_inputs(apk_path, out, cfg):
    pin = pinned_inputs(cfg)
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise Refused(f'{out} already exists; use a new directory')
    files, compiled = source_entries(apk_path, pin)
    if sha256(compiled) != pin['compiledNetworkSha256']:
        raise Refused('the pinned APK\'s compiled network security config is not the pinned one')
    wrong = sorted(r for r, data in files.items() if sha256(data) != pin['digests'][r])
    if wrong:
        raise Refused('public inputs differ from their committed digests: ' + ', '.join(wrong))
    for relative, data in sorted(files.items()):
        target = out/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    receipt = {'receiptType': 'public-connection-assets', 'files': {r: sha256(d) for r, d in sorted(files.items())}}
    (out/RECEIPT).write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return {'schema': 'roseos.agent-public-inputs.v1', 'sourceApk': pin['sourceApk'], 'files': receipt['files'],
            'regenerated': [NETWORK], 'compiledNetworkSha256': pin['compiledNetworkSha256']}


def check_apk(apk_path, cfg):
    """The built APK's five input entries equal the pinned source APK's (the compiled config by its pinned digest)."""
    pin = pinned_inputs(cfg)
    with zipfile.ZipFile(apk_path) as apk:
        names = apk.namelist()
        entries = apk_entries()
        if any(names.count(entry) != 1 for entry in entries.values()):
            raise Refused('the built APK does not carry each public input exactly once')
        found = {relative: sha256(apk.read(entry)) for relative, entry in entries.items()}
    expected = dict(pin['digests'], **{NETWORK: pin['compiledNetworkSha256']})
    wrong = sorted(r for r in expected if found[r] != expected[r])
    if wrong:
        raise Refused('the built APK carries different public inputs: ' + ', '.join(wrong))
    return {'checked': sorted(found), 'compiledNetworkSha256': found[NETWORK]}


def classify(stderr):
    """A fixed category for a failed read; gcloud's own text is never kept or printed."""
    text = stderr.lower()
    if 'attribute condition' in text or 'unauthorized_client' in text or 'sts.googleapis.com' in text \
            or 'invalid_grant' in text or 'workload identity' in text:
        return 'token-exchange-refused'
    if 'iam.serviceaccounts.getaccesstoken' in text or 'generateaccesstoken' in text:
        return 'impersonation-refused'
    if 'permission_denied' in text or 'permission denied' in text or 'does not have' in text:
        return 'permission-denied'
    if 'not_found' in text or 'not found' in text:
        return 'not-found'
    return 'failed'


def probe_signer(cfg, gcloud='gcloud', run=subprocess.run):
    """Each pinned app signing secret is readable by this job's identity, with the command the signing uses
    (host_secrets.fetch_secret): written to an owner-only RAM file, overwritten and removed at once."""
    integrate = cfg['host']['integrate']
    read = {}
    with host_secrets.private_directory('rose-agent-probe-') as private:
        for key in SECRETS:
            name, target = integrate['appSecrets'][key], private/key
            if not host_secrets.SECRET_NAME.fullmatch(name) or not re.fullmatch(r'[1-9][0-9]*', str(integrate['secretVersion'])):
                raise Refused('secret names and pinned numeric versions only')
            old = os.umask(0o077)
            try:
                result = run([gcloud, 'secrets', 'versions', 'access', str(integrate['secretVersion']), '--secret', name,
                              '--project', integrate['project'], '--out-file', str(target), '--quiet'],
                             capture_output=True, text=True, timeout=120)
                read[name] = 'read' if result.returncode == 0 and target.is_file() and target.stat().st_size > 0 \
                    else classify(result.stderr or '')
            except subprocess.TimeoutExpired:
                read[name] = 'timed-out'
            finally:
                os.umask(old)
                if target.is_file():
                    target.write_bytes(b'\0' * target.stat().st_size)
                    target.unlink()
    return {'schema': 'roseos.agent-signer-probe.v1', 'project': integrate['project'],
            'secretVersion': integrate['secretVersion'], 'secrets': read,
            'serviceAccount': os.environ.get('GCP_SIGNER_SERVICE_ACCOUNT', ''),
            'runnerEnvironment': os.environ.get('RUNNER_ENVIRONMENT', ''),
            'verdict': 'admitted' if all(v == 'read' for v in read.values()) else 'refused'}


# ---------------------------------------------------------------- reservation

def authenticated_fetch(token):
    """GET JSON from the GitHub API with the publisher token in the Authorization header only."""
    import urllib.request  # noqa: PLC0415

    def fetch(url):
        request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'roseos-agent-hosted',
                                                       'Authorization': 'Bearer ' + token, 'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read(32 << 20))
        except Exception as error:  # noqa: BLE001 - any failure is unavailable state, never a guess
            raise Refused(f'the release listing failed ({type(error).__name__})') from None
    return fetch


def committed_allocations(alloc, source, repo=ROOT):
    """Every committed latest-beta allocation record at the source commit, each checked against its own hash."""
    listing = alloc.git(repo, 'ls-tree', '--name-only', source, 'os/latest-beta/').decode().split()
    records = []
    for path in sorted(p for p in listing if re.fullmatch(r'os/latest-beta/[a-z0-9]+-\d{10}-allocation\.json', p)):
        records.append(alloc.check_allocation(json.loads(alloc.show(repo, source, path))))
    return records


def observation(alloc, source, releases, ledger, repo=ROOT):
    """release-allocate.py's observation, from what a hosted job can read: every pixel-ota release (drafts included,
    through the publisher token), no builder attempt directory (this route has none), the committed latest-beta
    allocation records and the shared ledger's reservations as app reservations."""
    return {'schema': alloc.OBSERVATION_SCHEMA,
            'releases': {'complete': True, 'includesDrafts': True,
                         'draftsBasis': 'listed with the pixel-ota publisher token, which sees drafts', 'items': releases},
            'attempts': {'complete': True, 'items': []},
            'reservations': {'complete': True, 'items': committed_allocations(alloc, source, repo)},
            'appReservations': {'complete': True, 'items': [
                {'packageName': r['packageName'], 'versionCode': v, 'sourceSha': r['sourceSha']}
                for v, r in sorted(ledger['reservations'].items())]},
            'ledger': alloc.ledger_part(ledger)}


def reserve(out, env, cfg, ledger, releases_fetch, clock=time.time, repo=ROOT):
    """One fresh hosted Rose Agent candidate held in the shared ledger for this run, or app_skip."""
    alloc = ALLOC
    source = env.get('GITHUB_SHA', '')
    if not re.fullmatch(r'[0-9a-f]{40}', source):
        raise Refused('GITHUB_SHA is not a commit')
    store = cfg['reservation']['store']
    run_owner = ledger_store.owner(env)
    out = Path(out)
    if out.exists():
        raise Refused(f'{out} already exists; use a new directory')
    found = ledger.read(PACKAGE)
    if not found['floors']:
        raise SetupMissing(['a builder floor in the reservation store (decision D2: the Frankel builder\'s consumed '
                            'versions are not in the ledger yet)'])
    pipeline = json.loads(alloc.show(repo, source, alloc.PIPELINE_CONFIG))
    releases = alloc.list_releases(pipeline['publication']['repository'], pipeline['publication'].get('api', RELEASES_API),
                                   releases_fetch)
    seen = observation(alloc, source, releases, found, repo)
    allocation = alloc.allocate_app(source, seen, int(clock()), run_owner['runId'], run_owner['runAttempt'], repo,
                                    floor=seen['ledger']['floor'], name_suffix=VERSION_SUFFIX)
    out.mkdir(parents=True)
    if allocation['verdict'] == 'unchanged':
        (out/'allocation.json').write_text(json.dumps(allocation, indent=2, sort_keys=True) + '\n')
        return {'app_skip': 'true', 'source': source, 'reason': allocation['reason']}
    candidate = alloc.derive_app(allocation, repo)
    record = ledger_store.reservation(PACKAGE, allocation['versionCode'], source, candidate,
                                      allocation['allocationSha256'], run_owner, store)
    written = ledger.reserve(record)
    (out/'allocation.json').write_text(json.dumps(dict(allocation, candidateSha=candidate), indent=2, sort_keys=True) + '\n')
    (out/'reservation.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    return {'app_skip': 'false', 'source': source, 'candidate_sha': candidate,
            'apps': json.dumps([[PACKAGE, allocation['versionCode']]], separators=(',', ':')),
            'version_code': str(allocation['versionCode']), 'reservation_sha256': record['reservationSha256'],
            'ref': written['ref']}


def bind(directory, env, cfg, ledger, repo=ROOT):
    """The reserve job's records, re-derived and owned by this run, and still held by the ledger."""
    alloc = ALLOC
    directory = Path(directory)
    allocation = json.loads((directory/'allocation.json').read_text())
    candidate = allocation.get('candidateSha')
    checked = alloc.check_app_allocation({k: v for k, v in allocation.items() if k not in ('verdict', 'candidateSha')})
    if checked['sourceSha'] != env.get('GITHUB_SHA'):
        raise Refused('the allocation is not of this run\'s dispatch commit')
    if alloc.derive_app(checked, repo) != candidate:
        raise Refused('the allocation does not re-derive its candidate')
    apps = [[PACKAGE, checked['versionCode']]]
    try:
        record = app_reservation.owned(str(directory/'reservation.json'), env, PACKAGE, apps, candidate,
                                       cfg['reservation']['store'])
    except app_reservation.Refused as error:
        raise Refused(str(error)) from None
    if record['allocationSha256'] != checked['allocationSha256'] or record['sourceSha'] != checked['sourceSha']:
        raise Refused('the reservation is not of this allocation')
    ledger.confirm(record)
    return {'ROSE_CANDIDATE_SHA': candidate, 'APPS': json.dumps(apps, separators=(',', ':')),
            'ROSE_APP_RESERVATION': str((directory/'reservation.json').resolve())}


def confirm(path, env, cfg, ledger):
    record = json.loads(Path(path).read_text())
    apps = [[PACKAGE, record.get('versionCode')]]
    try:
        app_reservation.owned(str(path), env, PACKAGE, apps, record.get('candidateSha'), cfg['reservation']['store'])
    except app_reservation.Refused as error:
        raise Refused(str(error)) from None
    return ledger.confirm(record)


def export(values, env):
    for name in ('GITHUB_OUTPUT',):
        if env.get(name):
            with open(env[name], 'a') as f:
                f.writelines(f'{k}={v}\n' for k, v in values.items() if '\n' not in str(v))


def main(argv=None, env=os.environ):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', type=Path, default=HOSTED)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('public-inputs')
    s.add_argument('--apk', type=Path, required=True)
    s.add_argument('--out', type=Path, required=True)
    s = sub.add_parser('check-apk')
    s.add_argument('--apk', type=Path, required=True)
    sub.add_parser('probe-signer').add_argument('--out', type=Path, required=True)
    sub.add_parser('reserve').add_argument('--out', type=Path, required=True)
    sub.add_parser('bind').add_argument('--dir', type=Path, required=True)
    sub.add_parser('confirm').add_argument('--reservation', type=Path, required=True)
    a = p.parse_args(argv)
    try:
        cfg = config(a.config)
        if a.command == 'public-inputs':
            result = public_inputs(a.apk, a.out, cfg)
        elif a.command == 'check-apk':
            result = check_apk(a.apk, cfg)
        elif a.command == 'probe-signer':
            result = probe_signer(cfg)
            a.out.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
            if result['verdict'] != 'admitted':
                raise Refused('the signer identity could not read every app signing secret: '
                              + ', '.join(f'{n} {v}' for n, v in result['secrets'].items() if v != 'read'))
        else:
            store = (cfg.get('reservation') or {}).get('store')
            if not store:
                raise SetupMissing(['reservation.store (os/release-pipeline/agent/hosted.json, decision D1)'])
            if a.command == 'reserve':
                token = env.get('PIXEL_OTA_TOKEN', '')
                if not token:
                    raise SetupMissing(['PIXEL_OTA_TOKEN (minted by the agent-reserve job)'])
                ledger = ledger_store.ledger_from(store, token=token)
                result = reserve(a.out, env, cfg, ledger, authenticated_fetch(token))
                export(result, env)
            elif a.command == 'bind':
                result = bind(a.dir, env, cfg, ledger_store.ledger_from(store))
                if env.get('GITHUB_ENV'):
                    with open(env['GITHUB_ENV'], 'a') as f:
                        f.writelines(f'{k}={v}\n' for k, v in result.items())
            else:
                result = {'confirmed': confirm(a.reservation, env, cfg, ledger_store.ledger_from(store))['reservationSha256']}
    except SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except (Refused, ledger_store.Refused) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error)}), file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - allocation refusals and I/O, named by class only
        detail = str(error) if isinstance(error, ALLOC.Refused) else error.__class__.__name__
        print(json.dumps({'verdict': 'FAIL', 'error': detail}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
