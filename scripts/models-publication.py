#!/usr/bin/env python3
"""One-off public prerelease of the signed Models v2 APK (MODELS-PUBLISH-001, owner-approved 2026-10-31).

The input is one fixed private orphan commit holding exactly the owner-supplied signed APK and its binding. Nothing
else is accepted: the commit, tree, blobs, bytes and binding are pinned here, read through the repository API with the
job's read-only token, and never checked out or executed. The publisher token is minted only after every check passed.

  ingest  --out D                   read the two input files of INPUT_COMMIT (GITHUB_TOKEN, contents: read)
  verify  --input D --sdk-root R    pinned build-tools r35 (scripts/models-sdk-prepare.py): aapt identity, native
                                    library, exact signer, v2 over API 24-27 and v3 over the APK's own range
  publish --input D                 PIXEL_OTA_TOKEN: upload to the fixed draft, verify, publish as a prerelease that
                                    never becomes Latest, read the public asset back

Exit status: 0 done, 1 refused (fixed category on stderr), 2 invalid input. No retry and no route switch: any failure
stops where it is and the outcome names the state left behind.
"""
import argparse
import base64
import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INPUT_REPOSITORY = 'Immortal-Protocols/RoseOS-1'
INPUT_COMMIT = '9e30d0cdfa4f5bafe343502a18eceb9d7c760d2f'
INPUT_TREE = '0a1bb3ae4f6112e91ea228eace49073e4a559e6b'
INPUT_DIR = 'publication-inputs/rose-models-2'
APK_NAME = 'rose-models-signed.apk'
BINDING_NAME = 'binding.json'
# name -> (git blob, bytes, sha256)
INPUT_FILES = {
    APK_NAME: ('c129f191eb4ece8e0ee05db89ec90c0f88c8dbdd', 971360,
               '998d63d2d23cce93cb7341ced3e3d028f58f48d06907222787d3606e66f86aee'),
    BINDING_NAME: ('a8ef7a36e5086ca5b8a6c925feb750f0f631d6d0', 1198,
                   'ffdf202c92eadb657005ea8d3b9638fd82064ada99d1926b43db290195d12e20'),
}
PACKAGE, VERSION_CODE, VERSION_NAME, ABI, MIN_SDK = 'dev.pixel.models', '2', '0.2.0', 'arm64-v8a', '29'
NATIVE_ENTRY = 'lib/arm64-v8a/librose_chat_android.so'
NATIVE_SHA256 = 'f3aad0226f99a8b0853663d08bd0e623559741338dd54915443f1daac811d060'
SIGNER_SHA256 = '066c66d62518d1db6c8a7d40795fcca580412049d2c5a5cce89b7673540108da'
TOOLS = {'archiveSha256': 'bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88',
         'apksignerSha256': '00ef9948f843fe395d2440ae3ef41405b8040a6d5d46493bd1902ac0ee6deae7',
         'aaptSha256': '393c4c87f675f9555c50921fb53be1f3893cacd29f2270c4863e2e3f99583c39'}
DESTINATION = {'repository': 'Immortal-Protocols/pixel-ota', 'releaseId': 429690780, 'tag': 'rose-models-2',
               'assetName': APK_NAME, 'prerelease': True, 'makeLatest': False}
LATEST_PREFIX = 'frankel-'
CAVEAT = ('Client APK only: backend availability, private pairing and end-to-end phone acceptance are not '
          'established by this release.')
# The binding must be exactly this document (its bytes are also pinned above).
BINDING = {
    'apk': {'abi': ABI, 'bytes': 971360, 'certificateSha256': SIGNER_SHA256, 'nativeLibraryPath': NATIVE_ENTRY,
            'nativeLibrarySha256': NATIVE_SHA256, 'package': PACKAGE, 'path': f'{INPUT_DIR}/{APK_NAME}',
            'requiredSchemes': ['v2', 'v3'], 'sha256': INPUT_FILES[APK_NAME][2], 'versionCode': 2,
            'versionName': VERSION_NAME},
    'destination': {'assetName': APK_NAME, 'makeLatest': False, 'prerelease': True, 'releaseId': 429690780,
                    'repository': 'Immortal-Protocols/pixel-ota', 'tag': 'rose-models-2'},
    'producer': {'runId': 41106159024, 'sourceSha': '5bc2d03fbc77a3f3569b0536a6ca87949a166837'},
    'provenance': 'Owner-supplied extracted APK; independently verified. Original output ZIP not verified.',
    'schema': 'rose-models-publication-input/1',
    'signing': {'recipeSha': '32a0cf8f42b73a29517818d0d6c1761eec818518', 'runId': 41159629239},
}
AFTER_PUBLISH = 'rose-models-2 is already published (no rollback; Infra reconciles)'
SDK_CONFIG = 'os/models-signing/models-sign.json'
BUILD_TOOLS = 'build-tools/android-15'
API = 'https://api.github.com'


