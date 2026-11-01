"""The controller's ongoing session notification describes what tapping it does now (#330): the viewer opens full
screen, and closing or leaving it does not stop the work. Source checks only; no device needed."""
from pathlib import Path
import re
import unittest

CONTROLLER = (Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol/Controller.java').read_text()


class NotificationWordingTest(unittest.TestCase):
    def live(self):
        return next(l for l in CONTROLLER.splitlines() if 'new Notification.Builder(c,"background-live")' in l and 'setContentIntent(view)' in l)

    def test_tap_text_matches_the_full_screen_viewer(self):
        text = re.search(r'\.setContentText\("([^"]*)"\)\.setContentIntent\(view\)', self.live()).group(1)
        self.assertEqual(text, 'Tap to watch full screen. Rose keeps working in the background.')
        self.assertNotIn('floating', self.live().lower(), 'the notification no longer promises a floating window')
        # Watching is not stopping: the text never suggests the tap pauses, ends or takes over the work.
        for word in ('stop', 'pause', 'end', 'take over'):
            self.assertNotIn(word, text.lower())

    def test_tap_still_opens_the_viewer_and_stop_stays_the_only_action(self):
        self.assertIn('PendingIntent view=PendingIntent.getActivity(c,1,new Intent(c,AgentPipActivity.class)', CONTROLLER)
        live = self.live()
        self.assertEqual(re.findall(r'new Notification\.Action\.Builder\(null,"(\w+)",(\w+)\)', live), [('Stop', 'stop')])
        self.assertIn('.setOngoing(true)', live)

    def test_lock_screen_version_stays_private(self):
        self.assertIn('nb.setVisibility(Notification.VISIBILITY_PRIVATE).setPublicVersion(', CONTROLLER)
        self.assertIn('.setContentTitle("Rose Agent is using an app").setContentText("Unlock to see or stop it")', CONTROLLER)


class ControlScreenWordingTest(unittest.TestCase):
    """The control screen's viewer button opens the same unbound viewer, which is full screen while automatic
    picture-in-picture is off (ViewerPresentation.AUTOMATIC_PIP = false), so it no longer offers a floating view."""
    MAIN = (Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol/MainActivity.java').read_text()
    PRESENTATION = (Path(__file__).resolve().parents[1]/'agent-pip/src/dev/pixel/backgroundcontrol/ViewerPresentation.java').read_text()

    def test_button_says_what_it_opens(self):
        self.assertIn('static final boolean AUTOMATIC_PIP = false;', self.PRESENTATION)
        self.assertIn('viewer.setText("Watch agent view full screen");viewer.setOnClickListener(v->{try{startActivity('
                      'new android.content.Intent(this,AgentPipActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK));}'
                      'catch(RuntimeException e){notice.failed("Agent view",e);show();}});', self.MAIN)
        self.assertNotIn('floating', self.MAIN.lower())

if __name__ == '__main__':
    unittest.main()
