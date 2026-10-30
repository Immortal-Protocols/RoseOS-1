#!/usr/bin/env python3
"""Dedicated-host signing job (SIGN-020): hash-bound inputs in, durable signed outputs and receipt out.

  signing-job.py run --manifest M.json --jobs-root J --out-root O     (cwd = the signer's source root)
  signing-job.py check --output-dir O/{device}-{build}

run re-hashes every input before any secret, signs with host-sign.py's existing route in a fresh 0700 workspace under a
signing-only lock, commits the outputs atomically to O/{jobId} (written to a .partial directory, fsynced, renamed; the
receipt is written last) and removes the workspace on success and failure. A committed job with equal inputs is reused
without secrets; different inputs refuse. A completed host-sign release whose commit was lost is recovered from its
durable bytes, matched to host-sign's signed marker and the job's inputs, never signed again. Exit 0 / 1 refused / 4 setup.
"""
import argparse
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'roseos.signing-job.v1'
RECEIPT_SCHEMA = 'roseos.signing-receipt.v1'
RECEIPT = 'signing-receipt.json'
HEX64 = re.compile(r'[0-9a-f]{64}')


class Refused(Exception):
    pass


class SetupMissing(Exception):
    def __init__(self, missing):
        super().__init__(', '.join(missing))
        self.missing = missing


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def cert_sha256(pem):
    body = pem.split('-----BEGIN CERTIFICATE-----')[1].split('-----END CERTIFICATE-----')[0]
    return hashlib.sha256(base64.b64decode(''.join(body.split()))).hexdigest()


def public_assets(device, build):
    return [f'{device}-ota_update-{build}.zip', f'{device}-stable', 'releasekey.x509.pem', 'avb_pkmd.bin', 'UPDATE.md']


def load_manifest(path):
    try:
        m = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise Refused('manifest_invalid: unreadable manifest') from None
    if not isinstance(m, dict) or m.get('schema') != SCHEMA:
        raise Refused(f'manifest_invalid: not a {SCHEMA} manifest')
    if not re.fullmatch(r'[a-z][a-z0-9]{1,31}', str(m.get('device'))) or not re.fullmatch(r'[0-9]{10}', str(m.get('build'))) \
            or not re.fullmatch(r'[0-9]{10}', str(m.get('buildTimestamp'))) or not re.fullmatch(r'[0-9a-f]{40}', str(m.get('candidateSha'))):
        raise Refused('manifest_invalid: device, build, buildTimestamp and candidateSha must be exact')
    config = m.get('config') or {}
    if not config.get('path') or not HEX64.fullmatch(str(config.get('sha256'))):
        raise Refused('manifest_invalid: config needs a path and sha256')
    inputs = m.get('inputs') or {}
    if sorted(inputs) not in (['otatools', 'targetFiles'], ['notes', 'otatools', 'targetFiles']):
        raise Refused('manifest_invalid: inputs are targetFiles, otatools and optionally notes')
    for key in sorted(inputs):
        spec = inputs.get(key) or {}
        if not Path(str(spec.get('path'))).is_absolute() or not isinstance(spec.get('size'), int) or spec['size'] <= 0 \
                or not HEX64.fullmatch(str(spec.get('sha256'))):
            raise Refused(f'manifest_invalid: inputs.{key} needs an absolute path, size and sha256')
    notes = m.get('notes')
    if notes is not None and 'notes' in inputs:
        raise Refused('manifest_invalid: notes are either a bound input file or a commit pin, not both')
    if notes is not None and (not re.fullmatch(r'[0-9a-f]{40}', str(notes.get('commit'))) or not HEX64.fullmatch(str(notes.get('sha256')))
                              or not re.fullmatch(r'os/release-notes/[\w.-]+\.md', str(notes.get('path')))):
        raise Refused('manifest_invalid: notes needs a full commit, an os/release-notes path and a sha256')
    return m


def load_config(m):
    path = Path(m['config']['path'])
    path = path if path.is_absolute() else ROOT/path
    try:
        data = path.read_bytes()
    except OSError:
        raise SetupMissing([f'config {path}']) from None
    if hashlib.sha256(data).hexdigest() != m['config']['sha256']:
        raise Refused('input_changed: the signing config differs from the manifest')
    cfg = json.loads(data)
    if cfg.get('target', {}).get('device') != m['device']:
        raise Refused('manifest_invalid: the config is for another device')
    return cfg


