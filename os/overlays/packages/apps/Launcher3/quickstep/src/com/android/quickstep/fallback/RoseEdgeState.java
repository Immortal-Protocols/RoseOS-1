/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.graphics.Rect;
import android.os.Looper;
import android.os.SystemProperties;
import android.util.Log;
import android.util.SparseArray;
import android.util.SparseIntArray;

import java.util.ArrayList;
import java.util.List;

/**
 * RoseOS (#410 E2c): whether the raised edge is up on each display, over which task, and whether WMShell has really
 * shortened that app (RoseEdgeShell), inside Launcher3's process. The swipe handler (RoseEdgeSwipe) reveals it while
 * the finger is down (peek), raises and lowers it; the edge window registers a listener, draws the tray, shows Home's
 * controls, and lowers it itself on a swipe down or when the panel asks.
 *
 * The edge can only rise while a listener is registered (something draws it) and the shell's IRoseEdge is available
 * (something really resizes the app): otherwise the release ends as stock Launcher3. On a raise the edge shows at once
 * and the shell is asked to shorten the app by liftPx. A refusal (the app is not resizeable, the device is locked, and
 * so on) lowers it again, with nothing resized; the shell lowering by itself (the task changed, the keyguard, the
 * display) lowers it here too. The keyboard showing does not lower it: the chin rises over an open keyboard (the
 * shell no longer sends LOWERED_IME_SHOWN, which keeps its number on the wire). It counts as raised only over the
 * task it rose over, so it can never leak into a gesture over another app. Main thread only: the swipe handler, the
 * window and the shell's replies run there.
 */
public final class RoseEdgeState {
    private RoseEdgeState() {}

    /** Told on the main thread whenever a display's edge goes up or down, and while a drag reveals it. */
    public interface Listener {
        void onRaisedChanged(int displayId, boolean raised);

        /**
         * A drag in progress reveals the tray: reveal 0..1 (1 is fully shown), approach 0..1 toward the switcher, and
         * the stage (RoseEdgeGesture.NONE, TRAY or SWITCHER). reveal < 0 means the drag ended; the tray stays only if
         * the edge rose.
         */
        default void onPeek(int displayId, float reveal, float approach, int stage) {}

        /**
         * The shell really shortened the raised task (its onRaised, again on every later onRaised): from here on
         * nothing of the app lies under the edge, so the window may report its own height as an inset. A lower always
         * arrives as onRaisedChanged(displayId, false).
         */
        default void onResized(int displayId) {}
    }

    /** Display id to the task id the edge rose over; absent when lowered. */
    private static final SparseIntArray RAISED_OVER = new SparseIntArray();
    /** Display id to the app bounds the shell reported once it really shortened that task. */
    private static final SparseArray<Rect> RESIZED = new SparseArray<>();
    /** Display id to the edge window's microphone-input slot, as last laid out. */
    private static final SparseArray<MicSlot> MIC_SLOT = new SparseArray<>();
    /** Agreed with OS Core (#410): each side 32-64dp, inside the bottom band of height min(H / 4, 160dp). */
    static final float MIC_SLOT_MIN_DP = 32, MIC_SLOT_MAX_DP = 64, MIC_SLOT_BAND_MAX_DP = 160;
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

    /** The shell has shortened this task's app area on this display (onRaised arrived for it). */
    public static boolean isResized(int displayId, int taskId) {
        return isRaised(displayId, taskId) && RESIZED.get(displayId) != null;
    }

    /** The app bounds the shell reported for the raised task, or null before it replied. */
    public static Rect resizedBounds(int displayId) {
        Rect bounds = RESIZED.get(displayId);
        return bounds == null ? null : new Rect(bounds);
    }

    /** Whether something (the edge window) is there to draw a raised edge. */
    public static boolean hasListener() {
        return !LISTENERS.isEmpty();
    }

    /** Whether a raise could happen at all: something draws the edge and the shell can really resize the app. */
    public static boolean canRaise() {
        return !LISTENERS.isEmpty() && RoseEdgeShell.available();
    }

    /**
     * Raises the edge over taskId and asks the shell to shorten the app by liftPx. Refused (false) when no listener
     * could draw it or the shell could not be asked; then nothing changes.
     */
    public static boolean raise(int displayId, int taskId, int liftPx) {
        mainThread();
        if (!canRaise()) return false;
        if (isRaised(displayId, taskId)) return true;
        boolean wasRaised = isRaised(displayId);
        RAISED_OVER.put(displayId, taskId);
        RESIZED.remove(displayId);
        if (!RoseEdgeShell.raise(displayId, taskId, liftPx, SHELL_REPLIES)) {
            RAISED_OVER.delete(displayId);
            if (wasRaised) notifyChanged(displayId, false);
            return false;
        }
        if (!wasRaised) notifyChanged(displayId, true);
        return true;
    }

    /**
     * A drag over an app reveals the tray (RoseEdgeSwipe.onMove). Drawing only: nothing is raised, resized or sent to
     * the shell, and the microphone slot is never sent before the shell's onRaised.
     */
    public static void peek(int displayId, float reveal, float approach, int stage) {
        mainThread();
        for (Listener listener : new ArrayList<>(LISTENERS)) {
            listener.onPeek(displayId, Math.max(0f, reveal), approach, stage);
        }
    }

    /** The drag that revealed the tray ended; it stays drawn only if the edge rose. */
    public static void endPeek(int displayId) {
        mainThread();
        for (Listener listener : new ArrayList<>(LISTENERS)) listener.onPeek(displayId, -1f, 0f, 0);
    }

    /** Lowers the edge and asks the shell to restore the full height. */
    public static void lower(int displayId) {
        mainThread();
        if (lowerHere(displayId)) RoseEdgeShell.lower(displayId);
    }

