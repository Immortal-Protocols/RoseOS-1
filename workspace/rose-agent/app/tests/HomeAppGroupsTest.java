package dev.pixel.voice;

/** #403: which apps leave the app pages for the "…" System folder, and which simplified glyph each suite app gets.
 * Host JVM only: the inputs are the launcher's own inventory facts (suite, our build, preinstalled, preferred camera). */
public final class HomeAppGroupsTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static int g(String pkg, boolean suite, boolean ours, boolean pre, String camera) {
        return HomeAppGroups.group(pkg, suite, ours, pre, camera);
    }

    public static void main(String[] args) {
        final int SUITE = HomeAppGroups.SUITE, SYSTEM = HomeAppGroups.SYSTEM, MAIN = HomeAppGroups.MAIN;
        String ai = "dev.pixel.aicamera";

        // Claude, ChatGPT and Amazon stay on the app pages however they were installed.
        for (String pkg : new String[]{"com.anthropic.claude", "com.openai.chatgpt", "com.amazon.mShop.android.shopping"}) {
            expect(g(pkg, false, false, false, ai) == MAIN, pkg + " from the Play Store: app pages");
            expect(g(pkg, false, false, true, ai) == MAIN, pkg + " even if preinstalled: app pages, never the folder");
            expect(g(pkg, false, true, true, ai) == MAIN, pkg + " in any combination");
        }

        // Rose's own suite stays first, preinstalled or not.
        expect(g("com.android.dialer", true, false, true, ai) == SUITE, "a preinstalled suite app (Phone) is the suite");
        expect(g("dev.pixel.mail", true, true, false, ai) == SUITE, "Mail is the suite");
        expect(g(ai, true, true, false, ai) == SUITE, "the preferred camera is the suite's Camera");

        // System apps: an OS app that is not ours.
        for (String pkg : new String[]{"app.grapheneos.apps", "app.attestation.auditor", "app.grapheneos.pdfviewer",
            "com.android.deskclock", "com.android.messaging", "com.android.calculator2", "app.grapheneos.info"})
            expect(g(pkg, false, false, true, ai) == SYSTEM, pkg + ": preinstalled, not ours, so the folder");
        expect(g("dev.pixel.glassescamera", false, true, true, ai) == MAIN, "our own preinstalled build stays on the pages");
        expect(g("dev.pixel.someapp", false, true, false, ai) == MAIN, "our other builds stay on the pages");

        // Duplicates named by AppLabels go in the folder even when not preinstalled.
        for (String pkg : new String[]{"dev.pixel.mail.debug", "org.fossify.calendar.pixel.debug", "dev.pixel.weather.opus"}) {
            expect(g(pkg, false, true, false, ai) == SYSTEM, pkg + ": a second copy of a suite app, so the folder");
            expect(g(pkg, false, false, false, ai) == SYSTEM, pkg + ": whoever signed it");
        }
        expect(g("net.sourceforge.opencamera", false, false, false, ai) == SYSTEM, "Open Camera beside the preferred camera is a duplicate");
        expect(g("app.grapheneos.camera", false, false, true, ai) == SYSTEM, "the system camera beside the preferred camera is a duplicate");
        expect(g("app.grapheneos.camera", false, false, true, "") == SYSTEM, "the system camera is a system app without a preferred camera too");
        expect(g("net.sourceforge.opencamera", true, false, false, "net.sourceforge.opencamera") == SUITE, "Open Camera as the preferred camera is the suite");
        expect(g(ai, false, true, false, "net.sourceforge.opencamera") == SYSTEM, "the preview camera beside a preferred Open Camera is a duplicate");

        // Ordinary installs are never hidden.
        for (String pkg : new String[]{"com.google.android.apps.maps", "com.southwestairlines.mobile", "org.thoughtcrime.securesms", "com.android.vending"}) {
            expect(g(pkg, false, false, false, ai) == MAIN, pkg + ": a user install stays on the pages");
        }
        expect(g(null, false, false, false, ai) == MAIN, "no package name: not hidden");

        // The folder's names.
        expect(HomeAppGroups.FOLDER_LABEL.equals("System"), "visible label");
        expect(HomeAppGroups.folderDescription(1).equals("System folder, 1 app"), "singular");
        expect(HomeAppGroups.folderDescription(7).equals("System folder, 7 apps"), "plural");

        // Glyphs: every suite package (AppLabels.SUITE_PACKAGES, the camera slot by the preferred camera) has one.
        String[] suite = {"com.bluebubbles.messaging", "dev.pixel.mail", "org.fossify.calendar.pixel", "dev.pixel.clock", ai,
            "dev.pixel.calculator.agent", "dev.pixel.notes.agent", "dev.pixel.reminders", "dev.pixel.news.feeder", "dev.pixel.books",
            "dev.pixel.podcasts", "dev.pixel.compass", "dev.pixel.sky", "dev.pixel.weather", "com.android.documentsui",
            "com.android.gallery3d", "com.android.dialer", "com.android.settings", "app.vanadium.browser", "com.android.contacts",
            "dev.pixel.memories"};
        String[] keys = {"messages", "mail", "calendar", "clock", "camera", "calculator", "notes", "reminders", "news", "books",
            "podcasts", "compass", "sky", "weather", "files", "gallery", "phone", "settings", "browser", "contacts", "memories"};
        java.util.HashSet<String> distinct = new java.util.HashSet<>();
        for (int i = 0; i < suite.length; i++) {
            expect(keys[i].equals(HomeAppGroups.glyph(suite[i], ai)), suite[i] + " draws " + keys[i]);
            distinct.add(HomeAppGroups.glyph(suite[i], ai));
        }
        expect(distinct.size() == suite.length, "each suite app has its own mark");
        expect("mail".equals(HomeAppGroups.glyph("dev.pixel.mail.debug", ai)), "a duplicate reads as the app it repeats");
        expect("calendar".equals(HomeAppGroups.glyph("org.fossify.calendar.pixel.debug", ai)), "test calendar");
        expect("weather".equals(HomeAppGroups.glyph("dev.pixel.weather.opus", ai)), "alternate weather");
        expect("camera".equals(HomeAppGroups.glyph("app.grapheneos.camera", ai)), "system camera");
        expect("camera".equals(HomeAppGroups.glyph("net.sourceforge.opencamera", "")), "open camera without a preferred camera");
        expect(HomeAppGroups.GLYPH_PACKAGES.length == HomeAppGroups.GLYPH_KEYS.length, "the package and key tables line up");
        for (String pkg : new String[]{"com.anthropic.claude", "com.openai.chatgpt", "com.amazon.mShop.android.shopping", "com.google.android.apps.maps", ""})
            expect(HomeAppGroups.glyph(pkg, ai) == null, pkg + ": not redrawn; its own (themed) icon");
        expect(HomeAppGroups.glyph(null, ai) == null, "null package");
        expect(HomeAppGroups.glyph("", "") == null, "an empty package never matches an empty camera");

        // F04: main Home never shows GrapheneOS Messaging (by package, com.android.messaging); its default-role place
        // goes to Rose's Messages (BlueBubbles); every other package is placed as it is, and nothing is hidden elsewhere.
        expect("com.bluebubbles.messaging".equals(HomeAppGroups.homeRole("com.android.messaging")), "GrapheneOS Messaging's place is Rose's Messages");
        expect("com.bluebubbles.messaging".equals(HomeAppGroups.homeRole("com.bluebubbles.messaging")), "Rose's Messages stays");
        for (String other : new String[]{"com.android.dialer", "app.grapheneos.camera", "app.vanadium.browser", "com.google.android.apps.messaging", "org.fossify.messages"})
            expect(other.equals(HomeAppGroups.homeRole(other)), "only the exact OS package is replaced, not " + other);
        expect(HomeAppGroups.homeRole(null) == null, "no role holder: nothing");
        expect(HomeAppGroups.group("com.android.messaging", false, false, true, ai) == SYSTEM, "it stays launchable in the System folder");
        System.out.println("HomeAppGroupsTest: " + checks + " checks passed");
    }
}
