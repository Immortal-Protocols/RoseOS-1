/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.quickstep.fallback;

import android.graphics.Rect;
import android.os.Binder;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Parcel;
import android.os.RemoteException;

/**
 * RoseOS (#410 E2c): Launcher3's client for WMShell's raised-edge resize (com.android.wm.shell.roseedge.IRoseEdge,
 * OS Core). It is hand-written binder code because Launcher3QuickStepLib compiles no AIDL; the transaction ids and
 * argument order must equal os/staged/rose-edge-resize/aidl exactly, which the staged native test checks against the
 * aidl tool's own generated Stub and Proxy.
 *
 * The shell's binder arrives in SystemUiProxy's shell bundle under DESCRIPTOR (setShell). Without it nothing can resize
 * a window, so the edge never rises (RoseEdgeState): no padding or scaling stands in for the real resize. Replies arrive
 * on binder threads and are delivered on the main thread, where the edge state lives.
 */
public final class RoseEdgeShell {
    private RoseEdgeShell() {}

    public static final String DESCRIPTOR = "com.android.wm.shell.roseedge.IRoseEdge";
    public static final String LISTENER_DESCRIPTOR = "com.android.wm.shell.roseedge.IRoseEdgeListener";
    static final int TRANSACTION_RAISE = IBinder.FIRST_CALL_TRANSACTION + 1;
    static final int TRANSACTION_LOWER = IBinder.FIRST_CALL_TRANSACTION + 2;
    static final int TRANSACTION_ON_RAISED = IBinder.FIRST_CALL_TRANSACTION + 1;
    static final int TRANSACTION_ON_REFUSED = IBinder.FIRST_CALL_TRANSACTION + 2;
    static final int TRANSACTION_ON_LOWERED = IBinder.FIRST_CALL_TRANSACTION + 3;

    /** IRoseEdgeListener refusal reasons, as agreed with OS Core on #410. */
    public static final int REFUSED_NOT_TOP_TASK = 1, REFUSED_NOT_RESIZEABLE = 2, REFUSED_LOCKED = 3,
            REFUSED_ROTATION = 4, REFUSED_MULTI_WINDOW = 5, REFUSED_BUSY = 6, REFUSED_LIFT_RANGE = 7,
            REFUSED_DISABLED = 8, REFUSED_NOT_DEFAULT_DISPLAY = 9;
    /** IRoseEdgeListener lowered reasons; the shell lowers by itself for every reason but REQUESTED. */
    public static final int LOWERED_REQUESTED = 0, LOWERED_TASK_CHANGED = 1, LOWERED_HOME_OR_RECENTS = 2,
            LOWERED_KEYGUARD = 3, LOWERED_DISPLAY_CHANGED = 4, LOWERED_IME_SHOWN = 5, LOWERED_LISTENER_DIED = 6,
            LOWERED_USER_SWITCH = 7;

    /** The shell's replies, on the main thread. */
    public interface Callback {
        void onRaised(int displayId, int taskId, Rect appBounds);
        void onRefused(int displayId, int taskId, int reason);
        void onLowered(int displayId, int reason);
    }

    private static IBinder sShell;
    /**
     * The one listener binder per callback: the shell ties a display's raise to the binder that raised it and refuses
     * a raise from another binder as BUSY, so every raise from this process uses the same one.
     */
    private static Listener sListener;

    /** From SystemUiProxy when the shell bundle arrives (null when it is gone or has no such interface). */
    public static void setShell(IBinder shell) {
        sShell = shell;
    }

    public static boolean available() {
        IBinder shell = sShell;
        return shell != null && shell.isBinderAlive();
    }

    /** Asks the shell to shorten the display's app area by liftPx over taskId; false when it could not be asked. */
    public static boolean raise(int displayId, int taskId, int liftPx, Callback callback) {
        IBinder shell = sShell;
        if (shell == null || callback == null) return false;
        Parcel data = Parcel.obtain();
        try {
            data.writeInterfaceToken(DESCRIPTOR);
            data.writeInt(displayId);
            data.writeInt(taskId);
            data.writeInt(liftPx);
            data.writeStrongBinder(listenerFor(callback));
            return shell.transact(TRANSACTION_RAISE, data, null, IBinder.FLAG_ONEWAY);
        } catch (RemoteException | RuntimeException e) {
            return false;
        } finally {
            data.recycle();
        }
    }

    private static Listener listenerFor(Callback callback) {
        if (sListener == null || sListener.mCallback != callback) sListener = new Listener(callback);
        return sListener;
    }

    /** Asks the shell to restore the display's full height; false when it could not be asked. */
    public static boolean lower(int displayId) {
        IBinder shell = sShell;
        if (shell == null) return false;
        Parcel data = Parcel.obtain();
        try {
            data.writeInterfaceToken(DESCRIPTOR);
            data.writeInt(displayId);
            return shell.transact(TRANSACTION_LOWER, data, null, IBinder.FLAG_ONEWAY);
        } catch (RemoteException | RuntimeException e) {
            return false;
        } finally {
            data.recycle();
        }
    }

    /** IRoseEdgeListener: the shell's replies, read exactly as the generated Stub reads them, handed to the main thread. */
    static final class Listener extends Binder {
        private final Callback mCallback;
        private final Handler mMain = new Handler(Looper.getMainLooper());

        Listener(Callback callback) {
            mCallback = callback;
            attachInterface(null, LISTENER_DESCRIPTOR);
        }

        @Override
        protected boolean onTransact(int code, Parcel data, Parcel reply, int flags) throws RemoteException {
            if (code == INTERFACE_TRANSACTION) {
                reply.writeString(LISTENER_DESCRIPTOR);
                return true;
            }
            if (code < FIRST_CALL_TRANSACTION || code > TRANSACTION_ON_LOWERED) return super.onTransact(code, data,
                    reply, flags);
            data.enforceInterface(LISTENER_DESCRIPTOR);
            int displayId = data.readInt();
            switch (code) {
                case TRANSACTION_ON_RAISED: {
                    int taskId = data.readInt();
                    Rect bounds = data.readTypedObject(Rect.CREATOR);
                    mMain.post(() -> mCallback.onRaised(displayId, taskId, bounds));
                    return true;
                }
                case TRANSACTION_ON_REFUSED: {
                    int taskId = data.readInt(), reason = data.readInt();
                    mMain.post(() -> mCallback.onRefused(displayId, taskId, reason));
                    return true;
                }
                case TRANSACTION_ON_LOWERED: {
                    int reason = data.readInt();
                    mMain.post(() -> mCallback.onLowered(displayId, reason));
                    return true;
                }
                default:
                    return super.onTransact(code, data, reply, flags);
            }
        }
    }
}
