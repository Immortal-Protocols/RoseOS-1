"""#410 E2c: the interface record in os/staged/rose-edge-resize/aidl and Launcher3's hand-written binder code in
os/overlays agree exactly, and apply-os-changes.py registers neither Rose edge patch (draft, default-off, until a real
compile). The opt-in Robolectric harness in os/staged/rose-edge-resize/native-test runs the same code against the aidl
tool's generated Stub and Proxy; this check needs no SDK so that CI keeps the two from drifting. Source only: no build,
device or host."""
from pathlib import Path
import hashlib
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
STAGED = ROOT/'os/staged/rose-edge-resize'
AIDL = STAGED/'aidl'
FALLBACK = ROOT/'os/overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback'
WIRING = ROOT/'os/patches/rose-edge-resize-launcher3.patch'


def methods(path):
    """[(name, [(type, name)], id)] of an AIDL interface, in declaration order."""
    text = re.sub(r'/\*.*?\*/', '', path.read_text(), flags=re.S)
    body = text[text.index('{') + 1:text.rindex('}')]
    out = []
    for m in re.finditer(r'(?:oneway\s+)?void\s+(\w+)\(([^)]*)\)\s*=\s*(\d+)\s*;', body):
        args = [tuple(a.split()[-2:]) for a in m.group(2).split(',') if a.strip()]
        out.append((m.group(1), args, int(m.group(3))))
    return out


