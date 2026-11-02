package dev.pixel.backgroundcontrol;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;

public final class MultiSessionTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final String CALC = "com.google.android.calculator", CLOCK = "com.google.android.deskclock",
            MAPS = "com.google.android.apps.maps", CONTROLLER = "dev.pixel.backgroundcontrol";
    static MultiSession.Slot slot(String id, String target, String status, String... attested) {
        boolean occupied = MultiSession.occupied(false, status);
        return new MultiSession.Slot(id, target, status, occupied, Arrays.asList(attested));
    }
    static final MultiSession.Slot IDLE = slot(null, null, "idle");
    static MultiSession.Slot[] slots(MultiSession.Slot a, MultiSession.Slot b) { return new MultiSession.Slot[] {a, b}; }
    static MultiSession.Membership open(int device, boolean complete, String... packages) {
        return new MultiSession.Membership(true, false, complete, device, Arrays.asList(packages));
    }

    public static void main(String[] args) {
        // Stable names and notification ids: slot 0 is byte-identical to the single-session controller.
        expect(MultiSession.name(0).equals("pixel-voice-background") && MultiSession.name(1).equals("pixel-voice-background-1"), "names");
        expect(MultiSession.notificationId(0) == 102 && MultiSession.notificationId(1) == 103, "notification ids");

        // sessionLimit is a capability: only an Integer 2 enables alongside.
        expect(MultiSession.sessionLimit(2) == 2, "pinned single-package uid");
        for (Object v : new Object[] {null, 1, 0, 3, -1, 2L, "2", true})
            expect(MultiSession.sessionLimit(v) == 1, "conservative 1 for " + v);

        // --- Calculator and Clock alive together as independent synthetic sessions.
        MultiSession.Slot[] none = slots(IDLE, IDLE);
        expect(MultiSession.start(none, CALC, null, 2).slot == 0, "legacy start goes to slot 0");
        MultiSession.Slot[] calc = slots(slot("s-calc", CALC, "ready"), IDLE);
        expect(MultiSession.start(calc, CLOCK, "alongside", 2).slot == 1, "alongside adds Clock in slot 1, never touching slot 0");
        expect(MultiSession.start(calc, CLOCK, null, 2).slot == 0, "legacy start still replaces slot 0's app (agreed legacy behaviour)");
        MultiSession.Slot[] both = slots(slot("s-calc", CALC, "ready"), slot("s-clock", CLOCK, "ready"));
        expect(MultiSession.live(both) == 2, "two live");

        // Alternating explicitly targeted commands and previews.
        for (int round = 0; round < 3; round++) {
            expect(MultiSession.target(both, "s-calc").slot == 0, "Calculator command/preview goes to Calculator's slot");
            expect(MultiSession.target(both, "s-clock").slot == 1, "Clock command/preview goes to Clock's slot");
        }
        expect(MultiSession.target(both, "s-gone").slot == 0, "an unknown id keeps slot 0's legacy refusal (Stale session ID)");
        expect(MultiSession.target(both, null).slot == 0, "legacy: no id is slot 0, whose own check refuses it");
        expect(MultiSession.state(both, "s-clock").slot == 1 && MultiSession.state(both, null).slot == 0, "get_state routing");
        expect(MultiSession.STALE_SESSION.equals(MultiSession.state(both, "s-gone").refusal), "get_state for an unknown id is stale");

        // Stopping one while the other continues; ambiguity refused, never guessed.
        expect(MultiSession.stop(both, "s-calc").slot == 0 && MultiSession.stop(both, "s-clock").slot == 1, "stop names exactly one");
        MultiSession.Route ambiguous = MultiSession.stop(both, null);
        expect(MultiSession.SESSION_REQUIRED.equals(ambiguous.refusal) && ambiguous.ownLive == 2, "untargeted stop with two live is refused");
        expect(MultiSession.STALE_SESSION.equals(MultiSession.stop(both, "s-gone").refusal), "a stale id stops nothing");
        MultiSession.Slot[] clockOnly = slots(slot("s-calc", CALC, "stopped"), slot("s-clock", CLOCK, "ready"));
        expect(MultiSession.stop(clockOnly, null).slot == 1, "untargeted stop with one live stops that one");
        expect(MultiSession.stop(none, null).slot == 0, "untargeted stop with none live is today's stop of slot 0");
        expect(MultiSession.target(clockOnly, "s-clock").slot == 1, "Clock continues after Calculator stopped");
        expect(MultiSession.ownerStop(both, "s-calc") == 0 && MultiSession.ownerStop(both, "s-clock") == 1, "owner Stop per notification");
        expect(MultiSession.ownerStop(both, null) == 0, "an older notification without an id is slot 0");
        expect(MultiSession.ownerStop(both, "s-gone") == -1, "an ended session's notification stops nothing");

        // Capability fallback and invalid modes.
        MultiSession.Route unavailable = MultiSession.start(calc, CLOCK, "alongside", 1);
        expect(MultiSession.MULTI_SESSION_UNAVAILABLE.equals(unavailable.refusal) && unavailable.ownLive == 1, "limit 1: alongside refused, Calculator untouched");
        expect(MultiSession.INVALID_MODE.equals(MultiSession.start(calc, CLOCK, "parallel", 2).refusal), "unknown mode refused");
        MultiSession.Route full = MultiSession.start(both, MAPS, "alongside", 2);
        expect(MultiSession.SESSION_LIMIT.equals(full.refusal) && full.ownLive == 2, "both slots occupied: session_limit, ownLive 2");

        // Duplicate app refusal: target or attested member of the other slot.
        expect(MultiSession.start(both, CALC, "alongside", 2).slot == 0, "alongside for the app a slot already targets returns that slot");
        MultiSession.Slot[] calcNavigated = slots(slot("s-calc", CALC, "ready", CALC, CLOCK), IDLE);
        expect(MultiSession.APP_IN_OTHER_SESSION.equals(MultiSession.start(calcNavigated, CLOCK, "alongside", 2).refusal),
               "Clock attested in Calculator's session: refused before any request");
        expect(MultiSession.APP_IN_OTHER_SESSION.equals(MultiSession.start(both, CLOCK, null, 2).refusal),
               "legacy start for the app held by slot 1 is refused, never moved");
        MultiSession.Slot[] dup = slots(slot("s-calc", CALC, "ready", CALC, CLOCK), slot("s-clock", CLOCK, "ready", CLOCK));
        expect(MultiSession.APP_IN_OTHER_SESSION.equals(MultiSession.openApp(dup, 1, CLOCK)), "open_app while the app is attested in the other slot");
        expect(MultiSession.openApp(both, 1, CLOCK) == null && MultiSession.openApp(both, 1, null) == null, "open_app of own target");
        expect("own_target_only".equals(MultiSession.openApp(both, 1, CALC)), "open_app confined to the slot's own app");

        // A cleanup_failed slot stays occupied: no reuse, no false free slot.
        MultiSession.Slot failed = new MultiSession.Slot("s-calc", CALC, "cleanup_failed", MultiSession.occupied(true, "cleanup_failed"), null);
        MultiSession.Slot[] failedAndClock = slots(failed, slot("s-clock", CLOCK, "ready"));
        expect(MultiSession.SESSION_LIMIT.equals(MultiSession.start(failedAndClock, MAPS, "alongside", 2).refusal), "failed close keeps its slot");
        for (String status : new String[] {"starting", "consent_required", "launching", "blocked"})
            expect(MultiSession.occupied(false, status), "occupied while " + status);
        for (String status : new String[] {"idle", "stopped", "closed", "failed", "rejected", "target_not_allowed"})
            expect(!MultiSession.occupied(false, status), "free after " + status);

        // --- Per-session attested membership (current_apps.sessions).
        HashSet<Integer> owned = new HashSet<>(Arrays.asList(11, 12));
        MultiSession.Membership[] members = {open(11, true, CALC, CONTROLLER), open(12, true, CLOCK)};
        List<MultiSession.Entry> e = MultiSession.sessions(both, members, owned, CONTROLLER);
        expect(e.size() == 2 && e.get(0).apps.equals(Collections.singletonList(CALC)) && e.get(0).complete, "Calculator's session, controller excluded");
        expect(e.get(1).apps.equals(Collections.singletonList(CLOCK)) && e.get(1).complete, "Clock's session");
        // Duplicate attested membership: incomplete in both, never a guessed owner.
        e = MultiSession.sessions(dup, new MultiSession.Membership[] {open(11, true, CALC, CLOCK), open(12, true, CLOCK)}, owned, CONTROLLER);
        expect(!e.get(0).complete && MultiSession.APP_IN_TWO_SESSIONS.equals(e.get(0).incompleteReason) && e.get(0).apps.equals(Collections.singletonList(CALC)), "dup: slot 0");
        expect(!e.get(1).complete && MultiSession.APP_IN_TWO_SESSIONS.equals(e.get(1).incompleteReason) && e.get(1).apps.isEmpty(), "dup: slot 1");
        // Other-user activity omitted: incomplete.
        e = MultiSession.sessions(both, new MultiSession.Membership[] {open(11, false, CALC), open(12, true, CLOCK)}, owned, CONTROLLER);
        expect(!e.get(0).complete && CurrentApps.OTHER_USER_OMITTED.equals(e.get(0).incompleteReason), "other user omitted");
        // Missing keys, no snapshot, or an unattested device: never complete.
        e = MultiSession.sessions(both, new MultiSession.Membership[] {new MultiSession.Membership(true, false, true, null, Arrays.asList(CALC)), open(12, true, CLOCK)}, owned, CONTROLLER);
        expect(!e.get(0).complete && MultiSession.DEVICE_UNATTESTED.equals(e.get(0).incompleteReason) && e.get(0).apps.isEmpty(), "no deviceId: no join");
        e = MultiSession.sessions(both, members, null, CONTROLLER);
        expect(!e.get(0).complete && !e.get(1).complete, "no owned snapshot: nothing complete");
        e = MultiSession.sessions(both, new MultiSession.Membership[] {open(99, true, CALC), open(12, true, CLOCK)}, owned, CONTROLLER);
        expect(!e.get(0).complete && e.get(0).apps.isEmpty(), "a device the snapshot does not list is not attested");
        e = MultiSession.sessions(both, new MultiSession.Membership[] {MultiSession.Membership.UNKNOWN, open(12, true, CLOCK)}, owned, CONTROLLER);
        expect(!e.get(0).complete && MultiSession.MEMBERSHIP_UNAVAILABLE.equals(e.get(0).incompleteReason), "unreadable (missing service, SecurityException)");
        // Closed ids are historical correlation only.
        MultiSession.Membership closed = new MultiSession.Membership(true, true, true, 11, null);
        e = MultiSession.sessions(both, new MultiSession.Membership[] {closed, open(12, true, CLOCK)}, owned, CONTROLLER);
        expect(e.get(0).apps.isEmpty() && !e.get(0).complete && MultiSession.SESSION_CLOSED.equals(e.get(0).incompleteReason), "closed but device still listed: lingering");
        e = MultiSession.sessions(both, new MultiSession.Membership[] {closed, open(12, true, CLOCK)}, new HashSet<>(Collections.singletonList(12)), CONTROLLER);
        expect(e.get(0).apps.isEmpty() && e.get(0).complete, "closed and device gone: genuinely empty");
        expect(e.get(1).complete && e.get(1).apps.equals(Collections.singletonList(CLOCK)), "Clock unaffected by Calculator's close");
        expect(MultiSession.deviceId(11) == 11 && MultiSession.deviceId(11L) == null && MultiSession.deviceId(null) == null, "deviceId type");
        expect(MultiSession.sessions(none, new MultiSession.Membership[] {MultiSession.Membership.UNKNOWN, MultiSession.Membership.UNKNOWN}, owned, CONTROLLER).isEmpty(), "no slots, no entries");

        System.out.println("MultiSessionTest: " + checks + " checks passed");
    }
}
