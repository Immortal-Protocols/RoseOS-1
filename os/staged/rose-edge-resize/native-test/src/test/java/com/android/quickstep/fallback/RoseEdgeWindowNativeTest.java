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
import android.view.WindowInsets;
import android.view.WindowManager;
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
    public void theTwoWellsAreAccessibleButtonsAndTheSlotIsNotThisWindows() throws Exception {
        RoseEdgeWindow.EdgeView view = raise();
        android.view.accessibility.AccessibilityNodeProvider nodes = view.getAccessibilityNodeProvider();
        android.view.accessibility.AccessibilityNodeInfo root = nodes.createAccessibilityNodeInfo(View.NO_ID);
        assertEquals(2, root.getChildCount());
        android.view.accessibility.AccessibilityNodeInfo rose = nodes.createAccessibilityNodeInfo(RoseEdgeLayout.ROSE);
        assertEquals("Open Rose", rose.getContentDescription().toString());
        assertTrue(rose.isClickable());
        Rect bounds = new Rect();
        rose.getBoundsInParent(bounds);
        assertEquals(view.control(RoseEdgeLayout.ROSE), bounds);
        assertEquals("Open system switcher",
                nodes.createAccessibilityNodeInfo(RoseEdgeLayout.SWITCHER).getContentDescription().toString());
        assertNull("the mic slot is SystemUI's control", nodes.createAccessibilityNodeInfo(RoseEdgeLayout.MIC));
        assertTrue(nodes.performAction(RoseEdgeLayout.SWITCHER,
                android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK, null));
        idle();
        assertEquals(List.of("switcher 0"), actions);
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
    }

    /** Installs pkg with one activity answering MAIN in each given category (plus DEFAULT, as a real HOME has). */
    void install(String pkg, String activity, String... categories) {
        android.content.pm.PackageInfo info = new android.content.pm.PackageInfo();
        info.packageName = pkg;
        info.applicationInfo = new android.content.pm.ApplicationInfo();
        info.applicationInfo.packageName = pkg;
        org.robolectric.shadows.ShadowPackageManager pm = shadowOf(context.getPackageManager());
        pm.installPackage(info);
        android.content.ComponentName name = new android.content.ComponentName(pkg, activity);
        pm.addActivityIfNotPresent(name);
        for (String category : categories) {
            android.content.IntentFilter filter = new android.content.IntentFilter(android.content.Intent.ACTION_MAIN);
            filter.addCategory(category);
            filter.addCategory(android.content.Intent.CATEGORY_DEFAULT);
            pm.addIntentFilterForActivity(name, filter);
        }
    }

    @Test
    public void roseOpensThroughRoseAgentsOwnLaunchIntentWhenItHoldsHome() {
        install("dev.pixel.voice", "dev.pixel.voice.MainActivity", android.content.Intent.CATEGORY_HOME,
                android.content.Intent.CATEGORY_LAUNCHER);
        android.content.Intent intent = RoseEdgeWindow.openRoseIntent(context.getPackageManager());
        assertEquals(android.content.Intent.ACTION_MAIN, intent.getAction());
        assertTrue(intent.hasCategory(android.content.Intent.CATEGORY_LAUNCHER));
        assertFalse(intent.hasCategory(android.content.Intent.CATEGORY_HOME));
        assertEquals(new android.content.ComponentName("dev.pixel.voice", "dev.pixel.voice.MainActivity"),
                intent.getComponent());
        assertTrue((intent.getFlags() & android.content.Intent.FLAG_ACTIVITY_NEW_TASK) != 0);
        assertEquals("Home reads history as a relaunch", 0,
                intent.getFlags() & android.content.Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY);
        assertTrue("no new extra", intent.getExtras() == null || intent.getExtras().isEmpty());
    }

    @Test
    public void roseFallsBackToThePlainHomeIntentWhenAnotherAppHoldsHome() {
        install("com.example.home", "com.example.home.Home", android.content.Intent.CATEGORY_HOME);
        install("dev.pixel.voice", "dev.pixel.voice.MainActivity", android.content.Intent.CATEGORY_LAUNCHER);
        android.content.Intent intent = RoseEdgeWindow.openRoseIntent(context.getPackageManager());
        assertEquals(android.content.Intent.ACTION_MAIN, intent.getAction());
        assertTrue(intent.hasCategory(android.content.Intent.CATEGORY_HOME));
        assertNull("whichever app holds HOME decides", intent.getComponent());
        assertTrue((intent.getFlags() & android.content.Intent.FLAG_ACTIVITY_NEW_TASK) != 0);
        assertTrue("no extra", intent.getExtras() == null || intent.getExtras().isEmpty());
    }

    @Test
    public void aRaiseGivesAtMostOneLaunch() throws Exception {
        RoseEdgeWindow.EdgeView view = raise();
        android.view.accessibility.AccessibilityNodeProvider nodes = view.getAccessibilityNodeProvider();
        nodes.performAction(RoseEdgeLayout.ROSE, android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK, null);
        nodes.performAction(RoseEdgeLayout.ROSE, android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK, null);
        view.activate(RoseEdgeLayout.ROSE);
        idle();
        assertEquals(List.of("rose 0"), actions);
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

    @Test
    public void theInsetIsProvidedOnlyOnceTheShellShortenedTheApp() throws Exception {
        RoseEdgeWindow.EdgeView view = raise();
        WindowManager.LayoutParams lp = (WindowManager.LayoutParams) view.getLayoutParams();
        assertEquals("raised but not yet resized: the full-height app must not see an inset", 0,
                RoseEdgeWindow.insetPx(lp));
        shell.listener.onRaised(DISPLAY, TASK, new Rect(0, 0, dp(384), dp(832 - 58)));
        idle();
        lp = (WindowManager.LayoutParams) window.shownOn(DISPLAY).getLayoutParams();
        assertEquals("the lift, as a systemOverlays inset", RoseEdgeGesture.raisedInsetPx(density),
                RoseEdgeWindow.insetPx(lp));
        assertEquals(1, lp.providedInsets.length);
        assertEquals("never navigationBars: the focused app controls those", WindowInsets.Type.systemOverlays(),
                lp.providedInsets[0].getType());
        // A repeated onRaised changes nothing.
        shell.listener.onRaised(DISPLAY, TASK, new Rect(0, 0, dp(384), dp(832 - 58)));
        idle();
        assertEquals(1, ((WindowManager.LayoutParams) window.shownOn(DISPLAY).getLayoutParams()).providedInsets.length);
        // Lowered: the window, and its inset with it, is gone; the next raise starts without one.
        RoseEdgeState.lower(DISPLAY);
        idle();
        assertNull(window.shownOn(DISPLAY));
        view = raise();
        assertEquals(0, RoseEdgeWindow.insetPx((WindowManager.LayoutParams) view.getLayoutParams()));
    }

    @Test
    public void aRefusedRaiseNeverProvidesTheInset() throws Exception {
        raise();
        shell.listener.onRefused(DISPLAY, TASK, 1);
        idle();
        assertNull(window.shownOn(DISPLAY));
        // A late onRaised for a raise already taken down is ignored.
        shell.listener.onRaised(DISPLAY, TASK, new Rect(0, 0, dp(384), dp(832 - 58)));
        idle();
        assertNull(window.shownOn(DISPLAY));
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
    @Test
    public void aDragRevealsTheTrayRisingWithTheFingerWithoutTheShell() throws Exception {
        RoseEdgeState.peek(DISPLAY, 0.25f, 0, RoseEdgeGesture.NONE);
        idle();
        RoseEdgeWindow.EdgeView view = window.shownOn(DISPLAY);
        assertNotNull("the first movement shows the tray", view);
        view.measure(View.MeasureSpec.makeMeasureSpec(dp(384), View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(dp(92), View.MeasureSpec.EXACTLY));
        view.layout(0, 0, dp(384), dp(92));
        RoseEdgeState.peek(DISPLAY, 0.25f, 0, RoseEdgeGesture.NONE);
        assertEquals("a quarter revealed: three quarters still below the screen", 0.75f * dp(92),
                view.getTranslationY(), 0.5f);
        RoseEdgeState.peek(DISPLAY, 1, 0, RoseEdgeGesture.TRAY);
        assertEquals("tray stage: fully shown", 0, view.getTranslationY(), 0.01f);
        assertEquals(1f, view.getAlpha(), 0.001f);
        RoseEdgeState.peek(DISPLAY, 1, 0.5f, RoseEdgeGesture.TRAY);
        assertEquals("approaching the switcher: fading", 0.8f, view.getAlpha(), 0.001f);
        RoseEdgeState.peek(DISPLAY, 1, 1, RoseEdgeGesture.SWITCHER);
        assertEquals("switcher stage: dimmed, the switcher has taken over", 0.35f, view.getAlpha(), 0.001f);
        assertTrue("revealing never reaches the shell (no raise, resize or microphone slot)", shell.calls.isEmpty());
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
    }

    @Test
    public void aDragThatEndsWithoutRaisingTakesTheTrayDownAndARaiseKeepsIt() throws Exception {
        RoseEdgeState.peek(DISPLAY, 0.5f, 0, RoseEdgeGesture.NONE);
        idle();
        assertNotNull(window.shownOn(DISPLAY));
        RoseEdgeState.endPeek(DISPLAY);
        idle();
        assertNull("cancelled: gone", window.shownOn(DISPLAY));

        RoseEdgeState.peek(DISPLAY, 1, 0, RoseEdgeGesture.TRAY);
        idle();
        RoseEdgeWindow.EdgeView peeked = window.shownOn(DISPLAY);
        assertTrue(RoseEdgeState.raise(DISPLAY, TASK, dp(58)));
        RoseEdgeState.endPeek(DISPLAY);
        idle();
        assertTrue("raised: the same tray stays, fully shown", window.shownOn(DISPLAY) == peeked);
        assertEquals(0, peeked.getTranslationY(), 0.01f);
        assertEquals(1f, peeked.getAlpha(), 0.001f);
    }

    @Test
    public void aDragUpOnTheOpenTrayArmsTheSwitcherAndReleaseOpensIt() throws Exception {
        RoseEdgeWindow.EdgeView view = raise();
        float x = view.control(RoseEdgeLayout.ROSE).exactCenterX(), y = dp(40);
        view.onTouchEvent(touch(MotionEvent.ACTION_DOWN, x, y));
        view.onTouchEvent(touch(MotionEvent.ACTION_MOVE, x, y - dp(60)));
        view.onTouchEvent(touch(MotionEvent.ACTION_MOVE, x, y - dp(100)));
        view.onTouchEvent(touch(MotionEvent.ACTION_UP, x, y - dp(100)));
        idle();
        assertEquals("past 96dp: the switcher, and the tray lowered", List.of("switcher 0"), actions);
        assertFalse(RoseEdgeState.isRaised(DISPLAY));

        view = raise();
        view.onTouchEvent(touch(MotionEvent.ACTION_DOWN, x, y));
        view.onTouchEvent(touch(MotionEvent.ACTION_MOVE, x, y - dp(100)));
        view.onTouchEvent(touch(MotionEvent.ACTION_MOVE, x, y - dp(80)));
        assertEquals("hysteresis: still armed at 80dp", List.of("switcher 0"), actions);
        view.onTouchEvent(touch(MotionEvent.ACTION_MOVE, x, y - dp(60)));
        view.onTouchEvent(touch(MotionEvent.ACTION_UP, x, y - dp(60)));
        idle();
        assertEquals("dragged back below 72dp: nothing opens", List.of("switcher 0"), actions);
        assertTrue("and the tray stays", RoseEdgeState.isRaised(DISPLAY));
    }
}
