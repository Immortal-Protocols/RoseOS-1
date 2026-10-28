"""Rose Agent becomes the Home app once per user (OS-HOME-2804-FIX-001). Applies the real
os/patches/rose-home-role-permission.patch to the pinned RoleService.java (packages/modules/Permission 4d6005db),
then compiles and runs its added method unchanged against small stand-ins for Settings, PackageManager and the role
controller. The stand-in controller mirrors the pinned RoleControllerServiceImpl.onAddRoleHolder: a profile has no
HOME role, the package must have a HOME activity that is enabled for the user, and the exclusive role keeps one
holder. The pinned behaviour this relies on (an existing qualifying holder survives the update's grantDefaultRoles)
is traced in docs/OS-INTEGRATION.md. Source and fixture logic only: no Android build, image or device."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT/'os/patches/rose-home-role-permission.patch'
PINNED = ROOT/'os/tests/fixtures/rose-home-role/RoleService.java'
sys.path.insert(0, str(ROOT/'scripts'))
from background_apks import ROSE_HOME_PACKAGE, rose_home_projects  # noqa: E402

STUBS = {
    'android/annotation/MainThread.java': 'package android.annotation; public @interface MainThread {}',
    'android/annotation/UserIdInt.java': 'package android.annotation; public @interface UserIdInt {}',
    'android/os/Bundle.java': 'package android.os; public class Bundle { public static final Bundle EMPTY = new Bundle(); }',
    'android/os/RemoteCallback.java': '''package android.os;
        public class RemoteCallback {
            public interface OnResultListener { void onResult(Bundle result); }
            private final OnResultListener listener;
            public RemoteCallback(OnResultListener listener) { this.listener = listener; }
            public void sendResult(Bundle result) { listener.onResult(result); }
        }''',
    'android/util/Log.java': '''package android.util;
        public class Log {
            public static int i(String tag, String message) { return 0; }
            public static int e(String tag, String message, Throwable error) { return 0; }
        }''',
    'android/app/role/RoleManager.java': 'package android.app.role; public class RoleManager { public static final String ROLE_HOME = "android.app.role.HOME"; }',
    'android/content/pm/ApplicationInfo.java': 'package android.content.pm; public class ApplicationInfo { public static final int FLAG_SYSTEM = 1; public int flags; }',
    'android/content/pm/PackageManager.java': '''package android.content.pm;
        public abstract class PackageManager {
            public static class NameNotFoundException extends Exception {}
            public abstract ApplicationInfo getApplicationInfo(String packageName, int flags) throws NameNotFoundException;
        }''',
    'android/content/ContentResolver.java': 'package android.content; public class ContentResolver { public final int userId; public ContentResolver(int userId) { this.userId = userId; } }',
    'android/content/Context.java': '''package android.content;
        public abstract class Context {
            public abstract ContentResolver getContentResolver();
            public abstract android.content.pm.PackageManager getPackageManager();
        }''',
    'android/provider/Settings.java': '''package android.provider;
        public class Settings {
            public static class Secure {
                public static final java.util.Map<String, Integer> VALUES = new java.util.HashMap<>();
                public static int getInt(android.content.ContentResolver resolver, String name, int def) {
                    return VALUES.getOrDefault(resolver.userId + "/" + name, def);
                }
                public static boolean putInt(android.content.ContentResolver resolver, String name, int value) {
                    VALUES.put(resolver.userId + "/" + name, value); return true;
                }
            }
        }''',
    # The real wait is 30 s; the stand-in caps it so the unanswered-controller case stays fast.
    'com/android/internal/infra/AndroidFuture.java': '''package com.android.internal.infra;
        public class AndroidFuture<T> extends java.util.concurrent.CompletableFuture<T> {
            @Override public T get(long timeout, java.util.concurrent.TimeUnit unit)
                    throws InterruptedException, java.util.concurrent.ExecutionException, java.util.concurrent.TimeoutException {
                return super.get(Math.min(unit.toMillis(timeout), 200), java.util.concurrent.TimeUnit.MILLISECONDS);
            }
        }''',
    'com/android/permission/util/UserUtils.java': '''package com.android.permission.util;
        public class UserUtils {
            public static android.content.Context getUserContext(int userId, android.content.Context context) {
                return ((com.android.role.Phone) context).user(userId);
            }
        }''',
}

# The phone: per-user HOME holder, installed packages and the controller's pinned onAddRoleHolder decisions.
PHONE = '''package com.android.role;
import android.os.Bundle;
import android.os.RemoteCallback;
import java.util.*;
public class Phone extends android.content.Context {
    public static class App { boolean system, enabled = true, home = true; }
    public final Map<Integer, String> holder = new HashMap<>();
    public final Map<Integer, Map<String, App>> apps = new HashMap<>();
    public final Set<Integer> profiles = new HashSet<>();
    public boolean hang;
    public int adds;
    public App install(int user, String name, boolean system) {
        App app = new App(); app.system = system; apps.computeIfAbsent(user, u -> new HashMap<>()).put(name, app); return app;
    }
    public android.content.Context user(int userId) {
        Phone phone = this;
        return new android.content.Context() {
            public android.content.ContentResolver getContentResolver() { return new android.content.ContentResolver(userId); }
            public android.content.pm.PackageManager getPackageManager() {
                return new android.content.pm.PackageManager() {
                    public android.content.pm.ApplicationInfo getApplicationInfo(String name, int flags) throws NameNotFoundException {
                        App app = phone.apps.getOrDefault(userId, Map.of()).get(name);
                        if (app == null) throw new NameNotFoundException();
                        android.content.pm.ApplicationInfo info = new android.content.pm.ApplicationInfo();
                        info.flags = app.system ? android.content.pm.ApplicationInfo.FLAG_SYSTEM : 0;
                        return info;
                    }
                };
            }
        };
    }
    public android.content.ContentResolver getContentResolver() { throw new AssertionError("use a user context"); }
    public android.content.pm.PackageManager getPackageManager() { throw new AssertionError("use a user context"); }
    public RoleController controller(int userId) {
        return (role, name, flags, callback) -> {
            adds++;
            if (hang) return;  // the controller never answers: the future times out
            App app = apps.getOrDefault(userId, Map.of()).get(name);
            boolean ok = role.equals(android.app.role.RoleManager.ROLE_HOME) && !profiles.contains(userId)
                && app != null && app.enabled && app.home;
            if (ok) holder.put(userId, name);  // exclusive: replaces the previous holder
            callback.sendResult(ok ? Bundle.EMPTY : null);
        };
    }
}'''

CONTROLLER = '''package com.android.role;
public interface RoleController {
    void onAddRoleHolder(String roleName, String packageName, int flags, android.os.RemoteCallback callback);
}'''

def method_and_fields(service):
    """The patch's added fields and method, exactly as they appear in the patched RoleService."""
    fields = re.findall(r'    private static final String ROSE_HOME_\w+ = "[^"]+";\n', service)
    start = service.index('    @MainThread\n    private void maybeSelectRoseHomeSync(')
    end = service.index('\n    }\n', start) + len('\n    }\n')
    return ''.join(fields), service[start:end]

