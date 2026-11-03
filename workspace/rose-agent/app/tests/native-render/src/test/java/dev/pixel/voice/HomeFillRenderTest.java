package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.GradientDrawable;
import android.view.View;
import android.widget.FrameLayout;
import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * #410 owner density correction (comment 6592423187, superseding the reference's two bottom rows): Home and the app
 * pages fill the measured safe area with real-app cells, top down to the reserve above the dots, at phone size (the
 * reference's 384x832dp at mdpi, so one dp is one pixel). Production pieces only: HomeAppPage, HomeAppLayout,
 * HomeAppTile (labelled app cells, unlabelled Home cells), the dots and the edge from HomeOxbloodRenderTest's harness.
 * Synthetic app icons stand in for installed apps' own icons; names are synthetic. Host-native renders, not a device.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class HomeFillRenderTest {
    static final int W = 384, H = 832;
    static Context context() { return RuntimeEnvironment.getApplication(); }

    @After public void normalFont() { RuntimeEnvironment.setFontScale(1f); }

    /** A synthetic installed app's own icon (a coloured rounded square). */
    static GradientDrawable icon(int i) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(0xff000000 | ((i * 0x3a5f91) & 0xffffff) | 0x404040);
        g.setCornerRadius(10);
        return g;
    }
    static View appCell(int i) {
        View cell = HomeAppTile.tile(context(), icon(i), "App " + (i + 1), false, true);
        cell.setTag("app-" + i);
        return cell;
    }
    static View homeCell(int i) {
        View cell = HomeAppTile.tile(context(), icon(i), "App " + (i + 1), false, false);
        cell.setTag("app-" + i);
        return cell;
    }

    /** An app page as MainActivity builds it, inside the frame's dots and edge, with navInset of tappable navigation. */
    static FrameLayout appPage(int apps, int navInset, int w, int h) {
        Context c = context();
        FrameLayout root = HomeOxbloodRenderTest.home(new View[0], 1, navInset);
        root.removeViewAt(1); // app pages have no date
        HomeAppPage grid = (HomeAppPage) root.getChildAt(0);
        root.removeView(grid);
        int columns = HomeAppLayout.columns((w - 2f * HomeAppLayout.MARGIN_DP));
        HomeAppPage page = new HomeAppPage(c, columns, null);
        for (int i = 0; i < apps; i++) page.addView(appCell(i));
        page.setPadding(HomeAppLayout.MARGIN_DP, HomeAppLayout.TOP_DP + 24, HomeAppLayout.MARGIN_DP,
            HomeAppLayout.EDGE_DP + navInset + HomeAppLayout.BAND_BOTTOM_DP);
        root.addView(page, 0, new FrameLayout.LayoutParams(-1, -1));
        return root;
    }

    static Bitmap draw(View v, int w, int h) {
        v.measure(View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(h, View.MeasureSpec.EXACTLY));
        v.layout(0, 0, w, h);
        Bitmap b = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        v.draw(new Canvas(b));
        return b;
    }

    /** The planned capacity of a measured page (what MainActivity's re-plan computes from it). */
    static int capacity(HomeAppPage page) {
        int inner = page.getHeight() - page.getPaddingTop() - page.getPaddingBottom();
        return page.columns * HomeAppLayout.rows(inner, page.cellHeight, 1f);
    }

    /** Every cell inside the page's safe area, no two overlapping, and the grid clear of the dots and the edge. */
    static void inside(HomeAppPage page, FrameLayout root, int h, int navInset, String what) {
        int safeBottom = page.getHeight() - page.getPaddingBottom();
        int dotsTop = h - (OxbloodEdge.HEIGHT_DP + navInset + HomeAppLayout.DOTS_ABOVE_EDGE_DP + HomeAppLayout.DOTS_DP / 2);
        for (int i = 0; i < page.getChildCount(); i++) {
            View a = page.getChildAt(i);
            assertTrue(what + ": cell " + i + " inside the safe area (" + a.getTop() + ".." + a.getBottom() + " of " + page.getPaddingTop() + ".." + safeBottom + ")",
                a.getTop() >= page.getPaddingTop() && a.getBottom() <= safeBottom && a.getLeft() >= 0 && a.getRight() <= page.getWidth());
            assertTrue(what + ": cell " + i + " clear of the dots", a.getBottom() <= dotsTop);
            for (int j = i + 1; j < page.getChildCount(); j++) {
                View b = page.getChildAt(j);
                boolean overlap = a.getLeft() < b.getRight() && b.getLeft() < a.getRight() && a.getTop() < b.getBottom() && b.getTop() < a.getBottom();
                assertTrue(what + ": cells " + i + " and " + j + " overlap", !overlap);
            }
        }
    }

    @Test public void fullAppPageFillsThePhone() throws Exception {
        FrameLayout probe = appPage(1, 0, W, H);
        draw(probe, W, H);
        int full = capacity((HomeAppPage) probe.getChildAt(0));
        assertTrue("more than the reference's two rows of four: " + full, full > 8);
        FrameLayout root = appPage(full, 0, W, H);
        Bitmap b = draw(root, W, H);
        HomeOxbloodRenderTest.save(b, "home-fill-app-page-full-384x832");
        HomeAppPage page = (HomeAppPage) root.getChildAt(0);
        inside(page, root, H, 0, "full app page");
        View last = page.getChildAt(full - 1);
        int spare = page.getHeight() - page.getPaddingBottom() - last.getBottom();
        assertTrue("a full page reaches down to the reserve (" + spare + "dp left)", spare < page.cellHeight);
        assertEquals("first row at the page top", page.getPaddingTop(), page.getChildAt(0).getTop());
        // Overflow: one more app does not fit on this page; MainActivity puts it on the next (HomeAppLayout.slice).
        assertEquals(2, HomeAppLayout.pages(full + 1, full));
    }

    @Test public void partialLastPageKeepsTheSameRowsFromTheTop() throws Exception {
        FrameLayout fullRoot = appPage(1, 0, W, H);
        draw(fullRoot, W, H);
        int full = capacity((HomeAppPage) fullRoot.getChildAt(0));
        FrameLayout a = appPage(full, 0, W, H), p = appPage(5, 0, W, H);
        draw(a, W, H);
        Bitmap b = draw(p, W, H);
        HomeOxbloodRenderTest.save(b, "home-fill-app-page-partial-384x832");
        HomeAppPage fullPage = (HomeAppPage) a.getChildAt(0), partial = (HomeAppPage) p.getChildAt(0);
        for (int i = 0; i < 5; i++) {
            assertEquals("cell " + i + " top as on a full page", fullPage.getChildAt(i).getTop(), partial.getChildAt(i).getTop());
            assertEquals("cell " + i + " left as on a full page", fullPage.getChildAt(i).getLeft(), partial.getChildAt(i).getLeft());
        }
        inside(partial, p, H, 0, "partial page");
    }

    @Test public void homeFillsBelowTheDateWithTheFolderLast() throws Exception {
        // Home: seven curated apps (glyph marks), then inventory apps, then the System folder in the last cell.
        String[] curated = {"phone", "messages", "camera", "compass", "mail", "calendar", "notes"};
        FrameLayout probe = HomeOxbloodRenderTest.home(new String[]{"phone"}, 0);
        draw(probe, W, H);
        HomeAppPage probeGrid = (HomeAppPage) probe.getChildAt(0);
        int capacity = capacity(probeGrid);
        int[] share = HomeAppLayout.homeShare(curated.length, true, 40, capacity);
        assertEquals("all curated kept", curated.length, share[0]);
        View[] cells = new View[share[0] + share[1] + 1];
        for (int i = 0; i < share[0]; i++) cells[i] = HomeAppTile.tile(context(), new HomeGlyph(curated[i]), curated[i], true, false);
        for (int i = 0; i < share[1]; i++) cells[share[0] + i] = homeCell(i);
        cells[cells.length - 1] = HomeAppTile.tile(context(), new HomeGlyph("system"), "System", true, false);
        assertEquals("Home is full", capacity, cells.length);
        assertTrue("more than the reference's eight: " + capacity, capacity > 8);
        FrameLayout root = HomeOxbloodRenderTest.home(cells, 0, 0);
        Bitmap b = draw(root, W, H);
        HomeOxbloodRenderTest.save(b, "home-fill-home-full-384x832");
        HomeAppPage grid = (HomeAppPage) root.getChildAt(0);
        inside(grid, root, H, 0, "full Home");
        View date = root.getChildAt(1);
        assertTrue("the grid starts below the date", grid.getChildAt(0).getTop() >= date.getBottom() + HomeAppLayout.BELOW_DATE_DP - 1);
        assertEquals("the System folder is the last cell", "System", grid.getChildAt(grid.getChildCount() - 1).getContentDescription());
        View last = grid.getChildAt(grid.getChildCount() - 1);
        assertTrue("Home reaches down to the reserve", grid.getHeight() - grid.getPaddingBottom() - last.getBottom() < grid.cellHeight + 1);
    }

    @Test public void largeFontMeansFewerRowsNeverOverlap() throws Exception {
        FrameLayout normal = appPage(1, 0, W, H);
        draw(normal, W, H);
        int normalCapacity = capacity((HomeAppPage) normal.getChildAt(0));
        RuntimeEnvironment.setFontScale(2f);
        FrameLayout probe = appPage(1, 0, W, H);
        draw(probe, W, H);
        HomeAppPage probePage = (HomeAppPage) probe.getChildAt(0);
        int large = capacity(probePage);
        assertTrue("200% text: fewer cells (" + large + " < " + normalCapacity + ")", large < normalCapacity && large >= probePage.columns);
        FrameLayout root = appPage(large, 0, W, H);
        Bitmap b = draw(root, W, H);
        HomeOxbloodRenderTest.save(b, "home-fill-app-page-font200-384x832");
        inside((HomeAppPage) root.getChildAt(0), root, H, 0, "200% text page");
    }

    @Test public void threeButtonNavigationKeepsTheGridClear() throws Exception {
        int nav = 48;
        FrameLayout probe = appPage(1, nav, W, H);
        draw(probe, W, H);
        int full = capacity((HomeAppPage) probe.getChildAt(0));
        FrameLayout root = appPage(full, nav, W, H);
        Bitmap b = draw(root, W, H);
        HomeOxbloodRenderTest.save(b, "home-fill-app-page-3button-384x832");
        inside((HomeAppPage) root.getChildAt(0), root, H, nav, "three-button navigation");
    }

    @Test @Config(qualifiers = "w832dp-h384dp-mdpi") public void landscapeHomeKeepsTheCuratedAppsAndTheFolder() throws Exception {
        int w = 832, h = 384, columns = HomeAppLayout.homeColumns(w - 2f * HomeAppLayout.MARGIN_DP);
        assertEquals("eight Home columns across a landscape phone", 8, columns);
        String[] curated = {"phone", "messages", "camera", "compass", "mail", "calendar", "notes"};
        View[] cells = new View[curated.length + 1];
        for (int i = 0; i < curated.length; i++) cells[i] = HomeAppTile.tile(context(), new HomeGlyph(curated[i]), curated[i], true, false);
        cells[curated.length] = HomeAppTile.tile(context(), new HomeGlyph("system"), "System", true, false);
        FrameLayout root = HomeOxbloodRenderTest.home(cells, 0, 0, columns);
        Bitmap b = draw(root, w, h);
        HomeOxbloodRenderTest.save(b, "home-fill-home-landscape-832x384");
        HomeAppPage grid = (HomeAppPage) root.getChildAt(0);
        int[] share = HomeAppLayout.homeShare(curated.length, true, 20, capacity(grid));
        assertEquals("every curated app stays on landscape Home", curated.length, share[0]);
        inside(grid, root, h, 0, "landscape Home");
        assertEquals("System", grid.getChildAt(grid.getChildCount() - 1).getContentDescription());
    }

    @Test @Config(qualifiers = "w832dp-h384dp-mdpi") public void landscapeUsesMoreColumnsAndFewerRows() throws Exception {
        int w = 832, h = 384;
        FrameLayout probe = appPage(1, 0, w, h);
        draw(probe, w, h);
        HomeAppPage probePage = (HomeAppPage) probe.getChildAt(0);
        int full = capacity(probePage);
        assertEquals("six columns across", 6, probePage.columns);
        assertTrue("at least one row: " + full, full >= 6);
        FrameLayout root = appPage(full, 0, w, h);
        Bitmap b = draw(root, w, h);
        HomeOxbloodRenderTest.save(b, "home-fill-app-page-landscape-832x384");
        inside((HomeAppPage) root.getChildAt(0), root, h, 0, "landscape");
    }
}
