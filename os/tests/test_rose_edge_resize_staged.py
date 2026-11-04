"""#410 E2c (staged, inert): the interface record in os/staged/rose-edge-resize/aidl and Launcher3's hand-written binder
code agree exactly, and nothing applies the staged files. The opt-in Robolectric harness in
os/staged/rose-edge-resize/native-test runs the same code against the aidl tool's generated Stub and Proxy; this check
needs no SDK so that CI keeps the two from drifting. Source only: no build, device or host."""
from pathlib import Path
import hashlib
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
STAGED = ROOT/'os/staged/rose-edge-resize'
AIDL = STAGED/'aidl'
FALLBACK = STAGED/'overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback'


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
        # Home's destination (#410 6623685186): Rose Agent's existing launch intent, no new extra or entry.
        self.assertNotIn('ROSE_EDGE_DESTINATION', every + (STAGED/'patches/rose-edge-resize-launcher3.patch').read_text())
        self.assertNotIn('Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY', every)
        window = (FALLBACK/'RoseEdgeWindow.java').read_text()
        self.assertIn('pm.getLaunchIntentForPackage(ROSE_PACKAGE)', window)
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
        patch = STAGED/'patches/rose-edge-resize-launcher3.patch'
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
        system_ui = (work/'quickstep/src/com/android/quickstep/SystemUiProxy.kt').read_text()
        self.assertIn('RoseEdgeShell.setShell(\n            params.getBinder(com.android.quickstep.fallback.RoseEdgeShell.DESCRIPTOR)',
            system_ui)
        service = (work/'quickstep/src/com/android/quickstep/TouchInteractionService.kt').read_text()
        self.assertIn('if (com.android.quickstep.fallback.RoseEdgeSwipe.switchOn())', service)
        self.assertIn('OverviewCommandHelper.CommandType.TOGGLE', service)
        self.assertIn('roseEdge?.stop()', service)

    def test_nothing_applies_the_staged_files(self):
        for path in ('scripts/apply-os-changes.py', 'scripts/background_apks.py', 'scripts/integration_copy.py',
                'scripts/launcher3-compile-contract.py'):
            self.assertNotIn('os/staged', (ROOT/path).read_text(), path)
        self.assertNotIn('staged', (ROOT/'scripts/apply-os-changes.py').read_text())
        # E2a stays the bound candidate; the staged client only extends it.
        e2a = ROOT/'os/overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback'
        self.assertFalse((e2a/'RoseEdgeShell.java').exists())
        self.assertIn('RoseEdgeState.hasListener()', (e2a/'RoseEdgeSwipe.java').read_text())
        self.assertIn('RoseEdgeState.canRaise()', (FALLBACK/'RoseEdgeSwipe.java').read_text())
        self.assertFalse((FALLBACK/'RoseEdgeGesture.java').exists(), 'the staged client reuses E2a RoseEdgeGesture')


if __name__ == '__main__':
    unittest.main()