SERVICE = '''package com.android.role;
import android.annotation.MainThread;
import android.annotation.UserIdInt;
import android.app.role.RoleManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.RemoteCallback;
import android.provider.Settings;
import android.util.Log;
import com.android.internal.infra.AndroidFuture;
import com.android.permission.util.UserUtils;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
public class RoseHomeHarness {
    private static final String LOG_TAG = "RoleService";
    final Phone phone;
    RoseHomeHarness(Phone phone) { this.phone = phone; }
    Context getContext() { return phone; }
    RoleController getOrCreateController(int userId) { return phone.controller(userId); }
    void onUserStarting(int userId) { maybeSelectRoseHomeSync(userId); }
%s
%s
}'''

SCENARIOS = r'''package com.android.role;
import android.provider.Settings;
public class Scenarios {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }
    static String marker(int user) { return String.valueOf(Settings.Secure.VALUES.get(user + "/rose_home_role_selected")); }
    static Phone phone() {
        Settings.Secure.VALUES.clear();
        Phone phone = new Phone();
        phone.install(0, "com.android.launcher3", true);
        phone.install(0, "dev.pixel.voice", true);
        return phone;
    }
    public static void main(String[] args) {
        // Update from 2804: Launcher3 kept the role across the update; the first boot selects Rose once.
        Phone phone = phone();
        phone.holder.put(0, "com.android.launcher3");
        RoseHomeHarness service = new RoseHomeHarness(phone);
        service.onUserStarting(0);
        expect("dev.pixel.voice".equals(phone.holder.get(0)), "2804 update: Rose replaces the kept launcher");
        expect("1".equals(marker(0)), "the selection is recorded for that user");
        // Every later boot leaves the role alone, including after the owner deliberately picks another Home.
        service.onUserStarting(0);
        expect(phone.adds == 1, "a reboot does not select again");
        phone.holder.put(0, "com.android.launcher3");
        service.onUserStarting(0);
        service.onUserStarting(0);
        expect("com.android.launcher3".equals(phone.holder.get(0)) && phone.adds == 1, "a later owner choice stands across reboots");

        // Fresh phone: whatever the default grant picked (or none, a chooser), the first start selects Rose.
        phone = phone();
        new RoseHomeHarness(phone).onUserStarting(0);
        expect("dev.pixel.voice".equals(phone.holder.get(0)), "fresh: Rose is Home at first boot");

        // Already Rose (a phone that chose it before): the selection is a no-op that is still recorded once.
        phone = phone();
        phone.holder.put(0, "dev.pixel.voice");
        service = new RoseHomeHarness(phone);
        service.onUserStarting(0);
        service.onUserStarting(0);
        expect("dev.pixel.voice".equals(phone.holder.get(0)) && "1".equals(marker(0)) && phone.adds == 1, "already Rose");

        // Rose disabled by the owner, or without a HOME activity: keep the current Home, record, never retry.
        for (int variant = 0; variant < 2; variant++) {
            phone = phone();
            phone.holder.put(0, "com.android.launcher3");
            Phone.App rose = phone.apps.get(0).get("dev.pixel.voice");
            if (variant == 0) rose.enabled = false; else rose.home = false;
            service = new RoseHomeHarness(phone);
            service.onUserStarting(0);
            expect("com.android.launcher3".equals(phone.holder.get(0)) && "1".equals(marker(0)), "nonqualifying Rose keeps the Home");
            rose.enabled = true; rose.home = true;
            service.onUserStarting(0);
            expect("com.android.launcher3".equals(phone.holder.get(0)), "re-enabling Rose later does not take the Home");
        }

        // Absent, or a same-named app that is not preinstalled: never asked to the controller.
        phone = phone();
        phone.apps.get(0).remove("dev.pixel.voice");
        phone.holder.put(0, "com.android.launcher3");
        new RoseHomeHarness(phone).onUserStarting(0);
        expect(phone.adds == 0 && "com.android.launcher3".equals(phone.holder.get(0)) && "1".equals(marker(0)), "absent Rose");
        phone = phone();
        phone.install(0, "dev.pixel.voice", false);
        phone.holder.put(0, "com.android.launcher3");
        new RoseHomeHarness(phone).onUserStarting(0);
        expect(phone.adds == 0 && "com.android.launcher3".equals(phone.holder.get(0)), "a sideloaded dev.pixel.voice is never selected");

        // Users: each full user once, with its own marker; a profile has no HOME role and keeps nothing new.
        phone = phone();
        phone.install(10, "com.android.launcher3", true);
        phone.install(10, "dev.pixel.voice", true);
        phone.install(11, "dev.pixel.voice", true);
        phone.profiles.add(11);
        phone.holder.put(10, "com.android.launcher3");
        service = new RoseHomeHarness(phone);
        service.onUserStarting(0);
        expect(!phone.holder.containsKey(10) || "com.android.launcher3".equals(phone.holder.get(10)), "user 0 does not touch user 10");
        service.onUserStarting(10);
        service.onUserStarting(11);
        expect("dev.pixel.voice".equals(phone.holder.get(10)) && "1".equals(marker(10)), "a second full user is selected on its own start");
        expect(!phone.holder.containsKey(11) && "1".equals(marker(11)), "a profile gets no HOME holder");

        // An unanswered controller is not recorded, so the next start retries.
        phone = phone();
        phone.holder.put(0, "com.android.launcher3");
        phone.hang = true;
        service = new RoseHomeHarness(phone);
        service.onUserStarting(0);
        expect("null".equals(marker(0)) && "com.android.launcher3".equals(phone.holder.get(0)), "timeout: not recorded");
        phone.hang = false;
        service.onUserStarting(0);
        expect("dev.pixel.voice".equals(phone.holder.get(0)) && "1".equals(marker(0)), "retried at the next start");
        System.out.println("RoseHomeRole: " + checks + " checks passed");
    }
}'''

