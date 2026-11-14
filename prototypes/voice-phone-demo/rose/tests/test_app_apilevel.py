"""The advisory API level report (docs/APP-BUILDER.md §2.3): what it finds in the built dex, and what it must not
claim. Every app is a fixture the test writes; it is compiled, never run. The matrix needs the pinned SDK."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rose.app_builder import apilevel, toolchain
from rose.app_builder.service import Builder
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK, fake_backup

FIXTURE = '''package {p};

import android.app.Activity;
import android.os.Build;
import android.os.Bundle;
import java.util.List;

public final class MainActivity extends Activity {{
    static boolean atLeastU() {{ return Build.VERSION.SDK_INT >= 34; }}
    String unguarded() {{ return "a %s".formatted(1); }}
    String guardedIf() {{ if (Build.VERSION.SDK_INT >= 34) return "b %s".formatted(2); return ""; }}
    String guardedReturn() {{ if (Build.VERSION.SDK_INT < 34) return ""; return "c %s".formatted(3); }}
    String guardedCodes() {{
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) return "d %s".formatted(4);
        return "";
    }}
    String wrongGuard() {{ if (Build.VERSION.SDK_INT < 34) return "e %s".formatted(5); return ""; }}
    String tooLowGuard() {{ if (Build.VERSION.SDK_INT >= 30) return "f %s".formatted(6); return ""; }}
    String guardedHelper() {{ return atLeastU() ? "g %s".formatted(7) : ""; }}
    void inherited() {{ setShowWhenLocked(true); }}
    String field() {{ return Build.SOC_MODEL; }}
    int constant() {{ return Build.VERSION_CODES.UPSIDE_DOWN_CAKE; }}
    Object newClass() {{ return android.graphics.BlendMode.SRC; }}
    int backported() {{ return List.of(1, 2).size() + "x".repeat(2).length(); }}
    Object reflect() throws Exception {{ return String.class.getMethod("formatted", Object[].class); }}
    void newCatch() {{
        try {{ startForegroundService(null); }} catch (android.app.ForegroundServiceStartNotAllowedException e) {{ }}
    }}
    Object newInterface() {{ android.window.OnBackInvokedCallback cb = () -> {{ }}; return cb; }}
    Object extension() {{
        if (Build.VERSION.SDK_INT >= 31) return android.os.ext.SdkExtensions.getAllExtensionVersions();
        return null;
    }}
    int switched(int x) {{
        switch (x) {{
            case 1: return "h %s".formatted(8).length();
            case 2: return 2;
            case 3: return 3;
            default: return 0;
        }}
    }}
    static boolean flag;
    static int sdkLevel() {{ return Build.VERSION.SDK_INT; }}
    String boolLocal() {{ boolean ok = Build.VERSION.SDK_INT >= 34; return ok ? "i %s".formatted(9) : ""; }}
    String orGuard() {{ if (flag || Build.VERSION.SDK_INT >= 34) return "j %s".formatted(10); return ""; }}
    String notEqual() {{ if (Build.VERSION.SDK_INT != 33) return "k %s".formatted(11); return ""; }}
    String reused() {{
        int v = Build.VERSION.SDK_INT;
        if (v < 30) return "";
        if (v >= 34) return "l %s".formatted(12);
        return "";
    }}
    String intHelper() {{ if (sdkLevel() >= 34) return "m %s".formatted(13); return ""; }}
    String fieldFlag() {{ if (flag) return "n %s".formatted(14); return ""; }}
    String inFinally() {{ try {{ if (Build.VERSION.SDK_INT >= 34) return "o %s".formatted(15); }} finally {{ flag = !flag; }} return ""; }}
    String afterCatch() {{
        try {{ if (Build.VERSION.SDK_INT < 34) throw new IllegalStateException(); }} catch (IllegalStateException e) {{ }}
        return "p %s".formatted(16);  // the throw below 34 is caught and lands here
    }}
    @Override public void onTopResumedActivityChanged(boolean top) {{ }}
    void ownOverride() {{ onTopResumedActivityChanged(true); }}
    @Override protected void onCreate(Bundle state) {{ super.onCreate(state); }}
}}
'''
FORMATTED = 'java/lang/String.formatted([Ljava/lang/Object;)Ljava/lang/String;'


def build(root: Path, source: str, **patches):
    def generate(src, kind, request, cancelled):
        java = next(src.glob('src/**/MainActivity.java'))
        java.write_text(source.format(p=java.read_text().split(';')[0].split()[1]))
    b = Builder(root, toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True), generate,
                backup=fake_backup)
    out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
    b.run_next()
    return b, b.job(out['jobId'])


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK (ANDROID_SDK_ROOT) and a JDK')
class Matrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.b, cls.job = build(Path(cls.tmp.name) / 'b', FIXTURE)
        project = cls.b.projects()[0]
        cls.report = cls.b.ledger.api_report(project['projectId'], 1) if project['versions'] else None
        cls.by_method = {}
        for f in (cls.report or {}).get('findings', []):
            method = f['where'].split('.')[-1].split('(')[0] if f['kind'] != 'supertype' else f['where']
            cls.by_method.setdefault(method, []).append(f)

    @classmethod
    def tearDownClass(cls):
        cls.b.ledger.close()
        cls.tmp.cleanup()

    def status(self, method, api=FORMATTED):
        found = [(f['status'], f['level']) for f in self.by_method.get(method, []) if f['api'] == api]
        self.assertEqual(len(found), 1, (method, self.by_method.get(method)))
        return found[0]

    def test_the_build_is_delivered_and_the_report_recorded_as_advisory(self):
        self.assertEqual(self.job['state'], 'ready', self.job)
        self.assertTrue(self.report['advisory'])
        self.assertEqual((self.report['minSdk'], self.report['database']['file']), (26, 'api-versions.xml'))
        self.assertIn('not device compatibility proof', self.report['note'])
        self.assertIn('API level report (advisory, not device proof)', self.job['log'] if 'log' in self.job
                      else self.b.ledger.job(self.job['jobId'])['logTail'])

    def test_guards_are_read_with_their_direction(self):
        self.assertEqual(self.status('unguarded'), ('not_guarded_here', 34))
        for method in ('guardedIf', 'guardedReturn', 'guardedCodes'):
            with self.subTest(method=method):
                self.assertEqual(self.status(method), ('guarded', 34))
        self.assertEqual(self.status('wrongGuard'), ('not_guarded_here', 34))  # SDK_INT < 34 runs it on 26-33
        self.assertEqual(self.status('tooLowGuard'), ('not_guarded_here', 34))  # 30 is not enough for 34

    def test_a_helper_guard_is_unverified_never_safe(self):
        status, _ = self.status('guardedHelper')
        self.assertEqual(status, 'unverified')
        for method in ('intHelper', 'fieldFlag', 'boolLocal', 'reused'):  # app values or an SDK_INT kept in a variable
            with self.subTest(method=method):
                self.assertEqual(self.status(method)[0], 'unverified')

    def test_no_branch_that_can_run_below_the_level_is_called_guarded(self):
        self.assertEqual(self.status('orGuard')[0], 'unverified')  # flag || SDK_INT >= 34: flag alone reaches it
        self.assertEqual(self.status('notEqual'), ('not_guarded_here', 34))  # != 33 lets 26 through
        self.assertEqual(self.status('inFinally'), ('guarded', 34))
        self.assertEqual(self.status('afterCatch'), ('not_guarded_here', 34))  # reached through the exception edge
        for f in self.report['findings']:
            if f['status'] == 'guarded':
                self.assertIn(f['where'].split('.')[-1].split('(')[0],
                              {'guardedIf', 'guardedReturn', 'guardedCodes', 'inFinally'}, f)

    def test_a_call_to_the_apps_own_override_is_not_a_platform_reference(self):
        self.assertNotIn('ownOverride', self.by_method)

    def test_inherited_members_fields_and_types_resolve_to_the_platform(self):
        self.assertEqual(self.status('inherited', 'android/app/Activity.setShowWhenLocked(Z)V'),
                         ('not_guarded_here', 27))  # called on the app's own MainActivity
        self.assertEqual(self.status('field', 'android/os/Build.SOC_MODEL'), ('not_guarded_here', 31))
        self.assertEqual(self.status('newClass', 'android/graphics/BlendMode.SRC'), ('not_guarded_here', 29))

    def test_constants_backports_and_reflection_leave_no_reference(self):
        for method in ('constant', 'backported', 'reflect'):
            with self.subTest(method=method):
                self.assertNotIn(method, self.by_method)  # reflection is a documented blind spot, not "safe"
        apis = {f['api'] for f in self.report['findings']}
        self.assertFalse(any(a.startswith(('java/util/List.of', 'java/lang/String.repeat')) for a in apis))

    def test_catch_handlers_and_synthetic_interfaces_are_found(self):
        self.assertEqual(self.status('newCatch', 'android/app/ForegroundServiceStartNotAllowedException'),
                         ('not_guarded_here', 31))
        lambdas = [f for f in self.report['findings'] if f['kind'] == 'supertype'
                   and f['api'] == 'android/window/OnBackInvokedCallback']
        self.assertEqual([(f['status'], f['level']) for f in lambdas], [('unverified', 33)])

    def test_extension_sdk_and_switch_methods_are_unverified_and_marked_incomplete(self):
        self.assertEqual(self.status('extension', 'android/os/ext/SdkExtensions.getAllExtensionVersions()Ljava/util/Map;'),
                         ('unverified', 31))
        self.assertEqual(self.status('switched'), ('unverified', 34))
        self.assertEqual(self.report['status'], 'incomplete')
        self.assertIn('switch', self.report['reason'])


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK (ANDROID_SDK_ROOT) and a JDK')
class Unavailable(unittest.TestCase):
    """Whatever the analysis cannot do, the build is unchanged and the report says so; it never reads as clean."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def check(self, patch, reason):
        with patch:
            b, job = build(Path(self.tmp.name) / reason[:8].replace(' ', '_'), FIXTURE)
        self.addCleanup(b.ledger.close)
        self.assertEqual(job['state'], 'ready', job)
        report = b.ledger.api_report(b.projects()[0]['projectId'], 1)
        self.assertEqual((report['status'], report['findings']), ('unavailable', []))
        self.assertIn(reason, report['reason'])
        self.assertNotIn('counts', report)  # no zero counts that could read as "nothing found"

    def test_a_missing_database(self):
        self.check(mock.patch.object(apilevel, 'DATABASE', 'platforms/android-35/data/missing.xml'), 'missing')

    def test_an_analysis_that_crashes(self):
        self.check(mock.patch.object(apilevel, 'analyse', side_effect=RuntimeError('boom')), 'analysis stopped')


