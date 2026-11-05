/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

/**
 * RoseOS (#410): the one bottom-origin swipe-up over an app, as visible stages (Nathan, 2026-11-04, #410 PM handoff).
 * Pure logic with no Android types: the stage machine, the release rules and the live preview geometry. Its caller is
 * RoseEdgeSwipe, from FallbackSwipeHandler via rose-edge-launcher3.patch, only while persist.wm.debug.rose_edge is on.
 *
 * Stages, by distance upward from touch-down in dp, each entered visibly (the tray, then the switcher) with a haptic:
 * - NONE: the first movement already starts revealing Rose's tray (reveal = up / TRAY_DP), with the app following.
 * - TRAY at TRAY_DP: the tray is fully shown. Leaving it needs the finger back below TRAY_EXIT_DP.
 * - SWITCHER at SWITCHER_DP, well apart from the tray: Launcher3's own switcher visibly takes over (recents attaches
 *   to the app, as a pause did before). Leaving it needs the finger back below SWITCHER_EXIT_DP.
 * Dragging back down walks back through the stages before release; the exits sit below the entries (hysteresis), so a
 * finger resting on a threshold cannot flicker between stages.
 *
 * Release:
 * - SWITCHER: the switcher opens. It wins over the release speed, so a stage the user saw never turns into Home.
 * - Otherwise a quick upward flick (lift-off speed above FLICK_DP_PER_MS) goes Home, as it always has.
 * - Otherwise a release moving clearly downward (faster than DOWN_DP_PER_MS) cancels.
 * - Otherwise TRAY raises the edge: the tray stays open and the app's height really changes, once, at full width.
 * - Otherwise (NONE, below the tray) it cancels: back to the app, nothing raised.
 * With the edge already up the stage starts at TRAY and never drops to NONE: release keeps it (STAY) unless the
 * switcher or a flick wins. Launcher3's own cancel and its horizontal quick switch always stand (the caller keeps them).
 *
 * Live preview while the finger is down (the app window's leash, full width, never with reduced motion):
 * - follow = reveal: the app's bottom lifts by follow * LIFT_BOTTOM_DP, matching the tray rising beneath it, with
 *   follow * CORNER_DP corners.
 * - approach = (up - TRAY_DP) / (SWITCHER_DP - TRAY_DP), clamped: on the way to the switcher the app also shrinks by
 *   approach * SHRINK about the shortened window's centre and lifts by approach * LIFT_DP, so progress toward the
 *   switcher is visible before it activates. In SWITCHER, Launcher3's own transform draws the app as a card.
 * Raised, the app's bottom stays LIFT_BOTTOM_DP up (the edge's height less its concave top) with CORNER_DP corners,
 * which is also the inset the edge provides for the app's real relayout.
 *
 * The thresholds are starting values to tune on the device, chosen far apart on purpose: a tray drag ends near
 * 48-90dp and slowly; the switcher needs about a quarter of the screen. They are not measured promises.
 */
public final class RoseEdgeGesture {
    private RoseEdgeGesture() {}

    /** Stages of a drag in progress. */
    public static final int NONE = 0, TRAY = 1, SWITCHER = 2;

    /** Tray: entered at TRAY_DP (fully revealed), left below TRAY_EXIT_DP. */
    public static final float TRAY_DP = 48, TRAY_EXIT_DP = 32;
    /** Switcher: entered at SWITCHER_DP, left below SWITCHER_EXIT_DP. */
    public static final float SWITCHER_DP = 220, SWITCHER_EXIT_DP = 170;
    /** A lift-off faster than this upward goes Home (below the switcher stage); Launcher3's fling floor is 0.5. */
    public static final float FLICK_DP_PER_MS = 1.2f;
    /** A lift-off faster than this downward cancels instead of raising. */
    public static final float DOWN_DP_PER_MS = 0.5f;
    /** A swipe down of more than this on the open tray lowers it (RoseEdgeWindow). */
    public static final float RAISE_DP = 36;
    /** On the open tray (RoseEdgeWindow), an upward drag of more than this opens the switcher; back below EXIT, no. */
    public static final float TRAY_TO_SWITCHER_DP = 96, TRAY_TO_SWITCHER_EXIT_DP = 72;

    /** Home edge (contract homeEdge.height 92, flatTop 34): the app's bottom rests 92 - 34 = 58dp up. */
    public static final float EDGE_HEIGHT_DP = 92, EDGE_FLAT_TOP_DP = 34, LIFT_BOTTOM_DP = EDGE_HEIGHT_DP - EDGE_FLAT_TOP_DP;
    public static final float CORNER_DP = 34, LIFT_DP = 24, SHRINK = 0.1f;