def check_inputs(m):
    for key, spec in m['inputs'].items():
        path = Path(spec['path'])
        if path.is_symlink() or not path.is_file():
            raise Refused(f'input_missing: inputs.{key}')
        if path.stat().st_size != spec['size'] or sha256_file(path) != spec['sha256']:
            raise Refused(f'input_changed: inputs.{key} differs from the manifest')


def read_receipt(directory):
    try:
        return json.loads((Path(directory)/RECEIPT).read_text())
    except (OSError, ValueError):
        return None


def check(output_dir):
    """Every committed output still has its receipt hash (after transport, before publication)."""
    output_dir = Path(output_dir)
    receipt = read_receipt(output_dir)
    if not receipt or receipt.get('schema') != RECEIPT_SCHEMA:
        raise Refused(f'receipt_missing: {output_dir}/{RECEIPT}')
    present = sorted(str(p.relative_to(output_dir)) for p in output_dir.rglob('*') if p.is_file() and p.name != RECEIPT)
    if present != sorted(receipt['outputs']):
        raise Refused('output_changed: the output files differ from the receipt')
    for name, digest in receipt['outputs'].items():
        if sha256_file(output_dir/name) != digest:
            raise Refused(f'output_changed: {name} differs from the receipt')
    return receipt


def fsync_tree(directory):
    for path in sorted(Path(directory).rglob('*')) + [Path(directory)]:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def commit(m, cfg, out_root, files, verdict, attempt):
    """files: {relative output path: bytes source path}. Written to O/.{jobId}.partial, fsynced, renamed to O/{jobId}."""
    job_id = f'{m["device"]}-{m["build"]}'
    out_root = Path(out_root)
    partial, final = out_root/f'.{job_id}.partial', out_root/job_id
    shutil.rmtree(partial, ignore_errors=True)
    (partial/'public').mkdir(parents=True)
    for name, source in files.items():
        shutil.copyfile(source, partial/name)
    outputs = {name: sha256_file(partial/name) for name in sorted(files)}
    identities = {'otaCertificateSha256': cert_sha256((partial/'public/releasekey.x509.pem').read_text()),
        'avbPublicKeySha256': sha256_file(partial/'public/avb_pkmd.bin')}
    expected = {k: (cfg.get('identities') or {}).get(k) for k in identities}
    if any(v and v != identities[k] for k, v in expected.items()):
        shutil.rmtree(partial, ignore_errors=True)
        raise Refused('identity_mismatch: the signed public identity differs from the permanent identity in the config')
    receipt = {'schema': RECEIPT_SCHEMA, 'jobId': job_id, 'verdict': verdict, 'attempt': attempt,
        'device': m['device'], 'build': m['build'], 'buildTimestamp': m['buildTimestamp'], 'candidateSha': m['candidateSha'],
        'config': m['config'], 'inputs': m['inputs'], 'notes': m.get('notes'), 'outputs': outputs, 'identities': identities,
        'signer': socket.gethostname(), 'committedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    (partial/RECEIPT).write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    fsync_tree(partial)
    os.rename(partial, final)
    fd = os.open(out_root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return final, receipt


def avb_public_key(vbmeta):
    import struct
    if vbmeta[:4] != b'AVB0' or len(vbmeta) < 256:
        raise Refused('recovery_mismatch: the signed vbmeta image is not an AVB image')
    auth_size, = struct.unpack('>Q', vbmeta[12:20])
    offset, size = struct.unpack('>QQ', vbmeta[64:80])
    start = 256 + auth_size + offset
    return vbmeta[start:start + size]


def recover(m, cfg, source_root, jobs_root, scratch):
    """A completed host-sign release (signed marker) whose commit was lost: its durable bytes, bound to this job's inputs
    (host-sign's own copies of them) and to every marker hash. Returns {output: source path}; nothing is signed."""
    device, build = m['device'], m['build']
    releases = Path(source_root)/'releases'/build
    marker = json.loads((releases/'.rose-actions-signed.json').read_text())
    if marker.get('candidate') != m['candidateSha'] or marker.get('build') != build:
        raise Refused('recovery_mismatch: the signed release belongs to another candidate')
    for key, name in (('targetFiles', f'{device}-target_files.zip'), ('otatools', f'{device}-otatools.zip')):
        if not (releases/name).is_file() or sha256_file(releases/name) != m['inputs'][key]['sha256']:
            raise Refused(f'recovery_mismatch: the signed release was not made from this job\'s {key}')
    output = releases/f'release-{device}-{build}'
    signed = output/f'{device}-target_files.zip'
    if not signed.is_file() or sha256_file(signed) != marker.get('signedTargetFilesSha256'):
        raise Refused('recovery_mismatch: the durable signed target-files is missing or differs from the marker')
    outputs = marker.get('outputs') or {}
    if sorted(outputs) != sorted(public_assets(device, build)):
        raise Refused('recovery_mismatch: the marker does not list the signed public outputs')
    if 'notes' in m['inputs'] and outputs['UPDATE.md'] != m['inputs']['notes']['sha256']:
        raise Refused("recovery_mismatch: the signed release was not made from this job's notes")
    earlier = [p/'release/public' for p in sorted(Path(jobs_root).glob(f'{device}-{build}.*')) if p.is_dir()]
    with zipfile.ZipFile(signed) as z:
        certs = zipfile.ZipFile(io.BytesIO(z.read('SYSTEM/etc/security/otacerts.zip')))
        pem = [certs.read(n) for n in certs.namelist() if n.endswith('releasekey.x509.pem')]
        derived = {'releasekey.x509.pem': pem[0] if len(pem) == 1 else b'', 'avb_pkmd.bin': avb_public_key(z.read('IMAGES/vbmeta.img')),
            f'{device}-stable': f'{build} {m["buildTimestamp"]} {device} {cfg["publication"]["channels"]["beta"]["feedChannel"]}\n'.encode()}
    files = {'signed-target_files.zip': signed}
    scratch.mkdir(parents=True, exist_ok=True)
    for name in public_assets(device, build):
        sources = [releases/'public'/name, output/name] + [p/name for p in earlier]
        found = next((s for s in sources if s.is_file() and not s.is_symlink() and sha256_file(s) == outputs[name]), None)
        if found is None and name in derived and hashlib.sha256(derived[name]).hexdigest() == outputs[name]:
            found = scratch/name
            found.write_bytes(derived[name])
        if found is None:
            raise Refused(f'recovery_missing: no byte-exact source for {name}')
        files[f'public/{name}'] = found
    return files


# ---------------------------------------------------------------- hosted ingress (SIGN-022)
# os-sign.yml's secret-free hosted job fetches the os-release run's bound unsigned-build artifact (actions: read, the
# job's own short-lived token), validates its provenance and exact members, and hands a same-run bundle to the signing
# job on the dedicated host, which stages it into $SIGNING_ROOT/inbox/<job>/ and validates it again before any credential.

SIGNING_ROOT = '/srv/rose-signing'
SOURCE_WORKFLOW = '.github/workflows/os-release.yml'


def github_get(env, path, follow=True):
    import urllib.error
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    request = urllib.request.Request(f"{env.get('GITHUB_API_URL', 'https://api.github.com')}/repos/{env['GITHUB_REPOSITORY']}{path}",
        headers={'Authorization': f"Bearer {env.get('GH_TOKEN', '')}", 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        if follow:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read(1 << 22))
        try:
            urllib.request.build_opener(NoRedirect).open(request, timeout=60)
        except urllib.error.HTTPError as redirect:
            if redirect.code in (301, 302, 303, 307, 308) and redirect.headers.get('Location'):
                return redirect.headers['Location']
            raise
        raise Refused('ingress_refused: the artifact download did not redirect')
    except Refused:
        raise
    except Exception:  # noqa: BLE001 - never echo a token-bearing error; an access denial stops here
        raise Refused(f'ingress_refused: GitHub read of {path} failed') from None


def download(url, destination):
    """The artifact archive from GitHub's short-lived redirect; the token is never sent there."""
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request(url), timeout=600) as response, open(destination, 'wb') as out:
        shutil.copyfileobj(response, out, 8 << 20)


API = {'get': github_get, 'download': download}


def ingress(env, source_run, artifact_id, artifact_digest, manifest_sha256, out, signing_root=SIGNING_ROOT, request_id=None):
    """Validate the source run, the artifact and every archive member; write the bundle and provenance.json to out."""
    for value, pattern, name in ((source_run, r'[1-9][0-9]{0,19}', 'source_run'), (artifact_id, r'[1-9][0-9]{0,19}', 'artifact_id'),
                                 (artifact_digest, r'sha256:[0-9a-f]{64}', 'artifact_digest'),
                                 (manifest_sha256, r'[0-9a-f]{64}', 'manifest_sha256')):
        if not re.fullmatch(pattern, str(value)):
            raise Refused(f'ingress_refused: {name} is not exact')
    # SIGN-023: request_id = <source_run>.<source_attempt>.<device>-<build>; it correlates the dispatch (the run-name),
    # and every part of it is checked against the API and the validated manifest; the title alone proves nothing.
    request = re.fullmatch(r'([1-9][0-9]{0,19})\.([1-9][0-9]{0,4})\.([a-z][a-z0-9]{1,31})-([0-9]{10})', str(request_id))
    if not request:
        raise Refused('ingress_refused: request_id must be <source_run>.<source_attempt>.<device>-<build>')
    if request[1] != str(source_run):
        raise Refused('ingress_refused: request_id names another source run')
    artifact = API['get'](env, f'/actions/artifacts/{artifact_id}')
    run = API['get'](env, f'/actions/runs/{source_run}')
    repository = env.get('GITHUB_REPOSITORY')
    if str((artifact.get('workflow_run') or {}).get('id')) != str(source_run) or artifact.get('expired') is not False \
            or artifact.get('digest') != artifact_digest:
        raise Refused("ingress_refused: the artifact is not the named run's live artifact with this digest")
    if run.get('path') != SOURCE_WORKFLOW or (run.get('repository') or {}).get('full_name') != repository \
            or run.get('head_branch') != 'main' or run.get('status') not in ('in_progress', 'completed'):
        raise Refused("ingress_refused: the source run is not this repository's os-release run on main")
    if str(run.get('run_attempt')) != request[2]:
        raise Refused('ingress_refused: request_id names another attempt of the source run')
    out = Path(out)
    out.mkdir(parents=True)
    archive = out.parent/f'{out.name}.zip'
    API['download'](API['get'](env, f'/actions/artifacts/{artifact_id}/zip', follow=False), archive)
    if 'sha256:' + sha256_file(archive) != artifact_digest:
        raise Refused('ingress_refused: the downloaded archive differs from the artifact digest')
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if len(set(names)) != len(names) or any('/' in n or '\\' in n or n in ('', '.', '..') or i.is_dir()
                                                 or (i.external_attr >> 16) & 0o170000 == 0o120000 for n, i in zip(names, infos)):
            raise Refused('ingress_refused: the archive holds directories, links, nested paths or duplicates')
        if 'manifest.json' not in names:
            raise Refused('ingress_refused: the archive has no manifest.json')
        data = z.read('manifest.json')
        if hashlib.sha256(data).hexdigest() != manifest_sha256:
            raise Refused('ingress_refused: manifest.json differs from manifest_sha256')
        (out/'manifest.json').write_bytes(data)
        m = load_manifest(out/'manifest.json')
        dirs = {str(Path(spec['path']).parent) for spec in m['inputs'].values()}
        expected = sorted(['manifest.json'] + [Path(spec['path']).name for spec in m['inputs'].values()])
        if len(dirs) != 1 or not re.fullmatch(re.escape(str(signing_root)) + r'/inbox/[A-Za-z0-9._-]+', next(iter(dirs))) \
                or sorted(names) != expected:
            raise Refused('ingress_refused: members must be exactly manifest.json and the inputs of one inbox job directory')
        for key, spec in m['inputs'].items():
            name = Path(spec['path']).name
            with z.open(name) as source, open(out/name, 'wb') as target:
                shutil.copyfileobj(source, target, 8 << 20)
            if (out/name).stat().st_size != spec['size'] or sha256_file(out/name) != spec['sha256']:
                raise Refused(f'ingress_refused: member {name} differs from inputs.{key}')
    archive.unlink()
    if (m['device'], m['build']) != (request[3], request[4]):
        raise Refused('ingress_refused: request_id names another device or build than the manifest')
    source = m.get('source')
    if source is not None and (str(source.get('run')) != str(source_run) or source.get('headSha') != run.get('head_sha')):
        raise Refused('ingress_refused: the manifest names another source run')
    provenance = {'requestId': request_id, 'sourceAttempt': request[2], 'sourceRun': str(source_run), 'headSha': run.get('head_sha'), 'workflow': SOURCE_WORKFLOW, 'repository': repository,
        'artifactId': str(artifact_id), 'artifactDigest': artifact_digest, 'manifestSha256': manifest_sha256,
        'job': f'{m["device"]}-{m["build"]}', 'inbox': str(Path(m['inputs']['targetFiles']['path']).parent)}
    (out/'provenance.json').write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
    return provenance


def stage(bundle, signing_root):
    """Signer host: place a same-run ingress bundle into its inbox directory (new, or byte-identical on retry)."""
    bundle, signing_root = Path(bundle), Path(signing_root)
    provenance = json.loads((bundle/'provenance.json').read_text())
    m = load_manifest(bundle/'manifest.json')
    if hashlib.sha256((bundle/'manifest.json').read_bytes()).hexdigest() != provenance['manifestSha256']:
        raise Refused('stage_refused: the bundle manifest differs from its provenance')
    inbox = Path(provenance['inbox'])
    if str(inbox) != f'{signing_root}/inbox/{inbox.name}' or {str(Path(s['path']).parent) for s in m['inputs'].values()} != {str(inbox)}:
        raise Refused('stage_refused: the bundle names another inbox')
    target = signing_root/'inbox'/inbox.name
    members = ['manifest.json'] + [Path(spec['path']).name for spec in m['inputs'].values()]
    if target.exists():
        if sorted(p.name for p in target.iterdir()) != sorted(members) or any(
                sha256_file(target/n) != sha256_file(bundle/n) for n in members):
            raise Refused('stage_refused: the inbox job directory already holds other bytes')
    else:
        partial = signing_root/'inbox'/f'.{inbox.name}.partial'
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(mode=0o700)
        for name in members:
            shutil.copyfile(bundle/name, partial/name)
        os.rename(partial, target)
    return str(target/'manifest.json')


def host_sign_signer(config_path, env):
    spec = importlib.util.spec_from_file_location('host_sign_for_job', ROOT/'scripts/host-sign.py')
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT/'scripts'))
    spec.loader.exec_module(module)
    try:
        return module.main(config_path=config_path, env=env)
    except module.AdapterSetupMissing as error:
        raise SetupMissing(error.missing) from None
    except module.AdapterFailed as error:
        raise Refused(f'sign_failed: {error}') from None


