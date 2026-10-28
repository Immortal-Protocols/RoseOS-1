"""host-app-build.py and host-reconcile.py with fake candidates and git source trees: public asset staging and
digest checks, output capture by before/after sets, and receipt-based reconciliation that never overwrites
owner changes."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
PY = sys.executable
sys.path.insert(0, str(SCRIPTS))

def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

app_build, reconcile, sdk_layout = load('host-app-build'), load('host-reconcile'), load('host-sdk-layout')
PIN = 'ab' * 32

def write(path, text, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    if mode:
        path.chmod(mode)
    return path

class AppBuildTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = self.root = Path(temp.name)
        self.source, self.candidate, self.release = root/'p1-inputs', root/'candidate', root/'release'
        assets = {'workspace/rose-agent/app/assets/connection.json': {'endpoint': 'https://100.64.0.1:9444', 'pin': PIN},
            'workspace/rose-agent/app/assets/mac-screen-connection.json': {'endpoint': 'https://100.64.0.1:9445', 'pin': PIN},
            'workspace/pixel-clipboard/clipboard-connection.json': {'endpoint': 'https://100.64.0.1:9446'}}
        for relative, value in assets.items():
            write(self.source/relative, json.dumps(value))
        write(self.source/'workspace/rose-agent/app/res/raw/mac_screen_cert.pem', '-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----\n')
        write(self.source/'workspace/rose-agent/app/res/xml/mac_screen_network.xml', '<network-security-config/>\n')
        shutil.copy(SCRIPTS/'check-public-assets.mjs', (self.candidate/'scripts').mkdir(parents=True) or self.candidate/'scripts')
        # Candidate build scripts: fakes that require the restored inputs and write fresh unique outputs.
        write(self.candidate/'workspace/rose-agent/build.sh', '''
            set -e; test -f workspace/rose-agent/app/assets/connection.json; test -n "$ANDROID_SDK_ROOT"
            d=$(mktemp -d out/rose-agent.XXXXXX); echo voice > "$d/rose-agent-unsigned.apk"''')
        write(self.candidate/'workspace/pixel-calculator-agent/build.sh', '''
            set -e; d=$(mktemp -d out/calculator.XXXXXX); echo calculator > "$d/calculator-unsigned.apk"''')
        write(self.candidate/'scripts/build-background-agent.py', '''
            import sys; out = sys.argv[sys.argv.index('--out') + 1]; open(out + '/Agent-unsigned.apk', 'w').write('controller')''')
        (self.candidate/'workspace/rose-agent/server').mkdir(parents=True)
        self.sdk = root/'sdk'
        for name in ('platforms/android-35/android.jar', 'build-tools/android-15/core-lambda-stubs.jar', 'build-tools/android-15/apksigner',
                     'build-tools/android-15/aapt', 'build-tools/android-15/d8', 'build-tools/android-15/zipalign'):
            write(self.sdk/name, 'x')
        write(root/'jdk/bin/java', '#!/bin/sh\n', 0o755)
        self.npm_calls = []
        self.assets = root/'staged'

    def run_fake(self, argv, cwd, env, check):
        if argv[0] == 'npm':
            self.npm_calls.append((argv, str(cwd)))
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.run(argv, cwd=cwd, env=env, check=check)

    def test_stage_then_build_captures_exactly_the_new_outputs(self):
        receipt = app_build.stage(self.source, self.assets, self.candidate)
        self.assertEqual(sorted(receipt['files']), sorted(app_build.PUBLIC_ASSETS))
        (self.candidate/'out/rose-agent.old').mkdir(parents=True)
        (self.candidate/'out/rose-agent.old/rose-agent-unsigned.apk').write_text('older build, must be ignored')
        hashes = app_build.build(self.candidate, self.release, self.sdk, self.root/'jdk', self.assets, run=self.run_fake)
        self.assertEqual((self.release/'unsigned/voice.apk').read_text().strip(), 'voice')
        self.assertEqual((self.release/'unsigned/calculator.apk').read_text().strip(), 'calculator')
        self.assertEqual((self.release/'unsigned/controller/Agent-unsigned.apk').read_text(), 'controller')
        self.assertEqual(set(hashes), {'voice', 'calculator', 'controller'})
        self.assertEqual(self.npm_calls[0][0][:3], ['npm', 'ci', '--ignore-scripts'])
        self.assertTrue(self.npm_calls[0][1].endswith('workspace/rose-agent/server'))

    def test_tampered_staged_input_or_unsafe_asset_is_refused(self):
        app_build.stage(self.source, self.assets, self.candidate)
        (self.assets/app_build.PUBLIC_ASSETS[3]).write_text('changed')
        with self.assertRaisesRegex(SystemExit, 'recorded digest'):
            app_build.build(self.candidate, self.release, self.sdk, self.root/'jdk', self.assets, run=self.run_fake)
        write(self.source/'workspace/rose-agent/app/assets/connection.json', json.dumps({'endpoint': 'http://127.0.0.1:9444', 'pin': PIN}))
        with self.assertRaises(subprocess.CalledProcessError):
            app_build.stage(self.source, self.root/'staged-2', self.candidate)

    def test_ambiguous_outputs_and_missing_sdk_fail(self):
        app_build.stage(self.source, self.assets, self.candidate)
        write(self.candidate/'workspace/rose-agent/build.sh', '''
            set -e; for i in 1 2; do d=$(mktemp -d out/rose-agent.XXXXXX); echo v > "$d/rose-agent-unsigned.apk"; done''')
        with self.assertRaisesRegex(SystemExit, 'exactly one new'):
            app_build.build(self.candidate, self.release, self.sdk, self.root/'jdk', self.assets, run=self.run_fake)
        (self.sdk/'build-tools/android-15/core-lambda-stubs.jar').unlink()
        with self.assertRaisesRegex(SystemExit, 'core-lambda-stubs.jar'):
            app_build.build(self.candidate, self.root/'release-2', self.sdk, self.root/'jdk', self.assets, run=self.run_fake)

class SdkLayoutTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = self.source = self.root/'R'
        for tool in ('aapt', 'aapt2', 'd8', 'zipalign'):
            write(source/'prebuilts/sdk/tools/linux/bin'/tool, '#!/bin/sh\nexit 0\n', 0o755)
        write(source/'prebuilts/sdk/tools/core-lambda-stubs.jar', 'stubs')
        write(source/'prebuilts/sdk/35/public/android.jar', 'android')
        write(source/'prebuilts/jdk/jdk21/linux-x86/bin/java', '#!/bin/sh\ntest "$1" = -jar && exit 0\n', 0o755)
        self.jar = write(self.root/'apksigner.jar', 'retained public apksigner jar')

    def test_layout_links_existing_tools_and_runs_each(self):
        receipt = sdk_layout.install(self.root/'sdk', self.source, self.jar)
        tools = self.root/'sdk/build-tools/android-15'
        self.assertTrue((tools/'d8').is_symlink() and (tools/'core-lambda-stubs.jar').is_symlink())
        self.assertEqual((tools/'lib/apksigner.jar').read_text(), 'retained public apksigner jar')
        self.assertTrue(os.access(tools/'apksigner', os.X_OK))
        self.assertIn('platforms/android-35/android.jar', receipt['files'])
        with self.assertRaisesRegex(SystemExit, 'already exists'):
            sdk_layout.install(self.root/'sdk', self.source, self.jar)

    def test_missing_existing_tool_installs_nothing(self):
        (self.source/'prebuilts/sdk/tools/core-lambda-stubs.jar').unlink()
        with self.assertRaisesRegex(SystemExit, 'core-lambda-stubs.jar'):
            sdk_layout.install(self.root/'sdk', self.source, self.jar)
        self.assertFalse((self.root/'sdk').exists())

def git(*args, cwd):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)

class ReconcileTest(unittest.TestCase):
    """A two-project source tree; the fake candidate apply script edits a tracked file, adds an overlay and writes a
    Rose-only preinstall directory, refusing (like the real script) to run on a dirty project."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = self.root = Path(temp.name)
        self.source, self.candidate = root/'R', root/'candidate'
        for project, files in (('frameworks/base', {'core/res/res/values/config.xml': '<resources/>\n', 'README': 'base\n'}),
                               ('build/make', {'target/product/handheld_product.mk': '# product\n'})):
            for relative, text in files.items():
                write(self.source/project/relative, text)
            git('init', '-q', cwd=self.source/project)
            git('add', '-A', cwd=self.source/project)
            git('-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', 'base', cwd=self.source/project)
        write(self.candidate/'os/patches/frameworks-base.patch', '--- a/core/res/res/values/config.xml\n+++ b/core/res/res/values/config.xml\n')
        self.version = 'v1'
        write(self.candidate/'scripts/apply-os-changes.py', '''
            import os, subprocess, sys
            source, version = sys.argv[1], sys.argv[2]
            for project in ('frameworks/base', 'build/make'):
                if subprocess.run(['git', '-C', os.path.join(source, project), 'status', '--porcelain'], capture_output=True, text=True).stdout:
                    sys.exit('Local changes present in ' + project)
            open(os.path.join(source, 'frameworks/base/core/res/res/values/config.xml'), 'w').write('<resources candidate="%s"/>\\n' % version)
            os.makedirs(os.path.join(source, 'frameworks/base/overlay'), exist_ok=True)
            open(os.path.join(source, 'frameworks/base/overlay/Agent.apk'), 'w').write('agent ' + version)
            os.makedirs(os.path.join(source, 'packages/apps/RosePreinstall/M'), exist_ok=True)
            open(os.path.join(source, 'packages/apps/RosePreinstall/M/app.apk'), 'w').write('voice ' + version)''')
        self.receipts = root/'receipts'
        self.release = root/'release'
        self.release.mkdir()

    def run_reconcile(self, version, label, mode='apply', reason=None):
        return reconcile.reconcile(self.source, self.candidate, self.release, [version], self.receipts,
            ['frameworks/base', 'build/make'], ['packages/apps/RosePreinstall'], {}, label, mode, reason)

    def read(self, relative):
        return (self.source/relative).read_text()

    def test_pristine_tree_gets_the_candidate_state_and_rerun_is_idempotent(self):
        record = self.run_reconcile('v1', '2026102901')
        self.assertEqual(sorted(record['changed']), ['frameworks/base/core/res/res/values/config.xml',
            'frameworks/base/overlay/Agent.apk', 'packages/apps/RosePreinstall/M/app.apk'])
        self.assertEqual(self.read('frameworks/base/core/res/res/values/config.xml'), '<resources candidate="v1"/>\n')
        record = self.run_reconcile('v1', '2026102902')
        self.assertEqual(record['changed'], [])
        self.assertEqual(sorted(p.name for p in self.receipts.glob('*.json')), ['0001-2026102901.json', '0002-2026102902.json'])

    def test_task_owned_files_are_replaced_by_the_next_candidate_with_backups(self):
        self.run_reconcile('v1', '2026102901')
        record = self.run_reconcile('v2', '2026102902')
        self.assertEqual(len(record['changed']), 3)
        self.assertEqual(self.read('packages/apps/RosePreinstall/M/app.apk'), 'voice v2')
        backup = self.receipts/'0002-2026102902-backup/frameworks/base/core/res/res/values/config.xml'
        self.assertEqual(backup.read_text(), '<resources candidate="v1"/>\n')

    def test_owner_change_conflicts_and_nothing_is_written(self):
        write(self.source/'frameworks/base/core/res/res/values/config.xml', '<resources owner="edit"/>\n')
        write(self.source/'build/make/target/product/handheld_product.mk', '# owner change elsewhere\n')
        with self.assertRaisesRegex(reconcile.Conflict, 'core/res/res/values/config.xml'):
            self.run_reconcile('v1', '2026102901')
        self.assertEqual(self.read('frameworks/base/core/res/res/values/config.xml'), '<resources owner="edit"/>\n')
        self.assertFalse((self.source/'frameworks/base/overlay').exists())
        self.assertEqual(self.read('build/make/target/product/handheld_product.mk'), '# owner change elsewhere\n')
        self.assertFalse(list(self.receipts.glob('*.json')))

    def test_explicit_adoption_then_apply(self):
        write(self.source/'frameworks/base/core/res/res/values/config.xml', '<resources p1="hand made"/>\n')
        with self.assertRaises(reconcile.Conflict):
            self.run_reconcile('v1', 'adopt', mode='adopt')
        record = self.run_reconcile('v1', 'adopt-p1', mode='adopt', reason='hand-made P1 integration 2026102801')
        self.assertEqual(record['adopted'], ['frameworks/base/core/res/res/values/config.xml'])
        self.assertEqual(self.read('frameworks/base/core/res/res/values/config.xml'), '<resources p1="hand made"/>\n')
        self.run_reconcile('v1', '2026102901')
        self.assertEqual(self.read('frameworks/base/core/res/res/values/config.xml'), '<resources candidate="v1"/>\n')

    def test_retained_repositories_are_only_read(self):
        before = {p: subprocess.run(['git', '-C', str(self.source/p), 'worktree', 'list'], capture_output=True, text=True).stdout
            for p in ('frameworks/base', 'build/make')}
        self.run_reconcile('v1', '2026102901')
        for project, listing in before.items():
            self.assertEqual(subprocess.run(['git', '-C', str(self.source/project), 'worktree', 'list'], capture_output=True,
                text=True).stdout, listing)
        self.assertFalse((self.release/'reconcile-shadow').exists())

if __name__ == '__main__':
    unittest.main()
