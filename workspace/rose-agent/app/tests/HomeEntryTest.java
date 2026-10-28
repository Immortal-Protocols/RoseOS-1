package dev.pixel.voice;

public final class HomeEntryTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    // main, home, launcher, fresh, fromHistory, paired
    static HomeEntry entry(boolean main, boolean home, boolean launcher, boolean fresh, boolean fromHistory, boolean paired) {
        return HomeEntry.of(main, home, launcher, fresh, fromHistory, paired);
    }
    public static void main(String[] args) {
        // The 2804 discrepancy: the app icon landed on the legacy voice page (302 kept voicePage for LAUNCHER).
        HomeEntry icon = entry(true, false, true, true, false, true);
        expect(icon.showHome && icon.openConversation, "Cold app-icon launch shows Rose Home and opens the Rose conversation over it");
        expect(entry(true, false, true, true, false, true).openConversation,
                "Icon tap on the running singleTask activity (onNewIntent) opens the conversation again");
        HomeEntry home = entry(true, true, false, true, false, true);
        expect(home.showHome && !home.openConversation, "HOME shows Rose Home and never opens the conversation by itself");
        expect(!entry(true, true, true, true, false, true).openConversation, "An intent with both categories is HOME");
        HomeEntry recreated = entry(true, false, true, false, false, true);
        expect(recreated.showHome && !recreated.openConversation, "Recreation keeps Home and does not reopen the conversation (no loop)");
        HomeEntry recents = entry(true, false, true, true, true, true);
        expect(recents.showHome && !recents.openConversation, "A relaunch from Recents does not reopen the conversation");
        HomeEntry unpaired = entry(true, false, true, true, false, false);
        expect(unpaired.showHome && !unpaired.openConversation, "Before pairing the pairing screen comes first, alone");
        HomeEntry other = entry(false, false, false, true, false, true);
        expect(!other.showHome && !other.openConversation, "Notification and entry-activity opens keep their existing page");
        expect(!entry(true, false, false, true, false, true).showHome, "A bare MAIN without LAUNCHER or HOME is not an icon launch");
        expect(!entry(false, false, true, true, false, true).openConversation, "LAUNCHER without MAIN is not an icon launch");
        System.out.println("HomeEntryTest: " + checks + " checks passed");
    }
}