class Refused(Exception):
    """A fixed category plus nonsecret detail."""


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- ingest

def api_fetch(url, token, timeout=60):
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token, 'User-Agent': 'roseos-models-publish',
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(8 << 20))
    except urllib.error.HTTPError as error:
        raise Refused(f'input_unreadable: GET {url.split("/repos/", 1)[-1].split("?")[0]} returned HTTP {error.code}') from None
    except (OSError, ValueError) as error:
        raise Refused(f'input_unreadable: {type(error).__name__}') from None


def ingest(out, fetch):
    """`fetch(path)` returns the JSON of GET {API}/repos/{INPUT_REPOSITORY}/{path}. Writes exactly the two files."""
    commit = fetch(f'git/commits/{INPUT_COMMIT}')
    if commit.get('sha') != INPUT_COMMIT or (commit.get('tree') or {}).get('sha') != INPUT_TREE or commit.get('parents') != []:
        raise Refused('input_commit: not the fixed orphan input commit and tree')
    tree = fetch(f'git/trees/{INPUT_TREE}?recursive=1')
    expected = {'publication-inputs': ('tree', None), INPUT_DIR: ('tree', None),
                **{f'{INPUT_DIR}/{n}': ('blob', v[0]) for n, v in INPUT_FILES.items()}}
    entries = tree.get('tree') or []
    observed = {e.get('path'): (e.get('type'), e.get('sha') if e.get('type') == 'blob' else None) for e in entries}
    if tree.get('truncated') is not False or len(entries) != len(observed) or observed != expected \
            or any(e.get('mode') != '100644' for e in entries if e.get('type') == 'blob'):
        raise Refused('input_tree: the input commit must hold exactly the APK and binding as regular files')
    out = Path(out)
    out.mkdir(mode=0o700)
    for name, (blob_sha, size, digest) in INPUT_FILES.items():
        blob = fetch(f'git/blobs/{blob_sha}')
        if blob.get('sha') != blob_sha or blob.get('encoding') != 'base64' or blob.get('size') != size:
            raise Refused(f'input_blob: {name} is not the pinned blob')
        data = base64.b64decode(blob.get('content', ''), validate=False)
        if len(data) != size or hashlib.sha1(b'blob %d\0' % size + data).hexdigest() != blob_sha \
                or hashlib.sha256(data).hexdigest() != digest:
            raise Refused(f'input_blob: {name} bytes do not match the pinned size, blob and SHA-256')
        (out/name).write_bytes(data)
    return check_files(out)


def check_files(directory):
    """The directory holds exactly the two pinned files and the binding is exactly the pinned document."""
    directory = Path(directory)
    if sorted(p.name for p in directory.iterdir()) != sorted(INPUT_FILES):
        raise Refused('input_files: exactly the APK and binding are expected')
    for name, (_, size, digest) in INPUT_FILES.items():
        path = directory/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size or sha256_file(path) != digest:
            raise Refused(f'input_hash_mismatch: {name}')
    try:
        binding = json.loads((directory/BINDING_NAME).read_text())
    except ValueError:
        raise Refused('binding_malformed: not JSON') from None
    if binding != BINDING:
        raise Refused('binding_mismatch: not the pinned rose-models-publication-input/1 document')
    return {'apkSha256': INPUT_FILES[APK_NAME][2], 'apkBytes': INPUT_FILES[APK_NAME][1],
            'bindingSha256': INPUT_FILES[BINDING_NAME][2]}


# ---------------------------------------------------------------- verify

def prepare_sdk(sdk_root, prepare=None):
    """The pinned Google build-tools_r35_linux.zip, staged by the existing models-sdk-prepare helper into its own
    cache under this job's temporary directory (digest before extraction, whole tree after)."""
    prep = _load('models_sdk_prepare', 'scripts/models-sdk-prepare.py')
    cfg = json.loads((ROOT/SDK_CONFIG).read_text())
    root = Path(sdk_root)
    cfg['sdk'].update(root=str(root), archive=str(root/'downloads/build-tools_r35_linux.zip'))
    try:
        proof = (prepare or prep.prepare)(cfg)
    except prep.Refused as error:
        raise Refused(str(error)) from None
    if {k: proof.get(k) for k in TOOLS} != TOOLS:
        raise Refused('sdk_tools_mismatch: apksigner or aapt is not the pinned r35 member')
    return proof


