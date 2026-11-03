package com.android.quickstep.fallback;

/**
 * RoseEdgeGesture against the frozen Oxblood HiFi (prototypes/rose-hifi at f6503a0d): app.js endDrag's release rules
 * and pointermove's --pk/--pk2, and oxblood.css's .screen.peek / .peek-drag geometry (bottom 58px * pk, corners 34px,
 * translateY(-40px * pk2), scale(1 - .26 * pk2)). Values in CSS px = dp. Launcher3's own fling, pause, quick switch and
 * cancel rules are kept, so those always come back STOCK.
 */
public final class RoseEdgeGestureTest {
    static int checks;

    static void expect(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    static boolean near(float a, float b) { return Math.abs(a - b) < 1e-3f; }

    static final int L = RoseEdgeGesture.STOCK_LAST_TASK, H = RoseEdgeGesture.STOCK_HOME,
            R = RoseEdgeGesture.STOCK_RECENTS, N = RoseEdgeGesture.STOCK_NEW_TASK;

    static int slow(int stock, float up) { return RoseEdgeGesture.release(true, false, false, false, false, stock, up); }

    public static void main(String[] args) {
        // A slow, unpaused release over an app: below 36dp nothing changes; 36 up to 240 raises; 240 and beyond is Home.
        expect(slow(L, 0) == RoseEdgeGesture.STOCK && slow(L, 35.9f) == RoseEdgeGesture.STOCK, "too short: Launcher3 decides");
        expect(slow(L, -50) == RoseEdgeGesture.STOCK, "finger ended lower: Launcher3 decides");
        expect(slow(L, 36) == RoseEdgeGesture.RAISE && slow(L, 120) == RoseEdgeGesture.RAISE
                && slow(L, 239.9f) == RoseEdgeGesture.RAISE, "a short swipe raises the edge");
        expect(slow(H, 100) == RoseEdgeGesture.RAISE, "a short slow swipe Launcher3 would send Home raises the edge instead");
        expect(slow(H, 20) == RoseEdgeGesture.STOCK, "but a tiny one keeps Launcher3's own result");
        expect(slow(L, 240) == RoseEdgeGesture.HOME && slow(H, 400) == RoseEdgeGesture.HOME, "a long swipe goes Home");
        expect(slow(L, Float.NaN) == RoseEdgeGesture.STOCK, "no distance: Launcher3 decides");

        // Launcher3's own rules always stand.
        for (float up : new float[] {10, 60, 200, 500}) {
            expect(RoseEdgeGesture.release(true, false, false, true, false, H, up) == RoseEdgeGesture.STOCK, "a fling stays Launcher3's (Home)");
            expect(RoseEdgeGesture.release(true, false, false, false, true, R, up) == RoseEdgeGesture.STOCK, "a pause stays the switcher");
            expect(RoseEdgeGesture.release(true, false, false, false, false, R, up) == RoseEdgeGesture.STOCK, "the switcher target stays");
            expect(RoseEdgeGesture.release(true, false, false, false, false, N, up) == RoseEdgeGesture.STOCK, "a quick switch stays");
            expect(RoseEdgeGesture.release(true, false, true, false, false, L, up) == RoseEdgeGesture.STOCK, "a cancel returns to the app");
            expect(RoseEdgeGesture.release(false, false, false, false, false, L, up) == RoseEdgeGesture.STOCK,
                    "not eligible (Home, switcher, locked, no edge): unchanged");
            expect(RoseEdgeGesture.release(true, true, true, false, false, L, up) == RoseEdgeGesture.STOCK, "raised, cancelled: unchanged");
            expect(RoseEdgeGesture.release(true, true, false, true, false, H, up) == RoseEdgeGesture.STOCK, "raised, fling: Home as always");
        }

        // With the edge up: 120dp goes Home, anything shorter leaves it up (never a second raise).
        expect(RoseEdgeGesture.release(true, true, false, false, false, L, 119.9f) == RoseEdgeGesture.STAY, "raised: short swipe keeps it");
        expect(RoseEdgeGesture.release(true, true, false, false, false, L, 0) == RoseEdgeGesture.STAY, "raised: a tap keeps it");
        expect(RoseEdgeGesture.release(true, true, false, false, false, H, 120) == RoseEdgeGesture.HOME, "raised: 120dp goes Home");

        // Preview geometry: --pk = min(1, up / 120), --pk2 = clamp((up / 120 - 1) / 1.2, 0, 1).
        expect(near(RoseEdgeGesture.follow(60, false), 0.5f) && near(RoseEdgeGesture.follow(120, false), 1)
                && near(RoseEdgeGesture.follow(300, false), 1) && near(RoseEdgeGesture.follow(-5, false), 0), "follow");
        expect(near(RoseEdgeGesture.beyond(120, false), 0) && near(RoseEdgeGesture.beyond(192, false), 0.5f)
                && near(RoseEdgeGesture.beyond(264, false), 1) && near(RoseEdgeGesture.beyond(999, false), 1), "beyond");
        expect(near(RoseEdgeGesture.follow(90, true), 0) && near(RoseEdgeGesture.beyond(400, true), 0),
                "reduced motion: no live follow (the result applies at once)");
        expect(near(RoseEdgeGesture.bottomLiftDp(0.5f), 29) && near(RoseEdgeGesture.bottomLiftDp(1), 58)
                && near(RoseEdgeGesture.bottomLiftDp(2), 58), "bottom: 58px * pk");
        expect(near(RoseEdgeGesture.bottomCornerDp(0.5f), 17) && near(RoseEdgeGesture.topCornerDp(0), 0)
                && near(RoseEdgeGesture.topCornerDp(1), 34), "corners: 34px * pk below, 34px * pk2 above");
        expect(near(RoseEdgeGesture.liftDp(0.5f), 20) && near(RoseEdgeGesture.scale(0.5f), 0.87f)
                && near(RoseEdgeGesture.scale(1), 0.74f) && near(RoseEdgeGesture.scale(0), 1), "translateY(-40px * pk2) scale(1 - .26 * pk2)");
        expect(near(RoseEdgeGesture.LIFT_BOTTOM_DP, 58), "raised: bottom 58px (92 edge less its 34 concave top)");

        // The first part of the drag is a full-width, unscaled crop; only past the edge does the app shrink.
        float d = 3f;
        int w = 1152, h = 2496;
        float[] r = RoseEdgeGesture.previewRect(w, h, d, 60, false);
        expect(near(r[0], 0) && near(r[2], w) && near(r[1], 0) && near(r[3], h - 29 * d), "half follow: full width, 29dp shorter");
        r = RoseEdgeGesture.previewRect(w, h, d, 120, false);
        expect(near(r[0], 0) && near(r[2], w) && near(r[3], h - 58 * d), "at the edge: full width, 58dp shorter");
        r = RoseEdgeGesture.previewRect(w, h, d, 264, false);
        float bottom = h - 58 * d;
        expect(near(r[2] - r[0], w * 0.74f) && near(r[3] - r[1], bottom * 0.74f)
                && near((r[1] + r[3]) / 2, bottom / 2 - 40 * d), "toward Home: scaled about its centre and lifted 40dp");
        r = RoseEdgeGesture.previewRect(w, h, d, 90, true);
        expect(near(r[0], 0) && near(r[1], 0) && near(r[2], w) && near(r[3], h), "reduced motion: untouched while dragging");
        expect(RoseEdgeGesture.raisedInsetPx(3f) == 174 && RoseEdgeGesture.raisedInsetPx(2.625f) == 152,
                "the raised inset is 58dp in px");

        System.out.println("RoseEdgeGestureTest: " + checks + " checks passed");
    }
}
