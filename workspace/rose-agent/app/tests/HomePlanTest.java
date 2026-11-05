package dev.pixel.voice;

import java.util.ArrayList;
import java.util.List;

/** F01 (Home page jitter): the re-plan rule (HomePlan) against the rule it replaces, on a simulated Home whose pages
 * report a transient size A and then the final size B in every build. Sizes are a 1080x2424px, 2.625x phone: A is the
 * Home grid without the edge's bottom reserve, B with it, and the inventory fills Home at B but not at A, so the plan
 * decides between one page and two. The phone's actual transient is not reproduced here. Host JVM only. */
public final class HomePlanTest {
    static int checks;
    static void expect(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }

    static final float D = 2.625f;
    static final int W = 954, A_H = 2122, B_H = 1712, C_W = 2130, C_H = 600; // C: the same phone in landscape
    static final float CELL = 155;
    static final int CURATED = 7, INVENTORY = 33;

    /** Home's pages for a capacity: Home, then the app pages for what it cannot hold (the System folder is a cell). */
    static int pages(int capacity) {
        int[] share = HomeAppLayout.homeShare(CURATED, true, INVENTORY, capacity);
        int overflow = INVENTORY - share[1];
        return 1 + (overflow > 0 ? HomeAppLayout.pages(overflow, 24) : 0);
    }

    /** The rule this replaces (MainActivity.homeMeasured before F01), with its stored size and plan. */
    static final class OldHome {
        int columns, capacity, storedW = -1, storedH = -1; float storedC = -1;
        OldHome(int w, int h) { plan(w, h); }
        void plan(int w, int h) { columns = HomeAppLayout.homeColumns(w / D); capacity = HomeAppLayout.homeCapacity(w, h, CELL, D); }
        /** Returns whether this report asked for a rebuild. */
        boolean measured(int w, int h, float c) {
            if (columns == HomeAppLayout.homeColumns(w / D) && capacity == HomeAppLayout.homeCapacity(w, h, c, D)) return false;
            if (w == storedW && h == storedH && c == storedC) return false;
            storedW = w; storedH = h; storedC = c;
            return true;
        }
        void rebuild() { plan(storedW, storedH); }
    }

    /** Home with HomePlan: every report recorded; a settled check re-plans only a stale plan the Breaker allows. */
    static final class NewHome {
        int columns, capacity, rebuilds;
        final HomePlan.Viewport home = new HomePlan.Viewport();
        final HomePlan.Breaker breaker = new HomePlan.Breaker();
        NewHome(int w, int h) { plan(w, h); }
        void plan(int w, int h) { columns = HomeAppLayout.homeColumns(w / D); capacity = HomeAppLayout.homeCapacity(w, h, CELL, D); }
        String key() { return HomePlan.key(columns, capacity, 4, 24); }
        /** The settle check: returns whether Home rebuilt. */
        boolean settle(long now) {
            if (!HomePlan.homeStale(columns, capacity, home, D)) return false;
            int c = HomeAppLayout.homeColumns(home.width / D), cap = HomeAppLayout.homeCapacity(home.width, home.height, home.cell, D);
            if (!breaker.allow(key(), HomePlan.key(c, cap, 4, 24), now)) return false;
            columns = c; capacity = cap; rebuilds++;
            return true;
        }
    }

    /** Runs builds whose pages report the given sizes gapMs apart, settling SETTLE_MS after each report; returns the
     * page count of every build until one does not rebuild (or maxBuilds). */
    static List<Integer> run(NewHome h, int[][] reports, long gapMs, long start, int maxBuilds) {
        List<Integer> counts = new ArrayList<>();
        long t = start;
        for (int b = 0; b < maxBuilds; b++) {
            counts.add(pages(h.capacity));
            boolean rebuilt = false;
            long settleAt = Long.MIN_VALUE;
            for (int[] r : reports) {
                // A settle check due before this report arrives runs first (the report was not yet measured).
                if (settleAt != Long.MIN_VALUE && settleAt <= t) { rebuilt = h.settle(settleAt); if (rebuilt) break; }
                h.home.record(r[0], r[1], CELL);
                settleAt = t + HomePlan.SETTLE_MS;
                t += gapMs;
            }
            if (!rebuilt) rebuilt = h.settle(settleAt);
            t = Math.max(t, settleAt) + 16;
            if (!rebuilt) break;
        }
        return counts;
    }

