"""#410 E2c (staged, inert): the interface record in os/staged/rose-edge-resize/aidl and Launcher3's hand-written binder
code agree exactly, and nothing applies the staged files. The opt-in Robolectric harness in
os/staged/rose-edge-resize/native-test runs the same code against the aidl tool's generated Stub and Proxy; this check
needs no SDK so that CI keeps the two from drifting. Source only: no build, device or host."""
from pathlib import Path
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
            ('IRoseEdgeListener', 'listener')], 1), ('lower', [('int', 'displayId')], 2)])
        listener = methods(AIDL/'com/android/wm/shell/roseedge/IRoseEdgeListener.aidl')
        self.assertEqual(listener, [('onRaised', [('int', 'displayId'), ('int', 'taskId'), ('Rect', 'appBounds')], 1),
            ('onRefused', [('int', 'displayId'), ('int', 'taskId'), ('int', 'reason')], 2),
            ('onLowered', [('int', 'displayId'), ('int', 'reason')], 3)])
        self.assertEqual([m[0] for m in methods(AIDL/'dev/pixel/voice/edge/IRoseEdgeControls.aidl')], ['attach', 'detach'])
        self.assertEqual([m[0] for m in methods(AIDL/'dev/pixel/voice/edge/IRoseEdgeHost.aidl')],
            ['onPanel', 'lower', 'goHome', 'showSwitcher'])
        for path in AIDL.rglob('*.aidl'):
            self.assertRegex(path.read_text(), r'oneway (interface|void)', f'{path.name}: every call is one-way')

    def test_the_hand_written_client_uses_the_records_descriptors_ids_and_order(self):
        java = self.shell()
        self.assertIn('DESCRIPTOR = "com.android.wm.shell.roseedge.IRoseEdge"', java)
        self.assertIn('LISTENER_DESCRIPTOR = "com.android.wm.shell.roseedge.IRoseEdgeListener"', java)
        ids = {name: int(n) for name, n in re.findall(r'(TRANSACTION_\w+) = IBinder\.FIRST_CALL_TRANSACTION \+ (\d+)', java)}
        self.assertEqual(ids, {'TRANSACTION_RAISE': 1, 'TRANSACTION_LOWER': 2, 'TRANSACTION_ON_RAISED': 1,
            'TRANSACTION_ON_REFUSED': 2, 'TRANSACTION_ON_LOWERED': 3})
        raise_body = java[java.index('public static boolean raise('):java.index('public static boolean lower(')]
        self.assertEqual(re.findall(r'data\.write(\w+)\((\w+)', raise_body), [('InterfaceToken', 'DESCRIPTOR'),
            ('Int', 'displayId'), ('Int', 'taskId'), ('Int', 'liftPx'), ('StrongBinder', 'new')])
        self.assertIn('IBinder.FLAG_ONEWAY', raise_body)
        listener = java[java.index('static final class Listener'):]
        self.assertEqual(re.findall(r'data\.read(\w+)\(', listener), ['Int', 'Int', 'TypedObject', 'Int', 'Int', 'Int'])

    def test_reasons_match_the_record(self):
        record = (AIDL/'com/android/wm/shell/roseedge/IRoseEdgeListener.aidl').read_text()
        java = self.shell()
        for n, name in re.findall(r'(\d) ([A-Z_]{4,})', record):
            prefix = 'REFUSED_' if name in ('NOT_TOP_TASK', 'NOT_RESIZEABLE', 'LOCKED', 'ROTATION', 'MULTI_WINDOW',
                'BUSY', 'DISABLED') else 'LOWERED_'
            self.assertRegex(java, rf'{prefix}{name} = {n}\b', f'{prefix}{name}')

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
