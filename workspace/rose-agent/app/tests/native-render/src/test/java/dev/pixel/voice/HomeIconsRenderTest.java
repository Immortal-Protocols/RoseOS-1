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
    static final int OXBLOOD_FIELD = 0xff35070e, LIGHT_WALL = 0xffa9bed6; // the reference field near the grid; a light stand-in

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
    static Bitmap tile(Drawable mark, int backdrop) { return tile(mark, backdrop, px(HomeAppLayout.GLYPH_DP)); }
    static Bitmap tile(Drawable mark, int backdrop, int icon) {
        int side = px(HomeAppLayout.TILE_DP);
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
        int side = px(HomeAppLayout.TILE_DP), icon = px(HomeAppLayout.GLYPH_DP), inset = (side - icon) / 2;
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

    @Test public void tileIsFaintGlassAndTheMarkKeepsContrastOnTheField() throws Exception {
        int side = px(HomeAppLayout.TILE_DP);
        Bitmap b = tile(new HomeGlyph("calendar"), OXBLOOD_FIELD);
        int inside = b.getPixel(px(6), side / 2);
        int dr = ((inside >> 16) & 255) - ((OXBLOOD_FIELD >> 16) & 255);
        assertTrue("#410: faint warm glass, a few levels above the field (" + dr + ")", dr > 2 && dr < 24);
        assertTrue("warm: red over blue", ((inside >> 16) & 255) > (inside & 255));
        double ratio = contrast(HomeGlyph.CREAM, inside);
        assertTrue("cream mark against the tile: " + ratio, ratio >= 7);
        save(b, "home-tile-calendar-on-oxblood");
        assertEquals("the rounded corner shows the field", OXBLOOD_FIELD, b.getPixel(0, 0));
    }

    @Test public void anotherAppKeepsItsOwnIconWithTheReferenceShadow() throws Exception {
        int side = px(HomeAppLayout.TILE_DP), icon = px(HomeAppLayout.ICON_DP), inset = (side - icon) / 2;
        ShapeDrawable own = new ShapeDrawable(new OvalShape());
        own.getPaint().setColor(0xffd97757);
        Bitmap b = tile(new HomeAppTile.Shadowed(own, density(), px(HomeAppLayout.ICON_DP)), OXBLOOD_FIELD, icon);
        assertEquals("its own colour, unrestyled", 0xffd97757, b.getPixel(side / 2, side / 2));
        int below = b.getPixel(side / 2, inset + icon + px(1));
        assertTrue("a soft dark shadow under it", luminance(below) <= luminance(b.getPixel(side / 2, px(2))) + .001);
        save(b, "home-tile-own-icon");
    }

    @Test public void cellIsOneNamedTouchTarget() {
        View cell = HomeAppTile.tile(context(), new HomeGlyph("mail"), "Mail", true, true);
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

    /** F05: Settings is a gear (teeth on a ring and a centre hole), recognisably not the reference's sun-like rays. */
    @Test public void settingsGlyphIsAGear() throws Exception {
        int side = 192;
        float k = side / HomeGlyph.GRID;
        Bitmap b = Bitmap.createBitmap(side, side, Bitmap.Config.ARGB_8888);
        b.eraseColor(0xff1a0a0d);
        HomeGlyph g = new HomeGlyph("settings");
        g.setBounds(0, 0, side, side);
        g.draw(new Canvas(b));
        save(b, "home-glyph-settings");
        for (int i = 0; i < 8; i++) {
            double tooth = Math.toRadians(-90 + i * 45), gap = tooth + Math.toRadians(22.5);
            // The ring between two teeth is inked (the sun's rays left these arcs empty).
            assertTrue("ring between teeth " + i, inked(b, 24 + 11.5 * Math.cos(gap), 24 + 11.5 * Math.sin(gap), k));
            // Each tooth's flat top is inked, and the space just beyond the ring between teeth is not.
            assertTrue("tooth top " + i, inked(b, 24 + 15.5 * Math.cos(tooth), 24 + 15.5 * Math.sin(tooth), k));
            assertFalse("open between teeth " + i, inked(b, 24 + 15.5 * Math.cos(gap), 24 + 15.5 * Math.sin(gap), k));
        }
        assertTrue("the centre hole's rim", inked(b, 24 + 4.6, 24, k));
        assertFalse("the centre is open", inked(b, 24, 24, k));
    }

    /** Whether the glyph's ink (warm, far brighter than the backdrop) lies within one grid unit of (x, y). */
    static boolean inked(Bitmap b, double x, double y, float k) {
        int cx = (int) Math.round(x * k), cy = (int) Math.round(y * k), r = Math.round(k * .6f);
        for (int dy = -r; dy <= r; dy++) for (int dx = -r; dx <= r; dx++) {
            int px = b.getPixel(Math.max(0, Math.min(b.getWidth() - 1, cx + dx)), Math.max(0, Math.min(b.getHeight() - 1, cy + dy)));
            if (((px >> 8) & 255) > 120) return true;
        }
        return false;
    }

    static List<View> tiles(int n, int[] clicks) {
        ArrayList<View> tiles = new ArrayList<>();
        String[] keys = {"camera", "mail", "calendar", "weather", "clock", "calculator", "files", "settings", "browser"};
        for (int i = 0; i < n; i++) {
            View t = HomeAppTile.tile(context(), new HomeGlyph(i < keys.length ? keys[i] : "system"), "App " + (i + 1), true, true);
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
