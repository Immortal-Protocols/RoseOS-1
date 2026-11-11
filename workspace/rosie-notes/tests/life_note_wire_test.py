"""Source boundary checks only; not an Android build or native/runtime acceptance."""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / 'workspace/rosie-notes'
SRC = APP / 'src/dev/pixel/rosienotes'
ANDROID = '{http://schemas.android.com/apk/res/android}'


class WireTests(unittest.TestCase):
    def test_separate_launchable_package_has_no_permissions(self):
        manifest = ET.parse(APP / 'AndroidManifest.xml').getroot()
        self.assertEqual(manifest.attrib['package'], 'dev.pixel.rosienotes')
        self.assertEqual(manifest.findall('uses-permission'), [])
        self.assertEqual(manifest.findall('permission'), [])
        app = manifest.find('application')
        self.assertEqual(app.attrib[ANDROID + 'allowBackup'], 'false')
        self.assertEqual(app.attrib[ANDROID + 'usesCleartextTraffic'], 'false')
        self.assertEqual(len(app.findall('activity')), 1)
        self.assertEqual(len(app.findall('provider')), 1)
        self.assertEqual(len(app.findall('service')) + len(app.findall('receiver')), 0)
        activity = app.find('activity')
        self.assertEqual(activity.find('intent-filter/category').attrib[ANDROID + 'name'], 'android.intent.category.LAUNCHER')
        self.assertEqual(app.find('provider').attrib[ANDROID + 'authorities'], 'dev.pixel.rosienotes.agent')

    def test_provider_authenticates_before_parsing_or_io(self):
        source = (SRC / 'AgentProvider.java').read_text()
        gate = source.index('checkSignatures(android.os.Process.myUid(), Binder.getCallingUid())')
        self.assertLess(gate, source.index('MiniJson.parse(raw)'))
        self.assertLess(gate, source.index('new LifeNoteStore('))
        self.assertIn('getNoBackupFilesDir()', source)
        self.assertIn('this::readyWorkspace', source)
        self.assertNotIn('receipts', source)
        self.assertNotIn('operationId)', source)
        self.assertIn('reply.putString("result", new JSONObject(result).toString())', source)

    def test_actual_controller_is_same_signer_and_exact_session(self):
        source = (SRC / 'AgentProvider.java').read_text()
        self.assertIn('WorkspaceBinding.CONTROLLER.equals(provider.packageName)', source)
        self.assertIn('pm.checkSignatures(getContext().getPackageName(), provider.packageName)', source)
        self.assertIn('new JSONObject().put("sessionId", workspace)', source)
        self.assertIn('"get_state", null, args', source)
        self.assertIn('SystemClock.elapsedRealtime() - before > 3000', source)
        self.assertNotIn('"start",', source)
        self.assertNotIn('"stop",', source)

    def test_no_owner_data_network_or_general_enumeration_route(self):
        all_source = '\n'.join(p.read_text() for p in SRC.glob('*.java'))
        for token in ('dev.pixel.notes', 'android.webkit', 'java.net.', 'HttpClient', 'getExternal', 'ACTION_VIEW', 'startActivity(', 'SharedPreferences', 'SettingsAgentProvider'):
            self.assertNotIn(token, all_source)
        provider = (SRC / 'AgentProvider.java').read_text()
        self.assertNotIn('localSnapshot', provider)
        self.assertNotIn('"tools"', provider)
        activity = (SRC / 'MainActivity.java').read_text()
        self.assertNotIn('getIntent', activity)
        self.assertNotIn('life_note_put', activity)
        self.assertIn('setLinksClickable(false)', activity)

    def test_existing_authenticated_transport_and_controller_policy_stay(self):
        transport = (ROOT / 'workspace/rose-agent/app/src/dev/pixel/voice/PhoneControl.java').read_text()
        self.assertIn('pm.checkSignatures(context.getPackageName(),pkg)', transport)
        self.assertIn('context.getContentResolver().call(', transport)
        self.assertIn('new JSONObject(result.getString("result"))', transport)
        home = (ROOT / 'os/background-control/agent-pip/src/dev/pixel/backgroundcontrol/HomeTarget.java').read_text()
        self.assertIn('dev.pixel.voice', home)
        self.assertNotIn('dev.pixel.rosienotes', home)
        settings = (ROOT / 'workspace/rose-agent/app/src/dev/pixel/voice/SettingsAgentProvider.java').read_text()
        self.assertNotIn('life_note_', settings)

    def test_verified_note_refreshes_only_the_visible_local_activity(self):
        provider = (SRC / 'AgentProvider.java').read_text()
        activity = (SRC / 'MainActivity.java').read_text()
        self.assertIn('"life_note_put".equals(method) && "verified".equals(result.get("status"))) MainActivity.notesChanged()', provider)
        self.assertIn('WeakReference<MainActivity>', activity)
        self.assertIn('active.get() == activity && !activity.stopped', activity)
        self.assertIn('if (!stopped && current == generation && !isFinishing())', activity)
        self.assertIn('reader.shutdownNow()', activity)
        self.assertNotIn('sendBroadcast', provider + activity)

    def test_unsigned_build_recipe_is_source_only(self):
        script = (APP / 'build.sh').read_text()
        self.assertIn('rosie-notes-unsigned.apk', script)
        for token in ('adb ', 'fastboot ', 'apksigner sign', 'gh ', 'curl ', 'gcloud ', 'ssh '):
            self.assertNotIn(token, script)


if __name__ == '__main__':
    unittest.main()
