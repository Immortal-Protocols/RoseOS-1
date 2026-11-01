package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
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
 * Host-rendered native output (MANAGED-WORKSPACE-OVERVIEW-001): the production WorkspaceOverviewView fed production
 * WorkspaceOverview models built from synthetic fixtures shaped like PR318's producer and the controller's get_state,
 * measured, laid out and drawn by Robolectric's native graphics at SDK 35. Not a device, activity, compositor or
 * phone acceptance; nothing starts a task, workspace, link or network.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w411dp-h914dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class WorkspaceOverviewRenderTest {
    static final Scope S = new Scope("server-a", "pair-1");
    static final Binding B = new Binding("rose-ws-n1-1-com.android.calculator2", "sess-1", "com.android.calculator2", S);
    static Workspace ready() { return Workspace.read("ready", "sess-1", B.controllerTaskId, B.targetPackage, "Calculator", S); }
    static List<Task> shared() {
        return Arrays.asList(
            new Task("1", S, Status.DONE, "Add 3 and 4", "", "3 + 4 = 7", 10, 15, B),
            new Task("2", S, Status.RUNNING, "Multiply 12 by 7", "Typing 12 × 7", "", 20, 0, B),
            new Task("3", S, Status.NEEDS_YOU, "Text Jamie I'm running late", "Draft ready to send", "", 30, 31, null));
    }

    static final class Taps implements WorkspaceOverviewView.Listener {
        final List<String> log = new ArrayList<>();
        public void select(Identity t) { log.add("select:" + t.id); }
        public void watch(Identity t) { log.add("watch:" + t.id); }
        public void stop(Identity t) { log.add("stop:" + t.id); }
    }

    static Bitmap draw(WorkspaceOverviewView v) {
        Context c = RuntimeEnvironment.getApplication();
        float d = c.getResources().getDisplayMetrics().density;
        int w = Math.round(411 * d), pad = Math.round(24 * d);
        v.setPadding(pad, pad, pad, pad);
        v.setBackgroundColor(0xfff5f7fa);
        v.measure(View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY), View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
        v.layout(0, 0, w, v.getMeasuredHeight());
        Bitmap b = Bitmap.createBitmap(w, v.getMeasuredHeight(), Bitmap.Config.ARGB_8888);
        v.draw(new Canvas(b));
        return b;
    }
    static void texts(View v, List<String> out) {
        if (v instanceof TextView) out.add(((TextView) v).getText().toString());
        if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) texts(((ViewGroup) v).getChildAt(i), out);
    }
    static Button button(View v, String label) {
        if (v instanceof Button && ((Button) v).getText().toString().equals(label)) return (Button) v;
        if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) { Button b = button(((ViewGroup) v).getChildAt(i), label); if (b != null) return b; }
        return null;
    }
    static WorkspaceOverviewView view(Model m, Taps taps) {
        WorkspaceOverviewView v = new WorkspaceOverviewView(RuntimeEnvironment.getApplication());
        v.setListener(taps); v.show(m); return v;
    }

    @Test public void sharedWorkspaceRendersOneCardAndRealRows() throws Exception {
        Taps taps = new Taps();
        Identity sel = new Identity("2", S);
        List<Task> tasks = Arrays.asList(shared().get(0), shared().get(1));
        WorkspaceOverviewView v = view(WorkspaceOverview.of(true, S, ready(), tasks, sel, new Stop()), taps);
        List<String> t = new ArrayList<>(); texts(v, t);
        assertEquals(1, t.stream().filter(s -> s.startsWith("Open in ")).count());
        assertTrue(t.contains("Open in Calculator") && t.contains("Multiply 12 by 7") && t.contains("3 + 4 = 7"));
        assertTrue(t.contains("Working · Typing 12 × 7"));
        Button watch = button(v, "Watch");
        assertTrue("the selected bound task offers Watch", watch != null);
        watch.performClick();
        Button stop = button(v, "Stop");
        stop.performClick();
        assertEquals(Arrays.asList("watch:2", "stop:2"), taps.log.subList(0, 2));
        Bitmap b = draw(v);
        RosePresenceRenderTest.save(b, "workspace-overview-shared");
        // With the draft task (unbound, finished) listed too: still one card, and it offers no Stop.
        WorkspaceOverviewView all = view(WorkspaceOverview.of(true, S, ready(), shared(), new Identity("3", S), new Stop()), new Taps());
        List<String> at = new ArrayList<>(); texts(all, at);
        assertTrue(at.contains("Needs your OK · Draft ready to send"));
        assertEquals(1, at.stream().filter(s -> s.startsWith("Open in ")).count());
        assertTrue(b.getHeight() > 400);
    }

    @Test public void unboundSelectionExplainsAndOffersNoWatch() throws Exception {
        WorkspaceOverviewView v = view(WorkspaceOverview.of(true, S, ready(), shared(), new Identity("3", S), new Stop()), new Taps());
        List<String> t = new ArrayList<>(); texts(v, t);
        assertTrue(t.contains(WorkspaceOverview.WATCH_UNLINKED));
        assertEquals(null, button(v, "Watch"));
        RosePresenceRenderTest.save(draw(v), "workspace-overview-unlinked");
    }

    @Test public void stopUnknownDisconnectedAndIdleRender() throws Exception {
        Identity sel = new Identity("2", S);
        Stop stop = new Stop(); stop.request(sel, 0); stop.ack(sel, Ack.ACCEPTED); stop.tick(Stop.TIMEOUT_MS);
        WorkspaceOverviewView unknown = view(WorkspaceOverview.of(true, S, ready(), shared(), sel, stop), new Taps());
        List<String> t = new ArrayList<>(); texts(unknown, t);
        assertTrue(t.contains(WorkspaceOverview.STOP_UNKNOWN));
        assertFalse(t.contains(WorkspaceOverview.STOP_DONE));
        assertEquals("no second Stop while unresolved", null, button(unknown, "Stop"));
        String a = RosePresenceRenderTest.save(draw(unknown), "workspace-overview-stop-unknown");

        WorkspaceOverviewView off = view(WorkspaceOverview.of(false, S, ready(), shared(), null, new Stop()), new Taps());
        t.clear(); texts(off, t);
        assertTrue(t.contains("Not connected"));
        assertEquals(null, button(off, "Stop"));
        String b = RosePresenceRenderTest.save(draw(off), "workspace-overview-disconnected");

        WorkspaceOverviewView idle = view(WorkspaceOverview.of(true, S, Workspace.read("idle", "", "", "", "", S), new ArrayList<Task>(), null, null), new Taps());
        t.clear(); texts(idle, t);
        assertTrue(t.contains("No workspace open"));
        String c = RosePresenceRenderTest.save(draw(idle), "workspace-overview-idle");
        assertNotEquals(a, b); assertNotEquals(b, c);
    }

    @Test public void reentryWithTheSameModelRedrawsNothing() {
        Model m = WorkspaceOverview.of(true, S, ready(), shared(), null, new Stop());
        WorkspaceOverviewView v = view(m, new Taps());
        View first = v.getChildAt(1);
        v.show(WorkspaceOverview.of(true, S, ready(), shared(), null, new Stop()));
        assertTrue("same key keeps the same views", v.getChildAt(1) == first);
    }
}
