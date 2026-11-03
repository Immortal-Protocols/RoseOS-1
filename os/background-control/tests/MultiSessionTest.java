package dev.pixel.backgroundcontrol;

import java.util.Arrays;
import java.util.Collections;
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
    static MultiSession.Bind bind(int slot, String id, String target, String status, Integer device, boolean closed, String front) {
        return new MultiSession.Bind(slot, id, "t-42", target, status, device, closed, front != null, front);
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

        // Viewer: bound shows only its own session; unbound never guesses between two.
        expect(MultiSession.viewer(both, true, "s-clock") == 1 && MultiSession.viewer(both, true, "s-gone") == -1, "bound viewer");
        expect(MultiSession.viewer(both, false, null) == -1, "unbound with two live shows nothing");
        expect(MultiSession.viewer(calc, false, null) == 0 && MultiSession.viewer(none, false, null) == 0, "unbound with at most slot 0 is slot 0 (legacy)");
        expect(MultiSession.viewer(clockOnly, true, "s-calc") == 0, "a bound viewer of a stopped session still finds its slot (then finishes)");

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

        // --- Bindings (current_apps.sessions[], apps[].sessionId/front/target): never membership or completeness.
        CurrentApps.Owned devices = CurrentApps.owned(30L, new int[] {7, 9}, new boolean[] {true, true}, new int[] {7, 9, 9}, new String[] {CALC, CLOCK, CONTROLLER});
        MultiSession.Bound b = MultiSession.bind(Arrays.asList(bind(0, "s-calc", CALC, "ready", 7, false, CALC), bind(1, "s-clock", CLOCK, "ready", 9, false, CLOCK)), devices);
        expect(b.bound.equals(Arrays.asList(true, true)) && b.targets.equals(Arrays.asList(CALC, CLOCK)) && b.fronts.equals(Arrays.asList(CALC, CLOCK)), "each session bound to its own device");
        expect("s-calc".equals(b.sessionOf(CALC)) && "s-clock".equals(b.sessionOf(CLOCK)), "each app maps to its own session");
        // Alternating targeted commands and previews go by these bindings; stopping Calculator leaves Clock bound.
        b = MultiSession.bind(Arrays.asList(bind(1, "s-clock", CLOCK, "ready", 9, false, CLOCK)), devices);
        expect(b.bound.equals(Collections.singletonList(true)) && b.sessionOf(CALC) == null && "s-clock".equals(b.sessionOf(CLOCK)),
               "Calculator's lingering device is shown unbound (no preview, Stop or Watch); Clock unchanged");
        // A front or target not listed on the session's own device is never flagged.
        b = MultiSession.bind(Arrays.asList(bind(0, "s-calc", CALC, "ready", 7, false, CLOCK)), devices);
        expect(b.fronts.isEmpty() && b.targets.equals(Collections.singletonList(CALC)), "front only from the session's own device");
        // Front only when known (ready, unblocked, unlocked, read): one per session.
        b = MultiSession.bind(Arrays.asList(bind(0, "s-calc", CALC, "launching", 7, false, null)), devices);
        expect(b.fronts.isEmpty() && b.targets.equals(Collections.singletonList(CALC)) && b.bound.get(0), "launching: bound, target flagged (live, as before), no front");
        expect(!new MultiSession.Bind(0, "s", "t", CALC, "ready", 7, false, false, CALC).frontKnown
               && new MultiSession.Bind(0, "s", "t", CALC, "ready", 7, false, false, CALC).front == null, "an unknown front is never kept");
        // Closed ids are historical: never bound. A cleanup_failed slot with a closed answer is unbound.
        b = MultiSession.bind(Arrays.asList(bind(0, "s-calc", CALC, "cleanup_failed", 7, true, null), bind(1, "s-clock", CLOCK, "ready", 9, false, CLOCK)), devices);
        expect(b.bound.equals(Arrays.asList(false, true)) && b.sessionOf(CALC) == null, "closed answer: historical id, unbound");
        // Unattested ids: a device the snapshot does not list, or missing keys with two slots, bind nothing.
        b = MultiSession.bind(Arrays.asList(bind(0, "s-calc", CALC, "ready", 99, false, CALC)), devices);
        expect(!b.bound.get(0) && b.fronts.isEmpty() && b.sessionOf(CALC) == null, "an unlisted device id binds nothing");
        b = MultiSession.bind(Arrays.asList(bind(0, "s-calc", CALC, "ready", null, false, CALC), bind(1, "s-clock", CLOCK, "ready", null, false, CLOCK)), devices);
        expect(b.bound.equals(Arrays.asList(false, false)), "no ids with two slots: nothing guessed");
        // Older build without the keys: the one live slot of a single-slot controller binds the only device, as before.
        CurrentApps.Owned oneDevice = CurrentApps.owned(31L, new int[] {7}, new boolean[] {true}, new int[] {7}, new String[] {CALC});
        b = MultiSession.bind(Arrays.asList(bind(0, "s-calc", CALC, "ready", null, false, CALC)), oneDevice);
        expect(b.bound.get(0) && b.targets.equals(Collections.singletonList(CALC)) && "s-calc".equals(b.sessionOf(CALC)), "legacy single binding");
        b = MultiSession.bind(Arrays.asList(bind(0, "s-calc", CALC, "cleanup_failed", null, false, null)), oneDevice);
        expect(!b.bound.get(0) && b.targets.isEmpty() && b.sessionOf(CALC) == null, "legacy: without ids a cleanup_failed slot is not live, so it is not guessed onto the device");
        b = MultiSession.bind(Arrays.asList(bind(0, "s-calc", CALC, "stopping", null, false, null)), oneDevice);
        expect(!b.bound.get(0), "legacy: a slot that is not live does not take the device");
        // Two slots naming one device: neither is bound; a package on two bound devices maps to no session.
        b = MultiSession.bind(Arrays.asList(bind(0, "s-a", CALC, "ready", 7, false, CALC), bind(1, "s-b", CLOCK, "ready", 7, false, CLOCK)), devices);
        expect(b.bound.equals(Arrays.asList(false, false)), "one device claimed twice: unbound");
        CurrentApps.Owned shared = CurrentApps.owned(32L, new int[] {7, 9}, new boolean[] {true, true}, new int[] {7, 7, 9}, new String[] {CALC, MAPS, MAPS});
        b = MultiSession.bind(Arrays.asList(bind(0, "s-a", CALC, "ready", 7, false, CALC), bind(1, "s-b", MAPS, "ready", 9, false, MAPS)), shared);
        expect(b.sessionOf(MAPS) == null && "s-a".equals(b.sessionOf(CALC)), "duplicate package: no session for it");
        // No snapshot (locked or unavailable): nothing bound.
        b = MultiSession.bind(Arrays.asList(bind(0, "s-calc", CALC, "ready", 7, false, CALC)), null);
        expect(!b.bound.get(0) && b.fronts.isEmpty() && b.targets.isEmpty(), "no snapshot: no bindings");
        expect(MultiSession.deviceId(11) == 11 && MultiSession.deviceId(11L) == null && MultiSession.deviceId(null) == null, "deviceId type");

        // #410 viewer Stop (Nathan-approved): only the viewer's own bound pair, rechecked when the owner confirms.
        {
            MultiSession.Slot[] two = slots(slot("s-calc", CALC, "ready"), slot("s-clock", CLOCK, "blocked"));
            MultiSession.Route own = MultiSession.viewerStop(two, true, "s-calc", "t-1", "t-1", true, false);
            expect(!own.refused() && own.slot == 0, "the bound, unlocked, current pair stops its own slot only");
            expect(MultiSession.viewerStop(two, true, "s-clock", "t-2", "t-2", true, false).slot == 1,
                "an already-bound blocked session can be stopped, as owner_stop allows (nothing is shown)");
            MultiSession.Slot holding = new MultiSession.Slot("s-calc", CALC, "cleanup_failed", MultiSession.occupied(true, "cleanup_failed"), null);
            expect(MultiSession.viewerStop(slots(holding, IDLE), true, "s-calc", "t-1", "t-1", true, false).slot == 0,
                "cleanup_failed still holds the session: Stop retries it, like the owner's other Stops");
            String[][] refused = {
                {MultiSession.VIEWER_UNBOUND, "unbound"}, {MultiSession.VIEWER_LOCKED, "locked"},
                {MultiSession.VIEWER_REPLACED, "replaced id"}, {MultiSession.VIEWER_ENDED, "ended"},
                {MultiSession.VIEWER_RETAGGED, "retagged"}, {MultiSession.VIEWER_REPLACED, "other object"}};
            MultiSession.Slot[] ended = slots(slot("s-calc", CALC, "stopped"), slot("s-clock", CLOCK, "ready"));
            MultiSession.Route[] routes = {
                MultiSession.viewerStop(two, false, "s-calc", "t-1", "t-1", true, false),
                MultiSession.viewerStop(two, true, "s-calc", "t-1", "t-1", true, true),
                MultiSession.viewerStop(two, true, "s-old", "t-1", null, false, false),
                MultiSession.viewerStop(ended, true, "s-calc", "t-1", "t-1", true, false),
                MultiSession.viewerStop(two, true, "s-calc", "t-1", "t-9", true, false),
                MultiSession.viewerStop(two, true, "s-calc", "t-1", "t-1", false, false)};
            for (int i = 0; i < routes.length; i++)
                expect(routes[i].refused() && refused[i][0].equals(routes[i].refusal) && routes[i].slot == -1,
                    "fails closed and stops nothing: " + refused[i][1]);
            for (String status : new String[] {"idle", "stopped", "closed", "failed", "rejected", "target_not_allowed"})
                expect(MultiSession.VIEWER_ENDED.equals(MultiSession.viewerStop(slots(slot("s-calc", CALC, status), IDLE),
                    true, "s-calc", "t-1", "t-1", true, false).refusal), "an ended slot that keeps its id stops nothing: " + status);
            expect(MultiSession.VIEWER_UNBOUND.equals(MultiSession.viewerStop(two, true, null, "t-1", "t-1", true, false).refusal)
                && MultiSession.VIEWER_UNBOUND.equals(MultiSession.viewerStop(two, true, "s-calc", null, null, true, false).refusal),
                "a pair without a session or task id is unbound");
            expect(MultiSession.VIEWER_LOCKED.equals(MultiSession.viewerStop(ended, true, "s-calc", "t-1", "t-1", true, true).refusal),
                "while locked nothing about the session is decided or stopped");
        }
        // The viewer's wording, exactly as agreed with Agent Product/Home (#410), and what each reply does.
        expect(ViewerStop.BUTTON.equals("Stop") && ViewerStop.KEEP.equals("Keep working"), "the Background Work sheet's pair");
        expect(ViewerStop.title("Clock").equals("Stop Rose\u2019s work in Clock?")
            && ViewerStop.description("Clock").equals("Stop Rose\u2019s work in Clock"), "title and accessibility name");
        expect(ViewerStop.MESSAGE.equals("This stops only this session. Rose\u2019s other work keeps running. Closing this view doesn\u2019t stop anything."),
            "the agreed message: closing never stops");
        Object[][] outcomes = {
            {"stopped", true, "Stopped. Rose\u2019s other work keeps running.", true, false},
            {"stopped", false, "Rose couldn\u2019t confirm the stop. It may still be running.", false, true},
            {"cleanup_failed", false, "Android couldn\u2019t close this session yet. It may still be running.", false, true},
            {MultiSession.VIEWER_LOCKED, false, "Unlock to stop it.", false, true},
            {MultiSession.VIEWER_ENDED, false, "This work already ended. Nothing was stopped.", true, false},
            {MultiSession.VIEWER_RETAGGED, false, "Rose moved this session to another task. Nothing was stopped.", true, false},
            {MultiSession.VIEWER_REPLACED, false, "This view no longer matches Rose\u2019s work. Nothing was stopped.", true, false},
            {MultiSession.VIEWER_UNBOUND, false, "This view no longer matches Rose\u2019s work. Nothing was stopped.", true, false},
            // An error or unknown reply may have stopped part of the work: never "Stopped", never "Nothing was stopped".
            {"error", false, "Rose couldn\u2019t confirm the stop. It may still be running.", false, true},
            {"cleanup_failed", true, "Android couldn\u2019t close this session yet. It may still be running.", false, true}};
        for (Object[] o : outcomes) {
            ViewerStop.Outcome r = ViewerStop.of((String) o[0], (Boolean) o[1]);
            expect(r.text.equals(o[2]) && r.closeViewer == (Boolean) o[3] && r.stopAgain == (Boolean) o[4], "outcome " + o[0] + "/" + o[1]);
        }
        System.out.println("MultiSessionTest: " + checks + " checks passed");
    }
}
