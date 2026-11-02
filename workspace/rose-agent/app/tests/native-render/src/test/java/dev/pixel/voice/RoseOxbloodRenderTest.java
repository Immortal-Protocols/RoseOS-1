package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
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
 * #410 Rose conversation at the reference's own geometry (384x832dp at mdpi, 1dp = 1px; main f6503a0 browser-check
 * 05/06/08). The production drawables (OxbloodField.ROSE, RosePearl, OxbloodRose.WorkButton/Pill/PillGlyph) and the
 * production caption rule (OxbloodRose.caption over ConversationState and RoseVisualState) are composed at
 * RoseConversationActivity's coordinates (header 49dp down, caption 88dp below it, the pill 42dp above the bottom).
 * The activity itself needs the voice service and app resources, so it is not instantiated here; not a device.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class RoseOxbloodRenderTest {
    static final File OUT = new File(System.getProperty("render.out", "target/render"));
    static final int W = 384, H = 832, HEAD = 49;
    static Context context() { return RuntimeEnvironment.getApplication(); }
    static View box(android.graphics.drawable.Drawable d) { View v = new View(context()); v.setBackground(d); return v; }
    static TextView text(String s, float sp, int color) { TextView t = new TextView(context()); t.setText(s); t.setTextSize(sp); t.setTextColor(color); t.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL)); t.setIncludeFontPadding(false); return t; }

    static FrameLayout rose(ConversationState state, RoseVisualState visual, boolean working) {
        Context c = context();
        FrameLayout root = new FrameLayout(c);
        root.setBackground(new OxbloodField(OxbloodField.ROSE));
        LinearLayout head = new LinearLayout(c); head.setGravity(Gravity.CENTER_VERTICAL);
        RosePearl pearl = new RosePearl(1f, 16);
        pearl.set(state.active() && state.muted, visual.surround == RoseVisualState.Surround.LISTENING, visual.surround == RoseVisualState.Surround.SPEAKING ? 0f : -1f);
        head.addView(box(pearl), new LinearLayout.LayoutParams(28, 28));
        TextView title = text("Rose", 23, OxbloodRose.INK);
        LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(-2, -2); tl.leftMargin = 7; head.addView(title, tl);
        FrameLayout.LayoutParams hp = new FrameLayout.LayoutParams(-2, 44, Gravity.TOP | Gravity.START); hp.topMargin = HEAD; hp.leftMargin = 24; root.addView(head, hp);
        OxbloodRose.WorkButton wb = new OxbloodRose.WorkButton(1f); wb.set(working, false);
        FrameLayout.LayoutParams wp = new FrameLayout.LayoutParams(44, 44, Gravity.TOP | Gravity.END); wp.topMargin = HEAD; wp.rightMargin = 18; root.addView(box(wb), wp);
        TextView caption = text(OxbloodRose.caption(state, visual), 15, OxbloodRose.INK);
        FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START); cp.topMargin = HEAD + 88; cp.leftMargin = 30; root.addView(caption, cp);
        TextView prompt = text("What would you like to do?", 26, OxbloodRose.INK); prompt.setLineSpacing(0, 1.2f);
        FrameLayout.LayoutParams pp = new FrameLayout.LayoutParams(324, -2, Gravity.TOP | Gravity.START); pp.topMargin = HEAD + 121 + 8; pp.leftMargin = 30; root.addView(prompt, pp);
        boolean live = state.active() || state.phase == ConversationState.Phase.CONNECTING;
        FrameLayout pill = new FrameLayout(c); pill.setBackground(new OxbloodRose.Pill(1f)); pill.setMinimumWidth(154); pill.setPadding(26, 0, 26, 0);
        LinearLayout row = new LinearLayout(c); row.setGravity(Gravity.CENTER);
        OxbloodRose.PillGlyph g = new OxbloodRose.PillGlyph(1f); g.set(live); row.addView(box(g), new LinearLayout.LayoutParams(15, 15));
        TextView label = text(live ? "Disconnect" : "Connect", 15, OxbloodRose.INK);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -2); lp.leftMargin = 11; row.addView(label, lp);
        pill.addView(row, new FrameLayout.LayoutParams(-2, -1, Gravity.CENTER));
        FrameLayout.LayoutParams plp = new FrameLayout.LayoutParams(-2, 44, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL); plp.bottomMargin = 42; root.addView(pill, plp);
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

    static ConversationState off() { return ConversationState.of(true, false, false, false, false, null, null, null); }
    static ConversationState connecting() { return ConversationState.of(true, false, false, false, true, null, null, null); }
    static ConversationState live(boolean muted) { return ConversationState.of(true, true, true, muted, false, null, null, null); }

    @Test public void captionsFollowTheRealState() {
        assertEquals("Disconnected", OxbloodRose.caption(off(), RoseVisualState.from(off(), Float.NaN, Float.NaN, true, true)));
        assertEquals("Connecting…", OxbloodRose.caption(connecting(), RoseVisualState.from(connecting(), Float.NaN, Float.NaN, true, true)));
        assertEquals("Listening", OxbloodRose.caption(live(false), RoseVisualState.from(live(false), .2f, 0f, true, true)));
        assertEquals("Microphone muted", OxbloodRose.caption(live(true), RoseVisualState.from(live(true), 0f, 0f, true, true)));
        assertEquals("Rose speaking", OxbloodRose.caption(live(false), RoseVisualState.from(live(false), .1f, .5f, true, true)));
        assertEquals("speaking needs fresh measured output, never a guess", "Listening",
            OxbloodRose.caption(live(false), RoseVisualState.from(live(false), Float.NaN, Float.NaN, true, true)));
        assertEquals("Disconnected", OxbloodRose.caption(null, null));
    }

    @Test public void roseStatesRender() throws Exception {
        Bitmap disconnected = draw(rose(off(), RoseVisualState.from(off(), Float.NaN, Float.NaN, true, true), false));
        save(disconnected, "oxblood-rose-disconnected");
        Bitmap listening = draw(rose(live(false), RoseVisualState.from(live(false), .2f, 0f, true, true), true));
        save(listening, "oxblood-rose-listening-working");
        Bitmap muted = draw(rose(live(true), RoseVisualState.from(live(true), 0f, 0f, true, true), false));
        save(muted, "oxblood-rose-muted");
        // Pearl at (38, 71): ruby when on, silver when the call is muted.
        int on = listening.getPixel(38, 71), off = muted.getPixel(38, 71);
        assertTrue("ruby pearl", ((on >> 16) & 255) > ((on >> 8) & 255) + 60);
        assertTrue("silver pearl when muted", Math.abs(((off >> 16) & 255) - (off & 255)) < 24);
        // The working badge (top right of the 44dp work target at x 322..366, y 49..93) only while work runs.
        int badgeOn = listening.getPixel(366 - 5, 49 + 5), badgeOff = disconnected.getPixel(366 - 5, 49 + 5);
        assertTrue("working badge is ruby", ((badgeOn >> 16) & 255) > 200 && ((badgeOn >> 8) & 255) < 120);
        assertTrue("no badge without running work", ((badgeOff >> 16) & 255) < 120);
        // The pill plate at the bottom centre.
        int plate = disconnected.getPixel(192, 832 - 42 - 22 + 16);
        assertTrue("dark pill plate", ((plate >> 16) & 255) < 90);
    }
}
