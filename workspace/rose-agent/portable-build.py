#!/usr/bin/env python3
"""Rose Agent's portable, Agent-only unsigned build (App Product). Source only: it builds nothing by itself on import.

The build path for independent (for example GitHub-hosted) capacity. It reuses scripts/host-app-build.py unchanged:
restore() of the public connection inputs, build(voice_only=True) with its locked `npm ci --ignore-scripts`, the
candidate's SHA-pinned scripts/setup-zxing.sh (ZXing core 3.5.4), and workspace/rose-agent/build.sh. What it adds is
explicit inputs instead of the Frankel builder's config, checked before anything runs:

  prepare --sdk-root ABS --build-tools-version 35.0.0 --java-home ABS --public-assets ABS --work ABS
          [--public-asset-digests FILE]
      Verify an Android SDK (platform API 35, the exact build-tools revision), a JDK 21 with jdk.compiler, and a
      public-input directory holding exactly the PUBLIC_ASSETS plus their digest receipt (no extra, missing,
      symlinked or tampered file; the repository's check-public-assets.mjs passes). Then make WORK/sdk-view, a
      symlinked layout with the platforms/android-35 and build-tools/android-15 paths build.sh and host-app-build
      expect, and write WORK/d3.json: the six absolute values the hosted release route consumes
      (appSdkRoot, javaHome, publicAssetsDir, publicAssetDigests, aapt, apksigner) plus the toolchain facts.
  build --candidate ABS --release-dir ABS --d3 FILE
      Re-verify the toolchain and inputs against d3.json, build Rose Agent only (never Calculator, the controller or
      any signing) into RELEASE_DIR/unsigned/voice.apk, and write RELEASE_DIR/agent-build-receipt.json binding the
      exact source, toolchain, inputs and unsigned output.
  inputs --d3 FILE --out FILE
      Write the record Release's hosted route reads (`host-app-release.py --hosted --portable-inputs FILE`, and
      `--check --hosted --portable-inputs FILE`): one flat roseos.agent-portable-inputs.v1 object with exactly the
      schema and the six D3 fields, never a copy of the hosted config. Signing, project and reservation values stay
      Release's; no path is committed.

Every path is absolute; every output (WORK, RELEASE_DIR, OUT) must not exist yet. Nothing reads a key, password or
credential, and no default or loopback connection data is ever invented: the inputs come only from the caller.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_D3, SCHEMA_RECEIPT = 'roseos.agent-portable-d3.v1', 'roseos.agent-portable-build-receipt.v1'
# Release's --hosted --portable-inputs record (scripts/host-app-release.py): exactly these seven keys, flat.
SCHEMA_INPUTS = 'roseos.agent-portable-inputs.v1'
PATH_FIELDS = ('appSdkRoot', 'javaHome', 'publicAssetsDir', 'aapt', 'apksigner')
PLATFORM_API, JDK_MAJOR, BUILD_TOOLS_MAJOR = 35, 21, 35
# The view's names are the ones workspace/rose-agent/build.sh and host-app-build.build() read.
VIEW_PLATFORM, VIEW_TOOLS = 'platforms/android-35', 'build-tools/android-15'
TOOL_EXECUTABLES = ('aapt', 'd8', 'zipalign', 'apksigner')
TOOL_FILES = ('core-lambda-stubs.jar', 'lib/d8.jar', 'lib/apksigner.jar')
D3_FIELDS = ('appSdkRoot', 'javaHome', 'publicAssetsDir', 'publicAssetDigests', 'aapt', 'apksigner')
ZXING_JAR = '.tools/zxing/core-3.5.4.jar'


class Refused(Exception):
    """A precondition failed; nothing was built and no output was left behind."""


def load_host_app_build():
    spec = importlib.util.spec_from_file_location('host_app_build', ROOT/'scripts/host-app-build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':')).encode()


def absolute(path, what):
    """An absolute, normalized path (os.path.normpath(p) == p): not /, no trailing slash, '.', '..', '//' or NUL."""
    text = os.fspath(path)
    if not os.path.isabs(text) or '\0' in text or text == '/' or os.path.normpath(text) != text or text.startswith('//'):
        raise Refused(f'{what} must be an absolute, normalized path: {text!r}')
    return Path(text)


def fresh(path, what):
    path = absolute(path, what)
    if path.exists() or path.is_symlink():
        raise Refused(f'{what} {path} already exists; use a new directory')
    return path


def properties(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def check_platform(sdk):
    platform = sdk/VIEW_PLATFORM
    jar, props = platform/'android.jar', platform/'source.properties'
    if not jar.is_file() or not props.is_file():
        raise Refused(f'Android platform {PLATFORM_API} missing under {sdk}')
    api = properties(props).get('AndroidVersion.ApiLevel')
    if api != str(PLATFORM_API):
        raise Refused(f'platform API {api} is not {PLATFORM_API}')
    return platform


def check_build_tools(sdk, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version or '') or int(version.split('.')[0]) != BUILD_TOOLS_MAJOR:
        raise Refused(f'build-tools {version!r} is not an explicit {BUILD_TOOLS_MAJOR}.x.y revision')
    tools = sdk/'build-tools'/version
    props = tools/'source.properties'
    if not props.is_file():
        raise Refused(f'build-tools {version} missing under {sdk}')
    revision = properties(props).get('Pkg.Revision')
    if revision != version:
        raise Refused(f'build-tools directory {version} reports revision {revision}')
    absent = [t for t in TOOL_EXECUTABLES if not (tools/t).is_file() or not os.access(tools/t, os.X_OK)]
    absent += [t for t in TOOL_FILES if not (tools/t).is_file()]
    if absent:
        raise Refused(f'build-tools {version} is missing {", ".join(absent)}')
    return tools


def check_jdk(java_home):
    release, java = java_home/'release', java_home/'bin/java'
    if not release.is_file() or not java.is_file() or not os.access(java, os.X_OK):
        raise Refused(f'{java_home} is not a JDK (no release file or bin/java)')
    facts = {k: v.strip('"') for k, v in properties(release).items()}
    version = facts.get('JAVA_VERSION', '')
    if version.split('.')[0] != str(JDK_MAJOR):
        raise Refused(f'JDK {version or "unknown"} is not JDK {JDK_MAJOR}')
    if 'jdk.compiler' not in facts.get('MODULES', '').split():
        raise Refused('the JDK lacks jdk.compiler, which build.sh compiles with')
    return version


def check_inputs(assets, pinned=None, checker=ROOT/'scripts/check-public-assets.mjs'):
    """Exactly the public inputs and their receipt: present, regular, not symlinked, matching the receipt (and the
    pinned digests when given), nothing else in the directory, and the repository's checker passes. A directory
    staged before an input was retired may still hold it and list it (host-app-build.RETIRED_ASSETS, as restore()
    allows): it is ignored, never checked, copied or required, so retiring an input needs no restage."""
    hab = load_host_app_build()
    expected, retired = set(hab.PUBLIC_ASSETS) | {hab.RECEIPT}, set(getattr(hab, 'RETIRED_ASSETS', ()))
    if not assets.is_dir() or assets.is_symlink():
        raise Refused(f'public input directory {assets} missing')
    found = set()
    for path in assets.rglob('*'):
        if path.is_symlink():
            raise Refused(f'public input {path.relative_to(assets)} is a symlink')
        if path.is_file():
            found.add(str(path.relative_to(assets)))
    missing, unexpected = sorted(expected - found), sorted(found - expected - retired)
    if missing:
        raise Refused('missing public inputs: ' + ', '.join(missing))
    if unexpected:
        raise Refused('unexpected files among the public inputs: ' + ', '.join(unexpected))
    receipt = json.loads((assets/hab.RECEIPT).read_text())
    if set(receipt) != {'receiptType', 'files'} or receipt['receiptType'] != 'public-connection-assets' \
            or set(receipt['files']) - retired != set(hab.PUBLIC_ASSETS):
        raise Refused('the public input receipt does not list exactly the public inputs')
    files = {relative: receipt['files'][relative] for relative in hab.PUBLIC_ASSETS}
    for relative in hab.PUBLIC_ASSETS:
        if sha256(assets/relative) != receipt['files'][relative]:
            raise Refused(f'public input {relative} does not match its recorded digest')
    if pinned is not None and pinned != files:
        raise Refused('the public inputs do not match the pinned digests')
    staging = Path(tempfile.mkdtemp(prefix='rose-public-check-'))  # private; never beside the (possibly read-only) inputs
    try:
        for relative in hab.PUBLIC_ASSETS:
            if relative.endswith('.json'):
                shutil.copyfile(assets/relative, staging/Path(relative).name)
        result = subprocess.run(['node', str(checker), str(staging)], capture_output=True, text=True)
        if result.returncode:
            raise Refused('public connection inputs failed check-public-assets.mjs')
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return files


def toolchain_facts(view, java_home, jdk_version, revision):
    tools = view/VIEW_TOOLS
    return {'platformApi': PLATFORM_API, 'androidJarSha256': sha256(view/VIEW_PLATFORM/'android.jar'),
            'buildTools': {'revision': revision, 'sha256': {t: sha256(tools/t) for t in (*TOOL_EXECUTABLES, *TOOL_FILES)}},
            'jdk': {'version': jdk_version, 'javaSha256': sha256(java_home/'bin/java')}}


def prepare(sdk_root, build_tools_version, java_home, public_assets, work, pinned_digests=None):
    sdk, java_home = absolute(sdk_root, 'sdk root'), absolute(java_home, 'java home')
    assets, work = absolute(public_assets, 'public input directory'), fresh(work, 'work directory')
    platform = check_platform(sdk)
    tools = check_build_tools(sdk, build_tools_version)
    jdk_version = check_jdk(java_home)
    pinned = json.loads(Path(pinned_digests).read_text()) if pinned_digests else None
    digests = check_inputs(assets, pinned)
    view = work/'sdk-view'
    (view/'platforms').mkdir(parents=True)
    (view/'build-tools').mkdir()
    os.symlink(platform.resolve(), view/VIEW_PLATFORM)
    os.symlink(tools.resolve(), view/VIEW_TOOLS)
    d3 = {'schema': SCHEMA_D3, 'appSdkRoot': str(view), 'javaHome': str(java_home), 'publicAssetsDir': str(assets),
          'publicAssetDigests': digests, 'aapt': str(view/VIEW_TOOLS/'aapt'), 'apksigner': str(view/VIEW_TOOLS/'apksigner'),
          'toolchain': toolchain_facts(view, java_home, jdk_version, build_tools_version)}
    (work/'d3.json').write_text(json.dumps(d3, indent=2, sort_keys=True) + '\n')
    return d3


def read_d3(path):
    d3 = json.loads(Path(absolute(path, 'd3 file')).read_text())
    if d3.get('schema') != SCHEMA_D3 or any(not d3.get(k) for k in (*D3_FIELDS, 'toolchain')):
        raise Refused(f'{path} is not a complete {SCHEMA_D3} record')
    for key in PATH_FIELDS:
        absolute(d3[key], key)
    return d3


def git(candidate, *args):
    return subprocess.run(['git', '-C', str(candidate), *args], capture_output=True, text=True, check=True).stdout.strip()


def zxing_pin(candidate):
    script = candidate/'scripts/setup-zxing.sh'
    match = re.search(r'^SHA256=([0-9a-f]{64})$', script.read_text(), re.M) if script.is_file() else None
    if not match:
        raise Refused('the candidate has no SHA-pinned scripts/setup-zxing.sh')
    return match.group(1)


def tool_run(command, **kwargs):
    """subprocess.run for the build's tools with their output on stderr, so stdout carries only the JSON result."""
    if not kwargs.get('capture_output') and 'stdout' not in kwargs:
        kwargs['stdout'] = sys.stderr
    return subprocess.run(command, **kwargs)