DB = '''<?xml version="1.0" encoding="utf-8"?>
<api version="3">
  <class name="java/lang/Object" since="1"><method name="toString()Ljava/lang/String;"/></class>
  <class name="java/lang/String" since="1"><extends name="java/lang/Object"/>
    <method name="formatted([Ljava/lang/Object;)Ljava/lang/String;" since="34"/></class>
  <class name="android/app/Activity" since="1"><extends name="java/lang/Object"/>
    <method name="setShowWhenLocked(Z)V" since="27"/></class>
  <class name="android/x/New" since="30"><extends name="java/lang/Object"/>
    <implements name="android/x/Later" since="33"/></class>
  <class name="android/x/Later" since="33"><method name="go()V"/></class>
</api>
'''


def dump(body: str, cls: str = 'app/x/A', superclass: str = 'java/lang/Object') -> str:
    lines = [f"Class #0            -\n  Class descriptor  : 'L{cls};'\n  Access flags      : 0x0001 (PUBLIC)\n"
             f"  Superclass        : 'L{superclass};'\n  Interfaces        -\n  Static fields     -\n"
             f"  Instance fields   -\n  Direct methods    -\n    #0              : (in L{cls};)\n"
             f"      name          : 'm'\n      type          : '()V'\n      code          -"]
    for i, insn in enumerate(body.strip().splitlines()):
        addr, rest = insn.split(':', 1)
        lines.append(f'{0x100 + 2 * i:06x}: 0000                                   |{addr.strip()}:{rest}')
    lines.append('      catches       : (none)\n      positions     :')
    return '\n'.join(lines) + '\n'


