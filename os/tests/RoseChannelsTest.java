package app.seamlessupdate.client;

import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

public final class RoseChannelsTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        // Migration on boot: (stored channel, marker set, first boot after OTA).
        expect(RoseChannels.migration(false, false, false) == null,
                "Fresh installation stores nothing and so follows the Prod default");
        expect(RoseChannels.LEGACY_BETA.equals(RoseChannels.migration(false, false, true)),
                "An existing phone OTA'd onto these rules without a stored key keeps the Beta (stable) feed");
        expect(RoseChannels.migration(true, false, true) == null,
                "A stored legacy stable or explicit choice is kept as is");
        expect(RoseChannels.migration(true, true, false) == null, "An explicit choice survives reboot");
        expect(RoseChannels.migration(false, true, true) == null,
                "After the first boot, a later OTA never moves a Prod-default phone to Beta");
        expect("stable".equals(RoseChannels.LEGACY_BETA) && "prod".equals(RoseChannels.PROD) && "alpha".equals(RoseChannels.ALPHA),
                "Wire tokens: Beta stays stable (frankel-stable), Prod prod, Alpha alpha");

        Set<String> published = new HashSet<>(Collections.singleton("stable"));
        expect(RoseChannels.shouldFetch("", "stable", published), "The existing Beta feed is fetched");
        expect(!RoseChannels.shouldFetch("", "prod", published), "Unpublished Prod is not fetched");
        expect(!RoseChannels.shouldFetch("", "alpha", published), "Unpublished Alpha is not fetched");
        expect(!RoseChannels.shouldFetch("", "stable-security-preview", published),
                "A security-preview variant needs its own published feed");
        expect(!RoseChannels.shouldFetch("", "beta", published),
                "A leftover upstream 'beta' choice has no RoseOS feed and is reported unavailable");
        expect(RoseChannels.shouldFetch("alpha", "alpha", published),
                "An explicit sys.update.channel override keeps stock behaviour");
        expect(!RoseChannels.shouldFetch(null, "prod", published), "A missing override is not an override");
        expect(RoseChannels.shouldFetch("", "prod", new HashSet<>(java.util.Arrays.asList("stable", "prod"))),
                "Prod is fetched once its feed is declared published");
        System.out.println("RoseChannelsTest: " + checks + " checks passed");
    }
}
