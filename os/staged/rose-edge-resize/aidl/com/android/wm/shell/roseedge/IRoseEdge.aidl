/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.wm.shell.roseedge;

import com.android.wm.shell.roseedge.IRoseEdgeListener;

/**
 * RoseOS (#410 E2c), interface record: the raised edge's real app resize. WMShell implements it (OS Core) and exports it
 * with ShellController.addExternalInterface(DESCRIPTOR, ...), so it reaches only the overview proxy (Launcher3) through
 * SystemUiProxy's shell bundle. Launcher3 calls it with hand-written binder code that must match these transaction ids.
 *
 * raise: accepted only when taskId is the top visible fullscreen task on displayId, resizeable and not in size compat,
 * the device unlocked, the display in its natural rotation, and no split, PiP or freeform window on the display. Then,
 * in one TRANSIT_CHANGE shell transition, the default task display area's bounds become (0, 0, W, H - liftPx): the app
 * gets real reduced bounds and a configuration change, width unchanged, never scaled. Replies onRaised or onRefused.
 * lower: clears those bounds (fill the display) and replies onLowered(REQUESTED); a lower when not raised is a no-op.
 * One raise per display, owned by the listener binder that raised it: a raise from another binder while raised is
 * refused BUSY. The shell clears the bounds whenever it (re)organizes a display, so a SystemUI restart cannot leave the
 * area shortened. Agreed with OS Core on #410 (comment 6621890904).
 */
interface IRoseEdge {
    oneway void raise(int displayId, int taskId, int liftPx, IRoseEdgeListener listener) = 1;
    oneway void lower(int displayId) = 2;
}
