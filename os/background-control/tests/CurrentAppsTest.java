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
        return CurrentApps.of(locked, session, status, target, Arrays.asList(front), CONTROLLER);
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
        expect(names(read(false, true, "ready", MAPS)).equals(Arrays.asList(MAPS + "!")), "front not read (blocked, or the read failed): only the target");
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
        expect(CurrentApps.signature(read(false, true, "ready", MAPS), null).equals(CurrentApps.signature(read(false, true, "ready", MAPS), "")), "no session id is no session id");
        System.out.println("CurrentAppsTest: " + checks + " checks passed");
    }
}
