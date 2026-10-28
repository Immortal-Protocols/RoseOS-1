/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.wm.shell;

/**
 * SystemUI's subscription to Rose Agent's tray service, as pure logic for the regression tests.
 * Subscribed (one REGISTER) only while the screen is on; screen-off sends UNREGISTER, which stops
 * the service's poll when SystemUI is its last client, and keeps the binding for a quick resume.
 * The service answers each REGISTER with fresh state, so a stale snapshot is never shown.
 */
final class AgentTrayLink {
    static final int REGISTER = 1, UNREGISTER = 2;

    /** Side effects, performed by AgentTrayPrototype on the main thread. */
    interface Port {
        /** bindServiceAsUser for the tray service; false when unavailable. */
        boolean bind(int user);
        void unbind();
        /** Messenger send with SystemUI's reply endpoint; false on RemoteException or no service. */
        boolean send(int what);
        /** Replaces any pending retry; at most one is ever pending. */
        void scheduleRetry();
        void cancelRetry();
        /** Forgets the last received state; the service connection is kept. */
        void resetState();
        /** Drops the service Messenger. */
        void forgetService();
    }

    private final Port port;
    private boolean screenOn, bound, connected, registered;
    private int user = -1;

    AgentTrayLink(Port port) { this.port = port; }

    boolean subscribed() { return registered; }
    int user() { return user; }

    /** Screen on or user present; repeated calls keep exactly one subscription. */
    void screenOn(int currentUser) {
        screenOn = true;
        ensure(currentUser);
    }

    /** Suspends the subscription. Voice and tasks are untouched: this only stops tray updates. */
    void screenOff() {
        screenOn = false;
        port.cancelRetry();
        unsubscribe();
        port.resetState();
    }

    void userSwitched(int currentUser) {
        if (screenOn) ensure(currentUser);
        else if (bound && currentUser != user) drop();
    }

    /** The pending retry fired. */
    void retry(int currentUser) {
        if (screenOn) ensure(currentUser);
    }

    /** onServiceConnected, possibly late (after screen-off or a repeated resume). */
    void connected() {
        if (!bound) return;
        connected = true;
        if (screenOn) subscribe();
    }

    /** onServiceDisconnected: the process died; Android keeps the binding and reconnects it. */
    void disconnected() {
        connected = registered = false;
        port.forgetService();
        port.resetState();
    }

    /** onBindingDied or onNullBinding: this binding will not reconnect. */
    void bindingDied() {
        drop();
        if (screenOn) port.scheduleRetry();
    }

    private void ensure(int currentUser) {
        port.cancelRetry();
        if (bound && currentUser != user) drop();
        if (!bound) {
            user = currentUser;
            bound = port.bind(user);
            if (!bound) {
                port.scheduleRetry();
                return;
            }
        }
        if (connected) subscribe();
    }

    private void subscribe() {
        if (registered) return;
        if (port.send(REGISTER)) registered = true;
        else disconnected();
    }

    private void unsubscribe() {
        if (!registered) return;
        registered = false;
        port.send(UNREGISTER);
    }

    private void drop() {
        unsubscribe();
        if (bound) port.unbind();
        bound = connected = false;
        port.forgetService();
        port.resetState();
    }
}
