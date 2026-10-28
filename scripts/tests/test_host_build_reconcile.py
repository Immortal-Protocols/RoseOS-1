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
            self.PROJECT])
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

if __name__ == '__main__':
    unittest.main()
