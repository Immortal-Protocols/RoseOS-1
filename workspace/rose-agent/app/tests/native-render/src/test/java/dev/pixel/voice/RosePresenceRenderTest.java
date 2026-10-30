package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.view.View;
import org.robolectric.RuntimeEnvironment;
import java.io.File;
import java.io.FileOutputStream;
import java.security.MessageDigest;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * Host-rendered native output (APP-OS-VISUAL-1030-M003): the production RosePresenceView, constructed and fed a
 * RoseVisualState exactly as the app does, measured, laid out and drawn into a real Bitmap by Robolectric's native
 * Android graphics at SDK 35. Not a physical device, full activity, compositor or timing proof. Fixtures are
 * ConversationState values built by the production factory; animation is off (reduced motion), so frames are
 * deterministic, and nothing starts VoiceService, pairing or network.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w411dp-h914dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class RosePresenceRenderTest {
    static final File OUT = new File(System.getProperty("render.out", "target/render"));

    static RoseVisualState state(boolean connected, boolean muted) {
        ConversationState s = ConversationState.of(connected, connected, connected, muted, false, "", "", true);
        return RoseVisualState.from(s, 0f, 0f, true, true);
    }

    static Bitmap render(RoseVisualState v, int sizeDp) {
        Context context = RuntimeEnvironment.getApplication();
        RosePresenceView view = new RosePresenceView(context);
        view.show(v);
        int px = Math.round(sizeDp * context.getResources().getDisplayMetrics().density);
        view.measure(View.MeasureSpec.makeMeasureSpec(px, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(px, View.MeasureSpec.EXACTLY));
        view.layout(0, 0, px, px);
        Bitmap bitmap = Bitmap.createBitmap(px, px, Bitmap.Config.ARGB_8888);
        view.draw(new Canvas(bitmap));
        return bitmap;
    }

    static String save(Bitmap bitmap, String name) throws Exception {
        OUT.mkdirs();
        File file = new File(OUT, name + ".png");
        try (FileOutputStream out = new FileOutputStream(file)) {
            assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, out));
        }
        byte[] bytes = java.nio.file.Files.readAllBytes(file.toPath());
        StringBuilder hex = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(bytes)) hex.append(String.format("%02x", b));
        System.out.println("RENDER " + name + " " + bitmap.getWidth() + "x" + bitmap.getHeight() + " sha256=" + hex);
        return hex.toString();
    }

    /** Opaque pixels and their mean colour inside the central disc: proves something was actually drawn. */
    static int[] coverage(Bitmap b) {
        int opaque = 0; long r = 0, g = 0, bl = 0;
        for (int y = 0; y < b.getHeight(); y++) for (int x = 0; x < b.getWidth(); x++) {
            int c = b.getPixel(x, y);
            if ((c >>> 24) > 200) { opaque++; r += (c >> 16) & 255; g += (c >> 8) & 255; bl += c & 255; }
        }
        return opaque == 0 ? new int[]{0, 0, 0, 0} : new int[]{opaque, (int) (r / opaque), (int) (g / opaque), (int) (bl / opaque)};
    }

    @Test public void nativeGraphicsIsActive() {
        assertEquals(GraphicsMode.Mode.NATIVE, org.robolectric.config.ConfigurationRegistry.get(GraphicsMode.Mode.class));
        assertEquals(35, android.os.Build.VERSION.SDK_INT);
    }

    @Test public void conversationAndTrayStatesRender() throws Exception {
        RoseVisualState on = state(true, false), muted = state(true, true), off = state(false, false);
        assertEquals(RoseVisualState.Mic.ROSE, on.mic);
        assertEquals(RoseVisualState.Mic.SILVER, muted.mic);
        assertEquals(RoseVisualState.Connection.WIDE_GAP, off.connection);
        int stage = 220, tray = RosePresenceModel.viewSizeFor(RosePresenceModel.TRAY_SPHERE_DP);
        Bitmap stageOn = render(on, stage), stageMuted = render(muted, stage), trayOn = render(on, tray), trayOff = render(off, tray);
        String a = save(stageOn, "stage-connected-mic-on"), b = save(stageMuted, "stage-connected-muted");
        String c = save(trayOn, "tray-connected-mic-on"), d = save(trayOff, "tray-disconnected");
        for (Bitmap bitmap : new Bitmap[]{stageOn, stageMuted, trayOn, trayOff}) {
            int[] cov = coverage(bitmap);
            System.out.println("COVERAGE opaque=" + cov[0] + " mean=#" + String.format("%02x%02x%02x", cov[1], cov[2], cov[3]));
            assertTrue("nonblank: the sphere is drawn", cov[0] > bitmap.getWidth() * bitmap.getHeight() / 10);
        }
        // Negative controls: a real state change changes the image; mic on is warm (rose), muted is cool (silver).
        assertNotEquals(a, b);
        assertNotEquals(c, d);
        int[] warm = coverage(stageOn), cool = coverage(stageMuted);
        assertTrue("mic on is warmer than muted", warm[1] - warm[3] > cool[1] - cool[3]);
        assertTrue("muted is blue steel", cool[3] > cool[1]);
    }

    /** A real View carrying the production tray/tile drawables at their Home sizes, drawn the same way. */
    static Bitmap drawable(android.graphics.drawable.Drawable d, int wDp, int hDp, boolean pressed) {
        Context context = RuntimeEnvironment.getApplication();
        float density = context.getResources().getDisplayMetrics().density;
        View view = new View(context);
        view.setBackground(d);
        view.setPressed(pressed);
        int w = Math.round(wDp * density), h = Math.round(hDp * density);
        view.measure(View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(h, View.MeasureSpec.EXACTLY));
        view.layout(0, 0, w, h);
        Bitmap bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        bitmap.eraseColor(0xff6f8fb0); // a fixed mid-blue backdrop standing in for the wallpaper, so glass shows
        view.draw(new Canvas(bitmap));
        return bitmap;
    }

    @Test public void trayWellAndGlassTileRender() throws Exception {
        float density = RuntimeEnvironment.getApplication().getResources().getDisplayMetrics().density;
        int pocket = 2 * TrayMaterial.POCKET_DP;
        Bitmap well = drawable(new TrayMaterial.Well(density), 104 + pocket, 66 + pocket, false);
        Bitmap pressed = drawable(new TrayMaterial.Well(density), 104 + pocket, 66 + pocket, true);
        Bitmap tray = drawable(new TrayMaterial.Tray(density), 363, 92, false);
        Bitmap tile = drawable(new HomeGlassTile(density), 64, 64, false);
        String w = save(well, "home-well"), p = save(pressed, "home-well-pressed");
        save(tray, "home-tray"); save(tile, "app-glass-tile");
        assertNotEquals("pressed state changes the well", w, p);
        int backdrop = 0xff6f8fb0;
        assertNotEquals(backdrop, tile.getPixel(tile.getWidth() / 2, tile.getHeight() / 4));
        assertNotEquals(backdrop, tray.getPixel(tray.getWidth() / 2, tray.getHeight() / 2));
    }
}
