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
    static float cell(float fontScale) { return HomeAppLayout.cellHeight(D, line(11.5f, fontScale)); }
    static float homeCellPx() { return HomeAppLayout.TILE_DP * D; }

    /** Every cell of a page, laid out the way HomeAppPage does, stays inside the page. */
    static void fits(int apps, int columns, float height, float cell, String message) {
        int used = HomeAppLayout.usedRows(apps, columns);
        float grid = HomeAppLayout.gridHeight(used, cell, D);
        float top = HomeAppLayout.gridTop(height, grid, D), bottom = top + grid;
        expect(top >= 0 && bottom <= height + 0.5f, message + ": rows " + used + " top " + top + " bottom " + bottom + " of " + height);
    }

    public static void main(String[] args) {
        // Columns follow the inset width only: four on a phone like the reference, never fewer for a long label.
        expect(HomeAppLayout.columns(412 - 48) == 4, "A 412dp phone (less the margins) has four columns");
        expect(HomeAppLayout.columns(360 - 48) == 4, "A 360dp phone keeps four (4 x 72 + 3 x 6 = 306)");
        expect(HomeAppLayout.columns(280 - 48) == 3, "Only a very narrow screen drops to three");
        expect(HomeAppLayout.columns(673 - 48) == 5 && HomeAppLayout.columns(900) == 6, "Wide screens add columns, at most six");
        expect(HomeAppLayout.homeColumns(384 - 48) == 4 && HomeAppLayout.homeColumns(884 - 48) == 4, "Home is four across (#410), unfolded too");
        expect(HomeAppLayout.homeColumns(280 - 48) == 3, "a very narrow Home drops to three (4 x 60 + 3 x 17.5 = 292.5)");
        expect(cell(1f) == (HomeAppLayout.TILE_DP + HomeAppLayout.LABEL_GAP_DP) * D + 2 * line(11.5f, 1f), "The cell is two label lines, whatever the label's length");

        // #410 reference geometry (384x832 at 1dp = 1px): the page area runs from the top margin (24) to the band's
        // bottom, the edge (92) plus 95 above the screen bottom = 645. Cells are 59 + 5 + two 14px label lines = 92 tall
        // on the app pages (one-line labels in the reference make 78) and 59 on Home.
        float refHeight = 645 - 24, refLine = 14;
        float appCell = HomeAppLayout.cellHeight(1f, refLine), homeCell = HomeAppLayout.TILE_DP;
        expect(HomeAppLayout.rows(refHeight, appCell, 1f) == 2, "two rows of app cells, as in the reference");
        float homeTop = 24 + HomeAppLayout.gridTop(refHeight, HomeAppLayout.gridHeight(2, homeCell, 1f), 1f);
        expect(homeTop == 475, "Home's grid starts at the reference 475: " + homeTop);
        expect(homeTop + homeCell + HomeAppLayout.rowGap(1f) == 548, "Home's second row at the reference 548");
        float oneLineCell = HomeAppLayout.TILE_DP + HomeAppLayout.LABEL_GAP_DP + refLine; // the reference's one-line 78
        expect(24 + HomeAppLayout.gridTop(refHeight, HomeAppLayout.gridHeight(2, oneLineCell, 1f), 1f) == 475,
            "the reference app grid (78px cells) starts at the same 475, its rows at 475 and 567");
        float twoLineTop = 24 + HomeAppLayout.gridTop(refHeight, HomeAppLayout.gridHeight(2, appCell, 1f), 1f);
        expect(twoLineTop < 475 && twoLineTop + HomeAppLayout.gridHeight(2, appCell, 1f) == 645,
            "a taller grid (two label lines) grows upward and keeps its bottom on the band: " + twoLineTop);
        expect(HomeAppLayout.gridLeft(384 - 48, 4, 60, 17.5f) + 24 == 45.75f, "Home columns start at the reference 45.75");
        expect(HomeAppLayout.gridLeft(384 - 48, 4, 72, 6) + 24 == 39f, "app columns start at the reference 39");

        // Normal phone: the measured area after the status bar, the band and the edge.
        float width = (412 - 48) * D, height = 560 * D;
        int perPage = HomeAppLayout.perPage(width, height, cell(1f), D);
        expect(perPage == 8, "A normal page holds the reference's two rows of four: " + perPage);
        fits(perPage, 4, height, cell(1f), "A full normal page fits");

        // Short (landscape or split screen): one row, never zero, still inside the page.
        float shortHeight = 150 * D;
        int shortPage = HomeAppLayout.perPage(width, shortHeight, cell(1f), D);
        expect(shortPage == 4, "A short page holds one row: " + shortPage);
        fits(shortPage, 4, shortHeight, cell(1f), "A one-row short page fits");
        expect(HomeAppLayout.rows(10 * D, cell(1f), D) == 1, "Even a tiny page keeps one row rather than none");

        // Large text: taller cells, never more than two rows, nothing clipped; where two do not fit, one.
        int large = HomeAppLayout.perPage(width, height, cell(2f), D);
        expect(large <= perPage && large % 4 == 0, "200% text keeps whole rows of the same four columns: " + large);
        fits(large, 4, height, cell(2f), "A large-text page fits");
        expect(HomeAppLayout.perPage(width, 200 * D, cell(2f), D) == 4, "200% text on a short page: one row");

        // Wide (unfolded): more columns, every cell still inside.
        float wide = (884 - 48) * D, wideHeight = 620 * D;
        int widePage = HomeAppLayout.perPage(wide, wideHeight, cell(1f), D);
        expect(widePage == 12, "An unfolded page uses six columns of two rows: " + widePage);
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
        // A one-row page starts at the same line as a two-row one: the band, not the content, sets the top.
        expect(HomeAppLayout.gridTop(height, HomeAppLayout.gridHeight(1, cell(1f), D), D)
            == HomeAppLayout.gridTop(height, HomeAppLayout.gridHeight(2, homeCellPx(), D), D), "rows start on the band's line");

        // Catalog change: an install that crosses a page boundary adds a page; an uninstall removes it.
        expect(HomeAppLayout.pages(perPage + 1, perPage) == 2 && HomeAppLayout.pages(perPage - 1, perPage) == 1,
            "The page count tracks installs and removals");

        // Re-measure: the first build uses an estimate; a page measured at another capacity re-plans once.
        int columns = HomeAppLayout.columns(width / D);
        expect(!HomeAppLayout.replan(columns, perPage, width, height, cell(1f), D), "A matching measurement keeps the pages");
        expect(HomeAppLayout.replan(columns, perPage, width, shortHeight, cell(1f), D), "A shorter real area re-plans");
        expect(HomeAppLayout.replan(columns, perPage, wide, height, cell(1f), D), "A wider real area re-plans");
        float estimate = HomeAppLayout.estimatedHeightDp(915);
        expect(estimate > 0 && estimate < 915 - 92 - 95, "The estimate leaves room for the bars, the band and the edge: " + estimate);

        // Accessible names replace the old visible headings.
        expect(HomeAppLayout.title(0, 1).equals("Apps") && HomeAppLayout.title(1, 3).equals("Apps, page 2 of 3"), "Page names");

        // Wallpaper (APP-OS-VISUAL-1030): CSS background-size: cover at a background-position, as the HiFi .wall
        // (62% 30%) and .ivory-bg::after (80% 20%) draw the 1024x1536 HiFi image.
        for (float[] area : new float[][]{{1080, 2400}, {720, 1600}, {1440, 3120}, {2208, 1840}, {1080, 1080}, {2400, 1080}}) {
            int[] crop = HomeAppLayout.cover(1024, 1536, area[0], area[1], HomeAppLayout.WALL_X, HomeAppLayout.WALL_Y);
            int w = crop[2] - crop[0], h = crop[3] - crop[1];
            expect(crop[0] >= 0 && crop[1] >= 0 && crop[2] <= 1024 && crop[3] <= 1536 && w > 0 && h > 0,
                "The crop stays inside the image at " + area[0] + "x" + area[1]);
            expect(Math.abs(w / (float) h - area[0] / area[1]) < 0.01f, "The crop keeps the screen's aspect, no stretch: " + w + "x" + h);
            expect(w == 1024 || h == 1536, "Cover fills one whole image dimension");
        }
        int[] phone = HomeAppLayout.cover(1024, 1536, 1080, 2400, .62f, .30f);
        expect(phone[1] == 0 && phone[3] == 1536 && phone[2] - phone[0] == 691, "A portrait phone takes a full-height 691-wide band");
        expect(phone[0] == Math.round((1024 - 691) * .62f), "positioned 62% across the spare width, as CSS does: " + phone[0]);
        int[] centred = HomeAppLayout.cover(1024, 1536, 1080, 2400, .5f, .5f);
        expect(Math.abs(centred[0] - (1024 - 691) / 2f) <= 0.5f, "50% 50% is a centred crop");
        expect(phone[0] > centred[0], "62% shows more of the right of the image than centring");
        int[] wideScreen = HomeAppLayout.cover(1024, 1536, 2400, 1080, .62f, .30f);
        int bandH = wideScreen[3] - wideScreen[1];
        expect(wideScreen[0] == 0 && wideScreen[2] == 1024 && wideScreen[1] == Math.round((1536 - bandH) * .30f),
            "A wide screen takes a full-width band 30% down");
        int[] square = HomeAppLayout.cover(1024, 1536, 1000, 1000, .80f, .20f);
        expect(square[0] == 0 && square[2] == 1024 && square[1] == Math.round((1536 - 1024) * .20f), "The reading surface's 80% 20%");
        int[] none = HomeAppLayout.cover(0, 0, 1080, 2400, .62f, .30f);
        expect(none[2] == 0 && none[3] == 0, "No bitmap: an empty crop");
        expect(HomeAppLayout.cover(1024, 1536, 0, 100, .62f, .30f)[2] == 0, "An empty area: an empty crop");

        System.out.println("HomeAppLayoutTest: " + checks + " checks passed");
    }
}
