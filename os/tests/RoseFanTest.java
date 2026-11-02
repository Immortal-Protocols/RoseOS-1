package com.android.quickstep.fallback;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * RoseFan against the frozen Oxblood HiFi (prototypes/rose-hifi at f6503a0d): the Rose carousel's per-card transform
 * as Chromium computed it for the pinned page (#410 capture, measures.*.agent: styleTransform and z-index per card),
 * its side padding (12px 130px folded, 12px 312px unfolded) and its placement rules. Values in CSS px = dp.
 */
public final class RoseFanTest {
    static int checks;

    static void expect(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    static boolean near(float a, float b) { return Math.abs(a - b) < 1e-4f; }

    /** One card as the capture measured it: offset from centre, translateX, rotateY, scale, z-index. */
    static void card(float off, float tx, float ry, float scale, int z) {
        float k = RoseFan.offset(off * 124 + 192, 192, 124);
        expect(near(RoseFan.shiftDp(k), tx) && near(RoseFan.turnDeg(k), ry) && near(RoseFan.scale(k), scale)
                && RoseFan.depth(k) == z, "offset " + off + ": translateX(" + tx + "px) rotateY(" + ry + "deg) scale("
                + scale + ") z " + z + "; got " + RoseFan.shiftDp(k) + ", " + RoseFan.turnDeg(k) + ", "
                + RoseFan.scale(k) + ", " + RoseFan.depth(k));
    }

    public static void main(String[] args) {
        // measures.initial/bottomPlus1/unfolded/reduced .agent cards (offFromCentre, styleTransform, zIndex).
        card(0, 0, 0, 1, 10);
        card(-1, 40, 40, 0.8f, 6);
        card(1, -40, -40, 0.8f, 6);
        card(-2, 56, 56, 0.8f, 4);
        card(2, -56, -56, 0.8f, 4);
        card(-3, 56, 56, 0.8f, 4);
        expect(near(RoseFan.offset(0, 0, 0), 0), "no width: no offset");
        expect(near(RoseFan.scale(0.5f), 0.9f) && RoseFan.depth(0.5f) == 8, "between cards the fan is continuous");
        expect(RoseFan.PERSPECTIVE_DP == 760 && RoseFan.CARD_WIDTH_DP == 124 && RoseFan.CARD_HEIGHT_DP == 146
                && RoseFan.CARD_RADIUS_DP == 12, "card 124x146, radius 12, perspective 760 (oxblood.css:450-451)");

        // CSS perspective(760px) as a camera: 760 px at 72 units per inch, View stores distance / dpi inches.
        expect(near(RoseFan.cameraDistance(760, 160), 760 * 160 / 72f) && near(RoseFan.cameraDistance(2280, 480),
                2280 * 480 / 72f), "camera distance for a 760 px perspective");

        // Side padding (12px 130px folded at 384 wide, 12px 312px unfolded at 748 wide) lets both ends centre.
        expect(RoseFan.sidePadding(384, 124) == 130, "folded side padding 130");
        expect(RoseFan.sidePadding(748, 124) == 312, "unfolded side padding 312");
        expect(RoseFan.sidePadding(100, 124) == 0, "never negative");
        expect(RoseFan.scrollFor(2, 124) == 248 && RoseFan.scrollFor(-1, 124) == 0, "scroll that centres a card");

        // Entry: the second card when there are three or more (the document), else the first.
        expect(RoseFan.initial(0) == 0 && RoseFan.initial(1) == 0 && RoseFan.initial(2) == 0, "fewer than three: first");
        expect(RoseFan.initial(3) == 1 && RoseFan.initial(8) == 1, "three or more: second");

        // Keys and release: one card at a time, clamped, no wrap; settling picks the nearest card.
        expect(RoseFan.step(0, -1, 3) == 0 && RoseFan.step(2, 1, 3) == 2 && RoseFan.step(1, 1, 3) == 2,
                "one card, clamped at both ends");
        expect(RoseFan.step(5, 0, 3) == 2 && RoseFan.step(0, 1, 0) == 0, "an index past the end, or no cards, clamps");
        expect(RoseFan.nearest(248, 124, 8) == 2 && RoseFan.nearest(185, 124, 8) == 1 && RoseFan.nearest(186, 124, 8) == 2,
                "a release settles on the nearest card");
        expect(RoseFan.nearest(-40, 124, 8) == 0 && RoseFan.nearest(5000, 124, 8) == 7 && RoseFan.nearest(10, 0, 3) == 0,
                "past either end, or no pitch: clamped");
        // A drag interrupted by a cancel (or repeated before it settles) still ends exactly on a card.
        for (int x = -200; x <= 1200; x += 7) {
            int i = RoseFan.nearest(x, 124, 8);
            expect(RoseFan.scrollFor(i, 124) % 124 == 0 && i >= 0 && i < 8, "every position settles on a card");
        }

        // Cards that change while shown: the centred card keeps its place by identity, else the nearest index.
        List<String> before = Arrays.asList("70/0/maps", "70/0/mail", "80/0/notes");
        expect(RoseFan.keep(before, 1, Arrays.asList("70/0/mail", "80/0/notes")) == 0, "the same card keeps the centre");
        expect(RoseFan.keep(before, 2, Arrays.asList("70/0/maps", "70/0/mail")) == 1, "a removed card: the nearest");
        expect(RoseFan.keep(before, 0, Collections.emptyList()) == 0 && RoseFan.keep(null, 3, before) == 2,
                "no cards, or nothing known before: clamped");
        expect(RoseFan.keep(before, -1, before) == 0, "nothing centred: the first");
        System.out.println("RoseFanTest: " + checks + " checks passed");
    }
}
