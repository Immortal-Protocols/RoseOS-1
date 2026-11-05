/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.wm.shell;

/** Pure gesture/state mapping shared by native status-bar input and its regression tests. */
final class AgentStatusControls {
    private int target;
    private float downX, downY;
    private long downAt;
    void cancel() { target = 0; }

    /**
     * A v1 STATE describes a session from Connecting until it ends. Connecting has connected=false and
     * listening=false (AgentTrayService), so the status string is what marks it. The tray draws, animates
     * and offers End for exactly this span; an idle Ready or Disconnected state draws nothing.
     */
    static boolean active(boolean connected, boolean listening, String status) {
        return connected || listening || "Connecting".equals(status);
    }

    /** F08 (agreed with Home): the SystemUI tray's REGISTER arg2 when the Rose chin can be raised, else 0. */
    static final int CHIN_AVAILABLE = 1;
    /** Settings.Secure.NAVIGATION_MODE for gesture navigation (NAV_BAR_MODE_GESTURAL), the only mode staging it. */
    static final int NAVIGATION_GESTURAL = 2;

    /**
     * The chin can be raised over a standard fullscreen app: the edge switch is on, the owner (user 0) is in front,
     * and gesture navigation is on. Locked, rotation and the keyboard are per-gesture and are judged when the edge is
     * raised.
     */
    static int chinArg(boolean edgeEnabled, int user, int navigationMode) {
        return edgeEnabled && user == 0 && navigationMode == NAVIGATION_GESTURAL ? CHIN_AVAILABLE : 0;
    }

    int touch(int action, int pointers, float x, float y, long at, int hit,
            boolean active, boolean muted, int slop) {
        if (pointers != 1 || action == 3) { cancel(); return 0; }
        if (action == 0) {
            target = hit; downX = x; downY = y; downAt = at;
            return 0;
        }
        if (Math.abs(x - downX) > slop || Math.abs(y - downY) > slop) cancel();
        if (action != 1) return 0;
        int tapped = target;
        cancel();
        if (tapped == 0 || hit != tapped || at - downAt > 400) return 0;
        return !active ? 3 : tapped == 1 ? 6 : muted ? 5 : 4;
    }
}
