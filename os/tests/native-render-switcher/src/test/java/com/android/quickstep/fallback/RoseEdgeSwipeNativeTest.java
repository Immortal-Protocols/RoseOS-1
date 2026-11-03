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
import android.os.SystemClock;
import android.os.SystemProperties;
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
 * #410 E2a: the production RoseEdgeSwipe and RoseEdgeState (compiled unchanged from the Launcher3 overlay) on
 * Robolectric's SDK 35 runtime, at the frozen reference's 384x832dp screen, xxhdpi (and once at 420dpi): the default-off
 * switch, the gates (including "no edge window, no raise"), raising and lowering per display and task with listeners,
 * distances and speed from touch-down, and the preview's matrix and crop. Framework behaviour on a host: the patched
 * Launcher3 swipe handler that calls these is not compiled or run here.
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
    final RoseEdgeState.Listener listener = (display, raised) -> events.add(display + (raised ? " up" : " down"));

    @Before
    public void setUp() {
        context = RuntimeEnvironment.getApplication();
        density = context.getResources().getDisplayMetrics().density;
        width = Math.round(384 * density);
        height = Math.round(832 * density);
        RoseEdgeState.lower(DISPLAY);
        RoseEdgeState.lower(OTHER_DISPLAY);
        RoseEdgeState.addListener(listener);
        events.clear();
    }

    /** The system's "Remove animations" (animator duration scale 0); ValueAnimator.areAnimatorsEnabled reads it. */
    static void durationScale(float scale) throws Exception {
        ValueAnimator.class.getMethod("setDurationScale", float.class).invoke(null, scale);
    }

    @After
    public void tearDown() throws Exception {
        RoseEdgeState.removeListener(listener);
        RoseEdgeState.lower(DISPLAY);
        RoseEdgeState.lower(OTHER_DISPLAY);
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, "");
        durationScale(1f);
    }

    /** A gesture over TASK on DISPLAY that touched down msAgo milliseconds ago. */
    RoseEdgeSwipe swipe(boolean on, boolean excluded, long msAgo) {
        return swipe(on, excluded, msAgo, DISPLAY, TASK);
    }

    RoseEdgeSwipe swipe(boolean on, boolean excluded, long msAgo, int display, int task) {
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, on ? "true" : "");
        return new RoseEdgeSwipe(context, display, task, SystemClock.uptimeMillis() - msAgo, SLOP_DP * density, excluded);
    }

    /** A release dp from touch-down that Launcher3 would send Home (the handler's displacement excludes the slop). */
    int release(RoseEdgeSwipe s, float dp) {
        return s.release(true, true, false, false, true, (dp - SLOP_DP) * density);
    }

    @Test
    public void theRealPropertyIsReadAndItIsOffByDefault() {
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, "true");
        assertEquals("the runtime's SystemProperties, not the compile stub", "true", SystemProperties.get(RoseEdgeSwipe.PROPERTY));
        RoseEdgeSwipe off = swipe(false, false, 400);
        assertFalse(off.enabled());
        assertFalse(off.active(true, true));
        assertEquals("off: Launcher3 decides everything", RoseEdgeGesture.STOCK, release(off, 80));
        assertFalse("off: Launcher3's own transform", off.preview(true, true, false, 80 * density, width, height,
                new Matrix(), new Rect(), new float[1]));
        assertTrue("and nothing was raised", events.isEmpty());
        Throwable[] thrown = new Throwable[1];
        Thread t = new Thread(() -> {
            try { off.settled(false); } catch (Throwable e) { thrown[0] = e; }
        });
        t.start();
        try { t.join(); } catch (InterruptedException e) { throw new AssertionError(e); }
        assertNull("off: settling never reaches RoseEdgeState (whatever thread)", thrown[0]);
    }

    @Test
    public void withoutAnEdgeWindowNothingRisesAndNothingChanges() {
        RoseEdgeState.removeListener(listener);
        assertFalse(RoseEdgeState.hasListener());
        RoseEdgeSwipe s = swipe(true, false, 400);
        assertFalse("no window to draw it: inactive", s.active(true, true));
        assertEquals("a short swipe keeps Launcher3's own result", RoseEdgeGesture.STOCK, release(s, 80));
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertFalse("no preview either", s.preview(true, true, false, 60 * density, width, height, new Matrix(),
                new Rect(), new float[1]));
        assertFalse("and the state refuses a raise", RoseEdgeState.raise(DISPLAY, TASK));
        RoseEdgeState.addListener(listener);
    }

    @Test
    public void onlyAnUnlockedAppUnderGestureNavigationInItsNaturalRotation() {
        assertTrue(swipe(true, false, 0).active(true, true));
        assertFalse("over Home, a trackpad, a continued gesture or not full screen", swipe(true, true, 0).active(true, true));
        assertFalse("three-button navigation", swipe(true, false, 0).active(false, true));
        assertFalse("rotated", swipe(true, false, 0).active(true, false));
        shadowOf(context.getSystemService(KeyguardManager.class)).setKeyguardLocked(true);
        assertFalse("locked", swipe(true, false, 0).active(true, true));
        shadowOf(context.getSystemService(KeyguardManager.class)).setKeyguardLocked(false);
    }

    @Test
    public void launcher3sOwnTargetCancelAndPauseStand() {
        RoseEdgeSwipe s = swipe(true, false, 400);
        float up = (80 - SLOP_DP) * density;
        assertEquals("back to the app (or anything not Home) stays", RoseEdgeGesture.STOCK, s.release(true, true, false, false, false, up));
        assertEquals("a cancel stays", RoseEdgeGesture.STOCK, s.release(true, true, true, false, true, up));
        assertEquals("a pause stays", RoseEdgeGesture.STOCK, s.release(true, true, false, true, true, up));
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertTrue(events.isEmpty());
    }

    @Test
    public void distancesCountFromTouchDownIncludingTheSlop() {
        assertEquals("40dp from touch-down raises (the slop is added back)", RoseEdgeGesture.RAISE, release(swipe(true, false, 400), 40));
        RoseEdgeState.lower(DISPLAY);
        assertEquals("36dp or less does not", RoseEdgeGesture.STOCK, release(swipe(true, false, 400), 35));
    }

    @Test
    public void aShortSwipeRaisesTheEdgeOverItsTaskAndLeavingLowersIt() {
        RoseEdgeSwipe s = swipe(true, false, 60);
        assertEquals(RoseEdgeGesture.RAISE, release(s, 80));
        assertTrue(RoseEdgeState.isRaised(DISPLAY, TASK));
        assertFalse("only over the task it rose over", RoseEdgeState.isRaised(DISPLAY, OTHER_TASK));
        assertEquals(List.of("0 up"), events);
        s.settled(true);
        assertTrue("back to the app keeps it up", RoseEdgeState.isRaised(DISPLAY, TASK));

        assertEquals("raised: a short swipe keeps it", RoseEdgeGesture.STAY, release(swipe(true, false, 60), 80));
        assertEquals("over another task it is not up: the gesture can raise it there",
                RoseEdgeGesture.RAISE, release(swipe(true, false, 60, DISPLAY, OTHER_TASK), 80));
        assertEquals("re-targeting tells nobody twice", List.of("0 up"), events);
        assertTrue(RoseEdgeState.isRaised(DISPLAY, OTHER_TASK));

        RoseEdgeSwipe home = swipe(true, false, 900, DISPLAY, OTHER_TASK);
        assertEquals("raised: more than 120dp goes Home", RoseEdgeGesture.HOME, release(home, 130));
        home.settled(false);
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertEquals(List.of("0 up", "0 down"), events);
    }

    @Test
    public void eachDisplayHasItsOwnEdge() {
        assertEquals(RoseEdgeGesture.RAISE, release(swipe(true, false, 60, OTHER_DISPLAY, TASK), 80));
        assertTrue(RoseEdgeState.isRaised(OTHER_DISPLAY, TASK));
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertEquals(List.of("2 up"), events);
    }

    @Test
    public void speedIsAveragedSinceTouchDown() {
        assertEquals("200dp in 150ms (1.33dp/ms): Home", RoseEdgeGesture.HOME, release(swipe(true, false, 150), 200));
        assertEquals("200dp in 400ms (0.5dp/ms): the edge", RoseEdgeGesture.RAISE, release(swipe(true, false, 400), 200));
    }

    @Test
    public void theStateDeduplicatesAndListenersAreSet() {
        RoseEdgeState.lower(DISPLAY);
        assertTrue("lowering what is down tells nobody", events.isEmpty());
        RoseEdgeState.addListener(listener);
        assertTrue(RoseEdgeState.raise(DISPLAY, TASK));
        assertTrue(RoseEdgeState.raise(DISPLAY, TASK));
        assertEquals("one listener, one change", List.of("0 up"), events);
    }

    @Test
    public void thePreviewCropsAtFullWidthThenShrinksTowardHome() {
        RoseEdgeSwipe s = swipe(true, false, 0);
        Matrix m = new Matrix();
        Rect crop = new Rect();
        float[] radius = new float[1];
        assertTrue(s.preview(true, true, false, 60 * density, width, height, m, crop, radius));
        assertEquals(new Rect(0, 0, width, Math.round(height - 29 * density)), crop);
        assertTrue("unscaled while following", m.isIdentity());
        assertEquals(17 * density, radius[0], 0.01f);

        assertTrue(s.preview(true, true, false, 264 * density, width, height, m, crop, radius));
        RectF shown = new RectF(crop);
        m.mapRect(shown);
        float bottom = height - 58 * density;
        assertEquals(width * 0.74f, shown.width(), 0.5f);
        assertEquals(bottom * 0.74f, shown.height(), 0.5f);
        assertEquals("centred horizontally", width / 2f, shown.centerX(), 0.5f);
        assertEquals("lifted 40dp about the shortened window's centre", bottom / 2 - 40 * density, shown.centerY(), 0.5f);

        assertFalse("attached to recents (pause or quick switch): Launcher3's own transform",
                s.preview(true, true, true, 90 * density, width, height, m, crop, radius));
        assertFalse("no size: Launcher3's own", s.preview(true, true, false, 60 * density, 0, height, m, crop, radius));
        assertFalse("no size: Launcher3's own", s.preview(true, true, false, 60 * density, width, 0, m, crop, radius));
    }

    @Test
    public void raisedOrReducedMotionTheAppDoesNotTrackTheFinger() throws Exception {
        Matrix m = new Matrix();
        Rect crop = new Rect();
        float[] radius = new float[1];
        durationScale(0f);
        assertFalse(ValueAnimator.areAnimatorsEnabled());
        RoseEdgeSwipe reduced = swipe(true, false, 0);
        for (float dp : new float[] {90, 264}) {
            assertTrue(reduced.preview(true, true, false, dp * density, width, height, m, crop, radius));
            assertEquals("reduced motion: untouched (" + dp + "dp)", new Rect(0, 0, width, height), crop);
            assertTrue(m.isIdentity());
        }
        durationScale(1f);

        RoseEdgeState.raise(DISPLAY, TASK);
        RoseEdgeSwipe raised = swipe(true, false, 0);
        for (float dp : new float[] {0, 90, 400}) {
            assertTrue(raised.preview(true, true, false, dp * density, width, height, m, crop, radius));
            assertEquals("raised: its raised rect", new Rect(0, 0, width, Math.round(height - 58 * density)), crop);
            assertTrue(m.isIdentity());
            assertEquals(34 * density, radius[0], 0.01f);
        }
    }

    @Test
    public void aRaiseAtReleaseDoesNotSnapTheWindow() {
        RoseEdgeSwipe s = swipe(true, false, 60);
        Matrix m = new Matrix();
        Rect crop = new Rect();
        float[] radius = new float[1];
        assertEquals(RoseEdgeGesture.RAISE, release(s, 80));
        assertTrue(s.preview(true, true, false, 40 * density, width, height, m, crop, radius));
        assertEquals("the return animation follows the shift, not the new raised state",
                new Rect(0, 0, width, Math.round(height - 58 * density / 3)), crop);
    }

    @Test
    @Config(qualifiers = "w384dp-h832dp-420dpi")
    public void anotherDensity() {
        assertEquals(2.625f, density, 0.001f);
        assertEquals(RoseEdgeGesture.RAISE, release(swipe(true, false, 400), 80));
        RoseEdgeState.lower(DISPLAY);
        Rect crop = new Rect();
        assertTrue(swipe(true, false, 0).preview(true, true, false, 60 * density, width, height, new Matrix(), crop,
                new float[1]));
        assertEquals(new Rect(0, 0, width, Math.round(height - 29 * density)), crop);
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
