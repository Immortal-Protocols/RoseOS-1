#!/usr/bin/env python3
"""Rose Agent's portable, Agent-only unsigned build (workspace/rose-agent/portable-build.py), offline.

Synthetic fixtures only: a fake SDK, a fake JDK, synthetic public connection inputs (example.test endpoints), a fake
candidate git checkout, and a fake runner for npm, setup-zxing and build.sh. Nothing is built, signed, downloaded or
dispatched, and no real or private input is read. A passing run here is not a built or delivered Agent.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PB = load(ROOT/'workspace/rose-agent/portable-build.py', 'portable_build')
HAB = load(ROOT/'scripts/host-app-build.py', 'host_app_build_for_test')
PIN = 'a' * 64


def sha(data):
    return hashlib.sha256(data).hexdigest()


def executable(path, text='#!/bin/sh\nexit 0\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class Fixture:
    """A fake SDK, JDK and synthetic public inputs under one temporary directory."""

    def __init__(self, base):
        self.base = base
        self.sdk = base/'sdk'
        platform = self.sdk/'platforms/android-35'
        platform.mkdir(parents=True)
        (platform/'android.jar').write_bytes(b'fake android.jar')
        (platform/'source.properties').write_text('Pkg.Desc=Android SDK Platform 15\nAndroidVersion.ApiLevel=35\n')
        tools = self.sdk/'build-tools/35.0.0'
        tools.mkdir(parents=True)
        (tools/'source.properties').write_text('Pkg.UserSrc=false\nPkg.Revision=35.0.0\n')
        for tool in PB.TOOL_EXECUTABLES:
            executable(tools/tool)
        for name in PB.TOOL_FILES:
            (tools/name).parent.mkdir(parents=True, exist_ok=True)
            (tools/name).write_bytes(b'fake ' + name.encode())
        self.jdk = base/'jdk21'
        executable(self.jdk/'bin/java')
        (self.jdk/'release').write_text('JAVA_VERSION="21.0.5"\nMODULES="java.base jdk.compiler jdk.zipfs"\n')
        self.assets = base/'public-assets'
        contents = {
            'workspace/rose-agent/app/assets/connection.json': json.dumps({'endpoint': 'https://rose.example.test/', 'pin': PIN}),
            'workspace/rose-agent/app/assets/mac-screen-connection.json': json.dumps({'endpoint': 'https://mac.example.test/', 'pin': PIN}),
            'workspace/pixel-clipboard/clipboard-connection.json': json.dumps({'endpoint': 'https://clip.example.test/'}),
            'workspace/rose-agent/app/res/raw/mac_screen_cert.pem': 'synthetic fixture, not a certificate\n',
            'workspace/rose-agent/app/res/xml/mac_screen_network.xml': '<network-security-config/>\n'}
        assert set(contents) == set(HAB.PUBLIC_ASSETS)
        for relative, text in contents.items():
            (self.assets/relative).parent.mkdir(parents=True, exist_ok=True)
            (self.assets/relative).write_text(text)
        self.write_receipt()

    def write_receipt(self):
        files = {r: sha((self.assets/r).read_bytes()) for r in HAB.PUBLIC_ASSETS}
        (self.assets/HAB.RECEIPT).write_text(json.dumps({'receiptType': 'public-connection-assets', 'files': files}))
        return files

    def prepare(self, work='work', **overrides):
        args = dict(sdk_root=str(self.sdk), build_tools_version='35.0.0', java_home=str(self.jdk),
                    public_assets=str(self.assets), work=str(self.base/work))
        args.update(overrides)
        return PB.prepare(**args)


class Candidate:
    """A fake candidate checkout (an exact git commit) and a fake runner for npm, setup-zxing and build.sh."""
    ZXING = b'synthetic zxing core jar'

    def __init__(self, base, apks=1):
        self.path = base/'candidate'
        files = {
            'scripts/check-public-assets.mjs': (ROOT/'scripts/check-public-assets.mjs').read_text(),
            'scripts/setup-zxing.sh': f'#!/usr/bin/env bash\nSHA256={sha(self.ZXING)}\n',
            'workspace/rose-agent/server/package-lock.json': '{"lockfileVersion": 3}\n',
            'workspace/rose-agent/build.sh': '#!/usr/bin/env bash\n',
            '.gitignore': 'out/\n.tools/\nworkspace/rose-agent/app/assets/\nworkspace/rose-agent/app/res/\nworkspace/pixel-clipboard/\n'}
        for relative, text in files.items():
            (self.path/relative).parent.mkdir(parents=True, exist_ok=True)
            (self.path/relative).write_text(text)
        git = lambda *a: subprocess.run(['git', '-C', str(self.path), *a], check=True, capture_output=True)
        git('init', '-q')
        git('add', '-A')
        git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.test', 'commit', '-q', '-m', 'fixture')
        self.apks, self.zxing, self.calls = apks, self.ZXING, []

    def run(self, command, cwd=None, env=None, check=False, capture_output=False, text=False):
        self.calls.append(list(command))
        out = ''
        if command[:2] == ['bash', 'scripts/setup-zxing.sh']:
            jar = self.path/PB.ZXING_JAR
            jar.parent.mkdir(parents=True, exist_ok=True)
            jar.write_bytes(self.zxing)
        elif command[:2] == ['bash', 'workspace/rose-agent/build.sh']:
            for n in range(self.apks):
                apk = self.path/f'out/rose-agent.fixture{n}/rose-agent-unsigned.apk'
                apk.parent.mkdir(parents=True, exist_ok=True)
                apk.write_bytes(b'synthetic unsigned apk')
        elif command[-1:] == ['--version']:
            out = {'node': 'v22.0.0', 'npm': '10.0.0'}[command[0]]
        return subprocess.CompletedProcess(command, 0, stdout=out, stderr='')


class PortableBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fx = Fixture(self.tmp)

    def refused(self, fragment, function, *args, **kwargs):
        with self.assertRaises(PB.Refused) as caught:
            function(*args, **kwargs)
        self.assertIn(fragment, str(caught.exception))

    # ---- prepare: the D3 values ----
    def test_prepare_emits_six_absolute_d3_values_and_a_layout_view(self):
        d3 = self.fx.prepare()
        for key in PB.D3_FIELDS:
            self.assertTrue(d3[key], key)
        for key in ('appSdkRoot', 'javaHome', 'publicAssetsDir', 'aapt', 'apksigner'):
            self.assertTrue(Path(d3[key]).is_absolute(), key)
        view = Path(d3['appSdkRoot'])
        self.assertEqual(os.readlink(view/'build-tools/android-15'), str((self.fx.sdk/'build-tools/35.0.0').resolve()))
        self.assertEqual(os.readlink(view/'platforms/android-35'), str((self.fx.sdk/'platforms/android-35').resolve()))
        self.assertEqual(d3['aapt'], str(view/'build-tools/android-15/aapt'))
        self.assertEqual(d3['publicAssetDigests'], self.fx.write_receipt())
        self.assertEqual(d3['toolchain']['buildTools']['revision'], '35.0.0')
        self.assertEqual(json.loads((self.tmp/'work/d3.json').read_text()), d3)
        # host-app-build.build()'s own SDK check accepts the view unchanged (the legacy layout names).
        self.assertTrue((view/'build-tools/android-15/core-lambda-stubs.jar').is_file())

    # ---- public inputs ----
    def test_missing_input_is_refused(self):
        (self.fx.assets/HAB.PUBLIC_ASSETS[3]).unlink()
        self.refused('missing public inputs', self.fx.prepare)
        self.assertFalse((self.tmp/'work').exists())

    def test_missing_receipt_is_refused(self):
        (self.fx.assets/HAB.RECEIPT).unlink()
        self.refused('missing public inputs: ' + HAB.RECEIPT, self.fx.prepare)

    def test_tampered_input_is_refused(self):
        (self.fx.assets/HAB.PUBLIC_ASSETS[0]).write_text(json.dumps({'endpoint': 'https://other.example.test/', 'pin': PIN}))
        self.refused('does not match its recorded digest', self.fx.prepare)

    def test_unexpected_input_is_refused(self):
        (self.fx.assets/'workspace/rose-agent/app/assets/extra.json').write_text('{}')
        self.refused('unexpected files', self.fx.prepare)

    def test_symlinked_input_is_refused(self):
        target = self.fx.assets/HAB.PUBLIC_ASSETS[4]
        moved = self.tmp/'elsewhere.xml'
        shutil.move(target, moved)
        os.symlink(moved, target)
        self.refused('is a symlink', self.fx.prepare)

    def test_receipt_listing_other_files_is_refused(self):
        receipt = json.loads((self.fx.assets/HAB.RECEIPT).read_text())
        receipt['files'].pop(HAB.PUBLIC_ASSETS[1])
        (self.fx.assets/HAB.RECEIPT).write_text(json.dumps(receipt))
        self.refused('exactly the five inputs', self.fx.prepare)

    def test_pinned_digest_mismatch_is_refused(self):
        pinned = self.fx.write_receipt()
        pinned[HAB.PUBLIC_ASSETS[2]] = 'b' * 64
        path = self.tmp/'pinned.json'
        path.write_text(json.dumps(pinned))
        self.refused('pinned digests', self.fx.prepare, pinned_digests=str(path))

    def test_inputs_failing_the_public_checker_are_refused(self):
        # A loopback or plaintext endpoint is never accepted, even with a matching receipt.
        (self.fx.assets/HAB.PUBLIC_ASSETS[0]).write_text(json.dumps({'endpoint': 'http://127.0.0.1:8080/', 'pin': PIN}))
        self.fx.write_receipt()
        self.refused('check-public-assets', self.fx.prepare)

    # ---- incompatible tools ----
    def test_wrong_platform_api_is_refused(self):
        (self.fx.sdk/'platforms/android-35/source.properties').write_text('AndroidVersion.ApiLevel=34\n')
        self.refused('platform API 34', self.fx.prepare)

    def test_build_tools_must_be_an_explicit_35_revision(self):
        self.refused("not an explicit 35.x.y", self.fx.prepare, build_tools_version='34.0.0')
        self.refused("not an explicit 35.x.y", self.fx.prepare, build_tools_version='latest')
        self.refused('build-tools 35.0.1 missing', self.fx.prepare, build_tools_version='35.0.1')

    def test_build_tools_revision_mismatch_is_refused(self):
        (self.fx.sdk/'build-tools/35.0.0/source.properties').write_text('Pkg.Revision=35.0.0 rc4\n')
        self.refused('reports revision', self.fx.prepare)

    def test_missing_or_non_executable_tool_is_refused(self):
        (self.fx.sdk/'build-tools/35.0.0/apksigner').unlink()
        self.refused('missing apksigner', self.fx.prepare)
        executable(self.fx.sdk/'build-tools/35.0.0/apksigner')
        (self.fx.sdk/'build-tools/35.0.0/d8').chmod(0o644)
        self.refused('missing d8', self.fx.prepare)

    def test_jdk_must_be_21_with_the_compiler(self):
        (self.fx.jdk/'release').write_text('JAVA_VERSION="17.0.9"\nMODULES="java.base jdk.compiler"\n')
        self.refused('JDK 17.0.9 is not JDK 21', self.fx.prepare)
        (self.fx.jdk/'release').write_text('JAVA_VERSION="21.0.5"\nMODULES="java.base"\n')
        self.refused('lacks jdk.compiler', self.fx.prepare)

    def test_relative_paths_are_refused(self):
        self.refused('must be an absolute, normalized path', self.fx.prepare, sdk_root='sdk')

    # ---- output collisions ----
    def test_existing_work_directory_is_refused(self):
        (self.tmp/'work').mkdir()
        self.refused('already exists', self.fx.prepare)

    def test_existing_release_directory_is_refused(self):
        self.fx.prepare()
        candidate = Candidate(self.tmp)
        (self.tmp/'release').mkdir()
        self.refused('already exists', PB.build, str(candidate.path), str(self.tmp/'release'), str(self.tmp/'work/d3.json'),
                     run=candidate.run)
        self.assertEqual(candidate.calls, [], 'nothing ran')

    # ---- build: Agent only, with a receipt ----
    def test_build_is_agent_only_and_binds_source_toolchain_inputs_and_output(self):
        d3 = self.fx.prepare()
        candidate = Candidate(self.tmp)
        release = self.tmp/'release'
        receipt = PB.build(str(candidate.path), str(release), str(self.tmp/'work/d3.json'), run=candidate.run)
        self.assertEqual(sorted(str(p.relative_to(release)) for p in release.rglob('*') if p.is_file()),
                         ['agent-build-receipt.json', 'unsigned/voice.apk'])
        commands = [' '.join(c) for c in candidate.calls]
        for forbidden in ('calculator', 'build-background-agent', 'controller', 'sign', 'apksigner', 'keystore', 'gcloud'):
            self.assertFalse(any(forbidden in c for c in commands), forbidden)
        self.assertIn('npm ci --ignore-scripts --no-audit --no-fund', commands)
        self.assertIn('bash scripts/setup-zxing.sh', commands)
        head = subprocess.run(['git', '-C', str(candidate.path), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        self.assertEqual(receipt['source']['candidateSha'], head)
        self.assertEqual(receipt['source']['packageLockSha256'], sha(b'{"lockfileVersion": 3}\n'))
        self.assertEqual(receipt['inputs']['publicAssetDigests'], d3['publicAssetDigests'])
        self.assertEqual(receipt['output'], {'path': 'unsigned/voice.apk', 'sha256': sha(b'synthetic unsigned apk'),
                                             'size': len(b'synthetic unsigned apk')})
        self.assertEqual(receipt['toolchain']['zxingSha256'], sha(Candidate.ZXING))
        self.assertEqual(receipt['toolchain']['buildTools'], d3['toolchain']['buildTools'])
        body = {k: v for k, v in receipt.items() if k != 'receiptSha256'}
        self.assertEqual(receipt['receiptSha256'], hashlib.sha256(PB.canonical(body)).hexdigest())
        self.assertEqual(json.loads((release/'agent-build-receipt.json').read_text()), receipt)
        text = json.dumps(receipt).lower()
        for secret_word in ('password', 'keystore', 'secret', 'token', 'pin'):
            self.assertNotIn(secret_word, text.replace('pinned', ''), secret_word)

    def test_toolchain_changed_after_prepare_is_refused(self):
        self.fx.prepare()
        executable(self.fx.sdk/'build-tools/35.0.0/aapt', '#!/bin/sh\necho swapped\n')
        candidate = Candidate(self.tmp)
        self.refused('toolchain changed', PB.build, str(candidate.path), str(self.tmp/'release'),
                     str(self.tmp/'work/d3.json'), run=candidate.run)

    def test_inputs_tampered_after_prepare_are_refused(self):
        self.fx.prepare()
        (self.fx.assets/HAB.PUBLIC_ASSETS[3]).write_text('changed\n')
        self.fx.write_receipt()
        candidate = Candidate(self.tmp)
        self.refused('pinned digests', PB.build, str(candidate.path), str(self.tmp/'release'),
                     str(self.tmp/'work/d3.json'), run=candidate.run)

    def test_candidate_with_tracked_changes_is_refused(self):
        self.fx.prepare()
        candidate = Candidate(self.tmp)
        (candidate.path/'workspace/rose-agent/build.sh').write_text('#!/usr/bin/env bash\necho changed\n')
        self.refused('tracked changes', PB.build, str(candidate.path), str(self.tmp/'release'),
                     str(self.tmp/'work/d3.json'), run=candidate.run)

    def test_zxing_not_matching_its_pin_leaves_no_output(self):
        self.fx.prepare()
        candidate = Candidate(self.tmp)
        candidate.zxing = b'a different jar'
        self.refused('ZXing', PB.build, str(candidate.path), str(self.tmp/'release'), str(self.tmp/'work/d3.json'),
                     run=candidate.run)
        self.assertFalse((self.tmp/'release').exists())

    def test_ambiguous_build_output_leaves_no_output(self):
        self.fx.prepare()
        candidate = Candidate(self.tmp, apks=2)
        with self.assertRaises(SystemExit):
            PB.build(str(candidate.path), str(self.tmp/'release'), str(self.tmp/'work/d3.json'), run=candidate.run)
        self.assertFalse((self.tmp/'release').exists())

    # ---- inputs: the portable-inputs overlay for the hosted release route ----
    def test_inputs_writes_releases_exact_seven_key_record(self):
        d3 = self.fx.prepare()
        out = self.tmp/'portable-inputs.json'
        written = PB.inputs(str(self.tmp/'work/d3.json'), str(out))
        # Release's --portable-inputs record: one flat object, exactly these seven keys, this schema.
        self.assertEqual(set(written), {'schema', 'appSdkRoot', 'javaHome', 'publicAssetsDir', 'publicAssetDigests',
                                        'aapt', 'apksigner'})
        self.assertEqual(written['schema'], 'roseos.agent-portable-inputs.v1')
        self.assertEqual({k: v for k, v in written.items() if k != 'schema'}, {k: d3[k] for k in PB.D3_FIELDS})
        self.assertEqual(set(written['publicAssetDigests']), set(HAB.PUBLIC_ASSETS))
        for value in written['publicAssetDigests'].values():
            self.assertRegex(value, r'^[0-9a-f]{64}$')
        for key in PB.PATH_FIELDS:
            self.assertEqual(os.path.normpath(written[key]), written[key], key)
            self.assertTrue(os.path.isabs(written[key]) and written[key] != '/' and not written[key].endswith('/'), key)
        self.assertTrue(out.is_file() and not out.is_symlink() and out.stat().st_size <= 64*1024)
        # Strict JSON: no duplicate keys, NaN or Infinity.
        json.loads(out.read_text(), object_pairs_hook=lambda pairs: (
            self.assertEqual(len(pairs), len({k for k, _ in pairs})), dict(pairs))[1],
            parse_constant=lambda c: self.fail(f'non-finite constant {c}'))
        text = out.read_text()
        for release_owned in ('appSecrets', 'project', 'secretVersion', 'reservation', 'appSignerSha256', 'toolchain'):
            self.assertNotIn(release_owned, text, release_owned)

    def test_the_six_fields_are_exactly_the_hosted_routes_unbound_app_inputs(self):
        # Applied where hosted.json keeps them (host.integrate / host.tools), the overlay leaves the Release consumer
        # (scripts/host-app-release.py, PR611) lacking nothing: Release binds its own reservation store (decision D1). The exact overlay nesting is
        # Release's --portable-inputs reader's; this pins only that the six fields are the right ones.
        self.fx.prepare()
        written = PB.inputs(str(self.tmp/'work/d3.json'), str(self.tmp/'portable-inputs.json'))
        shared = json.loads((ROOT/'os/release-pipeline/agent/hosted.json').read_text())
        release = load(ROOT/'scripts/host-app-release.py', 'host_app_release_for_test')
        unbound = set(release.hosted_missing_inputs(shared))
        app_fields = {'host.integrate.appSdkRoot', 'host.integrate.javaHome', 'host.integrate.publicAssetsDir',
                      'host.tools.aapt', 'host.tools.apksigner'}
        self.assertTrue(app_fields <= unbound)
        for key in ('appSdkRoot', 'javaHome', 'publicAssetsDir', 'publicAssetDigests'):
            shared['host']['integrate'][key] = written[key]
        for key in ('aapt', 'apksigner'):
            shared['host']['tools'][key] = written[key]
        self.assertEqual(release.hosted_missing_inputs(shared), [])

    def test_paths_must_be_absolute_and_normalized(self):
        base = str(self.tmp)
        for bad in ('sdk', '/', f'{base}/sdk/', f'{base}/./sdk', f'{base}/x/../sdk', f'/{base}/sdk', f'{base}//sdk',
                    f'{base}/sdk\0x'):
            self.refused('absolute, normalized', self.fx.prepare, sdk_root=bad)

    def test_inputs_refuses_bad_digests_or_paths_in_d3(self):
        d3 = self.fx.prepare()
        for mutate, fragment in ((lambda d: d['publicAssetDigests'].pop(HAB.PUBLIC_ASSETS[0]), 'exactly the five'),
                                 (lambda d: d['publicAssetDigests'].update({HAB.PUBLIC_ASSETS[0]: 'A' * 64}), 'lowercase'),
                                 (lambda d: d.update(aapt=d['aapt'] + '/'), 'normalized')):
            bad = json.loads(json.dumps(d3))
            mutate(bad)
            path = self.tmp/f'd3-bad-{fragment[:5]}.json'
            path.write_text(json.dumps(bad))
            self.refused(fragment, PB.inputs, str(path), str(self.tmp/f'out-{fragment[:5]}.json'))
            self.assertFalse((self.tmp/f'out-{fragment[:5]}.json').exists())

    def test_inputs_refuses_an_existing_output(self):
        self.fx.prepare()
        out = self.tmp/'portable-inputs.json'
        out.write_text('{}')
        self.refused('already exists', PB.inputs, str(self.tmp/'work/d3.json'), str(out))

    def test_inputs_refuses_an_incomplete_d3(self):
        d3 = self.fx.prepare()
        d3.pop('apksigner')
        path = self.tmp/'d3-incomplete.json'
        path.write_text(json.dumps(d3))
        self.refused('not a complete', PB.inputs, str(path), str(self.tmp/'out.json'))

    def test_tool_output_goes_to_stderr_so_stdout_is_only_the_json(self):
        seen = {}

        def fake(command, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(command, 0)
        original = PB.subprocess.run
        PB.subprocess.run = fake
        try:
            PB.tool_run(['bash', 'workspace/rose-agent/build.sh'], cwd='/x', check=True)
            self.assertIs(seen['stdout'], PB.sys.stderr)
            seen.clear()
            PB.tool_run(['node', '--version'], capture_output=True, text=True, check=True)
            self.assertNotIn('stdout', seen, 'version probes still capture their output')
        finally:
            PB.subprocess.run = original
        self.assertIs(PB.build.__defaults__[0], PB.tool_run)

    # ---- the command line ----
    def test_cli_reports_refusals_without_a_traceback(self):
        code = PB.main(['prepare', '--sdk-root', str(self.fx.sdk), '--build-tools-version', '34.0.0', '--java-home',
                        str(self.fx.jdk), '--public-assets', str(self.fx.assets), '--work', str(self.tmp/'w')])
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
