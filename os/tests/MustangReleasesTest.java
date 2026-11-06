package app.seamlessupdate.client;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** Synthetic mixed-device feed tests against the actual updater selection code. */
public final class MustangReleasesTest {
    static int checks;
    static void expect(boolean value, String reason) {
        checks++; if (!value) throw new AssertionError(reason);
    }
    static RoseReleases.Release release(String device, String build, boolean draft, String channel) {
        return new RoseReleases.Release(device + "-" + build, draft, Arrays.asList(
            new RoseReleases.Asset(device + "-" + channel, "uploaded"),
            new RoseReleases.Asset(device + "-ota_update-" + build + ".zip", "uploaded")));
    }
    public static void main(String[] args) {
        List<RoseReleases.Release> list = Arrays.asList(
            release("frankel", "2026110499", false, "stable"),
            release("rango", "2026110498", false, "stable"),
            release("blazer", "2026110497", false, "stable"),
            release("mustang", "2026110302", false, "stable"),
            release("mustang", "2026110401", false, "stable"),
            release("mustang", "2026110409", true, "stable"),
            release("mustang", "2026110408", false, "prod"));
        expect("mustang-2026110401".equals(RoseReleases.choose(list, true, "mustang", "stable", "2026110302").tag),
               "Mustang Beta selects only the newer complete Mustang stable-wire release");
        expect(RoseReleases.choose(list, true, "mustang", "stable", "2026110401").upToDate(), "Installed build is not reoffered");
        expect(RoseReleases.choose(Collections.singletonList(list.get(0)), true, "mustang", "stable", "2026110302").upToDate(),
               "No fallback to Frankel Latest");
        expect(RoseReleases.choose(list, false, "mustang", "stable", "2026110302").error != null, "Incomplete list refuses");
        RoseReleases.Release incomplete = new RoseReleases.Release("mustang-2026110499", false,
            Collections.singletonList(new RoseReleases.Asset("mustang-stable", "uploaded")));
        expect(RoseReleases.choose(Collections.singletonList(incomplete), true, "mustang", "stable", "2026110302").upToDate(),
               "Metadata without matching OTA is never eligible");
        expect(RoseReleases.choose(list, true, "mustang", "alpha", "2026110302").upToDate(), "Unpublished channel has no fallback");
        // Synthetic namespace boundaries only: no version is selected or reserved for a real build.
        String legacyCeiling = "9999123199", aboveLegacy = "9999123200", tenDigitCeiling = "9999999999";
        RoseReleases.Release boundary = release("mustang", aboveLegacy, false, "stable");
        RoseReleases.Release maximum = release("mustang", tenDigitCeiling, false, "stable");
        expect(RoseReleases.eligible(boundary, "mustang", "stable") == Long.parseLong(aboveLegacy),
               "Non-calendar ten-digit IDs fit the actual long selector");
        expect(RoseReleases.eligible(maximum, "mustang", "stable") == 9999999999L,
               "The upper ten-digit ID does not overflow a signed 32-bit integer");
        expect(("mustang-" + aboveLegacy).equals(RoseReleases.choose(Collections.singletonList(boundary), true,
               "mustang", "stable", legacyCeiling).tag), "First synthetic namespace boundary exceeds all legacy IDs");
        expect(("mustang-" + tenDigitCeiling).equals(RoseReleases.choose(Arrays.asList(maximum, boundary), true,
               "mustang", "stable", legacyCeiling).tag), "Selection compares numeric IDs regardless of listing order");
        expect(RoseReleases.choose(Collections.singletonList(boundary), true, "mustang", "stable", aboveLegacy).upToDate(),
               "An equal migrated ID is not reoffered");
        expect(RoseReleases.choose(Collections.singletonList(boundary), true, "mustang", "stable", tenDigitCeiling).upToDate(),
               "A lower migrated ID is not offered as an upgrade");
        expect(RoseReleases.eligible(release("mustang", "10000000000", false, "stable"), "mustang", "stable") == -1,
               "Eleven digits are outside the existing selector contract");
        expect(RoseReleases.eligible(release("mustang", "99991232x0", false, "stable"), "mustang", "stable") == -1,
               "Non-numeric IDs remain ineligible");
        expect(RoseReleases.choose(Collections.singletonList(release("frankel", tenDigitCeiling, false, "stable")),
               true, "mustang", "stable", legacyCeiling).upToDate(), "High IDs never permit another device fallback");
        expect(RoseReleases.choose(Collections.singletonList(maximum), false, "mustang", "stable", legacyCeiling).error != null,
               "A high eligible ID cannot bypass incomplete pagination");
        System.out.println("MustangReleasesTest: " + checks + " checks passed");
    }
}
