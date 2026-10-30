"""Rose Agent may update itself as a system app (issue #202). Applies the real
os/patches/rose-agent-self-update-frameworks-base.patch to the pinned PackageInstallerSession.java (frameworks/base
84183aff), then compiles and runs the patched system-app-update check and the added helper, both taken unchanged from
the patched file, against small stand-ins for the session state, PackageStateInternal and SigningDetails. Source and
fixture logic only: no Android build, image, installer or device."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT/'os/patches/rose-agent-self-update-frameworks-base.patch'
PINNED = ROOT/'os/tests/fixtures/rose-agent-self-update/PackageInstallerSession.java'
PINNED_SHA256 = '5ff683567eef824f49bb0e6f1eb9cee1c506ad6ac688970d20802506564a1216'
UPSTREAM = 'services/core/java/com/android/server/pm/PackageInstallerSession.java'
BLOCKED = 'System app updates from unknown sources are blocked'

STUBS = {
    'android/util/PackageUtils.java': '''package android.util;
        public class PackageUtils {
            public static String getFirstPartyAppSourcePackageName(Object context) { return "app.grapheneos.apps"; }
        }''',
    'android/os/Build.java': 'package android.os; public class Build { public static boolean IS_DEBUGGABLE; }',
    'android/os/Process.java': 'package android.os; public class Process { public static final int SHELL_UID = 2000; }',
    'android/os/UserHandle.java': '''package android.os;
        public class UserHandle { public static int getAppId(int uid) { return uid % 100000; } }''',
    'android/os/SystemProperties.java': '''package android.os;
        public class SystemProperties {
            public static java.util.Map<String, Boolean> values = new java.util.HashMap<>();
            public static boolean getBoolean(String key, boolean fallback) { return values.getOrDefault(key, fallback); }
        }''',
    'android/content/pm/PackageManager.java': '''package android.content.pm;
        public class PackageManager { public static final int INSTALL_FAILED_SESSION_INVALID = -116; }''',
    'android/content/pm/SigningDetails.java': '''package android.content.pm;
        public class SigningDetails {
            public static final SigningDetails UNKNOWN = new SigningDetails(null);
            public final String cert;
            public SigningDetails(String cert) { this.cert = cert; }
            public boolean signaturesMatchExactly(SigningDetails other) {
                return cert != null && cert.equals(other.cert);
            }
        }''',
    'android/content/pm/PackageInstaller.java': '''package android.content.pm;
        public class PackageInstaller {
            public static class SessionParams {
                public static final int MODE_FULL_INSTALL = 1;
                public static final int MODE_INHERIT_EXISTING = 2;
                public int mode = MODE_FULL_INSTALL;
                public boolean isStaged;
            }
        }''',
    'com/android/internal/annotations/GuardedBy.java':
        'package com.android.internal.annotations; public @interface GuardedBy { String value(); }',
    'com/android/server/pm/PackageManagerException.java': '''package com.android.server.pm;
        public class PackageManagerException extends Exception {
            public PackageManagerException(int code, String message) { super(message); }
        }''',
    'com/android/server/pm/pkg/PackageStateInternal.java': '''package com.android.server.pm.pkg;
        import android.content.pm.SigningDetails;
        public class PackageStateInternal {
            public String packageName; public int appId; public boolean system, updatedSystem, sharedUser;
            public long versionCode; public SigningDetails signing;
            public String getPackageName() { return packageName; }
            public int getAppId() { return appId; }
            public boolean isSystem() { return system; }
            public boolean isUpdatedSystemApp() { return updatedSystem; }
            public boolean hasSharedUser() { return sharedUser; }
            public long getVersionCode() { return versionCode; }
            public SigningDetails getSigningDetails() { return signing; }
        }''',
}

HARNESS = '''package com.android.server.pm;
import android.content.pm.PackageInstaller.SessionParams;
import android.content.pm.PackageManager;
import android.content.pm.SigningDetails;
import android.os.Build;
import android.os.Process;
import android.os.SystemProperties;
import android.os.UserHandle;
import com.android.internal.annotations.GuardedBy;
import com.android.server.pm.pkg.PackageStateInternal;

public class Harness {
    static class InstallSource { String mInitiatingPackageName; }
    final Object mLock = new Object();
    final Object mContext = null;
    final SessionParams params = new SessionParams();
    final InstallSource mInstallSource = new InstallSource();
    String mPackageName;
    long mVersionCode;
    SigningDetails mSigningDetails = SigningDetails.UNKNOWN;
    volatile int mInstallerUid;
    boolean apex;
    boolean isApexSession() { return apex; }

    @@HELPER@@

    void check(PackageStateInternal existingPkgSetting) throws PackageManagerException {
        @@CHECK@@
    }

    static final int ROSE = 10123;
    static PackageStateInternal installed(String name, int appId, String cert, long version) {
        PackageStateInternal p = new PackageStateInternal();
        p.packageName = name; p.appId = appId; p.signing = new SigningDetails(cert); p.versionCode = version;
        p.system = true;
        return p;
    }

    static Harness selfUpdate() {
        Harness h = new Harness();
        h.mPackageName = "dev.pixel.voice"; h.mVersionCode = 311; h.mSigningDetails = new SigningDetails("rose");
        h.mInstallerUid = ROSE; h.mInstallSource.mInitiatingPackageName = "dev.pixel.voice";
        return h;
    }

    static void run(String name, Harness h, PackageStateInternal existing) {
        try {
            h.check(existing);
            System.out.println(name + "=ALLOWED");
        } catch (PackageManagerException e) {
            System.out.println(name + "=BLOCKED:" + e.getMessage());
        }
    }

    public static void main(String[] args) {
        PackageStateInternal voice = installed("dev.pixel.voice", ROSE, "rose", 310);
        run("self-update", selfUpdate(), voice);
        Harness secondary = selfUpdate(); secondary.mInstallerUid = 1000000 + ROSE;
        run("self-update-other-user", secondary, voice);
        PackageStateInternal updated = installed("dev.pixel.voice", ROSE, "rose", 310);
        updated.system = false; updated.updatedSystem = true;
        run("self-update-updated-system", selfUpdate(), updated);

        Harness third = selfUpdate(); third.mInstallerUid = 10200;
        run("third-party-installer", third, voice);
        Harness spoof = selfUpdate(); spoof.mInstallerUid = 10200; spoof.mInstallSource.mInitiatingPackageName = "dev.pixel.voice";
        run("spoofed-initiating-name", spoof, voice);
        Harness system = selfUpdate(); system.mInstallerUid = 1000;
        run("system-uid-installer", system, voice);
        Harness otherTarget = selfUpdate(); otherTarget.mPackageName = "com.android.systemui";
        run("other-system-target", otherTarget, installed("com.android.systemui", 10050, "platform", 36));
        run("other-target-same-appid", otherTarget, installed("com.android.systemui", ROSE, "rose", 36));
        Harness signer = selfUpdate(); signer.mSigningDetails = new SigningDetails("attacker");
        run("different-signer", signer, voice);
        Harness unknown = selfUpdate(); unknown.mSigningDetails = SigningDetails.UNKNOWN;
        run("unknown-signer", unknown, voice);
        run("installed-signer-unknown", selfUpdate(), installed("dev.pixel.voice", ROSE, null, 310));
        Harness same = selfUpdate(); same.mVersionCode = 310;
        run("same-version", same, voice);
        Harness older = selfUpdate(); older.mVersionCode = 300;
        run("downgrade", older, voice);
        Harness inherit = selfUpdate(); inherit.params.mode = SessionParams.MODE_INHERIT_EXISTING;
        run("inherit-existing", inherit, voice);
        Harness staged = selfUpdate(); staged.params.isStaged = true;
        run("staged", staged, voice);
        Harness apex = selfUpdate(); apex.apex = true;
        run("apex", apex, voice);
        PackageStateInternal shared = installed("dev.pixel.voice", ROSE, "rose", 310); shared.sharedUser = true;
        run("shared-user", selfUpdate(), shared);

        Harness store = selfUpdate(); store.mInstallerUid = 10300; store.mInstallSource.mInitiatingPackageName = "app.grapheneos.apps";
        run("first-party-store", store, installed("com.android.systemui", 10050, "platform", 36));
        Harness shell = selfUpdate(); shell.mInstallerUid = Process.SHELL_UID; shell.mSigningDetails = new SigningDetails("other");
        run("shell", shell, voice);
        PackageStateInternal user = installed("dev.pixel.voice", ROSE, "rose", 310); user.system = false;
        run("not-a-system-app", third, user);
        run("new-install", third, null);
        Build.IS_DEBUGGABLE = true;
        SystemProperties.values.put("persist.allow_unknown_system_app_updates", true);
        run("debug-escape", third, voice);
    }
}
'''

def extract(text, start, opener='{'):
    """The source from the line containing start through its matching closing brace."""
    begin = text.index(start)
    begin = text.rindex('\n', 0, begin) + 1
    depth, i = 0, text.index(opener, text.index(start))
    while True:
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[begin:i + 1]
        i += 1

class RoseAgentSelfUpdateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='rose-self-update-'))
        tree = cls.tmp/'frameworks-base'
        (tree/UPSTREAM).parent.mkdir(parents=True)
        shutil.copyfile(PINNED, tree/UPSTREAM)
        subprocess.run(['git', 'init', '-q', str(tree)], check=True)
        cls.check = subprocess.run(['git', '-C', str(tree), 'apply', '--check', str(PATCH)], capture_output=True, text=True)
        subprocess.run(['git', '-C', str(tree), 'apply', str(PATCH)], check=True)
        cls.patched = (tree/UPSTREAM).read_text()
        src = cls.tmp/'src'
        for relative, code in STUBS.items():
            (src/relative).parent.mkdir(parents=True, exist_ok=True)
            (src/relative).write_text(code)
        constant = re.search(r'\n(    /\*\* RoseOS: the preinstalled Rose Agent.*?\n    private static final String '
            r'ROSE_AGENT_PACKAGE = "dev.pixel.voice";\n)', cls.patched, re.S)[1]
        helper = extract(cls.patched, '    @GuardedBy("mLock")\n    private boolean isRoseAgentSelfUpdateLocked(')
        check = extract(cls.patched, 'if (existingPkgSetting != null && (existingPkgSetting.isSystem()')
        harness = HARNESS.replace('@@HELPER@@', constant + '\n' + helper).replace('@@CHECK@@', check)
        (src/'com/android/server/pm/Harness.java').write_text(harness)
        classes = cls.tmp/'classes'
        sources = [str(p) for p in src.rglob('*.java')]
        subprocess.run(['javac', '-d', str(classes), *sources], check=True)
        output = subprocess.run(['java', '-cp', str(classes), 'com.android.server.pm.Harness'],
            check=True, capture_output=True, text=True).stdout
        cls.results = dict(line.split('=', 1) for line in output.splitlines())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_the_patch_applies_cleanly_to_the_exact_pinned_file(self):
        self.assertEqual(hashlib.sha256(PINNED.read_bytes()).hexdigest(), PINNED_SHA256)
        self.assertEqual(self.check.returncode, 0, self.check.stderr)
        upstream = PINNED.read_text()
        self.assertIn(BLOCKED, upstream)
        self.assertIn('if (!isFirstPartyInstaller && mInstallerUid != Process.SHELL_UID) {', upstream)
        for name in ('SessionParams;', 'SigningDetails;', 'UserHandle;', 'PackageStateInternal;', 'GuardedBy;'):
            self.assertRegex(upstream, r'\nimport [\w.]+\.' + re.escape(name), 'the helper uses only imported types')

    def test_only_rose_agent_updating_itself_is_allowed(self):
        for case in ('self-update', 'self-update-other-user', 'self-update-updated-system'):
            self.assertEqual(self.results[case], 'ALLOWED', case)

    def test_everything_else_stays_blocked_with_the_same_message(self):
        for case in ('third-party-installer', 'spoofed-initiating-name', 'system-uid-installer', 'other-system-target',
                'other-target-same-appid', 'different-signer', 'unknown-signer', 'installed-signer-unknown',
                'same-version', 'downgrade', 'inherit-existing', 'staged', 'apex', 'shared-user'):
            self.assertEqual(self.results[case], 'BLOCKED:' + BLOCKED, case)

    def test_existing_allowed_paths_are_unchanged(self):
        for case in ('first-party-store', 'shell', 'not-a-system-app', 'new-install', 'debug-escape'):
            self.assertEqual(self.results[case], 'ALLOWED', case)

    def test_the_patch_changes_only_this_check(self):
        text = PATCH.read_text()
        self.assertEqual(re.findall(r'^\+\+\+ b/(\S+)', text, re.M), [UPSTREAM])
        removed = [l for l in text.splitlines() if l.startswith('-') and not l.startswith('---')]
        self.assertEqual(removed, ['-            if (!isFirstPartyInstaller && mInstallerUid != Process.SHELL_UID) {'])
        self.assertNotIn('getFirstPartyAppSourcePackageName', ''.join(l for l in text.splitlines() if l.startswith('+')))

    def test_apply_os_changes_registers_it_after_the_base_framework_patch(self):
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        self.assertIn("projects = {'frameworks/base': ['frameworks-base.patch', "
            "'rose-agent-self-update-frameworks-base.patch']}", patcher)

if __name__ == '__main__':
    unittest.main()
