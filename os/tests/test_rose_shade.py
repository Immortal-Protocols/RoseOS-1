"""1G (OS Core): the Rose notification shade, Designer's F09 Refinement v1 (sha256 5ebc5f0c…42daf1).

The rules (grouping, expansion, empty state, quick settings, drafts and truthful reply states, back, the status-bar
pull) are pure Java, exercised by os/tests/roseshade/RoseShadeTest.java. These checks pin how the native SystemUI
surface uses them and the agreed boundaries:

- rose-shade-systemui.patch only adds RoseShadeModule to the pinned ReferenceSystemUIModule's includes, a file no
  other Rose patch touches;
- RoseShadeStartable takes notifications from SystemUI's own NotificationListener and the status-bar pull through
  AgentTrayPrototype's hook, which runs the shade before the tray; the lock screen and other users keep the stock
  shade;
- only an explicit Send (button or the keyboard's IME_ACTION_SEND) sends a reply; the keyboard, the chin and the shade
  closing keep the draft; the shade has no microphone control (the Rose chin holds the only one).

Source and fixture logic only: no SystemUI build, emulator or device."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT/'os/patches/rose-shade-systemui.patch'
MODULE = 'packages/SystemUI/src/com/android/systemui/dagger/ReferenceSystemUIModule.java'
FIXTURE = ROOT/'os/tests/fixtures/rose-shade/ReferenceSystemUIModule.java'
PINNED = '9ba02efa08a38d9bf98831bca55eca82704b802d95a34415ece5420214c51f37'
SHADE = ROOT/'os/overlays/frameworks/base/packages/SystemUI/src/com/android/systemui/roseshade'
TRAY = ROOT/'os/overlays/frameworks/base/libs/WindowManager/Shell/src/com/android/wm/shell/AgentTrayPrototype.java'


def flat(text):
    return re.sub(r'\s+', ' ', text)


def method(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 0
    for i in range(brace, len(source)):
        depth += {'{': 1, '}': -1}.get(source[i], 0)
        if depth == 0:
            return source[start:i + 1]
    raise ValueError(signature)


class RoseShadeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='rose-shade-'))
        (cls.tmp/MODULE).parent.mkdir(parents=True)
        shutil.copyfile(FIXTURE, cls.tmp/MODULE)
        subprocess.run(['git', 'init', '-q', str(cls.tmp)], check=True)
        cls.applied = subprocess.run(['git', '-C', str(cls.tmp), 'apply', str(PATCH)], capture_output=True,
            text=True)
        cls.module = (cls.tmp/MODULE).read_text()
        cls.shade = {p.name: p.read_text() for p in SHADE.glob('*.java')}
        cls.controller = cls.shade['RoseShadeController.java']
        cls.startable = cls.shade['RoseShadeStartable.java']
        cls.tray = TRAY.read_text()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_the_patch_only_includes_the_shade_module_in_a_file_no_other_patch_touches(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), PINNED)
        self.assertEqual(self.applied.returncode, 0, self.applied.stderr)
        text = PATCH.read_text()
        self.assertEqual(re.findall(r'^\+\+\+ b/(\S+)', text, re.M), [MODULE])
        added = [l for l in text.splitlines() if l.startswith('+') and not l.startswith('+++')]
        self.assertEqual(added[-1], '+        com.android.systemui.roseshade.RoseShadeModule.class,')
        self.assertFalse([l for l in text.splitlines() if l.startswith('-') and not l.startswith('---')])
        self.assertIn('ReferenceNotificationsModule.class, // RoseOS (1G): the Rose notification shade '
            '(com.android.systemui.roseshade, an overlay). com.android.systemui.roseshade.RoseShadeModule.class, '
            'PosturingModule.class,', flat(self.module))
        for other in (ROOT/'os/patches').glob('*.patch'):
            if other != PATCH:
                self.assertNotIn('ReferenceSystemUIModule', other.read_text(errors='replace'), other.name)
                self.assertNotIn('roseshade', other.read_text(errors='replace'), other.name)

    def test_the_startable_is_bound_and_fed_by_systemuis_own_listener(self):
        module = flat(self.shade['RoseShadeModule.java'])
        self.assertIn('@Binds @IntoMap @ClassKey(RoseShadeStartable.class) public abstract CoreStartable '
            'bindRoseShadeStartable(RoseShadeStartable startable);', module)
        start = flat(method(self.startable, 'public void start()'))
        self.assertLess(start.index('if (!RoseShadeController.enabled()) return;'),
            start.index('new RoseShadeController(mContext)'))
        self.assertIn('mListener.addNotificationHandler(', start)
        self.assertIn('final StatusBarNotification[] active = mListener.getActiveNotifications();', start)
        self.assertIn('AgentTrayPrototype.setStatusBarDragHandler(shade::onStatusBarTouch);', start)
        self.assertIn('@Inject public RoseShadeStartable(Context context, NotificationListener listener)',
            flat(self.startable))
        self.assertNotIn('extends NotificationListenerService', ''.join(self.shade.values()))

    def test_the_status_bar_pull_runs_before_the_tray_and_never_on_the_lock_screen(self):
        hook = flat(method(self.tray, 'public static boolean onStatusBarTouch(MotionEvent event)'))
        self.assertLess(hook.index('handler.onStatusBarTouch(event)'), hook.index('instance.handleTouch(event)'))
        self.assertIn('if (instance != null) instance.controls.cancel(); return true;', hook)
        touch = flat(method(self.controller, 'boolean onStatusBarTouch(MotionEvent event)'))
        self.assertLess(touch.index('isKeyguardLocked()'), touch.index('mDrag.down('))
        self.assertIn('if (!enabled() || ActivityManager.getCurrentUser() != 0', touch)
        self.assertIn('case MotionEvent.ACTION_DOWN: mDrag.down(event.getRawX(), event.getRawY()); return false;',
            touch)

    def test_only_send_sends_and_nothing_else_touches_a_draft(self):
        code = self.controller
        # The single delivery path, reached only from the Send button and the keyboard's Send action.
        self.assertEqual(len(re.findall(r'(?<![\w.])send\(ReplyRow\.this\.key\)', code)), 2)
        self.assertIn('if (actionId == EditorInfo.IME_ACTION_SEND) { send(ReplyRow.this.key); return true; }',
            flat(code))
        self.assertIn('sendButton = pill("Send", true, v -> send(ReplyRow.this.key));', code)
        self.assertEqual(code.count('mReplies.send('), 1)
        send = flat(method(code, 'private void send(String key)'))
        self.assertIn('RemoteInput.addResultsToIntent(inputs, fill, results);', send)
        self.assertIn('if (ok) mReplies.delivered(key); else mReplies.failed(key);', send)
        # The field is a single-line editor with the keyboard's Send key (OS Product's keyboard seam).
        self.assertIn('field.setSingleLine(true);', code)
        self.assertIn('field.setImeOptions(EditorInfo.IME_ACTION_SEND);', code)
        # Keyboard and chin changes only move the panel; closing keeps RoseShadeReplies.
        insets = flat(code[code.index('mRoot.setOnApplyWindowInsetsListener'):])
        self.assertIn('WindowInsets.Type.ime() | WindowInsets.Type.navigationBars()', insets)
        self.assertIn('mReplies.inputSurfaceChanged();', insets)
        close = flat(method(code, 'void close()'))
        self.assertNotIn('mReplies', close)
        # Back: keyboard, then quick settings, then the shade (RoseShadeModel.back).
        self.assertIn('final RoseShadeModel.Back back = mModel.back(mImeVisible);', code)
        self.assertIn('dispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, mBack);',
            code)

    def test_the_shade_has_no_microphone_control(self):
        for name, text in self.shade.items():
            lowered = text.lower()
            for banned in ('setmicrophonemute', 'tray_mute', 'mic_mute', '"mute', 'input muted', 'audiomanager'):
                self.assertNotIn(banned, lowered, f'{name}: {banned}')

    def test_the_window_lets_the_keyboard_and_chin_keep_their_space(self):
        attach = flat(method(self.controller, 'private void attach()'))
        self.assertIn('WindowManager.LayoutParams.TYPE_STATUS_BAR_ADDITIONAL', attach)
        self.assertIn('params.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE', attach)
        self.assertNotIn('FLAG_NOT_FOCUSABLE', attach)
        self.assertNotIn('FLAG_ALT_FOCUSABLE_IM', attach)


if __name__ == '__main__':
    unittest.main()
