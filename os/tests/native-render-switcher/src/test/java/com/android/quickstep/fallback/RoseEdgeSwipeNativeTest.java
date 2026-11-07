package com.android.quickstep.fallback;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.animation.ValueAnimator;
import android.app.KeyguardManager;
import android.content.Context;
import android.graphics.Matrix;
import android.graphics.Rect;
import android.graphics.RectF;
import android.hardware.display.DisplayManager;
import android.os.Looper;
import android.os.SystemProperties;
import android.view.Surface;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

/**
 * #410: the production RoseEdgeSwipe and RoseEdgeState (compiled unchanged from the Launcher3 overlay, with
 * RoseEdgeShell behind a fake shell binder that records every one-way call, so a raise can happen without a real
 * WMShell; the shell protocol itself is tested in os/staged/rose-edge-resize/native-test) on Robolectric's SDK 35
 * runtime, at the frozen reference's 384x832dp screen, xxhdpi (and once at 420dpi). It covers the staged bottom
 * gesture as the handler drives it: the default-off switch, when a gesture is staged (and why not), the tray revealed
 * with the finger (peek) without any shell call, the stages with the slop added back, the release (raise once, Home on
 * a flick, the switcher winning, cancel), the tray already up, abandon, and the preview's matrix and crop. Framework
 * behaviour on a host: the patched Launcher3 swipe handler that calls these is not compiled or run here.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
public class RoseEdgeSwipeNativeTest {
    static final int DISPLAY = 0, OTHER_DISPLAY = 2, TASK = 41, OTHER_TASK = 42;
    /** Launcher3's gestural touch slop is about 11dp; any value works, the slop is added back. */
    static final float SLOP_DP = 11;
    Context context;
    float density;
    int width, height;
    final List<String> events = new ArrayList<>();
    final List<String> peeks = new ArrayList<>();
    final List<Integer> shellCalls = new ArrayList<>();
    final RoseEdgeState.Listener listener = new RoseEdgeState.Listener() {
        @Override
        public void onRaisedChanged(int display, boolean raised) {
            events.add(display + (raised ? " up" : " down"));
        }

        @Override
        public void onPeek(int display, float reveal, float approach, int stage) {
            peeks.add(reveal < 0 ? display + " end" : display + " " + RoseEdgeGesture.stageName(stage) + " "
                    + Math.round(reveal * 100) + "%");
        }
    };
    /** The shell's IRoseEdge as Launcher3 receives it: it records each one-way transaction and replies nothing. */
    final android.os.Binder shell = new android.os.Binder() {
        @Override
        protected boolean onTransact(int code, android.os.Parcel data, android.os.Parcel reply, int flags) {
            shellCalls.add(code);
            return true;
        }
    };
    int lift;

    @Before
    public void setUp() {
        context = RuntimeEnvironment.getApplication();
        density = context.getResources().getDisplayMetrics().density;
        width = Math.round(384 * density);
        height = Math.round(832 * density);
        lift = RoseEdgeGesture.raisedInsetPx(density);
        RoseEdgeShell.setShell(shell);
        RoseEdgeState.lower(DISPLAY);
        RoseEdgeState.lower(OTHER_DISPLAY);
        RoseEdgeState.addListener(listener);
        events.clear();
        peeks.clear();
        shellCalls.clear();
    }

    /** The system's "Remove animations" (animator duration scale 0); ValueAnimator.areAnimatorsEnabled reads it. */
    static void durationScale(float scale) throws Exception {
        ValueAnimator.class.getMethod("setDurationScale", float.class).invoke(null, scale);
    }

    @After
    public void tearDown() throws Exception {
        RoseEdgeState.removeListener(listener);
        swipe(false);  // a new gesture drops any tray release a test left pending
        RoseEdgeState.lower(DISPLAY);
        RoseEdgeState.lower(OTHER_DISPLAY);
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, "");
        RoseEdgeShell.setShell(null);
        durationScale(1f);
    }

    RoseEdgeSwipe swipe(boolean on) {
        return swipe(on, false, true, false, DISPLAY, TASK);
    }

    RoseEdgeSwipe swipe(boolean on, boolean excluded, boolean gestureNav, boolean ime, int display, int task) {
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, on ? "true" : "");
        return new RoseEdgeSwipe(context, display, task, SLOP_DP * density, excluded, gestureNav, ime);
    }

    /** Moves to each dp from touch-down (the handler's displacement excludes the slop); returns the last stage. */
    int drag(RoseEdgeSwipe s, float... dps) {
        int stage = s.stage();
        for (float dp : dps) stage = s.onMove((dp - SLOP_DP) * density);
        return stage;
    }

    /** Releases at liftOffDpPerMs upward (Launcher3's end velocity is px/ms, negative upward). */
    int release(RoseEdgeSwipe s, float liftOffDpPerMs) {
        return s.release(false, -liftOffDpPerMs * density);
    }

    @Test
    public void theRealPropertyIsReadAndItIsOffByDefault() {
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, "true");
        assertEquals("the runtime's SystemProperties, not the compile stub", "true", SystemProperties.get(RoseEdgeSwipe.PROPERTY));
        RoseEdgeSwipe off = swipe(false);
        assertFalse(off.enabled());
        assertFalse(off.staged());
        assertEquals("off: moves change nothing", RoseEdgeGesture.NONE, drag(off, 60, 120));
        assertEquals("off: Launcher3 decides everything", RoseEdgeGesture.STOCK, release(off, 0));
        assertFalse("off: Launcher3's own transform", off.preview(false, 80 * density, width, height,
                new Matrix(), new Rect(), new float[1]));
        assertTrue("and nothing was raised or revealed", events.isEmpty() && peeks.isEmpty() && shellCalls.isEmpty());
        Throwable[] thrown = new Throwable[1];
        Thread t = new Thread(() -> {
            try { off.settled(false); off.abandon(); } catch (Throwable e) { thrown[0] = e; }
        });
        t.start();
        try { t.join(); } catch (InterruptedException e) { throw new AssertionError(e); }
        assertNull("off: settling never reaches RoseEdgeState (whatever thread)", thrown[0]);
    }

    @Test
    public void stagedOnlyForAnUnlockedFullScreenAppUnderGestureNavigationWithAnEdgeWindowAndTheShell() {
        assertTrue(swipe(true).staged());
        assertFalse("over Home, a trackpad, a continued gesture or not full screen",
                swipe(true, true, true, false, DISPLAY, TASK).staged());
        assertFalse("three-button navigation", swipe(true, false, false, false, DISPLAY, TASK).staged());
        assertFalse("the keyboard is showing", swipe(true, false, true, true, DISPLAY, TASK).staged());
        shadowOf(context.getSystemService(KeyguardManager.class)).setKeyguardLocked(true);
        assertFalse("locked", swipe(true).staged());
        shadowOf(context.getSystemService(KeyguardManager.class)).setKeyguardLocked(false);
        shadowOf(context.getSystemService(DisplayManager.class).getDisplay(DISPLAY)).setRotation(Surface.ROTATION_90);
        assertFalse("rotated", swipe(true).staged());
        shadowOf(context.getSystemService(DisplayManager.class).getDisplay(DISPLAY)).setRotation(Surface.ROTATION_0);
        RoseEdgeShell.setShell(null);
        assertFalse("the shell is unavailable", swipe(true).staged());
        RoseEdgeShell.setShell(shell);
        RoseEdgeState.removeListener(listener);
        assertFalse("no edge window to draw it", swipe(true).staged());
        RoseEdgeState.addListener(listener);
        RoseEdgeSwipe unstaged = swipe(true, true, true, false, DISPLAY, TASK);
        assertEquals("unstaged: moves change nothing", RoseEdgeGesture.NONE, drag(unstaged, 80, 300));
        assertEquals("unstaged: Launcher3 decides", RoseEdgeGesture.STOCK, release(unstaged, 0));
        assertTrue(peeks.isEmpty() && events.isEmpty());
    }

    @Test
    public void theFirstMovementRevealsTheTrayWithoutTouchingTheShell() {
        RoseEdgeSwipe s = swipe(true);
        assertEquals(RoseEdgeGesture.NONE, drag(s, 24));
        assertEquals(RoseEdgeGesture.TRAY, drag(s, 50));
        assertEquals(List.of("0 NONE 50%", "0 TRAY 100%"), peeks);
        assertTrue("revealing is drawing only: no raise, resize or microphone slot", shellCalls.isEmpty());
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
    }

    /** Runs the main looper's due work after ms (the pending raise's timeout is a main-thread Handler). */
    static void idle(long ms) {
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(ms));
    }

    @Test
    public void releaseAtTheTrayRaisesOnceAndTheTrayStays() {
        RoseEdgeSwipe s = swipe(true);
        drag(s, 10, 30, 55, 70);
        assertEquals(RoseEdgeGesture.RAISE, release(s, 0.05f));
        // Frankel2026110501: the shell is not asked while Launcher3's recents transition still owns the app.
        assertTrue("the tray stays drawn, fully shown", s.raisePending());
        assertEquals("0 TRAY 100%", peeks.get(peeks.size() - 1));
        assertTrue("no shell call before the return to the app", shellCalls.isEmpty());
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        s.settled(true);
        assertTrue("settling back to the app keeps it pending, still drawn", s.raisePending());
        assertEquals("0 TRAY 100%", peeks.get(peeks.size() - 1));
        s.returnedToApp();
        assertFalse(s.raisePending());
        assertTrue(RoseEdgeState.isRaised(DISPLAY, TASK));
        assertEquals("one raise", List.of("0 up"), events);
        assertEquals("one shell raise", List.of(RoseEdgeShell.TRANSACTION_RAISE), shellCalls);
        assertEquals("the reveal ended after the raise", "0 end", peeks.get(peeks.size() - 1));
        s.returnedToApp();
        s.abandon();
        idle(RoseEdgeSwipe.PENDING_RAISE_TIMEOUT_MS * 3);
        assertTrue("it stays up until deliberately dismissed", RoseEdgeState.isRaised(DISPLAY, TASK));
        assertEquals("still one raise", List.of(RoseEdgeShell.TRANSACTION_RAISE), shellCalls);
        RoseEdgeState.lower(DISPLAY);
        assertEquals("a deliberate dismissal lowers it", List.of("0 up", "0 down"), events);
        assertEquals(List.of(RoseEdgeShell.TRANSACTION_RAISE, RoseEdgeShell.TRANSACTION_LOWER), shellCalls);
    }

    @Test
    public void aPendingTrayReleaseIsDroppedUnlessItReturnsToTheApp() {
        // The recents animation was cancelled after the release.
        RoseEdgeSwipe cancelled = swipe(true);
        drag(cancelled, 30, 70);
        assertEquals(RoseEdgeGesture.RAISE, release(cancelled, 0));
        cancelled.abandon();
        assertFalse(cancelled.raisePending());
        assertEquals("the tray goes", "0 end", peeks.get(peeks.size() - 1));
        cancelled.returnedToApp();
        // Another gesture starts before the return completed: its recents transition owns the app again.
        RoseEdgeSwipe first = swipe(true);
        drag(first, 30, 70);
        assertEquals(RoseEdgeGesture.RAISE, release(first, 0));
        RoseEdgeSwipe next = swipe(true);
        assertFalse(first.raisePending());
        first.returnedToApp();
        assertEquals("the next gesture starts from nothing raised", RoseEdgeGesture.NONE, next.stage());
        // The final end target left the app.
        RoseEdgeSwipe left = swipe(true);
        drag(left, 30, 70);
        assertEquals(RoseEdgeGesture.RAISE, release(left, 0));
        left.settled(false);
        assertFalse(left.raisePending());
        // Nothing arrives: the pending raise times out.
        RoseEdgeSwipe lost = swipe(true);
        drag(lost, 30, 70);
        assertEquals(RoseEdgeGesture.RAISE, release(lost, 0));
        idle(RoseEdgeSwipe.PENDING_RAISE_TIMEOUT_MS - 1);
        assertTrue("not yet", lost.raisePending());
        idle(2);
        assertFalse(lost.raisePending());
        lost.returnedToApp();
        assertEquals("the tray went", "0 end", peeks.get(peeks.size() - 1));
        assertTrue("nothing was ever raised or sent", events.isEmpty() && shellCalls.isEmpty());
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        // Without an edge window there is no tray to keep: the release cancels at once.
        RoseEdgeSwipe noWindow = swipe(true);
        drag(noWindow, 30, 70);
        RoseEdgeState.removeListener(listener);
        assertEquals(RoseEdgeGesture.CANCEL, release(noWindow, 0));
        assertFalse(noWindow.raisePending());
        RoseEdgeState.addListener(listener);
    }

    @Test
    public void belowTheTrayOrMovingDownTheReleaseCancels() {
        RoseEdgeSwipe s = swipe(true);
        drag(s, 20, 40);
        assertEquals(RoseEdgeGesture.CANCEL, release(s, 0));
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertEquals("0 end", peeks.get(peeks.size() - 1));
        RoseEdgeSwipe down = swipe(true);
        drag(down, 30, 80, 70);
        assertEquals("released moving clearly down", RoseEdgeGesture.CANCEL, release(down, -0.8f));
        RoseEdgeSwipe back = swipe(true);
        drag(back, 30, 90, 60, 25);
        assertEquals("dragged back below the tray", RoseEdgeGesture.CANCEL, release(back, 0));
        assertTrue(events.isEmpty() && shellCalls.isEmpty());
    }

    @Test
    public void aQuickFlickBeforeTheSwitcherGoesHome() {
        RoseEdgeSwipe s = swipe(true);
        drag(s, 30, 90);
        assertEquals(RoseEdgeGesture.HOME, release(s, 2f));
        assertFalse("Home raises nothing", RoseEdgeState.isRaised(DISPLAY));
        s.settled(false);
        assertTrue(events.isEmpty() && shellCalls.isEmpty());
    }

    @Test
    public void theSwitcherStageWinsOverReleaseSpeedAndReversalWalksBack() {
        RoseEdgeSwipe s = swipe(true);
        assertEquals(RoseEdgeGesture.SWITCHER, drag(s, 60, 150, 230));
        assertEquals("a flick in the switcher stage still opens the switcher", RoseEdgeGesture.SWITCHER_OPEN, release(s, 3f));
        s.settled(false);
        assertFalse(RoseEdgeState.isRaised(DISPLAY));

        RoseEdgeSwipe back = swipe(true);
        assertEquals(RoseEdgeGesture.SWITCHER, drag(back, 120, 240));
        assertEquals("hysteresis: still the switcher at 180dp", RoseEdgeGesture.SWITCHER, drag(back, 180));
        assertEquals("back below 170dp: the tray again", RoseEdgeGesture.TRAY, drag(back, 160));
        assertEquals(RoseEdgeGesture.RAISE, release(back, 0));
        back.returnedToApp();
        assertTrue(RoseEdgeState.isRaised(DISPLAY, TASK));
    }

    @Test
    public void withTheTrayAlreadyUpTheStageStartsAtTheTray() {
        assertTrue(RoseEdgeState.raise(DISPLAY, TASK, lift));
        events.clear();
        shellCalls.clear();
        RoseEdgeSwipe s = swipe(true);
        assertEquals(RoseEdgeGesture.TRAY, s.stage());
        assertEquals(RoseEdgeGesture.TRAY, drag(s, 20));
        assertTrue("already shown: no reveal", peeks.isEmpty());
        assertEquals(RoseEdgeGesture.STAY, release(s, 0));
        assertTrue(RoseEdgeState.isRaised(DISPLAY, TASK));
        assertTrue("no second raise", events.isEmpty() && shellCalls.isEmpty());

        RoseEdgeSwipe home = swipe(true);
        drag(home, 40, 100);
        assertEquals(RoseEdgeGesture.HOME, release(home, 2f));
        home.settled(false);
        assertFalse("leaving lowers it", RoseEdgeState.isRaised(DISPLAY));
        assertEquals(List.of("0 down"), events);

        assertEquals("over another task it is not up", RoseEdgeGesture.NONE,
                swipe(true, false, true, false, DISPLAY, OTHER_TASK).stage());
    }

    @Test
    public void aCancelIsLauncher3sAndAbandonTakesTheTrayDown() {
        RoseEdgeSwipe s = swipe(true);
        drag(s, 30, 80);
        assertEquals(RoseEdgeGesture.STOCK, s.release(true, 0));
        assertEquals("0 end", peeks.get(peeks.size() - 1));
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        RoseEdgeSwipe gone = swipe(true);
        drag(gone, 30, 80);
        peeks.clear();
        gone.abandon();
        assertEquals("the recents animation was cancelled: the reveal ends", List.of("0 end"), peeks);
        gone.abandon();
        assertEquals("once", List.of("0 end"), peeks);
        assertEquals("after it ended, a move reveals nothing", RoseEdgeGesture.TRAY, drag(gone, 120));
        assertEquals(List.of("0 end"), peeks);
        assertEquals("and there is no second release", RoseEdgeGesture.STOCK, release(gone, 0));
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
    }

    @Test
    public void eachDisplayHasItsOwnEdge() {
        assertFalse("a display that does not exist is never staged",
                swipe(true, false, true, false, OTHER_DISPLAY, TASK).staged());
        int other = org.robolectric.shadows.ShadowDisplayManager.addDisplay("w384dp-h832dp-xxhdpi");
        RoseEdgeSwipe s = swipe(true, false, true, false, other, TASK);
        drag(s, 70);
        assertEquals(RoseEdgeGesture.RAISE, release(s, 0));
        s.returnedToApp();
        assertTrue(RoseEdgeState.isRaised(other, TASK));
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertEquals(List.of(other + " up"), events);
        RoseEdgeState.lower(other);
    }

    @Test
    public void thePreviewFollowsTheTrayAtFullWidthThenShrinksTowardTheSwitcher() {
        RoseEdgeSwipe s = swipe(true);
        Matrix m = new Matrix();
        Rect crop = new Rect();
        float[] radius = new float[1];
        assertTrue(s.preview(false, 24 * density, width, height, m, crop, radius));
        assertEquals(new Rect(0, 0, width, Math.round(height - 29 * density)), crop);
        assertTrue("unscaled while following", m.isIdentity());
        assertEquals(17 * density, radius[0], 0.01f);

        assertTrue(s.preview(false, 220 * density, width, height, m, crop, radius));
        RectF shown = new RectF(crop);
        m.mapRect(shown);
        float bottom = height - 58 * density;
        assertEquals(width * 0.9f, shown.width(), 0.5f);
        assertEquals(bottom * 0.9f, shown.height(), 0.5f);
        assertEquals("centred horizontally", width / 2f, shown.centerX(), 0.5f);
        assertEquals("lifted 24dp about the shortened window's centre", bottom / 2 - 24 * density, shown.centerY(), 0.5f);

        assertFalse("switcher stage or quick switch: Launcher3's own transform",
                s.preview(true, 90 * density, width, height, m, crop, radius));
        assertFalse("no size: Launcher3's own", s.preview(false, 60 * density, 0, height, m, crop, radius));
        assertFalse("no size: Launcher3's own", s.preview(false, 60 * density, width, 0, m, crop, radius));
    }

    @Test
    public void raisedOrReducedMotionTheAppDoesNotTrackTheFinger() throws Exception {
        Matrix m = new Matrix();
        Rect crop = new Rect();
        float[] radius = new float[1];
        durationScale(0f);
        assertFalse(ValueAnimator.areAnimatorsEnabled());
        RoseEdgeSwipe reduced = swipe(true);
        for (float dp : new float[] {30, 200}) {
            assertTrue(reduced.preview(false, dp * density, width, height, m, crop, radius));
            assertEquals("reduced motion: untouched (" + dp + "dp)", new Rect(0, 0, width, height), crop);
            assertTrue(m.isIdentity());
        }
        durationScale(1f);

        RoseEdgeState.raise(DISPLAY, TASK, lift);
        RoseEdgeSwipe raised = swipe(true);
        assertTrue(raised.preview(false, 0, width, height, m, crop, radius));
        assertEquals("raised: its raised rect", new Rect(0, 0, width, Math.round(height - 58 * density)), crop);
        assertTrue(m.isIdentity());
        assertEquals(34 * density, radius[0], 0.01f);
    }

    @Test
    @Config(qualifiers = "w384dp-h832dp-420dpi")
    public void anotherDensity() {
        assertEquals(2.625f, density, 0.001f);
        RoseEdgeSwipe s = swipe(true);
        assertEquals("stages count dp, from touch-down", RoseEdgeGesture.TRAY, drag(s, 49));
        assertEquals(RoseEdgeGesture.RAISE, release(s, 0));
        assertEquals("speed converts px/ms to dp/ms", RoseEdgeGesture.HOME, release(swipe(true), 1.3f));
    }

    @Test(expected = IllegalStateException.class)
    public void theStateIsMainThreadOnly() throws Throwable {
        Throwable[] thrown = new Throwable[1];
        Thread t = new Thread(() -> {
            try { RoseEdgeState.addListener((d, r) -> {}); } catch (Throwable e) { thrown[0] = e; }
        });
        t.start();
        t.join();
        if (thrown[0] != null) throw thrown[0];
    }
}