@contextlib.contextmanager
def signer_lock(path):
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # one signing at a time on this host; builds never take it
        yield
    finally:
        os.close(fd)


def run(manifest_path, jobs_root, out_root, signer=None, source_root=None, lock=None, base_env=None):
    m = load_manifest(manifest_path)
    cfg = load_config(m)
    job_id = f'{m["device"]}-{m["build"]}'
    jobs_root, out_root = Path(jobs_root), Path(out_root)
    jobs_root.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)
    final = out_root/job_id
    if final.exists():
        receipt = check(final)
        if (receipt['inputs'], receipt['config'], receipt['candidateSha'], receipt['buildTimestamp'], receipt.get('notes')) != \
                (m['inputs'], m['config'], m['candidateSha'], m['buildTimestamp'], m.get('notes')):
            raise Refused(f'job_conflict: {job_id} is already committed for other inputs')
        return {'verdict': 'reused', 'jobId': job_id, 'outputDir': str(final), 'receiptSha256': sha256_file(final/RECEIPT)}
    shutil.rmtree(out_root/f'.{job_id}.partial', ignore_errors=True)  # an interrupted commit never counts
    check_inputs(m)
    source_root = Path(source_root or os.getcwd())
    attempt = f'{os.getpid()}-{int(time.time())}'
    workspace = jobs_root/f'{job_id}.{attempt}'
    workspace.mkdir(mode=0o700)
    try:
        if (source_root/'releases'/m['build']/'.rose-actions-signed.json').is_file():
            files, verdict = recover(m, cfg, source_root, jobs_root, workspace/'recovered'), 'recovered'
        else:
            release = workspace/'release'
            release.mkdir()
            shutil.copyfile(m['inputs']['targetFiles']['path'], release/'unsigned-target_files.zip')
            shutil.copyfile(m['inputs']['otatools']['path'], workspace/'otatools.zip')
            copies = {'targetFiles': release/'unsigned-target_files.zip', 'otatools': workspace/'otatools.zip'}
            if 'notes' in m['inputs']:
                shutil.copyfile(m['inputs']['notes']['path'], workspace/'UPDATE.md')
                copies['notes'] = workspace/'UPDATE.md'
            check_inputs({'inputs': {k: dict(m['inputs'][k], path=str(p)) for k, p in copies.items()}})  # after the copy
            config_path = Path(m['config']['path'])
            config_path = config_path if config_path.is_absolute() else ROOT/config_path
            notes_pin = dict(m['notes'], build=m['build'], device=m['device']) if m.get('notes') else None
            if 'notes' in m['inputs']:
                notes_pin = {'file': str(copies['notes']), 'sha256': m['inputs']['notes']['sha256'], 'build': m['build'], 'device': m['device']}
            if notes_pin:
                job_cfg = json.loads(config_path.read_bytes())
                job_cfg['host']['sign']['updateNotes'] = notes_pin
                config_path = workspace/'config.json'
                config_path.write_text(json.dumps(job_cfg))
            env = dict(base_env if base_env is not None else os.environ, BUILD_NUMBER=m['build'], BUILD_DATETIME=m['buildTimestamp'],
                ROSE_CANDIDATE_SHA=m['candidateSha'], ROSE_RELEASE_DIR=str(release), ROSE_SIGN_OTATOOLS=str(workspace/'otatools.zip'))
            env.pop('ROSE_RETAINED_INPUT', None)
            with (lock or signer_lock)(jobs_root/'signer.lock'):
                (signer or host_sign_signer)(config_path, env)
            files = {'signed-target_files.zip': release/'signed-target_files.zip'}
            files.update({f'public/{n}': release/'public'/n for n in public_assets(m['device'], m['build'])})
            missing = [n for n, p in files.items() if not p.is_file()]
            if missing:
                raise Refused(f'sign_failed: the signer did not produce {missing}')
            verdict = 'signed'
        final, receipt = commit(m, cfg, out_root, files, verdict, attempt)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return {'verdict': verdict, 'jobId': job_id, 'outputDir': str(final), 'receiptSha256': sha256_file(final/RECEIPT)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    sub = p.add_subparsers(dest='command', required=True)
    r = sub.add_parser('run')
    r.add_argument('--manifest', required=True)
    r.add_argument('--jobs-root', required=True)
    r.add_argument('--out-root', required=True)
    sub.add_parser('check').add_argument('--output-dir', required=True)
    g = sub.add_parser('ingress')
    for name in ('--source-run', '--artifact-id', '--artifact-digest', '--manifest-sha256', '--out'):
        g.add_argument(name, required=True)
    g.add_argument('--signing-root', default=SIGNING_ROOT)
    g.add_argument('--request-id', required=True)
    t = sub.add_parser('stage')
    t.add_argument('--bundle', required=True)
    t.add_argument('--signing-root', required=True)
    a = p.parse_args(argv)
    try:
        if a.command == 'run':
            result = run(a.manifest, a.jobs_root, a.out_root)
        elif a.command == 'ingress':
            result = ingress(dict(os.environ), a.source_run, a.artifact_id, a.artifact_digest, a.manifest_sha256, a.out, a.signing_root,
                a.request_id)
        elif a.command == 'stage':
            result = {'manifest': stage(a.bundle, a.signing_root)}
        else:
            result = {'verdict': 'ok', **check(a.output_dir)}
    except SetupMissing as error:
        print(json.dumps({'verdict': 'SETUP_MISSING', 'missing': error.missing}), file=sys.stderr)
        return 4
    except (Refused, OSError, ValueError, KeyError) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': str(error) if isinstance(error, Refused) else type(error).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
