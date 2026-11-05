/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.graphics.Rect;

/**
 * RoseOS (#410 E2c): where the raised edge and its controls sit, from Home's design contract
 * (qa/oxblood/oxblood-contract.json homeEdge, Home's file; OS Product reads it key for key). Pure geometry in window px
 * for a window widthPx wide whose edge is EDGE_HEIGHT_DP tall above navInsetPx of tappable navigation (gesture
 * navigation adds nothing).
 *
 * Controls, left to right in a row centred with GAP_DP between them, centred CONTROL_CENTRE_DP above the edge bottom:
 * Rose (ROSE_DP), the microphone-input slot (MIC_DP, drawn by SystemUI's existing tray control: Chief's option (a)) and
 * the switcher (SWITCHER_DP).
 */
public final class RoseEdgeLayout {
    private RoseEdgeLayout() {}

    /** homeEdge: height 92, flatTop 34, concaveFilletRadius 34. */
    public static final float EDGE_HEIGHT_DP = 92, FLAT_TOP_DP = 34, FILLET_DP = 34;
    /** homeEdge.controls: layout gap 14, centre 29dp above the edge bottom; hit sizes. */
    public static final float GAP_DP = 14, CONTROL_CENTRE_DP = 29, CONTROL_HEIGHT_DP = 44;
    public static final float ROSE_DP = 103, MIC_DP = 44, SWITCHER_DP = 106;
    /** homeEdge.well: plateHeight 36, radius 8. */
    public static final float WELL_HEIGHT_DP = 36, WELL_RADIUS_DP = 8;

    public static final int ROSE = 0, MIC = 1, SWITCHER = 2;

    /** The edge window's height in px: the edge plus the tappable navigation inset below it. */
    public static int windowHeightPx(float density, int navInsetPx) {
        return Math.round(EDGE_HEIGHT_DP * density) + Math.max(0, navInsetPx);
    }

    /** The control's hit rect in window px (ROSE, MIC or SWITCHER). */
    public static Rect control(int which, int widthPx, float density, int navInsetPx) {
        float[] widths = {ROSE_DP, MIC_DP, SWITCHER_DP};
        float row = (ROSE_DP + MIC_DP + SWITCHER_DP + 2 * GAP_DP) * density;
        float left = (widthPx - row) / 2f;
        for (int i = 0; i < which; i++) left += (widths[i] + GAP_DP) * density;
        float edgeBottom = EDGE_HEIGHT_DP * density;
        float centreY = edgeBottom - CONTROL_CENTRE_DP * density;
        float half = CONTROL_HEIGHT_DP * density / 2f;
        return new Rect(Math.round(left), Math.round(centreY - half), Math.round(left + widths[which] * density),
                Math.round(centreY + half));
    }

    /** The microphone-input slot in screen px, for a window at windowTopPx on the display. */
    public static Rect micSlotOnScreen(int widthPx, float density, int navInsetPx, int windowTopPx) {
        Rect slot = control(MIC, widthPx, density, navInsetPx);
        slot.offset(0, windowTopPx);
        return slot;
    }
}
