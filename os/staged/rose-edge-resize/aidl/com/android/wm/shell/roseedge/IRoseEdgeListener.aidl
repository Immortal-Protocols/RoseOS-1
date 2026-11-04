/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.wm.shell.roseedge;

import android.graphics.Rect;

/**
 * RoseOS (#410 E2c), interface record: WMShell's replies to Launcher3 for IRoseEdge. Launcher3 implements it.
 *
 * Values as agreed with OS Core on #410 (comment 6621890904).
 * Refusal reasons: 1 NOT_TOP_TASK, 2 NOT_RESIZEABLE, 3 LOCKED, 4 ROTATION, 5 MULTI_WINDOW, 6 BUSY (another listener
 * binder holds this display's raise), 7 LIFT_RANGE (0 < liftPx <= min(H / 4, 160dp) fails), 8 DISABLED
 * (persist.wm.debug.rose_edge off), 9 NOT_DEFAULT_DISPLAY.
 * Lowered reasons: 0 REQUESTED (the caller's lower), 1 TASK_CHANGED (the top task changed or left fullscreen),
 * 2 HOME_OR_RECENTS, 3 KEYGUARD (or screen off), 4 DISPLAY_CHANGED (display, rotation or configuration), 5 IME_SHOWN,
 * 6 LISTENER_DIED, 7 USER_SWITCH. The shell lowers by itself for reasons 1-7 and always reports it.
 * One raise per display, owned by the listener binder that raised it.
 */
oneway interface IRoseEdgeListener {
    void onRaised(int displayId, int taskId, in Rect appBounds) = 1;
    void onRefused(int displayId, int taskId, int reason) = 2;
    void onLowered(int displayId, int reason) = 3;
}