CALL = 'invoke-virtual {v2}, Ljava/lang/String;.formatted:([Ljava/lang/Object;)Ljava/lang/String; // method@0001'


class Units(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / 'api.xml'
        path.write_text(DB)
        self.db = apilevel.Database(path)

    def statuses(self, text):
        return [(f['status'], f['level']) for f in apilevel.analyse([text], self.db, 'app.x', 26)['findings']]

    def test_levels_resolve_through_supertypes_and_added_interfaces(self):
        self.assertEqual(self.db.level('java/lang/String', 'formatted([Ljava/lang/Object;)Ljava/lang/String;'),
                         (34, False))
        self.assertEqual(self.db.level('java/lang/String', 'toString()Ljava/lang/String;'), (1, False))
        self.assertEqual(self.db.level('android/x/New', 'go()V'), (33, False))  # via an interface added at 33
        self.assertIsNone(self.db.level('android/x/Missing'))

    def test_every_comparison_form_and_constant_side(self):
        guarded, unguarded = ('guarded', 34), ('not_guarded_here', 34)
        cases = {  # comparison (SDK_INT in v0, the constant in v1), and where the call sits
            'if-lt v0, v1, 0009 // +0005': (34, unguarded, guarded),  # call on the true (< 34) branch, else fall through
            'if-ge v0, v1, 0009 // +0005': (34, guarded, unguarded),
            'if-gt v0, v1, 0009 // +0005': (33, guarded, unguarded),
            'if-le v0, v1, 0009 // +0005': (33, unguarded, guarded),
            'if-ge v1, v0, 0009 // +0005': (33, unguarded, guarded),  # 33 >= SDK_INT: the false branch has >= 34
        }
        for cmp, (const, on_true, on_false) in cases.items():
            for where, expected in (('true', on_true), ('false', on_false)):
                with self.subTest(cmp=cmp, where=where):
                    if where == 'true':  # the call at the branch target; the fall-through returns
                        body = (f'0000: sget v0, Landroid/os/Build$VERSION;.SDK_INT:I // field@0000\n'
                                f'0002: const/16 v1, #int {const} // #0\n0004: {cmp}\n0006: return-void\n'
                                f'0009: {CALL}\n000c: return-void')
                    else:
                        body = (f'0000: sget v0, Landroid/os/Build$VERSION;.SDK_INT:I // field@0000\n'
                                f'0002: const/16 v1, #int {const} // #0\n0004: {cmp}\n0006: {CALL}\n'
                                f'0009: return-void')
                    self.assertEqual(self.statuses(dump(body)), [expected])

    def test_an_unread_sdk_use_or_a_helper_is_unverified(self):
        stored = ('0000: sget v0, Landroid/os/Build$VERSION;.SDK_INT:I // field@0000\n'
                  '0002: sput v0, Lapp/x/A;.level:I // field@0001\n'
                  f'0004: {CALL}\n0007: return-void')
        self.assertEqual(self.statuses(dump(stored)), [('unverified', 34)])
        helper = ('0000: invoke-static {}, Lapp/x/A;.ok:()Z // method@0002\n0003: move-result v0\n'
                  f'0004: if-eqz v0, 0009 // +0005\n0006: {CALL}\n0009: return-void')
        self.assertEqual(self.statuses(dump(helper)), [('unverified', 34)])

    def test_an_inherited_call_on_the_apps_own_class_and_a_supertype(self):
        call = '0000: invoke-virtual {v0, v1}, Lapp/x/A;.setShowWhenLocked:(Z)V // method@0003\n0003: return-void'
        self.assertEqual(self.statuses(dump(call, superclass='android/app/Activity')), [('not_guarded_here', 27)])
        report = apilevel.analyse([dump('0000: return-void', superclass='android/x/New')], self.db, 'app.x', 26)
        self.assertEqual([(f['kind'], f['status'], f['level']) for f in report['findings']],
                         [('supertype', 'unverified', 30)])

    def test_goto32_wide_addresses_and_unreadable_code_fail_closed(self):
        # goto/32 prints a relative offset: the edge to the else side must exist, so the join is not guarded.
        body = ('0000: sget v0, Landroid/os/Build$VERSION;.SDK_INT:I // field@0000\n'
                '0002: const/16 v1, #int 34 // #0\n0004: if-ge v0, v1, 0009 // +0005\n'
                '0006: goto/32 #00000006\n0009: nop // spacer\n000c: ' + CALL + '\n000f: return-void')
        self.assertEqual(self.statuses(dump(body)), [('not_guarded_here', 34)])
        wide = f'10000: {CALL}\n10003: return-void'  # five-digit addresses are read, not dropped
        self.assertEqual(self.statuses(dump(wide)), [('not_guarded_here', 34)])
        for broken in (f'0000: {CALL}\n0003: goto zzzz\n0004: return-void',  # a target that cannot be read
                       f'0000: {CALL}\n0003: if-eqz v0, 0099 // +0096\n0005: return-void'):  # outside the method
            with self.subTest(broken=broken):
                report = apilevel.analyse([dump(broken)], self.db, 'app.x', 26)
                self.assertEqual(report['status'], 'incomplete')
                self.assertEqual([f['status'] for f in report['findings']], ['unverified'])
        text = dump(f'0000: {CALL}\n0003: return-void').replace('      catches', '000200: what is this\n      catches')
        report = apilevel.analyse([text], self.db, 'app.x', 26)  # a code line it could not read
        self.assertEqual((report['status'], report['findings'][0]['status']), ('incomplete', 'unverified'))

    def test_size_and_time_limits_are_reported_not_hidden(self):
        text = dump(f'0000: {CALL}\n0003: return-void')
        with mock.patch.object(apilevel, 'MAX_INSNS', 1):
            report = apilevel.analyse([text], self.db, 'app.x', 26)
        self.assertEqual((report['status'], report['findings'][0]['status']), ('incomplete', 'unverified'))
        report = apilevel.analyse([text], self.db, 'app.x', 26, budget_s=-1)
        self.assertEqual((report['status'], report['findings']), ('incomplete', []))
        self.assertIn('budget', report['reason'])

    def test_inherited_members_take_the_level_of_the_newer_class_and_the_earliest_path(self):
        self.assertEqual(self.db.level('android/x/New', 'toString()Ljava/lang/String;'), (30, False))
        path = Path(self.tmp.name) / 'two.xml'
        path.write_text('<api version="3"><class name="java/lang/Object" since="1"><method name="m()V"/></class>'
                        '<class name="a/Late" since="1"><extends name="java/lang/Object"/></class>'
                        '<class name="a/C" since="1"><implements name="a/Late" since="35"/>'
                        '<implements name="java/lang/Object"/></class></api>')
        self.assertEqual(apilevel.Database(path).level('a/C', 'm()V'), (1, False))  # reachable since 1, not 35

    def test_a_dump_without_classes_is_an_error_not_a_clean_report(self):
        with self.assertRaises(ValueError):
            apilevel.analyse(['Processing nothing\n'], self.db, 'app.x', 26)


if __name__ == '__main__':
    unittest.main()
