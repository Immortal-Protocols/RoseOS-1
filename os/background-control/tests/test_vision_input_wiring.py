"""The new vision input shares the live-session/frame guards, never accessibility targets."""
from pathlib import Path
import unittest

SRC = Path(__file__).resolve().parents[1] / 'agent-pip/src/dev/pixel/backgroundcontrol'


class VisionInputWiring(unittest.TestCase):
    def test_typing_uses_only_a_consumed_screenshot_from_its_own_slot(self):
        source = (SRC / 'Controller.java').read_text()
        branch = source.split('else if(action.equals("screen_text")){', 1)[1].split('else if(action.equals("set_text"))', 1)[0]
        for forbidden in ('node(', 'inspect(', 'isEditable', 'performAction', 'ACTION_SET_TEXT'):
            self.assertNotIn(forbidden, branch)
        self.assertLess(source.index('require(p);String action'), source.index('else if(action.equals("screen_text"))'))
        self.assertLess(source.index('fresh(p);if(operations>=200)'), source.index('else if(action.equals("screen_text"))'))
        self.assertIn('visualSnapshotId==null||!visualSnapshotId.equals(snapshotId)', branch)
        self.assertLess(branch.index('clearNodes();'), branch.index('VisionInput.insert('))
        # The input focus the text lands in is checked first: never a password field, never nothing focused.
        self.assertLess(branch.index('clearNodes();'), branch.index('visionFocus();'))
        self.assertLess(branch.index('visionFocus();'), branch.index('VisionInput.insert('))
        focus = source.split('static void visionFocus()', 1)[1].split('\n }\n', 1)[0]
        self.assertIn('findFocus(AccessibilityNodeInfo.FOCUS_INPUT)', focus)
        self.assertIn('VisionInput.refusal(found,editable,password)', focus)
        self.assertNotIn('performAction', focus)
        self.assertIn('s.visualSnapshotId=visualSnapshotId;', source)
        self.assertIn('visualSnapshotId=s.visualSnapshotId;', source)
        self.assertIn('snapshotId=null;visualSnapshotId=null;', source)
        capture = source.split('static JSONObject capture()', 1)[1].split('static JSONObject inspect()', 1)[0]
        self.assertIn('visualSnapshotId=snapshotId;', capture)
        inspect = source.split('static JSONObject inspect()', 1)[1]
        self.assertNotIn('visualSnapshotId=snapshotId;', inspect)
        self.assertIn('.put("visionText",true)', (SRC / 'AgentProvider.java').read_text())


if __name__ == '__main__':
    unittest.main()
