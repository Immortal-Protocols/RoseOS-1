package dev.pixel.voice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

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

/** Host-native (Robolectric, SDK 35) behaviour of the production WorkspaceOverviewPanel with synthetic inputs: Back,
 * re-entry, Stop routing and Watch routing. Not a device or activity proof. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35, qualifiers = "w411dp-h914dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class WorkspaceOverviewPanelRenderTest {
    static final Scope S = new Scope("server-a", "pair-1");
    static final Binding B = new Binding("rose-ws-n1-1-pkg", "sess-1", "pkg", S);

    static final class Host implements WorkspaceOverviewPanel.Source, WorkspaceOverviewPanel.Actions {
        boolean route; List<Task> tasks = new ArrayList<>(Arrays.asList(new Task("2", S, Status.RUNNING, "Multiply 12 by 7", "Typing", "", 20, 0, B)));
        final List<String> calls = new ArrayList<>();
        public boolean connected() { return true; }
        public Scope scope() { return S; }
        public List<Task> tasks() { return tasks; }
        public Workspace workspace() { return Workspace.read("ready", "sess-1", B.controllerTaskId, "pkg", "Calculator", S); }
        public boolean stopRoute() { return route; }
        public void stop(Identity t) { calls.add("stop:" + t.id); }
        public void endCall() { calls.add("end"); }
        public void watch(Identity t, Binding atTap) { calls.add("watch:" + t.id + (atTap == null ? ":none" : ":" + atTap.sessionId)); }
    }
    static List<String> texts(View v, List<String> out) {
        if (v instanceof TextView) out.add(((TextView) v).getText().toString());
        if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) texts(((ViewGroup) v).getChildAt(i), out);
        return out;
    }
    static Button button(View v, String label) {
        if (v instanceof Button && ((Button) v).getText().toString().equals(label)) return (Button) v;
        if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) { Button b = button(((ViewGroup) v).getChildAt(i), label); if (b != null) return b; }
        return null;
    }
    static View row(View v, String title) {
        if (v.getContentDescription() != null && v.getContentDescription().toString().startsWith(title + ",")) return v;
        if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) { View r = row(((ViewGroup) v).getChildAt(i), title); if (r != null) return r; }
        return null;
    }

    static View tagged(View v, String tag) {
        if (tag.equals(v.getTag())) return v;
        if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) { View r = tagged(((ViewGroup) v).getChildAt(i), tag); if (r != null) return r; }
        return null;
    }
    static View described(View v, String d) {
        if (v.getContentDescription() != null && d.equals(v.getContentDescription().toString())) return v;
        if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) { View r = described(((ViewGroup) v).getChildAt(i), d); if (r != null) return r; }
        return null;
    }

    @Test public void noStopRouteNoStopAndBackKeepsEverything() {
        Host host = new Host();
        WorkspaceOverviewPanel.State state = new WorkspaceOverviewPanel.State();
        WorkspaceOverviewPanel p = new WorkspaceOverviewPanel(RuntimeEnvironment.getApplication(), state);
        p.bind(host, host);
        p.refresh();
        assertFalse("closed panel draws nothing", p.isOpen());
        p.open();
        assertEquals("no live Stop without a route", null, tagged(p, "stop"));
        tagged(tagged(p, "task:2"), "stop-unavailable").performClick();
        assertTrue("an unavailable Stop says why", texts(p, new ArrayList<>()).contains(WorkspaceOverview.STOP_NO_ROUTE));
        assertTrue("nothing was sent", host.calls.isEmpty());
        tagged(p, "task:2").performClick();
        assertTrue(state.selected != null && state.selected.id.equals("2"));
        assertEquals("a linked task card opens Watch with its own binding", Arrays.asList("watch:2:sess-1"), host.calls);
        described(p, "Back").performClick();
        assertFalse(p.isOpen());
        p.open();
        assertTrue("selection survives Back and re-entry", state.selected.id.equals("2"));
        assertEquals("Back never stopped or opened anything", Arrays.asList("watch:2:sess-1"), host.calls);
    }

    @Test public void stopIsSentOnceForTheCapturedIdentityAndSurvivesReentry() {
        Host host = new Host(); host.route = true;
        WorkspaceOverviewPanel.State state = new WorkspaceOverviewPanel.State();
        WorkspaceOverviewPanel p = new WorkspaceOverviewPanel(RuntimeEnvironment.getApplication(), state);
        p.bind(host, host); p.open();
        tagged(p, "stop").performClick();
        assertEquals(Arrays.asList("stop:2"), host.calls);
        assertEquals(null, tagged(p, "stop"));
        assertTrue(texts(p, new ArrayList<>()).contains(WorkspaceOverview.STOP_SENDING));
        state.ack(new Identity("2", new Scope("server-b", "pair-1")), Ack.STOPPED);
        assertEquals("a foreign answer is ignored", StopPhase.SENDING, state.stop.phase);
        state.ack(new Identity("2", S), Ack.ACCEPTED);
        p.close();
        WorkspaceOverviewPanel again = new WorkspaceOverviewPanel(RuntimeEnvironment.getApplication(), state);
        again.bind(host, host); again.open();
        assertTrue("a recreated panel shows the same pending Stop", texts(again, new ArrayList<>()).contains(WorkspaceOverview.STOP_STOPPING));
        assertEquals(Arrays.asList("stop:2"), host.calls);
    }
}
