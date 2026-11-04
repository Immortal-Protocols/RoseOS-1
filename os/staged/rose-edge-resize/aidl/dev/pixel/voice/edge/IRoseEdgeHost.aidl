/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package dev.pixel.voice.edge;

import android.view.SurfaceControlViewHost.SurfacePackage;

/**
 * RoseOS (#410 E2c), interface record (proposal for Home's agreement): Launcher3's edge window, called by Home's panel.
 * lower: the panel asks to lower the edge. goHome: Launcher3 lowers it, then runs its own Home transition to
 * destination 0 (Home) or 1 (Rose). showSwitcher: Launcher3 lowers it and shows the switcher.
 */
oneway interface IRoseEdgeHost {
    void onPanel(int displayId, in SurfacePackage panel) = 1;
    void lower(int displayId) = 2;
    void goHome(int displayId, int destination) = 3;
    void showSwitcher(int displayId) = 4;
}
