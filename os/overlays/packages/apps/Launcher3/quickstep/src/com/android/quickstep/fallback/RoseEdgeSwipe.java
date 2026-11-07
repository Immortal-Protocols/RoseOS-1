/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.animation.ValueAnimator;
import android.app.KeyguardManager;
import android.content.Context;
import android.graphics.Matrix;
import android.graphics.Rect;
import android.hardware.display.DisplayManager;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemProperties;
import android.util.Log;
import android.view.Display;
import android.view.Surface;

/**
 * RoseOS (#410): one bottom swipe-up over an app, for FallbackSwipeHandler (rose-edge-launcher3.patch), as visible
 * stages (RoseEdgeGesture): the first movement reveals Rose's tray, a release at the tray keeps it open and really
 * shortens the app once (WMShell, RoseEdgeShell), going much farther visibly activates the system switcher, and a
 * quick flick before that goes Home. The handler feeds every move (onMove) and the release (release); this class
 * keeps the stage, tells the edge window to draw the tray as it is revealed (RoseEdgeState.peek), and raises the edge.
 *
 * Staged or not is decided once, when the gesture starts (staged()). A staged gesture replaces Launcher3's motion-pause
 * switcher rule with the switcher stage, so a slow drag shows the tray instead of the switcher; an unstaged gesture is
 * stock Launcher3 throughout. Not staged: the switch persist.wm.debug.rose_edge off (the default), over Home, for a
 * trackpad, for a gesture that continues the previous one, for a task that is not full screen, in split screen,
 * without gesture navigation, with the keyboard showing (it owns the bottom of the screen; the shell also lowers the
 * edge for it), on a locked device, when the display is not in its natural rotation, while no edge window is
 * registered to draw the tray, or while WMShell's IRoseEdge is unavailable (RoseEdgeState.canRaise). Launcher3's own
 * cancel and its horizontal quick switch always stand.
 *
 * Developer-only diagnostics: with persist.wm.debug.rose_edge_diag true, each gesture logs (tag RoseEdgeGesture) whether
 * it was staged and why not, each stage entered with its distance, and the release's distance, lift-off speed and
 * destination, when a tray release asks the shell to shorten the app, and the shell's answers (RoseEdgeState). Never
 * app content, task names or coordinates beyond the vertical distance; off by default.
 *
 * A release at the tray keeps the tray drawn at once, but the shell is asked to shorten the app only once Launcher3's
 * recents transition has finished back to the app (returnedToApp): until then that transition owns the app's task,
 * and a resize sent at finger-up raced it and the tray came down again (Frankel2026110501). If the return never
 * completes (the gesture is cancelled, another gesture starts, or nothing arrives within PENDING_RAISE_TIMEOUT_MS),
 * the pending raise is dropped and the tray goes with it, exactly like a release below the tray.
 *
 * The app follows on its own window leash during the gesture (full width, unscaled until it approaches the switcher)
 * and Launcher3 resets that leash when its recents animation finishes. On a raise the shell resizes the app's real
 * window area (its height less liftPx, width unchanged): one configuration change and relayout, never a scaled picture.
 * No new permission and no microphone access here; the only IPC is RoseEdgeShell.
 */
public final class RoseEdgeSwipe {
    /** The build's switch, default off (the same debug namespace the SystemUI tray uses). */
    public static final String PROPERTY = "persist.wm.debug.rose_edge";
    /** Developer-only gesture diagnostics in the log, default off. */
    public static final String DIAG_PROPERTY = "persist.wm.debug.rose_edge_diag";
    static final String TAG = "RoseEdgeGesture";
    /** How long a tray release waits for the recents transition to finish back to the app before it gives up. */
    static final long PENDING_RAISE_TIMEOUT_MS = 3000;
    /** The one gesture whose tray release is waiting for returnedToApp, or null. Main thread only. */
    private static RoseEdgeSwipe sPendingRaise;

    private final int displayId;
    private final int taskId;
    private final float density;
    private final float touchSlopPx;
    private final boolean enabled;
    private final boolean raisedAtStart;
    private final boolean resizedAtStart;
    private final boolean staged;
    private final boolean diag;
    private int stage;
    private float upDp;
    private boolean peeking;
    /** The release (or abandon) happened: later moves change nothing and never reveal the tray again. */
    private boolean ended;
    /** A tray release is waiting for the recents transition to finish back to the app (returnedToApp). */
    private boolean pendingRaise;
    private Handler timeoutHandler;
    private final Runnable pendingTimeout = () -> dropPendingRaise("timed out");

