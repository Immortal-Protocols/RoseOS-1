/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.animation.ValueAnimator;
import android.app.KeyguardManager;
import android.content.Context;
import android.graphics.Matrix;
import android.graphics.Rect;
import android.os.SystemClock;
import android.os.SystemProperties;

/**
 * RoseOS (#410, E2a): one bottom swipe-up over an app, for FallbackSwipeHandler (rose-edge-launcher3.patch). It asks
 * RoseEdgeGesture how the release ends and what the live preview looks like, and keeps RoseEdgeState up to date.
 *
 * Off unless the system property persist.wm.debug.rose_edge is true. It is a build's switch: set at build time
 * (a product property, as the agent tray's is) or with setprop on userdebug/eng builds; a user build offers the owner
 * no way to set it. Whether Launcher3's SELinux domain may read it is assumed from the SystemUI tray's precedent and
 * must be confirmed on the builder. Off, the patched handler behaves as stock Launcher3, with one exception the patch
 * makes regardless of this switch: its HOME starts share Launcher3's identity (ActivityOptions.setShareIdentityEnabled)
 * so the home app can verify a gesture_nav_contract_v1 request before replying.
 *
 * On, it still does nothing (Launcher3 decides as before) over Home, for a trackpad, for a gesture that continues the
 * previous one, for a task that is not full screen, in split screen, without gesture navigation, on a locked device,
 * when the display is not in its natural rotation, or while no edge window (E2b) is registered to draw the edge.
 *
 * Scope honestly stated: the preview crops the app's own window leash during the gesture (full width, unscaled), and
 * Launcher3 resets that leash when its recents animation finishes. Once raised, the app's relayout comes from the edge
 * window's inset (E2b), which moves content only in apps that pad for system bars; it is not a window resize. No
 * permission, IPC or microphone access here.
 */
public final class RoseEdgeSwipe {
    /** The build's switch, default off (the same debug namespace the SystemUI tray uses). */
    public static final String PROPERTY = "persist.wm.debug.rose_edge";

    private final int displayId;
    private final int taskId;
    private final float density;
    private final float touchSlopPx;
    private final long downTimeMs;
    private final boolean excluded;
    private final boolean enabled;
    private final boolean raisedAtStart;
    private final KeyguardManager keyguard;

    /**
     * @param taskId the task the gesture runs over
     * @param downTimeMs uptime of the gesture's touch-down (GestureState's start time)
     * @param touchSlopPx the slop Launcher3 subtracts before the window moves (RecentsAnimationDeviceState)
     * @param excluded Launcher3 handles this gesture unchanged (see the class comment)
     */
    public RoseEdgeSwipe(Context context, int displayId, int taskId, long downTimeMs, float touchSlopPx,
            boolean excluded) {
        this.displayId = displayId;
        this.taskId = taskId;
        this.density = context.getResources().getDisplayMetrics().density;
        this.touchSlopPx = Math.max(0, touchSlopPx);
        this.downTimeMs = downTimeMs;
        this.excluded = excluded;
        this.enabled = SystemProperties.getBoolean(PROPERTY, false);
        this.raisedAtStart = enabled && RoseEdgeState.isRaised(displayId, taskId);
        this.keyguard = enabled ? context.getSystemService(KeyguardManager.class) : null;
    }

    /** The build's switch: false means stock end targets and transforms (see the class comment for the one exception). */
    public boolean enabled() {
        return enabled;
    }

    /** The edge applies to this gesture at all. */
    public boolean active(boolean gestureNavigation, boolean naturalRotation) {
        return enabled && !excluded && gestureNavigation && naturalRotation && RoseEdgeState.hasListener()
                && (keyguard == null || !keyguard.isKeyguardLocked());
    }

    /**
     * How the release ends: one of RoseEdgeGesture.STOCK, RAISE, HOME or STAY. RAISE raises the edge over this task
     * at once (the app then animates back while the edge rises); STAY leaves it up. The caller maps RAISE and STAY to
     * the back-to-app end target and HOME to Home.
     *
     * @param upPxAfterSlop the handler's displacement: the finger's distance upward less Launcher3's touch slop, in
     *     px. The slop is added back here, so the thresholds count from touch-down as the reference's do; the speed
     *     is that distance over the time since touch-down.
     */
    public int release(boolean gestureNavigation, boolean naturalRotation, boolean cancelled, boolean paused,
            boolean stockHome, float upPxAfterSlop) {
        float upDp = (upPxAfterSlop + touchSlopPx) / density;
        long ms = Math.max(1, SystemClock.uptimeMillis() - downTimeMs);
        int result = RoseEdgeGesture.release(active(gestureNavigation, naturalRotation), raisedAtStart, cancelled,
                paused, stockHome ? RoseEdgeGesture.STOCK_HOME : RoseEdgeGesture.STOCK_LAST_TASK, upDp, upDp / ms);
        if (result == RoseEdgeGesture.RAISE && !RoseEdgeState.raise(displayId, taskId)) return RoseEdgeGesture.STOCK;
        return result;
    }

    /**
     * Called with the final end target of every release, atomic ones included: leaving the app (Home, the switcher,
     * another task) lowers the edge. With the switch off it does nothing at all, so stock Launcher3 never reaches
     * RoseEdgeState.
     */
    public void settled(boolean backToApp) {
        if (enabled && !backToApp) RoseEdgeState.lower(displayId);
    }

    /**
     * The live preview of the app window for a shift of upPx upward (Launcher3's own, which starts once the slop is
     * passed): a window crop and matrix in the leash's own space, and one corner radius. Whether the edge is up is
     * read once, at the start of the gesture, so a raise at release does not snap the window: it animates back with
     * the shift. The leash has a single radius, so the top corners share the bottom's whenever the app has followed
     * at all, including the whole raised state; the reference keeps them square until the app moves past the edge.
     * Returns false when the preview does not apply and Launcher3's own transform should be used.
     */
    public boolean preview(boolean gestureNavigation, boolean naturalRotation, boolean attachedToRecents, float upPx,
            int widthPx, int heightPx, Matrix outMatrix, Rect outCrop, float[] outRadiusPx) {
        if (attachedToRecents || !active(gestureNavigation, naturalRotation) || widthPx <= 0 || heightPx <= 0) return false;
        boolean reduced = !ValueAnimator.areAnimatorsEnabled();
        float upDp = upPx / density;
        float follow = raisedAtStart ? 1 : RoseEdgeGesture.follow(upDp, false, reduced);
        float beyond = RoseEdgeGesture.beyond(upDp, raisedAtStart, reduced);
        float bottom = heightPx - RoseEdgeGesture.bottomLiftDp(follow) * density;
        outCrop.set(0, 0, widthPx, Math.round(bottom));
        float s = RoseEdgeGesture.scale(beyond);
        outMatrix.setScale(s, s, widthPx / 2f, bottom / 2f);
        outMatrix.postTranslate(0, -RoseEdgeGesture.liftDp(beyond) * density);
        outRadiusPx[0] = Math.max(RoseEdgeGesture.bottomCornerDp(follow), RoseEdgeGesture.topCornerDp(beyond)) * density;
        return true;
    }
}
