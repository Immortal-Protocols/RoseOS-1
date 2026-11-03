package com.android.quickstep.fallback;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import java.io.File;
import java.io.FileOutputStream;
import java.security.MessageDigest;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * #410: the switcher's Oxblood field (RoseOxbloodField, production source unchanged), drawn by native Android graphics
 * at the frozen reference's 384x832 dp and at a wider 411x914 dp screen, xxhdpi, for comparison with Chromium's raster
 * of the pinned oxblood-rose.svg at the same size. Host-rendered native output; not a device.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class RoseOxbloodFieldNativeTest {
    static final File OUT = new File(System.getProperty("render.out", "target/render"));

    static Bitmap render(int w, int h, int alpha) {
        RoseOxbloodField field = new RoseOxbloodField();
        field.setBounds(0, 0, w, h);
        field.setAlpha(alpha);
        Bitmap b = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        field.draw(new Canvas(b));
        return b;
    }

    static void save(Bitmap bitmap, String name) throws Exception {
        OUT.mkdirs();
        File file = new File(OUT, name + ".png");
        try (FileOutputStream out = new FileOutputStream(file)) {
            assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, out));
        }
        StringBuilder hex = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(java.nio.file.Files.readAllBytes(file.toPath()))) {
            hex.append(String.format("%02x", b));
        }
        System.out.println("RENDER " + name + " " + bitmap.getWidth() + "x" + bitmap.getHeight() + " sha256=" + hex);
    }

    static int channelDistance(int a, int b) {
        return Math.max(Math.abs(Color.red(a) - Color.red(b)),
                Math.max(Math.abs(Color.green(a) - Color.green(b)), Math.abs(Color.blue(a) - Color.blue(b))));
    }

    @Test public void theFieldCoversTheScreenWithTheReferenceArtwork() throws Exception {
        int w = 1152, h = 2496; // 384x832 dp at xxhdpi: exactly the 390x844 viewBox scaled by max(W/390, H/844)
        Bitmap b = render(w, h, 255);
        save(b, "switcher-oxblood-field-384x832");
        for (int[] p : new int[][]{{0, 0}, {w - 1, 0}, {0, h - 1}, {w - 1, h - 1}, {w / 2, h / 2}}) {
            assertEquals("opaque everywhere", 255, b.getPixel(p[0], p[1]) >>> 24);
        }
        float s = RoseOxbloodField.scale(w, h), x0 = RoseOxbloodField.offsetX(w, s);
        // Reference pixels from Chromium's raster of the pinned oxblood-rose.svg (blob de9f42a1) at the same 1152x2496
        // (img object-fit cover, top): viewBox point -> RGB. Over the whole image the native render differed from it by
        // a mean of 0.31 per channel (p99 1; only antialiased and blurred hairline edges more).
        int[][] reference = {
            {330, 600, 51, 2, 13},      // crimson plane
            {295, 580, 255, 235, 235},  // glint centre
            {100, 400, 15, 9, 12},      // dark field
            {380, 200, 52, 24, 31},     // glass sheet
            {250, 800, 43, 2, 10},      // lower plane
            {385, 520, 99, 47, 56},     // lit edge region
        };
        for (int[] r : reference) {
            int px = b.getPixel(Math.min(w - 1, (int) (x0 + r[0] * s)), (int) (r[1] * s));
            assertTrue("viewBox (" + r[0] + "," + r[1] + "): native " + Integer.toHexString(px) + " vs Chromium rgb("
                    + r[2] + "," + r[3] + "," + r[4] + ")", channelDistance(px, Color.rgb(r[2], r[3], r[4])) <= 4);
        }
    }

    @Test public void aWiderScreenKeepsTheArtworkCentredAndTopAligned() throws Exception {
        int w = 1233, h = 2742; // 411x914 dp
        Bitmap b = render(w, h, 255);
        save(b, "switcher-oxblood-field-411x914");
        float s = RoseOxbloodField.scale(w, h);
        assertEquals("cover: height-limited", h / RoseOxbloodField.VIEW_H, s, 1e-4);
        assertTrue("centred across: equal overhang", Math.abs(RoseOxbloodField.offsetX(w, s)
                - (w - RoseOxbloodField.VIEW_W * s) / 2f) < 1e-3);
    }

    @Test public void asTheScrimsFillItsCoverageIsExactlyTheScrimsOpacity() throws Exception {
        // ScrimView paints its colour (the RoseOverviewOxblood base #170609 at the animated alpha) before onDraw calls
        // the drawing controller; the field must then replace that fill as one image at the same alpha, never stack.
        int w = 400, h = 866;
        Bitmap opaque = render(w, h, 255);
        int[][] probes = {{200, 100}, {300, 600}, {340, 640}, {50, 800}};
        for (int a : new int[] {0, 64, 128, 200, 255}) {
            Bitmap b = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
            Canvas c = new Canvas(b);
            c.drawColor(Color.argb(a, 0x17, 0x06, 0x09));
            new RoseOxbloodField().drawAsScrim(c, w, h, a);
            if (a == 128) save(b, "switcher-oxblood-field-as-scrim-alpha128");
            for (int[] p : probes) {
                int px = b.getPixel(p[0], p[1]), ref = opaque.getPixel(p[0], p[1]);
                assertTrue("alpha " + a + " at " + p[0] + "," + p[1] + ": coverage " + (px >>> 24),
                        Math.abs((px >>> 24) - a) <= 1);
                if (a >= 64) {
                    assertTrue("alpha " + a + ": the field's own colour, not a blend with the scrim fill ("
                            + Integer.toHexString(px) + " vs " + Integer.toHexString(ref) + ")",
                            channelDistance(px, ref) <= 4);
                }
            }
        }
    }

    @Test public void alphaFollowsTheScrimAndZeroDrawsNothing() throws Exception {
        Bitmap none = render(200, 400, 0);
        assertEquals("transparent states draw nothing", 0, none.getPixel(100, 200));
        Bitmap half = render(200, 400, 128);
        int a = half.getPixel(100, 50) >>> 24;
        assertTrue("half the scrim's opacity: about half the field (" + a + ")", a > 100 && a < 156);
    }
}
