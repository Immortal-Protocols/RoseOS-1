package dev.pixel.backgroundcontrol;

public final class WatchBindingTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static WatchBinding watch(String task, String session) { return WatchBinding.fromExtras(true, task, true, session); }
    static final String S1 = "6f1c2c1e-3a51-4f0e-9d7a-0b9f8a1d2e31", S2 = "0c5b6a77-1f2e-4d3c-8b9a-7e6d5c4b3a21";

    public static void main(String[] args) {
        expect("dev.pixel.backgroundcontrol.extra.WATCH_TASK_ID".equals(WatchBinding.EXTRA_TASK)
                && "dev.pixel.backgroundcontrol.extra.WATCH_SESSION_ID".equals(WatchBinding.EXTRA_SESSION),
                "Exact extra names agreed with Rose Agent's producer (APP-OS-WATCH-002)");

        // Entry decisions.
        WatchBinding legacy = WatchBinding.fromExtras(false, null, false, null);
        expect(legacy.mode == WatchBinding.Mode.UNBOUND, "No extras is the legacy unbound entry");
        expect(legacy.allows(S1, "task-a") && legacy.allows(S2, null), "Legacy entry shows the current session as before");
        WatchBinding a = watch("task-a", S1);
        expect(a.mode == WatchBinding.Mode.BOUND && "task-a".equals(a.task) && S1.equals(a.session), "Both valid is a bound Watch");
        expect(a.allows(S1, "task-a"), "Normal bound Watch shows its own task and session");
        for (WatchBinding bad : new WatchBinding[]{
                WatchBinding.fromExtras(true, "task-a", false, null), WatchBinding.fromExtras(false, null, true, S1),
                watch("", S1), watch("task-a", ""), watch(null, S1), watch("task-a", null),
                watch("task a", S1), watch("task-a", "../" + S1), watch(new String(new char[129]).replace('\0', 'x'), S1)})
            expect(bad.mode == WatchBinding.Mode.INVALID && !bad.allows(S1, "task-a"),
                    "A Watch with a missing, empty or invalid identity never falls back to unbound viewing");

        // The REVIEW005/V1 retargets: the old viewer attached whatever was current.
        expect(!a.allows(S2, "task-b"), "Task A selected, controller now on task B in another session: refused");
        expect(!a.allows(S1, "task-b"), "Same session re-tagged from task A to task B: refused");
        expect(!a.allows(S2, "task-a"), "Same task id in a replacement session: refused");
        expect(!a.allows(null, null) && !a.allows(S1, null), "Ended or untagged session: refused");
        expect(!a.allows(S1, "TASK-A") && !a.allows(S1 + " ", "task-a"), "Identities are exact strings");

        // New intents.
        expect(WatchBinding.next(a, legacy) == a, "An incidental unbound reopen keeps the selected binding");
        WatchBinding b = watch("task-b", S2);
        expect(WatchBinding.next(a, b).equals(b) && WatchBinding.next(a, b).allows(S2, "task-b") && !WatchBinding.next(a, b).allows(S1, "task-a"),
                "A fresh valid Watch replaces the pair");
        expect(WatchBinding.next(a, watch("task-b", "")).mode == WatchBinding.Mode.INVALID, "An invalid new Watch is not ignored as unbound");
        WatchBinding stale = WatchBinding.next(b, a);
        expect(!stale.allows(S2, "task-b"), "A stale Watch for a superseded pair cannot attach the current session");
        expect(WatchBinding.next(legacy, legacy) == legacy, "Unbound stays unbound");

        // #156 M004: an overview selection request (Android display + package), resolved by the controller.
        final String[] live = {"ready", S1, "task-a", "com.example.mail"};
        final int[] display = {7};
        WatchBinding.Live controller = (d, p) -> WatchBinding.fromSelection(d, p, live[1] != null, live[0], display[0],
                live[3], live[1], live[2]);
        WatchBinding sel = WatchBinding.fromEntry(false, null, false, null, true, 7, true, "com.example.mail", controller);
        expect(sel.mode == WatchBinding.Mode.BOUND && sel.task.equals("task-a") && sel.session.equals(S1),
                "A valid selection binds the controller's OWN live pair, never a caller id");
        expect(sel.allows(S1, "task-a") && !sel.allows(S2, "task-a"), "The resolved pair is then a normal Watch");
        expect(WatchBinding.fromEntry(false, null, false, null, true, 8, true, "com.example.mail", controller).mode == WatchBinding.Mode.INVALID,
                "Another display (a replaced session) is refused, never generic viewing");
        expect(WatchBinding.fromEntry(false, null, false, null, true, 7, true, "com.example.chat", controller).mode == WatchBinding.Mode.INVALID,
                "Another package on the agent display is refused");
        // Malformed and partial requests: never unbound.
        Object[][] bad = {{true, 7, false, null}, {false, null, true, "com.example.mail"}, {true, "7", true, "com.example.mail"},
                {true, 7, true, 5}, {true, 0, true, "com.example.mail"}, {true, -3, true, "com.example.mail"},
                {true, 7, true, "not a package"}, {true, 7, true, ""}, {true, null, true, null}};
        for (Object[] r : bad) {
            expect(WatchBinding.fromEntry(false, null, false, null, (Boolean) r[0], r[1], (Boolean) r[2], r[3], controller).mode
                    == WatchBinding.Mode.INVALID, "Malformed or partial selection is invalid: " + java.util.Arrays.toString(r));
        }
        expect(WatchBinding.fromEntry(true, "task-a", true, S1, true, 7, true, "com.example.mail", controller).mode
                == WatchBinding.Mode.INVALID, "Mixing a Watch and a selection is ambiguous and refused");
        expect(WatchBinding.fromEntry(false, null, false, null, true, 7, true, "com.example.mail", (d, p) -> null).mode
                == WatchBinding.Mode.INVALID, "An unresolvable selection is refused");
        expect(WatchBinding.fromEntry(false, null, false, null, true, 7, true, "com.example.mail", (d, p) -> WatchBinding.UNBOUND).mode
                == WatchBinding.Mode.INVALID, "A resolver can never turn a selection into unbound viewing");
        expect(WatchBinding.fromEntry(true, "task-a", true, S1, false, null, false, null, controller).equals(a),
                "Existing Watch entries are unchanged");
        expect(WatchBinding.fromEntry(false, null, false, null, false, null, false, null, controller) == WatchBinding.UNBOUND,
                "The legacy unbound entry is unchanged");
        // Controller state at resolution time decides (races after the overview's check).
        live[0] = "starting";
        expect(WatchBinding.fromEntry(false, null, false, null, true, 7, true, "com.example.mail", controller).mode == WatchBinding.Mode.INVALID,
                "A session that is not ready is refused");
        live[0] = "ready"; live[2] = null;
        expect(WatchBinding.fromEntry(false, null, false, null, true, 7, true, "com.example.mail", controller).mode == WatchBinding.Mode.INVALID,
                "A legacy session with no opaque task id is unverifiable and refused");
        live[2] = "task-a"; live[1] = null;
        expect(WatchBinding.fromEntry(false, null, false, null, true, 7, true, "com.example.mail", controller).mode == WatchBinding.Mode.INVALID,
                "No live session: refused");
        live[1] = S1; display[0] = -1;
        expect(WatchBinding.fromEntry(false, null, false, null, true, 7, true, "com.example.mail", controller).mode == WatchBinding.Mode.INVALID,
                "The session's own display could not be read: refused");
        display[0] = 7;
        // An already-bound viewer receiving another selection: the new resolution replaces it, valid or not.
        WatchBinding second = WatchBinding.next(b, sel);
        expect(second.equals(sel) && !second.allows(S2, "task-b"), "A new selection replaces an earlier Watch");
        live[2] = "task-c";
        WatchBinding retagged = WatchBinding.fromEntry(false, null, false, null, true, 7, true, "com.example.mail", controller);
        expect(!sel.allows(S1, "task-c") && retagged.allows(S1, "task-c"),
                "Same-session task replacement after binding closes the old view; a new selection binds the new task");
        expect(WatchBinding.next(sel, WatchBinding.fromEntry(false, null, false, null, true, 8, true, "com.example.mail", controller)).mode
                == WatchBinding.Mode.INVALID, "A refused second selection makes the viewer unavailable, not the old pair");

        // Recreation restores the same pair by value.
        expect(watch(a.task, a.session).equals(a) && !watch(a.task, a.session).equals(b), "Restored pair equals the saved pair");
        System.out.println("WatchBindingTest: " + checks + " checks passed");
    }
}
