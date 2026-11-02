package dev.pixel.voice;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.List;

/** LOCAL-TASK-FEED-002 test probe: the native half of the page-to-overview path. Reads one command per line from
 * stdin ("page G" makes page generation G current; "deliver G <raw>" is Bridge.localTasks from page G; "gone G" is
 * voiceRendererGone; "close" is onDestroy) through the production LocalTaskFeedGate into the production
 * WorkspaceOverviewFeed, and prints what the production overview model then shows, one JSON line per command. */
public final class LocalTaskFeedProbe {
    static int page;static boolean closed;
    public static void main(String[] args) throws Exception {
        WorkspaceOverviewFeed.clearForTest();
        LocalTaskFeedGate gate = new LocalTaskFeedGate(g -> !closed && g == page, WorkspaceOverviewFeed::accept);
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        // The one live controller workspace the overview reads, as a contract-1 binding would name it.
        String[] ws = args.length == 3 ? args : new String[] {"", "", ""};
        for (String line; (line = in.readLine()) != null; ) {
            if (line.isEmpty()) continue;
            String[] p = line.split(" ", 3);
            boolean handed = false;
            switch (p[0]) {
                case "page": page = Integer.parseInt(p[1]); break;
                case "deliver": handed = gate.deliver(Integer.parseInt(p[1]), p.length > 2 ? p[2] : null); break;
                case "gone": gate.pageGone(Integer.parseInt(p[1])); break;
                case "close": gate.closeAll(); closed = true; break;
                default: throw new IllegalArgumentException(p[0]);
            }
            List<WorkspaceOverview.Task> tasks = WorkspaceOverviewFeed.tasks();
            WorkspaceOverview.Scope scope = WorkspaceOverviewFeed.scope();
            WorkspaceOverview.Workspace open = WorkspaceOverview.Workspace.read("ready", ws[1], ws[0], ws[2], "App", scope);
            WorkspaceOverview.Model m = WorkspaceOverview.of(true, scope, open, tasks, null, new WorkspaceOverview.Stop());
            StringBuilder b = new StringBuilder("{\"handed\":").append(handed).append(",\"rows\":[");
            for (int i = 0; i < m.rows.size(); i++) {
                WorkspaceOverview.Row r = m.rows.get(i);
                if (i > 0) b.append(',');
                b.append("{\"key\":").append(q(r.identity.id)).append(",\"state\":").append(q(r.state)).append(",\"linked\":").append(r.linked)
                 .append(",\"watch\":").append(r.canWatch).append(",\"stop\":").append(r.canStop).append(",\"reason\":").append(q(r.watchReason)).append('}');
            }
            int bound = 0; for (WorkspaceOverview.Task t : tasks) if (t.binding != null) bound++;
            System.out.println(b.append("],\"bound\":").append(bound).append(",\"scopeKnown\":").append(scope.known()).append('}'));
        }
    }
    static String q(String s) {
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) { if (c == '"' || c == '\\') b.append('\\'); if (c < 0x20) b.append(String.format("\\u%04x", (int) c)); else b.append(c); }
        return b.append('"').toString();
    }
}
