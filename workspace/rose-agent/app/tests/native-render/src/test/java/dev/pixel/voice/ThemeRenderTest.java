package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.File;
import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * Plan 1E: the same production Home and Rose compositions drawn in each theme, as the running app draws them once
 * RoseThemeNow holds the theme (the field, plate, wells, pearl, tiles, glyphs, work button, pill and the text table), so
 * a review sees every theme side by side. A theme's raster wallpaper is decoded from its committed package copy, as
 * RoseThemeNow.prepare decodes it on the phone; a missing copy draws the theme's own field colour and geometry
 * fallback, and the capture name says so.
 * Host-rendered native output (Robolectric NATIVE graphics): not a device, an activity or a compositor.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class ThemeRenderTest {
    static final int W = 384, H = 832;
    static final File RES = new File(System.getProperty("rose.res", "../../res/drawable-nodpi"));

    static Context context() { return RuntimeEnvironment.getApplication(); }

    /** Holds theme and its raster wallpaper (when its package copy exists), exactly what OxbloodField reads. */
    static boolean use(String theme) throws Exception {
        RoseThemeNow.id = theme;
        Bitmap art = null;
        String drawable = RoseTheme.wallpaperDrawable(theme);
        if (drawable != null) {
            File png = new File(RES, drawable + ".png");
            if (png.isFile()) {
                BitmapFactory.Options o = new BitmapFactory.Options();
                o.inScaled = false;
                byte[] bytes = java.nio.file.Files.readAllBytes(png.toPath());
                art = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, o);
            }
        }
        java.lang.reflect.Field raster = RoseThemeNow.class.getDeclaredField("raster");
        java.lang.reflect.Field rasterTheme = RoseThemeNow.class.getDeclaredField("rasterTheme");
        raster.setAccessible(true);
        rasterTheme.setAccessible(true);
        raster.set(null, art);
        rasterTheme.set(null, art == null ? null : theme);
        return drawable == null || art != null;
    }

    @After public void reset() throws Exception { use(RoseTheme.DEFAULT); }

    static Bitmap draw(View v) {
        v.measure(View.MeasureSpec.makeMeasureSpec(W, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(H, View.MeasureSpec.EXACTLY));
        v.layout(0, 0, W, H);
        Bitmap b = Bitmap.createBitmap(W, H, Bitmap.Config.ARGB_8888);
        v.draw(new Canvas(b));
        return b;
    }

    /** Home as HomeOxbloodRenderTest composes it, with the date ink RoseHomeView gives it. */
    static FrameLayout home() {
        Context c = context();
        View[] cells = new View[HomeOxbloodRenderTest.STUDY.length];
        for (int i = 0; i < cells.length; i++) {
            String key = HomeOxbloodRenderTest.STUDY[i];
            cells[i] = HomeAppTile.tile(c, new HomeGlyph(key), key, true, false);
        }
        FrameLayout root = HomeOxbloodRenderTest.home(cells, 0, 0);
        ((TextView) root.getChildAt(1)).setTextColor(RoseThemeNow.c(HomeAppLayout.DATE_INK));
        return root;
    }

    /** Rose's header, prompt and Connect pill as RoseConversationActivity draws them, with its text table. */
    static FrameLayout rose() {
        Context c = context();
        int ink = RoseTheme.Text.color(RoseThemeNow.id, RoseTheme.Text.PRIMARY);
        FrameLayout root = new FrameLayout(c);
        root.setBackground(new OxbloodField(OxbloodField.ROSE));
        LinearLayout head = new LinearLayout(c);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.addView(HomeOxbloodRenderTest.box(new RosePearl(1f, 16)), new LinearLayout.LayoutParams(28, 28));
        TextView title = RoseOxbloodRenderTest.text("Rose", 23, ink);
        LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(-2, -2);
        tl.leftMargin = 7;
        head.addView(title, tl);
        FrameLayout.LayoutParams hp = new FrameLayout.LayoutParams(-2, 44, Gravity.TOP | Gravity.START);
        hp.topMargin = 49;
        hp.leftMargin = 24;
        root.addView(head, hp);
        OxbloodRose.WorkButton wb = new OxbloodRose.WorkButton(1f);
        wb.set(false, false);
        FrameLayout.LayoutParams wp = new FrameLayout.LayoutParams(44, 44, Gravity.TOP | Gravity.END);
        wp.topMargin = 49;
        wp.rightMargin = 18;
        root.addView(HomeOxbloodRenderTest.box(wb), wp);
        TextView prompt = RoseOxbloodRenderTest.text("What would you like to do?", 26, ink);
        FrameLayout.LayoutParams pp = new FrameLayout.LayoutParams(324, -2, Gravity.TOP | Gravity.START);
        pp.topMargin = 178;
        pp.leftMargin = 30;
        root.addView(prompt, pp);
        FrameLayout pill = new FrameLayout(c);
        pill.setBackground(new OxbloodRose.Pill(1f));
        pill.setMinimumWidth(154);
        pill.setPadding(26, 0, 26, 0);
        LinearLayout row = new LinearLayout(c);
        row.setGravity(Gravity.CENTER);
        OxbloodRose.PillGlyph g = new OxbloodRose.PillGlyph(1f);
        g.set(false);
        row.addView(HomeOxbloodRenderTest.box(g), new LinearLayout.LayoutParams(15, 15));
        TextView label = RoseOxbloodRenderTest.text("Connect", 15, ink);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -2);
        lp.leftMargin = 11;
        row.addView(label, lp);
        pill.addView(row, new FrameLayout.LayoutParams(-2, -1, Gravity.CENTER));
        FrameLayout.LayoutParams plp = new FrameLayout.LayoutParams(-2, 44, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        plp.bottomMargin = 42;
        root.addView(pill, plp);
        return root;
    }

    static double luminance(int c) {
        double[] v = {((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0};
        for (int i = 0; i < 3; i++) v[i] = v[i] <= 0.04045 ? v[i] / 12.92 : Math.pow((v[i] + 0.055) / 1.055, 2.4);
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
    }

    @Test public void everyThemeDrawsHomeAndRose() throws Exception {
        Bitmap oxbloodHome = null;
        for (String theme : RoseTheme.IDS) {
            boolean art = use(theme);
            String suffix = art ? "" : "-wallpaper-pending";
            Bitmap home = draw(home());
            HomeOxbloodRenderTest.save(home, "theme-" + theme + "-home" + suffix);
            HomeOxbloodRenderTest.save(draw(rose()), "theme-" + theme + "-rose" + suffix);
            if (RoseTheme.OXBLOOD.equals(theme)) oxbloodHome = home;
            // A light theme draws a light field behind Home's content; the others stay dark (row 120, the open field).
            double l = luminance(home.getPixel(200, 120));
            if (RoseTheme.light(theme)) assertTrue(theme + " field is light: " + l, l > 0.5);
            else assertTrue(theme + " field is dark: " + l, l < 0.3);
        }
        use(RoseTheme.OXBLOOD);
        assertTrue("Oxblood unchanged by drawing other themes", HomeOxbloodRenderTest.sameAs(oxbloodHome, draw(home())));
    }

    @Test public void windsTextIsDarkOnLight() throws Exception {
        use("winds");
        int ink = RoseTheme.Text.color("winds", RoseTheme.Text.PRIMARY);
        assertEquals(0xFF102D2D, ink);
        double contrast = (luminance(RoseTheme.Field.color("winds", RoseTheme.Field.BACKGROUND)) + 0.05) / (luminance(ink) + 0.05);
        assertTrue("Winds text on its field " + contrast, contrast >= 4.5);
    }
}
