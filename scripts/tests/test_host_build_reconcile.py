"""host-app-build.py and host-reconcile.py with fake candidates and git source trees: public asset staging and
digest checks, output capture by before/after sets, and receipt-based reconciliation that never overwrites
owner changes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import re
import textwrap
import unittest
import unittest.mock

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

    def test_voice_only_build_never_runs_the_calculator_or_controller(self):
        app_build.stage(self.source, self.assets, self.candidate)
        for path in ('workspace/pixel-calculator-agent/build.sh', 'scripts/build-background-agent.py'):
            write(self.candidate/path, 'exit 9\nraise SystemExit(9)\n' if path.endswith('.py') else 'exit 9\n')
        hashes = app_build.build(self.candidate, self.release, self.sdk, self.root/'jdk', self.assets, run=self.run_fake, voice_only=True)
        self.assertEqual(set(hashes), {'voice'})
        self.assertEqual(sorted(p.relative_to(self.release).as_posix() for p in self.release.rglob('*')), ['unsigned', 'unsigned/voice.apk'])
        self.assertEqual((self.release/'unsigned/voice.apk').read_text().strip(), 'voice')

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

class ZxingSetupTest(unittest.TestCase):
    """PR288: a fresh candidate has no .tools/zxing; its build.sh requires the jar its own pinned setup-zxing.sh fetches."""
    JAR = '.tools/zxing/core-3.5.4.jar'

    run_fake = AppBuildTest.run_fake

    def setUp(self):
        AppBuildTest.setUp(self)
        write(self.candidate/'workspace/rose-agent/build.sh', f'''
            set -e; test -f {self.JAR} || {{ echo "Missing build input: {self.JAR}" >&2; exit 1; }}
            d=$(mktemp -d out/rose-agent.XXXXXX); echo voice > "$d/rose-agent-unsigned.apk"''')
        app_build.stage(self.source, self.assets, self.candidate)
        self.order = []

    def run_logged(self, argv, cwd, env, check):
        self.order.append(argv[1] if argv[0] == 'bash' else argv[0])
        return self.run_fake(argv, cwd, env, check)

    def test_fresh_candidate_without_a_jar_runs_its_setup_before_the_app_build(self):
        self.assertFalse((self.candidate/'.tools').exists())
        write(self.candidate/app_build.ZXING_SETUP, f'''
            set -e; cd "$(dirname "$0")/.."; mkdir -p .tools/zxing; echo pinned > {self.JAR}''')
        hashes = app_build.build(self.candidate, self.release, self.sdk, self.root/'jdk', self.assets, run=self.run_logged, voice_only=True)
        self.assertEqual(set(hashes), {'voice'})
        self.assertEqual(self.order, ['npm', app_build.ZXING_SETUP, 'workspace/rose-agent/build.sh'])
        self.assertTrue((self.candidate/self.JAR).is_file())

    def test_the_real_pinned_setup_refuses_bytes_that_are_not_the_pinned_jar(self):
        shutil.copy(SCRIPTS/'setup-zxing.sh', self.candidate/app_build.ZXING_SETUP)
        bin_dir = self.root/'bin'
        write(bin_dir/'curl', '#!/bin/sh\nwhile [ "$1" != --output ]; do shift; done; echo not-zxing > "$2"\n', 0o755)
        with unittest.mock.patch.dict(os.environ, {'PATH': f'{bin_dir}:' + os.environ['PATH']}):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                app_build.build(self.candidate, self.release, self.sdk, self.root/'jdk', self.assets, run=self.run_logged)
        self.assertEqual(raised.exception.cmd, ['bash', app_build.ZXING_SETUP])
        self.assertNotIn('workspace/rose-agent/build.sh', self.order)
        self.assertFalse((self.candidate/self.JAR).exists())
        self.assertFalse((self.release/'unsigned').exists())

    def test_a_candidate_without_the_setup_script_builds_as_before(self):
        self.assertFalse((self.candidate/app_build.ZXING_SETUP).exists())
        write(self.candidate/self.JAR, 'preinstalled')
        app_build.build(self.candidate, self.release, self.sdk, self.root/'jdk', self.assets, run=self.run_logged, voice_only=True)
        self.assertEqual(self.order, ['npm', 'workspace/rose-agent/build.sh'])

    def test_the_wired_script_is_the_hash_pinned_one_the_candidate_build_checks(self):
        setup, build_sh = (SCRIPTS/'setup-zxing.sh').read_text(), (SCRIPTS.parent/'workspace/rose-agent/build.sh').read_text()
        pin = re.search(r'^SHA256=([0-9a-f]{64})$', setup, re.M)[1]
        self.assertIn(f'ZXING_SHA256={pin}', build_sh)
        self.assertIn('JAR="$DIR/core-3.5.4.jar"', setup)
        self.assertIn('ZXING="$ROOT/.tools/zxing/core-3.5.4.jar"', build_sh)

class SdkLayoutTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = self.source = self.root/'R'
        for tool in ('aapt', 'aapt2', 'zipalign'):
            write(source/'prebuilts/sdk/tools/linux/bin'/tool, '#!/bin/sh\nexit 0\n', 0o755)
        # Like the prebuilt launcher: resolve symlinks to itself, then need lib/d8.jar or ../framework/d8.jar there.
        write(source/'prebuilts/sdk/tools/linux/bin/d8', '#!/bin/sh\nprog="$0"\nwhile [ -h "$prog" ]; do prog=$(readlink "$prog"); done\n'
            'dir=$(dirname "$prog")\nfor jar in "$dir/lib/d8.jar" "$dir/../framework/d8.jar"; do\n'
            '  [ -r "$jar" ] && { echo "D8 from $jar"; exit 0; }\ndone\necho "d8: can\'t find d8.jar" >&2; exit 1\n', 0o755)
        write(source/'prebuilts/sdk/tools/linux/lib/d8.jar', 'd8 jar')
        write(source/'prebuilts/sdk/tools/core-lambda-stubs.jar', 'stubs')
        write(source/'prebuilts/sdk/35/public/android.jar', 'android')
        write(source/'prebuilts/jdk/jdk21/linux-x86/bin/java', '#!/bin/sh\ntest "$1" = -jar && exit 0\n', 0o755)
        self.jar = write(self.root/'apksigner.jar', 'retained public apksigner jar')

    def test_layout_links_existing_tools_and_runs_each(self):
        receipt = sdk_layout.install(self.root/'sdk', self.source, self.jar)
        tools = self.root/'sdk/build-tools/android-15'
        self.assertTrue((tools/'aapt2').is_symlink() and (tools/'core-lambda-stubs.jar').is_symlink())
        # The copied launcher finds its JAR through the linked lib/d8.jar; the original launcher alone cannot.
        self.assertFalse((tools/'d8').is_symlink())
        self.assertEqual(os.readlink(tools/'lib/d8.jar'), str(self.source/'prebuilts/sdk/tools/linux/lib/d8.jar'))
        self.assertIn('lib/d8.jar', subprocess.run([str(tools/'d8'), '--version'], capture_output=True, text=True, check=True).stdout)
        self.assertNotEqual(subprocess.run([str(self.source/'prebuilts/sdk/tools/linux/bin/d8'), '--version'],
            capture_output=True).returncode, 0)
        os.symlink(self.source/'prebuilts/sdk/tools/linux/bin/d8', self.root/'linked-d8')
        self.assertNotEqual(subprocess.run([str(self.root/'linked-d8'), '--version'], capture_output=True).returncode, 0)
        self.assertEqual(receipt['d8Launcher']['copiedFrom'], str(self.source/'prebuilts/sdk/tools/linux/bin/d8'))
        self.assertIn('build-tools/android-15/lib/d8.jar', receipt['files'])
        self.assertEqual((tools/'lib/apksigner.jar').read_text(), 'retained public apksigner jar')
        self.assertTrue(os.access(tools/'apksigner', os.X_OK))
        self.assertIn('platforms/android-35/android.jar', receipt['files'])
        with self.assertRaisesRegex(SystemExit, 'already exists'):
            sdk_layout.install(self.root/'sdk', self.source, self.jar)

    def test_missing_existing_tool_installs_nothing(self):
        for name in ('core-lambda-stubs.jar', 'linux/lib/d8.jar'):
            (self.source/'prebuilts/sdk/tools'/name).unlink()
            with self.assertRaisesRegex(SystemExit, name):
                sdk_layout.install(self.root/'sdk', self.source, self.jar)
            self.assertFalse((self.root/'sdk').exists())

def git(*args, cwd):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


ROOT = SCRIPTS.parent
CC = 'services/companion/java/com/android/server/companion/virtual/computercontrol'
SESSION = f'frameworks/base/{CC}/ComputerControlSessionImpl.java'
# PR276 (831f21e) changed this one ComputerControlSessionImpl hunk of the modern patch; reversing it gives the
# 10-27 bundle the retained Frankel tree was patched with before host-reconcile existed (generation 18 and earlier).
PR276_NEW = """@@ -1462,9 +1477,13 @@
 
             // If this new activity belongs to a package the session is authorized to control,
             // we should trust it, even if it's a secondary task (like a new Chrome window).
