"""#156: Rose Agent's work row in Launcher3's fallback overview. Applies os/patches/rose-switcher-launcher3.patch to
the pinned fallback layout (Launcher3 47accce), runs the pure RoseAgentWork decisions, and checks the view's
boundaries. Source and fixture logic only: no Launcher3 build, emulator or device."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT/'os/tests/fixtures/rose-switcher'
PATCH = ROOT/'os/patches/rose-switcher-launcher3.patch'
OVERLAY = ROOT/'os/launcher3-switcher'  # staged: installs to quickstep/src/com/android/quickstep/fallback/ (README)
LAYOUT = 'quickstep/res/layout/fallback_recents_activity.xml'
PINNED = {'fallback_recents_activity.xml': '8df1879fb83e2a9f5b11b657a498e6153504171cb702f859d136f37ab968a9f7',
    'OverviewComponentObserver.java': 'a94703d973e14ba8e1f43ab07d7eacaa0e32f8b5668d16bff1c6843dd4877893'}

class RoseSwitcherTest(unittest.TestCase):
    def test_fixtures_are_the_pinned_files_and_rose_home_gets_the_fallback_overview(self):
        for name, digest in PINNED.items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(), digest, name)
        observer = (FIXTURES/'OverviewComponentObserver.java').read_text()
        self.assertIn('// The default home app is a different launcher. Use the fallback Overview instead.', observer)
        self.assertIn('mOverviewIntent = mFallbackIntent;', observer)

    def test_the_patch_applies_cleanly_and_adds_only_the_agent_row(self):
        tree = Path(tempfile.mkdtemp(prefix='launcher3-'))
        self.addCleanup(shutil.rmtree, tree, True)
        (tree/LAYOUT).parent.mkdir(parents=True)
        shutil.copyfile(FIXTURES/'fallback_recents_activity.xml', tree/LAYOUT)
        subprocess.run(['git', 'init', '-q', str(tree)], check=True)
        check = subprocess.run(['git', '-C', str(tree), 'apply', '--check', str(PATCH)], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        subprocess.run(['git', '-C', str(tree), 'apply', str(PATCH)], check=True)
        layout = (tree/LAYOUT).read_text()
        self.assertIn('<com.android.quickstep.fallback.RoseAgentStrip', layout)
        self.assertLess(layout.index('android:id="@id/overview_panel"'), layout.index('RoseAgentStrip'),
            'the human carousel stays; the agent row is added beside it')
        removed = [l for l in PATCH.read_text().splitlines() if l.startswith('-') and not l.startswith('---')]
        self.assertEqual(removed, [], 'nothing of the stock overview is removed')

    def test_agent_work_decisions(self):
        out = Path(tempfile.mkdtemp(prefix='rose-agent-work-'))
        self.addCleanup(shutil.rmtree, out, True)
        subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '11', '-target', '11',
            '-Xlint:-options', '-d', str(out), str(OVERLAY/'RoseAgentWork.java'), str(ROOT/'os/tests/RoseAgentWorkTest.java')],
            check=True)
        result = subprocess.run(['java', '-cp', str(out), 'com.android.quickstep.fallback.RoseAgentWorkTest'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_view_uses_only_existing_authority_and_opens_only_the_current_work(self):
        strip = (OVERLAY/'RoseAgentStrip.java').read_text()
        for banned in ('AgentProvider', 'getContentResolver', 'content://', 'requestPermissions', 'grantRuntimePermission',
                'checkSignatures', 'startActivityFromRecents', 'moveTaskToFront', 'dev.pixel.voice'):
            self.assertNotIn(banned, strip, banned)
        self.assertIn('ActivityTaskManager.getInstance().getTasks(64)', strip)
        self.assertIn('d.getOwnerUid()', strip)
        select = strip[strip.index('private void select('):strip.index('private TextView text(')]
        self.assertLess(select.index('RoseAgentWork.open(task.taskId, work)'), select.index('startActivityAsUser'))
        self.assertIn('if (notice != null) {', select)
        self.assertIn('UserHandle.of(task.userId)', select)

    def test_the_layout_patch_is_not_registered_until_the_contract_change_is_coordinated(self):
        # Registering it adds packages/apps/Launcher3 to the Pixel 10 contract's Rose projects, which rebinds the
        # retained Blazer EXEC002 receipt (scripts/tests/test_blazer_preparation_preflight.py, Infra). Until Infra
        # takes that change, the patch is ready but apply-os-changes.py does not apply it.
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        self.assertNotIn('rose-switcher-launcher3.patch', patcher)

if __name__ == '__main__':
    unittest.main()