    /**
     * @param taskId the task the gesture runs over
     * @param touchSlopPx the slop Launcher3 subtracts before the window moves (RecentsAnimationDeviceState)
     * @param excluded Launcher3 handles this gesture unchanged (Home, a trackpad, a continued gesture, not full screen)
     * @param gestureNavigation fully gestural navigation
     * @param imeVisible the keyboard is showing
     */
    public RoseEdgeSwipe(Context context, int displayId, int taskId, float touchSlopPx, boolean excluded,
            boolean gestureNavigation, boolean imeVisible) {
        this.displayId = displayId;
        this.taskId = taskId;
        this.density = context.getResources().getDisplayMetrics().density;
        this.touchSlopPx = Math.max(0, touchSlopPx);
        this.enabled = SystemProperties.getBoolean(PROPERTY, false);
        this.diag = enabled && SystemProperties.getBoolean(DIAG_PROPERTY, false);
        // A new gesture supersedes a tray release still waiting for its return to the app: its own recents
        // transition would own the app again, so that raise is never sent.
        if (sPendingRaise != null) sPendingRaise.dropPendingRaise("new gesture");
        this.raisedAtStart = enabled && RoseEdgeState.isRaised(displayId, taskId);
        this.resizedAtStart = raisedAtStart && RoseEdgeState.isResized(displayId, taskId);
        String why = !enabled ? "switch off" : excluded ? "excluded" : !gestureNavigation ? "not gesture navigation"
                : imeVisible ? "keyboard showing" : !naturalRotation(context, displayId) ? "rotated"
                : locked(context) ? "locked" : !RoseEdgeState.hasListener() ? "no edge window"
                : !RoseEdgeShell.available() ? "shell unavailable" : null;
        this.staged = why == null;
        this.stage = raisedAtStart ? RoseEdgeGesture.TRAY : RoseEdgeGesture.NONE;
        if (diag) Log.i(TAG, "start: staged=" + staged + (why == null ? "" : " (" + why + ")") + " raised=" + raisedAtStart);
    }

    private static boolean naturalRotation(Context context, int displayId) {
        DisplayManager displays = context.getSystemService(DisplayManager.class);
        Display display = displays == null ? null : displays.getDisplay(displayId);
        return display != null && display.getRotation() == Surface.ROTATION_0;
    }

    private static boolean locked(Context context) {
        KeyguardManager keyguard = context.getSystemService(KeyguardManager.class);
        return keyguard != null && keyguard.isKeyguardLocked();
    }

    /** The build's switch, read once by the host before it starts the edge window. */
    public static boolean switchOn() {
        return SystemProperties.getBoolean(PROPERTY, false);
    }

    /** The build's switch: false means stock end targets and transforms (see the class comment for the one exception). */
    public boolean enabled() {
        return enabled;
    }

    /** This gesture is staged: the handler routes moves, pauses and the release through this class. */
    public boolean staged() {
        return staged;
    }

    /** The current stage (RoseEdgeGesture.NONE, TRAY or SWITCHER). */
    public int stage() {
        return stage;
    }

    /**
     * A move. upPxAfterSlop is the handler's displacement: the finger's distance upward less Launcher3's touch slop,
     * in px; the slop is added back, so the stages count from touch-down. Returns the stage after the move; the
     * handler gives the haptic and attaches or detaches Launcher3's switcher when it changes.
     */
    public int onMove(float upPxAfterSlop) {
        if (!staged || ended) return stage;
        upDp = (upPxAfterSlop + touchSlopPx) / density;
        int previous = stage;
        stage = RoseEdgeGesture.stage(previous, upDp, raisedAtStart);
        if (!raisedAtStart) {
            peeking = true;
            RoseEdgeState.peek(displayId, RoseEdgeGesture.reveal(upDp, false), RoseEdgeGesture.approach(upDp), stage);
        }
        if (diag && stage != previous) {
            Log.i(TAG, "stage " + RoseEdgeGesture.stageName(stage) + " at " + Math.round(upDp) + "dp");
        }
        return stage;
    }

    /** A tray release is waiting for returnedToApp (tests and the handler read it). */
    public boolean raisePending() {
        return pendingRaise;
    }