+            // A prototype session trusts any package only for a user and screen it may act on.
+            final boolean prototypeAllowed = isPrototypeSession()
+                    && isActivityLaunchAllowed(topActivity, userId);
             synchronized (mAllowedTaskIds) {
-                boolean isPackageAllowed = mRequest.params().getTargetPackageNames()
-                        .contains(topActivity.getPackageName());
+                boolean isPackageAllowed = prototypeAllowed
"""
PR276_OLD = """@@ -1463,8 +1478,9 @@
             // If this new activity belongs to a package the session is authorized to control,
             // we should trust it, even if it's a secondary task (like a new Chrome window).
             synchronized (mAllowedTaskIds) {
-                boolean isPackageAllowed = mRequest.params().getTargetPackageNames()
-                        .contains(topActivity.getPackageName());
+                boolean isPackageAllowed = isPrototypeSession()
"""

class RetainedIntegrationOutputTest(unittest.TestCase):
    """Run 41174386535 (generation 19): the retained ComputerControlSessionImpl.java held the 10-27 patch output since
    before host-reconcile existed, so no receipt owned it; PR276's first change to it read as an owner change. Real
    pinned 84183aff sources (os/tests/fixtures/background-control) and the repository's own patches; nothing else."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.source, self.candidate, self.receipts = root/'R', root/'candidate', root/'receipts'
        self.release = root/'release'
        self.release.mkdir()
        base = self.source/'frameworks/base'
        for name in ('ComputerControlAllowlistController.java', 'ComputerControlSessionImpl.java',
                'ComputerControlSessionProcessor.java'):
            write(base/CC/name, (ROOT/'os/tests/fixtures/background-control'/name).read_text())
        git('init', '-q', cwd=base)
        git('add', '-A', cwd=base)
        git('-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', '84183aff', cwd=base)
        current = (ROOT/'os/patches/background-control-modern-frameworks-base.patch').read_text()
        self.assertEqual(current.count(PR276_NEW), 1, 'the fixture tracks the PR276 hunk')
        bundles = {'old': current.replace(PR276_NEW, PR276_OLD), 'new': current}
        names = [n for n in reconcile.FRAMEWORKS_BASE_SEQUENCE if (ROOT/'os/patches'/n).is_file()]
        # The candidate repository: the 10-27 bundle, then the current one (PR276), as two commits.
        git('init', '-q', str(self.candidate), cwd=root)
        self.commits = {}
        for label in ('old', 'new'):
            for name in names:
                text = bundles[label] if name == 'background-control-modern-frameworks-base.patch' \
                    else (ROOT/'os/patches'/name).read_text()
                write(self.candidate/'os/patches'/name, text)
            git('add', '-A', cwd=self.candidate)
            git('-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', label, cwd=self.candidate)
            self.commits[label] = subprocess.run(['git', '-C', str(self.candidate), 'rev-parse', 'HEAD'],
                capture_output=True, text=True).stdout.strip()
        # A patcher like the real one for these files: the candidate's own sequence, in order, on the shadow.
        write(self.candidate/'scripts/apply-os-changes.py', f'''
            import os, subprocess, sys
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            for name in {list(reconcile.FRAMEWORKS_BASE_SEQUENCE)!r}:
                patch = os.path.join(here, 'os/patches', name)
                if os.path.isfile(patch):
                    # The #156 device-snapshot and multi-session admission hunks touch files this fixture does not hold.
                    subprocess.run(['git', '-C', os.path.join(sys.argv[1], 'frameworks/base'), 'apply',
                        '--exclude={CC}/AutomatedPackagesRepository.java',
                        '--exclude={CC}/ComputerControlSessionAdmission.java', '--include={CC}/*', patch], check=True)''')
        # The retained tree: the 10-27 bundle applied by hand before reconcile; the only receipt does not name it.
        self.retained = self.patched(bundles['old'])
        (self.source/SESSION).write_bytes(self.retained)
        for name in ('ComputerControlAllowlistController.java', 'ComputerControlSessionProcessor.java'):
            (base/CC/name).write_bytes(self.patched(bundles['old'], name))
        self.receipts.mkdir()
        (self.receipts/'0001-2026103113.json').write_text(json.dumps({'receiptType': 'reconcile', 'entries': {}}))

    def patched(self, modern, name='ComputerControlSessionImpl.java'):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            write(tree/CC/name, (ROOT/'os/tests/fixtures/background-control'/name).read_text())
            git('init', '-q', cwd=tree)
            for patch in reconcile.FRAMEWORKS_BASE_SEQUENCE:
                path = ROOT/'os/patches'/patch
                if not path.is_file():
                    continue
                if patch == 'background-control-modern-frameworks-base.patch':
                    (tree.parent/f'{tree.name}.patch').write_text(modern)
                    path = tree.parent/f'{tree.name}.patch'
                git('apply', f'--include={CC}/{name}', str(path), cwd=tree)
            return (tree/CC/name).read_bytes()

    def run_reconcile(self):
        return reconcile.reconcile(self.source, self.candidate, self.release, [], self.receipts, ['frameworks/base'],
            [], {}, '2026103114', patcher='candidate')

    def test_the_generation_19_state_was_a_conflict_without_the_baseline(self):
        changes = {SESSION: (self.patched((ROOT/'os/patches/background-control-modern-frameworks-base.patch').read_text()),
            0o644)}
        actions, conflicts = reconcile.plan(self.source, ['frameworks/base'], [], changes, {'entries': {}})
        self.assertEqual((actions, conflicts), ({}, [SESSION]), 'the previous rule alone refuses it, as run 41174386535 did')

    def test_earlier_rose_patch_output_is_replaced_with_backup_and_named_baseline(self):
        record = self.run_reconcile()
        self.assertEqual(record['changed'], [SESSION])
        expected = self.patched((ROOT/'os/patches/background-control-modern-frameworks-base.patch').read_text())
        self.assertEqual((self.source/SESSION).read_bytes(), expected)
        self.assertIn(b'prototypeAllowed', expected)
        entry = record['entries'][SESSION]
        self.assertEqual((entry['before'], entry['reproducedFrom']),
            (hashlib.sha256(self.retained).hexdigest(), self.commits['old']))
        self.assertEqual((self.receipts/f'0002-2026103114-backup/{SESSION}').read_bytes(), self.retained)
        # The next candidate finds it task-owned through the receipt as usual.
        self.assertEqual(reconcile.reconcile(self.source, self.candidate, self.release, [], self.receipts,
            ['frameworks/base'], [], {}, '2026103115', patcher='candidate')['changed'], [])

    def test_a_genuine_owner_edit_of_that_file_still_conflicts_and_nothing_is_written(self):
        edited = self.retained.replace(b'synchronized (mAllowedTaskIds) {', b'synchronized (mAllowedTaskIds) { // owner', 1)
        self.assertNotEqual(edited, self.retained)
        (self.source/SESSION).write_bytes(edited)
        with self.assertRaisesRegex(reconcile.Conflict, 'ComputerControlSessionImpl.java'):
            self.run_reconcile()
        self.assertEqual((self.source/SESSION).read_bytes(), edited)
        self.assertEqual(sorted(p.name for p in self.receipts.iterdir()), ['0001-2026103113.json'])

    def test_the_baseline_is_bounded_to_frameworks_base_rose_patches(self):
        found = reconcile.historical_outputs(self.source, self.candidate, 'frameworks/base', SESSION[len('frameworks/base/'):])
        self.assertEqual(found.get(hashlib.sha256(self.retained).hexdigest()), self.commits['old'])
        self.assertEqual(reconcile.historical_outputs(self.source, self.candidate, 'build/make', 'x'), {})
        self.assertEqual(reconcile.historical_outputs(self.source, self.candidate, 'frameworks/base', 'absent.java'), {})
        self.assertEqual(reconcile.historical_outputs(self.source, self.candidate, 'frameworks/base',
            SESSION[len('frameworks/base/'):], limit=0), {})

    def test_the_sequence_matches_the_patcher(self):
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        self.assertIn("projects = {'frameworks/base': ['frameworks-base.patch', 'rose-agent-self-update-frameworks-base.patch']}",
            patcher)
        self.assertIn("projects['frameworks/base'] += BACKGROUND_FRAMEWORK_PATCHES", patcher)
        sys.path.insert(0, str(ROOT/'scripts'))
        from background_apks import BACKGROUND_FRAMEWORK_PATCHES
        self.assertEqual(reconcile.FRAMEWORKS_BASE_SEQUENCE,
            ('frameworks-base.patch', 'rose-agent-self-update-frameworks-base.patch', *BACKGROUND_FRAMEWORK_PATCHES))

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

    def test_adoption_can_be_limited_to_named_conflicts(self):
        write(self.source/'frameworks/base/core/res/res/values/config.xml', '<resources p1="hand made"/>\n')
        write(self.source/'packages/apps/RosePreinstall/M/app.apk', 'voice p1')
        def adopt(paths):
            return reconcile.reconcile(self.source, self.candidate, self.release, ['v1'], self.receipts,
                ['frameworks/base', 'build/make'], [], {}, 'adopt', 'adopt', 'order-only difference', only=paths)
        with self.assertRaisesRegex(reconcile.Conflict, 'not current conflicts, nothing adopted: build/make/README'):
            adopt(['frameworks/base/core/res/res/values/config.xml', 'build/make/README'])
        self.assertFalse(list(self.receipts.glob('*.json')))
        record = adopt(['frameworks/base/core/res/res/values/config.xml'])
        self.assertEqual(record['adopted'], ['frameworks/base/core/res/res/values/config.xml'])
        with self.assertRaisesRegex(reconcile.Conflict, 'RosePreinstall/M/app.apk'):  # still an owner change
            reconcile.reconcile(self.source, self.candidate, self.release, ['v1'], self.receipts,
                ['frameworks/base', 'build/make'], [], {}, 'apply')

    def test_trusted_patcher_runs_against_the_candidate_bundle(self):
        write(self.candidate/'os/pinned-manifest.xml', '<manifest/>\n')
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            script = Path(argv[1])
            # The copied patcher reads os/ relative to itself: that must be the candidate's bundle.
            self.assertEqual((script.resolve().parents[1]/'os').resolve(), (self.candidate/'os').resolve())
            self.assertEqual(script.read_bytes(), (SCRIPTS/'apply-os-changes.py').read_bytes())
            checked = subprocess.run([sys.executable, str(script), str(self.root/'missing'), '--installed-identities', 'x'],
                capture_output=True, text=True)
            self.assertIn('--installed-identities needs --background-policy', checked.stderr)  # its imports resolve
            return subprocess.CompletedProcess(argv, 0, '', '')
        record = reconcile.reconcile(self.source, self.candidate, self.release, ['v1'], self.receipts,
            ['frameworks/base', 'build/make'], ['packages/apps/RosePreinstall'], {}, '2026102802', run=run, patcher='trusted')
        self.assertEqual(len(calls), 1)
        self.assertEqual(record['changed'], [])
        self.assertEqual(record['patcher']['patcher'], 'trusted')
        self.assertEqual(record['patcher']['sha256']['apply-os-changes.py'],
            hashlib.sha256((SCRIPTS/'apply-os-changes.py').read_bytes()).hexdigest())
        self.assertFalse((self.release/'reconcile-patcher').exists())
        self.assertEqual(self.run_reconcile('v1', '2026102803')['patcher']['patcher'], 'candidate')
        with self.assertRaisesRegex(reconcile.Conflict, 'unknown patcher'):
            reconcile.reconcile(self.source, self.candidate, self.release, ['v1'], self.receipts,
                ['frameworks/base'], [], {}, 'x', patcher='other')

    def test_retained_repositories_are_only_read(self):
        before = {p: subprocess.run(['git', '-C', str(self.source/p), 'worktree', 'list'], capture_output=True, text=True).stdout
            for p in ('frameworks/base', 'build/make')}
        self.run_reconcile('v1', '2026102901')
        for project, listing in before.items():
            self.assertEqual(subprocess.run(['git', '-C', str(self.source/project), 'worktree', 'list'], capture_output=True,
                text=True).stdout, listing)
        self.assertFalse((self.release/'reconcile-shadow').exists())

class PermissionReconcileTest(unittest.TestCase):
    """The checked-in reconcile inputs carry OS #70's Permission RoleService patch: a sparse shared clone of
    packages/modules/Permission holding the pinned RoleService.java (os/tests/fixtures/rose-home-role, 8bc87856...) and the
    real os/patches/rose-home-role-permission.patch, applied by a stand-in patcher that does what apply-os-changes.py does
    for this project (refuse a missing or dirty project, git apply the patch). The real patcher also pins the upstream
    revision, which a synthetic repository cannot have."""
    PROJECT = 'packages/modules/Permission'
    FILE = 'service/java/com/android/role/RoleService.java'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = self.root = Path(temp.name)
        self.config = json.loads((SCRIPTS.parent/'os/release-pipeline/frankel.json').read_text())['host']['integrate']['reconcile']
        self.source, self.candidate = root/'R', root/'candidate'
        project = self.source/self.PROJECT
        (project/self.FILE).parent.mkdir(parents=True)
        shutil.copyfile(SCRIPTS.parent/'os/tests/fixtures/rose-home-role/RoleService.java', project/self.FILE)
        write(project/'Android.bp', 'apex { name: "com.android.permission" }\n')
        write(project/'PermissionController/src/Unrelated.java', 'class Unrelated {}\n')
        git('init', '-q', cwd=project)
        git('add', '-A', cwd=project)
        git('-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', 'pinned', cwd=project)
        (self.candidate/'os/patches').mkdir(parents=True)
        shutil.copyfile(SCRIPTS.parent/'os/patches/rose-home-role-permission.patch', self.candidate/'os/patches/rose-home-role-permission.patch')
        write(self.candidate/'scripts/apply-os-changes.py', f'''
            import json, os, subprocess, sys
            dest = os.path.join(sys.argv[1], {self.PROJECT!r})
            if not os.path.isdir(dest) or subprocess.run(['git', '-C', dest, 'status', '--porcelain'], capture_output=True, text=True).stdout:
                sys.exit('missing or dirty ' + dest)
            listing = sorted(os.path.relpath(os.path.join(d, f), dest) for d, _, fs in os.walk(dest) if '.git' not in d for f in fs)
            json.dump(listing, open({str(root/'shadow-files.json')!r}, 'w'))
            subprocess.run(['git', '-C', dest, 'apply', os.path.abspath('os/patches/rose-home-role-permission.patch')], check=True)''')
        self.receipts, self.release = root/'receipts', root/'release'
        self.release.mkdir()
        patched = root/'patched'
        (patched/self.FILE).parent.mkdir(parents=True)
        shutil.copyfile(project/self.FILE, patched/self.FILE)
        git('init', '-q', cwd=patched)
        git('apply', str(self.candidate/'os/patches/rose-home-role-permission.patch'), cwd=patched)
        self.patched = (patched/self.FILE).read_bytes()

    def run_reconcile(self, label, projects=None):
        return reconcile.reconcile(self.source, self.candidate, self.release, [], self.receipts,
            projects or [self.PROJECT], self.config['integrationOwnedRoots'], self.config['sparsePaths'], label)

    def test_checked_in_reconcile_inputs_include_the_permission_project(self):
        self.assertEqual(self.config['projects'], ['frameworks/base', 'build/make', 'build/release', 'packages/apps/Updater',
            self.PROJECT, 'packages/apps/Launcher3'])  # Launcher3: the #156 Rose Agent work-row patch
        self.assertEqual(self.config['sparsePaths'][self.PROJECT], [self.FILE])
        self.assertEqual(self.config['integrationOwnedRoots'], ['packages/apps/PixelBackgroundControl', 'packages/apps/RosePreinstall'])
        self.assertIn(self.FILE, reconcile.sparse_paths(SCRIPTS.parent, self.PROJECT, self.config['sparsePaths']))

    def test_sparse_clone_installs_only_the_patched_role_service_and_rerun_is_idempotent(self):
        before = (self.source/self.PROJECT/self.FILE).read_bytes()
        self.assertEqual(hashlib.sha256(before).hexdigest(), '8bc87856050126743959e24467f7b27a7258d95e9f216d0a26b06d0e8f1b3415')
        record = self.run_reconcile('2026102805')
        self.assertEqual(record['changed'], [f'{self.PROJECT}/{self.FILE}'])
        self.assertEqual(json.loads((self.root/'shadow-files.json').read_text()), [self.FILE])  # sparse: nothing else checked out
        self.assertEqual((self.source/self.PROJECT/self.FILE).read_bytes(), self.patched)
        entry = record['entries'][f'{self.PROJECT}/{self.FILE}']
        self.assertEqual((entry['before'], entry['after'], entry['action']),
            (hashlib.sha256(before).hexdigest(), hashlib.sha256(self.patched).hexdigest(), 'installed'))
        self.assertEqual((self.receipts/f'0001-2026102805-backup/{self.PROJECT}/{self.FILE}').read_bytes(), before)
        self.assertEqual((self.source/self.PROJECT/'PermissionController/src/Unrelated.java').read_text(), 'class Unrelated {}\n')
        self.assertEqual(self.run_reconcile('2026102805-rerun')['changed'], [])
        self.assertEqual((self.source/self.PROJECT/self.FILE).read_bytes(), self.patched)

    def test_without_the_project_the_patcher_fails_closed(self):
        (self.source/'build/make').mkdir(parents=True)
        git('init', '-q', cwd=self.source/'build/make')
        git('-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'b', cwd=self.source/'build/make')
        with self.assertRaisesRegex(reconcile.Conflict, 'candidate apply-os-changes failed on the shadow tree: missing or dirty'):
            self.run_reconcile('2026102805', projects=['build/make'])
        self.assertFalse(list(self.receipts.glob('*.json')))
        self.assertEqual(hashlib.sha256((self.source/self.PROJECT/self.FILE).read_bytes()).hexdigest(),
            '8bc87856050126743959e24467f7b27a7258d95e9f216d0a26b06d0e8f1b3415')

    def test_owner_change_to_role_service_conflicts_and_nothing_is_written(self):
        target = self.source/self.PROJECT/self.FILE
        target.write_bytes(target.read_bytes() + b'// owner edit\n')
        with self.assertRaisesRegex(reconcile.Conflict, f'{self.PROJECT}/{self.FILE}'):
            self.run_reconcile('2026102805')
        self.assertTrue(target.read_bytes().endswith(b'// owner edit\n'))
        self.assertFalse(list(self.receipts.glob('*.json')))

class TrayOverlayReconcileTest(unittest.TestCase):
    """The checked-in tray overlay (OS #85) reaches frameworks/base through the sparse shadow: the stand-in patcher copies
    os/overlays recursively and refuses a differing existing target, as apply-os-changes.py does. The previous candidate's
    overlay (2026102805 source 7fb94ae6, before AgentTrayLink; fixtures/tray-2026102805) is installed first, so AgentTrayPrototype is task-owned and replaced while the
    new AgentTrayLink is installed where nothing was; untracked files outside the sparse paths still reach the receipt."""
    PROJECT = 'frameworks/base'
    SHELL = 'libs/WindowManager/Shell/src/com/android/wm/shell'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = self.root = Path(temp.name)
        self.config = json.loads((SCRIPTS.parent/'os/release-pipeline/frankel.json').read_text())['host']['integrate']['reconcile']
        self.source, self.receipts, self.release = root/'R', root/'receipts', root/'release'
        self.release.mkdir()
        write(self.source/self.PROJECT/'core/res/res/values/config.xml', '<resources/>\n')
        write(self.source/self.PROJECT/'libs/WindowManager/Shell/Android.bp', 'filegroup { name: "wm_shell-sources", srcs: ["src/**/*.java"] }\n')
        git('init', '-q', cwd=self.source/self.PROJECT)
        git('add', '-A', cwd=self.source/self.PROJECT)
        git('-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', 'pinned', cwd=self.source/self.PROJECT)
        self.overlay = SCRIPTS.parent/'os/overlays'/self.PROJECT/self.SHELL
        self.current = {f.name: f.read_bytes() for f in self.overlay.iterdir()}
        previous = SCRIPTS/'tests/fixtures/tray-2026102805/AgentTrayPrototype.java'
        self.assertEqual(hashlib.sha256(previous.read_bytes()).hexdigest(), '2e3e86cb93f17aa2e3c56a4f0a004d19a8e37cf5fec24efb777f0b7df5e38c21')
        self.previous = {**{n: d for n, d in self.current.items() if n != 'AgentTrayLink.java'}, previous.name: previous.read_bytes()}

    def candidate(self, name, files):
        candidate = self.root/name
        for file, data in files.items():
            (candidate/'os/overlays'/self.PROJECT/self.SHELL).mkdir(parents=True, exist_ok=True)
            (candidate/'os/overlays'/self.PROJECT/self.SHELL/file).write_bytes(data)
        write(candidate/'scripts/apply-os-changes.py', '''
            import os, pathlib, sys
            source, bundle = pathlib.Path(sys.argv[1]), pathlib.Path(__file__).resolve().parents[1]/'os/overlays'
            for file in bundle.rglob('*'):
                if not file.is_file():
                    continue
                target = source/file.relative_to(bundle)
                if target.exists() and target.read_bytes() != file.read_bytes():
                    sys.exit(f'Unexpected existing overlay target: {target}')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(file.read_bytes())''')
        return candidate

    def run_reconcile(self, candidate, label):
        return reconcile.reconcile(self.source, candidate, self.release, [], self.receipts, [self.PROJECT],
            self.config['integrationOwnedRoots'], self.config['sparsePaths'], label)

    def test_agent_tray_link_and_updated_prototype_enter_the_receipt_and_source_tree(self):
        self.assertIn('AgentTrayLink.java', self.current)
        self.assertNotIn('AgentTrayLink.java', self.previous)
        self.assertNotEqual(self.previous['AgentTrayPrototype.java'], self.current['AgentTrayPrototype.java'])
        self.assertNotIn(self.SHELL, ' '.join(self.config['sparsePaths'][self.PROJECT]))  # reached as untracked files, not via sparse paths
        self.run_reconcile(self.candidate('previous', self.previous), '2026102805')
        record = self.run_reconcile(self.candidate('current', self.current), '2026102806')
        link, prototype = (f'{self.PROJECT}/{self.SHELL}/{n}' for n in ('AgentTrayLink.java', 'AgentTrayPrototype.java'))
        self.assertEqual(sorted(record['changed']), [link, prototype])
        sha = lambda data: hashlib.sha256(data).hexdigest()
        self.assertEqual((record['entries'][link]['before'], record['entries'][link]['after'], record['entries'][link]['action']),
            (None, sha(self.current['AgentTrayLink.java']), 'installed'))
        self.assertEqual((record['entries'][prototype]['before'], record['entries'][prototype]['after']),
            (sha(self.previous['AgentTrayPrototype.java']), sha(self.current['AgentTrayPrototype.java'])))
        for name, data in self.current.items():
            self.assertEqual((self.source/self.PROJECT/self.SHELL/name).read_bytes(), data)
        backup = self.receipts/f'0002-2026102806-backup/{prototype}'
        self.assertEqual(backup.read_bytes(), self.previous['AgentTrayPrototype.java'])
        self.assertEqual(self.run_reconcile(self.candidate('current', self.current), '2026102806-rerun')['changed'], [])

    def test_owner_edit_to_the_tray_prototype_conflicts_and_the_link_is_not_installed(self):
        self.run_reconcile(self.candidate('previous', self.previous), '2026102805')
        target = self.source/self.PROJECT/self.SHELL/'AgentTrayPrototype.java'
        target.write_bytes(target.read_bytes() + b'// owner edit\n')
        with self.assertRaisesRegex(reconcile.Conflict, 'AgentTrayPrototype.java'):
            self.run_reconcile(self.candidate('current', self.current), '2026102806')
        self.assertFalse((self.source/self.PROJECT/self.SHELL/'AgentTrayLink.java').exists())
        self.assertEqual(sorted(p.name for p in self.receipts.glob('*.json')), ['0001-2026102805.json'])

class RetainedOverlayLineageTest(unittest.TestCase):
    """AGENT-TRAY-RECONCILE-001 (run 41309344025, candidate 2026110101): an overlay file the retained tree has held since
    before any receipt, byte-identical to every candidate until one changes it, is in no receipt: adopt records only the
    conflicts of its time and apply records only files it changes. AgentStatusControls.java was unchanged from import
    (ff87296) to PR #320 (450009c), so the first candidate changing it read as an owner change. Bytes equal to an earlier
    version of the candidate's own os/overlays file at that path are Rose's output; anything else still conflicts."""
    PROJECT = 'frameworks/base'
    RELATIVE = 'libs/WindowManager/Shell/src/com/android/wm/shell/AgentStatusControls.java'
    OVERLAY = 'os/overlays/frameworks/base/' + RELATIVE

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = self.root = Path(temp.name)
        self.config = json.loads((SCRIPTS.parent/'os/release-pipeline/frankel.json').read_text())['host']['integrate']['reconcile']
        self.source, self.receipts, self.release = root/'R', root/'receipts', root/'release'
        self.release.mkdir()
        write(self.source/self.PROJECT/'core/res/res/values/config.xml', '<resources/>\n')
        git('init', '-q', cwd=self.source/self.PROJECT)
        git('add', '-A', cwd=self.source/self.PROJECT)
        git('-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', 'pinned', cwd=self.source/self.PROJECT)
        # The real lineage of the file: the import bytes, then PR #320's.
        show = lambda commit: subprocess.run(['git', '-C', str(ROOT), 'show', f'{commit}:{self.OVERLAY}'],
            capture_output=True, check=True).stdout
        self.imported, self.changed = show('ff87296'), show('450009c')
        self.assertEqual(hashlib.sha256(self.imported).hexdigest(), 'f5eed27e1491c99edb79807f550d8833d0394c5d080a97bec49ac6ad2be2927c')
        self.assertEqual(hashlib.sha256(self.changed).hexdigest(), '488c0041fa43df4f6fb1f1d4e4e007362edb7c7214885efafde1d2ed81422a9c')
        # The candidate repository: a stand-in patcher that copies os/overlays as apply-os-changes.py does, with the
        # overlay's history (import, then the change) in git, checked out at the changing commit.
        self.candidate = root/'candidate'
        write(self.candidate/'scripts/apply-os-changes.py', '''
            import pathlib, sys
            source, bundle = pathlib.Path(sys.argv[1]), pathlib.Path(__file__).resolve().parents[1]/'os/overlays'
            for file in bundle.rglob('*'):
                if file.is_file():
                    target = source/file.relative_to(bundle)
                    if target.exists() and target.read_bytes() != file.read_bytes():
                        sys.exit(f'Unexpected existing overlay target: {target}')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(file.read_bytes())''')
        commit = lambda message: git('-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', message,
            cwd=self.candidate)
        (self.candidate/self.OVERLAY).parent.mkdir(parents=True)
        (self.candidate/self.OVERLAY).write_bytes(self.imported)
        git('init', '-q', cwd=self.candidate)
        git('add', '-A', cwd=self.candidate)
        commit('import')
        self.import_commit = subprocess.run(['git', '-C', str(self.candidate), 'rev-parse', 'HEAD'],
            capture_output=True, text=True, check=True).stdout.strip()
        # The hand-made integration placed the import bytes before reconcile kept receipts.
        self.target = self.source/self.PROJECT/self.RELATIVE
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(self.imported)
        # Every candidate that still carries the import bytes leaves the file out of the receipt.
        self.assertEqual(self.run_reconcile('2026103101')['changed'], [])
        self.assertNotIn(f'{self.PROJECT}/{self.RELATIVE}', json.loads(next(self.receipts.glob('0001-*.json')).read_text())['entries'])
        (self.candidate/self.OVERLAY).write_bytes(self.changed)
        git('add', '-A', cwd=self.candidate)
        commit('PR #320')

    def run_reconcile(self, label):
        return reconcile.reconcile(self.source, self.candidate, self.release, [], self.receipts, [self.PROJECT],
            self.config['integrationOwnedRoots'], self.config['sparsePaths'], label)

    def test_unreceipted_earlier_overlay_bytes_are_replaced_with_their_lineage_recorded(self):
        record = self.run_reconcile('2026110101')
        path = f'{self.PROJECT}/{self.RELATIVE}'
        self.assertEqual(record['changed'], [path])
        sha = lambda data: hashlib.sha256(data).hexdigest()
        self.assertEqual((record['entries'][path]['before'], record['entries'][path]['after']), (sha(self.imported), sha(self.changed)))
        self.assertEqual(record['entries'][path]['reproducedFrom'], self.import_commit)
        self.assertEqual(self.target.read_bytes(), self.changed)
        self.assertEqual((self.receipts/f'0002-2026110101-backup/{path}').read_bytes(), self.imported)
        self.assertEqual(self.run_reconcile('2026110101-rerun')['changed'], [])

    def test_owner_edit_of_the_overlay_target_still_conflicts_and_nothing_is_written(self):
        self.target.write_bytes(self.imported + b'// owner edit\n')
        with self.assertRaisesRegex(reconcile.Conflict, 'owner changes conflict.*AgentStatusControls.java'):
            self.run_reconcile('2026110101')
        self.assertEqual(self.target.read_bytes(), self.imported + b'// owner edit\n')
        self.assertEqual(sorted(p.name for p in self.receipts.glob('*.json')), ['0001-2026103101.json'])

    def test_bytes_from_another_path_s_history_are_not_lineage(self):
        other = self.candidate/'os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/Other.java'
        other.write_bytes(b'// other overlay\n')
        git('add', '-A', cwd=self.candidate)
        git('-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', 'other', cwd=self.candidate)
        other.unlink()
        git('add', '-A', cwd=self.candidate)
        git('-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', 'drop other', cwd=self.candidate)
        self.target.write_bytes(b'// other overlay\n')
        with self.assertRaisesRegex(reconcile.Conflict, 'AgentStatusControls.java'):
            self.run_reconcile('2026110101')
        self.assertEqual(self.target.read_bytes(), b'// other overlay\n')

if __name__ == '__main__':
    unittest.main()
