package dev.pixel.voice;

/** OS-HIFI-HOME-001: page order and where Home entries land, as MainActivity uses HomePages. */
public final class HomePagesTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        // Home, Our apps, Preinstalled, Play Store, Widgets, Voice home: six pages.
        int count = 6;
        expect(HomePages.HOME == 0 && HomePages.FIRST_APPS == 1, "Home first, the app pages right after it");
        expect(HomePages.widgets(count) == 4 && HomePages.voice(count) == 5, "Widgets then the legacy voice page last");

        // The change from the old behaviour: HOME from an app or the Rose conversation returns to the page left.
        expect(HomePages.target(true, false, false, 2, 5, count) == 2, "HOME after an app opened from Preinstalled returns to Preinstalled");
        expect(HomePages.target(true, false, false, 4, 5, count) == 4, "...and to Widgets");
        expect(HomePages.target(true, false, false, 0, 5, count) == 0, "...and to Home");
        // Unchanged: HOME pressed while Home is showing goes to Home, as before.
        expect(HomePages.target(true, false, true, 2, 5, count) == 0, "HOME on Home goes to the Home page");
        // Unchanged (PR67): the app icon shows Home, with the conversation opened over it.
        expect(HomePages.target(true, true, false, 3, 5, count) == 0, "The app icon lands on Home");
        // The legacy voice page, a missing or a stale page fall back to Home.
        expect(HomePages.target(true, false, false, 5, 5, count) == 0, "HOME never returns to the legacy voice page");
        expect(HomePages.target(true, false, false, -1, 5, count) == 0, "No previous page: Home");
        expect(HomePages.target(true, false, false, 9, 5, count) == 0, "A page gone after a rebuild: Home");
        // Unchanged: notifications and entry activities keep the activity's own page.
        expect(HomePages.target(false, false, false, 2, 5, count) == 5, "A non-Home entry keeps the fallback page");
        expect(HomePages.target(false, false, true, 2, 3, count) == 3, "...whatever was showing");

        // Recreation restores the saved page within the rebuilt pager.
        expect(HomePages.restore(3, count) == 3 && HomePages.restore(9, count) == 5 && HomePages.restore(-1, count) == 0
                && HomePages.restore(2, 0) == 0, "Restore clamps to the pages that exist");
        System.out.println("HomePagesTest: " + checks + " checks passed");
    }
}
