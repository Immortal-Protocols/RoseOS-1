/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.os.Looper;
import android.util.SparseBooleanArray;

import java.util.ArrayList;
import java.util.List;

/**
 * RoseOS (#410): whether the raised edge is up on each display, inside Launcher3's process. The swipe handler
 * (RoseEdgeSwipe) raises and lowers it; the edge window (E2b) listens and draws it, and lowers it itself on a swipe
 * down, Escape, the IME, the keyguard or the app leaving. Main thread only: the swipe handler runs there.
 */
public final class RoseEdgeState {
    private RoseEdgeState() {}

    /** Told on the main thread whenever a display's edge goes up or down. */
    public interface Listener {
        void onRaisedChanged(int displayId, boolean raised);
    }

    private static final SparseBooleanArray RAISED = new SparseBooleanArray();
    private static final List<Listener> LISTENERS = new ArrayList<>();

    public static boolean isRaised(int displayId) {
        return RAISED.get(displayId, false);
    }

    public static void raise(int displayId) { set(displayId, true); }

    public static void lower(int displayId) { set(displayId, false); }

    public static void addListener(Listener listener) {
        mainThread();
        if (!LISTENERS.contains(listener)) LISTENERS.add(listener);
    }

    public static void removeListener(Listener listener) {
        mainThread();
        LISTENERS.remove(listener);
    }

    private static void set(int displayId, boolean raised) {
        mainThread();
        if (RAISED.get(displayId, false) == raised) return;
        if (raised) RAISED.put(displayId, true);
        else RAISED.delete(displayId);
        for (Listener listener : new ArrayList<>(LISTENERS)) listener.onRaisedChanged(displayId, raised);
    }

    private static void mainThread() {
        if (Looper.myLooper() != Looper.getMainLooper()) throw new IllegalStateException("RoseEdgeState: main thread only");
    }
}
