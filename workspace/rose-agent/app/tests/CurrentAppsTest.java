package dev.pixel.voice;

import dev.pixel.voice.CurrentApps.*;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** #225/#156: the overview shows exactly the apps currently running under agent control, from complete authoritative
 * snapshots (never task history). Synthetic snapshots on the host JVM: launch, content change, close, control release,
 * reconnect/recreation, stale and malformed reports, and the interim controller adapter. */
public final class CurrentAppsTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static App app(String id, String pkg, String state) { return new App(id, pkg, state, null); }
    static Snapshot snap(String reg, long gen, App... apps) { return new Snapshot(reg, gen, true, "", Arrays.asList(apps)); }
    static List<String> ids(Tracker t) { List<String> o = new ArrayList<>(); for (App a : t.apps()) o.add(a.entryId); return o; }

    public static void main(String[] args) {
        Tracker t = new Tracker();
        expect(t.state() == State.UNAVAILABLE && t.reason().equals(CurrentApps.NOT_REPORTED), "nothing reported yet: unavailable, never 'none'");

        // Launch adds; content change updates in place; close removes.
        expect(t.apply(snap("r1", 1)) && t.state() == State.EMPTY, "first (empty) snapshot: none running");
        expect(t.apply(snap("r1", 2, app("s1/calc", "com.android.calculator2", CurrentApps.STATE_STARTING))), "launch");
        expect(t.state() == State.AVAILABLE && ids(t).equals(Arrays.asList("s1/calc")), "launch adds the app");
        expect(t.apply(snap("r1", 3, app("s1/calc", "com.android.calculator2", CurrentApps.STATE_RUNNING))), "content change");
        expect(t.apps().get(0).state.equals(CurrentApps.STATE_RUNNING), "state updated");
        expect(!t.apply(snap("r1", 4, app("s1/calc", "com.android.calculator2", CurrentApps.STATE_RUNNING))), "identical content: nothing changes");
        expect(t.apply(snap("r1", 5, app("s1/calc", "com.android.calculator2", CurrentApps.STATE_RUNNING), app("s2/maps", "com.google.android.apps.maps", CurrentApps.STATE_RUNNING))), "second app");
        expect(ids(t).equals(Arrays.asList("s1/calc", "s2/maps")), "both, in report order");
        expect(t.apply(snap("r1", 6, app("s2/maps", "com.google.android.apps.maps", CurrentApps.STATE_RUNNING))), "close one");
        expect(ids(t).equals(Arrays.asList("s2/maps")), "close removes exactly that app");
        expect(t.apply(snap("r1", 7)) && t.state() == State.EMPTY && t.apps().isEmpty(), "control released: none");

        // Stale/out-of-order generations never resurrect.
        expect(!t.apply(snap("r1", 6, app("s2/maps", "com.google.android.apps.maps", CurrentApps.STATE_RUNNING))), "an older report is ignored");
        expect(t.apps().isEmpty(), "...so a closed app stays closed");
        expect(!t.apply(snap("r1", 7, app("s9/x", "com.example", CurrentApps.STATE_RUNNING))), "an equal generation is ignored");

        // Reconnect / recreation: a new registration drops everything first.
        Tracker r = new Tracker();
        r.apply(snap("old", 10, app("s1/calc", "com.android.calculator2", CurrentApps.STATE_RUNNING)));
        r.apply(new Snapshot("new", 1, false, CurrentApps.NOT_REPORTED, Collections.<App>emptyList()));
        expect(r.apps().isEmpty() && r.state() == State.UNAVAILABLE, "reconnect: nothing from before is shown until the new source reports");
        expect(r.apply(snap("new", 2)) && r.state() == State.EMPTY, "the new registration's next report applies");
        expect(!r.apply(snap("old", 11, app("s1/calc", "com.android.calculator2", CurrentApps.STATE_RUNNING))) && r.apps().isEmpty(), "a late report from the replaced registration never resurrects its app");
        Tracker g = new Tracker();
        g.apply(snap("x", 1, app("a", "com.a", CurrentApps.STATE_RUNNING)));
        g.apply(snap("y", 0));
        expect(g.state() == State.EMPTY, "a new registration's first report applies at any generation, even lower");
        g.reset();
        expect(!g.apply(snap("y", 5, app("a", "com.a", CurrentApps.STATE_RUNNING))) && g.apps().isEmpty(), "after teardown the old registration stays retired");
        Tracker q = new Tracker();
        q.apply(snap("a", 5, app("s1/calc", "com.android.calculator2", CurrentApps.STATE_RUNNING)));
        q.apply(snap("b", 1));
        expect(q.apps().isEmpty(), "a recreated source that reports none: none, never the old app");
        q.reset();
        expect(q.state() == State.UNAVAILABLE && q.apps().isEmpty(), "teardown forgets everything");

        // Unavailable and malformed.
        Tracker u = new Tracker();
        u.apply(snap("r", 1, app("s1/calc", "com.android.calculator2", CurrentApps.STATE_RUNNING)));
        u.apply(Snapshot.unavailable("r", 2, CurrentApps.LOCKED));
        expect(u.apps().isEmpty() && u.reason().equals(CurrentApps.LOCKED), "locked: no apps shown, the reason instead");
        Snapshot[] bad = {
            new Snapshot("r", 3, true, "", null),
            snap("r", 4, app("s1", "p", CurrentApps.STATE_RUNNING), app("s1", "q", CurrentApps.STATE_RUNNING)),
            snap("r", 5, app("", "p", CurrentApps.STATE_RUNNING)),
            snap("r", 6, app("s1", "", CurrentApps.STATE_RUNNING)),
            snap("r", 7, app("s1", "p", "done")),
            snap("r", 8, new App("s1", "p", CurrentApps.STATE_RUNNING, new Preview("s1", ""))),
            new Snapshot("r", 9, false, "", Arrays.asList(app("s1", "p", CurrentApps.STATE_RUNNING))),
            snap("", 10, app("s1", "p", CurrentApps.STATE_RUNNING)),
        };
        for (Snapshot b : bad) {
            Tracker m = new Tracker();
            m.apply(snap(b.registration.isEmpty() ? "x" : b.registration, 1, app("ok", "com.ok", CurrentApps.STATE_RUNNING)));
            m.apply(b);
            expect(m.apps().isEmpty() && m.state() == State.UNAVAILABLE, "malformed report gen " + b.generation + ": never partly applied, nothing shown");
        }
        expect(CurrentApps.text(CurrentApps.LOCKED).contains("Unlock") && !CurrentApps.text("anything").isEmpty(), "fixed words for every reason");

        // Interim adapter: no session is authoritatively empty; a live session is not attested complete.
        WorkspaceOverview.Scope s = new WorkspaceOverview.Scope("srv", "c1");
        WorkspaceOverview.Binding verified = new WorkspaceOverview.Binding("rose-ws-1-calc", "s-1", "com.android.calculator2", s);
        for (String st : new String[]{"ready", "launching", "starting", "scheduled", "blocked", "cleanup_failed"}) {
            Snapshot live = CurrentApps.fromController("ctl", 1, null, st, "s-1", "rose-ws-1-calc", "com.android.calculator2", verified);
            expect(!live.available && live.reason.equals(CurrentApps.INCOMPLETE) && live.apps.isEmpty(), st + ": target-only membership is never shown as every app");
        }
        for (String st : new String[]{"idle", "stopped", "closed", "user_stopped", "failed", "rejected", "target_not_allowed", "consent_required"}) {
            Snapshot none = CurrentApps.fromController("ctl", 1, null, st, "", "", "", null);
            expect(none.available && none.apps.isEmpty(), st + ": no session display, nothing under control");
        }
        expect(!CurrentApps.fromController("ctl", 1, "denied", null, null, null, null, null).available, "read failed: unavailable, never 'none'");
        expect(CurrentApps.fromController("ctl", 1, CurrentApps.NOT_INSTALLED, null, null, null, null, null).reason.equals(CurrentApps.NOT_INSTALLED), "failure word kept");
        expect(CurrentApps.fromController("ctl", 1, null, "weird", "s", "t", "p", null).reason.equals(CurrentApps.UNREADABLE), "unknown status: unavailable");
        expect(CurrentApps.fromController("ctl", 1, null, null, null, null, null, null).reason.equals(CurrentApps.UNREADABLE), "no status: unavailable");
        Tracker life = new Tracker();
        life.apply(CurrentApps.fromController("ctl", 1, null, "ready", "s-1", "t", "com.android.calculator2", null));
        expect(life.state() == State.UNAVAILABLE && life.reason().equals(CurrentApps.INCOMPLETE), "launch through the controller: can't confirm, not a partial list");
        life.apply(CurrentApps.fromController("ctl", 2, null, "stopped", null, null, null, null));
        expect(life.apps().isEmpty() && life.state() == State.EMPTY, "released through the controller: none");
        // #410 expiry (Background Work snapshot retention) and the reopen regression: EXPIRED hides what is no longer
        // current without retiring the controller's registration; its next current report is shown again.
        {
            java.util.List<CurrentApps.ReplyApp> ra = java.util.Arrays.asList(new CurrentApps.ReplyApp("com.android.calculator2", true, true, "s-0"));
            java.util.List<CurrentApps.Binding> rb = java.util.Arrays.asList(new CurrentApps.Binding(0, "s-0", "t-0", "com.android.calculator2", "ready", true, true));
            Tracker e = new Tracker();
            expect(e.apply(CurrentApps.fromBindings("r", "live", 5L, ra, rb, "E1", true)) && e.state() == CurrentApps.State.AVAILABLE, "a current report shows its card");
            expect(e.apps().get(0).authoritativelyRunning(), "its session is ready: the edge may light");
            expect(e.apply(CurrentApps.EXPIRED) && e.state() == CurrentApps.State.UNAVAILABLE && e.apps().isEmpty(), "expired: no cards (no running edge), the checking state");
            expect(e.reason().equals(CurrentApps.NOT_REPORTED) && CurrentApps.text(e.reason()).contains("checking"), "it says Rose is checking");
            expect(!e.apply(CurrentApps.EXPIRED), "expiring again changes nothing");
            expect(e.registration().equals("controller:E1"), "the controller's registration is kept, never retired");
            expect(e.apply(CurrentApps.fromBindings("r", "live", 5L, ra, rb, "E1", true)) && e.state() == CurrentApps.State.AVAILABLE, "its next current report (same generation) is shown again");
            e.apply(CurrentApps.EXPIRED);
            expect(!e.apply(CurrentApps.fromBindings("r", "live", 4L, ra, rb, "E1", true)) && e.apps().isEmpty(), "an older report never comes back after expiry");
            expect(e.apply(CurrentApps.fromBindings("r", "live", 6L, ra, rb, "E1", true)) && e.apps().size() == 1, "a newer one does");
            expect(!e.apply(CurrentApps.fromBindings("r", "live", 6L, ra, rb, "E1", true)), "and then the normal generation order holds again");
            e.apply(CurrentApps.EXPIRED);
            expect(e.apply(CurrentApps.fromBindings("r", "live", 1L, ra, rb, "E2", true)) && e.registration().equals("controller:E2"), "a restarted controller (new epoch) is a new registration");
            expect(!e.apply(CurrentApps.fromBindings("r", "live", 9L, ra, rb, "E1", true)), "and the replaced one stays retired");
            // The old reopen path (a synthetic "pending" registration) retired the controller for good; EXPIRED does not.
            Tracker old = new Tracker();
            old.apply(CurrentApps.fromBindings("r", "live", 5L, ra, rb, "E1", true));
            old.apply(CurrentApps.Snapshot.unavailable("pending", 0, CurrentApps.NOT_REPORTED));
            expect(!old.apply(CurrentApps.fromBindings("r", "live", 6L, ra, rb, "E1", true)), "(documents the defect: a synthetic registration retires the real one)");
            Tracker fresh = new Tracker();
            expect(!fresh.apply(CurrentApps.EXPIRED) || fresh.state() == CurrentApps.State.UNAVAILABLE, "expired before anything: still the checking state");
            expect(fresh.apply(CurrentApps.fromBindings("r", "live", 1L, ra, rb, "E1", true)) && fresh.apps().size() == 1, "then the first report shows");
            // current(): read since the last resume and within MAX_AGE_MS.
            long resumed = 1000, max = CurrentApps.MAX_AGE_MS;
            expect(CurrentApps.current(1000, resumed, 1000) && CurrentApps.current(1200, resumed, 1200 + max), "a read since resume, up to the max age");
            expect(!CurrentApps.current(999, resumed, 1000), "a read from before the pause/lock/user switch is not current");
            expect(!CurrentApps.current(1200, resumed, 1201 + max), "an old read (a stalled call) is not current");
            expect(!CurrentApps.current(-1, 0, 0), "an unstamped read is not current");
        }
        System.out.println("CurrentAppsTest: " + checks + " checks passed");
    }
}