def build(candidate, release_dir, d3_path, run=tool_run):
    candidate, release_dir = absolute(candidate, 'candidate'), fresh(release_dir, 'release directory')
    d3 = read_d3(d3_path)
    view, java_home, assets = Path(d3['appSdkRoot']), Path(d3['javaHome']), Path(d3['publicAssetsDir'])
    revision = d3['toolchain']['buildTools']['revision']
    check_platform(view)
    jdk_version = check_jdk(java_home)
    if toolchain_facts(view, java_home, jdk_version, revision) != d3['toolchain']:
        raise Refused('the toolchain changed since prepare (tool, platform or JDK digests differ)')
    digests = check_inputs(assets, d3['publicAssetDigests'], checker=candidate/'scripts/check-public-assets.mjs')
    if git(candidate, 'status', '--porcelain', '--untracked-files=no'):
        raise Refused('the candidate has tracked changes; build only an exact commit')
    source = {'candidateSha': git(candidate, 'rev-parse', 'HEAD'), 'treeSha': git(candidate, 'rev-parse', 'HEAD^{tree}'),
              'packageLockSha256': sha256(candidate/'workspace/rose-agent/server/package-lock.json')}
    pin = zxing_pin(candidate)
    hab = load_host_app_build()
    try:
        hab.build(candidate, release_dir, view, java_home, assets, digests, run=run, voice_only=True)
        zxing = sha256(candidate/ZXING_JAR)
        if zxing != pin:
            raise Refused('the ZXing jar does not match setup-zxing.sh\'s pinned SHA-256')
        built = sorted(str(p.relative_to(release_dir)) for p in release_dir.rglob('*') if p.is_file())
        if built != ['unsigned/voice.apk']:
            raise Refused(f'the Agent-only build produced {built}, expected only unsigned/voice.apk')
    except BaseException:
        shutil.rmtree(release_dir, ignore_errors=True)  # never leave a partial output to collect
        raise
    apk = release_dir/'unsigned/voice.apk'
    receipt = {'schema': SCHEMA_RECEIPT, 'scope': 'Rose Agent only: unsigned; no Calculator, controller or signing',
               'source': source, 'toolchain': dict(d3['toolchain'], zxingSha256=zxing,
                                                   node=run_version(run, ['node', '--version']),
                                                   npm=run_version(run, ['npm', '--version'])),
               'inputs': {'publicAssetDigests': digests, 'receiptSha256': sha256(assets/hab.RECEIPT)},
               'output': {'path': 'unsigned/voice.apk', 'sha256': sha256(apk), 'size': apk.stat().st_size}}
    receipt['receiptSha256'] = hashlib.sha256(canonical(receipt)).hexdigest()
    (release_dir/'agent-build-receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt


def run_version(run, command):
    result = run(command, capture_output=True, text=True, check=True)
    return (result.stdout or '').strip()


def overlay(d3):
    """Release's portable-inputs record: one flat object with exactly the schema and the six D3 fields. The path
    fields are absolute and normalized; publicAssetDigests holds exactly the PUBLIC_ASSETS with lowercase 64-hex
    SHA-256 values. Nothing else: no hosted config, reservation, signer, secret, package or minimum-OS field."""
    record = {'schema': SCHEMA_INPUTS, **{key: d3[key] for key in D3_FIELDS}}
    for key in PATH_FIELDS:
        absolute(record[key], key)
    digests = record['publicAssetDigests']
    if not isinstance(digests, dict) or set(digests) != set(load_host_app_build().PUBLIC_ASSETS) \
            or not all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for v in digests.values()):
        raise Refused('publicAssetDigests must map exactly the public inputs to lowercase SHA-256 values')
    return record


def inputs(d3_path, out):
    out = fresh(out, 'portable-inputs file')
    data = overlay(read_d3(d3_path))
    out.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    return data


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    for flag in ('--sdk-root', '--build-tools-version', '--java-home', '--public-assets', '--work'):
        p.add_argument(flag, required=True)
    p.add_argument('--public-asset-digests')
    b = sub.add_parser('build')
    for flag in ('--candidate', '--release-dir', '--d3'):
        b.add_argument(flag, required=True)
    c = sub.add_parser('inputs')
    for flag in ('--d3', '--out'):
        c.add_argument(flag, required=True)
    a = parser.parse_args(argv)
    try:
        if a.command == 'prepare':
            result = prepare(a.sdk_root, a.build_tools_version, a.java_home, a.public_assets, a.work, a.public_asset_digests)
        elif a.command == 'build':
            result = build(a.candidate, a.release_dir, a.d3)
        else:
            result = inputs(a.d3, a.out)
    except Refused as error:
        print(json.dumps({'verdict': 'REFUSED', 'error': str(error)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, SystemExit) as error:
        print(json.dumps({'verdict': 'FAIL', 'error': f'{error.__class__.__name__}: {error}'}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
