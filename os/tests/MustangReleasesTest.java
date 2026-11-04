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
        System.out.println("MustangReleasesTest: " + checks + " checks passed");
    }
}
