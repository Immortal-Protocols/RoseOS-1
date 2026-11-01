package dev.pixel.voice;

import dev.pixel.voice.WorkspaceOverview.*;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** MANAGED-WORKSPACE-OVERVIEW-001: the managed workspace overview model on synthetic fixtures shaped like PR318's
 * producer (Task.to_dict: id, goal, status, detail, result, started, finished, ready_to_send) and the controller's
 * get_state (status, sessionId, taskId, targetPackage). Host JVM only: no device, link or controller. */
public final class WorkspaceOverviewTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }

    static final Scope S1 = new Scope("server-a", "pair-1");
    static final String WS = "rose-ws-n1-1-com.android.calculator2", SESSION = "sess-1", PKG = "com.android.calculator2";
    static Binding bound(Scope s) { return new Binding(WS, SESSION, PKG, s); }
    static Task task(String id, Scope s, Status st, String goal, Binding b, long started) {
        return new Task(id, s, st, goal, st == Status.RUNNING ? "Typing 12 × 7" : "", st == Status.DONE ? "84" : "", started, st.equals(Status.RUNNING) ? 0 : started + 5, b);
    }
    static Workspace ready(Scope s) { return Workspace.read("ready", SESSION, WS, PKG, "Calculator", s); }
    static Row row(Model m, String id) { for (Row r : m.rows) if (r.identity.id.equals(id)) return r; return null; }

    public static void main(String[] args) {
        producerStatus();
        sharedWorkspace();
        missingBinding();
        staleReconnectRepairAndReusedId();
        replacementDuringSelection();
        stopTimeoutAndDuplicateAck();
        completedWithoutSession();
        idleDisconnectedConfirmation();
        repeatedEntryAndBack();
        noStopRoute();
        System.out.println("WorkspaceOverviewTest: " + checks + " checks passed");
    }

    static void producerStatus() {
        expect(WorkspaceOverview.status("running", false) == Status.RUNNING, "running");
        expect(WorkspaceOverview.status("done", true) == Status.NEEDS_YOU, "a done task holding ready_to_send waits for the owner");
        expect(WorkspaceOverview.status("done", false) == Status.DONE, "done");
        expect(WorkspaceOverview.status("cancelled", false) == Status.CANCELLED, "cancelled");
        expect(WorkspaceOverview.status("failed", false) == Status.FAILED, "failed");
        expect(WorkspaceOverview.status("weird", false) == Status.UNKNOWN && WorkspaceOverview.status(null, false) == Status.UNKNOWN, "unknown is never relabelled");
    }

    /** Several logical tasks share the one real workspace: one card, never one window per task. */
    static void sharedWorkspace() {
        List<Task> tasks = Arrays.asList(
            task("1", S1, Status.DONE, "Add 3 and 4", bound(S1), 10),
            task("2", S1, Status.RUNNING, "Multiply 12 by 7", bound(S1), 20),
            task("3", S1, Status.NEEDS_YOU, "Text Jamie I'm late", bound(S1), 30));
        Model m = WorkspaceOverview.of(true, S1, ready(S1), tasks, null, new Stop());
        expect(m.card.place == Place.OPEN && m.card.label.equals("Open in Calculator"), "one real workspace card");
        expect(m.card.linkedTasks == 1 && m.card.detail.equals("Used by one task shown here."), "the card counts the session's current user");
        expect(m.rows.size() == 3, "every logical task is a row");
        expect(m.rows.get(0).identity.id.equals("3") && m.rows.get(1).identity.id.equals("2") && m.rows.get(2).identity.id.equals("1"), "active work first, newest first, then results");
        expect(row(m, "3").canWatch, "the newest active task on the binding is watchable");
        expect(!row(m, "2").canWatch && row(m, "2").watchReason.equals(WorkspaceOverview.WATCH_MOVED_ON), "an older task on the same binding never watches its successor");
        expect(WorkspaceOverview.watchBinding(new Identity("2", S1), true, S1, ready(S1), tasks) == null, "...nor at the tap re-check");
        expect(row(m, "2").canStop && !row(m, "3").canStop, "only running work is stoppable; a draft awaiting the owner has finished");
        Model two = WorkspaceOverview.of(true, S1, ready(S1), Arrays.asList(tasks.get(0), tasks.get(1)), null, null);
        expect(two.card.linkedTasks == 1 && row(two, "2").canWatch, "alone on the binding, the running task is watchable");
        expect(!row(m, "1").canWatch && row(m, "1").watchReason.equals(WorkspaceOverview.WATCH_FINISHED), "a finished task is never watched through a workspace a later task reused");
        expect(row(m, "2").detail.equals("Typing 12 × 7") && row(m, "1").result.equals("84"), "real activity and result");
        expect(row(m, "3").state.equals("Needs your OK"), "confirmation is shown as needing the owner");
        // A live workspace that moved to another generation is not this task's.
        Model moved = WorkspaceOverview.of(true, S1, Workspace.read("ready", "sess-2", "rose-ws-n1-2-com.android.chrome", "com.android.chrome", "Chrome", S1), tasks, null, new Stop());
        expect(!row(moved, "3").canWatch && !row(moved, "2").canWatch && row(moved, "2").watchReason.equals(WorkspaceOverview.WATCH_MOVED_ON), "a different session is never adopted");
        expect(moved.card.linkedTasks == 0 && moved.card.detail.equals("Not linked to a task shown here."), "...and the card links nothing");
        for (String f : new String[]{"session", "task", "package"}) {
            Workspace w = Workspace.read("ready", f.equals("session") ? "x" : SESSION, f.equals("task") ? "x" : WS, f.equals("package") ? "x" : PKG, "Calculator", S1);
            expect(!row(WorkspaceOverview.of(true, S1, w, Arrays.asList(tasks.get(1)), null, null), "2").canWatch, "every binding field must match: " + f);
        }
    }

    static void missingBinding() {
        List<Task> tasks = Arrays.asList(task("7", S1, Status.RUNNING, "Set a timer", null, 5),
            task("8", S1, Status.RUNNING, "Open Maps", new Binding(WS, "", PKG, S1), 6));
        Model m = WorkspaceOverview.of(true, S1, ready(S1), tasks, null, null);
        expect(!row(m, "7").canWatch && row(m, "7").watchReason.equals(WorkspaceOverview.WATCH_UNLINKED), "no binding: Watch withheld with a reason, even with a workspace open");
        expect(!row(m, "8").canWatch && row(m, "8").watchReason.equals(WorkspaceOverview.WATCH_UNLINKED), "an incomplete binding is no binding");
        expect(m.card.place == Place.OPEN && m.card.linkedTasks == 0, "the open workspace is shown but not attributed");
        expect(row(m, "7").canStop, "Stop is by task identity, not by workspace");
        expect(row(m, "7").description().contains(WorkspaceOverview.WATCH_UNLINKED), "the reason is in the spoken name");
        for (Row r : m.rows) expect(!r.watchReason.contains(WS) && !r.watchReason.contains(SESSION), "reasons never carry an identifier");
    }

    /** Server restart, re-pair and a reused id: the old binding and old task are never the new ones. */
    static void staleReconnectRepairAndReusedId() {
        Scope restarted = new Scope("server-b", "pair-1"), repaired = new Scope("server-a", "pair-2");
        List<Task> old = Arrays.asList(task("2", S1, Status.RUNNING, "Multiply 12 by 7", bound(S1), 20));
        for (Scope now : new Scope[]{restarted, repaired}) {
            Model m = WorkspaceOverview.of(true, now, Workspace.read("ready", SESSION, WS, PKG, "Calculator", now), old, null, null);
            expect(!m.rows.get(0).canWatch && m.rows.get(0).watchReason.equals(WorkspaceOverview.WATCH_OLD_LINK), "a binding from another server lifetime/pairing never verifies, even with equal ids");
            expect(!m.rows.get(0).canStop, "a task from another scope can't be stopped from here");
        }
        // Reused id after restart: task "2" again, different scope; selecting the old one selects nothing new.
        Identity oldSel = new Identity("2", S1);
        List<Task> reused = Arrays.asList(task("2", restarted, Status.RUNNING, "Open Chrome", new Binding("rose-ws-n2-1-x", "s9", "x", restarted), 1));
        Model m = WorkspaceOverview.of(true, restarted, Workspace.read("ready", "s9", "rose-ws-n2-1-x", "x", "Chrome", restarted), reused, oldSel, null);
        expect(m.selected == null && !m.rows.get(0).selected, "the reused id is a different task: the old selection doesn't move to it");
        expect(WorkspaceOverview.watchBinding(oldSel, true, restarted, Workspace.read("ready", "s9", "rose-ws-n2-1-x", "x", "Chrome", restarted), reused) == null, "Watch from the old selection opens nothing");
        expect(m.rows.get(0).canWatch, "the new task is watchable on its own binding");
        // A workspace read without a known scope never verifies.
        Model unscoped = WorkspaceOverview.of(true, S1, Workspace.read("ready", SESSION, WS, PKG, "Calculator", Scope.UNKNOWN), old, null, null);
        expect(!unscoped.rows.get(0).canWatch, "an unscoped read never verifies");
        Model unknownNow = WorkspaceOverview.of(true, Scope.UNKNOWN, ready(Scope.UNKNOWN), Arrays.asList(task("2", Scope.UNKNOWN, Status.RUNNING, "x", bound(Scope.UNKNOWN), 1)), null, null);
        expect(!unknownNow.rows.get(0).canWatch && !unknownNow.rows.get(0).canStop, "nothing is verified while the scope is unknown");
    }

    /** The selected task is replaced while selected: Watch and Stop never reach the replacement. */
    static void replacementDuringSelection() {
        Identity sel = new Identity("2", S1);
        Scope next = new Scope("server-b", "pair-1");
        List<Task> before = Arrays.asList(task("2", S1, Status.RUNNING, "Multiply 12 by 7", bound(S1), 20));
        Model m = WorkspaceOverview.of(true, S1, ready(S1), before, sel, null);
        expect(m.selected != null && m.selected.canWatch, "selected and watchable");
        expect(WorkspaceOverview.watchBinding(sel, true, S1, ready(S1), before) != null, "the tap re-check passes on the fresh read");
        expect(m.selected.binding != null && WorkspaceOverview.watchBinding(sel, m.selected.binding, true, S1, ready(S1), before) != null, "...with the binding captured at the tap");
        Binding changed = new Binding(WS, "sess-9", PKG, S1);
        List<Task> rebound = Arrays.asList(task("2", S1, Status.RUNNING, "Multiply 12 by 7", changed, 20));
        expect(WorkspaceOverview.watchBinding(sel, m.selected.binding, true, S1, Workspace.read("ready", "sess-9", WS, PKG, "Calculator", S1), rebound) == null, "a binding changed since the tap is refused, even when it matches the live session");
        expect(WorkspaceOverview.watchBinding(sel, null, true, S1, ready(S1), before) == null, "no captured binding opens nothing");
        // Fresh read at the tap shows the workspace moved on: no binding is substituted.
        Workspace moved = Workspace.read("ready", "sess-3", "rose-ws-n1-3-y", "y", "Clock", S1);
        expect(WorkspaceOverview.watchBinding(sel, true, S1, moved, before) == null, "never substitutes the open workspace");
        // Stop captured for the selection; the task list then holds a replacement with the same id.
        Stop stop = new Stop();
        expect(stop.request(sel, 0), "Stop captures the selection");
        List<Task> after = Arrays.asList(task("2", next, Status.RUNNING, "Open Chrome", null, 30));
        Model r = WorkspaceOverview.of(true, next, ready(next), after, sel, stop);
        expect(stop.phase == StopPhase.REPLACED && !stop.pending(), "the pending Stop ends as replaced");
        expect(r.rows.get(0).stopNote.isEmpty() && !r.rows.get(0).state.equals(WorkspaceOverview.STOP_STOPPING), "the replacement shows no Stop of its own");
        stop.ack(new Identity("2", next), Ack.STOPPED);
        expect(stop.phase == StopPhase.REPLACED, "an answer for the replacement never settles the old Stop");
        expect(stop.target.same(sel), "the captured identity never changes");
    }

    static void stopTimeoutAndDuplicateAck() {
        Identity id = new Identity("2", S1);
        List<Task> running = Arrays.asList(task("2", S1, Status.RUNNING, "Multiply 12 by 7", bound(S1), 20), task("4", S1, Status.RUNNING, "Other", null, 21));
        Stop stop = new Stop();
        expect(!stop.request(new Identity("2", Scope.UNKNOWN), 0), "an unverified identity is never sent");
        expect(stop.request(id, 1000), "Stop sent");
        Model m = WorkspaceOverview.of(true, S1, ready(S1), running, id, stop);
        expect(row(m, "2").stopNote.equals(WorkspaceOverview.STOP_SENDING) && row(m, "2").dot == Dot.STOPPING, "sending");
        expect(!row(m, "4").canStop && !row(m, "2").canStop, "no second Stop while one is unresolved");
        expect(!stop.request(new Identity("4", S1), 1001), "...not even for another task");
        stop.ack(id, Ack.ACCEPTED);
        expect(stop.phase == StopPhase.STOPPING && row(WorkspaceOverview.of(true, S1, ready(S1), running, id, stop), "2").state.equals(WorkspaceOverview.STOP_STOPPING), "accepted is stopping, never stopped");
        stop.ack(id, Ack.ACCEPTED);
        expect(stop.phase == StopPhase.STOPPING, "a duplicate accept changes nothing");
        stop.tick(1000 + Stop.TIMEOUT_MS - 1);
        expect(stop.phase == StopPhase.STOPPING, "before the timeout");
        stop.tick(1000 + Stop.TIMEOUT_MS);
        expect(stop.phase == StopPhase.UNKNOWN && stop.note().equals(WorkspaceOverview.STOP_UNKNOWN), "timeout is unknown, never stopped");
        Model t = WorkspaceOverview.of(true, S1, ready(S1), running, id, stop);
        expect(row(t, "2").state.equals(WorkspaceOverview.STOP_STOPPING) && !row(t, "2").canStop, "still waiting for the authoritative answer");
        stop.ack(new Identity("4", S1), Ack.STOPPED);
        expect(stop.phase == StopPhase.UNKNOWN, "an answer about another task is ignored");
        stop.ack(id, Ack.STOPPED);
        expect(stop.phase == StopPhase.STOPPED, "a late authoritative answer settles it");
        stop.ack(id, Ack.REFUSED);
        stop.ack(id, Ack.ACCEPTED);
        expect(stop.phase == StopPhase.STOPPED, "duplicate/late answers never move a settled Stop");
        // Settled from the task list: the same identity reaching cancelled.
        Stop s2 = new Stop(); s2.request(id, 0); s2.ack(id, Ack.ACCEPTED);
        WorkspaceOverview.of(true, S1, ready(S1), Arrays.asList(task("2", S1, Status.CANCELLED, "Multiply 12 by 7", bound(S1), 20)), id, s2);
        expect(s2.phase == StopPhase.STOPPED, "the task's own cancelled state is authoritative");
        Stop s3 = new Stop(); s3.request(id, 0);
        WorkspaceOverview.of(true, S1, ready(S1), Arrays.asList(task("2", S1, Status.DONE, "Multiply 12 by 7", bound(S1), 20)), id, s3);
        expect(s3.phase == StopPhase.NOT_RUNNING, "finished first: nothing was stopped");
        Stop s4 = new Stop(); s4.request(id, 0); s4.ack(id, Ack.REFUSED);
        expect(s4.phase == StopPhase.REFUSED && s4.note().equals(WorkspaceOverview.STOP_REFUSED), "refused is shown as such");
        expect(s4.request(new Identity("4", S1), 1) && s4.target.id.equals("4"), "a new Stop may follow a settled one");
    }

    static void completedWithoutSession() {
        List<Task> done = Arrays.asList(task("1", S1, Status.DONE, "Add 3 and 4", null, 10), task("5", S1, Status.FAILED, "Book a table", null, 11),
            task("6", S1, Status.CANCELLED, "Find a photo", null, 12));
        Model m = WorkspaceOverview.of(true, S1, Workspace.read("idle", "", "", "", "", S1), done, null, null);
        expect(m.card.place == Place.NONE && m.card.label.equals("No workspace open"), "no live session");
        expect(m.rows.size() == 3 && row(m, "1").result.equals("84") && row(m, "1").state.equals("Done"), "the result stays without a session");
        expect(row(m, "5").state.equals("Couldn’t finish") && row(m, "6").state.equals("Stopped"), "failed and stopped results");
        for (Row r : m.rows) expect(!r.canWatch && !r.canStop && r.watchReason.equals(WorkspaceOverview.WATCH_FINISHED), "finished: no Watch, no Stop, reason given");
        List<Task> many = new ArrayList<>();
        for (int i = 0; i < 10; i++) many.add(task("t" + i, S1, Status.DONE, "Task " + i, null, i));
        Model capped = WorkspaceOverview.of(true, S1, Workspace.read("idle", "", "", "", "", S1), many, null, null);
        expect(capped.rows.size() == WorkspaceOverview.MAX_ROWS && capped.rows.get(0).identity.id.equals("t9"), "recent results, newest first, capped");
    }

    static void idleDisconnectedConfirmation() {
        Model empty = WorkspaceOverview.of(true, S1, Workspace.read("idle", "", "", "", "", S1), new ArrayList<Task>(), null, null);
        expect(empty.rows.isEmpty() && empty.empty.equals(WorkspaceOverview.TASKS_NONE) && empty.card.place == Place.NONE, "idle");
        List<Task> one = Arrays.asList(task("2", S1, Status.RUNNING, "Multiply 12 by 7", bound(S1), 20));
        Model off = WorkspaceOverview.of(false, S1, ready(S1), one, null, null);
        expect(off.card.place == Place.DISCONNECTED && off.card.label.equals("Not connected"), "disconnected is not idle");
        expect(!off.rows.get(0).canWatch && !off.rows.get(0).canStop && off.rows.get(0).watchReason.equals(WorkspaceOverview.WATCH_DISCONNECTED), "disconnected: last-known task shown, no controls");
        expect(off.rows.get(0).state.equals("Working"), "the last state as received, not invented");
        expect(WorkspaceOverview.of(true, S1, Workspace.CHECKING, one, null, null).rows.get(0).watchReason.equals(WorkspaceOverview.WATCH_CHECKING), "checking");
        expect(WorkspaceOverview.of(true, S1, Workspace.UNREADABLE, one, null, null).card.place == Place.UNREADABLE, "unreadable is not idle");
        Model locked = WorkspaceOverview.of(true, S1, Workspace.read("blocked", SESSION, WS, PKG, "Calculator", S1), one, null, null);
        expect(locked.card.place == Place.WAITING && locked.card.dot == Dot.ATTENTION && locked.rows.get(0).canWatch, "blocked (locked) keeps its session");
        Model consent = WorkspaceOverview.of(true, S1, Workspace.read("consent_required", "", "", "", "", S1), one, null, null);
        expect(consent.card.label.equals("Waiting for your consent") && !consent.rows.get(0).canWatch, "consent");
        expect(WorkspaceOverview.of(true, S1, Workspace.read("strange", "", "", "", "", S1), one, null, null).card.place == Place.UNKNOWN, "unknown controller state is not relabelled");
    }

    /** Without an authoritative Stop route Stop is never offered; the row says so instead of failing silently. */
    static void noStopRoute() {
        List<Task> tasks = Arrays.asList(task("2", S1, Status.RUNNING, "Multiply 12 by 7", bound(S1), 20), task("1", S1, Status.DONE, "Add", null, 1));
        Model m = WorkspaceOverview.of(true, S1, ready(S1), tasks, null, new Stop(), false);
        expect(!row(m, "2").canStop && row(m, "2").stopNote.equals(WorkspaceOverview.STOP_NO_ROUTE), "no route: no Stop, with a reason");
        expect(row(m, "2").canWatch, "Watch is independent of the Stop route");
        expect(row(m, "1").stopNote.isEmpty(), "finished tasks need no Stop note");
        expect(row(WorkspaceOverview.of(true, S1, ready(S1), tasks, null, new Stop(), true), "2").canStop, "with a route, running work is stoppable");
    }

    /** Entering, leaving with Back/Home and re-entering change nothing: same model, same Stop, same selection. */
    static void repeatedEntryAndBack() {
        Identity sel = new Identity("2", S1);
        List<Task> tasks = Arrays.asList(task("2", S1, Status.RUNNING, "Multiply 12 by 7", bound(S1), 20));
        Stop stop = new Stop(); stop.request(sel, 0); stop.ack(sel, Ack.ACCEPTED);
        String first = WorkspaceOverview.of(true, S1, ready(S1), tasks, sel, stop).key();
        for (int i = 0; i < 5; i++) {
            Model again = WorkspaceOverview.of(true, S1, ready(S1), tasks, sel, stop);
            expect(again.key().equals(first), "re-entry " + i + " draws the same overview");
        }
        expect(stop.phase == StopPhase.STOPPING && stop.target.same(sel), "the pending Stop survives re-entry");
        expect(tasks.get(0).status == Status.RUNNING, "the task keeps running: the overview never stops it");
        expect(WorkspaceOverview.of(true, S1, ready(S1), tasks, sel, stop).selected != null, "the selection survives re-entry");
    }
}
