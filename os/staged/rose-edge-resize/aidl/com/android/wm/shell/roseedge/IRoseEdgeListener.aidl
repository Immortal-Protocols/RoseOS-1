/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.wm.shell.roseedge;

import android.graphics.Rect;

/**
 * RoseOS (#410 E2c), interface record: WMShell's replies to Launcher3 for IRoseEdge. Launcher3 implements it.
 *
 * Refusal reasons: 1 NOT_TOP_TASK, 2 NOT_RESIZEABLE, 3 LOCKED, 4 ROTATION, 5 MULTI_WINDOW, 6 BUSY, 7 DISABLED.
 * Lowered reasons: 0 REQUESTED, 1 TASK_CHANGED (the top task changed or left fullscreen), 2 KEYGUARD, 3 DISPLAY_CHANGED
 * (size or rotation), 4 IME_SHOWN, 5 LISTENER_DIED, 6 TRANSITION (Home, recents or another task opened).
 * The shell lowers by itself for reasons 1-6 and always reports it.
 */
oneway interface IRoseEdgeListener {
    void onRaised(int displayId, int taskId, in Rect appBounds) = 1;
    void onRefused(int displayId, int taskId, int reason) = 2;
    void onLowered(int displayId, int reason) = 3;
}