def tool_environment():
    """Only what apksigner (java) and aapt need; nothing injectable from the job environment."""
    java_home = os.environ.get('JAVA_HOME', '')
    path = (f'{java_home}/bin:' if java_home else '') + '/usr/bin:/bin'
    return {'PATH': path, 'LC_ALL': 'C', **({'JAVA_HOME': java_home} if java_home else {})}


def run_tools(apk, sdk_root):
    """Default runner: two apksigner verifications and aapt badging, each one bounded start."""
    diag = _load('rose_models_sign_diagnostic', 'workspace/models/signing/sign_diagnostic.py')
    bt, env = Path(sdk_root)/BUILD_TOOLS, tool_environment()
    popen = functools.partial(subprocess.Popen, env=env)
    base = [str(bt/'apksigner'), 'verify', '--verbose', '--print-certs']
    v2 = diag.run_bounded(base + ['--min-sdk-version', '24', '--max-sdk-version', '27', str(apk)], popen=popen)
    v3 = diag.run_bounded(base + [str(apk)], popen=popen)
    aapt = diag.run_bounded([str(bt/'aapt'), 'dump', 'badging', str(apk)],
                            popen=functools.partial(subprocess.Popen, env=dict(env, LD_LIBRARY_PATH=str(bt/'lib64'))))
    return {name: (r.outcome == 'ok', r.stdout) for name, r in (('v2', v2), ('v3', v3), ('aapt', aapt))}


def verify(input_dir, sdk_root, prepare=None, tools=run_tools):
    check_files(input_dir)
    apk = Path(input_dir)/APK_NAME
    with zipfile.ZipFile(apk) as archive:
        if archive.testzip() is not None:
            raise Refused('apk_corrupt: a member fails its CRC')
        names = archive.namelist()
        if len(names) != len(set(names)) or [n for n in names if n.startswith('lib/')] != [NATIVE_ENTRY]:
            raise Refused('wrong_abi: the APK must carry exactly the arm64-v8a Rose chat library')
        if hashlib.sha256(archive.read(NATIVE_ENTRY)).hexdigest() != NATIVE_SHA256:
            raise Refused('native_mismatch: librose_chat_android.so is not the pinned library')
    proof = prepare_sdk(sdk_root, prepare)
    out = tools(apk, sdk_root)
    verifier = _load('rose_models_synthetic_sign_check', 'workspace/models/signing/synthetic_sign_check.py')
    for scheme in ('v2', 'v3'):
        ok, stdout = out[scheme]
        if not ok or not verifier.schemes(stdout)[scheme] or not stdout.startswith('Verifies\n'):
            raise Refused(f'scheme_unverified: {scheme}' + (' over API 24-27' if scheme == 'v2' else ''))
        if verifier.SIGNERS.findall(stdout) != [SIGNER_SHA256]:
            raise Refused('signer_mismatch: expected exactly the Rose Models certificate')
    ok, badging = out['aapt']
    package = re.search(r"^package: name='([^']*)' versionCode='([^']*)' versionName='([^']*)'", badging or '', re.M)
    if not ok or not package or package.group(1) != PACKAGE:
        raise Refused(f'wrong_package: not {PACKAGE}')
    if package.groups()[1:] != (VERSION_CODE, VERSION_NAME):
        raise Refused('wrong_version: not versionCode 2 / 0.2.0')
    if re.findall(r'^native-code: (.*)$', badging, re.M) != [f"'{ABI}'"]:
        raise Refused('wrong_abi: aapt does not report exactly arm64-v8a')
    if re.findall(r"^sdkVersion:'(\d+)'$", badging, re.M) != [MIN_SDK]:
        raise Refused('wrong_min_sdk: the APK floor is not API 29')
    return {'package': PACKAGE, 'versionCode': VERSION_CODE, 'versionName': VERSION_NAME, 'abi': ABI,
            'minSdk': MIN_SDK, 'signerSha256': SIGNER_SHA256, 'schemes': {'v2': 'API 24-27', 'v3': 'API 29+'},
            'nativeSha256': NATIVE_SHA256, 'tools': {k: proof[k] for k in TOOLS}}


# ---------------------------------------------------------------- publish

def _single_asset(release, digest_required):
    assets = release.get('assets') or []
    if len(assets) != 1:
        return None
    asset = assets[0]
    size, digest = INPUT_FILES[APK_NAME][1:]
    good = (asset.get('name') == APK_NAME and asset.get('state') == 'uploaded' and asset.get('size') == size
            and (asset.get('digest') in ((f'sha256:{digest}',) if digest_required else (None, f'sha256:{digest}'))))
    return asset if good else None


