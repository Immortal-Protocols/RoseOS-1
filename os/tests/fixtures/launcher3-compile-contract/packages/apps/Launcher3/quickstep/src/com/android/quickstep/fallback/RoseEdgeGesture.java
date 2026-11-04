/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

/**
 * RoseOS (#410): the bottom-edge swipe-up over an app, as the selected Oxblood HiFi draws it (prototypes/rose-hifi at
 * f6503a0d: app.js "Bottom-edge gestures", oxblood.css "Edge gesture: live tracking" and "Home edge"). Pure logic with
 * no Android types and, for now, no caller: it fixes the release rules and the live preview geometry for Launcher3's
 * swipe handler and its raised-edge window. It is wired in only behind a builder compile of Launcher3 (#410 handoff),
 * so today it changes nothing at runtime.
 *
 * Release over an app (app.js endDrag), distances in dp upward from touch-down, speed averaged over the gesture:
 * - Home: more than HOME_DP, or more than HOME_FAST_DP at more than HOME_FAST_DP_PER_MS.
 * - Otherwise, more than RAISE_DP raises the edge. This holds at any release speed, so a short quick flick that
 *   Launcher3 calls a fling (more than quickstep_fling_threshold_speed, 0.5dp/ms at lift-off) raises the edge instead
 *   of going Home: the one deliberate change to stock navigation, from the reference.
 * - With the edge up: more than RAISED_HOME_DP goes Home at any speed; anything shorter leaves the edge as it is.
 *   Lowering it (swipe down on the edge, Escape, IME, leaving the app) belongs to the edge window.
 * Launcher3's own decision stands whenever it is not Home: a pause (the switcher), a quick switch to another task, a
 * cancel, a release with the finger moving back down, a downward fling (all back to the app), and every surface the
 * edge does not apply to. The caller maps only GestureEndTarget.HOME to STOCK_HOME (never RECENTS from a trackpad,
 * REJECT_HOME or NEW_TASK). A tiny swipe Launcher3 sends Home (a flick under RAISE_DP) also stays Home.
 *
 * Live preview while the finger is down (oxblood.css .peek-drag; never with the edge already up or reduced motion,
 * app.js pointermove: "if (peekDrag.wasPeek || ... || reduced()) return"):
 * - follow = min(1, up / FOLLOW_DP): the app window loses follow * LIFT_BOTTOM_DP at the bottom, at full width and
 *   unscaled, with follow * CORNER_DP bottom corners.
 * - beyond = clamp((up / FOLLOW_DP - 1) / BEYOND_RANGE, 0, 1): past the edge, toward Home, the app also lifts by
 *   beyond * LIFT_DP, shrinks by beyond * SHRINK about the shortened window's centre and rounds its top corners by
 *   beyond * CORNER_DP. Radii are in the window's own (pre-scale) space, as Launcher3's leash radius is.
 * Raised, the app's bottom stays LIFT_BOTTOM_DP up (the edge's height less its concave top) with CORNER_DP bottom
 * corners, which is also the inset the edge provides for the app's real relayout.
 */
public final class RoseEdgeGesture {
    private RoseEdgeGesture() {}

    /** app.js endDrag: dy < -36 raises; dy < -240, or dy < -120 with v < -0.9 px/ms, goes Home (strict). */
    public static final float RAISE_DP = 36, HOME_DP = 240, HOME_FAST_DP = 120, HOME_FAST_DP_PER_MS = 0.9f;
    /** With the edge up, a swipe of more than this goes Home (app.js endDrag, wasPeek: dy < -120). */
    public static final float RAISED_HOME_DP = 120;
    /** Preview: full follow over the first 120dp, then the leave-for-Home shrink over the next 1.2 * 120dp. */
    public static final float FOLLOW_DP = 120, BEYOND_RANGE = 1.2f;
    /** Home edge (contract homeEdge.height 92, flatTop 34): the app's bottom rests 92 - 34 = 58dp up. */
    public static final float EDGE_HEIGHT_DP = 92, EDGE_FLAT_TOP_DP = 34, LIFT_BOTTOM_DP = EDGE_HEIGHT_DP - EDGE_FLAT_TOP_DP;
    public static final float CORNER_DP = 34, LIFT_DP = 40, SHRINK = 0.26f;

