package com.android.quickstep.fallback;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.content.Context;
import android.graphics.Matrix;
import android.graphics.Rect;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.os.SystemProperties;
import com.android.wm.shell.roseedge.IRoseEdge;
import com.android.wm.shell.roseedge.IRoseEdgeListener;
import java.lang.reflect.Field;
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
 * #410 E2c (staged): the staged Launcher3 client for WMShell's raised-edge resize, compiled unchanged, against Java the
 * SDK's aidl tool generated from os/staged/rose-edge-resize/aidl, on Robolectric's SDK 35 runtime. The fake shell is the
 * generated IRoseEdge.Stub (as OS Core's implementation would be), and it replies through the generated
 * IRoseEdgeListener.Proxy, so every call crosses the real Parcel wire format both ways. It shows wire compatibility and
 * Launcher3's state rules; it does not show WMShell's resize, a compiled Launcher3 or any device behaviour.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
public class RoseEdgeResizeNativeTest {
    static final int DISPLAY = 0, TASK = 41, OTHER_TASK = 42;
    static final float SLOP_DP = 11;

    /** OS Core's side as the generated Stub sees it: records every call and keeps the listener it was given. */
    static final class FakeShell extends IRoseEdge.Stub {
        final List<String> calls = new ArrayList<>();
        IRoseEdgeListener listener;

        @Override
        public void raise(int displayId, int taskId, int liftPx, IRoseEdgeListener l) {
            calls.add("raise " + displayId + " " + taskId + " " + liftPx);
            listener = l;
        }

        @Override
        public void lower(int displayId) {
            calls.add("lower " + displayId);
        }
    }

    Context context;
    float density;
    FakeShell shell;
    final List<String> events = new ArrayList<>();
    final RoseEdgeState.Listener window = (display, raised) -> events.add(display + (raised ? " up" : " down"));

    @Before
    public void setUp() {
        context = RuntimeEnvironment.getApplication();
        density = context.getResources().getDisplayMetrics().density;
        shell = new FakeShell();
        RoseEdgeShell.setShell(shell);
        RoseEdgeState.lower(DISPLAY);
        RoseEdgeState.addListener(window);
        shell.calls.clear();
        events.clear();
    }

    @After
    public void tearDown() {
        RoseEdgeState.removeListener(window);
        RoseEdgeState.lower(DISPLAY);
        RoseEdgeShell.setShell(null);
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, "");
    }

    static void idle() {
        shadowOf(Looper.getMainLooper()).idle();
    }

    static int transactionId(Class<?> stub, String name) throws Exception {
        Field f = stub.getDeclaredField(name);
        f.setAccessible(true);
        return f.getInt(null);
    }

    @Test
    public void theHandWrittenClientMatchesTheGeneratedInterfaceExactly() throws Exception {
        assertEquals(IRoseEdge.DESCRIPTOR, RoseEdgeShell.DESCRIPTOR);
        assertEquals(IRoseEdgeListener.DESCRIPTOR, RoseEdgeShell.LISTENER_DESCRIPTOR);
        assertEquals(transactionId(IRoseEdge.Stub.class, "TRANSACTION_raise"), RoseEdgeShell.TRANSACTION_RAISE);
        assertEquals(transactionId(IRoseEdge.Stub.class, "TRANSACTION_lower"), RoseEdgeShell.TRANSACTION_LOWER);
        assertEquals(transactionId(IRoseEdgeListener.Stub.class, "TRANSACTION_onRaised"), RoseEdgeShell.TRANSACTION_ON_RAISED);
        assertEquals(transactionId(IRoseEdgeListener.Stub.class, "TRANSACTION_onRefused"),
                RoseEdgeShell.TRANSACTION_ON_REFUSED);
        assertEquals(transactionId(IRoseEdgeListener.Stub.class, "TRANSACTION_onLowered"),
                RoseEdgeShell.TRANSACTION_ON_LOWERED);
    }

    @Test
    public void callsAndRepliesCrossTheRealWireFormatBothWays() throws Exception {
        List<String> replies = new ArrayList<>();
        RoseEdgeShell.Callback callback = new RoseEdgeShell.Callback() {
            @Override public void onRaised(int d, int t, Rect b) { replies.add("raised " + d + " " + t + " " + b); }
            @Override public void onRefused(int d, int t, int r) { replies.add("refused " + d + " " + t + " " + r); }
            @Override public void onLowered(int d, int r) { replies.add("lowered " + d + " " + r); }
        };
        assertTrue(RoseEdgeShell.raise(3, 77, 174, callback));
        assertTrue(RoseEdgeShell.lower(3));
        assertEquals(List.of("raise 3 77 174", "lower 3"), shell.calls);
        assertNotNull("the generated Stub read a listener binder", shell.listener);
        IBinder binder = shell.listener.asBinder();
        assertEquals(IRoseEdgeListener.DESCRIPTOR, binder.getInterfaceDescriptor());
        shell.listener.onRaised(3, 77, new Rect(0, 0, 1152, 2322));
        shell.listener.onRefused(3, 77, RoseEdgeShell.REFUSED_NOT_RESIZEABLE);
        shell.listener.onLowered(3, RoseEdgeShell.LOWERED_IME_SHOWN);
        assertTrue("replies wait for the main thread", replies.isEmpty());
        idle();
        assertEquals(List.of("raised 3 77 Rect(0, 0 - 1152, 2322)", "refused 3 77 2", "lowered 3 4"), replies);
    }

    @Test
    public void withoutTheShellTheEdgeNeverRises() {
        RoseEdgeShell.setShell(null);
        assertFalse(RoseEdgeState.canRaise());
        assertFalse(RoseEdgeState.raise(DISPLAY, TASK, 174));
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertTrue(events.isEmpty());
    }

    @Test
    public void withoutAnEdgeWindowTheShellIsNeverAsked() {
        RoseEdgeState.removeListener(window);
        assertFalse(RoseEdgeState.raise(DISPLAY, TASK, 174));
        assertTrue(shell.calls.isEmpty());
    }

    @Test
    public void aRaiseShowsTheEdgeAndAsksTheShellAndItsBoundsArrive() {
        assertTrue(RoseEdgeState.raise(DISPLAY, TASK, 174));
        assertEquals(List.of("0 up"), events);
        assertEquals(List.of("raise 0 41 174"), shell.calls);
        assertTrue(RoseEdgeState.isRaised(DISPLAY, TASK));
        assertFalse("not resized until the shell replies", RoseEdgeState.isResized(DISPLAY, TASK));
        try {
            shell.listener.onRaised(DISPLAY, TASK, new Rect(0, 0, 1152, 2322));
        } catch (android.os.RemoteException e) {
            throw new AssertionError(e);
        }
        idle();
        assertTrue(RoseEdgeState.isResized(DISPLAY, TASK));
        assertEquals(new Rect(0, 0, 1152, 2322), RoseEdgeState.resizedBounds(DISPLAY));
        assertTrue("raising again over the same task is a no-op", RoseEdgeState.raise(DISPLAY, TASK, 174));
        assertEquals(1, shell.calls.size());
    }

    @Test
    public void aRefusalTakesTheEdgeDownWithNothingResized() throws Exception {
        RoseEdgeState.raise(DISPLAY, TASK, 174);
        shell.listener.onRefused(DISPLAY, TASK, RoseEdgeShell.REFUSED_NOT_RESIZEABLE);
        idle();
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertEquals(List.of("0 up", "0 down"), events);
        assertEquals("a refusal needs no lower", List.of("raise 0 41 174"), shell.calls);
    }

    @Test
    public void aStaleRefusalForAnotherTaskIsIgnored() throws Exception {
        RoseEdgeState.raise(DISPLAY, TASK, 174);
        IRoseEdgeListener first = shell.listener;
        RoseEdgeState.raise(DISPLAY, OTHER_TASK, 174);
        first.onRefused(DISPLAY, TASK, RoseEdgeShell.REFUSED_BUSY);
        idle();
        assertTrue(RoseEdgeState.isRaised(DISPLAY, OTHER_TASK));
    }

    @Test
    public void theShellLoweringByItselfLowersTheEdgeWithoutAskingAgain() throws Exception {
        RoseEdgeState.raise(DISPLAY, TASK, 174);
        shell.listener.onRaised(DISPLAY, TASK, new Rect(0, 0, 1152, 2322));
        shell.listener.onLowered(DISPLAY, RoseEdgeShell.LOWERED_IME_SHOWN);
        idle();
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
        assertNull(RoseEdgeState.resizedBounds(DISPLAY));
        assertEquals(List.of("raise 0 41 174"), shell.calls);
    }

    @Test
    public void launcherLoweringAsksTheShellToRestoreFullHeightOnce() {
        RoseEdgeState.raise(DISPLAY, TASK, 174);
        RoseEdgeState.lower(DISPLAY);
        RoseEdgeState.lower(DISPLAY);
        assertEquals(List.of("raise 0 41 174", "lower 0"), shell.calls);
        assertEquals(List.of("0 up", "0 down"), events);
    }

    @Test
    public void aDeadShellBinderMeansNoRaise() {
        RoseEdgeShell.setShell(new android.os.Binder() {
            @Override public boolean isBinderAlive() { return false; }
        });
        assertFalse(RoseEdgeState.canRaise());
    }

    RoseEdgeSwipe swipe(boolean on, long msAgo) {
        SystemProperties.set(RoseEdgeSwipe.PROPERTY, on ? "true" : "");
        return new RoseEdgeSwipe(context, DISPLAY, TASK, SystemClock.uptimeMillis() - msAgo, SLOP_DP * density, false);
    }

    int release(RoseEdgeSwipe s, float dp) {
        return s.release(true, true, false, false, true, (dp - SLOP_DP) * density);
    }

    @Test
    public void aShortSwipeAsksForTheReferenceLiftOf58dp() {
        assertEquals(RoseEdgeGesture.RAISE, release(swipe(true, 400), 60));
        int lift = Math.round(58 * density);
        assertEquals(List.of("raise 0 41 " + lift), shell.calls);
        assertEquals(RoseEdgeGesture.raisedInsetPx(density), lift);
    }

    @Test
    public void withoutTheShellAShortSwipeStaysStockLauncher3() {
        RoseEdgeShell.setShell(null);
        assertEquals(RoseEdgeGesture.STOCK, release(swipe(true, 400), 60));
        assertFalse(RoseEdgeState.isRaised(DISPLAY));
    }

    @Test
    public void defaultOffNeverReachesTheShell() {
        assertEquals(RoseEdgeGesture.STOCK, release(swipe(false, 400), 60));
        assertTrue(shell.calls.isEmpty());
    }

    @Test
    public void aLongSwipeStillGoesHome() {
        assertEquals(RoseEdgeGesture.HOME, release(swipe(true, 400), 260));
        assertTrue(shell.calls.isEmpty());
    }

    @Test
    public void overAResizedAppTheBottomIsNotCroppedAgainButKeepsItsCorners() throws Exception {
        RoseEdgeState.raise(DISPLAY, TASK, RoseEdgeGesture.raisedInsetPx(density));
        int width = Math.round(384 * density), shortened = Math.round((832 - 58) * density);
        shell.listener.onRaised(DISPLAY, TASK, new Rect(0, 0, width, shortened));
        idle();
        RoseEdgeSwipe s = swipe(true, 100);
        Matrix m = new Matrix();
        Rect crop = new Rect();
        float[] radius = new float[1];
        assertTrue(s.preview(true, true, false, 0, width, shortened, m, crop, radius));
        assertEquals(new Rect(0, 0, width, shortened), crop);
        assertTrue(m.isIdentity());
        assertEquals(34 * density, radius[0], 0.01f);
    }
}
