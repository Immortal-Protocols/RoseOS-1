/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import java.util.List;

/**
 * RoseOS (#410): the geometry and placement rules of the switcher's Rose carousel, a centred fan with depth, as the
 * selected Oxblood HiFi draws it (prototypes/rose-hifi at f6503a0d, views.js:240-246 and oxblood.css:450-451; measured
 * transforms in the #410 capture). Pure logic, no Android types: RoseAgentFan applies it to views.
 *
 * Each card's offset k from the centre is (card centre - viewport centre) / card width, clamped to +-MAX_OFFSET. The
 * card is then moved toward the centre by k * SHIFT_DP, turned by k * TURN_DEG about its vertical axis (right-hand
 * neighbours turn their outer edge toward the viewer), shrunk by min(1, |k|) * SHRINK, and drawn above cards that are
 * further out (depth 10 - round(4|k|)). The centre card is untouched. Values are Launcher3's own copy of the Oxblood
 * contract (qa/oxblood/oxblood-contract.json, written by Home); the test compares them with it once it is published.
 */
final class RoseFan {
    private RoseFan() {}

    /** Card size and corner radius of a Rose card (dp). */
    static final int CARD_WIDTH_DP = 124, CARD_HEIGHT_DP = 146, CARD_RADIUS_DP = 12;
    /** CSS perspective(760px): the camera distance of the turn (dp). */
    static final float PERSPECTIVE_DP = 760;
    static final float MAX_OFFSET = 1.4f, SHIFT_DP = 40, TURN_DEG = 40, SHRINK = 0.2f;

    /**
     * View.setCameraDistance's argument for a CSS perspective of perspectivePx pixels. View stores distance / densityDpi
     * as inches and the renderer's camera (Sk3DView.setCameraLocation) places it at 72 units per inch, so the camera
     * sits distance * 72 / dpi pixels away. (Inference from the platform source; confirm on a device recording.)
     */
    static float cameraDistance(float perspectivePx, float densityDpi) { return perspectivePx * densityDpi / 72f; }

    /** The card's offset from the centre in card widths, clamped. */
    static float offset(float cardCentre, float viewportCentre, float cardWidth) {
        if (cardWidth <= 0) return 0;
        float k = (cardCentre - viewportCentre) / cardWidth;
        return Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET, k));
    }

    /** translateX(-k * 40): toward the centre (dp). */
    static float shiftDp(float k) { return -k * SHIFT_DP; }

    /** rotateY(-k * 40deg). */
    static float turnDeg(float k) { return -k * TURN_DEG; }

    /** scale(1 - min(1, |k|) * 0.2). */
    static float scale(float k) { return 1 - Math.min(1f, Math.abs(k)) * SHRINK; }

    /** z-index 10 - round(4|k|): the centre card is on top. */
    static int depth(float k) { return 10 - Math.round(Math.abs(k) * 4); }

    /** The card a carousel shows first on entry: the second when there are three or more, else the first. */
    static int initial(int count) { return count >= 3 ? 1 : 0; }

    /** One card left or right, clamped at the ends; never wraps. */
    static int step(int current, int delta, int count) {
        if (count <= 0) return 0;
        return Math.max(0, Math.min(count - 1, current + delta));
    }

    /**
     * The card whose centre is nearest the viewport's centre: centres are where the cards actually are (scroll content
     * coordinates), so it holds in either layout direction (right-to-left lays the first card out on the right).
     */
    static int nearest(float[] centres, float viewportCentre) {
        int best = 0;
        float bestDistance = Float.MAX_VALUE;
        for (int i = 0; centres != null && i < centres.length; i++) {
            float distance = Math.abs(centres[i] - viewportCentre);
            if (distance < bestDistance) {
                bestDistance = distance;
                best = i;
            }
        }
        return best;
    }

    /** The scroll that puts a card's centre at the viewport's centre; never negative. */
    static int scrollToCentre(float cardCentre, int viewportWidth) {
        return Math.max(0, Math.round(cardCentre - viewportWidth / 2f));
    }

    /**
     * A visual step (+1 = toward the right of the screen) as a step in card order: right-to-left lays later cards out
     * to the left, so a step to the right is one card back.
     */
    static int orderStep(int visualStep, boolean rtl) { return rtl ? -visualStep : visualStep; }

    /** Side padding that lets the first and last cards reach the centre. */
    static int sidePadding(int viewportWidth, int cardWidth) { return Math.max(0, (viewportWidth - cardWidth) / 2); }

    /**
     * Where a carousel settles after its cards changed: the card it centred before, if that card is still there (the
     * same key), else the nearest remaining index. Keys are the cards' identities (display, user, package), never
     * their positions, so a card that moved keeps its place in front of the person.
     */
    static int keep(List<String> before, int centred, List<String> after) {
        if (after == null || after.isEmpty()) return 0;
        if (before != null && centred >= 0 && centred < before.size()) {
            int same = after.indexOf(before.get(centred));
            if (same >= 0) return same;
        }
        return Math.max(0, Math.min(after.size() - 1, centred));
    }
}
