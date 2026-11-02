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
        FrameLayout root = new FrameLayout(c);
        root.setBackground(new OxbloodField(OxbloodField.HOME));
        HomeAppPage grid = new HomeAppPage(c, 4, HomeAppLayout.HOME_CELL_DP, HomeAppLayout.HOME_COL_GAP_DP, null);
        for (String k : keys) grid.addView(HomeAppTile.tile(c, new HomeGlyph(k), k, true, false));
        grid.setPadding(HomeAppLayout.MARGIN_DP, HomeAppLayout.TOP_DP, HomeAppLayout.MARGIN_DP, HomeAppLayout.EDGE_DP + HomeAppLayout.BAND_BOTTOM_DP);
        root.addView(grid, new FrameLayout.LayoutParams(-1, -1));
        TextView date = new TextView(c);
        date.setText("Thu, Oct 29");
        date.setTextSize(HomeAppLayout.DATE_SP);
        date.setTextColor(HomeAppLayout.DATE_INK);
        date.setIncludeFontPadding(false);
        FrameLayout.LayoutParams dp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        dp.leftMargin = HomeAppLayout.DATE_LEFT_DP; dp.topMargin = HomeAppLayout.DATE_TOP_DP;
        root.addView(date, dp);
        // Dots: 19dp apart, centred 30dp above the edge's top.
        android.widget.LinearLayout dots = new android.widget.LinearLayout(c);
        dots.setGravity(Gravity.CENTER);
        for (int i = 0; i < 3; i++) { View d = box(new HomeDot(1f)); d.setSelected(i == dotsSelected); dots.addView(d, new android.widget.LinearLayout.LayoutParams(19, HomeAppLayout.DOTS_DP)); }
        FrameLayout.LayoutParams dotsParams = new FrameLayout.LayoutParams(-1, HomeAppLayout.DOTS_DP, Gravity.BOTTOM);
        dotsParams.bottomMargin = OxbloodEdge.HEIGHT_DP + HomeAppLayout.DOTS_ABOVE_EDGE_DP - HomeAppLayout.DOTS_DP / 2;
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
        FrameLayout switcher = new FrameLayout(c);
        switcher.setBackground(new OxbloodEdge.Well(1f));
        switcher.addView(box(new OxbloodEdge.SwitcherGlyph(1f)), new FrameLayout.LayoutParams(OxbloodEdge.SwitcherGlyph.BOX_W_DP, OxbloodEdge.SwitcherGlyph.BOX_H_DP, Gravity.CENTER));
        android.widget.LinearLayout.LayoutParams sp = new android.widget.LinearLayout.LayoutParams(OxbloodEdge.SWITCHER_W_DP, OxbloodEdge.CONTROL_H_DP);
        sp.leftMargin = OxbloodEdge.GAP_DP;
        edge.addView(switcher, sp);
        root.addView(edge, new FrameLayout.LayoutParams(-1, OxbloodEdge.HEIGHT_DP, Gravity.BOTTOM));
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
        // Reference (oxblood.css:354-360): grid top 475, columns at 45.75 / 123.25 / 200.75 / 278.25, rows at 475 and 548.
        assertEquals("grid top 475", 475, first.getTop());
        assertEquals("second row at 548", 548, fifth.getTop());
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
}