class RoseEdgeResizeStagedTest(unittest.TestCase):
    def shell(self):
        return (FALLBACK/'RoseEdgeShell.java').read_text()

    def test_the_interface_record_is_exactly_the_agreed_shape(self):
        edge = methods(AIDL/'com/android/wm/shell/roseedge/IRoseEdge.aidl')
        self.assertEqual(edge, [('raise', [('int', 'displayId'), ('int', 'taskId'), ('int', 'liftPx'),
            ('IRoseEdgeListener', 'listener')], 1), ('lower', [('int', 'displayId')], 2),
            ('setMicSlot', [('int', 'displayId'), ('Rect', 'slotBounds')], 3)])
        listener = methods(AIDL/'com/android/wm/shell/roseedge/IRoseEdgeListener.aidl')
        self.assertEqual(listener, [('onRaised', [('int', 'displayId'), ('int', 'taskId'), ('Rect', 'appBounds')], 1),
            ('onRefused', [('int', 'displayId'), ('int', 'taskId'), ('int', 'reason')], 2),
            ('onLowered', [('int', 'displayId'), ('int', 'reason')], 3)])
        # The Home-panel interface (IRoseEdgeControls/IRoseEdgeHost) was withdrawn for Home's no-new-trust option (a):
        # the edge's mic slot is SystemUI's existing trusted tray client (#410 comment 6622900952).
        self.assertEqual(sorted(str(p.relative_to(AIDL)) for p in AIDL.rglob('*.aidl')),
            ['com/android/wm/shell/roseedge/IRoseEdge.aidl', 'com/android/wm/shell/roseedge/IRoseEdgeListener.aidl'])
        for path in AIDL.rglob('*.aidl'):
            self.assertRegex(path.read_text(), r'oneway (interface|void)', f'{path.name}: every call is one-way')

    def test_the_hand_written_client_uses_the_records_descriptors_ids_and_order(self):
        java = self.shell()
        self.assertIn('DESCRIPTOR = "com.android.wm.shell.roseedge.IRoseEdge"', java)
        self.assertIn('LISTENER_DESCRIPTOR = "com.android.wm.shell.roseedge.IRoseEdgeListener"', java)
        ids = {name: int(n) for name, n in re.findall(r'(TRANSACTION_\w+) = IBinder\.FIRST_CALL_TRANSACTION \+ (\d+)', java)}
        self.assertEqual(ids, {'TRANSACTION_RAISE': 1, 'TRANSACTION_LOWER': 2, 'TRANSACTION_SET_MIC_SLOT': 3,
            'TRANSACTION_ON_RAISED': 1,
            'TRANSACTION_ON_REFUSED': 2, 'TRANSACTION_ON_LOWERED': 3})
        raise_body = java[java.index('public static boolean raise('):java.index('public static boolean lower(')]
        self.assertEqual(re.findall(r'data\.write(\w+)\((\w+)', raise_body), [('InterfaceToken', 'DESCRIPTOR'),
            ('Int', 'displayId'), ('Int', 'taskId'), ('Int', 'liftPx'), ('StrongBinder', 'listenerFor')])
        self.assertIn('IBinder.FLAG_ONEWAY', raise_body)
        slot_body = java[java.index('public static boolean setMicSlot('):java.index('static final class Listener')]
        self.assertEqual(re.findall(r'data\.write(\w+)\((\w+)', slot_body), [('InterfaceToken', 'DESCRIPTOR'),
            ('Int', 'displayId'), ('TypedObject', 'slotBounds')])
        self.assertIn('IBinder.FLAG_ONEWAY', slot_body)
        state = (FALLBACK/'RoseEdgeState.java').read_text()
        self.assertIn('MIC_SLOT_MIN_DP = 32, MIC_SLOT_MAX_DP = 64, MIC_SLOT_BAND_MAX_DP = 160', state)
        every = ''.join(p.read_text() for p in FALLBACK.glob('*.java'))
        self.assertNotRegex(every, r'(?i)AgentTray|setMicrophoneMute|SensorPrivacy|AudioManager|\bMUTE\b',
            'Launcher3 never touches microphone state (option (a))')
        # Nov 10 1A: direct, permission-protected conversation entry; no fabricated return task extra.
        self.assertNotIn('ROSE_EDGE_DESTINATION', every + WIRING.read_text())
        self.assertNotIn('Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY', every)
        window = (FALLBACK/'RoseEdgeWindow.java').read_text()
        self.assertIn('return RoseEdgeEntry.intent();', window)
        self.assertIn('ROSE_PACKAGE = "dev.pixel.voice"', window)
        listener = java[java.index('static final class Listener'):]
        self.assertEqual(re.findall(r'data\.read(\w+)\(', listener), ['Int', 'Int', 'TypedObject', 'Int', 'Int', 'Int'])

    def test_the_edge_window_follows_homes_contract_key_for_key(self):
        import json
        contract = json.loads((ROOT/'qa/oxblood/oxblood-contract.json').read_text())['homeEdge']
        layout = (FALLBACK/'RoseEdgeLayout.java').read_text()
        def const(name):
            return float(re.search(rf'\b{name} = ([0-9.]+)', layout).group(1))
        self.assertEqual((const('EDGE_HEIGHT_DP'), const('FLAT_TOP_DP'), const('FILLET_DP')),
            (contract['height'], contract['flatTop'], contract['concaveFilletRadius']))
        controls = contract['controls']
        self.assertEqual((const('ROSE_DP'), const('CONTROL_HEIGHT_DP')), tuple(controls['rose']['hit']))
        self.assertEqual(const('SWITCHER_DP'), controls['switcher']['hit'][0])
        self.assertEqual((const('MIC_DP'), const('CONTROL_HEIGHT_DP')), tuple(controls['microphone']['hit']))
        self.assertIn('gap 14', controls['layout'])
        self.assertIn('centre 29dp above the edge bottom', controls['layout'])
        self.assertEqual((const('GAP_DP'), const('CONTROL_CENTRE_DP')), (14, 29))
        self.assertEqual((const('WELL_HEIGHT_DP'), const('WELL_RADIUS_DP')),
            (contract['well']['plateHeight'], contract['well']['radius']))
        window = (FALLBACK/'RoseEdgeWindow.java').read_text()
        for color in ('0xFF4A141A', '0xFF3C0E13', '0xFF330C10', '0xA6D65468', '0x66ECCDBE', '0xFFEADBCD', '0xFF331019'):
            self.assertIn(color, window)
        # The edge's lift for the resize is the contract's height less its flat top (58dp).
        self.assertEqual(contract['height'] - contract['flatTop'], 58)

    def test_reasons_match_the_record(self):
        record = (AIDL/'com/android/wm/shell/roseedge/IRoseEdgeListener.aidl').read_text()
        java = self.shell()
        for n, name in re.findall(r'(\d) ([A-Z_]{4,})', record):
            prefix = 'REFUSED_' if name in ('NOT_TOP_TASK', 'NOT_RESIZEABLE', 'LOCKED', 'ROTATION', 'MULTI_WINDOW',
                'BUSY', 'LIFT_RANGE', 'DISABLED', 'NOT_DEFAULT_DISPLAY') else 'LOWERED_'
            self.assertRegex(java, rf'{prefix}{name} = {n}\b', f'{prefix}{name}')
        # OS Core's agreed values (#410 comment 6621890904).
        refused = dict(re.findall(r'REFUSED_(\w+) = (\d+)', java))
        lowered = dict(re.findall(r'LOWERED_(\w+) = (\d+)', java))
        self.assertEqual(refused, {'NOT_TOP_TASK': '1', 'NOT_RESIZEABLE': '2', 'LOCKED': '3', 'ROTATION': '4',
            'MULTI_WINDOW': '5', 'BUSY': '6', 'LIFT_RANGE': '7', 'DISABLED': '8', 'NOT_DEFAULT_DISPLAY': '9'})
        self.assertEqual(lowered, {'REQUESTED': '0', 'TASK_CHANGED': '1', 'HOME_OR_RECENTS': '2', 'KEYGUARD': '3',
            'DISPLAY_CHANGED': '4', 'IME_SHOWN': '5', 'LISTENER_DIED': '6', 'USER_SWITCH': '7'})

    def test_the_launcher3_wiring_patch_applies_to_the_pinned_files(self):
        """rose-edge-resize-launcher3.patch (SystemUiProxy hands RoseEdgeShell the shell's binder; TouchInteractionService
        starts RoseEdgeWindow behind the switch) applies with plain git apply to pinned Launcher3's own blobs, which the
        switcher and E2a patches do not touch."""
        import gzip, shutil, subprocess, tempfile
        fixtures = STAGED/'fixtures/packages/apps/Launcher3'
        listing = gzip.decompress((ROOT/'os/tests/fixtures/launcher3-compile-contract/trees/'
            'packages__apps__Launcher3.ls-tree.txt.gz').read_bytes()).decode()
        pinned = {line.split('\t')[1]: line.split()[2] for line in listing.splitlines()}
        for path in sorted(fixtures.rglob('*.kt')):
            relative = str(path.relative_to(fixtures))
            data = path.read_bytes()
            self.assertEqual(hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest(), pinned[relative], relative)
        patch = WIRING
        touched = sorted(re.findall(r'^\+\+\+ b/(\S+)', patch.read_text(), re.M))
        self.assertEqual(touched, ['quickstep/src/com/android/quickstep/SystemUiProxy.kt',
            'quickstep/src/com/android/quickstep/TouchInteractionService.kt'])
        for other in ('rose-switcher-launcher3.patch', 'rose-edge-launcher3.patch'):
            text = (ROOT/'os/patches'/other).read_text()
            for path in touched:
                self.assertNotIn('b/' + path, text, f'{other} touches {path}')
        work = Path(tempfile.mkdtemp(prefix='rose-edge-wiring-'))
        self.addCleanup(shutil.rmtree, work, True)
        shutil.copytree(fixtures, work, dirs_exist_ok=True)
        subprocess.run(['git', 'init', '-q', str(work)], check=True)
        for step in (['apply', '--check'], ['apply']):
            subprocess.run(['git', '-C', str(work), *step, str(patch)], check=True)
        # Each file's index line names the blobs the patch really goes from and to (the contract checks this too).
        for before, after, path in re.findall(r'^index ([0-9a-f]+)\.\.([0-9a-f]+) \d+\n--- a/\S+\n\+\+\+ b/(\S+)',
                patch.read_text(), re.M):
            self.assertTrue(pinned[path].startswith(before), path)
            data = (work/path).read_bytes()
            self.assertTrue(hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest().startswith(after),
                f'{path}: the index line is stale; regenerate the patch with git diff')
        system_ui = (work/'quickstep/src/com/android/quickstep/SystemUiProxy.kt').read_text()
        self.assertIn('RoseEdgeShell.setShell(\n            params.getBinder(com.android.quickstep.fallback.RoseEdgeShell.DESCRIPTOR)',
            system_ui)
        service = (work/'quickstep/src/com/android/quickstep/TouchInteractionService.kt').read_text()
        self.assertIn('if (com.android.quickstep.fallback.RoseEdgeSwipe.switchOn())', service)
        self.assertIn('OverviewCommandHelper.CommandType.TOGGLE', service)
        self.assertIn('roseEdge?.stop()', service)

    def test_the_shells_merged_aidl_and_bundle_key_match_the_launcher3_side(self):
        # The hand-off is a bundle key: Launcher3 reads params.getBinder(RoseEdgeShell.DESCRIPTOR) and the shell
        # registers addExternalInterface(IRoseEdge.DESCRIPTOR, ...). Neither compile proves they agree; this does.
        patch = (ROOT/'os/patches/rose-edge-wmshell.patch').read_text()
        added = {}
        for block in re.split(r'^diff --git ', patch, flags=re.M)[1:]:
            path = re.match(r'a/(\S+) b/', block).group(1)
            added[path] = ''.join(l[1:] + '\n' for l in block.split('\n') if l.startswith('+') and not l.startswith('+++'))
        def code(text):
            text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
            text = re.sub(r'//[^\n]*', '', text)
            return [l.strip() for l in text.splitlines() if l.strip()]
        shell = (FALLBACK/'RoseEdgeShell.java').read_text()
        for name, constant in (('IRoseEdge', 'DESCRIPTOR'), ('IRoseEdgeListener', 'LISTENER_DESCRIPTOR')):
            aidl = added['libs/WindowManager/Shell/src/com/android/wm/shell/roseedge/' + name + '.aidl']
            self.assertEqual(code(aidl), code((AIDL/f'com/android/wm/shell/roseedge/{name}.aidl').read_text()),
                f'{name}: the merged shell AIDL is the interface record, line for line')
            package = re.search(r'^package ([\w.]+);', aidl, re.M).group(1)
            self.assertIn(f'{constant} = "{package}.{name}";', shell)
        self.assertIn('addExternalInterface(IRoseEdge.DESCRIPTOR,', patch)
        wiring = WIRING.read_text()
        self.assertIn('params.getBinder(com.android.quickstep.fallback.RoseEdgeShell.DESCRIPTOR)', wiring)

    def test_the_patcher_registers_both_edge_patches_with_the_switch(self):
        # #410 (Chief): the normal OS build compiles them; the switch is the existing persist.wm.debug property, which
        # every app domain may read (pinned sepolicy private/app.te: get_prop(appdomain, persist_wm_debug_prop)).
        patcher = (ROOT/'scripts/apply-os-changes.py').read_text()
        block = patcher[patcher.index('if a.background_policy:'):patcher.index('preinstalls = preinstall_plan(')]
        self.assertLess(block.index("'rose-switcher-launcher3.patch'"), block.index("'rose-edge-launcher3.patch'"))
        self.assertLess(block.index("'rose-edge-launcher3.patch'"), block.index("'rose-edge-resize-launcher3.patch'"))
        self.assertIn("projects['frameworks/base'].append('rose-edge-wmshell.patch')", block)
        self.assertIn("if a.agent_tray:projects['build/make'].append('rose-edge-build-make.patch')", block)
        switch = (ROOT/'os/patches/rose-edge-build-make.patch').read_text()
        added = [l[1:] for l in switch.splitlines() if l.startswith('+') and not l.startswith('+++')]
        self.assertEqual([l for l in added if not l.startswith('#') and l.strip()],
            ['PRODUCT_SYSTEM_PROPERTIES += persist.wm.debug.rose_edge=true'], 'one property, nothing else')
        # Host reconcile treats a file an earlier integration already changed, and no receipt entry owns, as an owner
        # change and refuses (frankel run 41857685135 on generic_system.mk). So the switch lives in a product file no
        # other os/patches patch touches, which is pristine at HEAD on every tree; handheld_system.mk is inherited by
        # generic_system.mk on every Pixel 10 device (os/tests/test_device_contract.py).
        target = re.findall(r'^\+\+\+ b/(\S+)', switch, re.M)
        self.assertEqual(target, ['target/product/handheld_system.mk'])
        for other in sorted((ROOT/'os/patches').glob('*.patch')):
            if other.name != 'rose-edge-build-make.patch':
                self.assertNotIn('b/target/product/handheld_system.mk', other.read_text(errors='replace'), other.name)
        for path in ('scripts/apply-os-changes.py', 'scripts/background_apks.py', 'scripts/integration_copy.py'):
            self.assertNotIn('os/staged', (ROOT/path).read_text(), f'{path} never reads os/staged')
        # #410 PM-NATIVE-EDGE-GESTURE-002 replaces E2a's release-only rules with the staged gesture (RoseEdgeGesture's
        # stages, os/tests/RoseEdgeGestureTest.java), still gated on an edge window and the shell.
        self.assertIn('public static int stage(int previous, float upDp, boolean raisedAtStart)',
            (FALLBACK/'RoseEdgeGesture.java').read_text())
        self.assertIn('!RoseEdgeState.hasListener() ? "no edge window"', (FALLBACK/'RoseEdgeSwipe.java').read_text())
        self.assertIn('!RoseEdgeShell.available() ? "shell unavailable"', (FALLBACK/'RoseEdgeSwipe.java').read_text())
        self.assertFalse((STAGED/'overlays').exists() or (STAGED/'patches').exists(), 'nothing is left staged')


    def test_direct_conversation_alias_is_guarded_and_keeps_the_existing_task(self):
        import xml.etree.ElementTree as ET
        a = '{http://schemas.android.com/apk/res/android}'
        manifest = ET.parse(ROOT/'workspace/rose-agent/app/AndroidManifest.xml').getroot()
        app = manifest.find('application')
        alias = next(e for e in app.findall('activity-alias') if e.get(a+'name') == '.RoseTrayEntry')
        self.assertEqual(alias.get(a+'targetActivity'), '.RoseConversationActivity')
        self.assertEqual(alias.get(a+'permission'), 'android.permission.INTERNAL_SYSTEM_WINDOW')
        self.assertEqual(alias.get(a+'exported'), 'true')
        self.assertEqual(len(alias), 0, 'not an implicit or browsable route')
        activity = next(e for e in app.findall('activity') if e.get(a+'name') == alias.get(a+'targetActivity'))
        self.assertEqual(activity.get(a+'exported'), 'false')
        self.assertEqual(activity.get(a+'launchMode'), 'singleTask', 're-entry reuses the conversation')
        self.assertEqual(activity.get(a+'taskAffinity'), 'dev.pixel.voice.conversation')
        self.assertEqual(activity.get(a+'excludeFromRecents'), 'true')
        launcher = ET.parse(ROOT/'os/tests/fixtures/rose-switcher/Launcher3-quickstep-AndroidManifest.xml').getroot()
        self.assertIn(alias.get(a+'permission'), [e.get(a+'name') for e in launcher.findall('uses-permission')],
            'Launcher3 already holds the alias permission; no new grant')
        wiring = WIRING.read_text()
        self.assertIn('RoseEdgeEntry.open(\n+                                    this@TouchInteractionService', wiring)
        self.assertNotIn('startActivity(', wiring, 'the host must use the failure-handling entry')

    def test_production_entry_and_exact_return_with_missing_and_denied_routes(self):
        '''Execute the real entry helper and unchanged return policy against narrow API fakes, not a phone runtime.'''
        import shutil, subprocess, tempfile
        with tempfile.TemporaryDirectory(prefix='rose-tray-entry-') as folder:
            work = Path(folder)
            sources = {
                'android/content/Intent.java': r'''package android.content;
public class Intent {
 public static final int FLAG_ACTIVITY_NEW_TASK = 0x10000000;
 public String pkg, component; public int flags;
 public Intent setClassName(String p, String c) { pkg=p; component=c; return this; }
 public Intent addFlags(int f) { flags |= f; return this; }
}''',
                'android/content/Context.java': r'''package android.content;
public class Context { public void startActivity(Intent intent) {} }''',
                'android/content/ActivityNotFoundException.java': r'''package android.content;
public class ActivityNotFoundException extends RuntimeException {}''',
                'android/widget/Toast.java': r'''package android.widget;
public class Toast {
 public static final int LENGTH_LONG = 1; public static int shown;
 public static Toast makeText(android.content.Context c, String text, int length) { return new Toast(); }
 public void show() { shown++; }
}''',
                'com/android/quickstep/fallback/RoseEdgeEntryTest.java': r'''package com.android.quickstep.fallback;
import android.content.*;
import android.widget.Toast;
import com.android.wm.shell.roseedge.RoseEdgePolicy.Return;
public class RoseEdgeEntryTest {
 static int checks;
 static void expect(boolean value, String reason) { checks++; if (!value) throw new AssertionError(reason); }
 static class Host extends Context {
  Intent opened; int calls; int failure;
  public void startActivity(Intent intent) {
   calls++;
   if (failure == 1) throw new ActivityNotFoundException();
   if (failure == 2) throw new SecurityException();
   opened = intent;
  }
 }
 public static void main(String[] args) {
  Host host = new Host();
  RoseEdgeEntry.open(host);
  expect(host.calls == 1 && host.opened != null, "one direct launch");
  expect(host.opened.pkg.equals("dev.pixel.voice"), "explicit Rose package");
  expect(host.opened.component.equals("dev.pixel.voice.RoseTrayEntry"), "alias, never MainActivity");
  expect(host.opened.flags == Intent.FLAG_ACTIVITY_NEW_TASK, "no clear/reset/history flags");
  expect(Toast.shown == 0, "success has no failure notice");
  // These source tasks have stable container identity; entry never creates or relaunches them.
  Return.Task mail = new Return.Task(7, 0, 0, "mail", true, true, new Object());
  Return.Task maps = new Return.Task(8, 0, 0, "maps", true, true, new Object());
  Return.Task rose = new Return.Task(9, 0, 0, host.opened.pkg, true, true, new Object());
  Return.Task home = new Return.Task(10, 0, 0, "dev.pixel.voice", false, true, new Object());
  Return old = new Return(() -> 41L);
  old.onFocusChanged(mail, 0); old.onFocusChanged(home, 0); old.onFocusChanged(rose, 0);
  expect(old.target() == null, "regression: the old app -> Home -> Rose path loses the source task");
  Return current = new Return(new java.util.concurrent.atomic.AtomicLong(42)::getAndIncrement);
  current.onFocusChanged(mail, 0); current.onFocusChanged(rose, 0);
  expect(current.target() == mail, "direct conversation keeps the exact source task");
  expect(current.check(current.token(), false, rose, mail, 0) == Return.GO, "approved return can reorder it");
  current.onFocusChanged(rose, 0);
  expect(current.target() == mail, "repeated entry keeps the source task");
  current.onFocusChanged(maps, 0); current.onFocusChanged(rose, 0);
  expect(current.target() == maps, "newer app supersedes the old destination");
  current.onTaskVanished(8);
  expect(current.target() == null, "closed source never relaunches");
  current.onFocusChanged(mail, 0); current.onFocusChanged(home, 0); current.onFocusChanged(rose, 0);
  expect(current.target() == null, "actual Home still clears the target");
  for (int failure = 1; failure <= 2; failure++) {
   host = new Host(); host.failure = failure; int notices = Toast.shown;
   RoseEdgeEntry.open(host);
   expect(host.calls == 1 && host.opened == null, "missing/disabled/denied alias has no fallback launch");
   expect(Toast.shown == notices + 1, "failure is visible");
  }
  System.out.println("RoseEdgeEntryTest: " + checks + " checks passed");
 }
}''',
            }
            patch = (ROOT/'os/patches/rose-edge-wmshell.patch').read_text()
            block = next(b for b in re.split(r'^diff --git ', patch, flags=re.M)[1:]
                if b.startswith('a/libs/WindowManager/Shell/src/com/android/wm/shell/roseedge/RoseEdgePolicy.java '))
            sources['com/android/wm/shell/roseedge/RoseEdgePolicy.java'] = '\n'.join(
                line[1:] for line in block.splitlines() if line.startswith('+') and not line.startswith('+++')) + '\n'
            sources['com/android/quickstep/fallback/RoseEdgeEntry.java'] = (FALLBACK/'RoseEdgeEntry.java').read_text()
            for name, source in sources.items():
                path = work/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            compiled = subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-d', str(work/'classes'),
                *[str(work/name) for name in sources]], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            run = subprocess.run(['java', '-cp', str(work/'classes'),
                'com.android.quickstep.fallback.RoseEdgeEntryTest'], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('RoseEdgeEntryTest: 16 checks passed', run.stdout)

if __name__ == '__main__':
    unittest.main()
