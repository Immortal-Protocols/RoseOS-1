package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.ColorDrawable;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.TextView;
import dev.pixel.voice.CurrentApps.*;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * #225/#156: the production WorkspaceOverviewPanel and view show exactly the apps currently running under Rose's
 * control, in the selected HiFi (ce126f56) cards and tray, measured and drawn by Robolectric's native graphics at SDK 35
 * in the HiFi's 384x832dp screen. Synthetic CurrentApps snapshots and labelled synthetic frames only; host-rendered
 * output, not a device or phone acceptance.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class WorkspaceOverviewRenderTest {
    static final String CALC = "com.android.calculator2", MAPS = "com.google.android.apps.maps";

    static final class Host implements WorkspaceOverviewPanel.Source, WorkspaceOverviewPanel.Actions {
        boolean live = true; Snapshot snapshot = new Snapshot("r1", 1, true, "", Collections.<App>emptyList());
        final List<String> calls = new ArrayList<>();
        final List<WorkspaceOverviewPreview.Request> previews = new ArrayList<>();
        public boolean connected() { return live; }
        public Snapshot currentApps() { return snapshot; }
        public void watch(App app, WorkspaceOverview.Binding atTap) { calls.add("watch:" + app.entryId + ":" + atTap.sessionId); }
        public void endCall() { calls.add("end"); }
        public void requestPreview(WorkspaceOverviewPreview.Request r) { previews.add(r); }
        void report(String reg, long gen, App... apps) { snapshot = new Snapshot(reg, gen, true, "", Arrays.asList(apps)); }
    }
    static App app(String id, String pkg) { return new App(id, pkg, CurrentApps.STATE_RUNNING, null); }
    static App viewable(String id, String pkg, String session, String task) { return new App(id, pkg, CurrentApps.STATE_RUNNING, new Preview(session, task)); }
    static WorkspaceOverviewPanel panel(Host h) {
        WorkspaceOverviewPanel p = new WorkspaceOverviewPanel(RuntimeEnvironment.getApplication());
        p.bind(h, h); p.setIcons(pkg -> new ColorDrawable(0xffd97757)); p.setLabels(pkg -> CALC.equals(pkg) ? "Calculator" : MAPS.equals(pkg) ? "Maps" : "");
        p.open(); layout(p); return p;
    }
    static int dp(float v) { return Math.round(v * RuntimeEnvironment.getApplication().getResources().getDisplayMetrics().density); }
    static void layout(View v) {
        v.measure(View.MeasureSpec.makeMeasureSpec(dp(384), View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(dp(832), View.MeasureSpec.EXACTLY));
        v.layout(0, 0, dp(384), dp(832));
    }
    static Bitmap draw(View v) { layout(v); Bitmap b = Bitmap.createBitmap(dp(384), dp(832), Bitmap.Config.ARGB_8888); v.draw(new Canvas(b)); return b; }
    static void all(View v, List<View> out) { out.add(v); if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) all(((ViewGroup) v).getChildAt(i), out); }
    static List<View> all(View v) { List<View> out = new ArrayList<>(); all(v, out); return out; }
    static boolean visible(View v) { for (View p = v; p != null; p = p.getParent() instanceof View ? (View) p.getParent() : null) if (p.getVisibility() != View.VISIBLE) return false; return true; }
    static List<String> texts(View root) { List<String> t = new ArrayList<>(); for (View v : all(root)) if (v instanceof TextView && visible(v)) t.add(((TextView) v).getText().toString()); return t; }
    static View tagged(View root, String tag) { for (View v : all(root)) if (tag.equals(v.getTag())) return v; return null; }
    static List<View> taggedPrefix(View root, String prefix) { List<View> o = new ArrayList<>(); for (View v : all(root)) if (v.getTag() instanceof String && ((String) v.getTag()).startsWith(prefix)) o.add(v); return o; }
    static View described(View root, String d) { for (View v : all(root)) if (v.getContentDescription() != null && d.equals(v.getContentDescription().toString())) return v; return null; }
    static int[] at(View v) { int x = 0, y = 0; for (View p = v; p != null; p = p.getParent() instanceof View ? (View) p.getParent() : null) { x += p.getLeft() - p.getScrollX(); y += p.getTop() - p.getScrollY(); } return new int[]{x, y}; }
    static Bitmap synthetic(int w, int h) {
        Bitmap b = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888); Canvas c = new Canvas(b);
        android.graphics.Paint p = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        p.setShader(new android.graphics.LinearGradient(0, 0, 0, h, 0xff2f6be6, 0xffe8eef7, android.graphics.Shader.TileMode.CLAMP)); c.drawRect(0, 0, w, h, p);
        p.setShader(null); p.setColor(0xffffffff); p.setTextSize(28); c.drawText("SYNTHETIC", 20, 60, p); return b;
    }
    static WorkspaceOverviewPreview.Response ok(String session, String task, String pkg, Bitmap img) {
        return new WorkspaceOverviewPreview.Response("ok", session, task, pkg, System.currentTimeMillis(), img.getWidth(), img.getHeight(), "image/jpeg", 30000, img);
    }
    static ImageView preview(View root) { View v = tagged(root, "preview"); return v instanceof ImageView ? (ImageView) v : null; }

    /** Selected HiFi structure, current apps only: no heading, no push buttons, the three-well tray, 250dp cards in two columns. */
    @Test public void currentAppsInTheSelectedHiFi() throws Exception {
        Host h = new Host(); h.report("r1", 2, app("s1/calc", CALC), app("s2/maps", MAPS));
        WorkspaceOverviewPanel p = panel(h);
        assertFalse(texts(p).contains(WorkspaceOverview.TITLE));
        for (View v : all(p)) assertFalse("no Android push buttons", v instanceof Button);
        View back = described(p, "Back"), mid = described(p, "End call"), self = described(p, "Rose’s apps, current screen");
        assertNotNull(back); assertNotNull(mid); assertNotNull(self);
        int[] b = at(back), m = at(mid), s = at(self);
        assertTrue("three wells in one tray", b[1] == m[1] && m[1] == s[1] && b[0] < m[0] && m[0] < s[0]);
        View calc = tagged(p, "app:s1/calc"), maps = tagged(p, "app:s2/maps");
        assertNotNull(calc); assertNotNull(maps);
        assertEquals(dp(250), calc.getHeight()); assertEquals(calc.getWidth(), maps.getWidth(), 1);
        int[] a = at(calc), c = at(maps);
        assertEquals("same row", a[1], c[1]); assertEquals("12dp gap", dp(12), c[0] - (a[0] + calc.getWidth()), 1);
        assertEquals("24dp margin", dp(24), a[0], 1); assertEquals("64dp top", dp(64), a[1], 1);
        List<String> t = texts(p);
        assertTrue("real app names", t.contains("Calculator") && t.contains("Maps"));
        assertEquals("no live view: said plainly on each card", 2, Collections.frequency(t, WorkspaceOverviewView.LIVE_UNAVAILABLE));
        assertTrue("no task history or result cards", taggedPrefix(p, "task:").isEmpty() && taggedPrefix(p, "workspace").isEmpty());
        boolean icon = false; for (View v : all(calc)) icon |= v instanceof ImageView && ((ImageView) v).getDrawable() != null;
        assertTrue("real app icon", icon);
        for (View st : taggedPrefix(p, "stop")) { assertEquals(dp(44), st.getWidth()); assertEquals(dp(44), st.getHeight()); }
        RosePresenceRenderTest.save(draw(p), "current-apps-two");
    }

    /** Launch adds, content change updates, close/release removes; reconnect and recreation never resurrect. */
    @Test public void launchChangeCloseReleaseReconnect() throws Exception {
        Host h = new Host();
        WorkspaceOverviewPanel p = panel(h);
        assertTrue("none running", texts(p).contains(WorkspaceOverview.EMPTY));
        h.report("r1", 2, app("s1/calc", CALC)); p.refresh(); layout(p);
        assertNotNull("launch adds", tagged(p, "app:s1/calc"));
        h.snapshot = new Snapshot("r1", 3, true, "", Arrays.asList(new App("s1/calc", CALC, CurrentApps.STATE_WAITING, null))); p.refresh(); layout(p);
        assertTrue("content change shown", texts(p).contains("Waiting"));
        h.report("r1", 4, app("s1/calc", CALC), app("s2/maps", MAPS)); p.refresh(); layout(p);
        h.report("r1", 5, app("s2/maps", MAPS)); p.refresh(); layout(p);
        assertNull("close removes that app", tagged(p, "app:s1/calc")); assertNotNull(tagged(p, "app:s2/maps"));
        h.report("r1", 6); p.refresh(); layout(p);
        assertTrue("control released: none", taggedPrefix(p, "app:").isEmpty() && texts(p).contains(WorkspaceOverview.EMPTY));
        h.report("r1", 5, app("s2/maps", MAPS)); p.refresh(); layout(p);
        assertTrue("an older report never resurrects", taggedPrefix(p, "app:").isEmpty());
        h.snapshot = Snapshot.unavailable("r2", 1, CurrentApps.NOT_REPORTED); p.refresh(); layout(p);
        assertTrue("reconnect: unavailable until the new source reports", texts(p).contains(CurrentApps.text(CurrentApps.NOT_REPORTED)));
        h.report("r1", 7, app("s2/maps", MAPS)); p.refresh(); layout(p);
        assertTrue("a late report from the replaced source is ignored", taggedPrefix(p, "app:").isEmpty());
        described(p, "Back").performClick(); p.open(); layout(p);
        assertTrue("re-entry shows the current report only", taggedPrefix(p, "app:").isEmpty());
        Host fresh = new Host(); fresh.report("r9", 1);
        WorkspaceOverviewPanel recreated = panel(fresh);
        assertTrue("recreation starts from its own source", taggedPrefix(recreated, "app:").isEmpty());
        h.snapshot = Snapshot.unavailable("r2", 2, CurrentApps.LOCKED); p.refresh(); layout(p);
        assertTrue("locked: the reason, no cards", texts(p).contains(CurrentApps.text(CurrentApps.LOCKED)) && taggedPrefix(p, "app:").isEmpty());
        RosePresenceRenderTest.save(draw(p), "current-apps-locked");
    }

    /** Live content only from the app's own verified handle; wrong/stale answers, Back, pause and teardown never show one. */
    @Test public void livePreviewForTheAppsOwnHandleOnly() throws Exception {
        Host h = new Host(); h.report("r1", 2, viewable("s1/calc", CALC, "s-1", "rose-ws-1"), app("s2/maps", MAPS));
        WorkspaceOverviewPanel p = panel(h);
        assertEquals("one request, for the app with a handle", 1, h.previews.size());
        WorkspaceOverviewPreview.Request r = h.previews.get(0);
        assertEquals("s-1", r.sessionId()); assertEquals("rose-ws-1", r.taskId());
        Bitmap img = synthetic(320, 693);
        p.previewAnswer(r, ok("s-1", "rose-ws-1", CALC, img)); layout(p);
        ImageView iv = preview(tagged(p, "app:s1/calc"));
        assertNotNull("its own card shows the frame", iv);
        assertTrue(((android.graphics.drawable.BitmapDrawable) iv.getDrawable()).getBitmap() == img);
        assertNull("the other app has no live view", preview(tagged(p, "app:s2/maps")));
        assertTrue(texts(tagged(p, "app:s2/maps")).contains(WorkspaceOverviewView.LIVE_UNAVAILABLE));
        RosePresenceRenderTest.save(draw(p), "current-apps-preview-synthetic");
        h.report("r1", 3, app("s2/maps", MAPS)); p.refresh(); layout(p);
        assertNull("the app closed: its frame is gone with it", preview(p));
        for (String[] wrong : new String[][]{{"s-2", "rose-ws-1", CALC}, {"s-1", "rose-ws-9", CALC}, {"s-1", "rose-ws-1", MAPS}}) {
            Host q = new Host(); q.report("r1", 2, viewable("s1/calc", CALC, "s-1", "rose-ws-1"));
            WorkspaceOverviewPanel pq = panel(q);
            pq.previewAnswer(q.previews.get(0), ok(wrong[0], wrong[1], wrong[2], synthetic(320, 693))); layout(pq);
            assertNull("wrong identity " + Arrays.toString(wrong), preview(pq));
        }
        Host s = new Host(); s.report("r1", 2, viewable("s1/calc", CALC, "s-1", "rose-ws-1"));
        WorkspaceOverviewPanel ps = panel(s);
        ps.previewAnswer(s.previews.get(0), new WorkspaceOverviewPreview.Response("ok", "s-1", "rose-ws-1", CALC, System.currentTimeMillis() - 60000, 320, 693, "image/jpeg", 30000, synthetic(320, 693))); layout(ps);
        assertNull("stale frame", preview(ps));
        Host b = new Host(); b.report("r1", 2, viewable("s1/calc", CALC, "s-1", "rose-ws-1"));
        WorkspaceOverviewPanel pb = panel(b);
        described(pb, "Back").performClick();
        pb.previewAnswer(b.previews.get(0), ok("s-1", "rose-ws-1", CALC, synthetic(320, 693)));
        for (int i = 0; i < 4; i++) pb.refresh();
        assertEquals("closed: no further requests", 1, b.previews.size());
        pb.open(); layout(pb); assertNull("a late answer after Back never lands", preview(pb));
        Host d = new Host(); d.report("r1", 2, viewable("s1/calc", CALC, "s-1", "rose-ws-1"));
        WorkspaceOverviewPanel pd = panel(d);
        pd.pause(); pd.refresh();
        pd.previewAnswer(d.previews.get(0), ok("s-1", "rose-ws-1", CALC, synthetic(320, 693))); layout(pd);
        assertNull("paused: nothing lands", preview(pd)); assertEquals(1, d.previews.size());
        pd.teardown(); pd.resume(); layout(pd);
        assertTrue("after teardown the old source's report never comes back", taggedPrefix(pd, "app:").isEmpty());
        Host n = new Host(); n.report("r1", 2, app("s1/calc", CALC));
        WorkspaceOverviewPanel pn = panel(n); for (int i = 0; i < 4; i++) pn.refresh();
        assertTrue("no handle: never a request", n.previews.isEmpty());
    }

    @Test public void tapsWatchOnlyWithTheAppsHandleAndOtherwiseSayWhy() throws Exception {
        Host h = new Host(); h.report("r1", 2, viewable("s1/calc", CALC, "s-1", "rose-ws-1"), app("s2/maps", MAPS));
        WorkspaceOverviewPanel p = panel(h);
        tagged(p, "app:s2/maps").performClick(); layout(p);
        assertTrue(texts(p).contains(WorkspaceOverviewPanel.WATCH_APP_UNAVAILABLE)); assertTrue(h.calls.isEmpty());
        tagged(p, "app:s1/calc").performClick();
        assertEquals(Arrays.asList("watch:s1/calc:s-1"), h.calls);
        tagged(tagged(p, "app:s2/maps"), "stop-unavailable").performClick(); layout(p);
        assertTrue(texts(p).contains(WorkspaceOverviewView.STOP_APP_UNAVAILABLE));
        described(p, "End call").performClick();
        assertEquals("end", h.calls.get(h.calls.size() - 1));
        h.live = false; p.refresh(); layout(p);
        assertNotNull("not connected: the middle well opens Rose", described(p, "Open Rose"));
    }
}
