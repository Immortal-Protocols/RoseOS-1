package com.android.quickstep.fallback;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
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
import org.robolectric.shadows.ShadowSystemClock;

/**
 * #410 E2a: the production RoseEdgeSwipe and RoseEdgeState (compiled unchanged from the Launcher3 overlay) on
 * Robolectric's SDK 35 runtime, at the frozen reference's 384x832dp screen, xxhdpi: the default-off switch, the
 * keyguard and surface gates, raising and lowering with listeners, and the preview's matrix and crop. Framework
 * behaviour on a host: the patched Launcher3 swipe handler that calls these is not compiled or run here.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
public class RoseEdgeSwipeNativeTest {
    static final int DISPLAY = 0;
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
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, "");
        durationScale(1f);
    }

    RoseEdgeSwipe swipe(boolean on, boolean excluded) {
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, on ? "true" : "");
        return new RoseEdgeSwipe(context, DISPLAY, excluded);
    }

    /** A release after ms milliseconds, dp upward, that Launcher3 would send Home. */
    int release(RoseEdgeSwipe s, float dp, long ms) {
        ShadowSystemClock.advanceBy(Duration.ofMillis(ms));
        return s.release(true, true, false, false, true, dp * density);
    }

    @Test
    public void theRealPropertyIsReadAndItIsOffByDefault() {
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, "true");
        assertEquals("the runtime's SystemProperties, not the compile stub", "true", SystemProperties.get(RoseEdgeSwipe.PROPERTY));
        RoseEdgeSwipe off = swipe(false, false);
        assertFalse(off.active(true, true));
        assertEquals("off: Launcher3 decides everything", RoseEdgeGesture.STOCK, release(off, 80, 400));
        assertFalse("off: Launcher3's own transform", off.preview(true, true, false, 80 * density, width, height,
                new Matrix(), new Rect(), new float[1]));
        assertTrue("and nothing was raised", events.isEmpty());
        Throwable[] thrown = new Throwable[1];
        Thread t = new Thread(() -> {
            try { off.settled(false); } catch (Throwable e) { thrown[0] = e; }
        });
        t.start();
        try { t.join(); } catch (InterruptedException e) { throw new AssertionError(e); }
        assertEquals("off: settling never reaches RoseEdgeState (whatever thread)", null, thrown[0]);
    }

    @Test
    public void onlyAnUnlockedAppUnderGestureNavigationInItsNaturalRotation() {
        assertTrue(swipe(true, false).active(true, true));
        assertFalse("over Home or a trackpad", swipe(true, true).active(true, true));
        assertFalse("three-button navigation", swipe(true, false).active(false, true));
        assertFalse("rotated", swipe(true, false).active(true, false));
        shadowOf(context.getSystemService(KeyguardManager.class)).setKeyguardLocked(true);
        assertFalse("locked", swipe(true, false).active(true, true));
        shadowOf(context.getSystemService(KeyguardManager.class)).setKeyguardLocked(false);
    }

    @Test
    public void aShortSwipeRaisesTheEdgeAndLeavingLowersIt() {
        RoseEdgeSwipe s = swipe(true, false);
        assertEquals(RoseEdgeGesture.RAISE, release(s, 80, 60));
        assertTrue(RoseEdgeState.isRaised(DISPLAY));
        assertEquals(List.of("0 up"), events);
        s.settled(true);
        assertTrue("back to the app keeps it up", RoseEdgeState.isRaised(DISPLAY));

        RoseEdgeSwipe again = swipe(true, false);
        assertEquals("raised: a short swipe keeps it", RoseEdgeGesture.STAY, release(again, 80, 60));
        assertEquals("and does not raise it twice", List.of("0 up"), events);

        RoseEdgeSwipe home = swipe(true, false);
        assertEquals("raised: more than 120dp goes Home", RoseEdgeGesture.HOME, release(home, 130, 900));
        home.settled(false);
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertEquals(List.of("0 up", "0 down"), events);
    }

    @Test
    public void speedIsAveragedOverTheWholeGesture() {
        assertEquals("200dp in 150ms (1.33dp/ms): Home", RoseEdgeGesture.HOME, release(swipe(true, false), 200, 150));
        assertEquals("200dp in 400ms (0.5dp/ms): the edge", RoseEdgeGesture.RAISE, release(swipe(true, false), 200, 400));
    }

    @Test
    public void thePreviewCropsAtFullWidthThenShrinksTowardHome() {
        RoseEdgeSwipe s = swipe(true, false);
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
        assertEquals("lifted 40dp about the shortened window's centre", bottom / 2 - 40 * density, shown.centerY(), 0.5f);

        assertFalse("attached to recents (pause or quick switch): Launcher3's own transform",
                s.preview(true, true, true, 90 * density, width, height, m, crop, radius));
    }

    @Test
    public void raisedOrReducedMotionTheAppDoesNotTrackTheFinger() throws Exception {
        RoseEdgeSwipe s = swipe(true, false);
        Matrix m = new Matrix();
        Rect crop = new Rect();
        float[] radius = new float[1];
        durationScale(0f);
        assertFalse(ValueAnimator.areAnimatorsEnabled());
        assertTrue(s.preview(true, true, false, 90 * density, width, height, m, crop, radius));
        assertEquals("reduced motion: untouched", new Rect(0, 0, width, height), crop);
        assertTrue(m.isIdentity());
        durationScale(1f);

        RoseEdgeState.raise(DISPLAY);
        for (float dp : new float[] {0, 90, 400}) {
            assertTrue(s.preview(true, true, false, dp * density, width, height, m, crop, radius));
            assertEquals("raised: its raised rect", new Rect(0, 0, width, Math.round(height - 58 * density)), crop);
            assertTrue(m.isIdentity());
            assertEquals(34 * density, radius[0], 0.01f);
        }
    }

    @Test(expected = IllegalStateException.class)
    public void theStateIsMainThreadOnly() throws Throwable {
        Throwable[] thrown = new Throwable[1];
        Thread t = new Thread(() -> {
            try { RoseEdgeState.raise(DISPLAY); } catch (Throwable e) { thrown[0] = e; }
        });
        t.start();
        t.join();
        if (thrown[0] != null) throw thrown[0];
    }
}
