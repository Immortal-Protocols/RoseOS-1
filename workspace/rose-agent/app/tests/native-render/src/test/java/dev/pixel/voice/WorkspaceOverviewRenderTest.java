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
        boolean route = true; int checks;
        public boolean stopRoute() { return route; }
        public void ownerStop(String sessionId, String taskId) { calls.add("owner_stop:" + sessionId + ":" + taskId); }
        public void checkStops() { checks++; }
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

    /** #410 Oxblood structure, current apps only: no heading, no push buttons, the pearl and Back to Rose at the top,
     * 276dp cards in two columns 18dp apart, starting 100dp down, 24dp in. */
    @Test public void currentAppsInTheSelectedHiFi() throws Exception {
        Host h = new Host(); h.report("r1", 2, app("s1/calc", CALC), app("s2/maps", MAPS));
        WorkspaceOverviewPanel p = panel(h);
        assertFalse(texts(p).contains(WorkspaceOverview.TITLE));
        for (View v : all(p)) assertFalse("no Android push buttons", v instanceof Button);
        View back = described(p, "Back to Rose");
        assertNotNull(back);
        int[] b = at(back);
        assertEquals("Back to Rose: 44dp, in the header row", dp(44), back.getHeight()); assertEquals(dp(40), b[1], 1);
        View calc = tagged(p, "app:s1/calc"), maps = tagged(p, "app:s2/maps");
        assertNotNull(calc); assertNotNull(maps);
        assertEquals(dp(276), calc.getHeight()); assertEquals(calc.getWidth(), maps.getWidth(), 1);
        int[] a = at(calc), c = at(maps);
        assertEquals("same row", a[1], c[1]); assertEquals("18dp gap", dp(18), c[0] - (a[0] + calc.getWidth()), 1);
        assertEquals("24dp margin", dp(24), a[0], 1); assertEquals("100dp top", dp(100), a[1], 1);
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
        described(p, "Back to Rose").performClick(); p.open(); layout(p);
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
        described(pb, "Back to Rose").performClick();
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
        // Back to Rose only closes the panel; nothing is sent and the work is unchanged.
        int before = h.calls.size();
        View back = described(p, "Back to Rose"); assertNotNull(back); back.performClick();
        assertFalse(p.isOpen()); assertEquals("Back sends nothing", before, h.calls.size());
        Bitmap shot = draw(panel(h));
        java.io.File out = new java.io.File(System.getProperty("render.out", "target/render")); out.mkdirs();
        try (java.io.FileOutputStream o = new java.io.FileOutputStream(new java.io.File(out, "oxblood-background-work.png"))) { shot.compress(Bitmap.CompressFormat.PNG, 100, o); }
    }

    // ---------- Agreed two-app contract: snapshot membership, per-session bindings, Stop and live view ----------
    static List<CurrentApps.Binding> pair(String status1) {
        return Arrays.asList(new CurrentApps.Binding(0, "s-0", "rose-ws-0", CALC, "ready", true, true), new CurrentApps.Binding(1, "s-1", "rose-ws-1", MAPS, status1, true, true));
    }
    static Snapshot twoApps(long gen, String status1) {
        return CurrentApps.fromBindings("r", "live", gen, Arrays.asList(new CurrentApps.ReplyApp(CALC, true, true, "s-0"), new CurrentApps.ReplyApp(MAPS, true, true, "s-1")), pair(status1), "e-1", true);
    }

    @Test public void twoSessionsTwoCardsEachItsOwnLiveView() throws Exception {
        CurrentApps.SessionStop.class.getName(); WorkspaceOverviewPanel.STOPS.clear();
        Host h = new Host(); h.snapshot = twoApps(1, "ready");
        WorkspaceOverviewPanel p = panel(h);
        assertNotNull(tagged(p, "app:s-0/" + CALC)); assertNotNull(tagged(p, "app:s-1/" + MAPS));
        assertEquals("one preview request per ready session", 2, h.previews.size());
        WorkspaceOverviewPreview.Request r0 = null, r1 = null;
        for (WorkspaceOverviewPreview.Request r : h.previews) { if (r.sessionId().equals("s-0")) r0 = r; else r1 = r; }
        assertEquals("rose-ws-0", r0.taskId()); assertEquals("rose-ws-1", r1.taskId());
        Bitmap a = synthetic(320, 693), b = synthetic(320, 600);
        p.previewAnswer(r0, ok("s-0", "rose-ws-0", CALC, a)); p.previewAnswer(r1, ok("s-1", "rose-ws-1", MAPS, b)); layout(p);
        assertTrue(((android.graphics.drawable.BitmapDrawable) preview(tagged(p, "app:s-0/" + CALC)).getDrawable()).getBitmap() == a);
        assertTrue(((android.graphics.drawable.BitmapDrawable) preview(tagged(p, "app:s-1/" + MAPS)).getDrawable()).getBitmap() == b);
        Host c = new Host(); c.snapshot = twoApps(1, "ready"); WorkspaceOverviewPanel pc = panel(c);
        WorkspaceOverviewPreview.Request c0 = c.previews.get(0);
        pc.previewAnswer(c0, ok(c0.sessionId().equals("s-0") ? "s-1" : "s-0", c0.taskId(), c0.sessionId().equals("s-0") ? CALC : MAPS, synthetic(320, 693))); layout(pc);
        assertNull("one session's frame never lands on the other's card", preview(pc));
        Host d = new Host(); d.snapshot = twoApps(1, "blocked"); WorkspaceOverviewPanel pd = panel(d);
        assertEquals("blocked session: no request", 1, d.previews.size());
        assertTrue(texts(tagged(pd, "app:s-1/" + MAPS)).contains("Waiting"));
        RosePresenceRenderTest.save(draw(p), "two-sessions-live-synthetic");
    }

    @Test public void boundAppsHaveNoWatchUntilThePerSessionViewer() throws Exception {
        WorkspaceOverviewPanel.STOPS.clear();
        Host h = new Host(); h.snapshot = twoApps(1, "ready");
        WorkspaceOverviewPanel p = panel(h);
        tagged(p, "app:s-1/" + MAPS).performClick(); layout(p);
        assertTrue("no second-session view: said plainly", texts(p).contains(WorkspaceOverviewPanel.WATCH_APP_UNAVAILABLE));
        for (String call : h.calls) assertFalse("never a watch for a bound app", call.startsWith("watch:"));
    }

    @Test public void lingeringAppsStayWithoutControls() throws Exception {
        WorkspaceOverviewPanel.STOPS.clear();
        Host h = new Host();
        h.snapshot = CurrentApps.fromBindings("r", "live", 3L, Arrays.asList(new CurrentApps.ReplyApp(CALC, false, false, null), new CurrentApps.ReplyApp(MAPS, true, true, "s-1")),
            Arrays.asList(new CurrentApps.Binding(1, "s-1", "rose-ws-1", MAPS, "ready", true, true)), "e-1", true);
        WorkspaceOverviewPanel p = panel(h);
        View lingering = tagged(p, "app:owned/" + CALC);
        assertNotNull("lingering app still listed", lingering);
        assertNotNull("its Stop is unavailable", tagged(lingering, "stop-unavailable"));
        assertNull(preview(lingering));
        assertEquals("only the bound session is previewed", 1, h.previews.size());
    }

    @Test public void stopAsksThenStopsTheWholeSessionAndConfirmsOnlyItsPair() throws Exception {
        WorkspaceOverviewPanel.STOPS.clear();
        Host h = new Host(); h.snapshot = twoApps(1, "ready");
        WorkspaceOverviewPanel p = panel(h);
        tagged(tagged(p, "app:s-1/" + MAPS), "stop").performClick(); layout(p);
        View sheet = tagged(p, "stop-sheet");
        assertNotNull("the HiFi sheet asks first", sheet);
        assertTrue(texts(sheet).contains(WorkspaceOverviewPanel.STOP_SHEET_TITLE));
        boolean names = false; for (String t : texts(sheet)) names |= t.contains("Maps");
        assertTrue("it names the session's apps", names);
        tagged(sheet, "sheet-keep").performClick(); layout(p);
        assertNull("Keep working closes it", tagged(p, "stop-sheet"));
        assertTrue("nothing sent", h.calls.isEmpty());
        tagged(tagged(p, "app:s-1/" + MAPS), "stop").performClick(); layout(p);
        tagged(tagged(p, "stop-sheet"), "sheet-stop").performClick(); layout(p);
        assertEquals(Arrays.asList("owner_stop:s-1:rose-ws-1"), h.calls);
        assertTrue("stopping, never stopped on the request", texts(tagged(p, "app:s-1/" + MAPS)).contains(WorkspaceOverview.STOP_STOPPING));
        assertNull("no second Stop while waiting", tagged(tagged(p, "app:s-1/" + MAPS), "stop"));
        assertNotNull("the other session's Stop is unaffected", tagged(tagged(p, "app:s-0/" + CALC), "stop"));
        p.stopAnswer("s-1", "rose-ws-1", false); layout(p);
        p.stopSummary("cleanup_failed", "user", "s-1", "rose-ws-1"); p.stopSummary("stopped", "user", "s-0", "rose-ws-0"); p.stopSummary("stopped", "user", "s-1", "rose-ws-9"); layout(p);
        assertTrue("accepted, cleanup_failed or another pair never confirm", texts(tagged(p, "app:s-1/" + MAPS)).contains(WorkspaceOverview.STOP_STOPPING));
        p.stopSummary("stopped", "user", "s-1", "rose-ws-1"); layout(p);
        assertTrue("confirmed", texts(p).contains(WorkspaceOverviewPanel.STOP_DONE_NOTE));
        assertNotNull("the app stays listed until the snapshot drops it", tagged(p, "app:s-1/" + MAPS));
        RosePresenceRenderTest.save(draw(p), "two-sessions-stop-confirmed");
    }

    @Test public void stopTimesOutToUnknownAndSurvivesReentry() throws Exception {
        WorkspaceOverviewPanel.STOPS.clear();
        Host h = new Host(); h.snapshot = twoApps(1, "ready");
        WorkspaceOverviewPanel p = panel(h);
        tagged(tagged(p, "app:s-0/" + CALC), "stop").performClick();
        tagged(tagged(p, "stop-sheet"), "sheet-stop").performClick(); layout(p);
        described(p, "Back to Rose").performClick(); p.open(); layout(p);
        assertTrue("a pending Stop survives Back and re-entry", texts(tagged(p, "app:s-0/" + CALC)).contains(WorkspaceOverview.STOP_STOPPING));
        synchronized (WorkspaceOverviewPanel.STOPS) { for (CurrentApps.SessionStop st : WorkspaceOverviewPanel.STOPS.values()) st.tick(System.currentTimeMillis() + CurrentApps.SessionStop.TIMEOUT_MS); }
        p.refresh(); layout(p);
        assertTrue("timeout: unknown, never stopped", texts(p).contains(WorkspaceOverviewPanel.STOP_UNKNOWN_NOTE) || hasUnknown());
        assertNotNull("an unknown Stop may be tried again", tagged(tagged(p, "app:s-0/" + CALC), "stop"));
        Host n = new Host(); n.route = false; n.snapshot = twoApps(1, "ready");
        WorkspaceOverviewPanel pn = panel(n);
        assertNull("no owner-Stop route: no Stop", tagged(pn, "stop"));
    }
    static boolean hasUnknown() { synchronized (WorkspaceOverviewPanel.STOPS) { for (CurrentApps.SessionStop st : WorkspaceOverviewPanel.STOPS.values()) if (st.phase == CurrentApps.SessionStop.Phase.UNKNOWN) return true; } return false; }
}
