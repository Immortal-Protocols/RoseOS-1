package com.android.quickstep.fallback;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Rect;
import android.os.Looper;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewTreeObserver;
import com.android.wm.shell.roseedge.IRoseEdge;
import com.android.wm.shell.roseedge.IRoseEdgeListener;
import java.util.ArrayList;
import java.util.List;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * #410 E2c (staged): the production RoseEdgeWindow and RoseEdgeLayout on Robolectric's SDK 35 runtime with native
 * graphics, against the aidl-generated IRoseEdge.Stub as the shell. It shows Home's contract geometry, the window
 * following the edge, the microphone slot as a transparent and untouchable hole whose bounds reach the shell only after
 * onRaised, and the two own controls and the swipe down. It does not show a real window manager, input dispatch or
 * SystemUI's tray control.
 */
@RunWith(RobolectricTestRunner.class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
public class RoseEdgeWindowNativeTest {
    static final int DISPLAY = 0, TASK = 41;

    static final class FakeShell extends IRoseEdge.Stub {
        final List<String> calls = new ArrayList<>();
        IRoseEdgeListener listener;

        @Override public void raise(int d, int t, int lift, IRoseEdgeListener l) { calls.add("raise"); listener = l; }
        @Override public void lower(int d) { calls.add("lower"); }
        @Override public void setMicSlot(int d, Rect slot) { calls.add("slot " + slot); }
    }

    Context context;
    float density;
    FakeShell shell;
    RoseEdgeWindow window;
    final List<String> actions = new ArrayList<>();

    @Before
    public void setUp() {
        context = RuntimeEnvironment.getApplication();
        density = context.getResources().getDisplayMetrics().density;
        shell = new FakeShell();
        RoseEdgeShell.setShell(shell);
        RoseEdgeState.lower(DISPLAY);
        window = new RoseEdgeWindow(context, new RoseEdgeWindow.Actions() {
            @Override public void openRose(int displayId) { actions.add("rose " + displayId); }
            @Override public void showSwitcher(int displayId) { actions.add("switcher " + displayId); }
        });
        window.start();
        shell.calls.clear();
    }

    @After
    public void tearDown() {
        window.stop();
        RoseEdgeState.lower(DISPLAY);
        RoseEdgeShell.setShell(null);
    }

    static void idle() {
        shadowOf(Looper.getMainLooper()).idle();
    }

    int dp(float v) {
        return Math.round(v * density);
    }

    RoseEdgeWindow.EdgeView raise() throws Exception {
        assertTrue(RoseEdgeState.raise(DISPLAY, TASK, dp(58)));
        idle();
        RoseEdgeWindow.EdgeView view = window.shownOn(DISPLAY);
        assertNotNull("raising shows the edge window", view);
        view.measure(View.MeasureSpec.makeMeasureSpec(dp(384), View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(dp(92), View.MeasureSpec.EXACTLY));
        view.layout(0, 0, dp(384), dp(92));
        return view;
    }

    @Test
    public void theGeometryIsHomesContract() {
        int w = dp(384);
        Rect rose = RoseEdgeLayout.control(RoseEdgeLayout.ROSE, w, density, 0);
        Rect mic = RoseEdgeLayout.control(RoseEdgeLayout.MIC, w, density, 0);
        Rect sw = RoseEdgeLayout.control(RoseEdgeLayout.SWITCHER, w, density, 0);
        assertEquals(dp(92), RoseEdgeLayout.windowHeightPx(density, 0));
        assertEquals(dp(103), rose.width(), 1);
        assertEquals(dp(44), mic.width(), 1);
        assertEquals(dp(106), sw.width(), 1);
        assertEquals("gap 14dp", dp(14), mic.left - rose.right, 1);
        assertEquals("gap 14dp", dp(14), sw.left - mic.right, 1);
        assertEquals("row centred", w - sw.right, rose.left, 2);
        for (Rect r : new Rect[] {rose, mic, sw}) {
            assertEquals("centre 29dp above the edge bottom", dp(92 - 29), r.exactCenterY(), 1);
            assertEquals(dp(44), r.height(), 1);
        }
        Rect onScreen = RoseEdgeLayout.micSlotOnScreen(w, density, 0, dp(832 - 92));
        assertTrue("the slot satisfies the geometry agreed with OS Core",
                RoseEdgeState.micSlotFits(onScreen, w, dp(832), density));
    }

    @Test
    public void startingIsWhatLetsTheEdgeRise() {
        assertTrue(RoseEdgeState.canRaise());
        window.stop();
        assertFalse(RoseEdgeState.canRaise());
        window.start();
    }

    @Test
    public void theWindowFollowsTheEdge() throws Exception {
        raise();
        RoseEdgeState.lower(DISPLAY);
        idle();
        assertNull(window.shownOn(DISPLAY));
    }

    @Test
    public void theSlotIsAnUntouchableHoleTheRestIsTouchable() throws Exception {
        RoseEdgeWindow.EdgeView view = raise();
        ViewTreeObserver.InternalInsetsInfo info = new ViewTreeObserver.InternalInsetsInfo();
        view.onComputeInternalInsets(info);
        Rect mic = view.control(RoseEdgeLayout.MIC), rose = view.control(RoseEdgeLayout.ROSE);
        assertFalse(info.touchableRegion.contains(mic.centerX(), mic.centerY()));
        assertTrue(info.touchableRegion.contains(rose.centerX(), rose.centerY()));
        assertTrue(info.touchableRegion.contains(dp(10), dp(80)));
    }

    @Test
    public void theSlotIsLeftTransparentAndThePlateIsDrawnAroundIt() throws Exception {
        RoseEdgeWindow.EdgeView view = raise();
        Bitmap bitmap = Bitmap.createBitmap(dp(384), dp(92), Bitmap.Config.ARGB_8888);
        view.draw(new Canvas(bitmap));
        String out = System.getProperty("render.out");
        if (out != null) {
            java.io.File dir = new java.io.File(out);
            dir.mkdirs();
            try (java.io.FileOutputStream f = new java.io.FileOutputStream(new java.io.File(dir, "rose-edge.png"))) {
                bitmap.compress(Bitmap.CompressFormat.PNG, 100, f);
            }
        }
        Rect mic = view.control(RoseEdgeLayout.MIC);
        assertEquals("nothing drawn in the slot", 0, Color.alpha(bitmap.getPixel(mic.centerX(), mic.centerY())));
        assertEquals("the plate is opaque below the flat top", 255, Color.alpha(bitmap.getPixel(dp(4), dp(88))));
        assertEquals("the concave fillet leaves its inside clear", 0, Color.alpha(bitmap.getPixel(dp(30), dp(2))));
        assertEquals("on both sides", 0, Color.alpha(bitmap.getPixel(dp(384 - 30), dp(2))));
        assertEquals("the flat top's line is drawn", 255, Color.alpha(bitmap.getPixel(dp(192), dp(34) + dp(1))));
        int rose = bitmap.getPixel(view.control(RoseEdgeLayout.ROSE).centerX(), view.control(RoseEdgeLayout.ROSE).centerY());
        assertTrue("the ruby pearl sits in the Rose well", Color.red(rose) > Color.blue(rose) + 40);
    }

    @Test
    public void theSlotReachesTheShellOnlyAfterOnRaised() throws Exception {
        raise();
        assertEquals(List.of("raise"), shell.calls);
        shell.listener.onRaised(DISPLAY, TASK, new Rect(0, 0, dp(384), dp(832 - 58)));
        idle();
        Rect expected = RoseEdgeLayout.micSlotOnScreen(dp(384), density, 0, dp(832) - dp(92));
        assertEquals(List.of("raise", "slot " + expected), shell.calls);
    }

    MotionEvent touch(int action, float x, float y) {
        long now = SystemClock.uptimeMillis();
        return MotionEvent.obtain(now, now, action, x, y, 0);
    }

    void tap(View view, Rect r) {
        assertTrue(view.onTouchEvent(touch(MotionEvent.ACTION_DOWN, r.exactCenterX(), r.exactCenterY())));
        view.onTouchEvent(touch(MotionEvent.ACTION_UP, r.exactCenterX(), r.exactCenterY()));
        idle();
    }

    @Test
    public void roseAndTheSwitcherLowerTheEdgeThenAct() throws Exception {
        RoseEdgeWindow.EdgeView view = raise();
        tap(view, view.control(RoseEdgeLayout.ROSE));
        assertEquals(List.of("rose 0"), actions);
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        view = raise();
        tap(view, view.control(RoseEdgeLayout.SWITCHER));
        assertEquals(List.of("rose 0", "switcher 0"), actions);
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
    }

    @Test
    public void aSwipeDownLowersWithoutActingAndTheSlotNeverActs() throws Exception {
        RoseEdgeWindow.EdgeView view = raise();
        Rect mic = view.control(RoseEdgeLayout.MIC);
        assertFalse("the slot's touches are not this window's",
                view.onTouchEvent(touch(MotionEvent.ACTION_DOWN, mic.exactCenterX(), mic.exactCenterY())));
        assertTrue(RoseEdgeState.isRaised(DISPLAY));
        Rect rose = view.control(RoseEdgeLayout.ROSE);
        view.onTouchEvent(touch(MotionEvent.ACTION_DOWN, rose.exactCenterX(), dp(20)));
        view.onTouchEvent(touch(MotionEvent.ACTION_UP, rose.exactCenterX(), dp(20) + dp(40)));
        idle();
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertTrue(actions.isEmpty());
    }
}