class RoseHomeRoleTest(unittest.TestCase):
    def patched_service(self, directory):
        target = directory/'service/java/com/android/role/RoleService.java'
        target.parent.mkdir(parents=True)
        shutil.copy(PINNED, target)
        subprocess.run(['git', 'init', '-q'], cwd=directory, check=True)
        subprocess.run(['git', 'apply', '--check', str(PATCH)], cwd=directory, check=True)
        subprocess.run(['git', 'apply', str(PATCH)], cwd=directory, check=True)
        return target.read_text()

    def test_patch_applies_to_the_pinned_role_service_and_only_adds(self):
        self.assertEqual(hashlib.sha256(PINNED.read_bytes()).hexdigest(),
            '8bc87856050126743959e24467f7b27a7258d95e9f216d0a26b06d0e8f1b3415')
        text = PATCH.read_text()
        self.assertEqual(re.findall(r'^diff --git a/(\S+)', text, re.M), ['service/java/com/android/role/RoleService.java'])
        self.assertFalse([l for l in text.splitlines() if l.startswith('-') and not l.startswith('---')])
        with tempfile.TemporaryDirectory() as directory:
            service = self.patched_service(Path(directory))
        # Runs right after the existing default grant, on every user start; the marker makes it one-time.
        self.assertIn('            maybeGrantDefaultRolesSync(user.getUserHandle().getIdentifier());\n'
                      '            maybeSelectRoseHomeSync(user.getUserHandle().getIdentifier());\n', service)
        _, method = method_and_fields(service)
        # Only the existing role-controller selection: no new permission, owner, preferred-activity or wallpaper path.
        for absent in ('setPreferredActivity', 'DevicePolicyManager', 'Wallpaper', 'grantRuntimePermission',
                       'setRoleFallbackEnabled', 'clearRoleHolders', 'removeRoleHolder', 'putString'):
            self.assertNotIn(absent, method)
        self.assertIn('getOrCreateController(userId).onAddRoleHolder(RoleManager.ROLE_HOME, ROSE_HOME_PACKAGE,', method)
        self.assertIn(f'ROSE_HOME_PACKAGE = "{ROSE_HOME_PACKAGE}"', service)

    def test_selection_semantics_on_the_patched_method(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fields, method = method_and_fields(self.patched_service(root/'module'))
            src = root/'src'
            files = dict(STUBS)
            files['com/android/role/Phone.java'] = PHONE
            files['com/android/role/RoleController.java'] = CONTROLLER
            files['com/android/role/RoseHomeHarness.java'] = SERVICE % (fields, method)
            files['com/android/role/Scenarios.java'] = SCENARIOS
            for relative, body in files.items():
                (src/relative).parent.mkdir(parents=True, exist_ok=True)
                (src/relative).write_text(body)
            out = root/'classes'
            subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-nowarn', '-d', str(out),
                *map(str, src.rglob('*.java'))], check=True, capture_output=True, text=True)
            result = subprocess.run(['java', '-cp', str(out), 'com.android.role.Scenarios'],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('checks passed', result.stdout)

    def test_only_a_rose_agent_product_build_carries_the_patch(self):
        self.assertEqual(rose_home_projects([{'packageName': 'dev.pixel.voice'}, {'packageName': 'dev.pixel.calculator.agent'}]),
            {'packages/modules/Permission': ['rose-home-role-permission.patch']})
        self.assertEqual(rose_home_projects([{'packageName': 'dev.pixel.calculator.agent'}]), {})
        self.assertEqual(rose_home_projects([]), {})
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        self.assertIn('preinstalls = preinstall_plan(policy, a.preinstall_apk) if a.preinstall_apk else []\n'
                      'projects.update(rose_home_projects(preinstalls))\n', patcher)
        # The project is pinned, so the patcher's revision and clean-tree checks cover it.
        self.assertRegex((ROOT/'os/pinned-manifest.xml').read_text(),
            r'path="packages/modules/Permission"[^>]*revision="4d6005dbabf3f2bf96e5215c5c2801809b051bf3"')

if __name__ == '__main__':
    unittest.main()
