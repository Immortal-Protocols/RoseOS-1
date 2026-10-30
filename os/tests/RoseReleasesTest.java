package app.seamlessupdate.client;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** Offline checks of RoseReleases: Rango release discovery over a mixed pixel-ota release list. Fixtures are
 * shaped like the GitHub release list Service parses; names are adversarial examples, not publications. */
public final class RoseReleasesTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static RoseReleases.Asset up(String name) { return new RoseReleases.Asset(name, "uploaded"); }
    static RoseReleases.Release release(String tag, boolean draft, RoseReleases.Asset... assets) {
        return new RoseReleases.Release(tag, draft, Arrays.asList(assets));
    }
    static RoseReleases.Release rango(String n) {
        return release("rango-" + n, false, up("rango-stable"), up("rango-ota_update-" + n + ".zip"), up("SHA256SUMS"));
    }
    static String chosen(List<RoseReleases.Release> list, String running) {
        RoseReleases.Choice c = RoseReleases.choose(list, true, "rango", "stable", running);
        return c.error != null ? "error:" + c.error : c.tag == null ? "up-to-date" : c.tag;
    }

    public static void main(String[] args) {
        // Mixed Frankel and Rango: only Rango, and the newest eligible one.
        List<RoseReleases.Release> mixed = new ArrayList<>(Arrays.asList(
            release("frankel-2026103101", false, up("frankel-stable"), up("frankel-ota_update-2026103101.zip")),
            rango("2026103001"), rango("2026103105"),
            release("rose-agent-311", false, up("dev.pixel.voice-311.apk"))));
        expect(chosen(mixed, "2026103001").equals("rango-2026103105"), "Newest eligible Rango release");
        expect(RoseReleases.base("https://github.com/Immortal-Protocols/pixel-ota/releases/download/", "rango-2026103105")
            .equals("https://github.com/Immortal-Protocols/pixel-ota/releases/download/rango-2026103105/"),
            "Metadata and OTA are read from the same release");
        // Frankel's own device never sees Rango, and Rango never falls back to Frankel.
        expect(RoseReleases.choose(mixed, true, "frankel", "stable", "2026103001").tag.equals("frankel-2026103101"),
            "The same list serves Frankel only its own releases");
        expect(chosen(Collections.singletonList(mixed.get(0)), "2026103001").equals("up-to-date"), "No Rango release: nothing, no fallback");

        // Not newer than the running build: up to date (the Updater's own build-date check still runs later).
        expect(chosen(mixed, "2026103105").equals("up-to-date") && chosen(mixed, "2026103200").equals("up-to-date"), "Non-newer is not offered");
        expect(chosen(mixed, "unknown").equals("rango-2026103105"), "An unreadable running build cannot hide a release");

        // Ineligible releases are skipped, never chosen.
        String n = "2026103199";
        for (RoseReleases.Release bad : Arrays.asList(
                release("rango-" + n, true, up("rango-stable"), up("rango-ota_update-" + n + ".zip")),                 // draft
                release("rango-" + n, false, up("rango-stable")),                                                       // no OTA
                release("rango-" + n, false, up("rango-ota_update-" + n + ".zip")),                                     // no metadata
                release("rango-" + n, false, up("rango-beta"), up("rango-ota_update-" + n + ".zip")),                   // other channel
                release("rango-" + n, false, up("rango-stable"), up("rango-ota_update-2026103001.zip")),                // OTA of another build
                release("rango-" + n, false, up("rango-stable"), up("rango-stable"), up("rango-ota_update-" + n + ".zip")), // duplicate
                release("rango-" + n, false, new RoseReleases.Asset("rango-stable", "starter"), up("rango-ota_update-" + n + ".zip")), // incomplete
                release("rango-" + n + "-test", false, up("rango-stable"), up("rango-ota_update-" + n + ".zip")),     // malformed tag
                release("rangoo-" + n, false, up("rangoo-stable"), up("rangoo-ota_update-" + n + ".zip")),             // other device
                release(null, false, up("rango-stable")))) {
            List<RoseReleases.Release> list = new ArrayList<>(mixed);
            list.add(bad);
            expect(chosen(list, "2026103001").equals("rango-2026103105"), "Skipped: " + (bad.tag == null ? "no tag" : bad.tag + " " + bad.assets.size()));
        }

        // Channel is the Updater's wire token: Beta persists and requests "stable"; Alpha/Prod need their own metadata.
        expect(RoseReleases.choose(mixed, true, "rango", "prod", "2026103001").upToDate(), "Prod has no rango-prod asset: nothing");
        // An incomplete list (more pages than read) decides nothing rather than reporting up to date.
        RoseReleases.Choice partial = RoseReleases.choose(mixed, false, "rango", "stable", "2026103001");
        expect(partial.tag == null && partial.error != null, "An incomplete list is an error, not up to date");
        expect(RoseReleases.choose(mixed, true, "Rango/../x", "stable", "1").error != null
            && RoseReleases.choose(mixed, true, "rango", "", "1").error != null, "Malformed device or channel is refused");
        expect(RoseReleases.page("https://api.github.com/repos/Immortal-Protocols/pixel-ota/releases", 2)
            .equals("https://api.github.com/repos/Immortal-Protocols/pixel-ota/releases?per_page=100&page=2"), "Never /releases/latest");
        System.out.println("RoseReleasesTest: " + checks + " checks passed");
    }
}
