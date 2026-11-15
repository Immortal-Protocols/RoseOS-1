package dev.pixel.voice;

import android.content.Context;
import java.util.Collections;
import java.util.List;

/**
 * Project 2: whether the workspace may offer and send Rose tasks. On by default, so a page can offer the tasks
 * below. The "tasks-route-enabled" preference set to false opts out, and a preference that cannot be read keeps
 * them off. Nothing runs on its own: every task asks first on the confirm sheet, and only the person's
 * confirmation sends it. The Rose server serves the route only when it was started with --workspace-tasks (App
 * Core, #834). Without that, the POST is a 404, which the card shows as "Rose tasks aren't turned on for your
 * Rose server", and nothing starts.
 *
 * TEMPLATES are the only Rose tasks the page can offer. They are the approved HiFi's own tasks, phrased for a real
 * phone. "Draft a reply to Jamie" is left out because it belongs to one notification. Each goal tells Rose to ask
 * before sending, sharing, booking or buying anything.
 */
final class WorkspaceTaskGate {
 private WorkspaceTaskGate() {}
 static final String PREFS="rose-workspace",KEY="tasks-route-enabled";
 /** The tasks the page can offer (WorkspaceTaskTemplates). */
 static final List<WorkspaceSnapshot.Task> TEMPLATES=WorkspaceTaskTemplates.ALL;

 static boolean enabled(Context context){
  try{return context.getSharedPreferences(PREFS,Context.MODE_PRIVATE).getBoolean(KEY,true);}
  catch(RuntimeException e){return false;}
 }
 static WorkspaceTaskClient.Gate gate(Context context){return new WorkspaceTaskClient.Gate(enabled(context));}
 /** The templates to offer: none when opted out. */
 static List<WorkspaceSnapshot.Task> offered(Context context){return enabled(context)?TEMPLATES:Collections.<WorkspaceSnapshot.Task>emptyList();}
}
