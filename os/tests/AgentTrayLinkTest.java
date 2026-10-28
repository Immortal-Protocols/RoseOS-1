/* Copyright 2026. SPDX-License-Identifier: Apache-2.0 */
package com.android.wm.shell;

import java.util.ArrayList;
import java.util.List;

/**
 * SystemUI tray subscription lifecycle (OS-TRAY-LIFECYCLE-001), driven through AgentTrayLink with a
 * fake service binding. Pure JVM: no Android runtime, binder or device.
 */
public final class AgentTrayLinkTest {
    static int checks;

    /** Records side effects and models one Android binding and AgentTrayService's client set. */
    static final class FakePort implements AgentTrayLink.Port {
        final List<String> log = new ArrayList<>();
        boolean bindOk = true, sendOk = true, serviceKnown, retryPending, stateShown;
        Integer boundUser;
        int registeredClients; // AgentTrayService keeps a set keyed by SystemUI's reply binder: 0 or 1

        @Override public boolean bind(int user) {
            log.add("bind:" + user);
            if (!bindOk) return false;
            if (boundUser != null) throw new AssertionError("second binding while one is held");
            boundUser = user;
            return true;
        }
        @Override public void unbind() {
            log.add("unbind");
            if (boundUser == null) throw new AssertionError("unbind without a binding");
            boundUser = null;
        }
        @Override public boolean send(int what) {
            log.add(what == AgentTrayLink.REGISTER ? "REGISTER" : "UNREGISTER");
            if (!serviceKnown || !sendOk) return false;
            if (what == AgentTrayLink.REGISTER) { registeredClients = 1; stateShown = true; }
            else registeredClients = 0;
            return true;
        }
        @Override public void scheduleRetry() { retryPending = true; }
        @Override public void cancelRetry() { retryPending = false; }
        @Override public void resetState() { stateShown = false; }
        @Override public void forgetService() { serviceKnown = false; }

        /** Android delivering onServiceConnected for the current binding. */
        void connect(AgentTrayLink link) { serviceKnown = true; link.connected(); }
        long count(String event) { return log.stream().filter(event::equals).count(); }
        void clear() { log.clear(); }
    }

    static void expect(String name, Object expected, Object actual) {
        if (!expected.equals(actual)) throw new AssertionError(name + ": expected " + expected + ", got " + actual);
        checks++;
    }

    static FakePort subscribedAt(AgentTrayLink[] out, int user) {
        FakePort port = new FakePort();
        AgentTrayLink link = new AgentTrayLink(port);
        link.screenOn(user);
        port.connect(link);
        out[0] = link;
        return port;
    }

    public static void main(String[] args) {
        AgentTrayLink[] holder = new AgentTrayLink[1];

        // Screen off suspends the subscription; the service's last client leaves, so its poll stops.
        FakePort port = subscribedAt(holder, 0);
        AgentTrayLink link = holder[0];
        expect("subscribed once on start", 1L, port.count("REGISTER"));
        port.clear();
        link.screenOff();
        expect("screen-off unregisters", List.of("UNREGISTER"), port.log);
        expect("service has no SystemUI client while off", 0, port.registeredClients);
        expect("binding kept for a quick resume", 0, port.boundUser);
        expect("stale state forgotten", false, port.stateShown);
        link.screenOff();
        expect("repeated screen-off sends nothing more", 1L, port.count("UNREGISTER"));

        // Resume restores exactly one subscription with fresh state, despite SCREEN_ON + USER_PRESENT.
        port.clear();
        link.screenOn(0);
        link.screenOn(0);
        expect("duplicate resume events register once", List.of("REGISTER"), port.log);
        expect("fresh state requested on resume", true, port.stateShown);
        expect("one client after resume", 1, port.registeredClients);

        // A pending bind that connects late, after the screen went off, must not subscribe.
        port = new FakePort();
        link = new AgentTrayLink(port);
        link.screenOn(0);
        link.screenOff();
        port.connect(link);
        expect("late connection while off does not register", 0L, port.count("REGISTER"));
        link.screenOn(0);
        expect("resume after late connection registers once", 1L, port.count("REGISTER"));
        expect("no rebind for an existing binding", 1L, port.count("bind:0"));

        // User switch moves the single subscription to the new user.
        port = subscribedAt(holder, 0);
        link = holder[0];
        port.clear();
        link.userSwitched(10);
        expect("user switch unregisters, unbinds, binds the new user",
                List.of("UNREGISTER", "unbind", "bind:10"), port.log);
        port.connect(link);
        expect("new user's subscription", 1L, port.count("REGISTER"));
        expect("tracked user", 10, link.user());
        link.userSwitched(10);
        expect("same user is a no-op", 1L, port.count("bind:10"));

        // User switch while the screen is off releases the old user's binding and waits for resume.
        link.screenOff();
        port.clear();
        link.userSwitched(0);
        expect("off-screen user switch releases only", List.of("unbind"), port.log);
        link.screenOn(0);
        port.connect(link);
        expect("resume binds and registers the current user", List.of("unbind", "bind:0", "REGISTER"), port.log);

        // Binder death: one retry, then exactly one fresh subscription.
        port = subscribedAt(holder, 0);
        link = holder[0];
        port.serviceKnown = false; // the dead binder rejects sends
        port.clear();
        link.bindingDied();
        expect("binding death releases the binding", List.of("UNREGISTER", "unbind"), port.log);
        expect("one retry pending", true, port.retryPending);
        link.retry(0);
        port.connect(link);
        expect("retry resubscribes once", 1L, port.count("REGISTER"));
        expect("retry cleared after a successful bind", false, port.retryPending);

        // Binding death while the screen is off schedules nothing; resume rebinds.
        port = subscribedAt(holder, 0);
        link = holder[0];
        link.screenOff();
        link.bindingDied();
        expect("no retry while off", false, port.retryPending);
        port.clear();
        link.screenOn(0);
        port.connect(link);
        expect("resume after off-screen death", List.of("bind:0", "REGISTER"), port.log);

        // Service process restart: Android keeps the binding and reconnects; subscribe again once.
        port = subscribedAt(holder, 0);
        link = holder[0];
        link.disconnected();
        port.serviceKnown = false;
        port.clear();
        port.connect(link);
        expect("reconnected service gets one REGISTER, no rebind", List.of("REGISTER"), port.log);
        link.disconnected();
        port.serviceKnown = false;
        link.screenOff();
        port.connect(link);
        expect("reconnection while off does not register", 1L, port.count("REGISTER"));

        // A failed REGISTER (RemoteException) leaves the link unsubscribed so the next connect retries.
        port = new FakePort();
        link = new AgentTrayLink(port);
        port.sendOk = false;
        link.screenOn(0);
        port.connect(link);
        expect("failed register is not counted", false, link.subscribed());
        port.sendOk = true;
        port.connect(link);
        expect("next connection subscribes", true, link.subscribed());

        // Bind failures keep a single pending retry (the earlier F7 fix) and stop while off.
        port = new FakePort();
        link = new AgentTrayLink(port);
        port.bindOk = false;
        link.screenOn(0);
        link.screenOn(0);
        link.userSwitched(0);
        expect("one pending retry after repeated failures", true, port.retryPending);
        link.screenOff();
        expect("screen-off cancels the retry", false, port.retryPending);
        link.retry(0);
        expect("a stale retry while off does not bind", 3L, port.log.stream().filter(e -> e.startsWith("bind")).count());

        System.out.println("AgentTrayLinkTest: " + checks + " checks passed");
    }
}