    /** Where a release ends. STOCK means Launcher3's own end target stands unchanged. */
    public static final int STOCK = 0, RAISE = 1, HOME = 2, STAY = 3;

    /** Launcher3's own end target for the release, reduced to what the edge needs to know. */
    public static final int STOCK_LAST_TASK = 0, STOCK_HOME = 1, STOCK_RECENTS = 2, STOCK_NEW_TASK = 3;

    /**
     * The end of a swipe-up released over an app.
     *
     * @param eligible the edge applies: a task other than Home is in front (Home draws its own edge), the device is
     *     unlocked, gesture navigation, and the edge is enabled on this display
     * @param raised the edge is already up on this display
     * @param cancelled the gesture ended in ACTION_CANCEL (another window or the system took it)
     * @param paused Launcher3 detected a motion pause (its switcher rule)
     * @param stock Launcher3's own end target, one of STOCK_*; only STOCK_HOME is ever refined
     * @param upDp distance travelled upward since touch-down, in dp (negative when the finger ended lower)
     * @param upDpPerMs the gesture's average upward speed (upDp over its duration), in dp per ms
     */
    public static int release(boolean eligible, boolean raised, boolean cancelled, boolean paused, int stock,
            float upDp, float upDpPerMs) {
        if (!eligible || cancelled || paused || stock != STOCK_HOME || !(upDp == upDp)) return STOCK;
        if (raised) return upDp > RAISED_HOME_DP ? HOME : STAY;
        if (upDp > HOME_DP || (upDp > HOME_FAST_DP && upDpPerMs > HOME_FAST_DP_PER_MS)) return HOME;
        return upDp > RAISE_DP ? RAISE : STOCK;
    }

    /** 0..1: how far the app has followed toward its raised position. */
    public static float follow(float upDp, boolean raised, boolean reducedMotion) {
        if (raised || reducedMotion || !(upDp > 0)) return 0;
        return Math.min(1f, upDp / FOLLOW_DP);
    }

    /** 0..1: how far past the edge the drag has gone, toward Home. */
    public static float beyond(float upDp, boolean raised, boolean reducedMotion) {
        if (raised || reducedMotion || !(upDp > 0)) return 0;
        return Math.max(0f, Math.min(1f, (upDp / FOLLOW_DP - 1f) / BEYOND_RANGE));
    }

    /** dp the app window is lifted at the bottom (full width, never scaled for this part). */
    public static float bottomLiftDp(float follow) { return LIFT_BOTTOM_DP * clamp01(follow); }

    public static float bottomCornerDp(float follow) { return CORNER_DP * clamp01(follow); }

    public static float topCornerDp(float beyond) { return CORNER_DP * clamp01(beyond); }

    /** dp the app moves up past the edge (positive is up). */
    public static float liftDp(float beyond) { return LIFT_DP * clamp01(beyond); }

    public static float scale(float beyond) { return 1f - SHRINK * clamp01(beyond); }

    /**
     * The visible app rect for a screen of widthPx x heightPx at density, as {left, top, right, bottom} in px. While
     * dragging, the shortened window (bottom lift, full width) is scaled about its own centre and moved up; with
     * beyond == 0 it is exactly the full width. With the edge up the window stays at its raised rect and does not
     * track the finger; with reduced motion it is untouched until the result applies.
     */
    public static float[] previewRect(int widthPx, int heightPx, float density, float upDp, boolean raised,
            boolean reducedMotion) {
        if (raised) return new float[] {0, 0, widthPx, heightPx - LIFT_BOTTOM_DP * density};
        float f = follow(upDp, false, reducedMotion), b = beyond(upDp, false, reducedMotion);
        float bottom = heightPx - bottomLiftDp(f) * density;
        float s = scale(b), cx = widthPx / 2f, cy = bottom / 2f, lift = liftDp(b) * density;
        return new float[] {cx - cx * s, cy - cy * s - lift, cx + cx * s, cy + (bottom - cy) * s - lift};
    }

    /** The inset the raised edge provides above the navigation's own (gesture navigation adds nothing), in px. */
    public static int raisedInsetPx(float density) { return Math.round(LIFT_BOTTOM_DP * density); }

    private static float clamp01(float v) { return !(v > 0) ? 0 : Math.min(1f, v); }
}
