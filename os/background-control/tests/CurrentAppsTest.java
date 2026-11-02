package dev.pixel.backgroundcontrol;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

public final class CurrentAppsTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final String CONTROLLER = "dev.pixel.backgroundcontrol", MAPS = "com.google.android.apps.maps",
        INSTALLER = "com.google.android.packageinstaller", AMAZON = "com.amazon.mShop.android.shopping";

    /** A consumer as the contract asks: replace the whole set when the generation changes, keep nothing else. */
    static final class Consumer {
        long generation; String signature = ""; List<String> shown = Collections.emptyList();
        boolean read(CurrentApps.Result r, String sessionId) {
            String s = CurrentApps.signature(r, sessionId);
            if (s.equals(signature)) return false;
            signature = s; generation++;
            String[] names = new String[r.apps.size()];
            for (int i = 0; i < names.length; i++) names[i] = r.apps.get(i).packageName;
            shown = Arrays.asList(names);
            return true;
        }
    }
    static CurrentApps.Result read(boolean locked, boolean session, String status, String target, String... front) {
        return CurrentApps.of(locked, session, status, "blocked".equals(status), target, Arrays.asList(front), CONTROLLER);
    }
    static CurrentApps.Result unread(String status, String target) {
        return CurrentApps.of(false, true, status, "blocked".equals(status), target, null, CONTROLLER);
    }
    static List<String> names(CurrentApps.Result r) {
        String[] n = new String[r.apps.size()];
        for (int i = 0; i < n.length; i++) n[i] = r.apps.get(i).packageName + (r.apps.get(i).front ? "*" : "") + (r.apps.get(i).target ? "!" : "");
        return Arrays.asList(n);
    }

    public static void main(String[] args) {
        // No session, or a session that is not live: nothing is running under agent control.
        for (String status : new String[] {"idle", "starting", "scheduled", "consent_required", "target_not_allowed", "stopped", "closed", "failed", "rejected", "cleanup_failed", "error", ""})
            expect(read(false, true, status, MAPS, MAPS).apps.isEmpty() && CurrentApps.NONE.equals(read(false, true, status, MAPS).status), "not live: " + status);
        expect(read(false, false, "ready", MAPS, MAPS).apps.isEmpty(), "no session object: nothing, whatever the status says");
        expect(CurrentApps.LOCKED.equals(read(true, true, "ready", MAPS, MAPS).status) && read(true, true, "ready", MAPS, MAPS).apps.isEmpty(), "locked lists nothing");

        // Live: the front windows, then the target; the controller itself never; no duplicates; order kept.
        expect(names(read(false, true, "ready", MAPS, MAPS)).equals(Arrays.asList(MAPS + "*!")), "target in front");
        expect(names(unread("ready", MAPS)).equals(Arrays.asList(MAPS + "!")), "front not read (the read failed): only the target");
        expect(names(read(false, true, "ready", MAPS, INSTALLER, MAPS, CONTROLLER, "", null)).equals(Arrays.asList(INSTALLER + "*", MAPS + "*!")), "installer over the target");
        expect(names(read(false, true, "ready", MAPS, INSTALLER)).equals(Arrays.asList(INSTALLER + "*", MAPS + "!")), "the target behind the installer stays a member");
        expect(names(read(false, true, "launching", MAPS)).equals(Arrays.asList(MAPS + "!")) && names(read(false, true, "blocked", MAPS)).equals(Arrays.asList(MAPS + "!")), "launching and blocked are live");
        expect(read(false, true, "ready", CONTROLLER, CONTROLLER).apps.isEmpty(), "the controller is never its own member");
        expect(read(false, true, "ready", null).apps.isEmpty() && read(false, true, "ready", "").apps.isEmpty(), "no target, no front: empty, not invented");

        // Scenario: launch, content change, repeated reads, close, release, reconnect, re-entry.
        Consumer home = new Consumer();
        expect(home.read(read(false, false, "idle", null), null) && home.shown.isEmpty(), "first read: none");
        expect(!home.read(read(false, false, "idle", null), null), "re-entry with nothing running changes nothing");
        expect(home.read(read(false, true, "launching", MAPS), "s1") && home.shown.equals(Arrays.asList(MAPS)), "launch adds the app");
        expect(home.read(read(false, true, "ready", MAPS, MAPS), "s1") && home.shown.equals(Arrays.asList(MAPS)), "ready: now in front (content changed)");
        expect(!home.read(read(false, true, "ready", MAPS, MAPS), "s1"), "the same session read again is the same generation");
        expect(home.read(read(false, true, "ready", MAPS, INSTALLER, MAPS), "s1") && home.shown.equals(Arrays.asList(INSTALLER, MAPS)), "an installer opens over it");
        expect(home.read(read(false, true, "ready", MAPS, MAPS), "s1") && home.shown.equals(Arrays.asList(MAPS)), "the installer closes: it leaves");
        long beforeClose = home.generation;
        expect(home.read(read(false, true, "closed", MAPS), "s1") && home.shown.isEmpty() && home.generation == beforeClose + 1, "close removes it");
        expect(home.read(read(false, true, "ready", AMAZON, AMAZON), "s2") && home.shown.equals(Arrays.asList(AMAZON)), "a new session lists only its own app");
        expect(home.read(read(false, true, "stopped", AMAZON), "s2") && home.shown.isEmpty(), "Stop (release of control) removes it; the target name left in state is not a member");
        expect(home.read(read(false, true, "ready", AMAZON, AMAZON), "s3") && home.shown.equals(Arrays.asList(AMAZON)), "reconnect: same app, new session");
        expect(home.read(read(false, true, "ready", AMAZON, AMAZON), "s4"), "a replaced session with the same app is a new generation, never merged");
        expect(home.read(read(true, true, "ready", AMAZON, AMAZON), "s4") && home.shown.isEmpty(), "locking hides it");
        expect(home.read(read(false, true, "ready", AMAZON, AMAZON), "s4") && home.shown.equals(Arrays.asList(AMAZON)), "unlocking shows the live set again, from the session");
        expect(home.read(read(false, true, "failed", AMAZON), "s4") && home.shown.isEmpty(), "failure removes it");
        // Completeness: empty is complete only when no session is live; a live set always says why it is not complete.
        expect(read(false, false, "idle", null).complete && read(false, true, "stopped", MAPS).complete && "".equals(read(false, true, "closed", MAPS).incompleteReason), "no live session: genuinely empty");
        expect(!read(true, true, "ready", MAPS, MAPS).complete && CurrentApps.LOCKED.equals(read(true, true, "ready", MAPS, MAPS).incompleteReason), "locked is unknown, never empty");
        expect(!read(false, true, "ready", MAPS, MAPS).complete && CurrentApps.BEHIND_FRONT_UNLISTED.equals(read(false, true, "ready", MAPS, MAPS).incompleteReason), "a live set never claims completeness");
        expect(CurrentApps.WINDOWS_UNREADABLE.equals(unread("ready", MAPS).incompleteReason) && !unread("ready", MAPS).complete, "an unread front says so");
        expect(CurrentApps.BLOCKED.equals(unread("blocked", MAPS).incompleteReason), "blocked says so");
        expect(CurrentApps.BLOCKED.equals(CurrentApps.of(false, true, "ready", true, MAPS, null, CONTROLLER).incompleteReason), "the blocked flag wins over a ready status");
        expect(!read(false, true, "ready", null).complete && read(false, true, "ready", null).apps.isEmpty(), "live but nothing identified: empty yet not complete");
        expect(!CurrentApps.signature(read(false, true, "ready", MAPS), "s1").equals(CurrentApps.signature(unread("ready", MAPS), "s1")), "a change in completeness is a new generation");
        expect(CurrentApps.signature(read(false, true, "ready", MAPS), null).equals(CurrentApps.signature(read(false, true, "ready", MAPS), "")), "no session id is no session id");
        // Platform answer (#156 parity: ComputerControlSession.getAutomatedApps Bundle, PR361): validated first.
        int me = 0;
        CurrentApps.Platform ok = CurrentApps.platform(7L, new String[] {AMAZON, MAPS}, 0, true, false, me);
        expect(ok != null && ok.generation == 7L && ok.packages.equals(Arrays.asList(AMAZON, MAPS)) && ok.complete && !ok.closed, "valid answer");
        Object[][] bad = {
            {7, new String[] {MAPS}, 0, true, false}, {7L, Arrays.asList(MAPS), 0, true, false}, {7L, new String[] {MAPS}, 0L, true, false},
            {7L, new String[] {MAPS}, 0, "true", false}, {7L, new String[] {MAPS}, 0, true, null}, {null, new String[] {MAPS}, 0, true, false},
            {7L, new String[] {MAPS, ""}, 0, true, false}, {7L, new String[] {null}, 0, true, false},
            {7L, new String[] {MAPS}, 10, true, false}, {Long.MAX_VALUE, new String[] {MAPS}, 0, true, true}};
        for (Object[] b : bad) expect(CurrentApps.platform(b[0], b[1], b[2], b[3], b[4], me) == null, "malformed, another user's, or closed-with-apps: refused " + Arrays.toString(b));
        // Members are the platform's packages, front or hidden; front ones first; the target only if the platform lists it.
        CurrentApps.Result p = CurrentApps.of(false, true, "ready", MAPS, ok, Arrays.asList(AMAZON), CONTROLLER);
        expect(names(p).equals(Arrays.asList(AMAZON + "*", MAPS + "!")) && p.complete && "".equals(p.incompleteReason) && CurrentApps.PLATFORM.equals(p.source),
            "a hidden app behind the front is a member, and the set is complete");
        CurrentApps.Platform installer = CurrentApps.platform(8L, new String[] {INSTALLER}, 0, true, false, me);
        expect(names(CurrentApps.of(false, true, "ready", MAPS, installer, Arrays.asList(INSTALLER), CONTROLLER)).equals(Arrays.asList(INSTALLER + "*")),
            "the target's activities finished: it is no longer a member, whatever the session's target says");
        expect(names(CurrentApps.of(false, true, "ready", MAPS, ok, null, CONTROLLER)).equals(Arrays.asList(AMAZON, MAPS + "!")), "front unread: members kept, none marked front");
        expect(names(CurrentApps.of(false, true, "ready", MAPS, ok, Arrays.asList("com.not.listed"), CONTROLLER)).equals(Arrays.asList(AMAZON, MAPS + "!")), "a window the platform does not list adds nothing");
        CurrentApps.Platform withController = CurrentApps.platform(9L, new String[] {CONTROLLER, MAPS}, 0, true, false, me);
        expect(names(CurrentApps.of(false, true, "ready", MAPS, withController, null, CONTROLLER)).equals(Arrays.asList(MAPS + "!")), "never the controller");
        CurrentApps.Result partial = CurrentApps.of(false, true, "ready", MAPS, CurrentApps.platform(9L, new String[] {MAPS}, 0, false, false, me), null, CONTROLLER);
        expect(!partial.complete && CurrentApps.OTHER_USER_OMITTED.equals(partial.incompleteReason), "another user's activity omitted: incomplete, unnamed");
        CurrentApps.Result handover = CurrentApps.of(false, true, "ready", MAPS, CurrentApps.platform(10L, new String[0], 0, true, false, me), null, CONTROLLER);
        expect(CurrentApps.LIVE.equals(handover.status) && handover.apps.isEmpty() && handover.complete, "handover: open, genuinely empty, complete");
        CurrentApps.Result closed = CurrentApps.of(false, true, "ready", MAPS, CurrentApps.platform(Long.MAX_VALUE, new String[0], 0, true, true, me), null, CONTROLLER);
        expect(CurrentApps.NONE.equals(closed.status) && closed.apps.isEmpty() && closed.complete, "closed: nothing, for good, even if the controller still says ready");
        expect(CurrentApps.NONE.equals(CurrentApps.of(false, true, "stopped", MAPS, ok, null, CONTROLLER).status), "a session the controller ended lists nothing");
        expect(CurrentApps.LOCKED.equals(CurrentApps.of(true, true, "ready", MAPS, ok, null, CONTROLLER).status), "locked lists nothing");
        expect(!CurrentApps.signature(p, "s1").equals(CurrentApps.signature(read(false, true, "ready", MAPS, AMAZON), "s1")), "a change of source is a new generation");
        // The fallback stays marked incomplete and says where it came from.
        expect(CurrentApps.SESSION_WINDOWS.equals(read(false, true, "ready", MAPS, MAPS).source) && !read(false, true, "ready", MAPS, MAPS).complete, "fallback is the session windows, incomplete");
        // Platform scenario: launch, hidden app, content change, handover, close; each read replaces the whole set.
        Consumer launcher = new Consumer();
        String[][] steps = {{MAPS}, {AMAZON, MAPS}, {MAPS}, {}};
        long gen = 1;
        for (String[] step : steps) {
            CurrentApps.Platform pl = CurrentApps.platform(gen++, step, 0, true, false, me);
            expect(launcher.read(CurrentApps.of(false, true, "ready", MAPS, pl, null, CONTROLLER), "s1") && launcher.shown.equals(Arrays.asList(step)), "platform step " + Arrays.toString(step));
        }
        expect(launcher.read(CurrentApps.of(false, true, "ready", MAPS, CurrentApps.platform(Long.MAX_VALUE, new String[0], 0, true, true, me), null, CONTROLLER), "s1") && launcher.shown.isEmpty(), "close");
        // Home's normalized contract (PR362): the consumer generation is the controller's counter, not the platform's.
        CurrentApps.Platform first = CurrentApps.platform(5L, new String[] {MAPS}, 0, true, false, me);
        CurrentApps.Platform replacement = CurrentApps.platform(1L, new String[] {MAPS}, 0, true, false, me);
        CurrentApps.Result a = CurrentApps.of(false, true, "ready", MAPS, first, null, CONTROLLER), b2 = CurrentApps.of(false, true, "ready", MAPS, replacement, null, CONTROLLER);
        expect(CurrentApps.signature(a, "s1", "ready").equals(CurrentApps.signature(CurrentApps.of(false, true, "ready", MAPS, CurrentApps.platform(6L, new String[] {MAPS}, 0, true, false, me), null, CONTROLLER), "s1", "ready")),
            "a platform generation step with identical content is no change for the consumer");
        expect(!CurrentApps.signature(a, "s1", "ready").equals(CurrentApps.signature(b2, "s2", "ready")), "a replacement session whose platform generation restarted is still a change");
        expect(!CurrentApps.signature(a, "s1", "ready").equals(CurrentApps.signature(a, "s1", "blocked")), "the session status is part of what the consumer sees");
        expect(CurrentApps.signature(closed, "s1", "ready").equals(CurrentApps.signature(closed, null, null)), "closed: no session, no session status");
        System.out.println("CurrentAppsTest: " + checks + " checks passed");
    }
}
