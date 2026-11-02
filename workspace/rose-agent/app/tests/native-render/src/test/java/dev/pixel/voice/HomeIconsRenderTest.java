package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.AdaptiveIconDrawable;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.ShapeDrawable;
import android.graphics.drawable.shapes.OvalShape;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ScrollView;
import java.io.File;
import java.io.FileOutputStream;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * #403, host-rendered: the production simplified glyphs (HomeGlyph) on the production subdued tile (HomeGlassTile), the
 * production cell (HomeAppTile), a Home grid (HomeAppPage) and the "…" System folder (HomeFolder), drawn by Robolectric's
 * native Android graphics at SDK 35 on a 411x914dp xxhdpi screen. Backdrops are fixed colours: the study's Oxblood field
 * (#35070E, for comparison with Rose-07-Home-No-Status) and a light steel blue standing in for the current wallpaper,
 * which this change does not touch. Not a device, launcher activity, compositor or PackageManager proof.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w411dp-h914dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class HomeIconsRenderTest {
    static final File OUT = new File(System.getProperty("render.out", "target/render"));
    static final int OXBLOOD_FIELD = 0xff35070e, LIGHT_WALL = 0xffa9bed6;

    static Context context() { return RuntimeEnvironment.getApplication(); }
    static float density() { return context().getResources().getDisplayMetrics().density; }
    static int px(float dp) { return Math.round(dp * density()); }

    static String save(Bitmap bitmap, String name) throws Exception {
        OUT.mkdirs();
        File file = new File(OUT, name + ".png");
        try (FileOutputStream out = new FileOutputStream(file)) { assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)); }
        StringBuilder hex = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(java.nio.file.Files.readAllBytes(file.toPath()))) hex.append(String.format("%02x", b));
        System.out.println("RENDER " + name + " " + bitmap.getWidth() + "x" + bitmap.getHeight() + " sha256=" + hex);
        return hex.toString();
    }

    static Bitmap draw(View view, int w, int h, int backdrop) {
        view.measure(View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(h, View.MeasureSpec.EXACTLY));
        view.layout(0, 0, w, h);
        Bitmap bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        bitmap.eraseColor(backdrop);
        view.draw(new Canvas(bitmap));
        return bitmap;
    }

    /** The 64dp tile with a mark in its 44dp centre, as HomeAppTile places it. */
    static Bitmap tile(Drawable mark, int backdrop) {
        int side = px(HomeAppLayout.TILE_DP), icon = px(HomeAppLayout.ICON_DP);
        Bitmap bitmap = Bitmap.createBitmap(side, side, Bitmap.Config.ARGB_8888);
        bitmap.eraseColor(backdrop);
        Canvas canvas = new Canvas(bitmap);
        HomeGlassTile glass = new HomeGlassTile(density());
        glass.setBounds(0, 0, side, side);
        glass.draw(canvas);
        int inset = (side - icon) / 2;
        mark.setBounds(inset, inset, inset + icon, inset + icon);
        mark.draw(canvas);
        return bitmap;
    }

    static boolean cream(int c) {
        int r = (c >> 16) & 255, g = (c >> 8) & 255, b = c & 255;
        return r > 200 && g > 185 && b > 165 && r >= b;
    }
    static int creamCount(Bitmap b, int l, int t, int r, int bottom) {
        int n = 0;
        for (int y = t; y < bottom; y++) for (int x = l; x < r; x++) if (cream(b.getPixel(x, y))) n++;
        return n;
    }
    static double luminance(int c) {
        double[] v = {((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0};
        for (int i = 0; i < 3; i++) v[i] = v[i] <= .03928 ? v[i] / 12.92 : Math.pow((v[i] + .055) / 1.055, 2.4);
        return .2126 * v[0] + .7152 * v[1] + .0722 * v[2];
    }
    static double contrast(int a, int b) {
        double la = luminance(a), lb = luminance(b);
        return (Math.max(la, lb) + .05) / (Math.min(la, lb) + .05);
    }
    static String digest(Bitmap b) throws Exception {
        java.nio.ByteBuffer buffer = java.nio.ByteBuffer.allocate(b.getByteCount());
        b.copyPixelsToBuffer(buffer);
        StringBuilder hex = new StringBuilder();
        for (byte x : MessageDigest.getInstance("SHA-256").digest(buffer.array())) hex.append(String.format("%02x", x));
        return hex.toString();
    }

    @Test public void everyGlyphDrawsADistinctCreamMarkInsideItsIconArea() throws Exception {
        int side = px(HomeAppLayout.TILE_DP), icon = px(HomeAppLayout.ICON_DP), inset = (side - icon) / 2;
        HashSet<String> distinct = new HashSet<>();
        int columns = 6, rows = (HomeGlyph.KEYS.length + columns - 1) / columns, gap = px(12);
        Bitmap sheet = Bitmap.createBitmap(columns * (side + gap) + gap, rows * (side + gap) + gap, Bitmap.Config.ARGB_8888);
        sheet.eraseColor(OXBLOOD_FIELD);
        Canvas canvas = new Canvas(sheet);
        for (int i = 0; i < HomeGlyph.KEYS.length; i++) {
            String key = HomeGlyph.KEYS[i];
            assertTrue(key, HomeGlyph.known(key));
            Bitmap b = tile(new HomeGlyph(key), OXBLOOD_FIELD);
            int inside = creamCount(b, inset, inset, inset + icon, inset + icon);
            int all = creamCount(b, 0, 0, side, side);
            assertTrue(key + " draws a visible mark (" + inside + " px)", inside > icon * icon / 60);
            assertEquals(key + " stays inside its 44dp icon area", inside, all);
            assertTrue(key + " is a line mark, not a filled block", inside < icon * icon / 3);
            assertTrue(key + " differs from every other mark", distinct.add(digest(b)));
            canvas.drawBitmap(b, gap + (i % columns) * (side + gap), gap + (i / columns) * (side + gap), null);
        }
        save(sheet, "home-glyphs-sheet");
        // An unknown key draws nothing on the tile.
        Bitmap blank = tile(new HomeGlyph("no-such-app"), OXBLOOD_FIELD);
        assertEquals(0, creamCount(blank, 0, 0, side, side));
        assertFalse(HomeGlyph.known("no-such-app"));
    }

    @Test public void tileIsSubduedAndTheMarkKeepsContrastOnEitherBackdrop() throws Exception {
        int side = px(HomeAppLayout.TILE_DP);
        for (int backdrop : new int[]{OXBLOOD_FIELD, LIGHT_WALL}) {
            Bitmap b = tile(new HomeGlyph("calendar"), backdrop);
            int top = b.getPixel(side / 2, px(6)), lower = b.getPixel(px(10), side - px(10));
            assertTrue("a dark, subdued tile over " + Integer.toHexString(backdrop), luminance(top) < .06 && luminance(lower) < .06);
            assertTrue("warm oxblood, red over blue", ((top >> 16) & 255) > (top & 255) + 20);
            double ratio = contrast(HomeGlyph.CREAM, lower);
            assertTrue("cream mark against the tile: " + ratio, ratio >= 7);
            save(b, backdrop == LIGHT_WALL ? "home-tile-calendar-on-light-wall" : "home-tile-calendar-on-oxblood");
        }
        // The rounded corner shows the backdrop: the tile is a rounded square, not a block.
        Bitmap b = tile(new HomeGlyph("phone"), LIGHT_WALL);
        assertEquals(LIGHT_WALL, b.getPixel(0, 0));
    }

    @Test public void anotherAppKeepsItsOwnMarkThroughItsMonochromeLayer() throws Exception {
        ShapeDrawable mark = new ShapeDrawable(new OvalShape());
        mark.getPaint().setColor(0xff000000);
        AdaptiveIconDrawable withMono = new AdaptiveIconDrawable(new ColorDrawable(0xffd97757), new ColorDrawable(0xffffffff), mark);
        Drawable themed = HomeGlyph.themed(withMono);
        assertNotNull("an app with a monochrome layer is drawn from it", themed);
        int side = px(HomeAppLayout.TILE_DP), icon = px(HomeAppLayout.ICON_DP), inset = (side - icon) / 2;
        Bitmap b = tile(themed, OXBLOOD_FIELD);
        int inside = creamCount(b, inset, inset, inset + icon, inset + icon);
        assertTrue("the app's own shape, tinted cream (" + inside + ")", inside > icon * icon / 4);
        assertEquals("clipped to the icon area", inside, creamCount(b, 0, 0, side, side));
        assertFalse("its own colours are not drawn", b.getPixel(side / 2, side / 2) == 0xffd97757);
        save(b, "home-tile-themed-monochrome");
        assertNull("no monochrome layer: the app's own icon is used unchanged",
            HomeGlyph.themed(new AdaptiveIconDrawable(new ColorDrawable(0xffd97757), new ColorDrawable(0xffffffff))));
        assertNull("a plain icon: unchanged", HomeGlyph.themed(new ColorDrawable(0xff00ff00)));
        assertNull("no icon", HomeGlyph.themed(null));
    }

    @Test public void cellIsOneNamedTouchTarget() {
        View cell = HomeAppTile.tile(context(), new HomeGlyph("mail"), "Mail");
        cell.measure(View.MeasureSpec.makeMeasureSpec(px(84), View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
        assertTrue("at least 48dp wide", cell.getMeasuredWidth() >= px(48));
        assertTrue("at least 48dp tall", cell.getMeasuredHeight() >= px(48));
        assertEquals("Mail", cell.getContentDescription());
        assertTrue(cell.isClickable() && cell.isFocusable());
        ViewGroup group = (ViewGroup) cell;
        assertEquals("the image is not read separately", View.IMPORTANT_FOR_ACCESSIBILITY_NO,
            ((ViewGroup) group.getChildAt(0)).getChildAt(0).getImportantForAccessibility());
        assertEquals("the visible label is not read twice", View.IMPORTANT_FOR_ACCESSIBILITY_NO, group.getChildAt(1).getImportantForAccessibility());
        int[] clicks = {0};
        cell.setOnClickListener(v -> clicks[0]++);
        assertTrue(cell.performClick());
        assertEquals(1, clicks[0]);
    }

    /** The study's Home rows, in the production grid: the eight marks it shows (here phone, messages, the direction
     * arrow, camera, mail, calendar, lines and the "…" folder), four columns, over the Oxblood field and the light wall. */
    @Test public void homeRowsRenderLikeTheStudy() throws Exception {
        String[][] apps = {{"phone", "Phone"}, {"messages", "Messages"}, {"compass", "Compass"}, {"camera", "Camera"},
            {"mail", "Mail"}, {"calendar", "Calendar"}, {"notes", "Notes"}, {"more", "System"}};
        for (int backdrop : new int[]{OXBLOOD_FIELD, LIGHT_WALL}) {
            HomeAppPage page = new HomeAppPage(context(), 4, null);
            for (String[] app : apps) page.addView(HomeAppTile.tile(context(), new HomeGlyph(app[0]), app[1]));
            page.setPadding(px(24), px(24), px(24), 0);
            Bitmap b = draw(page, px(411), px(420), backdrop);
            assertEquals(8, page.getChildCount());
            View first = page.getChildAt(0), fifth = page.getChildAt(4);
            assertTrue("two rows of four", fifth.getTop() > first.getBottom() - 1 && fifth.getLeft() == first.getLeft());
            assertTrue("the grid sits at the bottom of the page", page.getChildAt(7).getBottom() <= b.getHeight());
            int marks = creamCount(b, 0, 0, b.getWidth(), b.getHeight());
            assertTrue("eight cream marks drawn: " + marks, marks > 8 * px(44) * px(44) / 60);
            save(b, backdrop == LIGHT_WALL ? "home-rows-on-light-wall" : "home-rows-on-oxblood");
        }
    }

    static List<View> tiles(int n, int[] clicks) {
        ArrayList<View> tiles = new ArrayList<>();
        String[] keys = {"camera", "mail", "calendar", "weather", "clock", "calculator", "files", "settings", "browser"};
        for (int i = 0; i < n; i++) {
            View t = HomeAppTile.tile(context(), i < keys.length ? new HomeGlyph(keys[i]) : new HomeGlyph("more"), "App " + (i + 1));
            t.setOnClickListener(v -> clicks[0]++);
            tiles.add(t);
        }
        return tiles;
    }

    @Test public void systemFolderOpensAsANamedPaneAndClosesFromOutside() throws Exception {
        int[] closes = {0}, launches = {0};
        HomeFolder folder = new HomeFolder(context(), "System", tiles(9, launches), 4, () -> closes[0]++);
        int w = px(411), h = px(914);
        Bitmap b = draw(folder, w, h, LIGHT_WALL);
        save(b, "home-system-folder");
        assertEquals("System", folder.panel.getAccessibilityPaneTitle());
        assertTrue(folder.title.isAccessibilityHeading());
        assertEquals("Close System", folder.scrim.getContentDescription());
        assertTrue("panel inset by its margins", folder.panel.getLeft() >= px(HomeFolder.MARGIN_DP) && folder.panel.getRight() <= w - px(HomeFolder.MARGIN_DP));
        assertTrue("panel centred vertically", Math.abs((folder.panel.getTop() + folder.panel.getBottom()) / 2 - h / 2) <= 2);
        assertEquals(9, folder.grid.getChildCount());
        View first = folder.grid.getChildAt(0), fifth = folder.grid.getChildAt(4), ninth = folder.grid.getChildAt(8);
        assertTrue("four columns", folder.grid.getChildAt(3).getTop() == first.getTop() && fifth.getTop() > first.getTop());
        assertTrue("three rows", ninth.getTop() > fifth.getTop() && ninth.getLeft() == first.getLeft());
        assertTrue("cells are touch-sized", first.getWidth() >= px(48) && first.getHeight() >= px(48));
        assertTrue("dimmed Home behind", luminance(b.getPixel(px(4), px(4))) < luminance(LIGHT_WALL) * .6);
        // Outside closes; the panel itself does not; a cell launches.
        assertTrue(folder.scrim.performClick());
        assertEquals(1, closes[0]);
        folder.panel.performClick(); // clickable, so a tap there is consumed, and it has no action of its own
        assertTrue(folder.panel.isClickable());
        assertEquals("a tap on the panel does not close", 1, closes[0]);
        assertTrue(first.performClick());
        assertEquals(1, launches[0]);
    }

    @Test public void aLongFolderScrollsInsideTheScreen() throws Exception {
        int[] ignored = {0};
        HomeFolder folder = new HomeFolder(context(), "System", tiles(40, ignored), 4, () -> {});
        int w = px(411), h = px(914);
        Bitmap b = draw(folder, w, h, OXBLOOD_FIELD);
        save(b, "home-system-folder-long");
        assertTrue("the panel stays on screen", folder.panel.getTop() >= 0 && folder.panel.getBottom() <= h);
        ScrollView scroll = (ScrollView) folder.grid.getParent();
        assertTrue("its rows scroll", folder.grid.getHeight() > scroll.getHeight());
        assertTrue(scroll.canScrollVertically(1));
        assertEquals(40, folder.grid.getChildCount());
    }
}
