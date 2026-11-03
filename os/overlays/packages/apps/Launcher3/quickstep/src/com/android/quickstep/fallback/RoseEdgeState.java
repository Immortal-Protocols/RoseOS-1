/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.os.Looper;
import android.util.SparseIntArray;

import java.util.ArrayList;
import java.util.List;

/**
 * RoseOS (#410): whether the raised edge is up on each display, and over which task, inside Launcher3's process. The
 * swipe handler (RoseEdgeSwipe) raises and lowers it; the edge window (E2b) registers a listener, draws it, and lowers
 * it itself on a swipe down, Escape, the IME, the keyguard or the app leaving.
 *
 * The edge can only rise while a listener is registered (hasListener): without E2b's window nothing is drawn, so
 * nothing may change how the next swipe behaves. It also counts as raised only over the task it rose over, so it can
 * never leak into a gesture over another app. Main thread only: the swipe handler and the window run there.
 */
public final class RoseEdgeState {
    private RoseEdgeState() {}

    /** Told on the main thread whenever a display's edge goes up or down. */
    public interface Listener {
        void onRaisedChanged(int displayId, boolean raised);
    }

    /** Display id to the task id the edge rose over; absent when lowered. */
    private static final SparseIntArray RAISED_OVER = new SparseIntArray();
    private static final List<Listener> LISTENERS = new ArrayList<>();

    /** Up on this display over exactly this task. */
    public static boolean isRaised(int displayId, int taskId) {
        int index = RAISED_OVER.indexOfKey(displayId);
        return index >= 0 && RAISED_OVER.valueAt(index) == taskId;
    }

    /** Up on this display over any task. */
    public static boolean isRaised(int displayId) {
        return RAISED_OVER.indexOfKey(displayId) >= 0;
    }

    /** Whether something (the edge window) is there to draw a raised edge. */
    public static boolean hasListener() {
        return !LISTENERS.isEmpty();
    }

    /** Raises the edge over taskId; refused (false) when no listener could draw it. */
    public static boolean raise(int displayId, int taskId) {
        mainThread();
        if (LISTENERS.isEmpty()) return false;
        boolean wasRaised = isRaised(displayId);
        RAISED_OVER.put(displayId, taskId);
        if (!wasRaised) notifyChanged(displayId, true);
        return true;
    }

    public static void lower(int displayId) {
        mainThread();
        int index = RAISED_OVER.indexOfKey(displayId);
        if (index < 0) return;
        RAISED_OVER.removeAt(index);
        notifyChanged(displayId, false);
    }

    public static void addListener(Listener listener) {
        mainThread();
        if (!LISTENERS.contains(listener)) LISTENERS.add(listener);
    }

    public static void removeListener(Listener listener) {
        mainThread();
        LISTENERS.remove(listener);
    }

    private static void notifyChanged(int displayId, boolean raised) {
        for (Listener listener : new ArrayList<>(LISTENERS)) listener.onRaisedChanged(displayId, raised);
    }

    private static void mainThread() {
        if (Looper.myLooper() != Looper.getMainLooper()) throw new IllegalStateException("RoseEdgeState: main thread only");
    }
}
