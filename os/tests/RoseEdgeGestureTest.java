package com.android.quickstep.fallback;

/**
 * RoseEdgeGesture against values copied from the frozen Oxblood HiFi (prototypes/rose-hifi at f6503a0d): app.js
 * endDrag's release rules (strict: dy < -36 raises; dy < -240, or dy < -120 with v < -0.9 px/ms, goes Home; raised,
 * dy < -120 goes Home) and pointermove's --pk/--pk2, and oxblood.css's .screen.peek / .peek-drag geometry (bottom
 * 58px * pk, corners 34px, translateY(-40px * pk2), scale(1 - .26 * pk2)). Values in CSS px = dp. Launcher3's own
 * pause, quick switch, cancel and back-to-app results are kept, so those always come back STOCK.
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
    static final int STOCK = RoseEdgeGesture.STOCK, RAISE = RoseEdgeGesture.RAISE, HOME = RoseEdgeGesture.HOME,
            STAY = RoseEdgeGesture.STAY;

    /** A release Launcher3 would send Home, over an app, edge down. */
    static int home(float up, float speed) { return RoseEdgeGesture.release(true, false, false, false, H, up, speed); }

    static int raised(int stock, float up, float speed) {
        return RoseEdgeGesture.release(true, true, false, false, stock, up, speed);
    }

    public static void main(String[] args) {
        // Short swipes raise at any speed, including a quick flick Launcher3 calls a fling.
        for (float speed : new float[] {0, 0.3f, 0.9f, 2.5f, 8}) {
            expect(home(36.1f, speed) == RAISE && home(80, speed) == RAISE && home(120, speed) == RAISE,
                    "36-120dp raises at " + speed + "dp/ms");
            expect(home(36, speed) == STOCK && home(20, speed) == STOCK,
                    "36dp or less (strict, as the reference): Launcher3's own result stands");
            expect(home(240.1f, speed) == HOME && home(500, speed) == HOME, "more than 240dp goes Home");
        }
        // 120-240dp: Home only when the gesture averaged more than 0.9dp/ms.
        expect(home(121, 0.91f) == HOME && home(239, 3) == HOME, "a fast medium swipe goes Home");
        expect(home(121, 0.9f) == RAISE && home(200, 0.5f) == RAISE && home(240, 0.2f) == RAISE,
                "a slower medium swipe (and exactly 240dp) raises");
        expect(home(Float.NaN, 1) == STOCK, "no distance: Launcher3 decides");

        // Launcher3's own rules always stand. Each guard is tested on a release Launcher3 would send Home, so the
        // guard alone decides.
        for (float up : new float[] {10, 60, 200, 500}) {
            expect(RoseEdgeGesture.release(false, false, false, false, H, up, 1) == STOCK,
                    "not eligible (Home, locked, three-button, edge off): unchanged");
            expect(RoseEdgeGesture.release(true, false, true, false, H, up, 1) == STOCK, "a cancel returns to the app");
            expect(RoseEdgeGesture.release(true, false, false, true, H, up, 1) == STOCK, "a pause stays Launcher3's");
            expect(RoseEdgeGesture.release(true, true, true, false, H, up, 1) == STOCK, "raised, cancelled: unchanged");
            expect(RoseEdgeGesture.release(false, true, false, false, H, up, 1) == STOCK, "raised, not eligible: unchanged");
            for (int other : new int[] {L, R, N})
                for (boolean up2 : new boolean[] {false, true})
                    expect(RoseEdgeGesture.release(true, up2, false, false, other, up, 1) == STOCK,
                            "back to the app, the switcher or a quick switch stand (" + other + ")");
        }

        // With the edge up: more than 120dp goes Home at any speed; anything shorter keeps it (never a second raise).
        for (float speed : new float[] {0, 1, 6}) {
            expect(raised(H, 120.1f, speed) == HOME && raised(H, 400, speed) == HOME, "raised: more than 120dp goes Home");
            expect(raised(H, 120, speed) == STAY && raised(H, 37, speed) == STAY && raised(H, 0, speed) == STAY,
                    "raised: 120dp or less keeps it");
        }
        expect(raised(H, Float.NaN, 1) == STOCK, "raised, no distance: Launcher3 decides (not STAY)");

        // Preview: --pk = min(1, up / 120), --pk2 = clamp((up / 120 - 1) / 1.2, 0, 1).
        expect(near(RoseEdgeGesture.follow(60, false, false), 0.5f) && near(RoseEdgeGesture.follow(120, false, false), 1)
                && near(RoseEdgeGesture.follow(300, false, false), 1) && near(RoseEdgeGesture.follow(-5, false, false), 0)
                && near(RoseEdgeGesture.follow(Float.NaN, false, false), 0), "follow");
        expect(near(RoseEdgeGesture.beyond(120, false, false), 0) && near(RoseEdgeGesture.beyond(192, false, false), 0.5f)
                && near(RoseEdgeGesture.beyond(264, false, false), 1) && near(RoseEdgeGesture.beyond(999, false, false), 1),
                "beyond");
        expect(near(RoseEdgeGesture.follow(90, false, true), 0) && near(RoseEdgeGesture.beyond(400, false, true), 0),
                "reduced motion: no live follow (the result applies at once)");
        expect(near(RoseEdgeGesture.follow(90, true, false), 0) && near(RoseEdgeGesture.beyond(400, true, false), 0),
                "edge already up: no live follow");
        expect(near(RoseEdgeGesture.bottomLiftDp(0.5f), 29) && near(RoseEdgeGesture.bottomLiftDp(1), 58)
                && near(RoseEdgeGesture.bottomLiftDp(2), 58), "bottom: 58px * pk");
        expect(near(RoseEdgeGesture.bottomCornerDp(0.5f), 17) && near(RoseEdgeGesture.topCornerDp(0), 0)
                && near(RoseEdgeGesture.topCornerDp(1), 34), "corners: 34px * pk below, 34px * pk2 above");
        expect(near(RoseEdgeGesture.liftDp(0.5f), 20) && near(RoseEdgeGesture.scale(0.5f), 0.87f)
                && near(RoseEdgeGesture.scale(1), 0.74f) && near(RoseEdgeGesture.scale(0), 1),
                "translateY(-40px * pk2) scale(1 - .26 * pk2)");
        expect(near(RoseEdgeGesture.LIFT_BOTTOM_DP, 58), "raised: bottom 58px (92 edge less its 34 concave top)");

        // The first part of the drag is a full-width, unscaled crop; only past the edge does the app shrink.
        float d = 3f;
        int w = 1152, h = 2496;
        float[] r = RoseEdgeGesture.previewRect(w, h, d, 60, false, false);
        expect(near(r[0], 0) && near(r[2], w) && near(r[1], 0) && near(r[3], h - 29 * d), "half follow: full width, 29dp shorter");
        r = RoseEdgeGesture.previewRect(w, h, d, 120, false, false);
        expect(near(r[0], 0) && near(r[2], w) && near(r[3], h - 58 * d), "at the edge: full width, 58dp shorter");
        r = RoseEdgeGesture.previewRect(w, h, d, 264, false, false);
        float bottom = h - 58 * d;
        expect(near(r[2] - r[0], w * 0.74f) && near(r[3] - r[1], bottom * 0.74f)
                && near((r[1] + r[3]) / 2, bottom / 2 - 40 * d), "toward Home: scaled about its centre and lifted 40dp");
        r = RoseEdgeGesture.previewRect(w, h, d, 90, false, true);
        expect(near(r[0], 0) && near(r[1], 0) && near(r[2], w) && near(r[3], h), "reduced motion: untouched while dragging");
        for (float up : new float[] {0, 90, 400}) {
            r = RoseEdgeGesture.previewRect(w, h, d, up, true, false);
            expect(near(r[0], 0) && near(r[1], 0) && near(r[2], w) && near(r[3], bottom),
                    "raised: stays at its raised rect, never jumping back to full height (" + up + "dp)");
        }
        expect(RoseEdgeGesture.raisedInsetPx(3f) == 174 && RoseEdgeGesture.raisedInsetPx(2.625f) == 152,
                "the raised inset is 58dp in px");

        System.out.println("RoseEdgeGestureTest: " + checks + " checks passed");
    }
}