    /**
     * How the release ends: one of RoseEdgeGesture.STOCK, RAISE, HOME, STAY, CANCEL or SWITCHER_OPEN. RAISE keeps
     * the tray drawn and waits for returnedToApp to ask the shell to shorten the app (it falls back to CANCEL if the
     * edge cannot rise at all); the caller maps RAISE, STAY and CANCEL to the back-to-app end target, HOME to Home and
     * SWITCHER_OPEN to the switcher.
     *
     * @param endVelocityPxPerMs Launcher3's lift-off velocity along the swipe (negative is upward), in px per ms
     */
    public int release(boolean cancelled, float endVelocityPxPerMs) {
        if (!staged || ended) return RoseEdgeGesture.STOCK;
        ended = true;
        float upDpPerMs = -endVelocityPxPerMs / density;
        int result = RoseEdgeGesture.release(stage, raisedAtStart, cancelled, upDpPerMs);
        if (result == RoseEdgeGesture.RAISE && !RoseEdgeState.canRaise()) {
            result = RoseEdgeGesture.CANCEL;
        }
        if (result == RoseEdgeGesture.RAISE) {
            // The tray stays fully shown while the recents transition finishes back to the app.
            pendingRaise = true;
            sPendingRaise = this;
            RoseEdgeState.peek(displayId, 1f, 0f, RoseEdgeGesture.TRAY);
            if (timeoutHandler == null) timeoutHandler = new Handler(Looper.getMainLooper());
            timeoutHandler.postDelayed(pendingTimeout, PENDING_RAISE_TIMEOUT_MS);
        } else {
            endPeek();
        }
        if (diag) {
            Log.i(TAG, "release at " + Math.round(upDp) + "dp, " + (Math.round(upDpPerMs * 100) / 100f) + "dp/ms, stage "
                    + RoseEdgeGesture.stageName(stage) + (cancelled ? " (cancelled)" : "") + " -> "
                    + RoseEdgeGesture.releaseName(result));
        }
        return result;
    }

    /**
     * The release went back to the app and Launcher3's recents transition has finished (AbsSwipeUpHandler's
     * onLastTaskResumed): a tray release now raises the edge over this task and asks the shell to shorten the app.
     * Anything else does nothing.
     */
    public void returnedToApp() {
        if (!pendingRaise) return;
        clearPending();
        boolean raised = RoseEdgeState.raise(displayId, taskId, RoseEdgeGesture.raisedInsetPx(density));
        if (diag) Log.i(TAG, raised ? "returned to the app: raise sent" : "returned to the app: raise not possible");
        endPeek();
    }

    /**
     * The gesture ended without a release this class saw (the recents animation was cancelled, the consumer left),
     * or after one: a tray release still waiting for returnedToApp is dropped with it.
     */
    public void abandon() {
        if (pendingRaise) {
            dropPendingRaise("abandoned");
            return;
        }
        if (diag && peeking) Log.i(TAG, "abandoned at stage " + RoseEdgeGesture.stageName(stage));
        ended = true;
        endPeek();
    }

    /** Drops a tray release still waiting for returnedToApp: nothing is raised and the tray goes. */
    void dropPendingRaise(String why) {
        if (!pendingRaise) return;
        clearPending();
        if (diag) Log.i(TAG, "pending raise dropped (" + why + ")");
        ended = true;
        endPeek();
    }

    private void clearPending() {
        pendingRaise = false;
        if (sPendingRaise == this) sPendingRaise = null;
        if (timeoutHandler != null) timeoutHandler.removeCallbacks(pendingTimeout);
    }

    private void endPeek() {
        if (!peeking) return;
        peeking = false;
        RoseEdgeState.endPeek(displayId);
    }

    /**
     * Called with the final end target of every release, atomic ones included: leaving the app (Home, the switcher,
     * another task) lowers the edge. With the switch off it does nothing at all, so stock Launcher3 never reaches
     * RoseEdgeState.
     */
    public void settled(boolean backToApp) {
        if (!backToApp) dropPendingRaise("left the app");
        if (!pendingRaise) endPeek();
        if (enabled && !backToApp) RoseEdgeState.lower(displayId);
    }

    /**
     * The live preview of the app window for a shift of upPx upward (Launcher3's own, which starts once the slop is
     * passed): a window crop and matrix in the leash's own space, and one corner radius. Whether the edge is up is
     * read once, at the start of the gesture, so a raise at release does not snap the window: it animates back with
     * the shift. Returns false when the preview does not apply (not staged, or Launcher3's switcher has attached) and
     * Launcher3's own transform should be used.
     */
    public boolean preview(boolean attachedToRecents, float upPx, int widthPx, int heightPx, Matrix outMatrix,
            Rect outCrop, float[] outRadiusPx) {
        if (!staged || attachedToRecents || widthPx <= 0 || heightPx <= 0) return false;
        boolean reduced = !ValueAnimator.areAnimatorsEnabled();
        float up = upPx / density;
        float follow = RoseEdgeGesture.follow(up, raisedAtStart, reduced);
        float approach = reduced ? 0 : RoseEdgeGesture.approach(up);
        // Raised and already shortened by the shell: the window is at its raised size, so its bottom is not cropped
        // again (it keeps the raised corners).
        float bottom = heightPx - RoseEdgeGesture.bottomLiftDp(resizedAtStart ? 0 : follow) * density;
        outCrop.set(0, 0, widthPx, Math.round(bottom));
        float s = RoseEdgeGesture.scale(approach);
        outMatrix.setScale(s, s, widthPx / 2f, bottom / 2f);
        outMatrix.postTranslate(0, -RoseEdgeGesture.liftDp(approach) * density);
        outRadiusPx[0] = RoseEdgeGesture.cornerDp(follow) * density;
        return true;
    }
}