    /**
     * The edge window's microphone-input slot on this display, in screen px, for a display of widthPx x heightPx at
     * density (null or empty: none). Shell shows SystemUI's existing trusted tray control there; Launcher3 never touches
     * microphone state. A slot belongs to the current raise: it is sent only after the shell's onRaised, again after
     * every later onRaised, never while lowered, and never outside the agreed bottom band and size.
     */
    public static void setMicSlot(int displayId, Rect slot, int widthPx, int heightPx, float density) {
        mainThread();
        if (slot == null || slot.isEmpty()) {
            MIC_SLOT.remove(displayId);
            if (RESIZED.get(displayId) != null) RoseEdgeShell.setMicSlot(displayId, new Rect());
            return;
        }
        MicSlot now = new MicSlot(slot, widthPx, heightPx, density), was = MIC_SLOT.get(displayId);
        // The same slot laid out again (a relayout, such as the window taking its inset) was already sent.
        if (now.equals(was)) return;
        MIC_SLOT.put(displayId, now);
        if (RESIZED.get(displayId) != null) sendMicSlot(displayId);
    }

    /**
     * Whether a slot satisfies the agreed geometry for a display of widthPx x heightPx at density, rounded exactly as
     * the shell's RoseEdgePolicy.validMicSlot rounds it (#523), so a slot sent here is never one the shell drops.
     */
    public static boolean micSlotFits(Rect slot, int widthPx, int heightPx, float density) {
        if (slot == null || slot.isEmpty() || !(density > 0)) return false;
        int min = Math.round(MIC_SLOT_MIN_DP * density), max = Math.round(MIC_SLOT_MAX_DP * density);
        int bandTop = heightPx - Math.min(heightPx / 4, Math.round(MIC_SLOT_BAND_MAX_DP * density));
        return slot.width() >= min && slot.width() <= max && slot.height() >= min && slot.height() <= max
                && slot.left >= 0 && slot.right <= widthPx && slot.top >= bandTop && slot.bottom <= heightPx;
    }

    private static void sendMicSlot(int displayId) {
        MicSlot slot = MIC_SLOT.get(displayId);
        if (slot == null || !micSlotFits(slot.bounds, slot.widthPx, slot.heightPx, slot.density)) return;
        RoseEdgeShell.setMicSlot(displayId, new Rect(slot.bounds));
    }

    /** A laid-out slot and the display size it was laid out for. */
    private static final class MicSlot {
        final Rect bounds;
        final int widthPx, heightPx;
        final float density;

        MicSlot(Rect bounds, int widthPx, int heightPx, float density) {
            this.bounds = new Rect(bounds);
            this.widthPx = widthPx;
            this.heightPx = heightPx;
            this.density = density;
        }

        @Override
        public boolean equals(Object o) {
            if (!(o instanceof MicSlot)) return false;
            MicSlot other = (MicSlot) o;
            return bounds.equals(other.bounds) && widthPx == other.widthPx && heightPx == other.heightPx
                    && Float.compare(density, other.density) == 0;
        }

        @Override
        public int hashCode() {
            return bounds.hashCode() * 31 + widthPx * 7 + heightPx;
        }
    }

    public static void addListener(Listener listener) {
        mainThread();
        if (!LISTENERS.contains(listener)) LISTENERS.add(listener);
    }

    public static void removeListener(Listener listener) {
        mainThread();
        LISTENERS.remove(listener);
    }

    /** The shell's replies (RoseEdgeShell delivers them on the main thread). */
    static final RoseEdgeShell.Callback SHELL_REPLIES = new RoseEdgeShell.Callback() {
        @Override
        public void onRaised(int displayId, int taskId, Rect appBounds) {
            diag("shell: raised");
            if (!isRaised(displayId, taskId) || appBounds == null) return;
            RESIZED.put(displayId, new Rect(appBounds));
            // The slot belongs to this raise (the shell dropped any earlier one on its last lower).
            sendMicSlot(displayId);
            for (Listener listener : new ArrayList<>(LISTENERS)) listener.onResized(displayId);
        }

        @Override
        public void onRefused(int displayId, int taskId, int reason) {
            diag("shell: refused, reason " + reason);
            // Nothing was resized: take the edge down again, but only if it is still up over that same task.
            if (isRaised(displayId, taskId)) lowerHere(displayId);
        }

        @Override
        public void onLowered(int displayId, int reason) {
            diag("shell: lowered, reason " + reason);
            // The shell already restored the full height (it lowered by itself, or confirms a lower).
            lowerHere(displayId);
        }
    };

    /**
     * Developer-only (RoseEdgeSwipe.DIAG_PROPERTY): the shell's answers with their IRoseEdgeListener reason number, so
     * a dismissed tray can be traced to the rule that took it down. Content-free; off by default.
     */
    private static void diag(String message) {
        if (SystemProperties.getBoolean(RoseEdgeSwipe.PROPERTY, false)
                && SystemProperties.getBoolean(RoseEdgeSwipe.DIAG_PROPERTY, false)) {
            Log.i(RoseEdgeSwipe.TAG, message);
        }
    }

    /** Takes the edge down here only; true when it was up. */
    private static boolean lowerHere(int displayId) {
        int index = RAISED_OVER.indexOfKey(displayId);
        if (index < 0) return false;
        RAISED_OVER.removeAt(index);
        RESIZED.remove(displayId);
        notifyChanged(displayId, false);
        return true;
    }

    private static void notifyChanged(int displayId, boolean raised) {
        for (Listener listener : new ArrayList<>(LISTENERS)) listener.onRaisedChanged(displayId, raised);
    }

    private static void mainThread() {
        if (Looper.myLooper() != Looper.getMainLooper()) throw new IllegalStateException("RoseEdgeState: main thread only");
    }
}
