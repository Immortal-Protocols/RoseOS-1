package dev.pixel.voice;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

public final class UpdateCheckTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final String SELF = "dev.pixel.voice";
    static String rep(char c) { char[] a = new char[64]; Arrays.fill(a, c); return new String(a); }
    static final String HASH = rep('a');
    static UpdateCheck.Record self(Object version) {
        return new UpdateCheck.Record(HASH, "Rose Agent", SELF, version, HASH);
    }
    static final UpdateCheck.Record OTHER =
            new UpdateCheck.Record(rep('b'), "Calculator", "dev.pixel.calculator.agent", 9, rep('b'));

    public static void main(String[] args) {
        UpdateCheck r = UpdateCheck.evaluate(200, Arrays.asList(OTHER, self(298)), 298, SELF, false);
        expect(r.state == UpdateCheck.State.NO_UPDATE && r.offered == 298, "Same version is no update");
        expect(UpdateCheck.evaluate(200, Arrays.asList(self(297)), 298, SELF, false).state == UpdateCheck.State.NO_UPDATE,
                "Older offered version is no update");
        r = UpdateCheck.evaluate(200, Arrays.asList(self(299)), 298, SELF, false);
        expect(r.state == UpdateCheck.State.AVAILABLE && r.offered == 299 && r.message.contains("isn’t enabled"),
                "Higher version is reported, without claiming it installs");
        r = UpdateCheck.evaluate(200, Arrays.asList(self(299.0)), 298, SELF, true);
        expect(r.state == UpdateCheck.State.AVAILABLE && r.message.contains("interrupt"),
                "JSON whole number accepted; during a call nothing interrupts it");

        // Unavailable: publication not established. Current server returns {apps:[]} with no delivery dir.
        expect(UpdateCheck.evaluate(200, Collections.<UpdateCheck.Record>emptyList(), 298, SELF, false).state
                == UpdateCheck.State.UNAVAILABLE, "Empty catalog is unavailable, not no update");
        expect(UpdateCheck.evaluate(200, Arrays.asList(OTHER), 298, SELF, false).state == UpdateCheck.State.UNAVAILABLE,
                "Catalog without Rose Agent is unavailable");
        // Incomplete self records are never offered and never reported as up to date.
        for (Object bad : new Object[] {null, "299", 0, -3, 299.5, 1e300}) {
            UpdateCheck x = UpdateCheck.evaluate(200, Arrays.asList(self(bad)), 298, SELF, false);
            expect(x.state == UpdateCheck.State.UNAVAILABLE, "Invalid versionCode " + bad + " is unavailable");
        }
        UpdateCheck.Record badHash = new UpdateCheck.Record(HASH, "Rose Agent", SELF, 299, rep('c'));
        expect(UpdateCheck.evaluate(200, Arrays.asList(badHash), 298, SELF, false).state == UpdateCheck.State.UNAVAILABLE,
                "id must equal sha256");
        UpdateCheck.Record noId = new UpdateCheck.Record(null, "Rose Agent", SELF, 299, null);
        expect(UpdateCheck.evaluate(200, Arrays.asList(noId), 298, SELF, false).state == UpdateCheck.State.UNAVAILABLE,
                "Missing id is unavailable");

        // Errors: never "no update".
        expect(UpdateCheck.evaluate(200, Arrays.asList(self(298)), -1, SELF, false).state == UpdateCheck.State.ERROR,
                "Installed version read failure is an error, not no update");
        expect(UpdateCheck.evaluate(200, null, 298, SELF, false).state == UpdateCheck.State.ERROR,
                "Unreadable body is an error");
        expect(UpdateCheck.evaluate(0, null, 298, SELF, false).state == UpdateCheck.State.ERROR, "Network failure is an error");
        r = UpdateCheck.evaluate(401, null, 298, SELF, false);
        expect(r.state == UpdateCheck.State.ERROR && r.message.contains("401"), "Auth failure is an error with its status");
        expect(UpdateCheck.evaluate(503, Arrays.asList(self(299)), 298, SELF, false).state == UpdateCheck.State.ERROR,
                "A non-200 body is never trusted");

        List<UpdateCheck.Record> both = Arrays.asList(self(300), OTHER);
        expect(UpdateCheck.evaluate(200, both, 298, SELF, false).offered == 300, "Finds the self record among others");

        System.out.println("UpdateCheckTest: " + checks + " checks passed");
    }
}
