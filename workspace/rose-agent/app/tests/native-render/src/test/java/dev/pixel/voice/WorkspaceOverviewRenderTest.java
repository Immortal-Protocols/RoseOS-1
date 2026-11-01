package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.ColorDrawable;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.TextView;
import dev.pixel.voice.WorkspaceOverview.*;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;

/**
 * HOME-WORKSPACE-FIDELITY-225: the production WorkspaceOverviewPanel and its view, measured and drawn by Robolectric's
 * native graphics at SDK 35 in the selected HiFi's screen size (384x832 dp, ce126f56 .screen), against the HiFi
 * Rose's apps structure: no visible heading, a two-column grid of 250dp cards (12dp gaps, 24dp margins, 64dp top),
 * window/task cards with header/body/footer and a round 44dp Stop, and the three-well tray (Back, End/Rose, selected
 * grid). Synthetic inputs shaped like PR321's list; host-rendered output, not a device or phone acceptance.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w384dp-h832dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class WorkspaceOverviewRenderTest {
    static final Scope S = new Scope("server-a", "pair-1");
    static final Binding B = new Binding("rose-ws-n1-1-com.android.calculator2", "sess-1", "com.android.calculator2", S);
    static Workspace ready(String label) { return Workspace.read("ready", "sess-1", B.controllerTaskId, B.targetPackage, label, S); }
    static final Scope NONE = Scope.UNKNOWN;
    /** PR318 today: no server, no binding (as the PM photo: an open workspace not linked to any task, one finished task). */
    static List<Task> current() {
        return Arrays.asList(new Task("@1761960000.5#1", NONE, Status.DONE, "What's 12 times 7?", "", "12 × 7 = 84", 10, 15, null));
    }
    static List<Task> linked() {
        return Arrays.asList(new Task("2", S, Status.RUNNING, "Multiply 12 by 7", "Typing 12 × 7", "", 20, 0, B),
            new Task("1", S, Status.DONE, "Add 3 and 4", "", "3 + 4 = 7", 10, 15, null));
    }

    static final class Host implements WorkspaceOverviewPanel.Source, WorkspaceOverviewPanel.Actions {
        boolean live = true; Workspace ws = ready("Claude"); List<Task> tasks = current(); Scope scope = NONE;
        final List<String> calls = new ArrayList<>();
        public boolean connected() { return live; }
        public Scope scope() { return scope; }
        public List<Task> tasks() { return tasks; }
        public Workspace workspace() { return ws; }
        public boolean stopRoute() { return false; }
        public void stop(Identity t) { calls.add("stop:" + t.id); }
        public void watch(Identity t, Binding atTap) { calls.add("watch:" + t.id + ":" + (atTap == null ? "none" : atTap.sessionId)); }
        public void endCall() { calls.add("end"); }
    }
    static WorkspaceOverviewPanel panel(Host h) {
        WorkspaceOverviewPanel p = new WorkspaceOverviewPanel(RuntimeEnvironment.getApplication(), new WorkspaceOverviewPanel.State());
        p.bind(h, h); p.setIcons(pkg -> new ColorDrawable(0xffd97757)); p.open(); layout(p); return p;
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
    static int[] at(View v) { int[] l = new int[2]; v.getLocationInWindow(l); int x = 0, y = 0; for (View p = v; p != null; p = p.getParent() instanceof View ? (View) p.getParent() : null) { x += p.getLeft() - p.getScrollX(); y += p.getTop() - p.getScrollY(); } return new int[]{x, y}; }

    /** The PM's photo complaints, as structure: plain heading, rectangular Back, list instead of cards. */
    @Test public void hifiStructureNotAPlainList() throws Exception {
        WorkspaceOverviewPanel p = panel(new Host());
        assertFalse("no visible heading (HiFi h2 is screen-reader only)", texts(p).contains(WorkspaceOverview.TITLE));
        for (View v : all(p)) assertFalse("no Android push buttons (rectangular Back)", v instanceof Button);
        View back = described(p, "Back"), mid = described(p, "End call"), self = described(p, "Rose’s apps, current screen");
        assertNotNull("tray Back well", back); assertNotNull("tray End call well while connected", mid); assertNotNull("selected grid well", self);
        int[] b = at(back), m = at(mid), s = at(self);
        assertTrue("three wells side by side in one tray", b[1] == m[1] && m[1] == s[1] && b[0] < m[0] && m[0] < s[0]);
        assertEquals("tray bottom 34dp above the screen edge", dp(832 - 34), b[1] + back.getHeight() + (dp(92) - back.getHeight()) / 2, dp(2));
        View ws = tagged(p, "workspace"), task = tagged(p, "task:@1761960000.5#1");
        assertNotNull(ws); assertNotNull(task);
        assertEquals("cards are 250dp", dp(250), ws.getHeight()); assertEquals(ws.getHeight(), task.getHeight());
        int[] a = at(ws), c = at(task);
        assertEquals("two columns, same row", a[1], c[1]);
        assertEquals("equal columns", ws.getWidth(), task.getWidth(), 1);
        assertEquals("12dp gap", dp(12), c[0] - (a[0] + ws.getWidth()), 1);
        assertEquals("24dp margin", dp(24), a[0], 1);
        assertEquals("64dp top", dp(64), a[1], 1);
        List<String> t = texts(p);
        assertTrue("window card: the real app's name and state", t.contains("Claude") && t.contains("Open"));
        boolean icon = false; for (View v : all(ws)) icon |= v instanceof ImageView && ((ImageView) v).getDrawable() != null;
        assertTrue("window card: the app's real icon", icon);
        assertTrue("task card: real goal and result", t.contains("What's 12 times 7?") && t.contains("12 × 7 = 84") && t.contains("Done"));
        assertEquals("one window card only (one controller workspace)", 1, taggedPrefix(p, "workspace").size());
        for (View st : taggedPrefix(p, "stop")) { assertEquals(dp(44), st.getWidth()); assertEquals(dp(44), st.getHeight()); }
        assertTrue("finished cards are muted", task.getAlpha() < 1f);
        RosePresenceRenderTest.save(draw(p), "overview-hifi-current-unlinked");
    }

    @Test public void tapsExplainInsteadOfFailingSilently() throws Exception {
        Host h = new Host();
        WorkspaceOverviewPanel p = panel(h);
        tagged(p, "workspace").performClick(); layout(p);
        assertTrue("unlinked workspace: says why", texts(p).contains(WorkspaceOverview.WATCH_CARD_UNLINKED));
        assertTrue("nothing opened", h.calls.isEmpty());
        h.tasks = Arrays.asList(new Task("@5#3", NONE, Status.RUNNING, "Open Maps", "Opening", "", 30, 0, null)); p.refresh(); layout(p);
        tagged(tagged(p, "task:@5#3"), "stop-unavailable").performClick(); layout(p);
        assertTrue("an unavailable Stop says why", texts(p).contains(WorkspaceOverview.STOP_NO_ROUTE));
        tagged(tagged(p, "workspace"), "stop-unavailable").performClick(); layout(p);
        assertTrue("the workspace's Stop says Stop belongs to a task", texts(p).contains("Stop belongs to a task, not to the workspace."));
        assertTrue("nothing stopped", h.calls.isEmpty());
        described(p, "End call").performClick();
        assertEquals(Arrays.asList("end"), h.calls);
        described(p, "Back").performClick();
        assertFalse(p.isOpen());
    }

    @Test public void linkedWorkspaceWatchesItsOwnTask() throws Exception {
        Host h = new Host(); h.tasks = linked(); h.scope = S; h.ws = ready("Calculator");
        WorkspaceOverviewPanel p = panel(h);
        List<String> t = texts(p);
        assertTrue("the window card says what its linked task is doing", t.contains("Calculator") && t.contains("Multiply 12 by 7") && t.contains("Typing 12 × 7"));
        tagged(p, "workspace").performClick();
        assertEquals(Arrays.asList("watch:2:sess-1"), h.calls);
        RosePresenceRenderTest.save(draw(p), "overview-hifi-linked");
    }

    @Test public void emptyDisconnectedAndIdleStates() throws Exception {
        Host h = new Host(); h.ws = Workspace.read("idle", "", "", "", "", NONE); h.tasks = new ArrayList<>();
        WorkspaceOverviewPanel p = panel(h);
        assertTrue("HiFi a-empty", texts(p).contains(WorkspaceOverview.EMPTY));
        assertNull("idle: no window card", tagged(p, "workspace"));
        RosePresenceRenderTest.save(draw(p), "overview-hifi-empty");
        h.live = false; h.ws = ready("Claude"); h.tasks = current(); p.refresh(); layout(p);
        assertNotNull("disconnected: Open Rose in the middle well", described(p, "Open Rose"));
        assertTrue(texts(p).contains("Not connected"));
        RosePresenceRenderTest.save(draw(p), "overview-hifi-disconnected");
    }
}
