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
 * Off unless the system property persist.wm.debug.rose_edge is true, so a build that carries it navigates exactly
 * as stock Launcher3 until the owner turns it on. It also does nothing over Home, for a trackpad, without gesture
 * navigation, on a locked device, or when the display is not in its natural rotation; Launcher3 then decides
 * everything as before.
 *
 * Scope honestly stated: the preview crops the app's own window leash during the gesture (full width, unscaled).
 * Once raised, the app's real relayout comes from the edge window's inset (E2b), which moves content only in apps
 * that pad for system bars; it is not a window resize. No permission, IPC or microphone access here.
 */
public final class RoseEdgeSwipe {
    /** The owner's switch, default off (the same debug namespace the SystemUI tray uses). */
    public static final String PROPERTY = "persist.wm.debug.rose_edge";

    private final int displayId;
    private final float density;
    private final boolean excluded;
    private final boolean enabled;
    private final KeyguardManager keyguard;
    private final long startMs = SystemClock.uptimeMillis();

    /**
     * @param excluded the gesture runs over Home or comes from a trackpad: Launcher3 handles it unchanged
     */
    public RoseEdgeSwipe(Context context, int displayId, boolean excluded) {
        this.displayId = displayId;
        this.density = context.getResources().getDisplayMetrics().density;
        this.excluded = excluded;
        this.enabled = SystemProperties.getBoolean(PROPERTY, false);
        this.keyguard = context.getSystemService(KeyguardManager.class);
    }

    /** The owner's switch: false means the patched handler behaves exactly as stock Launcher3. */
    public boolean enabled() {
        return enabled;
    }

    /** The edge applies to this gesture at all. */
    public boolean active(boolean gestureNavigation, boolean naturalRotation) {
        return enabled && !excluded && gestureNavigation && naturalRotation
                && (keyguard == null || !keyguard.isKeyguardLocked());
    }

    /**
     * How the release ends: one of RoseEdgeGesture.STOCK, RAISE, HOME or STAY. RAISE raises the edge at once (the
     * app then animates back while the edge rises); STAY leaves it up. The caller maps RAISE and STAY to the
     * back-to-app end target and HOME to Home.
     *
     * @param upPx the finger's distance upward since touch-down, in px
     */
    public int release(boolean gestureNavigation, boolean naturalRotation, boolean cancelled, boolean paused,
            boolean stockHome, float upPx) {
        float upDp = upPx / density;
        long ms = Math.max(1, SystemClock.uptimeMillis() - startMs);
        int result = RoseEdgeGesture.release(active(gestureNavigation, naturalRotation), RoseEdgeState.isRaised(displayId),
                cancelled, paused, stockHome ? RoseEdgeGesture.STOCK_HOME : RoseEdgeGesture.STOCK_LAST_TASK, upDp,
                upDp / ms);
        if (result == RoseEdgeGesture.RAISE) RoseEdgeState.raise(displayId);
        return result;
    }

    /**
     * Called with the final end target: leaving the app (Home, the switcher, another task) lowers the edge. With the
     * switch off it does nothing at all, so stock Launcher3 never reaches RoseEdgeState.
     */
    public void settled(boolean backToApp) {
        if (enabled && !backToApp) RoseEdgeState.lower(displayId);
    }

    /**
     * The live preview of the app window for a shift equal to upPx upward: a window crop and matrix in the leash's own
     * space, and one corner radius (the leash has a single radius, so the top corners share the bottom's while the
     * app follows; the reference keeps them square until it moves past the edge). Returns false when the preview
     * does not apply and Launcher3's own transform should be used.
     */
    public boolean preview(boolean gestureNavigation, boolean naturalRotation, boolean attachedToRecents, float upPx,
            int widthPx, int heightPx, Matrix outMatrix, Rect outCrop, float[] outRadiusPx) {
        if (attachedToRecents || !active(gestureNavigation, naturalRotation) || widthPx <= 0 || heightPx <= 0) return false;
        boolean raised = RoseEdgeState.isRaised(displayId);
        boolean reduced = !ValueAnimator.areAnimatorsEnabled();
        float upDp = upPx / density;
        float follow = raised ? 1 : RoseEdgeGesture.follow(upDp, false, reduced);
        float beyond = RoseEdgeGesture.beyond(upDp, raised, reduced);
        float bottom = heightPx - RoseEdgeGesture.bottomLiftDp(follow) * density;
        outCrop.set(0, 0, widthPx, Math.round(bottom));
        float s = RoseEdgeGesture.scale(beyond);
        outMatrix.setScale(s, s, widthPx / 2f, bottom / 2f);
        outMatrix.postTranslate(0, -RoseEdgeGesture.liftDp(beyond) * density);
        outRadiusPx[0] = Math.max(RoseEdgeGesture.bottomCornerDp(follow), RoseEdgeGesture.topCornerDp(beyond)) * density;
        return true;
    }
}
