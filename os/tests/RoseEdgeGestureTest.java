package com.android.quickstep.fallback;

/**
 * #410: RoseEdgeGesture, the staged bottom swipe-up (Nathan, 2026-11-04): the first movement reveals the tray, a
 * release at the tray keeps it open (RAISE), going substantially farther activates the switcher (it wins over the
 * release speed), a quick flick before that goes Home, dragging back walks the stages back with hysteresis, and a
 * release below the tray cancels. The regression matrix replays finger traces (distance in dp from touch-down, sampled
 * as Launcher3's moves arrive) through the real stage machine and release rules: short drags, threshold overshoot,
 * reversal, slow long swipes, fast flicks, the tray already open, cancels and jitter on each threshold.
 */
public final class RoseEdgeGestureTest {
    static int checks;

    static void expect(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    static boolean near(float a, float b) { return Math.abs(a - b) < 1e-3f; }

    static final int NONE = RoseEdgeGesture.NONE, TRAY = RoseEdgeGesture.TRAY, SWITCHER = RoseEdgeGesture.SWITCHER;
    static final int STOCK = RoseEdgeGesture.STOCK, RAISE = RoseEdgeGesture.RAISE, HOME = RoseEdgeGesture.HOME,
            STAY = RoseEdgeGesture.STAY, CANCEL = RoseEdgeGesture.CANCEL, OPEN = RoseEdgeGesture.SWITCHER_OPEN;

    /** A trace: the stage after each sample, then the release at the given lift-off speed (dp/ms, up positive). */
    static final class Run {
        final int[] stages;
        final int stage;
        final int result;
        int entriesTray, entriesSwitcher;

        Run(boolean raised, float liftOff, float... ups) {
            stages = new int[ups.length];
            int s = raised ? TRAY : NONE;
            for (int i = 0; i < ups.length; i++) {
                int next = RoseEdgeGesture.stage(s, ups[i], raised);
                if (next == TRAY && s == NONE) entriesTray++;
                if (next == SWITCHER && s != SWITCHER) entriesSwitcher++;
                s = next;
                stages[i] = s;
            }
            stage = s;
            result = RoseEdgeGesture.release(s, raised, false, liftOff);
        }
    }

    static Run over(float liftOff, float... ups) { return new Run(false, liftOff, ups); }

    static Run raised(float liftOff, float... ups) { return new Run(true, liftOff, ups); }

    /** Evenly spaced samples from a to b (inclusive). */
    static float[] ramp(float a, float b, int n) {
        float[] out = new float[n];
        for (int i = 0; i < n; i++) out[i] = a + (b - a) * i / (n - 1);
        return out;
    }

    static float[] concat(float[]... parts) {
        int n = 0;
        for (float[] p : parts) n += p.length;
        float[] out = new float[n];
        int i = 0;
        for (float[] p : parts) for (float v : p) out[i++] = v;
        return out;
    }

    public static void main(String[] args) {
        // Thresholds are generously separated, each with hysteresis below its entry.
        expect(RoseEdgeGesture.TRAY_EXIT_DP < RoseEdgeGesture.TRAY_DP
                && RoseEdgeGesture.TRAY_DP < RoseEdgeGesture.SWITCHER_EXIT_DP
                && RoseEdgeGesture.SWITCHER_EXIT_DP < RoseEdgeGesture.SWITCHER_DP, "exit < entry, tray far below switcher");
        expect(RoseEdgeGesture.SWITCHER_DP - RoseEdgeGesture.TRAY_DP >= 150, "at least 150dp between tray and switcher");
        expect(RoseEdgeGesture.SWITCHER_DP - RoseEdgeGesture.SWITCHER_EXIT_DP >= 40
                && RoseEdgeGesture.TRAY_DP - RoseEdgeGesture.TRAY_EXIT_DP >= 12, "hysteresis bands are generous");
        expect(RoseEdgeGesture.FLICK_DP_PER_MS > 0.5f, "a Home flick is quicker than Launcher3's 0.5dp/ms fling floor");

        // 1. Short drag that stops below the tray: the tray was partly revealed, release cancels.
        Run r = over(0.05f, ramp(0, 40, 9));
        expect(r.stage == NONE && r.result == CANCEL, "short drag below the tray cancels");
        expect(near(RoseEdgeGesture.reveal(24, false), 0.5f) && RoseEdgeGesture.reveal(0, false) == 0,
                "the first movement already starts revealing the tray");
        expect(RoseEdgeGesture.reveal(48, false) == 1 && RoseEdgeGesture.reveal(150, false) == 1, "fully shown at TRAY_DP");

        // 2. Slow deliberate drag to the tray and release: it stays open and raises once.
        r = over(0.02f, ramp(0, 70, 30));
        expect(r.stage == TRAY && r.result == RAISE && r.entriesTray == 1, "slow drag to the tray raises it, entered once");
        // The same with the finger stopped (zero speed) and with a gentle upward drift at lift-off.
        expect(over(0, ramp(0, 90, 20)).result == RAISE, "stopped at the tray raises");
        expect(over(0.9f, ramp(0, 90, 20)).result == RAISE, "a gentle upward release at the tray still raises");

        // 3. Quick flick before the switcher goes Home, from below the tray or from the tray.
        expect(over(2.5f, ramp(0, 30, 3)).result == HOME, "a quick short flick goes Home");
        expect(over(1.8f, ramp(0, 160, 5)).result == HOME, "a quick flick through the tray goes Home");

        // 4. Overshoot into the switcher: it activates visibly and wins over release speed, even a fast flick.
        r = over(3f, ramp(0, 260, 8));
        expect(r.stage == SWITCHER && r.result == OPEN && r.entriesSwitcher == 1, "switcher wins over a flick");
        expect(over(0, ramp(0, 300, 40)).result == OPEN, "slow long swipe opens the switcher");
        expect(over(-2f, ramp(0, 240, 10)).result == OPEN, "even released moving down while still in the stage");

        // 5. Reversal: back down below the switcher's exit returns to the tray; below the tray's exit to nothing.
        r = over(0, concat(ramp(0, 250, 10), ramp(250, 120, 6)));
        expect(r.stage == TRAY && r.result == RAISE, "back from the switcher to the tray, release raises");
        r = over(0, concat(ramp(0, 120, 6), ramp(120, 20, 6)));
        expect(r.stage == NONE && r.result == CANCEL, "back below the tray cancels");
        r = over(0, concat(ramp(0, 250, 10), ramp(250, 10, 12)));
        expect(r.stage == NONE && r.result == CANCEL, "all the way back down cancels");

        // 6. Hysteresis: jitter around each threshold does not flicker.
        r = over(0, 50, 47, 49, 46, 48, 45, 47);
        int changes = 0;
        for (int i = 1; i < r.stages.length; i++) if (r.stages[i] != r.stages[i - 1]) changes++;
        expect(r.stages[0] == TRAY && changes == 0, "jitter around TRAY_DP stays in the tray");
        r = over(0, 230, 215, 225, 205, 222, 200);
        changes = 0;
        for (int i = 1; i < r.stages.length; i++) if (r.stages[i] != r.stages[i - 1]) changes++;
        expect(r.stages[0] == SWITCHER && changes == 0 && r.result == OPEN, "jitter around SWITCHER_DP stays in the switcher");
        expect(RoseEdgeGesture.stage(TRAY, 33, false) == TRAY && RoseEdgeGesture.stage(TRAY, 31, false) == NONE,
                "the tray is left only below TRAY_EXIT_DP");
        expect(RoseEdgeGesture.stage(SWITCHER, 171, false) == SWITCHER
                && RoseEdgeGesture.stage(SWITCHER, 169, false) == TRAY, "the switcher is left only below its exit");
        expect(RoseEdgeGesture.stage(NONE, 219, false) == TRAY && RoseEdgeGesture.stage(NONE, 220, false) == SWITCHER,
                "entry at SWITCHER_DP exactly");
        expect(RoseEdgeGesture.stage(NONE, 400, false) == SWITCHER, "one big move lands in the switcher directly");

        // 7. Released moving clearly down from the tray: cancels instead of raising.
        expect(over(-0.8f, ramp(0, 80, 10)).result == CANCEL, "a downward release at the tray cancels");
        expect(over(-0.4f, ramp(0, 80, 10)).result == RAISE, "a slight downward drift at lift-off still raises");

        // 8. Tray already open (swipe from the bottom system area over the raised edge): the stage never drops below
        //    TRAY, release keeps it, the switcher and a flick still win.
        r = raised(0, ramp(0, 40, 5));
        expect(r.stage == TRAY && r.result == STAY, "raised: a short swipe keeps the tray");
        expect(raised(-2f, ramp(0, 10, 3)).result == STAY, "raised: a downward release keeps it (the window lowers it)");
        expect(raised(2f, ramp(0, 100, 4)).result == HOME, "raised: a quick flick goes Home");
        expect(raised(0, ramp(0, 250, 10)).result == OPEN, "raised: far enough opens the switcher");
        expect(raised(0, concat(ramp(0, 250, 10), ramp(250, 0, 10))).result == STAY, "raised: back down keeps the tray");
        expect(RoseEdgeGesture.reveal(0, true) == 1, "raised: the tray is fully shown throughout");

        // 9. Launcher3's cancel always stands, at any stage.
        for (int stage : new int[] {NONE, TRAY, SWITCHER})
            for (boolean up : new boolean[] {false, true})
                expect(RoseEdgeGesture.release(stage, up, true, 3f) == STOCK, "a cancel is Launcher3's (" + stage + ")");
        expect(RoseEdgeGesture.release(TRAY, false, false, Float.NaN) == RAISE, "no speed: treated as at rest");
        expect(RoseEdgeGesture.stage(TRAY, Float.NaN, false) == TRAY, "no distance: stage unchanged");

        // Preview geometry: the app's bottom follows the tray at full width; toward the switcher it shrinks and lifts.
        float d = 3;
        int w = 1152, h = 2496;
        float[] rect = RoseEdgeGesture.previewRect(w, h, d, 24, false, false);
        expect(near(rect[0], 0) && near(rect[2], w) && near(rect[1], 0), "following the tray: full width, top in place");
        expect(near(rect[3], h - 29 * d), "half revealed: the bottom lifted half the edge (29dp)");
        rect = RoseEdgeGesture.previewRect(w, h, d, 48, false, false);
        expect(near(rect[3], h - 58 * d) && near(rect[0], 0), "tray stage: bottom 58dp up, still full width");
        rect = RoseEdgeGesture.previewRect(w, h, d, 220, false, false);
        float bottom = h - 58 * d, cy = bottom / 2;
        expect(rect[0] > 0 && rect[2] < w && near(rect[1], cy - cy * 0.9f - 24 * d)
                && near(rect[3], cy + (bottom - cy) * 0.9f - 24 * d), "at the switcher threshold: shrunk 10% and lifted 24dp");
        expect(near(RoseEdgeGesture.scale(1), 0.9f) && near(RoseEdgeGesture.liftDp(1), 24), "approach: 10% and 24dp");
        rect = RoseEdgeGesture.previewRect(w, h, d, 120, false, true);
        expect(near(rect[3], h) && near(rect[0], 0), "reduced motion: untouched");
        rect = RoseEdgeGesture.previewRect(w, h, d, 0, true, false);
        expect(near(rect[3], h - 58 * d), "raised: the app stays at its raised rect");
        expect(RoseEdgeGesture.raisedInsetPx(3) == 174 && RoseEdgeGesture.raisedInsetPx(2.625f) == 152,
                "the raised inset is the edge less its concave top, 58dp");
        expect(near(RoseEdgeGesture.cornerDp(1), 34) && near(RoseEdgeGesture.cornerDp(0.5f), 17), "corners follow");

        // Content-free diagnostics names.
        expect("TRAY".equals(RoseEdgeGesture.stageName(TRAY)) && "SWITCHER".equals(RoseEdgeGesture.releaseName(OPEN))
                && "RAISE".equals(RoseEdgeGesture.releaseName(RAISE)), "diagnostic names");

        System.out.println("PASS " + checks + " checks");
    }
}
