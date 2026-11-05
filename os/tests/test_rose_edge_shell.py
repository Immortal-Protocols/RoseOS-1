"""#410 resize on release, the WMShell side (OS Core), as agreed with OS Product on #410.

os/patches/rose-edge-wmshell.patch adds the Rose raised edge to WMShell: IRoseEdge/IRoseEdgeListener, exported only
through ShellController's external interfaces (MANAGE_ACTIVITY_TASKS on every call), and RoseEdgeController, which
shrinks the default task display area to (0, 0, W, H - liftPx) so the foreground app gets real, smaller bounds with its
width unchanged. All rules are in the pure RoseEdgePolicy.

These tests:
- apply the patch on the pinned WMShellModule, a file no registered Rose patch touches;
- run the production RoseEdgePolicy through RoseEdgePolicyTest;
- compile RoseEdgeController against signature stubs copied from pinned 84183aff, with the listener and binder
  interfaces generated from the patched AIDL;
- check the wiring, caller gates, geometry and lowering hooks in the patched sources.

The patch is deliberately not registered in any build yet: overlays and registered patches go into every OS build,
and WMShell has not been compiled for real. Source and fixture logic only: no framework build, device or runtime."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT/'os/patches/rose-edge-wmshell.patch'
HARNESS = ROOT/'os/tests/rose-edge'
SHELL = 'libs/WindowManager/Shell/src/com/android/wm/shell'
MODULE = f'{SHELL}/dagger/WMShellModule.java'
EDGE = f'{SHELL}/roseedge'
# The edge's microphone-input slot (#410 option a); in the shell package to use the tray's own AgentTrayLink.
MIC = f'{SHELL}/RoseEdgeMicSlot.java'
TRAY = ROOT/'os/overlays/frameworks/base'/SHELL
PINNED = {MODULE: (ROOT/'os/tests/fixtures/rose-edge/WMShellModule.java',
    '62d968a9484b7cc64dcf599996be38125b0aba0ceddd60bf75efec3afbd744d9')}
CREATED = ('IRoseEdge.aidl', 'IRoseEdgeListener.aidl', 'RoseEdgeController.java', 'RoseEdgePolicy.java')


def flat(text):
    return re.sub(r'\s+', ' ', text)


def method(source, signature):
    """The body of the first method whose declaration contains signature, by brace matching."""
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 0
    for i in range(brace, len(source)):
        depth += {'{': 1, '}': -1}.get(source[i], 0)
        if depth == 0:
            return source[start:i + 1]
    raise ValueError(signature)


def aidl_to_java(text):
    """The Java interface an AIDL interface file declares (methods only), for an offline compile of its users."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    package = re.search(r'package ([\w.]+);', text).group(1)
    name, body = re.search(r'interface (\w+) \{(.*)\}', text, re.S).groups()
    methods = []
    for m in body.split(';'):
        m = re.sub(r'\s*=\s*\d+\s*$', '', m.strip())
        if not m:
            continue
        m = re.sub(r'\boneway\s+', '', m)
        m = re.sub(r'\b(in|out|inout)\s+', '', m)
        methods.append(f'    {m} throws android.os.RemoteException;\n')
    imports = ''.join(f'import {i};\n' for i in re.findall(r'import ([\w.]+);', text))
    stub = (f'    String DESCRIPTOR = "{package}.{name}";\n    abstract class Stub extends android.os.Binder '
        f'implements {name} {{ @Override public android.os.IBinder asBinder() {{ return this; }} }}\n')
    return (f'package {package};\n{imports}public interface {name} extends android.os.IInterface {{\n'
        + stub + ''.join(methods) + '}\n')


class RoseEdgeShellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='rose-edge-'))
        cls.tree = cls.tmp/'frameworks-base'
        target = cls.tree/MODULE
        target.parent.mkdir(parents=True)
        shutil.copyfile(PINNED[MODULE][0], target)
        subprocess.run(['git', 'init', '-q', str(cls.tree)], check=True)
        cls.applied = []
        for patch in (PATCH,):
            result = subprocess.run(['git', '-C', str(cls.tree), 'apply', f'--include={MODULE}', f'--include={EDGE}/*',
                f'--include={MIC}', str(patch)], capture_output=True, text=True)
            cls.applied.append((patch.name, result.returncode, result.stderr))
        cls.module = (cls.tree/MODULE).read_text()
        cls.source = {name: (cls.tree/EDGE/name).read_text() for name in CREATED}
        cls.controller = cls.source['RoseEdgeController.java']
        cls.mic = (cls.tree/MIC).read_text()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_fixture_is_pinned_and_the_patch_applies(self):
        for path, (fixture, digest) in PINNED.items():
            self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(), digest, path)
        for name, code, stderr in self.applied:
            self.assertEqual(code, 0, f'{name}: {stderr}')
        # The patch touches only the shell's handheld module and its own new package.
        touched = re.findall(r'^\+\+\+ b/(\S+)', PATCH.read_text(), re.M)
        self.assertEqual(sorted(touched), sorted([MODULE, MIC] + [f'{EDGE}/{n}' for n in CREATED]))
        # No file it touches is touched by any other Rose patch, so host-reconcile's history of Rose's own
        # frameworks/base output (historical_outputs skips files touched outside its sequence) is unaffected.
        for other in (ROOT/'os/patches').glob('*.patch'):
            if other != PATCH:
                theirs = set(re.findall(r'^\+\+\+ b/(\S+)', other.read_text(errors='replace'), re.M))
                self.assertFalse(theirs & set(touched), other.name)

    def test_inert_until_a_real_compile_registers_it(self):
        # Every registered patch and every overlay goes into each OS build. #410 (Chief): the normal OS build is the
        # compile proof, so apply-os-changes.py registers the patch, last in frameworks/base; no overlay is added.
        self.assertIn("projects['frameworks/base'].append('rose-edge-wmshell.patch')",
            (ROOT/'scripts/apply-os-changes.py').read_text())
        # host-reconcile (#543) mirrors that order: its frameworks/base sequence names the patch once, last.
        reconcile = (ROOT/'scripts/host-reconcile.py').read_text()
        self.assertEqual(reconcile.count('rose-edge-wmshell'), 1)
        self.assertRegex(reconcile, r"FRAMEWORKS_BASE_SEQUENCE = \([^)]*'rose-edge-wmshell\.patch'\)")
        self.assertFalse(list((ROOT/'os/overlays').rglob('roseedge')))
        # Default off even once registered.
        self.assertIn('static final String ENABLED_PROPERTY = "persist.wm.debug.rose_edge";', self.controller)
        self.assertIn('return SystemProperties.getBoolean(ENABLED_PROPERTY, false);', self.controller)

    def build(self):
        out = self.tmp/'classes'
        if out.is_dir():
            return out
        gen = self.tmp/'gen'/EDGE.replace('libs/WindowManager/Shell/src/', '')
        gen.mkdir(parents=True)
        for name in ('IRoseEdge', 'IRoseEdgeListener'):
            (gen/f'{name}.java').write_text(aidl_to_java(self.source[f'{name}.aidl']))
        sources = [str(p) for p in sorted((HARNESS/'stubs').rglob('*.java'))]
        sources += [str(p) for p in sorted((self.tmp/'gen').rglob('*.java'))]
        # The tray's real AgentTrayLink (an overlay, unchanged) is compiled with the slot that reuses it.
        sources += [str(self.tree/EDGE/'RoseEdgePolicy.java'), str(self.tree/EDGE/'RoseEdgeController.java'),
            str(self.tree/MIC), str(TRAY/'AgentTrayLink.java'), str(HARNESS/'RoseEdgePolicyTest.java')]
        compiled = subprocess.run(['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-nowarn', '-d', str(out),
            *sources], capture_output=True, text=True)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        return out

    def test_the_policy_meets_the_agreed_rules(self):
        run = subprocess.run(['java', '-cp', str(self.build()), 'com.android.wm.shell.roseedge.RoseEdgePolicyTest'],
            capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertRegex(run.stdout, r'RoseEdgePolicyTest: \d+ checks passed')

    def test_the_controller_compiles_against_the_pinned_shell_signatures_and_its_own_aidl(self):
        # Stubs mirror pinned 84183aff signatures; the shell's own small interfaces are verbatim copies.
        self.assertTrue((self.build()/'com/android/wm/shell/roseedge/RoseEdgeController.class').is_file())

    def test_the_interface_is_the_one_agreed_on_410(self):
        edge, listener = flat(self.source['IRoseEdge.aidl']), flat(self.source['IRoseEdgeListener.aidl'])
        self.assertIn('package com.android.wm.shell.roseedge;', edge)
        self.assertIn('oneway void raise(int displayId, int taskId, int liftPx, IRoseEdgeListener listener) = 1;', edge)
        self.assertIn('oneway void lower(int displayId) = 2;', edge)
        self.assertIn('import android.graphics.Rect;', self.source['IRoseEdge.aidl'])
        self.assertIn('oneway void setMicSlot(int displayId, in Rect slotBounds) = 3;', edge)
        self.assertIn('oneway interface IRoseEdgeListener', listener)
        self.assertIn('void onRaised(int displayId, int taskId, in Rect appBounds) = 1;', listener)
        self.assertIn('void onRefused(int displayId, int taskId, int reason) = 2;', listener)
        self.assertIn('void onLowered(int displayId, int reason) = 3;', listener)
        policy = self.source['RoseEdgePolicy.java']
        reasons = dict(re.findall(r'public static final int ((?:REFUSED|LOWERED)_\w+) = (\d+);', policy))
        self.assertEqual({k: int(v) for k, v in reasons.items()}, {
            'REFUSED_NOT_TOP_TASK': 1, 'REFUSED_NOT_RESIZEABLE': 2, 'REFUSED_LOCKED': 3, 'REFUSED_ROTATION': 4,
            'REFUSED_MULTI_WINDOW': 5, 'REFUSED_BUSY': 6, 'REFUSED_LIFT': 7, 'REFUSED_DISABLED': 8,
            'REFUSED_DISPLAY': 9, 'LOWERED_REQUESTED': 0, 'LOWERED_TASK': 1, 'LOWERED_HOME': 2,
            'LOWERED_KEYGUARD': 3, 'LOWERED_DISPLAY': 4, 'LOWERED_IME': 5, 'LOWERED_CALLER_DIED': 6,
            'LOWERED_USER': 7})

    def test_exported_only_to_the_recents_component_with_the_existing_permission(self):
        c = flat(self.controller)
        self.assertIn('mShellController.addExternalInterface(IRoseEdge.DESCRIPTOR, this::createExternalInterface, '
            'this);', c)
        impl = method(self.controller, 'private static class IRoseEdgeImpl')
        self.assertEqual(impl.count('executeRemoteCallWithTaskPermission(mController,'), 3,
            'raise, lower and setMicSlot all check MANAGE_ACTIVITY_TASKS on the caller')
        self.assertEqual(len(re.findall(r'public void (\w+)\(', impl)), 4, 'invalidate, raise, lower and setMicSlot only')
        patch = PATCH.read_text()
        for token in ('clearCallingIdentity', 'withCleanCallingIdentity', 'uses-permission', 'grantPermission',
                'enforceCallingOrSelfPermission', 'Binder.getCallingUid'):
            self.assertFalse(token in patch, token)

    def test_real_bounds_full_width_and_never_scaled(self):
        raise_ = method(self.controller, 'private void raise(int displayId')
        self.assertIn('RoseEdgePolicy.refusal(displayId, taskId, liftPx, conditions(displayId, layout),', raise_)
        self.assertLess(raise_.index('refused(listener, displayId, taskId, reason);'),
            raise_.index('wct.setBounds(tda.token, bounds);'), 'a refusal changes nothing')
        self.assertIn('RoseEdgePolicy.raisedBounds(layout.width(), layout.height(), liftPx)', raise_)
        policy = flat(self.source['RoseEdgePolicy.java'])
        self.assertIn('return new int[] {0, 0, displayWidth, displayHeight - liftPx};', policy)
        lower = method(self.controller, 'private void lowerNow(int reason)')
        self.assertIn('wct.setBounds(tda.token, new Rect());', lower)
        # One TRANSIT_CHANGE per raise or lower, played by this handler alone, with no animation of any kind.
        self.assertEqual(self.controller.count('mTransitions.startTransition(TRANSIT_CHANGE, wct, this)'), 1)
        anim = method(self.controller, 'public boolean startAnimation(')
        self.assertIn('if (applied == null) return false;', anim, 'other transitions are not ours')
        self.assertIn('startTransaction.apply(); finishCallback.onTransitionFinished(null);', flat(anim))
        code = re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', self.controller, flags=re.S))
        for token in ('setScale', 'setMatrix', 'setWindowCrop', 'setWindowingMode', 'setPosition', 'Animator',
                'ScreenCapture', 'screenshot', 'captureLayers'):
            self.assertFalse(token in code, token)
        # Only the default task display area organized by the shell is ever changed.
        self.assertEqual(len(re.findall(r'wct\.setBounds\(', self.controller)), 3, 'raise, lower and the restart reset')
        self.assertEqual(self.controller.count('mRootTdaOrganizer.getDisplayAreaInfo(displayId)'), 2)

    def test_every_lowering_event_is_wired_to_the_policy(self):
        hooks = {
            'public void onFocusTaskChanged(': 'mState.onFocusChanged(taskState(taskInfo),',
            'public void onTaskInfoChanged(': 'mState.onTaskInfoChanged(taskState(taskInfo))',
            'public void onTaskVanished(': 'mState.onTaskVanished(taskInfo.taskId)',
            'public void onKeyguardVisibilityChanged(': 'mState.onKeyguard(visible)',
            'public void onUserChanged(': 'mState.onUserChanged()',
            'public void onDisplayConfigurationChanged(': 'mState.onDisplayChanged(displayId)',
            'public void onDisplayRemoved(': 'mState.onDisplayChanged(displayId)',
            'public void onImeVisibilityChanged(': 'mState.onImeVisibility(displayId, isShowing)',
            'private void requestLower(': 'mState.onLowerRequested(displayId)',
        }
        for signature, call in hooks.items():
            self.assertIn(call, flat(method(self.controller, signature)), signature)
        for signature in ('public void onFocusTaskChanged(', 'public void onTaskInfoChanged(',
                'public void onTaskVanished('):
            self.assertIn('mMainExecutor.execute(() -> {', method(self.controller, signature),
                'posted: never inside ShellTaskOrganizer\'s lock')
        init = flat(method(self.controller, 'private void onInit()'))
        for registration in ('addKeyguardChangeListener(this)', 'addUserChangeListener(this)', 'addFocusListener(this)',
                'addTaskInfoChangedListener(this)', 'addTaskVanishedListener(this)', 'addDisplayWindowListener(this)',
                'addPositionProcessor(this)', 'mTransitions.addHandler(this)',
                'mRootTdaOrganizer.registerListener(Display.DEFAULT_DISPLAY, this)'):
            self.assertIn(registration, init)
        # The owner's death, and Launcher3 re-binding, lower; a lower is never lost even if its transition aborts.
        self.assertIn('lowerIfNeeded(mState.onOwnerGone(binder))', flat(method(self.controller, 'private boolean linkOwner(')))
        self.assertIn('lowerNow(RoseEdgePolicy.LOWERED_CALLER_DIED)',
            method(self.controller, 'private void onInterfaceInvalidated()'))
        self.assertIn('controller::onInterfaceInvalidated', method(self.controller, 'public void invalidate()'))
        self.assertIn('mRootTdaOrganizer.applyTransaction(applied.mWct);',
            method(self.controller, 'private void onAborted('))
        # A shell that died raised leaves the server's requested bounds behind: cleared when the shell organizes again.
        appeared = flat(method(self.controller, 'public void onDisplayAreaAppeared('))
        self.assertIn('if (mState.raised() || !enabled()) return;', appeared)
        self.assertIn('wct.setBounds(displayAreaInfo.token, new Rect()); mRootTdaOrganizer.applyTransaction(wct);',
            appeared)
        # The keyguard is also read live at raise time.
        self.assertIn('mKeyguardVisible || keyguardLocked()', self.controller)
        # Reason 3 is "keyguard or screen off": screen-off lowers too, on the shell's executor.
        screen = flat(self.controller[self.controller.index('mContext.registerReceiver(new BroadcastReceiver()'):])
        self.assertIn('if (Intent.ACTION_SCREEN_OFF.equals(intent.getAction())) { '
            'mMainExecutor.execute(() -> lowerIfNeeded(mState.onKeyguard(true)));', screen)
        self.assertIn('new IntentFilter(Intent.ACTION_SCREEN_OFF), Context.RECEIVER_EXPORTED);', screen)

    def test_mic_slot_is_mute_only_on_the_existing_trusted_tray_route(self):
        # Option (a): SystemUI's own tray protocol, from SystemUI's identity; Launcher3 only reports where the hole is.
        mic, tray = self.mic, (TRAY/'AgentTrayPrototype.java').read_text()
        component = 'new ComponentName( VOICE_PACKAGE, "dev.pixel.voice.AgentTrayService")'
        self.assertIn(component, flat(mic))
        self.assertIn('"dev.pixel.voice", "dev.pixel.voice.AgentTrayService"', flat(tray), 'the same service as the tray')
        self.assertIn('private static final String VOICE_PACKAGE = "dev.pixel.voice";', mic)
        self.assertIn('mLink = new AgentTrayLink(new AgentTrayLink.Port() {', mic, 'the tray\'s own lifecycle class')
        # Commands: only the policy's MUTE (4) or UNMUTE (5); never START (3) or END (6), never a raw number.
        code = re.sub(r'/\*.*?\*/', '', mic, flags=re.S)
        sends = re.findall(r'(?<![.\w])send\(([^()]*(?:\([^()]*\))?)\)', code)
        self.assertEqual(set(sends), {'RoseEdgePolicy.micCommand(mMuted)', 'int what'}, sends)
        self.assertEqual(code.count('RoseEdgeMicSlot.this.send(what)'), 1, 'AgentTrayLink sends only REGISTER/UNREGISTER')
        self.assertEqual(code.count('mVoice.send('), 1, 'one Messenger send, behind send(int)')
        # F08: REGISTER alone carries the chin marker, so Rose Agent can tell this chin from the SystemUI tray.
        self.assertIn('if (what == AgentTrayLink.REGISTER) message.arg1 = RoseEdgePolicy.MIC_CLIENT_CHIN;',
            method(mic, 'private boolean send(int what)'))
        self.assertEqual(code.count('.arg1'), 1)
        self.assertIn('public static final int MIC_CLIENT_CHIN = 1;', self.source['RoseEdgePolicy.java'])
        toggle = flat(method(mic, 'private void toggle()'))
        self.assertIn('if (!mKnown || !allowed() || !RoseEdgePolicy.micCommandAllowed(now, mLastCommandAt)) return;',
            toggle, 'nothing before the first STATE, outside the gates or faster than the tray')
        self.assertIn('if (message.what != STATE || !mLink.subscribed() || !trusted(message.sendingUid)) return true;',
            mic, 'state only from Rose Agent itself, while subscribed')
        self.assertIn('if (data.getInt("protocolVersion") != 1) return true;', mic)
        self.assertIn('uid == mWindowContext.getPackageManager().getPackageUidAsUser(VOICE_PACKAGE, mLink.user())',
            flat(mic))
        # Real javac (#410 build 41861964515): View's protected mContext shadows an outer mContext inside Control, and
        # super(mContext) then reads View's own field before View's constructor. The outer field has its own name,
        # and the View stub declares mContext as the platform does, so the compile above fails if it comes back.
        control = mic[mic.index('private final class Control extends View {'):]
        self.assertNotRegex(control, r'\bmContext\b', 'Control must not name View\'s inherited mContext')
        self.assertIn('Control() { super(mWindowContext);', flat(control))
        self.assertIn('protected android.content.Context mContext;', (HARNESS/'stubs/android/view/View.java').read_text())
        # One thread: the creator's (the shell's main thread); binder callbacks are moved onto it.
        self.assertIn('new Handler(Looper.myLooper() != null ? Looper.myLooper() : Looper.getMainLooper());', mic)
        self.assertIn('mReply = new Messenger(new Handler(mHandler.getLooper(), message -> {', mic)
        connection = flat(mic[mic.index('mConnection = new ServiceConnection() {'):mic.index('mLink = new AgentTrayLink(')])
        self.assertEqual(connection.count('mHandler.post('), 4, 'every ServiceConnection callback is posted')
        # The tray's gates: tray on, user 0, unlocked, screen on.
        allowed = flat(method(mic, 'private boolean allowed()'))
        for gate in ('SystemProperties.getBoolean(TRAY_PROPERTY, false)', 'ActivityManager.getCurrentUser() == 0',
                '!keyguard.isKeyguardLocked()', 'power.isInteractive()'):
            self.assertIn(gate, allowed)
        # A non-focusable panel window over the slot only, painted opaque with the edge plate behind the well.
        show = flat(method(mic, 'public void show(Rect slot, int displayHeight)'))
        self.assertIn('new WindowManager.LayoutParams(slot.width(), slot.height(), '
            'WindowManager.LayoutParams.TYPE_NAVIGATION_BAR_PANEL, WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE', show)
        self.assertIn('params.x = slot.left; params.y = slot.top;', show)
        self.assertIn('private static final int[] PLATE_COLORS = {0xFF4A141A, 0xFF4A141A, 0xFF3C0E13, 0xFF330C10};', mic)
        self.assertIn('private static final float[] PLATE_STOPS_DP = {0, 34, 46, 92};', mic)
        self.assertIn('canvas.drawRect(0, 0, getWidth(), getHeight(), mPlate);', mic)
        # The controller: permission-checked, the current raise only, validated, and gone with every lower.
        slot = flat(method(self.controller, 'private void setMicSlot(int displayId'))
        self.assertTrue(slot.index('if (!mState.acceptsMicSlot(displayId)) return;')
            < slot.index('RoseEdgePolicy.validMicSlot(') < slot.index('mMicSlot.show(new Rect(slot), layout.height());'))
        lower = method(self.controller, 'private void lowerNow(int reason)')
        self.assertIn('if (mMicSlot != null) mMicSlot.hide();', lower)
        # No overlay, AgentTrayService or permission change: the patch only adds the slot class.
        self.assertFalse('AgentTrayService.java' in PATCH.read_text())
        self.assertFalse(re.search(r'^\+\+\+ b/\S*AgentTray', PATCH.read_text(), re.M))

    def test_mic_slot_draws_exactly_homes_edge_microphone(self):
        # Home's merged OxbloodEdge.Well.mic and OxbloodEdge.MicGlyph (#514) are the design; the slot is a port of
        # them, so both edges look identical. Any drift in Home's values fails here.
        home = (ROOT/'workspace/rose-agent/app/src/dev/pixel/voice/OxbloodEdge.java').read_text()
        tray = (ROOT/'workspace/rose-agent/app/src/dev/pixel/voice/HomeTray.java').read_text()
        mic = self.mic
        def const(src, name):
            return re.search(rf'\b{name} = (0x[0-9a-fA-F]+|\d+)', src).group(1).lower()
        well = home[home.index('static final class Well'):home.index('static final class MicGlyph')]
        glyph = home[home.index('static final class MicGlyph'):home.index('static final class SwitcherGlyph')]
        for name in ('MIC_PLATE_DP', 'MIC_RADIUS_DP'):
            self.assertEqual(const(mic, name), const(home, name), name)
        for name in ('FILL_TOP', 'FILL_BOTTOM', 'RING', 'TOP_SHADOW'):
            self.assertEqual(const(mic, name), const(well, name), name)
        for mine, theirs in (('GLYPH_DP', 'GLYPH_DP'), ('INK', 'INK'), ('MUTED_INK', 'MUTED_INK'),
                ('MUTED_RING', 'RING'), ('MUTED_GLOW', 'GLOW')):
            self.assertEqual(const(mic, mine), const(glyph, theirs), mine)
        self.assertEqual(const(mic, 'MIC_PLATE_DP'), '32')
        self.assertEqual(const(mic, 'MIC_RADIUS_DP'), '9')
        # The same drawing statements: plate shadow and ring, glyph geometry, muted ring, glow and slash.
        for statement in ('new BlurMaskFilter(Math.max(.1f, (1 * d - .5f) / .57735f)',
                'new RectF(8.6f, 2.5f, 15.4f, 15f), 3.4f, 3.4f,', 'new RectF(5.6f, 4.4f, 18.4f, 17.2f), 0, 180, false,',
                'drawLine(12, 17.4f, 12, 20.6f,', 'drawLine(8.8f, 20.8f, 15.2f, 20.8f,', 'drawLine(4.5f, 3.5f, 19.5f, 20.5f,',
                'Math.round(.55f * 255) << 24', 'setStrokeWidth(3.4f)', 'setStrokeWidth(1.9f)', '0xff1c0609',
                'new BlurMaskFilter(5 * d, BlurMaskFilter.Blur.NORMAL)', 'float s = GLYPH_DP * d / 24f;'):
            self.assertGreater(flat(home).count(statement), 0, statement)
            self.assertEqual(flat(mic).count(statement), flat(home).count(statement), statement)
        # Home's spoken names: the action a tap takes.
        for label in ('"Rose’s microphone is muted. Unmute"', '"Mute Rose’s microphone"'):
            self.assertIn(label, tray)
            self.assertIn(label, mic)
        # Remote state is never guessed: dimmed until the first STATE, and the edge plate under it stays opaque.
        draw = flat(method(mic, 'protected void onDraw(Canvas canvas)'))
        self.assertLess(draw.index('canvas.drawRect(0, 0, getWidth(), getHeight(), mPlate);'),
            draw.index('canvas.saveLayerAlpha('), 'only the well and glyph are dimmed')
        self.assertIn('drawGlyph(canvas, d, mKnown && mMuted);', draw)

    def test_wired_into_the_shell_once(self):
        module = flat(self.module)
        self.assertIn('import com.android.wm.shell.roseedge.RoseEdgeController;', self.module)
        self.assertIn('static RoseEdgeController provideRoseEdgeController(Context context, ShellInit shellInit, '
            'ShellController shellController, ShellTaskOrganizer shellTaskOrganizer, RootTaskDisplayAreaOrganizer '
            'rootTaskDisplayAreaOrganizer, DisplayController displayController, DisplayImeController '
            'displayImeController, Transitions transitions, @ShellMainThread ShellExecutor mainExecutor) {', module)
        self.assertEqual(self.module.count('new RoseEdgeController('), 1)
        create = method(self.module, 'static Object provideIndependentShellComponentsToCreate(')
        self.assertIn('@ShellCreateTriggerOverride', self.module[self.module.rindex('@WMSingleton',
            0, self.module.index('static Object provideIndependentShellComponentsToCreate(')):])
        self.assertIn('RoseEdgeController roseEdgeController) {', create, 'created with the shell')


if __name__ == '__main__':
    unittest.main()
