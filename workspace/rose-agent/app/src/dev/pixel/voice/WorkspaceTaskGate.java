package dev.pixel.voice;

import android.content.Context;
import java.util.Collections;
import java.util.List;

/**
 * Project 2: whether the workspace may offer and send Rose tasks at all. Off by default. It turns on only
 * through an explicit setting (the "tasks-route-enabled" preference), and nothing in source writes that
 * setting. When enabling App Core's route is decided, the decision comes with its own change.
 *
 * TEMPLATES are the only Rose tasks the page can offer. The list is empty: no task is invented here. Templates
 * arrive with the decision that enables the route, each with its label, ask and goal.
 */
final class WorkspaceTaskGate {
 private WorkspaceTaskGate() {}
 static final String PREFS="rose-workspace",KEY="tasks-route-enabled";
 static final List<WorkspaceSnapshot.Task> TEMPLATES=Collections.emptyList();

 static boolean enabled(Context context){
  try{return context.getSharedPreferences(PREFS,Context.MODE_PRIVATE).getBoolean(KEY,false);}
  catch(RuntimeException e){return false;}
 }
 static WorkspaceTaskClient.Gate gate(Context context){return new WorkspaceTaskClient.Gate(enabled(context));}
 /** The templates to offer: none unless enabled, and today none at all. */
 static List<WorkspaceSnapshot.Task> offered(Context context){return enabled(context)?TEMPLATES:Collections.<WorkspaceSnapshot.Task>emptyList();}
}
