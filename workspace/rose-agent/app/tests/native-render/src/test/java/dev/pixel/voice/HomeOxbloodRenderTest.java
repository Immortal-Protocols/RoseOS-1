package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.Drawable;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.TextView;
import java.io.File;
import java.io.FileOutputStream;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * #410 Home at the reference's own geometry: a 384x832dp screen at mdpi, so one dp is one pixel and the render lines
 * up with the prototype's capture (main f6503a0, browser-check 01-home.png, screen at image 12,12). Production pieces
 * only: the Oxblood field, Home's grid of unlabelled cells (HomeAppPage in Home mode with HomeAppTile and HomeGlyph,
 * placed by the pages' inset bottom exactly as HomeFrame gives it), the dots (HomeDot) and the edge (OxbloodEdge.Plate,
 * Well, SwitcherGlyph and RosePearl at HomeTray's sizes). The date is a fixed string in the production style (the
 * live TextClock would make the pixels time-dependent). The edge's centre microphone is intentionally absent (no
 * OS-wide microphone API, #410), so the two controls are centred without it. Not a device or a launcher activity.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class HomeOxbloodRenderTest {
    static final File OUT = new File(System.getProperty("render.out", "target/render"));
    static final int W = 384, H = 832;

    static Context context() { return RuntimeEnvironment.getApplication(); }

    static View box(Drawable d) { View v = new View(context()); v.setBackground(d); return v; }

    static FrameLayout home(String[] keys, int dotsSelected) {
        Context c = context();
        View[] cells = new View[keys.length];
        for (int i = 0; i < keys.length; i++) cells[i] = HomeAppTile.tile(c, new HomeGlyph(keys[i]), keys[i], true, false);
        return home(cells, dotsSelected, 0);
    }

    /** Home as RoseHomeView builds it (#410 owner density correction): the date, then the unlabelled grid from 24dp
     * below the date's line down to the pages' inset bottom (the edge, any tappable navigation inset, and the reserve). */
    static FrameLayout home(View[] cells, int dotsSelected, int navInset) { return home(cells, dotsSelected, navInset, 4); }

    static FrameLayout home(View[] cells, int dotsSelected, int navInset, int columns) {
        Context c = context();
        FrameLayout root = new FrameLayout(c);
        root.setBackground(new OxbloodField(OxbloodField.HOME));
        TextView date = new TextView(c);
        date.setText("Thu, Oct 29");
        date.setTextSize(HomeAppLayout.DATE_SP);
        date.setTextColor(HomeAppLayout.DATE_INK);
        date.setIncludeFontPadding(false);
        HomeAppPage grid = new HomeAppPage(c, columns, HomeAppLayout.HOME_CELL_DP, HomeAppLayout.HOME_COL_GAP_DP, null);
        for (View cell : cells) grid.addView(cell);
        grid.setPadding(HomeAppLayout.MARGIN_DP, Math.round(HomeAppLayout.homeTop(HomeAppLayout.DATE_TOP_DP, date.getLineHeight(), 1f)),
            HomeAppLayout.MARGIN_DP, HomeAppLayout.EDGE_DP + navInset + HomeAppLayout.BAND_BOTTOM_DP);
        root.addView(grid, new FrameLayout.LayoutParams(-1, -1));
        FrameLayout.LayoutParams dp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        dp.leftMargin = HomeAppLayout.DATE_LEFT_DP; dp.topMargin = HomeAppLayout.DATE_TOP_DP;
        root.addView(date, dp);
        // Dots: 19dp apart, centred 30dp above the edge's top.
        android.widget.LinearLayout dots = new android.widget.LinearLayout(c);
        dots.setGravity(Gravity.CENTER);
        for (int i = 0; i < 3; i++) { View d = box(new HomeDot(1f)); d.setSelected(i == dotsSelected); dots.addView(d, new android.widget.LinearLayout.LayoutParams(19, HomeAppLayout.DOTS_DP)); }
        FrameLayout.LayoutParams dotsParams = new FrameLayout.LayoutParams(-1, HomeAppLayout.DOTS_DP, Gravity.BOTTOM);
        dotsParams.bottomMargin = OxbloodEdge.HEIGHT_DP + navInset + HomeAppLayout.DOTS_ABOVE_EDGE_DP - HomeAppLayout.DOTS_DP / 2;
        root.addView(dots, dotsParams);
        // The edge: plate, then the two controls centred 14dp apart in the flat area, as HomeTray lays them out.
        android.widget.LinearLayout edge = new android.widget.LinearLayout(c);
        edge.setGravity(Gravity.CENTER);
        edge.setBackground(new OxbloodEdge.Plate(1f));
        edge.setPadding(0, OxbloodEdge.FLAT_TOP_DP, 0, 0);
        FrameLayout rose = new FrameLayout(c);
        rose.setBackground(new OxbloodEdge.Well(1f));
        RosePearl pearl = new RosePearl(1f, OxbloodEdge.PEARL_DP);
        rose.addView(box(pearl), new FrameLayout.LayoutParams(OxbloodEdge.PEARL_DP + 12, OxbloodEdge.PEARL_DP + 12, Gravity.CENTER));
        edge.addView(rose, new android.widget.LinearLayout.LayoutParams(OxbloodEdge.ROSE_W_DP, OxbloodEdge.CONTROL_H_DP));
        // Rose's microphone-input mute well (HomeTray: Well under MicGlyph), between Rose and the switcher.
        FrameLayout mic = new FrameLayout(c);
        OxbloodEdge.MicGlyph micGlyph = new OxbloodEdge.MicGlyph(1f);
        mic.setBackground(new android.graphics.drawable.LayerDrawable(new Drawable[]{new OxbloodEdge.Well(1f), micGlyph}));
        mic.setTag(micGlyph);
        android.widget.LinearLayout.LayoutParams mp = new android.widget.LinearLayout.LayoutParams(OxbloodEdge.MIC_W_DP, OxbloodEdge.CONTROL_H_DP);
        mp.leftMargin = OxbloodEdge.GAP_DP;
        edge.addView(mic, mp);
        FrameLayout switcher = new FrameLayout(c);
        switcher.setBackground(new OxbloodEdge.Well(1f));
        switcher.addView(box(new OxbloodEdge.SwitcherGlyph(1f)), new FrameLayout.LayoutParams(OxbloodEdge.SwitcherGlyph.BOX_W_DP, OxbloodEdge.SwitcherGlyph.BOX_H_DP, Gravity.CENTER));
        android.widget.LinearLayout.LayoutParams sp = new android.widget.LinearLayout.LayoutParams(OxbloodEdge.SWITCHER_W_DP, OxbloodEdge.CONTROL_H_DP);
        sp.leftMargin = OxbloodEdge.GAP_DP;
        edge.addView(switcher, sp);
        root.addView(edge, new FrameLayout.LayoutParams(-1, OxbloodEdge.HEIGHT_DP + navInset, Gravity.BOTTOM));
        root.setTag(pearl);
        return root;
    }

    static Bitmap draw(View v) {
        v.measure(View.MeasureSpec.makeMeasureSpec(W, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(H, View.MeasureSpec.EXACTLY));
        v.layout(0, 0, W, H);
        Bitmap b = Bitmap.createBitmap(W, H, Bitmap.Config.ARGB_8888);
        v.draw(new Canvas(b));
        return b;
    }

    static void save(Bitmap b, String name) throws Exception {
        OUT.mkdirs();
        try (FileOutputStream out = new FileOutputStream(new File(OUT, name + ".png"))) { assertTrue(b.compress(Bitmap.CompressFormat.PNG, 100, out)); }
        System.out.println("RENDER " + name + " " + b.getWidth() + "x" + b.getHeight());
    }

    static final String[] STUDY = {"phone", "messages", "compass", "camera", "mail", "calendar", "notes", "system"};

    @Test public void homeMatchesTheReferenceGeometry() throws Exception {
        FrameLayout root = home(STUDY, 0);
        Bitmap b = draw(root);
        save(b, "oxblood-home-384x832");
        HomeAppPage grid = (HomeAppPage) root.getChildAt(0);
        View first = grid.getChildAt(0), fifth = grid.getChildAt(4), fourth = grid.getChildAt(3);
        // #410 owner density correction: the grid starts 24dp below the date's line (not the reference's 475) and its
        // rows are spaced to fill down to the reserve; the reference's columns (45.75 / 123.25 / 200.75 / 278.25) stay.
        TextView date = (TextView) root.getChildAt(1);
        assertEquals("grid starts below the date", date.getBottom() + HomeAppLayout.BELOW_DATE_DP, first.getTop(), 1);
        int innerHeight = grid.getHeight() - grid.getPaddingTop() - grid.getPaddingBottom(), fit = HomeAppLayout.rows(innerHeight, first.getHeight(), 1f);
        assertEquals("second row one pitch down", first.getTop() + HomeAppLayout.rowPitch(innerHeight, fit, first.getHeight(), 1f), fifth.getTop(), 1);
        assertTrue("first column at 45-46: " + first.getLeft(), Math.abs(first.getLeft() - 45.75) <= 1);
        assertTrue("fourth column at 278: " + fourth.getLeft(), Math.abs(fourth.getLeft() - 278.25) <= 1);
        assertEquals("60dp cells", 60, first.getWidth());
        // The edge: the plate's flat top 34dp below the edge top (y 774), hairline there; fillets reach the sides at 740.
        int flat = b.getPixel(W / 2, 776), above = b.getPixel(W / 2, 770);
        assertTrue("the plate is burgundy below the concave top", ((flat >> 16) & 255) > 40 && ((flat >> 16) & 255) > (flat & 255));
        assertTrue("the field shows above the flat top in the middle", above != flat);
        int corner = b.getPixel(1, 752);
        assertTrue("the fillet rises to the edge top at the sides", ((corner >> 16) & 255) > 45);
        // The pearl: ruby (red well over blue) at the Rose control's centre.
        View roseControl = ((android.widget.LinearLayout) root.getChildAt(3)).getChildAt(0);
        assertTrue("the edge is the fourth child", root.getChildAt(3).getBackground() instanceof OxbloodEdge.Plate);
        int[] at = {roseControl.getLeft() + roseControl.getWidth() / 2, H - OxbloodEdge.HEIGHT_DP + roseControl.getTop() + roseControl.getHeight() / 2};
        int p = b.getPixel(at[0], at[1]);
        assertTrue("ruby pearl", ((p >> 16) & 255) > 150 && ((p >> 16) & 255) > ((p >> 8) & 255) + 60);
        // Silver when Rose's call is muted.
        ((RosePearl) root.getTag()).set(true, false, -1);
        Bitmap muted = draw(root);
        int s = muted.getPixel(at[0], at[1]);
        assertTrue("silver pearl", Math.abs(((s >> 16) & 255) - (s & 255)) < 20 && ((s >> 16) & 255) > 120);
        save(muted, "oxblood-home-384x832-call-muted");
    }

    /** #410 wave item 6: Rose's microphone-input mute well on Home's edge, unmuted and muted (the HiFi's tray-mic):
     * warm ink mic; muted, a dimmed ruby mic with a slash and a ruby ring on the well. The same pixels for the raised
     * edge's peek-mic are OS Product's to draw; the state is the one RoseMicInput state. */
    @Test public void edgeMicWellShowsRoseInputMute() throws Exception {
        FrameLayout root = home(STUDY, 0);
        android.widget.LinearLayout edge = (android.widget.LinearLayout) root.getChildAt(3);
        assertEquals("Rose, mic, switcher", 3, edge.getChildCount());
        View mic = edge.getChildAt(1);
        OxbloodEdge.MicGlyph glyph = (OxbloodEdge.MicGlyph) mic.getTag();
        Bitmap on = draw(root);
        int x0 = mic.getLeft(), y0 = H - OxbloodEdge.HEIGHT_DP + mic.getTop(), cx = x0 + mic.getWidth() / 2, cy = y0 + mic.getHeight() / 2;
        int ink = on.getPixel(cx, cy - 4);
        assertTrue("the mic body in warm ink: " + Integer.toHexString(ink), ((ink >> 16) & 255) > 180 && ((ink >> 8) & 255) > 160);
        int cropX = Math.max(0, x0 - 20), cropY = Math.max(0, y0 - 8), cropW = Math.min(W - cropX, mic.getWidth() + 40), cropH = Math.min(H - cropY, mic.getHeight() + 16);
        save(Bitmap.createBitmap(on, cropX, cropY, cropW, cropH), "oxblood-edge-mic-unmuted");
        glyph.set(true);
        ((RosePearl) root.getTag()).set(true, false, -1); // HomeTray: the pearl follows the same mute
        Bitmap off = draw(root);
        int ruby = off.getPixel(cx, cy - 4);
        assertTrue("muted: the body turns dim ruby: " + Integer.toHexString(ruby), ((ruby >> 16) & 255) > ((ruby >> 8) & 255) + 40);
        int ringX = x0 + 1, ringY = cy;
        int ringOn = on.getPixel(ringX, ringY), ringOff = off.getPixel(ringX, ringY);
        assertTrue("muted: a ruby ring on the well's edge", ((ringOff >> 16) & 255) > ((ringOn >> 16) & 255) + 20);
        save(Bitmap.createBitmap(off, cropX, cropY, cropW, cropH), "oxblood-edge-mic-muted");
        save(off, "oxblood-home-384x832-mic-muted");
        View rose = edge.getChildAt(0);
        int rx0 = rose.getLeft(), ry0 = H - OxbloodEdge.HEIGHT_DP + rose.getTop();
        int pearl = off.getPixel(rx0 + rose.getWidth() / 2, ry0 + rose.getHeight() / 2);
        assertTrue("the pearl turns silver with the mute", Math.abs(((pearl >> 16) & 255) - (pearl & 255)) < 20);
        // Nothing else changes: the mute is drawn only in the mic well and the pearl.
        int changed = 0;
        for (int y = 0; y < H; y++) for (int x = 0; x < W; x++) {
            boolean inWell = x >= x0 - 12 && x < x0 + mic.getWidth() + 12 && y >= y0 - 12 && y < y0 + mic.getHeight() + 12;
            boolean inRose = x >= rx0 && x < rx0 + rose.getWidth() && y >= ry0 && y < ry0 + rose.getHeight();
            if (!inWell && !inRose && on.getPixel(x, y) != off.getPixel(x, y)) changed++;
        }
        assertEquals("only the well and the pearl change", 0, changed);
        ((RosePearl) root.getTag()).set(false, false, -1);
    }

    @Test public void pearlStatesForTheRoseHeader() throws Exception {
        Bitmap sheet = Bitmap.createBitmap(4 * 48, 48, Bitmap.Config.ARGB_8888);
        sheet.eraseColor(0xff120a0d);
        Canvas c = new Canvas(sheet);
        float[][] states = {{0, 0, -1}, {1, 0, -1}, {0, 1, -1}, {0, 0, .5f}};
        for (int i = 0; i < states.length; i++) {
            RosePearl p = new RosePearl(1f, 16);
            p.set(states[i][0] == 1, states[i][1] == 1, states[i][2]);
            p.setBounds(i * 48, 0, i * 48 + 48, 48);
            p.draw(c);
        }
        save(sheet, "oxblood-pearl-states");
        int quiet = sheet.getPixel(24 + 11, 24), listening = sheet.getPixel(2 * 48 + 24 + 11, 24), speaking = sheet.getPixel(3 * 48 + 24 + 12, 24);
        assertTrue("listening shows a ring outside the pearl", ((listening >> 16) & 255) > ((quiet >> 16) & 255) + 20);
        assertTrue("speaking shows the breath", ((speaking >> 16) & 255) > ((quiet >> 16) & 255) + 20);
    }

    /** #410 gap closure: the same Home with the display's glass sheen (GlassSheen at Home's .35), saved beside the plain
     * render for before/after, and the sweep band at three points of its travel (drawable-driven frames, not a device
     * recording). With animations off the band is parked: nothing extra is drawn. */
    @Test public void glassSheenOverHome() throws Exception {
        FrameLayout plain = home(STUDY, 0);
        Bitmap before = draw(plain);
        save(before, "oxblood-home-384x832-before-sheen");
        FrameLayout root = home(STUDY, 0);
        GlassSheen sheen = new GlassSheen(GlassSheen.HOME);
        root.setForeground(sheen);
        Bitmap after = draw(root);
        save(after, "oxblood-home-384x832-sheen");
        // The bright band crosses the upper left; the sheen only ever lightens (white at low alpha), never darkens.
        int lit = 0, darker = 0;
        for (int y = 0; y < H; y += 4) for (int x = 0; x < W; x += 4) {
            int a = before.getPixel(x, y), b = after.getPixel(x, y);
            int da = ((b >> 16) & 255) - ((a >> 16) & 255);
            if (da > 3) lit++; if (da < -1) darker++;
        }
        assertTrue("the sheen lightens part of the screen: " + lit, lit > 200);
        assertEquals("the sheen never darkens", 0, darker);
        assertEquals("parked with no sweep", -1f, sheen.band, 0f);
        // Every frame is the same sheen: the shaders are not dimmed by the paint state the previous frame left behind.
        assertTrue("a second frame draws the same sheen as the first", draw(root).sameAs(after));
        for (float p : new float[]{.2f, .5f, .8f}) {
            sheen.band = p;
            save(draw(root), "oxblood-home-sheen-sweep-" + Math.round(p * 100));
        }
        sheen.band = -1;
        // Reduced motion: no animator is started when animations are off.
        durationScale(0f);
        GlassSheen still = new GlassSheen(GlassSheen.HOME);
        still.setVisible(true, false);
        assertTrue("animations off: no sweep animator", still.sweep == null);
        durationScale(1f);
        GlassSheen live = new GlassSheen(GlassSheen.HOME);
        live.setVisible(true, false);
        assertTrue("animations on and visible: the sweep runs", live.sweep != null);
        live.setVisible(false, false);
        assertTrue("hidden: the sweep stops and the band parks", live.sweep == null && live.band == -1f);
    }

    /** #410 gap closure: backdrop blur. The same Home with the tiles drawing the field's blurred copy behind them
     * (HomeGlassTile.Backdrop, sigma 6dp) versus without (the fallback). Only the tiles change; outside them the screen
     * is identical. Saved as before/after for review. */
    @Test public void backdropBlurBehindHomeTiles() throws Exception {
        HomeGlassTile.Backdrop.set(null, null);
        FrameLayout plain = home(STUDY, 0);
        Bitmap before = draw(plain);
        save(before, "oxblood-home-384x832-before-blur");
        FrameLayout root = home(STUDY, 0);
        HomeGlassTile.Backdrop.set((OxbloodField) root.getBackground(), root);
        // Attach to a window so the tiles can find their place (Robolectric's activity window).
        android.app.Activity a = org.robolectric.Robolectric.buildActivity(android.app.Activity.class).setup().get();
        a.setContentView(root, new android.view.ViewGroup.LayoutParams(W, H));
        Bitmap after = draw(root);
        save(after, "oxblood-home-384x832-blur");
        HomeAppPage grid = (HomeAppPage) root.getChildAt(0);
        int changedIn = 0, changedOut = 0;
        for (int y = 0; y < H; y += 2) for (int x = 0; x < W; x += 2) {
            boolean inTile = false;
            for (int k = 0; k < grid.getChildCount(); k++) {
                View c = grid.getChildAt(k);
                int l = c.getLeft() + (c.getWidth() - HomeAppLayout.TILE_DP) / 2, t = c.getTop();
                if (x >= l && x < l + HomeAppLayout.TILE_DP && y >= t && y < t + HomeAppLayout.TILE_DP) inTile = true;
            }
            if (before.getPixel(x, y) != after.getPixel(x, y)) { if (inTile) changedIn++; else changedOut++; }
        }
        assertTrue("the tiles show the blurred field behind them: " + changedIn, changedIn > 50);
        assertEquals("nothing outside the tiles changes", 0, changedOut);
        // Fallback: with no backdrop (another window, or no blurred copy) the tile draws exactly as before.
        HomeGlassTile.Backdrop.set(null, null);
        assertTrue("fallback is the plain tile", sameAs(before, draw(root)));
    }
    static boolean sameAs(Bitmap a, Bitmap b) {
        for (int y = 0; y < a.getHeight(); y += 2) for (int x = 0; x < a.getWidth(); x += 2) if (a.getPixel(x, y) != b.getPixel(x, y)) return false;
        return true;
    }

    /** The system animator scale (the owner's "animations off" is 0), set through the platform's hidden test hook. */
    static void durationScale(float scale) throws Exception {
        android.animation.ValueAnimator.class.getMethod("setDurationScale", float.class).invoke(null, scale);
    }
}