def publish(input_dir, client, workdir):
    """`client` is a github_release_publisher.GitHubReleases for the fixed destination repository."""
    check_files(input_dir)
    if client.repository != DESTINATION['repository']:
        raise Refused('destination_unexpected: not Immortal-Protocols/pixel-ota')
    rid, tag, size, digest = DESTINATION['releaseId'], DESTINATION['tag'], *INPUT_FILES[APK_NAME][1:]
    latest_before = client.latest_tag()
    if not str(latest_before or '').startswith(LATEST_PREFIX):
        raise Refused('latest_unexpected: the current Latest is not an OS release; nothing was changed')
    release = client.get_release(rid)
    if (release.get('id') != rid or release.get('tag_name') != tag or release.get('draft') is not True
            or release.get('prerelease') is not True):
        raise Refused('destination_unexpected: release 429690780 is not the draft prerelease rose-models-2')
    if release.get('assets'):
        raise Refused('destination_occupied: the draft already holds assets; nothing is overwritten')
    same_tag = client.find_release(tag)
    if not same_tag or same_tag.get('id') != rid:
        raise Refused('destination_collision: another release owns tag rose-models-2')
    outcome = {'schema': 'rose-models-publication-outcome/1', 'release': rid, 'tag': tag, 'asset': APK_NAME,
               'bytes': size, 'sha256': digest, 'latestBefore': latest_before, 'caveat': CAVEAT}
    try:
        client.upload(rid, Path(input_dir)/APK_NAME)
    except Exception as error:  # PublishError or transport: the outcome is unknown, so nothing else happens
        raise Refused(f'upload_failed: {error}; stopped without retry, the draft is left for inspection') from None
    asset = _single_asset(client.get_release(rid), digest_required=False)
    if asset is None:
        raise Refused('upload_unverified: the draft does not hold exactly the expected asset; stopped, not published')
    readback = Path(workdir)/'draft-readback.apk'
    client.download(asset['id'], readback, size)
    if sha256_file(readback) != digest:
        raise Refused('upload_unverified: the uploaded bytes differ; stopped, not published')
    published = client.publish_prerelease(rid)
    if published.get('draft') is not False or published.get('prerelease') is not True or published.get('tag_name') != tag:
        raise Refused('publish_unverified: the release did not become a published prerelease')
    final = client.get_release(rid)
    asset = _single_asset(final, digest_required=False)
    if final.get('draft') is not False or final.get('prerelease') is not True or asset is None:
        raise Refused(f'publish_unverified: {AFTER_PUBLISH} but is not exactly the expected prerelease')
    latest_after = client.latest_tag()
    if latest_after == tag or not str(latest_after or '').startswith(LATEST_PREFIX):
        raise Refused(f'latest_changed: {AFTER_PUBLISH}; Latest is no longer an OS release')
    public = Path(workdir)/'public-readback.apk'
    client.public_download(asset['browser_download_url'], public, size)
    if sha256_file(public) != digest:
        raise Refused(f'public_readback_mismatch: {AFTER_PUBLISH}; the public asset differs from the verified APK')
    return dict(outcome, outcome='published', latestAfter=latest_after, publicReadback='verified')


# ---------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('ingest').add_argument('--out', required=True)
    v = sub.add_parser('verify')
    v.add_argument('--input', required=True)
    v.add_argument('--sdk-root', required=True)
    sub.add_parser('publish').add_argument('--input', required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == 'ingest':
            if os.environ.get('GITHUB_REPOSITORY') != INPUT_REPOSITORY:
                raise Refused('input_repository: this job reads only Immortal-Protocols/RoseOS-1')
            token = os.environ.get('GITHUB_TOKEN', '')
            if not token:
                raise Refused('input_unreadable: no job token')
            result = ingest(a.out, lambda path: api_fetch(f'{API}/repos/{INPUT_REPOSITORY}/{path}', token))
        elif a.command == 'verify':
            result = verify(a.input, a.sdk_root)
        else:
            gh = _load('github_release_publisher', 'scripts/github_release_publisher.py')
            with tempfile.TemporaryDirectory(prefix='models-publish-') as work:
                try:
                    client = gh.GitHubReleases(DESTINATION['repository'], os.environ.get('PIXEL_OTA_TOKEN', ''))
                    result = publish(a.input, client, work)
                except gh.PublishError as error:
                    raise Refused(f'publisher_failed: {error}; stopped without retry') from None
    except Refused as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
