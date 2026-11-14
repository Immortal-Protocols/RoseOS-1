package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import java.io.File;
import java.io.FileOutputStream;
import java.security.MessageDigest;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/** #410: the native Oxblood fields (OxbloodField), drawn by Robolectric's native graphics at the 411x914dp xxhdpi
 * screen, saved for pixel comparison with Chromium's raster of the reference SVGs at the same size. Not a device. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w411dp-h914dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class OxbloodFieldRenderTest {
    static final File OUT = new File(System.getProperty("render.out", "target/render"));

    static Bitmap field(int kind, int w, int h) {
        OxbloodField f = new OxbloodField(kind);
        f.setBounds(0, 0, w, h);
        Bitmap b = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        f.draw(new Canvas(b));
        return b;
    }
    static String save(Bitmap bitmap, String name) throws Exception {
        OUT.mkdirs();
        File file = new File(OUT, name + ".png");
        try (FileOutputStream out = new FileOutputStream(file)) { assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)); }
        StringBuilder hex = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(java.nio.file.Files.readAllBytes(file.toPath()))) hex.append(String.format("%02x", b));
        System.out.println("RENDER " + name + " " + bitmap.getWidth() + "x" + bitmap.getHeight() + " sha256=" + hex);
        return hex.toString();
    }

    @Test public void fieldsRenderOpaqueAndCoverTheScreen() throws Exception {
        float d = RuntimeEnvironment.getApplication().getResources().getDisplayMetrics().density;
        int w = Math.round(411 * d), h = Math.round(914 * d);
        for (int kind : new int[]{OxbloodField.HOME, OxbloodField.ROSE}) {
            Bitmap b = field(kind, w, h);
            save(b, kind == OxbloodField.HOME ? "oxblood-home-field" : "oxblood-rose-field");
            for (int[] p : new int[][]{{0, 0}, {w - 1, 0}, {0, h - 1}, {w - 1, h - 1}, {w / 2, h / 2}})
                assertEquals("opaque everywhere", 255, b.getPixel(p[0], p[1]) >>> 24);
        }
    }
}
