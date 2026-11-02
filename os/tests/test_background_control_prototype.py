"""General background app control: the platform side of the signer-pinned prototype agent.

Applies the background-control patch sequence (scripts/background_apks.py BACKGROUND_FRAMEWORK_PATCHES) to the pinned
ComputerControl sources (GrapheneOS platform_frameworks_base 84183aff) and checks that the one supported control-any-app
mechanism lifts only the per-target gates: it is bound to the exact pinned controller signer, and every session keeps
its user, locked owner-gate, secure-window, Stop, app-op and agent-allowlist boundaries. Source and fixture logic only:
no framework build, emulator or device, and not phone proof."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from background_apks import BACKGROUND_FRAMEWORK_PATCHES  # noqa: E402

FIXTURES = ROOT/'os/tests/fixtures/background-control'
SOURCE = 'services/companion/java/com/android/server/companion/virtual/computercontrol'
PINNED = {
    'ComputerControlAllowlistController.java': 'f96c32b111b4021187200adc2ec1944bcdbf7a913325b8aa92e4702c67f4a79b',
    'ComputerControlSessionImpl.java': '66447d84e061d2a0304f51f9e06fa45032db3f2a1e5ac035689cb4522fd0f735',
    'ComputerControlSessionProcessor.java': 'b0ace1a81f4c49dbcfb724d16951ac717684ab4a6ee97f6ea58ae7c850ebbf52',
}

def method(source, signature):
    """The body of the first method whose declaration contains signature, by brace matching."""
    start = source.index(signature)
    open_brace = source.index('{', start)
    depth = 0
    for i in range(open_brace, len(source)):
        depth += {'{': 1, '}': -1}.get(source[i], 0)
        if depth == 0:
            return source[start:i + 1]
    raise ValueError(signature)

class BackgroundControlPrototypeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = Path(tempfile.mkdtemp(prefix='computercontrol-'))
        (cls.tree/SOURCE).mkdir(parents=True)
        for name in PINNED:
            shutil.copyfile(FIXTURES/name, cls.tree/SOURCE/name)
        subprocess.run(['git', 'init', '-q', str(cls.tree)], check=True)
        cls.applied = []
        for patch in BACKGROUND_FRAMEWORK_PATCHES:
            # The automated-device snapshot hunk (#156) has its own fixtures: os/tests/test_automated_devices_contract.py.
            result = subprocess.run(['git', '-C', str(cls.tree), 'apply',
                f'--exclude={SOURCE}/AutomatedPackagesRepository.java', f'--include={SOURCE}/*',
                str(ROOT/'os/patches'/patch)], capture_output=True, text=True)
            cls.applied.append((patch, result.returncode, result.stderr))
        read = lambda name: (cls.tree/SOURCE/name).read_text()
        cls.session = read('ComputerControlSessionImpl.java')
        cls.allowlist = read('ComputerControlAllowlistController.java')
        cls.processor = read('ComputerControlSessionProcessor.java')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tree, True)

    def test_fixtures_are_pinned_and_the_patch_sequence_applies_cleanly(self):
        for name, digest in PINNED.items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(), digest, name)
        for patch, code, stderr in self.applied:
            self.assertEqual(code, 0, f'{patch}: {stderr}')

    def test_control_any_app_is_bound_to_the_exact_pinned_controller_signer(self):
        prototype = method(self.allowlist, 'boolean hasPrototypeControl(')
        self.assertIn('getSigningDetails(packageName, packageManager)', prototype)
        self.assertIn('if (signing == null) return false;', prototype)
        self.assertIn('R.array.config_pixelComputerControlPrototypeAgents', prototype)
        self.assertIn('packageSignatureMatches(packageName, signing, parse(pin), false)', prototype)
        # The pins ship empty; only the release configuration fills them with package:certificate pins.
        config = (ROOT/'os/patches/background-control-modern-frameworks-base.patch').read_text()
        self.assertIn('+    <string-array name="config_pixelComputerControlPrototypeAgents" translatable="false" />',
            config)
        # The session's own check is the owner package, judged by its own user's package manager.
        self.assertIn('return mAllowlistController.hasPrototypeControl(mRequest.ownerPackageName(),\n'
            '                mRequest.ownerPackageManager());', method(self.session, 'private boolean isPrototypeSession('))

    def test_only_the_per_target_gates_are_lifted(self):
        automatable = method(self.allowlist, 'boolean isPackageAutomatable(@Nullable String targetPackage,\n'
            '            @Nullable String agentPackage')
        self.assertLess(automatable.index('if (targetPackage == null || agentPackage == null)'),
            automatable.index('hasPrototypeControl(agentPackage, packageManager)'))
        consent = method(self.allowlist, 'boolean doesAgentHaveConsentToAutomateTargetApp(')
        self.assertLess(consent.index('if (targetPackage == null || agentPackageName == null)'),
            consent.index('hasPrototypeControl(agentPackageName,'))
        # Every session-side bypass goes through the signer-bound check; no package name grants it.
        bypasses = re.findall(r'isPrototypeSession\(\)', self.session)
        self.assertEqual(len(bypasses), 6,
            'declaration plus launch, assist, launch-allowed, top-activity and the multi-session admission accessor')
        # The accessor only reports the owner's status to the processor's admission rule; it lifts no gate.
        self.assertIn('boolean isPrototypeOwner() {\n        return isPrototypeSession();\n    }', self.session)
        self.assertNotIn('dev.pixel', self.session + self.allowlist + self.processor)

    def test_user_and_locked_owner_gates_hold_for_every_prototype_path(self):
        allowed = method(self.session, 'private boolean isActivityLaunchAllowed(')
        gate = allowed.index('isOwnerGatedWhileLocked(componentName.getPackageName())')
        user = allowed.index('return userId == UserHandle.USER_SYSTEM || userId == mRequest.ownerUserId();')
        self.assertLess(allowed.index('isPrototypeSession()'), gate)
        self.assertLess(gate, user)

        launch = method(self.session, 'public void launchApplication(')
        self.assertLess(launch.index('mAllowlistController.isPackageAutomatable(packageName, this)'),
            launch.index('isPrototypeSession()'))
        self.assertIn('if (isOwnerGatedWhileLocked(packageName)) {', launch)

        assist = method(self.session, 'private void refreshVisibleTaskForScreenshot(')
        self.assertIn('&& isActivityLaunchAllowed(top.getComponentName(), top.getUserId())', assist)

        # A prototype session trusts a new top task (input and screenshots) only for a user and screen it may act on:
        # wrong user, or an owner-gated screen while locked, is never added to the allowed tasks.
        top = method(self.session, 'public void onTopActivityChanged(int displayId, @NonNull ComponentName topActivity,\n'
            '                @UserIdInt int userId)')
        self.assertIn('final boolean prototypeAllowed = isPrototypeSession()\n'
            '                    && isActivityLaunchAllowed(topActivity, userId);', top)
        self.assertLess(top.index('prototypeAllowed = '), top.index('synchronized (mAllowedTaskIds)'),
            'evaluated before taking the task lock, so mAllowlistedPackages is never nested inside it')
        locked = top[top.index('synchronized (mAllowedTaskIds)'):]
        self.assertIn('boolean isPackageAllowed = prototypeAllowed\n', locked)
        self.assertNotIn('isPrototypeSession()', locked)

    def test_secure_window_stop_app_op_and_agent_allowlist_are_untouched(self):
        removed = []
        for patch in BACKGROUND_FRAMEWORK_PATCHES:
            text = (ROOT/'os/patches'/patch).read_text()
            removed += [l for l in text.splitlines() if l.startswith('-') and not l.startswith('---')]
        for token in ('mSecureWindowPackage', 'onSecureWindowShown', 'public void close()',
                'closeSessionByUserIntent', 'OP_COMPUTER_CONTROL', 'shouldDisallowInteractions("',
                'mSessionOwnerAllowlist'):
            self.assertFalse([l for l in removed if token in l], token)
        pinned = {name: (FIXTURES/name).read_text() for name in PINNED}
        for name, token in (('ComputerControlSessionImpl.java', 'config.mSecureWindowPackage ='),
                ('ComputerControlSessionImpl.java', 'public void close() throws RemoteException'),
                ('ComputerControlSessionProcessor.java', 'public void closeSessionByUserIntent(int deviceId)'),
                ('ComputerControlSessionProcessor.java', 'AppOpsManager.OP_COMPUTER_CONTROL')):
            patched = {'ComputerControlSessionImpl.java': self.session,
                'ComputerControlSessionProcessor.java': self.processor}[name]
            self.assertEqual(patched.count(token), pinned[name].count(token), token)
            self.assertGreater(patched.count(token), 0, token)
        # The pinned agent allowlist still decides who may open a session; prototype pins never feed it.
        self.assertIn('R.array.config_pixelComputerControlAgents)));', self.allowlist)
        self.assertNotIn('config_pixelComputerControlPrototypeAgents)));', self.allowlist)
        # Locked sessions remain a separate, default-off opt-in.
        locked = method(self.allowlist, 'boolean isLockedSessionAllowed(')
        self.assertLess(locked.index('config_pixelComputerControlLockedSessions'),
            locked.index('hasPrototypeControl(packageName, packageManager)'))

if __name__ == '__main__':
    unittest.main()
