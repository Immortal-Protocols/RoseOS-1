package dev.pixel.voice;

import dev.pixel.voice.CurrentApps.*;
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

        // Controller restart: a new instance is a new registration even though its generation starts again.
        Tracker r = new Tracker();
        r.apply(live(40, "ready", true, new ReplyApp(CALC, true, true)));
        Snapshot restarted = CurrentApps.fromReply("reader-1", "none", null, null, 1L, Collections.<ReplyApp>emptyList(), "i-2", null, true);
        expect(r.apply(restarted) && r.state() == State.EMPTY, "restart: the lower generation of a new instance applies");
        expect(!r.apply(live(41, "ready", true, new ReplyApp(CALC, true, true))) && r.apps().isEmpty(), "the old instance's late reply never resurrects");
        Snapshot noInstance = CurrentApps.fromReply("reader-7", "none", null, null, 1L, Collections.<ReplyApp>emptyList(), null, null, true);
        expect(noInstance.registration.equals("reader-7"), "without instance: the reader's own registration");
        System.out.println("CurrentAppsReplyTest: " + checks + " checks passed");
    }
}
