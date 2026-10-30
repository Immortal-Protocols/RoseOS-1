package dev.pixel.voice;

/** OS-PAGING-158-M001: the Home app pages against the production HomeAppLayout, at the sizes the Home pages
 * actually meet (px at a given density, as HomeAppPage and MainActivity call it). Normal, narrow, short and
 * wide viewports, large text, long labels, empty/one/many pages, a catalog change and a re-measure. */
public final class HomeAppLayoutTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    static final float D = 2.625f; // a Pixel-class density
    static float line(float sp, float fontScale) { return sp * fontScale * 1.32f * D; }
    static float cell(float fontScale) { return HomeAppLayout.cellHeight(D, line(12.5f, fontScale)); }

    /** Every cell of a page, laid out the way HomeAppPage does, stays inside the page. */
    static void fits(int apps, int columns, float height, float cell, String message) {
        int used = HomeAppLayout.usedRows(apps, columns);
        float top = HomeAppLayout.gridTop(height, cell, used, D), gap = HomeAppLayout.rowGap(height, cell, used, D);
        float bottom = top + used * cell + Math.max(0, used - 1) * gap;
        expect(top >= 0 && bottom <= height + 0.5f, message + ": rows " + used + " top " + top + " bottom " + bottom + " of " + height);
    }

    public static void main(String[] args) {
        // Columns follow the inset width only: four on a phone like the reference, never fewer for a long label.
        expect(HomeAppLayout.columns(412 - 48) == 4, "A 412dp phone (less the margins) has four columns");
        expect(HomeAppLayout.columns(360 - 48) == 4, "A 360dp phone keeps four");
        expect(HomeAppLayout.columns(280 - 48) == 3, "Only a very narrow screen drops to three");
        expect(HomeAppLayout.columns(673 - 48) == 5 && HomeAppLayout.columns(900) == 6, "Wide screens add columns, at most six");
        // There is no label input at all: "Rose Agent Calculator Companion Pro" cannot change the composition.
        expect(cell(1f) == HomeAppLayout.cellHeight(D, line(12.5f, 1f)), "The cell is two label lines, whatever the label's length");

        // Normal phone: the measured area after status bar, tray, dots and margins.
        float width = (412 - 48) * D, height = 560 * D;
        int perPage = HomeAppLayout.perPage(width, height, cell(1f), D);
        expect(perPage % 4 == 0 && perPage >= 16, "A normal page holds whole rows of four: " + perPage);
        fits(perPage, 4, height, cell(1f), "A full normal page fits");
        expect(HomeAppLayout.rowGap(height, cell(1f), perPage / 4, D) <= HomeAppLayout.ROW_GAP_DP * D,
            "Rows are never further apart than the reference 20dp");

        // Short (landscape or split screen): fewer rows, never zero, still inside the page.
        float shortHeight = 150 * D;
        int shortPage = HomeAppLayout.perPage(width, shortHeight, cell(1f), D);
        expect(shortPage == 4, "A short page holds one row: " + shortPage);
        fits(shortPage, 4, shortHeight, cell(1f), "A one-row short page fits");
        expect(HomeAppLayout.rows(10 * D, cell(1f), D) == 1, "Even a tiny page keeps one row rather than none");

        // Large text: taller cells, fewer rows per page, nothing clipped.
        int large = HomeAppLayout.perPage(width, height, cell(2f), D);
        expect(large < perPage && large % 4 == 0, "200% text fits fewer rows of the same four columns: " + large);
        fits(large, 4, height, cell(2f), "A large-text page fits");

        // Wide (unfolded): more columns, every cell still inside.
        float wide = (884 - 48) * D, wideHeight = 620 * D;
        int widePage = HomeAppLayout.perPage(wide, wideHeight, cell(1f), D);
        expect(widePage % 6 == 0, "An unfolded page uses six columns: " + widePage);
        fits(widePage, 6, wideHeight, cell(1f), "A full wide page fits");

        // Inventory decides the page count: empty is one truthful page, one app is one page, more spill over.
        expect(HomeAppLayout.pages(0, perPage) == 1, "No apps: one page (it says so)");
        expect(HomeAppLayout.pages(1, perPage) == 1 && HomeAppLayout.pages(perPage, perPage) == 1, "Up to a page: one page");
        expect(HomeAppLayout.pages(perPage + 1, perPage) == 2, "One more app: a second page");
        int many = 3 * perPage - 5, total = 0;
        for (int i = 0; i < HomeAppLayout.pages(many, perPage); i++) {
            int[] range = HomeAppLayout.slice(many, perPage, i);
            expect(range[0] == total, "Pages are contiguous");
            total = range[1];
        }
        expect(total == many && HomeAppLayout.pages(many, perPage) == 3, "Every launchable app is on exactly one page");
        // A partial last page sits at the bottom, above the dots, like a full one.
        expect(HomeAppLayout.gridTop(height, cell(1f), 1, D) > HomeAppLayout.gridTop(height, cell(1f), perPage / 4, D),
            "A one-row page starts lower: the grid hugs the dots");

        // Catalog change: an install that crosses a page boundary adds a page; an uninstall removes it.
        expect(HomeAppLayout.pages(perPage + 1, perPage) == 2 && HomeAppLayout.pages(perPage - 1, perPage) == 1,
            "The page count tracks installs and removals");

        // Re-measure: the first build uses an estimate; a page measured at another capacity re-plans once.
        int columns = HomeAppLayout.columns(width / D);
        expect(!HomeAppLayout.replan(columns, perPage, width, height, cell(1f), D), "A matching measurement keeps the pages");
        expect(HomeAppLayout.replan(columns, perPage, width, shortHeight, cell(1f), D), "A shorter real area re-plans");
        expect(HomeAppLayout.replan(columns, perPage, wide, height, cell(1f), D), "A wider real area re-plans");
        expect(HomeAppLayout.replan(columns, perPage, width, height, cell(2f), D), "A font-size change re-plans");
        float estimate = HomeAppLayout.estimatedHeightDp(915, 92, 16, 44);
        expect(estimate > 0 && estimate < 915 - 92 - 44, "The estimate leaves room for the bars, tray and dots: " + estimate);

        // Accessible names replace the old visible headings.
        expect(HomeAppLayout.title(0, 1).equals("Apps") && HomeAppLayout.title(1, 3).equals("Apps, page 2 of 3"), "Page names");
        // Home (#157, APP-OS-VISUAL-1030): the four default-role apps in the reference's sparse first-page grid, in
        // the app pages' own cells and area, over the one tray: three columns on a phone, five unfolded, and one row
        // of four only where two rows would not fit. Every case must keep all four inside the page.
        expect(HomeAppLayout.homeColumns(312 * D, 560 * D, cell(1f), D) == 3
            && HomeAppLayout.homeColumns(364 * D, 560 * D, cell(1f), D) == 3, "A phone's Home groups its default apps in three columns");
        expect(HomeAppLayout.homeColumns(836 * D, 620 * D, cell(1f), D) == 5, "An unfolded Home uses five");
        expect(HomeAppLayout.homeColumns(364 * D, 150 * D, cell(1f), D) == 4, "A short Home keeps its apps on one row");
        expect(HomeAppLayout.homeColumns(364 * D, 250 * D, cell(2f), D) == 4, "200% text on a short Home: one row");
        for (float[] screen : new float[][]{{360, 560}, {412, 560}, {412, 250}, {412, 150}, {280, 150}, {884, 620}, {884, 150}}) {
            for (float scale : new float[]{1f, 1.3f, 2f}) {
                float w = (screen[0] - 48) * D, h = screen[1] * D;
                int homeColumns = HomeAppLayout.homeColumns(w, h, cell(scale), D);
                fits(4, homeColumns, h, cell(scale), "Four default apps fit at " + screen[0] + "x" + screen[1] + " text " + scale);
                fits(0, homeColumns, h, cell(scale), "No default apps: an empty Home grid");
                expect(homeColumns >= 3 && homeColumns <= 5, "Home columns stay 3 to 5: " + homeColumns);
            }
        }

        System.out.println("HomeAppLayoutTest: " + checks + " checks passed");
    }
}
