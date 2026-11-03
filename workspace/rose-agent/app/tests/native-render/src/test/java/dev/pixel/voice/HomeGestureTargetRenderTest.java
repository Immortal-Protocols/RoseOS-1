package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;

import android.app.Activity;
import android.graphics.RectF;
import android.graphics.drawable.ColorDrawable;
import android.view.View;
import android.widget.FrameLayout;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.shadows.ShadowLooper;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * #410 gesture_nav_contract_v1 landing target (HomeGestureTarget): the window rect of the closing app's 59dp glass mark
 * on the page Home is showing, matched by exact package and activity, or the System folder's cell for a folder app;
 * nothing for a missing, hidden, clipped or off-page tile. Real HomeAppPage and HomeAppTile cells in an attached window.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class HomeGestureTargetRenderTest {
    static final int W = 384, H = 832;

    static View cell(Activity a, String pkg, String cls) {
        View t = HomeAppTile.tile(a, new ColorDrawable(0xff3a5f91), pkg, false, true);
        t.setTag(GestureContract.tag(pkg, cls));
        return t;
    }
    static View folder(Activity a) {
        View t = HomeAppTile.tile(a, new HomeGlyph("system"), "System", true, true);
        t.setTag(GestureContract.FOLDER_TAG);
        return t;
    }
    static HomeAppPage page(Activity a, View... cells) {
        HomeAppPage p = new HomeAppPage(a, 4, null);
        for (View c : cells) p.addView(c);
        p.setPadding(HomeAppLayout.MARGIN_DP, HomeAppLayout.TOP_DP, HomeAppLayout.MARGIN_DP, HomeAppLayout.EDGE_DP + HomeAppLayout.BAND_BOTTOM_DP);
        FrameLayout root = new FrameLayout(a);
        root.addView(p, new FrameLayout.LayoutParams(-1, -1));
        a.setContentView(root);
        ShadowLooper.idleMainLooper();
        root.measure(View.MeasureSpec.makeMeasureSpec(W, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(H, View.MeasureSpec.EXACTLY));
        root.layout(0, 0, W, H);
        return p;
    }

    @Test public void theClosingAppsMarkOnThePageHomeShows() {
        Activity a = Robolectric.buildActivity(Activity.class).setup().get();
        View calc = cell(a, "com.android.calculator2", "com.android.calculator2.Calculator");
        View maps = cell(a, "com.google.android.apps.maps", "com.google.android.maps.MapsActivity");
        HomeAppPage p = page(a, calc, maps, folder(a));
        RectF r = HomeGestureTarget.find(p, GestureContract.tag("com.google.android.apps.maps", "com.google.android.maps.MapsActivity"), false, W, H);
        assertNotNull("a visible tile is the target", r);
        View mark = ((android.view.ViewGroup) maps).getChildAt(0);
        int[] at = new int[2]; mark.getLocationInWindow(at);
        assertEquals("the 59dp mark, not the label", HomeAppLayout.TILE_DP, r.width(), 0.5f);
        assertEquals(HomeAppLayout.TILE_DP, r.height(), 0.5f);
        assertEquals("in window coordinates", at[0], r.left, 0.5f);
        assertEquals(at[1], r.top, 0.5f);
        // Exact package and activity only.
        assertNull("another activity of the same app is not this tile",
            HomeGestureTarget.find(p, GestureContract.tag("com.google.android.apps.maps", "com.google.android.maps.Other"), false, W, H));
        assertNull("an app not on this page: no target", HomeGestureTarget.find(p, GestureContract.tag("com.example", "com.example.Main"), false, W, H));
        // A System folder app lands on the folder's cell.
        RectF f = HomeGestureTarget.find(p, GestureContract.tag("com.android.settings", "com.android.settings.Settings"), true, W, H);
        assertNotNull("a folder app: the folder cell", f);
        assertEquals(HomeAppLayout.TILE_DP, f.width(), 0.5f);
        assertNull("not a folder app: never the folder", HomeGestureTarget.find(p, GestureContract.tag("com.android.settings", "com.android.settings.Settings"), false, W, H));
    }

    @Test public void hiddenClippedOrOutsideIsNoTarget() {
        Activity a = Robolectric.buildActivity(Activity.class).setup().get();
        View calc = cell(a, "com.android.calculator2", "com.android.calculator2.Calculator");
        HomeAppPage p = page(a, calc);
        String tag = GestureContract.tag("com.android.calculator2", "com.android.calculator2.Calculator");
        assertNotNull(HomeGestureTarget.find(p, tag, false, W, H));
        calc.setVisibility(View.INVISIBLE);
        assertNull("hidden: no target", HomeGestureTarget.find(p, tag, false, W, H));
        calc.setVisibility(View.VISIBLE);
        p.setTranslationX(-W); // an off-screen page (the pager's neighbour)
        assertNull("off screen: no target", HomeGestureTarget.find(p, tag, false, W, H));
        p.setTranslationX(-60); // the first mark (45dp in) partly outside the window
        assertNull("partly clipped: no target", HomeGestureTarget.find(p, tag, false, W, H));
        p.setTranslationX(0);
        assertNull("a window smaller than the rect: no target", HomeGestureTarget.find(p, tag, false, 40, 40));
        assertNull("no page: no target", HomeGestureTarget.find(null, tag, false, W, H));
    }
}
