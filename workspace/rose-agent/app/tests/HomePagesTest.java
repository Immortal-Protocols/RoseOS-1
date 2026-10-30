package dev.pixel.voice;

/** OS-HIFI-HOME-001: page order and where Home entries land, as MainActivity uses HomePages. */
public final class HomePagesTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        // #223: Home, Our apps, Preinstalled, Play Store and nothing after them: no Widgets or voice page.
        int count = 4;
        expect(HomePages.HOME == 0 && HomePages.FIRST_APPS == 1, "Home first, the app pages right after it");

        // The change from the old behaviour: HOME from an app or the Rose conversation returns to the page left.
        expect(HomePages.target(true, false, false, 2, 5, count) == 2, "HOME after an app opened from Preinstalled returns to Preinstalled");
        expect(HomePages.target(true, false, false, 3, 2, count) == 3, "...and to the last app page");
        expect(HomePages.target(true, false, false, 0, 5, count) == 0, "...and to Home");
        // Unchanged: HOME pressed while Home is showing goes to Home, as before.
        expect(HomePages.target(true, false, true, 2, 5, count) == 0, "HOME on Home goes to the Home page");
        // Unchanged (PR67): the app icon shows Home, with the conversation opened over it.
        expect(HomePages.target(true, true, false, 3, 5, count) == 0, "The app icon lands on Home");
        // The legacy voice page, a missing or a stale page fall back to Home.
        // #223: the old Widgets (4) and voice (5) positions are past the pages, so HOME never lands there.
        expect(HomePages.target(true, false, false, 4, 2, count) == 0 && HomePages.target(true, false, false, 5, 2, count) == 0,
                "HOME never returns to a removed Widgets or voice page");
        expect(HomePages.target(true, false, false, -1, 5, count) == 0, "No previous page: Home");
        expect(HomePages.target(true, false, false, 9, 5, count) == 0, "A page gone after a rebuild: Home");
        // Unchanged: notifications and entry activities keep the activity's own page.
        expect(HomePages.target(false, false, false, 2, 3, count) == 3, "A non-Home entry keeps the fallback page");
        expect(HomePages.target(false, false, true, 2, 1, count) == 1, "...whatever was showing");

        // Recreation restores a saved page the rebuilt pager still has; anything else is Home. A state saved before
        // #223 on Widgets (4) or the voice page (5) comes back on Home, not on the last app page.
        expect(HomePages.restore(3, count) == 3 && HomePages.restore(1, count) == 1, "Restore keeps a page that exists");
        expect(HomePages.restore(4, count) == 0 && HomePages.restore(5, count) == 0 && HomePages.restore(9, count) == 0
                && HomePages.restore(-1, count) == 0 && HomePages.restore(2, 0) == 0, "A page that no longer exists restores to Home");
        // Gesture ownership (LauncherPager): inside the slop nobody owns the drag; clearly vertical stays with the
        // child (a scrolling list); clearly horizontal goes to the pager, which cancels the child's tap.
        float slop = 24;
        expect(HomePages.owner(10, 5, slop) == HomePages.UNDECIDED, "A small wobble is still a tap");
        expect(HomePages.owner(5, 40, slop) == HomePages.CHILD, "Vertical stays with the child");
        expect(HomePages.owner(-60, 10, slop) == HomePages.PAGER && HomePages.owner(60, -10, slop) == HomePages.PAGER, "Horizontal pages");
        expect(HomePages.owner(40, 35, slop) == HomePages.UNDECIDED, "A diagonal is not taken from the child until it is clearly horizontal");

        // Settling a released drag: width 1000, fling threshold 1700 px/s, six pages.
        int w = 1000, n = 6; float fling = 1700;
        expect(HomePages.settle(1, -150, 0, w, fling, 1, n) == 1, "A partial drag springs back");
        expect(HomePages.settle(1, -250, 0, w, fling, 1, n) == 2, "Past a fifth of the width turns the page");
        expect(HomePages.settle(1, 250, 0, w, fling, 1, n) == 0, "...either way");
        expect(HomePages.settle(1, -50, -3000, w, fling, 1, n) == 2, "A short fling turns the page");
        // The demonstrated defect: a drag that reverses at the end. Before, distance won, so 30% left then a fling
        // right still turned to the next page, against the finger. Now the release direction decides.
        expect(HomePages.settle(1, -300, 3000, w, fling, 1, n) == 0, "A reversed fling goes where the finger last went");
        expect(HomePages.settle(1, 300, -3000, w, fling, 1, n) == 2, "...in both directions");
        // Never past the ends, never more than one page from the start, even if scrolled further.
        expect(HomePages.settle(0, 400, 0, w, fling, 0, n) == 0 && HomePages.settle(5, -400, 0, w, fling, 5, n) == 5, "Ends hold");
        expect(HomePages.settle(1, -80, 0, w, fling, 4, n) == 2, "A drag that scrolled far still settles one page away");
        expect(HomePages.settle(2, 0, 0, w, fling, 2, 1) == 0, "A pager with one page always settles on it");
        System.out.println("HomePagesTest: " + checks + " checks passed");
    }
}
