/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package dev.pixel.voice.edge;

import dev.pixel.voice.edge.IRoseEdgeHost;

/**
 * RoseOS (#410 E2c), interface record (proposal for Home's agreement): Home's Rose controls on the raised edge. Home
 * (dev.pixel.voice) implements it in a service for action dev.pixel.voice.action.ROSE_EDGE_CONTROLS and accepts only
 * the config_recentsComponentName caller with a shared uid (#442's GestureContract.caller rule). Launcher3 binds it only
 * on the HOME role holder.
 *
 * attach: Home renders its controls (Rose, microphone input mute, overview) with SurfaceControlViewHost on hostToken
 * (the SurfaceView host token of Launcher3's edge window) at widthPx x heightPx and replies host.onPanel. Taps inside
 * the panel go to Home's own views. No microphone API crosses this interface: the mute is Home's one shared state and
 * mutes only microphone input into Rose Agent (never sensor privacy, output, the call or background work).
 * detach: Home releases that display's panel.
 */
oneway interface IRoseEdgeControls {
    void attach(int displayId, IBinder hostToken, int widthPx, int heightPx, IRoseEdgeHost host) = 1;
    void detach(int displayId) = 2;
}
