package dev.pixel.voice;

import dev.pixel.voice.WorkspaceOverview.*;
import java.util.List;

/** MANAGED-WORKSPACE-OVERVIEW-001: onLocalTasks deliveries exactly as PR321's VoiceProviders.localTasks produced them
 * (generated with node from app/assets/voice-providers.js on PR318-shaped events), read by the overview feed and fed
 * to the production model. Host JVM only: no page, link or controller. */
public final class WorkspaceOverviewFeedTest {
    static int checks;
    static void expect(boolean condition, String message) {
        checks++; if (!condition) throw new AssertionError(message);
    }
    // PR318 today: no server, no contract, no workspace.
    static final String CURRENT = "[{\"key\":\"@1761960000.5#1\",\"id\":1,\"server\":null,\"kind\":\"phone\",\"goal\":\"Add 3 and 4\",\"status\":\"done\",\"detail\":\"\",\"result\":\"7\",\"background\":true,\"onScreen\":false,\"awaitingSend\":false,\"started\":1761960000.5,\"finished\":1761960004.25,\"contract\":null,\"connection\":\"c1-abc\",\"binding\":null},{\"key\":\"@1761960010#2\",\"id\":2,\"server\":null,\"kind\":\"phone\",\"goal\":\"Text Jamie\",\"status\":\"done\",\"detail\":\"\",\"result\":\"Draft typed\",\"background\":false,\"onScreen\":false,\"awaitingSend\":true,\"started\":1761960010,\"finished\":1761960015,\"contract\":null,\"connection\":\"c1-abc\",\"binding\":null},{\"key\":\"@1761960020#3\",\"id\":3,\"server\":null,\"kind\":\"phone\",\"goal\":\"Multiply 12 by 7\",\"status\":\"running\",\"detail\":\"Typing 12 x 7\",\"result\":\"\",\"background\":false,\"onScreen\":false,\"awaitingSend\":false,\"started\":1761960020,\"finished\":null,\"contract\":null,\"connection\":\"c1-abc\",\"binding\":null}]";
    // A future contract-1 producer: explicit server and workspace binding.
    static final String CONTRACT1 = "[{\"key\":\"srv-9#4\",\"id\":4,\"server\":\"srv-9\",\"kind\":\"phone\",\"goal\":\"Open Calculator\",\"status\":\"running\",\"detail\":\"Opening\",\"result\":\"\",\"background\":false,\"onScreen\":false,\"awaitingSend\":false,\"started\":1761960030,\"finished\":null,\"contract\":1,\"connection\":\"c2-def\",\"binding\":{\"controllerTaskId\":\"rose-ws-n-1-com.android.calculator2\",\"sessionId\":\"s-1\",\"targetPackage\":\"com.android.calculator2\"}}]";
    static Row row(Model m, String key) { for (Row r : m.rows) if (r.identity.id.equals(key)) return r; return null; }

