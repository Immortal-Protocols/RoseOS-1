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
        self.link = (SHELL/'AgentTrayLink.java').read_text()

    def test_the_tray_draws_nothing_at_rest(self):
        # #224: the idle grey lines across the status bar read as artifacts and are not in the HiFi design.
        waves = self.tray[self.tray.index('private final class Waves'):]
        for banned in ('drawRest', 'drawLine', '0xff888888'):
            self.assertNotIn(banned, waves, banned)
        self.assertIn('} else agentGlow.clear();', waves)
        self.assertIn('} else micGlow.clear();', waves)
        self.assertIn('if (active) {\n                agentGlow.draw(', waves)

    def test_connecting_is_drawn_and_animated_for_its_whole_span(self):
        # The service publishes STATE only on change, and Connecting has connected=false and listening=false. The
        # tray's active rule (AgentStatusControls.active, run by AgentStatusControlsTest) must cover the producer's
        # active statuses, and the glow must animate for the span it is drawn; animating only while connected or
        # listening froze the Connecting glow on one frame.
        state = (APP/'ConversationState.java').read_text()
        statuses = set(re.findall(r'"(Ready|Connecting|Listening|Muted|Disconnected)"',
            state[state.index('String trayStatus('):].split('\n', 1)[0]))
        self.assertEqual(statuses, {'Ready', 'Connecting', 'Listening', 'Muted', 'Disconnected'})
        self.assertIn('return connected || listening || "Connecting".equals(status);', self.controls)
        self.assertIn('active = AgentStatusControls.active(connected, listening, data.getString("status"));', self.tray)
        waves = self.tray[self.tray.index('private final class Waves'):]
        self.assertIn('if (active) postInvalidateOnAnimation();', waves)
        self.assertNotIn('if (connected || listening) postInvalidateOnAnimation();', waves)

    def test_message_codes_match(self):
        codes = dict((name, int(value)) for name, value in
            re.findall(r'\b(REGISTER|UNREGISTER|START|MUTE|UNMUTE|END|STATE)=(\d+)', self.service))
        self.assertEqual(codes, {'REGISTER': 1, 'UNREGISTER': 2, 'START': 3, 'MUTE': 4,
            'UNMUTE': 5, 'END': 6, 'STATE': 100})
        # SystemUI's subscription (AgentTrayLink) uses the service's REGISTER/UNREGISTER codes and
        # the tray accepts only STATE replies.
        self.assertIn('static final int REGISTER = 1, UNREGISTER = 2;', self.link)
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
        self.assertIn('getPackageUidAsUser(\n                    "dev.pixel.voice", link.user())', self.tray)

    def test_tray_keeps_at_most_one_pending_rebind(self):
        # Every failed bind, binding death and screen/user broadcast used to start its own 3 s chain.
        self.assertNotIn('postDelayed(this::bind', self.tray)
        self.assertEqual(self.tray.count('handler.postDelayed(retryBind, 3000);'), 1)
        schedule = self.tray[self.tray.index('public void scheduleRetry()'):]
        self.assertLess(schedule.index('handler.removeCallbacks(retryBind);'),
            schedule.index('handler.postDelayed(retryBind, 3000);'))

    def test_lifecycle_is_delegated_to_the_tested_link(self):
        # AgentTrayLinkTest exercises the behaviour; this pins the Android wiring to it.
        for wiring in ('link.connected();', 'public void onServiceDisconnected(ComponentName name) { link.disconnected(); }',
                'public void onBindingDied(ComponentName name) { link.bindingDied(); }',
                'public void onNullBinding(ComponentName name) { link.bindingDied(); }',
                'link.screenOff();', 'link.screenOn(ActivityManager.getCurrentUser());',
                'link.userSwitched(ActivityManager.getCurrentUser());',
                'this.link.retry(ActivityManager.getCurrentUser())'):
            self.assertIn(wiring, self.tray, wiring)
        # Only the link registers or unregisters; the prototype never sends codes 1 or 2 itself.
        self.assertNotRegex(self.tray, r'send\((1|2)\)')
        self.assertNotIn('bindServiceAsUser', self.tray.replace(
            self.tray[self.tray.index('new AgentTrayLink.Port()'):self.tray.index('AgentTrayPrototype(Context source')], ''))

    def test_register_says_whether_the_rose_chin_can_be_raised(self):
        # F08 (agreed with Home): REGISTER's arg2 is AgentStatusControls.chinArg (AgentStatusControlsTest), read fresh
        # for each REGISTER; only the link re-registers (AgentTrayLink.refresh, AgentTrayLinkTest) when it changes.
        send = self.tray[self.tray.index('private boolean send(int action) {'):]
        send = send[:send.index('\n    }\n') + 6]
        self.assertIn('if (action == AgentTrayLink.REGISTER) message.arg2 = sentChin = chinArg();', send)
        self.assertNotIn('arg1', self.tray)
        self.assertIn('return AgentStatusControls.chinArg(SystemProperties.getBoolean(EDGE_PROPERTY, false), '
            'link.user(),', self.tray)
        self.assertIn('Settings.Secure.getIntForUser(context.getContentResolver(), Settings.Secure.NAVIGATION_MODE, 0,',
            self.tray)
        self.assertIn('private static final String EDGE_PROPERTY = "persist.wm.debug.rose_edge";', self.tray)
        # Not by class name: R8 may rename com.android.wm.shell.roseedge, which SystemUI's keep rule does not cover.
        self.assertNotIn('Class.forName', self.tray)
        self.assertIn('if (chinArg() != sentChin) link.refresh();', self.tray)
        self.assertIn('Settings.Secure.getUriFor(Settings.Secure.NAVIGATION_MODE), false,', self.tray)
        self.assertIn('SystemProperties.addChangeCallback(() -> handler.post(this::chinMaybeChanged));', self.tray)
        # The edge's switch is the one the Shell controller reads and only the edge's build patch sets.
        controller = (ROOT/'os/patches/rose-edge-wmshell.patch').read_text()
        self.assertIn('static final String ENABLED_PROPERTY = "persist.wm.debug.rose_edge";', controller)
        self.assertIn('persist.wm.debug.rose_edge=true', (ROOT/'os/patches/rose-edge-build-make.patch').read_text())

    def test_app_glow_shader_matches_the_os_copy(self):
        self.assertEqual(shader(APP/'StatusVoiceGlow.java'), shader(SHELL/'StatusVoiceGlow.java'))

if __name__ == '__main__':
    unittest.main()