    /** Where a release ends. STOCK means Launcher3's own end target stands unchanged. */
    public static final int STOCK = 0, RAISE = 1, HOME = 2, STAY = 3, CANCEL = 4, SWITCHER_OPEN = 5;

    /**
     * The stage after a move to upDp, from the previous stage (hysteresis). With the edge up at the start the stage
     * never drops below TRAY.
     */
    public static int stage(int previous, float upDp, boolean raisedAtStart) {
        if (!(upDp == upDp)) return raisedAtStart ? Math.max(TRAY, previous) : previous;
        int floor = raisedAtStart ? TRAY : NONE;
        int next;
        if (upDp >= SWITCHER_DP) next = SWITCHER;
        else if (previous == SWITCHER && upDp >= SWITCHER_EXIT_DP) next = SWITCHER;
        else if (upDp >= TRAY_DP) next = TRAY;
        else if (previous >= TRAY && upDp >= TRAY_EXIT_DP) next = TRAY;
        else next = NONE;
        return Math.max(floor, next);
    }

    /**
     * The end of a staged release.
     *
     * @param stage the stage at lift-off (from stage())
     * @param raisedAtStart the edge was already up over this task when the gesture began
     * @param cancelled the gesture ended in ACTION_CANCEL (another window or the system took it)
     * @param upDpPerMs the finger's upward speed at lift-off, in dp per ms (negative when moving down)
     */
    public static int release(int stage, boolean raisedAtStart, boolean cancelled, float upDpPerMs) {
        if (cancelled) return STOCK;
        if (stage == SWITCHER) return SWITCHER_OPEN;
        float v = upDpPerMs == upDpPerMs ? upDpPerMs : 0;
        if (v > FLICK_DP_PER_MS) return HOME;
        if (v < -DOWN_DP_PER_MS) return raisedAtStart ? STAY : CANCEL;
        if (raisedAtStart) return STAY;
        return stage == TRAY ? RAISE : CANCEL;
    }

    /** 0..1: how much of the tray the drag has revealed (1 from TRAY_DP, and whenever the edge is already up). */
    public static float reveal(float upDp, boolean raisedAtStart) {
        if (raisedAtStart) return 1;
        return clamp01(upDp / TRAY_DP);
    }

    /** 0..1: progress from the tray toward the switcher. */
    public static float approach(float upDp) {
        return clamp01((upDp - TRAY_DP) / (SWITCHER_DP - TRAY_DP));
    }

    /** 0..1: how far the app has followed toward its raised position. */
    public static float follow(float upDp, boolean raisedAtStart, boolean reducedMotion) {
        if (raisedAtStart) return 1;
        if (reducedMotion) return 0;
        return reveal(upDp, false);
    }

    /** dp the app window is lifted at the bottom (full width, never scaled for this part). */
    public static float bottomLiftDp(float follow) { return LIFT_BOTTOM_DP * clamp01(follow); }

    public static float cornerDp(float follow) { return CORNER_DP * clamp01(follow); }

    /** dp the app moves up toward the switcher (positive is up). */
    public static float liftDp(float approach) { return LIFT_DP * clamp01(approach); }

    public static float scale(float approach) { return 1f - SHRINK * clamp01(approach); }

    /**
     * The visible app rect for a screen of widthPx x heightPx at density, as {left, top, right, bottom} in px: the
     * shortened window (bottom lift, full width) scaled about its own centre and moved up by the approach. With
     * reduced motion the app is untouched until the result applies.
     */
    public static float[] previewRect(int widthPx, int heightPx, float density, float upDp, boolean raisedAtStart,
            boolean reducedMotion) {
        float f = follow(upDp, raisedAtStart, reducedMotion), a = reducedMotion ? 0 : approach(upDp);
        float bottom = heightPx - bottomLiftDp(f) * density;
        float s = scale(a), cx = widthPx / 2f, cy = bottom / 2f, lift = liftDp(a) * density;
        return new float[] {cx - cx * s, cy - cy * s - lift, cx + cx * s, cy + (bottom - cy) * s - lift};
    }

    /** The inset the raised edge provides above the navigation's own (gesture navigation adds nothing), in px. */
    public static int raisedInsetPx(float density) { return Math.round(LIFT_BOTTOM_DP * density); }

    /** Content-free diagnostic names (developer-only logging). */
    public static String stageName(int stage) {
        return stage == SWITCHER ? "SWITCHER" : stage == TRAY ? "TRAY" : "NONE";
    }

    public static String releaseName(int release) {
        switch (release) {
            case RAISE: return "RAISE";
            case HOME: return "HOME";
            case STAY: return "STAY";
            case CANCEL: return "CANCEL";
            case SWITCHER_OPEN: return "SWITCHER";
            default: return "STOCK";
        }
    }

    private static float clamp01(float v) { return !(v > 0) ? 0 : Math.min(1f, v); }
}
