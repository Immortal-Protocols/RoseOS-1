"""Source checks for the SystemUI tray <-> Rose Agent boundary; no device or Android build.

The tray ships in the OS image and AgentTrayService ships in Rose Agent, which can now be
updated on its own. These checks keep both sides of the Messenger protocol and the copied
voice-glow shader in step."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT/'os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell'
APP = ROOT/'workspace/rose-agent/app/src/dev/pixel/voice'

def shader(path):
    """Shader statements from a Java text block (OS) or concatenated literals (app, Java 8),
    with comments and whitespace normalized; formatting differences do not change the shader."""
    text = path.read_text()
    block = re.search(r'SOURCE\s*=\s*"""(.*?)""";', text, re.S)
    if block:
        source = block.group(1)
    else:
        literals = re.search(r'SOURCE\s*=\s*((?:\s*"(?:[^"\\]|\\.)*"\s*\+?)+);', text)
        assert literals, f'no shader SOURCE in {path}'
        source = ''.join(part.encode().decode('unicode_escape')
            for part in re.findall(r'"((?:[^"\\]|\\.)*)"', literals.group(1)))
    lines = (re.sub(r'\s+', ' ', line).strip() for line in source.split('\n'))
    return [line for line in lines if line and not line.startswith('//')]

class TrayContractTest(unittest.TestCase):
    def setUp(self):
        self.service = (APP/'AgentTrayService.java').read_text()
        self.tray = (SHELL/'AgentTrayPrototype.java').read_text()
        self.controls = (SHELL/'AgentStatusControls.java').read_text()

    def test_message_codes_match(self):
        codes = dict((name, int(value)) for name, value in
            re.findall(r'\b(REGISTER|UNREGISTER|START|MUTE|UNMUTE|END|STATE)=(\d+)', self.service))
        self.assertEqual(codes, {'REGISTER': 1, 'UNREGISTER': 2, 'START': 3, 'MUTE': 4,
            'UNMUTE': 5, 'END': 6, 'STATE': 100})
        # The tray registers on connect, unregisters on user switch and accepts only STATE replies.
        self.assertIn('send(1);', self.tray)
        self.assertIn('send(2);', self.tray)
        self.assertIn('message.what != 100', self.tray)
        # Tap commands the tray can send are exactly START/MUTE/UNMUTE/END.
        self.assertIn('return !active ? 3 : tapped == 1 ? 6 : muted ? 5 : 4;', self.controls)

    def test_protocol_version_and_state_keys_match(self):
        self.assertIn('b.putInt("protocolVersion",1)', self.service)
        self.assertIn('data.getInt("protocolVersion") != 1', self.tray)
        for key in ['listening', 'muted', 'connected', 'status', 'inputLevel', 'outputLevel', 'audioObservedAt']:
            self.assertRegex(self.service, r'b\.put\w+\("%s"' % key, key)
            self.assertIn('"%s"' % key, self.tray, key)

    def test_both_sides_authenticate_the_peer(self):
        self.assertIn('android:name=".AgentTrayService" android:exported="true" android:permission="android.permission.STATUS_BAR_SERVICE"',
            (ROOT/'workspace/rose-agent/app/AndroidManifest.xml').read_text())
        self.assertIn('"com.android.systemui"', self.service)
        self.assertIn('getPackageUidAsUser(\n                    "dev.pixel.voice", user)', self.tray)

    def test_tray_keeps_at_most_one_pending_rebind(self):
        # Every failed bind, binding death and screen/user broadcast used to start its own 3 s chain.
        self.assertNotIn('postDelayed(this::bind', self.tray)
        self.assertEqual(self.tray.count('handler.postDelayed(retryBind, 3000);'), 1)
        schedule = self.tray[self.tray.index('private void scheduleBind()'):]
        self.assertLess(schedule.index('handler.removeCallbacks(retryBind);'),
            schedule.index('handler.postDelayed(retryBind, 3000);'))
        bind = self.tray[self.tray.index('private void bind()'):]
        self.assertTrue(bind.split('\n')[1].strip() == 'handler.removeCallbacks(retryBind);')

    def test_app_glow_shader_matches_the_os_copy(self):
        self.assertEqual(shader(APP/'StatusVoiceGlow.java'), shader(SHELL/'StatusVoiceGlow.java'))

if __name__ == '__main__':
    unittest.main()
