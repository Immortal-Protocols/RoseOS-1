/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

/**
 * RoseOS (#410): the bottom-edge swipe-up over an app, as the selected Oxblood HiFi draws it (prototypes/rose-hifi at
 * f6503a0d: app.js "Bottom-edge gestures", oxblood.css "Edge gesture: live tracking" and "Home edge"). Pure logic with
 * no Android types and, for now, no caller: it fixes the release rules and the live preview geometry that the Launcher3
 * swipe handler and the system's raised edge are to share. It is wired in only after OS Core's half of the interface
 * exists and both sides compile together on the builder (#410 handoff), so today it changes nothing at runtime.
 *
 * Release over an app, distances in dp upward from the touch-down point:
 * - Launcher3's own decision always wins for a fling (any upward fling goes Home), a pause (the switcher), a sideways
 *   quick switch, a cancel, a release with the finger moving back down (the app), and every surface the edge does not
 *   apply to. The edge only refines Launcher3's slow swipe Home (AbsSwipeUpHandler.calculateEndTargetForNonFling at
 *   47accce: velocity.y < 0 and mCanSlowSwipeGoHome, which needs 36dp): below HOME_DP the edge rises instead.
 * - With the edge already up, a further slow swipe goes Home only from RAISED_HOME_DP; anything shorter leaves the
 *   edge as it is. Lowering it (swipe down on the edge, Escape, IME, leaving the app) belongs to the edge window.
 *
 * Live preview while the finger is down (never with reduced motion: the result then applies at once):
 * - follow = min(1, up / FOLLOW_DP): the app window loses follow * LIFT_BOTTOM_DP at the bottom, at full width and
 *   unscaled, with follow * CORNER_DP bottom corners.
 * - beyond = clamp((up / FOLLOW_DP - 1) / BEYOND_RANGE, 0, 1): past the edge, toward Home, the app also lifts by
 *   beyond * LIFT_DP, shrinks by beyond * SHRINK and rounds its top corners by beyond * CORNER_DP.
 * Raised, the app's bottom stays LIFT_BOTTOM_DP up (the edge's height less its concave top), which is the inset the
 * edge provides for the app's real relayout.
 */
public final class RoseEdgeGesture {
    private RoseEdgeGesture() {}

    /** Release thresholds (app.js endDrag: dy < -36 raises; dy < -240, or dy < -120 with v < -0.9 px/ms, goes Home). */
    static final float RAISE_DP = 36, HOME_DP = 240;
    /** With the edge up, a swipe of this length goes Home (app.js endDrag, wasPeek: dy < -120). */
    static final float RAISED_HOME_DP = 120;
    /** Preview: full follow over the first 120dp, then the leave-for-Home shrink over the next 1.2 * 120dp. */
    static final float FOLLOW_DP = 120, BEYOND_RANGE = 1.2f;
    /** Home edge (contract homeEdge.height 92, flatTop 34): the app's bottom rests 92 - 34 = 58dp up. */
    static final float EDGE_HEIGHT_DP = 92, EDGE_FLAT_TOP_DP = 34, LIFT_BOTTOM_DP = EDGE_HEIGHT_DP - EDGE_FLAT_TOP_DP;
    static final float CORNER_DP = 34, LIFT_DP = 40, SHRINK = 0.26f;

    /** Where a release ends. STOCK means Launcher3's own end target stands unchanged. */
    static final int STOCK = 0, RAISE = 1, HOME = 2, STAY = 3;

    /** Launcher3's own end target for the release, reduced to what the edge needs to know. */
    static final int STOCK_LAST_TASK = 0, STOCK_HOME = 1, STOCK_RECENTS = 2, STOCK_NEW_TASK = 3;

    /**
     * The end of a swipe-up released over an app.
     *
     * @param eligible the edge applies: a full-screen app is in front (not Home, the switcher or a Rose surface Home
     *     draws itself), the device is unlocked, and OS Core's edge is present on this display
     * @param raised the edge is already up on this display
     * @param cancelled the gesture ended in ACTION_CANCEL (another window or the system took it)
     * @param fling Launcher3 classified the release as a fling
     * @param paused Launcher3 detected a motion pause (its switcher rule)
     * @param stock Launcher3's own end target, one of STOCK_*
     * @param upDp distance travelled upward since touch-down, in dp (negative when the finger ended lower)
     */
    static int release(boolean eligible, boolean raised, boolean cancelled, boolean fling, boolean paused, int stock,
            float upDp) {
        if (!eligible || cancelled || fling || paused || stock != STOCK_HOME || !(upDp == upDp)) return STOCK;
        if (raised) return upDp >= RAISED_HOME_DP ? HOME : STAY;
        if (upDp >= HOME_DP) return HOME;
        return upDp >= RAISE_DP ? RAISE : STOCK;
    }

    /** 0..1: how far the app has followed toward its raised position. */
    static float follow(float upDp, boolean reducedMotion) {
        if (reducedMotion || !(upDp > 0)) return 0;
        return Math.min(1f, upDp / FOLLOW_DP);
    }

    /** 0..1: how far past the edge the drag has gone, toward Home. */
    static float beyond(float upDp, boolean reducedMotion) {
        if (reducedMotion || !(upDp > 0)) return 0;
        return Math.max(0f, Math.min(1f, (upDp / FOLLOW_DP - 1f) / BEYOND_RANGE));
    }

    /** dp the app window is lifted at the bottom (full width, never scaled for this part). */
    static float bottomLiftDp(float follow) { return LIFT_BOTTOM_DP * clamp01(follow); }

    static float bottomCornerDp(float follow) { return CORNER_DP * clamp01(follow); }

    static float topCornerDp(float beyond) { return CORNER_DP * clamp01(beyond); }

    /** dp the app moves up past the edge (positive is up). */
    static float liftDp(float beyond) { return LIFT_DP * clamp01(beyond); }

    static float scale(float beyond) { return 1f - SHRINK * clamp01(beyond); }

    /**
     * The preview's visible app rect for a screen of widthPx x heightPx at density, as {left, top, right, bottom}
     * in px. The shortened window (bottom lift, full width) is scaled about its own centre and moved up. With
     * beyond == 0 the rect is exactly the full width.
     */
    static float[] previewRect(int widthPx, int heightPx, float density, float upDp, boolean reducedMotion) {
        float f = follow(upDp, reducedMotion), b = beyond(upDp, reducedMotion);
        float bottom = heightPx - bottomLiftDp(f) * density;
        float s = scale(b), cx = widthPx / 2f, cy = bottom / 2f, lift = liftDp(b) * density;
        return new float[] {cx - cx * s, cy - cy * s - lift, cx + cx * s, cy + (bottom - cy) * s - lift};
    }

    /** The inset the raised edge provides above the navigation's own (gesture navigation adds nothing), in px. */
    static int raisedInsetPx(float density) { return Math.round(LIFT_BOTTOM_DP * density); }

    private static float clamp01(float v) { return !(v > 0) ? 0 : Math.min(1f, v); }
}
