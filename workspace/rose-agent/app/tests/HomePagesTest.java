package dev.pixel.voice;

/** OS-HIFI-HOME-001 and Project 2: page order and where Home entries land, as MainActivity uses HomePages. */
public final class HomePagesTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    public static void main(String[] args) {
        // Project 2: the workspace, then Home, Our apps, Preinstalled, Play Store, and nothing after them (#223: no
        // Widgets or voice page).
        int W = HomePages.WORKSPACE, H = HomePages.HOME, count = 5;
        expect(W == 0 && H == 1 && HomePages.FIRST_APPS == 2, "The workspace left of Home, the app pages right after Home");
        expect(HomePages.workspace(W) && !HomePages.workspace(H) && !HomePages.workspace(HomePages.FIRST_APPS), "Only page 0 is the workspace");

        // The change from the old behaviour: HOME from an app or the Rose conversation returns to the page left.
        expect(HomePages.target(true, false, false, 3, 4, count) == 3, "HOME after an app opened from Preinstalled returns to Preinstalled");
        expect(HomePages.target(true, false, false, 4, 3, count) == 4, "...and to the last app page");
        expect(HomePages.target(true, false, false, H, 4, count) == H, "...and to Home");
        expect(HomePages.target(true, false, false, W, 4, count) == W, "...and to the workspace, which never rearranges itself while away");
        // Unchanged: HOME pressed while Home is showing goes to Home, as before, from any page including the workspace.
        expect(HomePages.target(true, false, true, 3, 4, count) == H, "HOME on Home goes to the Home page");
        expect(HomePages.target(true, false, true, W, W, count) == H, "HOME on the workspace goes to Home");
        // Unchanged (PR67): the app icon shows Home, with the conversation opened over it.
        expect(HomePages.target(true, true, false, 4, 4, count) == H, "The app icon lands on Home");
        // A missing or a stale page falls back to Home, never to the workspace.
        expect(HomePages.target(true, false, false, 5, 3, count) == H && HomePages.target(true, false, false, 6, 3, count) == H,
                "HOME never returns to a page past the end");
        expect(HomePages.target(true, false, false, -1, 4, count) == H, "No previous page: Home");
        expect(HomePages.target(true, false, false, 9, 4, count) == H, "A page gone after a rebuild: Home");
        // Unchanged: notifications and entry activities keep the activity's own page.
        expect(HomePages.target(false, false, false, 3, 4, count) == 4, "A non-Home entry keeps the fallback page");
        expect(HomePages.target(false, false, true, 3, W, count) == W, "...whatever was showing, the workspace too");

        // Recreation restores a saved page the rebuilt pager still has; anything else is Home, not the workspace.
        expect(HomePages.restore(4, count) == 4 && HomePages.restore(H, count) == H && HomePages.restore(W, count) == W, "Restore keeps a page that exists");
        expect(HomePages.restore(5, count) == H && HomePages.restore(9, count) == H && HomePages.restore(-1, count) == H
                && HomePages.restore(2, 0) == H, "A page that no longer exists restores to Home");
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
        expect(HomePages.settle(1, 250, 0, w, fling, 1, n) == 0, "...either way: from Home to the workspace");
        expect(HomePages.settle(1, -50, -3000, w, fling, 1, n) == 2, "A short fling turns the page");
        // The demonstrated defect: a drag that reverses at the end. Before, distance won, so 30% left then a fling
        // right still turned to the next page, against the finger. Now the release direction decides.
        expect(HomePages.settle(1, -300, 3000, w, fling, 1, n) == 0, "A reversed fling goes where the finger last went");
        expect(HomePages.settle(1, 300, -3000, w, fling, 1, n) == 2, "...in both directions");
        // Never past the ends (going further right than the workspace holds), never more than one page from the start.
        expect(HomePages.settle(0, 400, 0, w, fling, 0, n) == 0 && HomePages.settle(5, -400, 0, w, fling, 5, n) == 5, "Ends hold");
        expect(HomePages.settle(0, 400, 3000, w, fling, 0, n) == W, "A fling right on the workspace stays on it");
        expect(HomePages.settle(1, -80, 0, w, fling, 4, n) == 2, "A drag that scrolled far still settles one page away");
        expect(HomePages.settle(2, 0, 0, w, fling, 2, 1) == 0, "A pager with one page always settles on it");
        System.out.println("HomePagesTest: " + checks + " checks passed");
    }
}
