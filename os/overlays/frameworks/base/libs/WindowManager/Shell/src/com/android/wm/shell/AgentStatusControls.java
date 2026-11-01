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
