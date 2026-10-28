package dev.pixel.voice;

import java.util.Arrays;
import java.util.List;

/** OS-HOME-REUSE-001: a HOME or app-icon return keeps the built Home only while nothing it was built from
 * changed. Models MainActivity's sequence: attachLauncherPages records the stamp; onNewIntent rebuilds
 * (home()) unless HomeReuse.reuse holds; the package receiver bumps the generation and rebuilds. */
public final class HomeReuseTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }

    /** The parts of MainActivity the decision reads, and a count of full rebuilds. */
    static final class Activity {
        int generation, rebuilds;
        String configuration = "360x640@320/1/1.0/17/en-US", built;
        List<String> roles = Arrays.asList("com.android.dialer", "com.android.messaging", "app.grapheneos.camera", "org.chromium.chrome");
        boolean pagerShown = true, chatMode, detailOpen, settingChanged;
        String stamp() { return HomeReuse.stamp(generation, configuration, roles); }
        void attach() { rebuilds++; built = stamp(); pagerShown = true; }
        void create() { attach(); }
        void newIntent(boolean homeEntry) {
            if (!HomeReuse.reuse(homeEntry, pagerShown, built, stamp(), chatMode, detailOpen, settingChanged)) {
                chatMode = false; detailOpen = false; settingChanged = false; attach();
            }
        }
        void packageChanged(boolean rebuildNow) { generation++; if (rebuildNow) attach(); }
    }

    public static void main(String[] args) {
        Activity home = new Activity();
        home.create();
        for (int i = 0; i < 10; i++) home.newIntent(true);
        expect(home.rebuilds == 1, "Ten unchanged HOME returns reuse the one built Home");
        home.newIntent(true);
        expect(home.rebuilds == 1, "...and an app-icon return (also a Home entry) reuses it too");
        home.newIntent(false);
        expect(home.rebuilds == 2, "A notification or entry-activity open still rebuilds, as before");

        // Package add/remove/replace/enable changes: the receiver rebuilds at once; if it could not, the generation
        // still forces the next HOME to rebuild.
        home.packageChanged(true);
        expect(home.rebuilds == 3, "A package change rebuilds the catalog");
        home.newIntent(true);
        expect(home.rebuilds == 3, "...after which HOME reuses the fresh build");
        home.packageChanged(false);
        home.newIntent(true);
        expect(home.rebuilds == 4, "A package change the receiver did not rebuild for is caught on the next HOME");

        // Configuration: rotation and size (handled in-activity), density, font scale, night mode, locale.
        for (String configuration : new String[]{"640x360@320/2/1.0/17/en-US", "360x640@480/1/1.0/17/en-US",
                "360x640@320/1/1.3/17/en-US", "360x640@320/1/1.0/33/en-US", "360x640@320/1/1.0/17/fr-FR"}) {
            int before = home.rebuilds;
            home.configuration = configuration;
            home.newIntent(true);
            expect(home.rebuilds == before + 1, "Configuration " + configuration + " rebuilds");
            home.newIntent(true);
            expect(home.rebuilds == before + 1, "...then reuses");
        }

        // A changed default app (no package broadcast) rebuilds the dock on the next HOME.
        int before = home.rebuilds;
        home.roles = Arrays.asList("com.android.dialer", "org.thoughtcrime.securesms", "app.grapheneos.camera", "org.chromium.chrome");
        home.newIntent(true);
        expect(home.rebuilds == before + 1, "A new default messages app rebuilds the dock");

        // State the old rebuild reset is still reset: chat mode, an open visual or Mac screen, a changed Home setting.
        before = home.rebuilds;
        home.chatMode = true; home.newIntent(true);
        home.detailOpen = true; home.newIntent(true);
        home.settingChanged = true; home.newIntent(true);
        expect(home.rebuilds == before + 3, "Chat mode, an open detail and a changed Home setting each rebuild");
        home.pagerShown = false; home.newIntent(true);
        expect(home.rebuilds == before + 4, "When another view replaced the pager, HOME rebuilds");
        expect(!HomeReuse.reuse(true, true, null, home.stamp(), false, false, false), "Nothing built yet: rebuild");

        // Stamps differ for each input and agree otherwise.
        String base = HomeReuse.stamp(1, "c", Arrays.asList("a", "b"));
        expect(base.equals(HomeReuse.stamp(1, "c", Arrays.asList("a", "b"))), "Same inputs, same stamp");
        expect(!base.equals(HomeReuse.stamp(2, "c", Arrays.asList("a", "b"))) && !base.equals(HomeReuse.stamp(1, "d", Arrays.asList("a", "b")))
                && !base.equals(HomeReuse.stamp(1, "c", Arrays.asList("b", "a"))) && !base.equals(HomeReuse.stamp(1, "c", Arrays.asList("a"))),
                "Generation, configuration and dock order each change the stamp");
        System.out.println("HomeReuseTest: " + checks + " checks passed");
    }
}
