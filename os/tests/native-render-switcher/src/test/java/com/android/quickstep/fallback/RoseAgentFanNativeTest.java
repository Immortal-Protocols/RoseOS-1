package com.android.quickstep.fallback;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.animation.ValueAnimator;
import android.app.Activity;
import android.content.pm.ApplicationInfo;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.GradientDrawable;
import android.os.Looper;
import android.os.SystemClock;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import java.io.File;
import java.io.FileOutputStream;
import java.security.MessageDigest;
import java.time.Duration;
import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * #410: the switcher's production Rose carousel (RoseAgentFan + RoseFan, compiled unchanged from the Launcher3
 * overlay) run through the real Android framework View, touch, scroll and animation code that Robolectric's SDK 35
 * runtime carries, at the frozen reference's 384x832 dp screen, xxhdpi. Touches are real MotionEvents dispatched from
 * the root, so a card consumes ACTION_DOWN and the fan intercepts the drag exactly as on a device; scrolling is stepped
 * frame by frame. Host-rendered native output and framework behaviour: not a device, not hardware composition, not
 * touch feel or timing.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class RoseAgentFanNativeTest {
    static final File OUT = new File(System.getProperty("render.out", "target/render"));
    float density;
    Activity activity;
    FrameLayout root;
    RoseAgentFan fan;
    LinearLayout cards;
    int clicks;

    @After public void restoreAnimations() throws Exception { durationScale(1f); }

    static void durationScale(float scale) throws Exception {
        // The system's "Remove animations" (animator duration scale 0); ValueAnimator.areAnimatorsEnabled reads it.
        ValueAnimator.class.getMethod("setDurationScale", float.class).invoke(null, scale);
    }

    int px(float dp) { return Math.round(dp * density); }

    /** A carousel of n Oxblood cards, as RoseAgentStrip builds them (clickable plates of the card size). */
    void build(int n, int layoutDirection) {
        // Launcher3 declares android:supportsRtl="true" (quickstep/AndroidManifest.xml); the test app must too, or the
        // framework resolves every view to left to right (View.hasRtlSupport reads the application's flag).
        RuntimeEnvironment.getApplication().getApplicationInfo().flags |= ApplicationInfo.FLAG_SUPPORTS_RTL;
        activity = Robolectric.buildActivity(Activity.class).setup().get();
        density = activity.getResources().getDisplayMetrics().density;
        root = new FrameLayout(activity);
        root.setLayoutDirection(layoutDirection);
        root.setBackgroundColor(0xFF170609);
        fan = new RoseAgentFan(activity);
        cards = new LinearLayout(activity);
        cards.setOrientation(LinearLayout.HORIZONTAL);
        fan.addView(cards);
        fill(n);
        root.addView(fan, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, px(RoseFan.CARD_HEIGHT_DP)));
        activity.setContentView(root);
        idle();
    }

    void fill(int n) {
        cards.removeAllViews();
        for (int i = 0; i < n; i++) {
            View card = new View(activity);
            GradientDrawable plate = new GradientDrawable();
            plate.setCornerRadius(px(RoseFan.CARD_RADIUS_DP));
            plate.setColor(0xFF1A0A0D);
            plate.setStroke(Math.max(1, px(1)), i == 1 ? 0xFFD2B9AB : 0x29ECD5C6);
            card.setBackground(plate);
            card.setOnClickListener(v -> clicks++);
            card.setFocusable(true);
            cards.addView(card, new LinearLayout.LayoutParams(px(RoseFan.CARD_WIDTH_DP), px(RoseFan.CARD_HEIGHT_DP)));
        }
    }

    void idle() { shadowOf(Looper.getMainLooper()).idle(); }

    /** Runs animation frames until the scroll stops (computeScroll is what a drawn frame calls). */
    void frames() {
        int last = Integer.MIN_VALUE;
        for (int i = 0; i < 200; i++) {
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(16));
            fan.computeScroll();
            if (fan.getScrollX() == last && i > 30) break;
            last = fan.getScrollX();
        }
        idle();
    }

    float cardCentre(int index) {
        View card = cards.getChildAt(index);
        return cards.getLeft() + card.getLeft() + card.getWidth() / 2f;
    }

    /** The carousel rests exactly on a card: that card's centre is the viewport's centre (within a pixel). */
    void assertOnACard(String why) {
        int index = fan.centred();
        float viewportCentre = fan.getScrollX() + fan.getWidth() / 2f;
        boolean clampedEnd = fan.getScrollX() == 0 || !fan.canScrollHorizontally(1);
        assertTrue(why + ": card " + index + " centre " + cardCentre(index) + " vs viewport centre " + viewportCentre,
                Math.abs(cardCentre(index) - viewportCentre) <= 1.01f
                        || (clampedEnd && Math.abs(cardCentre(index) - viewportCentre) <= 1.01f));
    }

    long t = 1000;

    void touch(int action, float x, float y) {
        MotionEvent e = MotionEvent.obtain(1000, t, action, x, y, 0);
        root.dispatchTouchEvent(e);
        e.recycle();
    }

    /** A drag from the centre card by dx (px) over the given number of 16 ms steps, then UP (or CANCEL). */
    void drag(float dx, int steps, int end) {
        float y = fan.getTop() + fan.getHeight() / 2f, x = root.getWidth() / 2f;
        t += 50;
        touch(MotionEvent.ACTION_DOWN, x, y);
        for (int i = 1; i <= steps; i++) {
            t += 16;
            touch(MotionEvent.ACTION_MOVE, x + dx * i / steps, y);
        }
        t += 16;
        touch(end, x + dx, y);
        idle();
    }

    static void save(View view, String name) throws Exception {
        Bitmap bitmap = Bitmap.createBitmap(view.getWidth(), view.getHeight(), Bitmap.Config.ARGB_8888);
        view.draw(new Canvas(bitmap));
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

    @Test public void entryPlacesTheSecondCardAndEachCardTakesTheReferenceTransform() throws Exception {
        build(5, View.LAYOUT_DIRECTION_LTR);
        fan.place(RoseFan.initial(5));
        idle();
        assertEquals("three or more: the second card first", 1, fan.centred());
        assertOnACard("placed on entry");
        // The frozen reference's computed transforms (k = -1, 0, +1, +2 clamped to 1.4).
        float[][] expect = {{40, 40, .8f}, {0, 0, 1}, {-40, -40, .8f}, {-56, -56, .8f}, {-56, -56, .8f}};
        for (int i = 0; i < 5; i++) {
            View card = cards.getChildAt(i);
            assertEquals("card " + i + " shift", expect[i][0] * density, card.getTranslationX(), 0.5f);
            assertEquals("card " + i + " turn", expect[i][1], card.getRotationY(), 0.01f);
            assertEquals("card " + i + " scale", expect[i][2], card.getScaleX(), 0.001f);
        }
        assertTrue("the centre card draws and takes touches on top",
                cards.getChildAt(1).getTranslationZ() > cards.getChildAt(0).getTranslationZ());
        save(root, "switcher-rose-fan-entry-5");
    }

    @Test public void aDragCancelledMidwayStillSettlesOnACard() throws Exception {
        build(5, View.LAYOUT_DIRECTION_LTR);
        fan.place(1);
        idle();
        drag(-0.6f * px(RoseFan.CARD_WIDTH_DP), 12, MotionEvent.ACTION_MOVE);
        save(root, "switcher-rose-fan-mid-drag");
        t += 16;
        touch(MotionEvent.ACTION_CANCEL, root.getWidth() / 2f, fan.getTop() + fan.getHeight() / 2f);
        frames();
        assertOnACard("a cancelled drag (e.g. the system gesture took it)");
        assertEquals("a cancelled drag never opens the card", 0, clicks);
        save(root, "switcher-rose-fan-after-cancel");
    }

    @Test public void aFlingMovesExactlyOneCard() throws Exception {
        build(6, View.LAYOUT_DIRECTION_LTR);
        fan.place(1);
        idle();
        drag(-0.45f * px(RoseFan.CARD_WIDTH_DP), 2, MotionEvent.ACTION_UP);
        frames();
        assertEquals("a quick flick moves one card, never skipping work", 2, fan.centred());
        assertOnACard("after a fling");
    }

    @Test public void aSlowDragStartingOnACardAfterAFlingStillSettles() throws Exception {
        build(6, View.LAYOUT_DIRECTION_LTR);
        fan.place(1);
        idle();
        drag(-0.45f * px(RoseFan.CARD_WIDTH_DP), 2, MotionEvent.ACTION_UP);
        frames();
        // The regression the review found: the second gesture starts on a card, so the fan sees no ACTION_DOWN.
        drag(-0.3f * px(RoseFan.CARD_WIDTH_DP), 30, MotionEvent.ACTION_UP);
        frames();
        assertOnACard("a slow drag after a fling");
    }

    @Test public void reEntryPlacesAfreshAndAMembershipChangeKeepsTheCentredCard() throws Exception {
        build(5, View.LAYOUT_DIRECTION_LTR);
        fan.place(3);
        idle();
        assertEquals(3, fan.centred());
        // Leaving and coming back: placed afresh, not where it was left.
        fan.unplace();
        assertFalse(fan.placed());
        fill(4);
        fan.place(RoseFan.initial(4));
        idle();
        assertEquals("re-entry: the second card first again", 1, fan.centred());
        assertOnACard("re-entry");
        // A card before the centred one leaves: the same card stays in front (RoseFan.keep by identity).
        java.util.List<String> before = java.util.Arrays.asList("a", "b", "c", "d");
        java.util.List<String> after = java.util.Arrays.asList("b", "c", "d");
        fill(3);
        fan.place(RoseFan.keep(before, fan.centred(), after));
        idle();
        assertEquals("card b keeps the centre", 0, fan.centred());
        assertOnACard("after a card left");
    }

    @Test public void withAnimationsOffEveryMoveIsImmediate() throws Exception {
        durationScale(0f);
        assertFalse(ValueAnimator.areAnimatorsEnabled());
        build(5, View.LAYOUT_DIRECTION_LTR);
        fan.place(1);
        idle();
        drag(-0.7f * px(RoseFan.CARD_WIDTH_DP), 20, MotionEvent.ACTION_UP);
        // No animation frames are run: the settle must already be complete.
        assertOnACard("reduced motion: settled at once on release");
        int before = fan.centred();
        assertTrue(fan.executeKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_DPAD_RIGHT)));
        assertEquals("a key step is immediate too", before + 1, fan.centred());
        assertOnACard("reduced motion: key step");
        save(root, "switcher-rose-fan-reduced-motion");
    }

    @Test public void rightToLeftPlacesAndStepsByWhereTheCardsAre() throws Exception {
        build(4, View.LAYOUT_DIRECTION_RTL);
        assertTrue(fan.getLayoutDirection() == View.LAYOUT_DIRECTION_RTL);
        fan.place(0);
        idle();
        assertTrue("right to left lays the first card out on the right",
                cards.getChildAt(0).getLeft() > cards.getChildAt(3).getLeft());
        assertEquals(0, fan.centred());
        assertOnACard("RTL entry");
        // A step to the right of the screen is the previous card in right to left: at the first card, nothing to take.
        assertFalse("at an end the key is not taken", fan.executeKeyEvent(
                new KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_DPAD_RIGHT)));
        assertTrue(fan.executeKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_DPAD_LEFT)));
        frames();
        assertEquals("left is the next card in right to left", 1, fan.centred());
        assertOnACard("RTL step");
        save(root, "switcher-rose-fan-rtl");
    }

    @Test public void keysAndScreenReaderScrollMoveExactlyOneCard() throws Exception {
        build(3, View.LAYOUT_DIRECTION_LTR);
        fan.place(0);
        idle();
        assertFalse("at the first card, left is not taken (focus can leave)", fan.executeKeyEvent(
                new KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_DPAD_LEFT)));
        assertTrue(fan.performAccessibilityAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD, null));
        frames();
        assertEquals(1, fan.centred());
        assertOnACard("screen reader scroll forward");
        assertTrue(fan.executeKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_SPACE)));
        frames();
        assertEquals(2, fan.centred());
        assertFalse("at the last card, space is not taken", fan.executeKeyEvent(
                new KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_SPACE)));
        assertOnACard("keys");
    }

    @Test public void aTapOnACardStillReachesIt() throws Exception {
        build(3, View.LAYOUT_DIRECTION_LTR);
        fan.place(1);
        idle();
        float x = root.getWidth() / 2f, y = fan.getTop() + fan.getHeight() / 2f;
        t += 50;
        touch(MotionEvent.ACTION_DOWN, x, y);
        t += 40;
        touch(MotionEvent.ACTION_UP, x, y);
        idle();
        assertEquals("the centre card receives the tap", 1, clicks);
        assertOnACard("a tap moves nothing");
    }
}
