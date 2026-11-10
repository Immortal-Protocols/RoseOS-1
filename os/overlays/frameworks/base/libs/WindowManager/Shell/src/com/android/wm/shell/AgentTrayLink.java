/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.wm.shell;

/**
 * SystemUI's subscription to Rose Agent's tray service, as pure logic for the regression tests.
 * Subscribed (one REGISTER) only while the screen is on; screen-off sends UNREGISTER, which stops
 * the service's poll when SystemUI is its last client, and keeps the binding for a quick resume.
 * The service answers each REGISTER with fresh state, so a stale snapshot is never shown.
 * {@link Return} carries 1C's exact-task return over this same link; it is public only for the shell's
 * RoseEdgeController, in its own package.
 */
public final class AgentTrayLink {
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

    /**
     * F08: what this client's REGISTER says changed (the Rose chin's availability, arg2). While subscribed, REGISTER
     * again so the service updates it; the service answers every REGISTER with fresh state, so a repeat is harmless.
     */
    void refresh() {
        if (!registered) return;
        if (!port.send(REGISTER)) disconnected();
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

    /**
     * 1C (agreed with App Core and Home/App, protocol v1): the exact-task return. The shell's RoseEdgeController
     * records and restores the task; the status-bar tray only carries its messages to and from Rose Agent, on this
     * link's existing trust (Rose Agent's uid on one side, SystemUI's identity on the other). Both live in SystemUI's
     * process, so this is a plain in-process seam: nothing new is exported.
     */
    public static final class Return {
        // SystemUI to Rose Agent.
        public static final int TARGET = 7, RESULT = 8;
        // Rose Agent to SystemUI.
        public static final int RETURN_TO = 101, CANCEL = 102;
        // RESULT's arg2.
        public static final int RETURNED = 0, UNAVAILABLE = 1, REFUSED = 2;

        /** Where one return's single answer goes; any thread. */
        public interface Answer {
            void answer(int attempt, int result);
        }

        /** The shell side: a return to the record quoting token, answered exactly once. */
        public interface Seam {
            void returnTo(long token, int attempt, Answer answer);
        }

        /** What RETURN_TARGET says; token 0 means there is nothing to return to. */
        public static final class Target {
            public static final Target NONE = new Target(0, "", 0, "");
            public final long token;
            public final String packageName;
            public final int userId;
            public final String label;

            public Target(long token, String packageName, int userId, String label) {
                this.token = token;
                this.packageName = packageName;
                this.userId = userId;
                this.label = label;
            }
        }

        private static volatile Seam seam;
        private static volatile Target target = Target.NONE;
        private static volatile Runnable listener;

        private Return() {}

        /** The shell installs itself once. */
        public static void install(Seam s) {
            seam = s;
        }

        /** The shell's record changed (null is none); the tray is told on whatever thread this runs. */
        public static void publish(Target t) {
            target = t == null ? Target.NONE : t;
            final Runnable l = listener;
            if (l != null) l.run();
        }

        static Target target() {
            return target;
        }

        /** The tray listens, and posts to its own thread. */
        static void listen(Runnable l) {
            listener = l;
        }

        /** A RETURN_TO from Rose Agent; with no shell installed it is refused, never guessed. */
        static void returnTo(long token, int attempt, Answer answer) {
            final Seam s = seam;
            if (s == null) {
                answer.answer(attempt, REFUSED);
                return;
            }
            s.returnTo(token, attempt, answer);
        }
    }
}