    public static void main(String[] args) {
        int capA = HomeAppLayout.homeCapacity(W, A_H, CELL, D), capB = HomeAppLayout.homeCapacity(W, B_H, CELL, D);
        expect(capA != capB, "A and B hold different numbers of cells: " + capA + " vs " + capB);
        expect(pages(capA) == 1 && pages(capB) == 2, "at A Home holds everything; at B there is a second page");

        // ---- The demonstrated mechanism: the old rule never settles when every build reports A then B. ----
        OldHome old = new OldHome(W, B_H);
        List<Integer> oldCounts = new ArrayList<>();
        for (int build = 0; build < 8; build++) {
            oldCounts.add(pages(old.capacity));
            boolean rebuild = old.measured(W, A_H, CELL);
            rebuild |= old.measured(W, B_H, CELL);
            if (!rebuild) break;
            old.rebuild();
        }
        expect(oldCounts.size() == 8, "the old rule was still rebuilding after 8 builds: " + oldCounts);
        for (int i = 1; i < oldCounts.size(); i++) expect(!oldCounts.get(i).equals(oldCounts.get(i - 1)),
            "the old rule flips between one and two pages every build: " + oldCounts);

        // ---- The new rule, same reports (A then B a frame apart): no rebuild, two pages throughout. ----
        int[][] ab = {{W, A_H}, {W, B_H}};
        NewHome n = new NewHome(W, B_H);
        List<Integer> counts = run(n, ab, 16, 0, 8);
        expect(counts.size() == 1 && counts.get(0) == 2 && n.rebuilds == 0, "settles at once on B's two pages: " + counts);

        // A first plan from a wrong estimate (A-like) corrects once to the real viewport, then stays.
        NewHome e = new NewHome(W, A_H);
        counts = run(e, ab, 16, 0, 8);
        expect(e.rebuilds == 1 && counts.equals(java.util.Arrays.asList(1, 2)), "one correction from the estimate: " + counts);

        // The legitimate second page is kept: the settled plan is B's, which needs it.
        expect(pages(e.capacity) == 2, "the second page is real at B and stays");

        // Consistent reversed order (B then A): the latest real viewport wins and stays.
        NewHome r = new NewHome(W, B_H);
        counts = run(r, new int[][]{{W, B_H}, {W, A_H}}, 16, 0, 8);
        expect(r.rebuilds == 1 && pages(r.capacity) == 1, "the latest viewport (A) is planned once: " + counts);

        // Reports too far apart for the settle window: at most one flip, then the Breaker holds Home still.
        NewHome s = new NewHome(W, B_H);
        counts = run(s, ab, HomePlan.SETTLE_MS + 150, 0, 8);
        expect(s.rebuilds <= 1, "a slow A/B pair moves Home at most once: " + counts + " rebuilds " + s.rebuilds);

        // A real change (rotation to C) re-plans once; returning later re-plans once again.
        NewHome rot = new NewHome(W, B_H);
        run(rot, ab, 16, 0, 8);
        counts = run(rot, new int[][]{{C_W, C_H}}, 16, 10_000, 8);
        expect(rot.rebuilds == 1 && rot.columns == HomeAppLayout.homeColumns(C_W / D), "rotation re-plans once: " + counts);
        counts = run(rot, ab, 16, 30_000, 8);
        expect(rot.rebuilds == 2 && pages(rot.capacity) == 2, "rotating back long after re-plans again: " + counts);

        // ---- Pieces. ----
        HomePlan.Viewport v = new HomePlan.Viewport();
        expect(!HomePlan.homeStale(4, 1, v, D) && !HomePlan.appsStale(4, 1, v, D), "an unmeasured viewport never re-plans");
        v.record(W, B_H, CELL);
        expect(!HomePlan.homeStale(HomeAppLayout.homeColumns(W / D), capB, v, D), "a plan that fits is not stale");
        expect(HomePlan.homeStale(HomeAppLayout.homeColumns(W / D), capA, v, D), "a plan for another capacity is stale");
        int appCols = HomeAppLayout.columns(W / D), perPage = HomeAppLayout.perPage(W, B_H, 200, D);
        HomePlan.Viewport apps = new HomePlan.Viewport();
        apps.record(W, B_H, 200);
        expect(!HomePlan.appsStale(appCols, perPage, apps, D) && HomePlan.appsStale(appCols, perPage + 4, apps, D), "app pages likewise");
        HomePlan.Breaker b = new HomePlan.Breaker();
        expect(!b.allow("p", "p", 0), "the same plan is never a move");
        expect(b.allow("p", "q", 0), "a new plan is allowed");
        expect(!b.allow("q", "p", HomePlan.FLIP_MS - 1), "flipping straight back is refused");
        expect(b.allow("q", "r", 10), "a third plan is allowed");
        expect(b.allow("r", "q", 20 + HomePlan.FLIP_MS), "returning to a plan after FLIP_MS is allowed");
        System.out.println("HomePlanTest: " + checks + " checks passed");
    }
}
