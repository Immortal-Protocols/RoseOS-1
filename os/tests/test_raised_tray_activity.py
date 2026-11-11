"""Offline producer projection + Launcher lifecycle. No Android build/device claims."""
import pathlib
import subprocess
import tempfile
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
APP=ROOT/'workspace/rose-agent/app'
SHELL=ROOT/'os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell'
LAUNCHER=ROOT/'os/overlays/packages/apps/Launcher3/quickstep/src/com/android/quickstep/fallback'
class RaisedTrayActivityTest(unittest.TestCase):
 def test_real_models(self):
  sources=[APP/'src/dev/pixel/voice'/name for name in ['TrayBackgroundActivity.java','WorkspaceOverviewFeed.java','WorkspaceOverview.java','MiniJson.java']]
  sources += [APP/'tests/TrayBackgroundActivityTest.java',LAUNCHER/'RoseBackgroundActivity.java',ROOT/'os/tests/RoseBackgroundActivityTest.java',SHELL/'AgentStatusControls.java',ROOT/'os/tests/TrayActivityMirrorTest.java']
  with tempfile.TemporaryDirectory() as out:
   subprocess.run(['java','-m','jdk.compiler/com.sun.tools.javac.Main','-d',out,*map(str,sources)],check=True)
   for cls in ['dev.pixel.voice.TrayBackgroundActivityTest','com.android.quickstep.fallback.RoseBackgroundActivityTest','com.android.wm.shell.TrayActivityMirrorTest']:
    subprocess.run(['java','-cp',out,cls],check=True)
 def test_transport_and_drawing_seams(self):
  app=(APP/'src/dev/pixel/voice/AgentTrayService.java').read_text()
  shell=(SHELL/'AgentTrayPrototype.java').read_text()
  view=(LAUNCHER/'RoseEdgeWindow.java').read_text()
  self.assertIn('b.putString("backgroundActivity",backgroundActivity.state',app)
  self.assertIn('+b.getString("backgroundActivity")',app)
  self.assertIn('!this.link.subscribed() || !trusted(message.sendingUid)',shell)
  self.assertIn('mirrorBackgroundActivity(data.getString("backgroundActivity"))',shell)
  self.assertIn('private void clearState() {\n        mirrorBackgroundActivity(null);',shell)
  self.assertIn('theme.start();\n        mirrorBackgroundActivity(null);',shell)
  self.assertIn('until - now <= 3000',(SHELL/'AgentStatusControls.java').read_text())
  self.assertIn('Settings.Global.BOOT_COUNT',shell)
  self.assertIn('ValueAnimator.areAnimatorsEnabled()',view)
  self.assertIn('top - 3 * s + shift * mDensity',view)
  self.assertIn('unregisterContentObserver(mActivityObserver)',view)
  self.assertNotIn('setTranslationY',view[view.index('private void drawSwitcherGlyph'):view.index('private Path card')])
