package dev.pixel.voice;

import dev.pixel.voice.CurrentApps.*;
import dev.pixel.voice.CurrentApps.SessionStop;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** #156/#225: Home's reading of App Core's phone-local call("current_apps") reply (PR355 head bcb96e8 fields, plus the
 * proposed instance/sessionStatus/complete). Synthetic replies on the host JVM; no controller or phone. */
public final class CurrentAppsReplyTest {
    static int checks;
    static void expect(boolean c, String m) { checks++; if (!c) throw new AssertionError(m); }
    static final String CALC = "com.android.calculator2", PERM = "com.google.android.permissioncontroller";
    static List<ReplyApp> apps(ReplyApp... a) { return Arrays.asList(a); }
    static Snapshot live(long gen, String status, Boolean complete, ReplyApp... a) {
        return CurrentApps.fromReply("reader-1", "live", "s-1", "rose-ws-1", gen, apps(a), "i-1", status, complete);
    }

    public static void main(String[] args) {
        // Complete, attested list: every member shown; only the topmost front app gets the preview handle.
        Snapshot s = live(4, "ready", true, new ReplyApp(PERM, true, false), new ReplyApp(CALC, false, true));
        expect(s.available && s.apps.size() == 2, "both members");
        expect(s.apps.get(0).preview != null && s.apps.get(0).preview.sessionId.equals("s-1") && s.apps.get(0).preview.controllerTaskId.equals("rose-ws-1"), "front app: the session display's preview");
        expect(s.apps.get(1).preview == null, "the app behind it says live view isn't available");
        expect(s.registration.equals("controller:i-1") && s.generation == 4, "registration from the controller instance");
        Snapshot two = live(5, "ready", true, new ReplyApp(PERM, true, false), new ReplyApp(CALC, true, true));
        expect(two.apps.get(0).preview != null && two.apps.get(1).preview == null, "one frame of the display: one preview handle, never one per member");
        expect(live(5, "ready", true, new ReplyApp(CALC, false, true)).apps.get(0).preview == null, "a member not on the display's front (windows not read): no live view claimed");
        expect(live(6, "blocked", true, new ReplyApp(CALC, true, true)).apps.get(0).state.equals(CurrentApps.STATE_WAITING), "blocked: waiting");
        expect(live(6, "launching", true, new ReplyApp(CALC, false, true)).apps.get(0).state.equals(CurrentApps.STATE_STARTING), "launching: starting");
        expect(CurrentApps.fromReply("r", "live", "s-1", null, 1L, apps(new ReplyApp(CALC, true, true)), "i", "ready", true).apps.get(0).preview == null, "no task tag: no preview (PR340 refuses it)");

        // Not attested complete: never shown as the whole set.
        for (Boolean c : new Boolean[]{null, false}) {
            Snapshot inc = live(7, "ready", c, new ReplyApp(CALC, true, true));
            expect(!inc.available && inc.reason.equals(CurrentApps.INCOMPLETE) && inc.apps.isEmpty(), "complete=" + c + ": explicit unavailable, no partial list");
        }
        Tracker t = new Tracker();
        t.apply(live(1, "ready", true, new ReplyApp(CALC, true, true)));
        t.apply(live(2, "ready", null, new ReplyApp(CALC, true, true)));
        expect(t.state() == State.UNAVAILABLE && t.reason().equals(CurrentApps.INCOMPLETE) && t.apps().isEmpty(), "losing attestation clears what was shown");
        expect(CurrentApps.text(CurrentApps.INCOMPLETE).contains("every app"), "said plainly");

        // none / locked / malformed / unreadable.
        Snapshot attestedNone = CurrentApps.fromReply("r", "none", null, null, 3L, Collections.<ReplyApp>emptyList(), "i", null, true);
        expect(attestedNone.available && attestedNone.apps.isEmpty(), "none attested complete: authoritatively nothing running");
        for (Boolean c : new Boolean[]{null, false}) {
            Snapshot n = CurrentApps.fromReply("r", "none", null, null, 3L, Collections.<ReplyApp>emptyList(), "i", null, c);
            expect(!n.available && n.reason.equals(CurrentApps.INCOMPLETE), "none with complete=" + c + ": unavailable, never 'none running'");
        }
        expect(CurrentApps.fromReply("r", "locked", null, null, 3L, Collections.<ReplyApp>emptyList(), "i", null, null).reason.equals(CurrentApps.LOCKED), "locked");
        expect(CurrentApps.fromReply("r", "none", null, null, 3L, apps(new ReplyApp(CALC, true, true)), "i", null, true).reason.equals(CurrentApps.MALFORMED), "none with members: malformed");
        expect(live(8, null, true, new ReplyApp(CALC, true, true)).reason.equals(CurrentApps.MALFORMED), "no sessionStatus: no guessed state");
        expect(live(8, "ready", true, new ReplyApp("", true, true)).reason.equals(CurrentApps.MALFORMED), "empty package");
        expect(CurrentApps.fromReply("r", "live", "", "t", 1L, apps(new ReplyApp(CALC, true, true)), "i", "ready", true).reason.equals(CurrentApps.MALFORMED), "live without session");
        expect(CurrentApps.fromReply("r", "error", null, null, 1L, null, "i", null, null).reason.equals(CurrentApps.UNREADABLE), "unknown status");
        expect(CurrentApps.fromReply("r", null, null, null, null, null, null, null, null).reason.equals(CurrentApps.UNREADABLE), "no reply");

        // PR355 head 41140ec, exact shapes: live is always complete:false (no sessionStatus field); none with no live
        // session is complete:true; locked lists nothing.
        for (String reason : new String[]{"behind_front_unlisted", "windows_unreadable", "blocked"}) {
            Snapshot pr = CurrentApps.fromReply("reader", "live", "s-1", "rose-ws-1", 9L, apps(new ReplyApp(CALC, true, true)), null, null, false);
            expect(!pr.available && pr.reason.equals(CurrentApps.INCOMPLETE) && pr.apps.isEmpty(), "PR355 live (" + reason + "): can't confirm, never a list or 'couldn't be read'");
        }
        Snapshot prNone = CurrentApps.fromReply("reader", "none", null, null, 9L, Collections.<ReplyApp>emptyList(), null, null, true);
        expect(prNone.available && prNone.apps.isEmpty(), "PR355 none, complete: genuinely nothing under control");
        Snapshot prLocked = CurrentApps.fromReply("reader", "locked", null, null, 9L, Collections.<ReplyApp>emptyList(), null, null, false);
        expect(!prLocked.available && prLocked.reason.equals(CurrentApps.LOCKED), "PR355 locked");
        // PR361 platform semantics through the controller: an open session whose apps all left (handover) is attested
        // empty without any state; a complete platform list includes hidden apps, still one preview handle (front).
        Snapshot handover = CurrentApps.fromReply("reader", "live", "s-1", "rose-ws-1", 10L, Collections.<ReplyApp>emptyList(), null, null, true);
        expect(handover.available && handover.apps.isEmpty(), "open session, no apps, complete: nothing under control");
        Snapshot hidden = CurrentApps.fromReply("reader", "live", "s-1", "rose-ws-1", 11L, apps(new ReplyApp(PERM, true, false), new ReplyApp(CALC, false, true)), null, "ready", true);
        expect(hidden.available && hidden.apps.size() == 2 && hidden.apps.get(0).preview != null && hidden.apps.get(1).preview == null, "complete list with a hidden app: listed, live view unavailable");
        expect(CurrentApps.fromReply("reader", "live", "s-1", "rose-ws-1", 11L, apps(new ReplyApp(CALC, true, true)), null, null, true).reason.equals(CurrentApps.MALFORMED), "complete members without sessionStatus: no guessed state");
        // PR379 owned_devices: an attested complete live list is shown whatever the informational sessionStatus, even
        // without a session; every other source keeps the ready/blocked/launching gate. Live view only with a ready,
        // explicitly associated session's front app.
        for (String st : new String[]{"cleanup_failed", null, "ready", "blocked", "launching"}) {
            Snapshot o = CurrentApps.fromReply("r", "live", "s-1", "rose-ws-1", 20L, apps(new ReplyApp(CALC, false, false)), null, st, true, "owned_devices");
            expect(o.available && o.apps.size() == 1 && o.apps.get(0).packageName.equals(CALC), "owned_devices, sessionStatus " + st + ": the attested app is shown");
        }
        expect(CurrentApps.fromReply("r", "live", "s-1", "t", 20L, apps(new ReplyApp(CALC, false, false)), null, "cleanup_failed", true, "owned_devices").apps.get(0).state.equals(CurrentApps.STATE_RUNNING), "informational status: the app is running");
        expect(CurrentApps.fromReply("r", "live", "s-1", "t", 20L, apps(new ReplyApp(CALC, false, false)), null, "blocked", true, "owned_devices").apps.get(0).state.equals(CurrentApps.STATE_WAITING), "a known status still names the card's state");
        Snapshot noSession = CurrentApps.fromReply("r", "live", null, null, 21L, apps(new ReplyApp(CALC, true, false)), null, null, true, "owned_devices");
        expect(noSession.available && noSession.apps.size() == 1 && noSession.apps.get(0).preview == null, "owned_devices without a session: shown, no live view");
        for (String src : new String[]{"session_windows", "platform", null}) {
            expect(CurrentApps.fromReply("r", "live", "s-1", "t", 22L, apps(new ReplyApp(CALC, true, true)), null, "cleanup_failed", true, src).reason.equals(CurrentApps.MALFORMED), "source " + src + ": no guessed state");
            expect(CurrentApps.fromReply("r", "live", null, "t", 22L, apps(new ReplyApp(CALC, true, true)), null, "ready", true, src).reason.equals(CurrentApps.MALFORMED), "source " + src + ": a live list needs its session");
        }
        expect(CurrentApps.fromReply("r", "live", "s-1", "t", 23L, apps(new ReplyApp(CALC, true, true)), null, "cleanup_failed", false, "owned_devices").reason.equals(CurrentApps.INCOMPLETE), "owned_devices complete:false: cannot confirm");
        expect(CurrentApps.fromReply("r", "none", null, null, 23L, Collections.<ReplyApp>emptyList(), null, null, true, "owned_devices").available, "owned_devices none, complete: genuinely empty");
        for (String st : new String[]{"blocked", "launching", "cleanup_failed", null}) {
            Snapshot np = CurrentApps.fromReply("r", "live", "s-1", "rose-ws-1", 24L, apps(new ReplyApp(CALC, true, true)), null, st, true, "owned_devices");
            expect(np.apps.get(0).preview == null, "sessionStatus " + st + ": no live view");
        }
        expect(CurrentApps.fromReply("r", "live", "s-1", "rose-ws-1", 24L, apps(new ReplyApp(CALC, true, true)), null, "ready", true, "owned_devices").apps.get(0).preview != null, "ready, associated, front: live view");
        expect(CurrentApps.fromReply("r", "live", "s-1", "rose-ws-1", 24L, apps(new ReplyApp(CALC, true, true)), null, "blocked", true, "platform").apps.get(0).preview == null, "platform blocked: no live view either");
        // Agreed two-app contract (owned_devices + bindings): membership is the snapshot; bindings give controls only.
        String MAPS = "com.google.android.apps.maps";
        List<Binding> pair = Arrays.asList(new Binding(0, "s-0", "rose-ws-0", CALC, "ready", true, true), new Binding(1, "s-1", "rose-ws-1", MAPS, "blocked", true, true));
        Snapshot both = CurrentApps.fromBindings("r", "live", 30L, apps(new ReplyApp(CALC, true, true, "s-0"), new ReplyApp(MAPS, true, true, "s-1")), pair, "e-1", true);
        expect(both.available && both.apps.size() == 2 && both.registration.equals("controller:e-1") && both.generation == 30, "two sessions: two cards, epoch registration");
        App calc = both.apps.get(0), maps = both.apps.get(1);
        expect(calc.state.equals(CurrentApps.STATE_RUNNING) && maps.state.equals(CurrentApps.STATE_WAITING), "each card's state from its own binding");
        expect(calc.preview != null && calc.preview.sessionId.equals("s-0") && calc.preview.controllerTaskId.equals("rose-ws-0"), "ready session's front app: its own preview handle");
        expect(maps.preview == null, "blocked session: no live view");
        expect(calc.stop != null && calc.stop.sessionId.equals("s-0") && maps.stop != null && maps.stop.controllerTaskId.equals("rose-ws-1"), "each bound session has its own Stop handle");
        expect(!calc.entryId.equals(maps.entryId), "distinct entries");
        Snapshot lingering = CurrentApps.fromBindings("r", "live", 31L, apps(new ReplyApp(CALC, false, false, null), new ReplyApp(MAPS, true, true, "s-1")),
            Arrays.asList(new Binding(1, "s-1", "rose-ws-1", MAPS, "ready", true, true)), "e-1", true);
        expect(lingering.apps.size() == 2 && lingering.apps.get(0).sessionId.isEmpty() && lingering.apps.get(0).stop == null && lingering.apps.get(0).preview == null, "lingering/unbound app: shown, no Stop or live view");
        expect(lingering.apps.get(0).state.equals(CurrentApps.STATE_RUNNING), "unbound app is running (attested)");
        Snapshot unboundRef = CurrentApps.fromBindings("r", "live", 32L, apps(new ReplyApp(CALC, true, true, "s-gone")), Arrays.asList(new Binding(0, "s-gone", "t", CALC, "ready", false, true)), "e-1", true);
        expect(unboundRef.available && unboundRef.apps.get(0).stop == null && unboundRef.apps.get(0).preview == null, "binding not bound: membership kept, no controls");
        Snapshot untagged = CurrentApps.fromBindings("r", "live", 33L, apps(new ReplyApp(CALC, true, true, "s-0")), Arrays.asList(new Binding(0, "s-0", null, CALC, "ready", true, true)), "e-1", true);
        expect(untagged.apps.get(0).stop == null && untagged.apps.get(0).preview == null, "untagged slot: no Home Stop or live view (needs both ids)");
        Snapshot unknownFront = CurrentApps.fromBindings("r", "live", 34L, apps(new ReplyApp(CALC, true, true, "s-0")), Arrays.asList(new Binding(0, "s-0", "t", CALC, "ready", true, false)), "e-1", true);
        expect(unknownFront.apps.get(0).preview == null && unknownFront.apps.get(0).stop != null, "frontKnown:false: no live view, Stop still available");
        Snapshot twoFronts = CurrentApps.fromBindings("r", "live", 35L, apps(new ReplyApp(CALC, true, true, "s-0"), new ReplyApp(MAPS, true, false, "s-0")), Arrays.asList(new Binding(0, "s-0", "t", CALC, "ready", true, true)), "e-1", true);
        expect(twoFronts.apps.get(0).preview == null && twoFronts.apps.get(1).preview == null, "two fronts for one session: Home never picks, no live view");
        Snapshot notFront = CurrentApps.fromBindings("r", "live", 36L, apps(new ReplyApp(CALC, false, true, "s-0")), Arrays.asList(new Binding(0, "s-0", "t", CALC, "ready", true, true)), "e-1", true);
        expect(notFront.apps.get(0).preview == null, "a hidden app: no live view");
        for (String st : new String[]{"launching", "cleanup_failed", ""})
            expect(CurrentApps.fromBindings("r", "live", 37L, apps(new ReplyApp(CALC, true, true, "s-0")), Arrays.asList(new Binding(0, "s-0", "t", CALC, st, true, true)), "e-1", true).apps.get(0).preview == null, st + ": no live view");
        expect(CurrentApps.fromBindings("r", "live", 38L, apps(new ReplyApp(CALC, true, true, "s-0")), Arrays.asList(new Binding(0, "s-0", "t", CALC, "ready", true, true)), "e-1", false).reason.equals(CurrentApps.INCOMPLETE), "complete:false: cannot confirm, whatever the bindings");
        expect(CurrentApps.fromBindings("r", "none", 39L, Collections.<ReplyApp>emptyList(), Collections.<Binding>emptyList(), "e-1", true).available, "none+complete: genuinely empty");
        expect(!CurrentApps.fromBindings("r", "none", 39L, Collections.<ReplyApp>emptyList(), null, "e-1", false).available, "none without attestation: cannot confirm");
        expect(CurrentApps.fromBindings("r", "locked", 40L, Collections.<ReplyApp>emptyList(), null, "e-1", false).reason.equals(CurrentApps.LOCKED), "locked");
        expect(CurrentApps.fromBindings("r", "live", 41L, apps(new ReplyApp(CALC, true, true, "s-0"), new ReplyApp(CALC, false, false, "s-1")), pair, "e-1", true).reason.equals(CurrentApps.MALFORMED), "a package listed twice: malformed, never an owner guess");
        expect(CurrentApps.fromBindings("r", "live", 42L, apps(new ReplyApp("", true, true, "s-0")), pair, "e-1", true).reason.equals(CurrentApps.MALFORMED), "empty package");
        expect(CurrentApps.fromBindings("r", "live", 43L, apps(new ReplyApp(CALC, false, true, null)), null, "e-1", true).apps.size() == 1, "no bindings at all: membership still shown");
        expect(CurrentApps.fromBindings("reader-9", "live", 44L, apps(new ReplyApp(CALC, false, true, null)), null, null, true).registration.equals("reader-9"), "no epoch: the reader's registration");
        Tracker ep = new Tracker();
        ep.apply(CurrentApps.fromBindings("r", "live", 50L, apps(new ReplyApp(CALC, true, true, "s-0")), pair, "e-1", true));
        expect(ep.apply(CurrentApps.fromBindings("r", "none", 1L, Collections.<ReplyApp>emptyList(), null, "e-2", true)) && ep.state() == State.EMPTY, "controller restart (new epoch): its lower generation applies");
        expect(!ep.apply(CurrentApps.fromBindings("r", "live", 51L, apps(new ReplyApp(CALC, true, true, "s-0")), pair, "e-1", true)) && ep.apps().isEmpty(), "the old epoch's late reply never resurrects");

        // Session Stop: confirmed only for exactly the captured pair; timeout is unknown, never stopped.
        SessionStop st = new SessionStop("s-1", "rose-ws-1", 1000);
        expect(st.pending() && st.phase == SessionStop.Phase.SENDING, "sent");
        st.answered(false);
        expect(st.phase == SessionStop.Phase.STOPPING, "accepted alone is stopping, never stopped");
        st.answered(null);
        expect(st.phase == SessionStop.Phase.STOPPING, "no reply: still stopping");
        String[][] wrong = {{"cleanup_failed", "user", "s-1", "rose-ws-1"}, {"stopped", "agent", "s-1", "rose-ws-1"}, {"stopped", "user", "s-2", "rose-ws-1"},
            {"stopped", "user", "s-1", "rose-ws-9"}, {"ready", "user", "s-1", "rose-ws-1"}, {"stopped", "user", null, null}};
        for (String[] w : wrong) { st.summary(w[0], w[1], w[2], w[3]); expect(st.phase == SessionStop.Phase.STOPPING, "no confirmation from " + Arrays.toString(w)); }
        st.tick(1000 + SessionStop.TIMEOUT_MS - 1);
        expect(st.phase == SessionStop.Phase.STOPPING, "before the timeout");
        st.tick(1000 + SessionStop.TIMEOUT_MS);
        expect(st.phase == SessionStop.Phase.UNKNOWN && !st.pending(), "timeout: unknown (may be retried), never stopped");
        expect(st.checking(1000 + SessionStop.TIMEOUT_MS + 1) && !st.checking(1000 + 2 * SessionStop.TIMEOUT_MS), "a late confirmation is still looked for, for a while");
        st.summary("stopped", "user", "s-1", "rose-ws-1");
        expect(st.phase == SessionStop.Phase.STOPPED, "a late matching summary settles it");
        st.summary("ready", "user", "s-2", "x"); st.answered(false); st.tick(1000 + 10 * SessionStop.TIMEOUT_MS);
        expect(st.phase == SessionStop.Phase.STOPPED, "stopped is final");
        SessionStop direct = new SessionStop("s-0", "rose-ws-0", 0);
        direct.answered(true);
        expect(direct.phase == SessionStop.Phase.STOPPED && !direct.pending(), "owner_stop confirmed:true");
        SessionStop untaggedStop = new SessionStop("s-0", "", 0);
        untaggedStop.summary("stopped", "user", "s-0", null);
        expect(untaggedStop.phase == SessionStop.Phase.STOPPED, "an exactly matching null task (untagged) confirms");
        SessionStop noSid = new SessionStop("", "", 0);
        noSid.summary("stopped", "user", null, null);
        expect(noSid.phase != SessionStop.Phase.STOPPED, "no captured session: never confirmed");

        // Controller restart: a new instance is a new registration even though its generation starts again.
        Tracker r = new Tracker();
        r.apply(live(40, "ready", true, new ReplyApp(CALC, true, true)));
        Snapshot restarted = CurrentApps.fromReply("reader-1", "none", null, null, 1L, Collections.<ReplyApp>emptyList(), "i-2", null, true);
        expect(r.apply(restarted) && r.state() == State.EMPTY, "restart: the lower generation of a new instance applies");
        expect(!r.apply(live(41, "ready", true, new ReplyApp(CALC, true, true))) && r.apps().isEmpty(), "the old instance's late reply never resurrects");
        Snapshot noInstance = CurrentApps.fromReply("reader-7", "none", null, null, 1L, Collections.<ReplyApp>emptyList(), null, null, true);
        expect(noInstance.registration.equals("reader-7"), "without instance: the reader's own registration");
        // #410 AppCore finding: the running edge (and Rose's working button) mean authoritatively running only, i.e. the
        // app's controller session reported exactly ready. Membership and Stop are unchanged; the card's chip stays truthful.
        String[][] notRunning = {{"launching", "Starting"}, {"starting", "Starting"}, {"scheduled", "Starting"}, {"blocked", "Waiting"},
            {"consent_required", "Waiting"}, {"cleanup_failed", "Still open"}, {"", "Open"}, {"mystery", "Open"}};
        for (String[] c : notRunning) {
            Snapshot b = CurrentApps.fromBindings("r", "live", 60L, apps(new ReplyApp(CALC, true, true, "s-0")), Arrays.asList(new Binding(0, "s-0", "rose-ws-0", CALC, c[0], true, true)), "e-1", true);
            App a = b.apps.get(0);
            expect(b.available && b.apps.size() == 1, c[0] + ": membership kept");
            expect(!a.authoritativelyRunning(), c[0] + ": not authoritatively running (no lit edge)");
            expect(CurrentApps.cardState(a).equals(c[1]), c[0] + ": chip says " + c[1] + ", got " + CurrentApps.cardState(a));
            expect(a.stop != null && a.stop.sessionId.equals("s-0") && a.stop.controllerTaskId.equals("rose-ws-0"), c[0] + ": per-session Stop unchanged");
        }
        App ready = CurrentApps.fromBindings("r", "live", 61L, apps(new ReplyApp(CALC, true, true, "s-0")), Arrays.asList(new Binding(0, "s-0", "rose-ws-0", CALC, "ready", true, true)), "e-1", true).apps.get(0);
        expect(ready.authoritativelyRunning() && CurrentApps.cardState(ready).equals("Running") && ready.stop != null, "ready: authoritatively running, Stop kept");
        Snapshot mixed = CurrentApps.fromBindings("r", "live", 62L, apps(new ReplyApp(CALC, false, false, null), new ReplyApp(MAPS, true, true, "s-1"), new ReplyApp(PERM, true, true, "s-gone")),
            Arrays.asList(new Binding(1, "s-1", "rose-ws-1", MAPS, "ready", true, true), new Binding(2, "s-gone", "t", PERM, "ready", false, true)), "e-1", true);
        expect(!mixed.apps.get(0).authoritativelyRunning() && CurrentApps.cardState(mixed.apps.get(0)).equals("Open"), "lingering/unbound app: no edge, chip Open");
        expect(mixed.apps.get(1).authoritativelyRunning(), "its ready neighbour still lights");
        expect(!mixed.apps.get(2).authoritativelyRunning() && mixed.apps.get(2).stop == null, "a ready binding that isn't bound: no edge, no Stop");
        for (String os : new String[]{"cleanup_failed", "consent_required", "scheduled", null}) {
            App o = CurrentApps.fromReply("r", "live", "s-1", "rose-ws-1", 63L, apps(new ReplyApp(CALC, true, true)), null, os, true, "owned_devices").apps.get(0);
            expect(o.state.equals(CurrentApps.STATE_RUNNING) && !o.authoritativelyRunning(), "owned_devices " + os + ": listed under control, not authoritatively running");
        }
        expect(CurrentApps.fromReply("r", "live", "s-1", "rose-ws-1", 64L, apps(new ReplyApp(CALC, true, true)), null, "ready", true, "owned_devices").apps.get(0).authoritativelyRunning(), "owned_devices with a ready session: running");
        expect(!CurrentApps.fromReply("r", "live", null, null, 64L, apps(new ReplyApp(CALC, true, true)), null, "ready", true, "owned_devices").apps.get(0).authoritativelyRunning(), "owned_devices ready but no session: not running");
        expect(live(65, "ready", true, new ReplyApp(CALC, true, true)).apps.get(0).authoritativelyRunning(), "session reply ready: running");
        expect(!live(65, "launching", true, new ReplyApp(CALC, true, true)).apps.get(0).authoritativelyRunning() && !live(65, "blocked", true, new ReplyApp(CALC, true, true)).apps.get(0).authoritativelyRunning(), "session reply launching/blocked: not running");
        expect(CurrentApps.sessionRunning("ready") && !CurrentApps.sessionRunning("launching") && !CurrentApps.sessionRunning("cleanup_failed") && !CurrentApps.sessionRunning(null) && !CurrentApps.sessionRunning("Ready"), "the one running status is exactly ready");
        Tracker flip = new Tracker();
        flip.apply(CurrentApps.fromBindings("r", "live", 70L, apps(new ReplyApp(CALC, true, true, "s-0")), Arrays.asList(new Binding(0, "s-0", "t", CALC, "ready", true, true)), "e-1", true));
        expect(flip.apply(CurrentApps.fromBindings("r", "live", 71L, apps(new ReplyApp(CALC, true, true, "s-0")), Arrays.asList(new Binding(0, "s-0", "t", CALC, "cleanup_failed", true, true)), "e-1", true)), "ready -> cleanup_failed is a visible change (edge goes out)");
        expect(!flip.apps().get(0).authoritativelyRunning(), "after the flip: not running");
        System.out.println("CurrentAppsReplyTest: " + checks + " checks passed");
    }
}
