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

        // Recreation restores the same pair by value.
        expect(watch(a.task, a.session).equals(a) && !watch(a.task, a.session).equals(b), "Restored pair equals the saved pair");
        System.out.println("WatchBindingTest: " + checks + " checks passed");
    }
}