    public static void main(String[] args) {
        WorkspaceOverviewFeed.clearForTest();
        expect(WorkspaceOverviewFeed.tasks().isEmpty(), "nothing before the first delivery");

        // Current producer: honest tasks and results, no Watch, no Stop.
        WorkspaceOverviewFeed.accept(CURRENT);
        List<Task> tasks = WorkspaceOverviewFeed.tasks();
        expect(tasks.size() == 3 && tasks.get(0).id.equals("@1761960000.5#1"), "the PR321 key is the identity");
        expect(tasks.get(0).started == 1761960000500L && tasks.get(0).finished == 1761960004250L, "epoch seconds become milliseconds");
        expect(tasks.get(1).status == Status.NEEDS_YOU && tasks.get(2).status == Status.RUNNING, "awaitingSend and running");
        for (Task t : tasks) expect(t.binding == null && !t.scope.known(), "no server/contract: no binding, unknown scope");
        Scope scope = WorkspaceOverviewFeed.scope();
        Workspace open = Workspace.read("ready", "s-1", "rose-ws-n-1-com.android.calculator2", "com.android.calculator2", "Calculator", scope);
        Model m = WorkspaceOverview.of(true, scope, open, tasks, null, new Stop(), false);
        expect(m.rows.size() == 3 && row(m, "@1761960020#3").detail.equals("Typing 12 x 7") && row(m, "@1761960000.5#1").result.equals("7"), "real activity and result shown");
        for (Row r : m.rows) expect(!r.canWatch && !r.canStop, "read-only: an open workspace is never adopted, and there is no Stop route");
        expect(row(m, "@1761960020#3").watchReason.equals(WorkspaceOverview.WATCH_UNLINKED), "the running task says it isn't linked");
        expect(row(m, "@1761960020#3").stopNote.equals(WorkspaceOverview.STOP_NO_ROUTE), "...and that Stop isn't available here");
        expect(m.card.place == Place.OPEN && m.card.linkedTasks == 0, "the one controller workspace is shown, attributed to no task");
        expect(!CURRENT.contains("secret draft"), "the fixture carries no draft text (PR321 projection)");

        // Contract 1: binding verifies only against the matching live read.
        WorkspaceOverviewFeed.accept(CONTRACT1);
        tasks = WorkspaceOverviewFeed.tasks(); scope = WorkspaceOverviewFeed.scope();
        expect(tasks.size() == 1 && tasks.get(0).binding != null && scope.same(new Scope("srv-9", "c2-def")), "binding and scope from the explicit fields");
        Workspace live = Workspace.read("ready", "s-1", "rose-ws-n-1-com.android.calculator2", "com.android.calculator2", "Calculator", scope);
        Model c = WorkspaceOverview.of(true, scope, live, tasks, null, new Stop(), false);
        expect(c.rows.get(0).canWatch && !c.rows.get(0).canStop, "Watch with a verified binding; still no Stop without a route");
        Workspace other = Workspace.read("ready", "s-2", "rose-ws-n-2-com.android.chrome", "com.android.chrome", "Chrome", scope);
        expect(!WorkspaceOverview.of(true, scope, other, tasks, null, null, false).rows.get(0).canWatch, "another session is never this task's");

        // Close/replacement/unpair delivers []: the list empties.
        WorkspaceOverviewFeed.accept("[]");
        expect(WorkspaceOverviewFeed.tasks().isEmpty() && !WorkspaceOverviewFeed.scope().known(), "[] clears the list and scope");
        Model empty = WorkspaceOverview.of(true, WorkspaceOverviewFeed.scope(), Workspace.read("idle", "", "", "", "", Scope.UNKNOWN), WorkspaceOverviewFeed.tasks(), null, null, false);
        expect(empty.empty.equals(WorkspaceOverview.EMPTY), "idle workspace and no tasks: the HiFi empty text");

        // Malformed or mixed deliveries are dropped whole; the previous list stays.
        WorkspaceOverviewFeed.accept(CURRENT);
        String[] bad = {null, "", "{}", "[1]", "[{\"key\":\"\",\"status\":\"running\",\"connection\":\"c\"}]", "not json",
            "[" + CURRENT.substring(1, CURRENT.length() - 1) + "," + CONTRACT1.substring(1), "[{\"key\":\"k\",\"status\":\"running\",\"connection\":\"c\",\"started\":1}] trailing"};
        for (String b : bad) { WorkspaceOverviewFeed.accept(b); expect(WorkspaceOverviewFeed.tasks().size() == 3, "dropped: " + b); }
        StringBuilder big = new StringBuilder("["); for (int i = 0; i < WorkspaceOverviewFeed.MAX_TASKS + 1; i++) big.append(i == 0 ? "" : ",").append("{\"key\":\"k" + i + "\",\"status\":\"done\",\"connection\":\"c\"}");
        WorkspaceOverviewFeed.accept(big.append("]").toString());
        expect(WorkspaceOverviewFeed.tasks().size() == 3, "an over-long list is dropped");
        // A binding without contract 1 or without a server is ignored.
        WorkspaceOverviewFeed.accept(CONTRACT1.replace("\"contract\":1", "\"contract\":null"));
        expect(WorkspaceOverviewFeed.tasks().get(0).binding == null, "no contract 1: no binding");
        WorkspaceOverviewFeed.accept(CONTRACT1.replace("\"server\":\"srv-9\"", "\"server\":null"));
        expect(WorkspaceOverviewFeed.tasks().get(0).binding == null, "no server: no binding");
        WorkspaceOverviewFeed.accept(CONTRACT1.replace("\"sessionId\":\"s-1\"", "\"sessionId\":\"\""));
        expect(WorkspaceOverviewFeed.tasks().size() == 1 && WorkspaceOverviewFeed.tasks().get(0).binding == null, "an incomplete binding is no binding");
        System.out.println("WorkspaceOverviewFeedTest: " + checks + " checks passed");
    }
}
