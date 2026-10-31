"""Source checks that the PiP controller wires AnyAppControl into the real session paths; no device needed.
These are static assertions about Controller.java; the behaviour itself is AnyAppControlTest and, on a phone,
only an OS build whose config_pixelComputerControlPrototypeAgents pins this controller."""
from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol/Controller.java').read_text()

def method(signature):
    start = SOURCE.index(signature)
    depth, i = 0, SOURCE.index('{', start)
    while True:
        depth += {'{': 1, '}': -1}.get(SOURCE[i], 0)
        if depth == 0:return SOURCE[start:i + 1]
        i += 1

class AnyAppWiringTest(unittest.TestCase):
    def test_capability_comes_only_from_the_platform_pin_and_this_package_signers(self):
        probe = method('static boolean anyAppControl(Context c)')
        self.assertIn('getIdentifier("config_pixelComputerControlPrototypeAgents","array","android")', probe)
        self.assertIn('getPackageInfo(c.getPackageName(),PackageManager.GET_SIGNING_CERTIFICATES)', probe)
        self.assertIn('return AnyAppControl.pinned(c.getPackageName(),digests,pins);', probe)
        self.assertIn('catch(Throwable e){return false;}', probe, 'an unreadable pin never claims the capability')
        self.assertIn('anyApp=anyAppControl(c);', method('static synchronized JSONObject start(Context context,JSONObject p)'))

    def test_a_refused_target_is_typed_not_generic(self):
        start = method('static synchronized JSONObject start(Context context,JSONObject p)')
        self.assertIn('status=AnyAppControl.targetRefused(error)?"target_not_allowed":"failed";', start)

    def test_inspection_and_actions_use_the_same_package_rule(self):
        walk, node = method('static void walk('), method('static AccessibilityNodeInfo node(JSONObject p)')
        for body in (walk, node):
            self.assertIn('AnyAppControl.actionable(n.getPackageName()==null?"":n.getPackageName().toString(),target,anyApp)', body)
            self.assertNotIn('target.contentEquals(', body, 'no second, target-only rule remains')
        self.assertIn('.put("packageName",String.valueOf(n.getPackageName()))', walk)

    def test_state_reports_the_capability_and_the_app_in_front(self):
        self.assertIn('.put("anyAppControl",anyApp)', method('static synchronized JSONObject state(Context c)'))
        base = method('static JSONObject base()')
        self.assertIn('.put("observedPackage",observed==null?target:observed)', base)
        inspect = method('static JSONObject inspect()')
        self.assertIn('w.getType()==AccessibilityWindowInfo.TYPE_APPLICATION', inspect)
        # The session's own windows only: nothing reads another display.
        self.assertIn('session.getClass().getMethod("getAccessibilityWindows").invoke(session)', inspect)

if __name__ == '__main__':
    unittest.main()
